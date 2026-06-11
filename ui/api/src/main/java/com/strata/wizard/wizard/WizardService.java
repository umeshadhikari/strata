package com.strata.wizard.wizard;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.strata.wizard.rails.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.*;

/**
 * Heart of the wizard. Composes the LLM context with the rail registry,
 * deterministic rail candidates, and detected beneficiary/account mentions
 * — then walks the tool calls applying validators and auto-deriving BIC
 * from IBAN. Behavior is intentionally identical to the FastAPI version.
 */
@Service
public class WizardService {

    private static final Logger log = LoggerFactory.getLogger(WizardService.class);

    /** OpenAI-style tools grammar passed verbatim to Ollama. */
    private static final List<Map<String, Object>> TOOLS = List.of(
            tool("set_field",
                    "Set a single field on the payment form. Use this for every fact you extract.",
                    Map.of(
                            "type", "object",
                            "properties", Map.of(
                                    "field_id", Map.of("type", "string", "description", "Field id from available_fields."),
                                    "value", Map.of("description", "Numbers as numbers, strings as strings."),
                                    "confidence", Map.of("type", "number", "minimum", 0, "maximum", 1)
                            ),
                            "required", List.of("field_id", "value", "confidence")
                    )),
            tool("select_rail",
                    "Select one rail from the server-provided candidates.",
                    Map.of(
                            "type", "object",
                            "properties", Map.of(
                                    "rail_id", Map.of("type", "string"),
                                    "why", Map.of("type", "string")
                            ),
                            "required", List.of("rail_id", "why")
                    )),
            tool("ask",
                    "Ask the user for a missing or ambiguous field. Provide choices when enumerable.",
                    Map.of(
                            "type", "object",
                            "properties", Map.of(
                                    "field_id", Map.of("type", "string"),
                                    "prompt", Map.of("type", "string"),
                                    "choices", Map.of("type", "array", "items", Map.of("type", "string"))
                            ),
                            "required", List.of("field_id", "prompt")
                    )),
            tool("explain",
                    "Answer a 'what is X' question in 2-3 sentences. Does NOT mutate the form.",
                    Map.of(
                            "type", "object",
                            "properties", Map.of(
                                    "topic", Map.of("type", "string"),
                                    "body", Map.of("type", "string")
                            ),
                            "required", List.of("topic", "body")
                    ))
    );

    private static Map<String, Object> tool(String name, String desc, Map<String, Object> params) {
        return Map.of(
                "type", "function",
                "function", Map.of("name", name, "description", desc, "parameters", params)
        );
    }

