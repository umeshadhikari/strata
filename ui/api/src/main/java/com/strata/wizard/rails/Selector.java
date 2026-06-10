package com.strata.wizard.rails;

import org.springframework.stereotype.Component;

import java.util.*;

/**
 * Deterministic rail selector — direct port of selector.py.
 *
 * Given (country, currency, amount, urgency) returns an ordered list of
 * candidate rails with a one-line rationale. The LLM never decides this;
 * it only presents these candidates to the user.
 */
@Component
public class Selector {

    private static final Set<String> SEPA_COUNTRIES = Set.of(
            "DE", "FR", "NL", "IT", "ES", "BE", "AT", "IE", "PT", "FI",
            "GR", "LU", "EE", "LV", "LT", "SK", "SI", "CY", "MT", "HR"
    );

    public record Candidate(String railId, String why) {}

    public List<Candidate> selectRails(String country, String currency, Double amount, String urgency) {
        if (country == null || currency == null) return List.of();
        String c = country.toUpperCase();
        String cur = currency.toUpperCase();
        List<Candidate> out = new ArrayList<>();

        if (SEPA_COUNTRIES.contains(c) && cur.equals("EUR")) {
            if (amount == null || amount <= 100_000) {
                out.add(new Candidate("sepa_inst",
                        "EUR within the SEPA zone, ≤ €100k — instant and free."));
            }
            out.add(new Candidate("swift_mt103",
                    "Always available; used for > €100k or non-standard cases. 1–3 days, ~€20–50 fee."));
        } else if (c.equals("GB") && cur.equals("GBP")) {
            if (amount == null || amount <= 1_000_000) {
                out.add(new Candidate("uk_fps", "GBP to UK, ≤ £1M — instant via Faster Payments."));
            }
            out.add(new Candidate("swift_mt103", "For > £1M or non-standard UK cases."));
        } else if (c.equals("US") && cur.equals("USD")) {
            out.add(new Candidate("us_ach", "Low-cost USD domestic — next business day."));
            out.add(new Candidate("swift_mt103", "Same-day option (Fedwire-style) at higher cost."));
        } else if (c.equals("IN") && cur.equals("INR")) {
            out.add(new Candidate("india_imps",
                    "INR domestic — IMPS (account) or UPI (VPA), both instant."));
        } else if (c.equals("BR") && cur.equals("BRL")) {
            out.add(new Candidate("brazil_pix", "BRL domestic instant via PIX — one key field."));
        } else {
            out.add(new Candidate("swift_mt103",
                    "Cross-border or unsupported domestic rail for " + cur + " to " + c + "."));
        }
        return out;
    }
}
