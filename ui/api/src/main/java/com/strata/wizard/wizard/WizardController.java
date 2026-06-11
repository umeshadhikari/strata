package com.strata.wizard.wizard;

import com.strata.wizard.rails.*;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDate;
import java.util.*;

/**
 * REST surface — identical paths and response shapes to the FastAPI wizard
 * router, so the existing Angular ApiService talks to either backend without
 * any frontend change.
 */
@RestController
@RequestMapping("/api/wizard")
public class WizardController {

    private final RailsRegistry registry;
    private final Directory directory;
    private final Lookups lookups;
    private final Selector selector;
    private final RecurringDetector detector;
    private final WizardService service;
    private final OllamaClient ollama;

    public WizardController(RailsRegistry registry, Directory directory, Lookups lookups,
                            Selector selector, RecurringDetector detector, WizardService service,
                            OllamaClient ollama) {
        this.registry = registry;
        this.directory = directory;
        this.lookups = lookups;
        this.selector = selector;
        this.detector = detector;
        this.service = service;
        this.ollama = ollama;
    }

    @GetMapping("/rails")
    public Map<String, Object> listRails() {
        return registry.asMap();
    }

    @GetMapping("/accounts")
    public Map<String, Object> listAccounts(@RequestParam(required = false) String currency,
                                            @RequestParam(required = false) String q) {
        return Map.of("accounts", directory.findAccounts(currency, q));
    }

    @GetMapping("/beneficiaries")
    public Map<String, Object> listBeneficiaries(@RequestParam(required = false) String q,
                                                 @RequestParam(required = false) String country) {
        var results = new ArrayList<>(directory.findBeneficiaries(q, country));
        results.sort(Comparator.comparing(b -> (String) b.get("name")));
        return Map.of("beneficiaries", results);
    }

    @GetMapping("/beneficiaries/{id}")
    public Map<String, Object> getBeneficiary(@PathVariable int id) {
        Map<String, Object> b = directory.getBeneficiary(id);
        if (b == null) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Beneficiary not found");
        return b;
    }

    @GetMapping("/iban-lookup")
    public Map<String, Object> ibanLookup(@RequestParam String iban) {
        Lookups.Bank bank = lookups.ibanToBank(iban);
        return Map.of(
                "iban", iban,
                "bank", bank == null ? Map.of() : Map.of("bic", bank.bic(), "name", bank.name())
        );
    }

    public record SelectRailsRequest(String country, String currency, Double amount, String urgency) {}

    @PostMapping("/select-rail")
    public Map<String, Object> selectRail(@RequestBody SelectRailsRequest req) {
        return Map.of("candidates", selector.selectRails(
                req.country(), req.currency(), req.amount(), req.urgency()));
    }

    @PostMapping("/turn")
    public TurnResponse turn(@RequestBody TurnRequest req) {
        return service.turn(req);
    }

    /**
     * Recurring-payment suggestions ranked by confidence, plus quick-reuse
     * templates for beneficiaries without a strong cadence. The asOf
     * parameter lets demos pin "today" to a fixed date for reproducibility.
     */
    @GetMapping("/suggestions")
    public RecurringDetector.Result suggestions(
            @RequestParam(value = "as_of", required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate asOf) {
        return detector.detect(Directory.BENEFICIARIES,
                asOf == null ? LocalDate.now() : asOf);
    }

    // ── Runtime model picker ──────────────────────────────────────────── //

    /**
     * List the chat models Ollama has locally + the one we're currently using.
     * Powers the gear-icon model picker in the wizard header. Excludes
     * embedding-only models (their `families` includes "bert" or name contains
     * "embed"/"paraphrase").
     */
    @GetMapping("/models")
    public Map<String, Object> listModels() {
        List<Map<String, Object>> chatModels = new ArrayList<>();
        try {
            var tags = ollama.listModels();
            var models = tags == null ? null : tags.get("models");
            if (models != null && models.isArray()) {
                for (var m : models) {
                    String name = m.path("name").asText("");
                    if (name.isBlank()) continue;
                    String lower = name.toLowerCase(Locale.ROOT);
                    // Skip non-chat models (embeddings, cloud-routed shims).
                    if (lower.contains("embed") || lower.contains("paraphrase")) continue;
                    if (lower.endsWith("-cloud")) continue;
                    Map<String, Object> entry = new LinkedHashMap<>();
                    entry.put("name", name);
                    entry.put("size", m.path("size").asLong(0));
                    entry.put("modified_at", m.path("modified_at").asText(""));
                    chatModels.add(entry);
                }
            }
        } catch (Exception ex) {
            // Ollama unreachable — return an empty list rather than 500, so
            // the picker just shows "no models found, is Ollama running?".
        }
        return Map.of(
                "active", ollama.getActiveModel(),
                "models", chatModels
        );
    }

    public record SetModelRequest(String model) {}

    /**
     * Swap the active model at runtime. Body: {"model":"llama3.1:8b"}.
     * Validates the model exists in Ollama before accepting the swap.
     */
    @PostMapping("/model")
    public Map<String, Object> setModel(@RequestBody SetModelRequest req) {
        String requested = req.model();
        if (requested == null || requested.isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "model is required");
        }
        // Validate against what Ollama actually has loaded.
        var tags = ollama.listModels();
        var models = tags == null ? null : tags.get("models");
        boolean found = false;
        if (models != null && models.isArray()) {
            for (var m : models) {
                if (requested.equals(m.path("name").asText(""))) {
                    found = true;
                    break;
                }
            }
        }
        if (!found) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND,
                    "Model '" + requested + "' not found in Ollama. Use GET /api/wizard/models to list available.");
        }
        ollama.setActiveModel(requested);
        return Map.of("active", requested);
    }
}