    private static final String SYSTEM_PROMPT = """
            You are a payment-form assistant. The user describes a payment in natural language; you extract fields and emit tool calls. You are NOT allowed to reply with prose — every response must be one or more tool calls.

            CRITICAL: You MUST invoke the tools via the function-calling interface. Do NOT write "set_field(...)" as plain text. Multiple tool calls per response are allowed and expected.

            SEARCH-STYLE UTTERANCES — the user is often just looking up a counterparty or account by voice/typing, without giving any other details. Handle these the same way as a full payment description: act on whatever match was found, even when the amount, currency, etc. are missing.

            If the context contains `matched_beneficiaries` with one entry, that's a saved counterparty the user is referring to. Fan ALL its data into set_field calls: beneficiary_name, beneficiary_country, the preferred_currency, and every key in its `fields` map. Also call select_rail with its `preferred_rail`. Use high confidence (0.99) on these. THIS APPLIES EVEN WHEN THE USER ONLY MENTIONED THE BENEFICIARY NAME OR THEIR BANK ("send to Acme", "find Smith Holland", "use my Barclays one"). Do NOT wait for amount/currency before populating fields — fill what we know now and `ask` for the rest.

            If the context contains `matched_beneficiaries` with MULTIPLE entries (the user's phrase was ambiguous), emit `ask field_id="beneficiary_name"` with `choices` containing each candidate's display name — do NOT guess.

            If the context contains `matched_debit_accounts` with at least one entry, pick the one whose currency matches the payment currency (or the first if none do) and set debit_account_id to its id with confidence 0.95+. THIS APPLIES EVEN WHEN THE USER ONLY SAID "use OPS GBP" or "pay from my treasury USD" — set the account immediately, no need to wait for the beneficiary.

            RULES
            1. For every fact the user stated, emit set_field. Use the exact field_id from available_fields. Confidence 0.95+ when explicit, 0.6–0.8 when inferred.
            2. If no rail is selected AND candidates is non-empty, emit select_rail with the first candidate.
            3. `rail_id` MUST be one of the strings in `candidates`. NEVER invent rail names like "uk_to_eu" or "sepa". The ONLY valid values are the rail IDs from the registry (see `valid_rail_ids` in the message). CRITICAL: `uk_fps` is for UK→UK GBP only (domestic). A GBP payment from UK to anywhere else is cross-border and MUST use `swift_mt103`. Same for `us_ach` (US→US USD only) and `india_imps` (India domestic only).
            4. After a rail is selected, only set fields that exist on that rail.
            5. If something is missing or ambiguous, emit ask.
            6. Country codes ISO 3166-1 alpha-2 (DE, GB, US, IN, BR). Currency codes ISO 4217 uppercase. Amounts as plain numbers.
            7. CURRENCY INFERENCE: when the user names a country but not a currency, infer the destination's local currency. "to Spain" → EUR (not GBP, even if the source is the UK). "to Germany" → EUR. "to India" → INR. "to Brazil" → BRL. Only override this when the user explicitly states the currency.

            EXAMPLES
            User: "send 5000 EUR to Acme GmbH in Germany IBAN DE89370400440532013000"
            You: tool-call set_field amount=5000 0.99; set_field currency="EUR" 0.99; set_field beneficiary_country="DE" 0.99; set_field beneficiary_name="Acme GmbH" 0.95; select_rail rail_id="sepa_inst" why="EUR to Germany — SEPA Instant."; set_field iban="DE89370400440532013000" 0.99.

            User: "pay 200 BRL to consultor@itau.com.br via PIX"
            You: tool-call set_field amount=200 0.99; set_field currency="BRL" 0.99; set_field beneficiary_country="BR" 0.95; select_rail rail_id="brazil_pix" why="BRL domestic — PIX instant."; set_field pix_key="consultor@itau.com.br" 0.99.

            User: "send a supplier payment from uk to spain"
            You: tool-call set_field beneficiary_country="ES" 0.95; set_field currency="GBP" 0.7; select_rail rail_id="swift_mt103" why="GBP from UK to Spain is cross-border — uk_fps is UK-domestic only."; ask field_id="amount" prompt="How much?".

            User: "send to Smith and Holland"   (search-only — matched_beneficiaries returns the LLP record)
            You: tool-call set_field beneficiary_name="Smith & Holland LLP" 0.99; set_field beneficiary_country="GB" 0.99; set_field currency="GBP" 0.99; select_rail rail_id="uk_fps" why="Saved counterparty's preferred rail."; set_field sort_code="20-30-40" 0.99; set_field account_number="12345678" 0.99; ask field_id="amount" prompt="How much?".

            User: "use my Barclays beneficiary"   (search-only — bank_alias match → Smith & Holland LLP)
            You: tool-call set_field beneficiary_name="Smith & Holland LLP" 0.99; set_field beneficiary_country="GB" 0.99; set_field currency="GBP" 0.99; select_rail rail_id="uk_fps" why="Saved counterparty via bank alias 'Barclays'."; set_field sort_code="20-30-40" 0.99; set_field account_number="12345678" 0.99; ask field_id="amount" prompt="How much?".

            User: "pay from OPS GBP"   (search-only — matched_debit_accounts returns one entry)
            You: tool-call set_field debit_account_id=<the "id" value from matched_debit_accounts[0]> 0.99; set_field currency="GBP" 0.95; ask field_id="beneficiary_name" prompt="Who's the beneficiary?".

            CRITICAL: debit_account_id MUST be one of the integer ids in matched_debit_accounts. NEVER invent a number. If matched_debit_accounts is empty, do NOT set debit_account_id at all — emit `ask` instead.
            """;

    private final RailsRegistry registry;
    private final Selector selector;
    private final Lookups lookups;
    private final Directory directory;
    private final Validators validators;
    private final OllamaClient ollama;
    private final ProseParser proseParser;
    private final ObjectMapper json = new ObjectMapper();

