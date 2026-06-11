package com.strata.wizard.wizard;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.strata.wizard.config.AppSettings;
import io.github.resilience4j.bulkhead.annotation.Bulkhead;
import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;
import io.github.resilience4j.retry.annotation.Retry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestClient;
import org.springframework.web.server.ResponseStatusException;

import java.time.Duration;

import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Thin Ollama HTTP client. Talks to the OpenAI-compatible
 * /v1/chat/completions endpoint, wrapped in three Resilience4j layers so
 * Ollama trouble degrades gracefully instead of cascading into backend
 * timeouts:
 *
 *   @Bulkhead       — caps concurrent inference calls per backend instance
 *                     (config: resilience4j.bulkhead.instances.ollama)
 *   @CircuitBreaker — opens after a sustained failure rate; lets traffic
 *                     past in half-open state to probe recovery
 *   @Retry          — retries genuinely transient network failures only;
 *                     does NOT retry 4xx/5xx Ollama responses
 *
 * When everything else has failed (breaker open, bulkhead saturated, retry
 * exhausted), the {@link #ollamaFallback} method returns a synthetic
 * message so the wizard turn surfaces a clean "AI assistant unavailable"
 * line in the chat strip instead of hanging.
 */
@Component
public class OllamaClient {

    private static final Logger log = LoggerFactory.getLogger(OllamaClient.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();

    private final AppSettings settings;
    private final RestClient client;
    /** Active chat model. Initialized from application.yaml, swappable at runtime
     *  via the /api/wizard/model endpoint so demo operators can switch models
     *  from the UI without recreating the container. */
    private final AtomicReference<String> activeModel;

    public OllamaClient(AppSettings settings) {
        this.settings = settings;
        // Generous timeouts because CPU inference for 8B+ models can take 60-180s
        // per turn, and the FIRST request after a model swap has to load weights
        // (5-13 GB) into RAM. 5 minutes covers both comfortably.
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout((int) Duration.ofSeconds(10).toMillis());
        factory.setReadTimeout((int) Duration.ofMinutes(5).toMillis());
        this.client = RestClient.builder()
                .baseUrl(settings.getOllama().getUrl())
                .requestFactory(factory)
                .build();
        this.activeModel = new AtomicReference<>(settings.getOllama().getModel());
    }

    /** Currently active chat model name (e.g. "llama3.1:8b"). */
    public String getActiveModel() {
        return activeModel.get();
    }

    /** Swap the chat model at runtime. The next chatCompletion call uses it. */
    public void setActiveModel(String model) {
        log.info("Switching active Ollama model to '{}'", model);
        activeModel.set(model);
    }

    /** List models Ollama currently has locally (calls /api/tags). */
    public JsonNode listModels() {
        return client.get()
                .uri("/api/tags")
                .retrieve()
                .body(JsonNode.class);
    }

    @Bulkhead(name = "ollama")
    @CircuitBreaker(name = "ollama", fallbackMethod = "ollamaFallback")
    @Retry(name = "ollama")
    public JsonNode chatCompletion(String systemPrompt,
                                   String userContent,
                                   List<Map<String, Object>> tools) {
        Map<String, Object> body = Map.of(
                "model", activeModel.get(),
                "messages", List.of(
                        Map.of("role", "system", "content", systemPrompt),
                        Map.of("role", "user", "content", userContent)
                ),
                "tools", tools,
                "tool_choice", "auto",
                "temperature", 0.2
        );

        try {
            JsonNode response = client.post()
                    .uri("/v1/chat/completions")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(body)
                    .retrieve()
                    .body(JsonNode.class);
            if (response == null || !response.has("choices")) {
                throw new ResponseStatusException(HttpStatus.BAD_GATEWAY,
                        "Ollama returned no choices");
            }
            return response.get("choices").get(0).get("message");
        } catch (HttpClientErrorException ex) {
            // 4xx from Ollama is NOT transient — don't waste retry budget.
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY,
                    "Ollama error: " + ex.getStatusCode() + " " + ex.getResponseBodyAsString());
        }
        // ResourceAccessException + SocketTimeoutException intentionally
        // propagate so Resilience4j @Retry can act on them.
    }

    /**
     * Resilience4j fallback — invoked when the circuit breaker is open OR
     * when an exception escapes after retries are exhausted.
     *
     * Returning a synthetic "message" JsonNode means the wizard pipeline
     * doesn't crash: WizardService sees no tool_calls and a raw_message
     * that the frontend's existing system-entry rendering already handles.
     * The chat strip shows: model returned prose: "(assistant temporarily
     * unavailable…)" — a clean degraded mode where the user can still type
     * field values directly.
     *
     * The signature must match the protected method's, plus a trailing
     * Throwable — that's how Resilience4j discovers the fallback.
     */
    @SuppressWarnings("unused")
    private JsonNode ollamaFallback(String systemPrompt,
                                    String userContent,
                                    List<Map<String, Object>> tools,
                                    Throwable t) {
        log.warn("Ollama unavailable — serving fallback (cause: {})", t.toString());
        ObjectNode msg = MAPPER.createObjectNode();
        msg.put("content",
                "(AI assistant temporarily unavailable — please fill the form manually)");
        return msg;
    }
}