    public WizardService(RailsRegistry registry, Selector selector, Lookups lookups,
                         Directory directory, Validators validators,
                         OllamaClient ollama, ProseParser proseParser) {
        this.registry = registry;
        this.selector = selector;
        this.lookups = lookups;
        this.directory = directory;
        this.validators = validators;
        this.ollama = ollama;
        this.proseParser = proseParser;
    }

    public TurnResponse turn(TurnRequest req) {
        Map<String, Object> form = new HashMap<>(req.form_state() == null ? Map.of() : req.form_state());
        String railId = (String) form.get("rail_id");
        // If the user picked a rail directly via the "Pick a rail" entry screen,
        // form_state.rail_locked is true. We honour that pick — no select_rail
        // tool calls from the LLM, no deterministic fallback. The LLM is then
        // restricted to set_field/ask/explain on this rail's fields only.
        boolean railLocked = Boolean.TRUE.equals(form.get("rail_locked"));

        // Deterministic rail candidates.
        List<Selector.Candidate> candidates = selector.selectRails(
                (String) form.get("beneficiary_country"),
                (String) form.get("currency"),
                toDouble(form.get("amount")),
                (String) form.get("urgency"));

        // available_fields whitelist for the LLM.
        List<String> availableFields;
        if (railId != null) {
            availableFields = new ArrayList<>(registry.fieldIdsForRail(railId));
            availableFields.add("rail_id");
        } else {
            // Union of all rail fields so a single turn can pre-fill rail-specific
            // values (e.g. an IBAN mentioned in the same sentence as Germany).
            Set<String> union = new TreeSet<>();
            union.add("rail_id");
            registry.commonFields().forEach(f -> union.add((String) f.get("id")));
            registry.rails().values().forEach(rail -> {
                @SuppressWarnings("unchecked")
                List<Map<String, Object>> fs = (List<Map<String, Object>>) rail.getOrDefault("fields", List.of());
                fs.forEach(f -> union.add((String) f.get("id")));
            });
            availableFields = new ArrayList<>(union);
        }

        // Directory enrichment: detect mentioned beneficiaries + accounts,
        // optionally narrow accounts by current currency.
        List<Map<String, Object>> beneficiaryMatches = directory.detectBeneficiaryMentions(req.user_text());
        List<Map<String, Object>> accountMatches = directory.detectAccountMentions(req.user_text());
        if (form.get("currency") instanceof String cur) {
            List<Map<String, Object>> filtered = accountMatches.stream()
                    .filter(a -> ((String) a.get("currency")).equalsIgnoreCase(cur))
                    .toList();
            if (!filtered.isEmpty()) accountMatches = filtered;
        }

        String userContent;
        try {
            userContent = "available_fields: " + availableFields + "\n"
                    + "valid_rail_ids: " + registry.rails().keySet() + "\n"
                    + "form_state: " + json.writeValueAsString(form) + "\n"
                    + "candidates: " + candidates.stream().map(Selector.Candidate::rail_id).toList() + "\n"
                    + "matched_beneficiaries: " + json.writeValueAsString(beneficiaryMatches) + "\n"
                    + "matched_debit_accounts: " + json.writeValueAsString(accountMatches) + "\n\n"
                    + "User: " + req.user_text();
        } catch (JsonProcessingException ex) {
            throw new RuntimeException(ex);
        }

        JsonNode msg = ollama.chatCompletion(SYSTEM_PROMPT, userContent, TOOLS);
        String rawMessage = msg.path("content").asText(null);

        List<ToolCall> toolCalls = new ArrayList<>();
        JsonNode rawCalls = msg.get("tool_calls");
        if (rawCalls != null && rawCalls.isArray()) {
            for (JsonNode tc : rawCalls) {
                String name = tc.path("function").path("name").asText(null);
                String argsStr = tc.path("function").path("arguments").asText("{}");
                if (name == null) continue;
                try {
                    @SuppressWarnings("unchecked")
                    Map<String, Object> args = json.readValue(argsStr, Map.class);
                    toolCalls.add(new ToolCall(name, args));
                } catch (Exception ex) {
                    log.warn("Bad tool-call args from model: {}", argsStr);
                }
            }
        }

        // Prose fallback: convert text-form calls into real ToolCalls.
        if (toolCalls.isEmpty() && rawMessage != null && !rawMessage.isBlank()) {
            List<ToolCall> recovered = proseParser.parse(rawMessage);
            if (!recovered.isEmpty()) {
                log.info("Recovered {} tool calls from prose fallback", recovered.size());
                toolCalls.addAll(recovered);
            }
        }

        // Validate + accept/drop + auto-derive.
        List<TurnResponse.ValidationResult> validation = new ArrayList<>();
        Map<String, Object> derived = new LinkedHashMap<>();
        List<ToolCall> accepted = new ArrayList<>();
        String effectiveRail = railId;
        boolean railWasPicked = railId != null;
        Set<String> candidateIds = candidates.stream()
                .map(Selector.Candidate::rail_id)
                .collect(java.util.stream.Collectors.toCollection(java.util.LinkedHashSet::new));

        for (ToolCall tc : toolCalls) {
            if ("select_rail".equals(tc.name())) {
                if (railLocked) {
                    log.info("Dropping select_rail — rail '{}' was locked by user direct-pick.", railId);
                    continue;
                }
                Object newRail = tc.args().get("rail_id");
                String s = (newRail instanceof String str) ? str : null;
                // Two layers of defense:
                //   (a) rail must exist in the registry (catches "uk_to_eu"-style fabrications)
                //   (b) rail must be in the deterministic candidate list (catches plausible
                //       but wrong picks like uk_fps for a UK→Spain GBP payment, which is
                //       cross-border and only swift_mt103 is valid)
                if (s == null || registry.getRail(s) == null) {
                    log.warn("Dropping select_rail for unknown rail '{}'. Known: {}",
                            newRail, registry.rails().keySet());
                    validation.add(new TurnResponse.ValidationResult(
                            "rail_id", false,
                            "Model suggested rail '" + newRail + "' which is not in the registry — falling back to a deterministic candidate."));
                    continue;
                }
                if (!candidateIds.isEmpty() && !candidateIds.contains(s)) {
                    log.warn("Dropping select_rail '{}' — not in candidates {}", s, candidateIds);
                    validation.add(new TurnResponse.ValidationResult(
                            "rail_id", false,
                            "Model picked '" + s + "' but the deterministic selector ruled it out for this country/currency. Available: " + candidateIds + "."));
                    continue;
                }
                effectiveRail = s;
                railWasPicked = true;
            }
            if ("set_field".equals(tc.name())) {
                String fid = (String) tc.args().get("field_id");
                Object value = tc.args().get("value");
                Map<String, Object> def = registry.fieldDef(effectiveRail, fid);
                if (def == null) {
                    log.info("Dropping set_field for unknown field '{}' on rail '{}'", fid, effectiveRail);
                    continue;
                }
                String validatorName = (String) def.get("validate");
                if (validatorName != null && value != null && !"".equals(value)) {
                    Validators.Result vr = validators.validate(validatorName, String.valueOf(value));
                    validation.add(new TurnResponse.ValidationResult(fid, vr.ok(), vr.error()));
                    if (!vr.ok()) continue;
                }
                if ("iban".equals(fid) && value != null) {
                    Lookups.Bank bank = lookups.ibanToBank(String.valueOf(value));
                    if (bank != null) {
                        derived.put("bic", bank.bic());
                        derived.put("_bank_name", bank.name());
                    }
                }
                // The LLM sometimes invents numeric IDs (e.g. "102") for
                // debit_account_id when it should be picking from the directory.
                // Reject anything that doesn't map to a real account so the
                // picker doesn't show a blank field downstream — the
                // deterministic cascade below will then fill in the correct id.
                if ("debit_account_id".equals(fid) && value != null) {
                    int requested = toInt(value);
                    boolean exists = Directory.DEBIT_ACCOUNTS.stream()
                            .anyMatch(a -> toInt(a.get("id")) == requested);
                    if (!exists) {
                        log.warn("Dropping set_field debit_account_id={} — no such account in directory", requested);
                        validation.add(new TurnResponse.ValidationResult(
                                "debit_account_id", false,
                                "Model picked account id " + requested + " which doesn't exist — falling back to the directory match."));
                        continue;
                    }
                }
            }
            accepted.add(tc);
        }

        // ── Deterministic directory cascade ───────────────────────────── //
        // Small models (llama3.1:8b, qwen 3b) often emit only beneficiary_name
        // when matched_beneficiaries has one hit, leaving the other 4-6 fields
        // empty. Same with debit accounts: the model sets the name but forgets
        // the id. Fan the rest out here so the user gets the same result they
        // would by clicking the typeahead row manually.
        //
        // Already-set fields by the LLM are NOT overwritten — the model's
        // explicit choices win.
        Set<String> alreadySetFields = new HashSet<>();
        for (ToolCall tc : accepted) {
            if ("set_field".equals(tc.name())) {
                Object fid = tc.args().get("field_id");
                if (fid instanceof String s) alreadySetFields.add(s);
            }
        }

        // Beneficiary cascade — exactly one match → full fan-out.
        if (beneficiaryMatches.size() == 1) {
            @SuppressWarnings("unchecked")
            Map<String, Object> b = beneficiaryMatches.get(0);
            String benRail = (String) b.get("preferred_rail");
            String benCountry = (String) b.get("country");
            String benCurrency = (String) b.get("preferred_currency");
            String benName = (String) b.get("name");
            @SuppressWarnings("unchecked")
            Map<String, Object> benFields = (Map<String, Object>) b.get("fields");

            // Lock rail to the beneficiary's preferred rail so the cascade fields
            // align with the right form schema.
            if (!railLocked && benRail != null && registry.getRail(benRail) != null) {
                if (!railWasPicked || !benRail.equals(effectiveRail)) {
                    accepted.add(new ToolCall("select_rail", Map.of(
                            "rail_id", benRail,
                            "why", "Saved counterparty's preferred rail (" + benName + ")."
                    )));
                    effectiveRail = benRail;
                    railWasPicked = true;
                }
            }
            // Top-level common fields.
            cascade(accepted, alreadySetFields, "beneficiary_name", benName);
            cascade(accepted, alreadySetFields, "beneficiary_country", benCountry);
            cascade(accepted, alreadySetFields, "currency", benCurrency);
            // Rail-specific fields from the beneficiary's `fields` map.
            if (benFields != null) {
                for (var entry : benFields.entrySet()) {
                    Map<String, Object> def = registry.fieldDef(effectiveRail, entry.getKey());
                    if (def == null) continue; // skip fields that don't apply on this rail
                    cascade(accepted, alreadySetFields, entry.getKey(), entry.getValue());
                }
            }
        }

        // Debit-account cascade — exactly one match (after the currency filter)
        // → set the account id. The frontend already syncs the picker display
        // text from the id.
        if (accountMatches.size() == 1 && !alreadySetFields.contains("debit_account_id")) {
            Object accId = accountMatches.get(0).get("id");
            if (accId != null) {
                accepted.add(new ToolCall("set_field", Map.of(
                        "field_id", "debit_account_id",
                        "value", accId,
                        "confidence", 0.98
                )));
                alreadySetFields.add("debit_account_id");
            }
        }

        // Deterministic fallback: if the model didn't pick a valid rail AND we
        // have a candidate from the selector, synthesise the select_rail so the
        // form always morphs to *something* sensible. The "why" makes it clear
        // to the user that this came from the rule engine, not the LLM.
        // Skipped when the user has locked the rail.
        if (!railWasPicked && !railLocked && !candidates.isEmpty()) {
            Selector.Candidate top = candidates.get(0);
            accepted.add(new ToolCall("select_rail", Map.of(
                    "rail_id", top.rail_id(),
                    "why", "Auto-selected from deterministic candidates: " + top.why()
            )));
        }

        return new TurnResponse(accepted, candidates, availableFields, validation, derived, rawMessage);
    }

    /** Append a set_field tool call, but only if the LLM didn't already set it. */
    private static void cascade(List<ToolCall> accepted, Set<String> already,
                                String fieldId, Object value) {
        if (value == null || "".equals(value)) return;
        if (already.contains(fieldId)) return;
        accepted.add(new ToolCall("set_field", Map.of(
                "field_id", fieldId,
                "value", value,
                "confidence", 0.98
        )));
        already.add(fieldId);
    }

    private static Double toDouble(Object v) {
        if (v instanceof Number n) return n.doubleValue();
        if (v instanceof String s && !s.isBlank()) {
            try { return Double.parseDouble(s); } catch (NumberFormatException ignored) {}
        }
        return null;
    }

    private static int toInt(Object v) {
        if (v instanceof Number n) return n.intValue();
        if (v instanceof String s && !s.isBlank()) {
            try { return Integer.parseInt(s.trim()); } catch (NumberFormatException ignored) {}
        }
        return -1;
    }
}
