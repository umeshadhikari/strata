package com.strata.wizard.rails;

import org.springframework.stereotype.Component;

import java.time.Clock;
import java.util.*;

/**
 * Deterministic rail selector — now intelligence-aware.
 *
 * Given (country, currency, amount, urgency) it:
 *   1. Filters rails by what the corridor allows (correct country+currency, under cap).
 *   2. Decorates each survivor with availability (from {@link RailScheduler}) and
 *      cost (from {@link RailCostEstimator}).
 *   3. Scores each by a weighted blend of speed, cost, and cut-off proximity,
 *      modulated by user intent ("urgent" / "cheap" / "normal").
 *   4. Returns the list sorted by score, top-pick first.
 *
 * The LLM is still gated to this list — see WizardService.
 *
 * SEPA country list is read from the rail registry rather than duplicated here,
 * so registry edits propagate automatically.
 */
@Component
public class Selector {

    /**
     * Enriched candidate. {@code rail_id} and {@code why} are required for backward
     * compatibility; the rest are populated when amount/clock are available.
     */
    public record Candidate(
            String rail_id,
            String why,
            RailScheduler.Availability availability,
            RailCostEstimator.Estimate cost,
            double score,
            boolean exceeds_limit
    ) {
        /** Back-compat factory for tests that don't care about enrichment. */
        public static Candidate basic(String railId, String why) {
            return new Candidate(railId, why, null, null, 0.0, false);
        }
    }

    private final RailsRegistry registry;
    private final RailScheduler scheduler;
    private final RailCostEstimator costEstimator;
    private final Clock clock;

    public Selector(RailsRegistry registry, RailScheduler scheduler,
                    RailCostEstimator costEstimator, Clock clock) {
        this.registry = registry;
        this.scheduler = scheduler;
        this.costEstimator = costEstimator;
        this.clock = clock;
    }

    public List<Candidate> selectRails(String country, String currency, Double amount, String urgency) {
        if (country == null || currency == null) return List.of();
        String c = country.toUpperCase();
        String cur = currency.toUpperCase();

        // Eligibility filter — what corridors does this (country, currency) belong to?
        List<RawCandidate> raw = filterByCorridor(c, cur, amount);
        if (raw.isEmpty()) return List.of();

        // Enrichment — availability + cost + score.
        String intent = normalizeIntent(urgency);
        List<Candidate> enriched = new ArrayList<>(raw.size());
        for (RawCandidate r : raw) {
            Map<String, Object> railDef = registry.getRail(r.railId);
            if (railDef == null) continue;
            @SuppressWarnings("unchecked")
            Map<String, Object> schedule = (Map<String, Object>) railDef.get("schedule");
            RailScheduler.Availability avail = scheduler.availability(schedule, clock);
            RailCostEstimator.Estimate cost = costEstimator.estimate(railDef, amount);
            double score = score(railDef, avail, cost, r.exceedsLimit, intent);
            enriched.add(new Candidate(r.railId, r.why, avail, cost, score, r.exceedsLimit));
        }

        // Sort by score desc, then push limit-exceeders to the bottom.
        enriched.sort((a, b) -> {
            if (a.exceeds_limit != b.exceeds_limit) return a.exceeds_limit ? 1 : -1;
            return Double.compare(b.score, a.score);
        });
        return enriched;
    }

    // ── Corridor filter ─────────────────────────────────────────────── //

    private record RawCandidate(String railId, String why, boolean exceedsLimit) {}

    @SuppressWarnings("unchecked")
    private List<RawCandidate> filterByCorridor(String c, String cur, Double amount) {
        Set<String> sepaCountries = sepaCountries();
        List<RawCandidate> out = new ArrayList<>();

        if (sepaCountries.contains(c) && cur.equals("EUR")) {
            double max = limitFor("sepa_inst");
            boolean over = amount != null && amount > max;
            out.add(new RawCandidate("sepa_inst",
                    over ? "EUR within SEPA but over €" + (long) max + " cap — use SWIFT."
                         : "EUR within the SEPA zone — instant and free.",
                    over));
            out.add(new RawCandidate("swift_mt103",
                    "Always available — used for over-cap or non-standard cross-border cases.", false));
        } else if (c.equals("GB") && cur.equals("GBP")) {
            double max = limitFor("uk_fps");
            boolean over = amount != null && amount > max;
            out.add(new RawCandidate("uk_fps",
                    over ? "GBP UK domestic but over £" + (long) max + " cap — use SWIFT for UK BACS or CHAPS-style flow."
                         : "GBP to UK — instant via Faster Payments.",
                    over));
            out.add(new RawCandidate("swift_mt103",
                    "For > £1M or non-standard UK cases.", false));
        } else if (c.equals("US") && cur.equals("USD")) {
            out.add(new RawCandidate("us_ach",
                    "Low-cost USD domestic — next business day.", false));
            out.add(new RawCandidate("swift_mt103",
                    "Same-day option (Fedwire-style) at higher cost.", false));
        } else if (c.equals("IN") && cur.equals("INR")) {
            double max = limitFor("india_imps");
            boolean over = amount != null && amount > max;
            out.add(new RawCandidate("india_imps",
                    over ? "Above ₹" + (long) max + " IMPS cap — would need NEFT/RTGS (not modelled) or SWIFT."
                         : "INR domestic — IMPS (account) or UPI (VPA), both instant.",
                    over));
            if (over) {
                out.add(new RawCandidate("swift_mt103",
                        "Above IMPS cap — fall back to SWIFT for large INR amounts.", false));
            }
        } else if (c.equals("BR") && cur.equals("BRL")) {
            double max = limitFor("brazil_pix");
            boolean over = amount != null && amount > max;
            out.add(new RawCandidate("brazil_pix",
                    over ? "Above the conservative R$" + (long) max + " demo cap."
                         : "BRL domestic instant via PIX — one key field.",
                    over));
            if (over) {
                out.add(new RawCandidate("swift_mt103",
                        "Cross-border or large-amount fallback.", false));
            }
        } else {
            out.add(new RawCandidate("swift_mt103",
                    "Cross-border or unsupported domestic rail for " + cur + " to " + c + ".", false));
        }
        return out;
    }

    private double limitFor(String railId) {
        Map<String, Object> rail = registry.getRail(railId);
        if (rail == null) return Double.MAX_VALUE;
        Object max = rail.get("max_amount");
        return max instanceof Number n ? n.doubleValue() : Double.MAX_VALUE;
    }

    /** Pull SEPA country list from registry's sepa_inst.countries (single source of truth). */
    @SuppressWarnings("unchecked")
    private Set<String> sepaCountries() {
        Map<String, Object> rail = registry.getRail("sepa_inst");
        if (rail == null) return Set.of();
        Object countries = rail.get("countries");
        if (countries instanceof List<?> list) {
            Set<String> set = new HashSet<>();
            for (Object o : list) {
                if (o instanceof String s) set.add(s.toUpperCase());
            }
            return set;
        }
        return Set.of();
    }

    // ── Scoring ─────────────────────────────────────────────────────── //

    /**
     * Score blends:
     *   - speed (instant rails win)
     *   - availability (in-window > cuts-off-soon > next-window)
     *   - cost (lower is better)
     *
     * User intent shifts the weights:
     *   "urgent" → speed weight tripled, cost weight halved
     *   "cheap"  → cost weight tripled, speed weight halved
     */
    @SuppressWarnings("unchecked")
    private double score(Map<String, Object> railDef,
                         RailScheduler.Availability avail,
                         RailCostEstimator.Estimate cost,
                         boolean exceedsLimit,
                         String intent) {
        if (exceedsLimit) return -100; // anything that breaks the cap belongs at the bottom

        double wSpeed = 1.0, wAvail = 1.0, wCost = 1.0;
        switch (intent) {
            case "urgent" -> { wSpeed = 3.0; wCost = 0.5; }
            case "cheap"  -> { wSpeed = 0.5; wCost = 3.0; }
            default       -> { /* keep defaults */ }
        }

        // Speed: instant rails (settles same day) = 1.0; T+1 = 0.6; T+2/T+3 = 0.3
        String settlement = (String) railDef.get("settlement");
        double speedScore = switch (settlement == null ? "" : settlement) {
            case "instant"             -> 1.0;
            case "next_day"            -> 0.6;
            case "1_3_business_days"   -> 0.3;
            default                     -> 0.5;
        };

        // Availability: now > today_soon > today > next_window
        double availScore = switch (avail == null ? "now" : avail.urgency()) {
            case "now"        -> 1.0;
            case "today_soon" -> 0.7;
            case "today"      -> 0.9;
            case "next_window"-> 0.3;
            default            -> 0.5;
        };

        // Cost: invert — cheaper wins. Bucket roughly so we don't chase pennies.
        double costMid = cost == null ? 0 : (cost.total_low() + cost.total_high()) / 2.0;
        double costScore;
        if (costMid <= 0.01)      costScore = 1.0;
        else if (costMid <= 1.0)  costScore = 0.95;
        else if (costMid <= 10)   costScore = 0.8;
        else if (costMid <= 30)   costScore = 0.5;
        else                       costScore = 0.2;

        return wSpeed * speedScore + wAvail * availScore + wCost * costScore;
    }

    private static String normalizeIntent(String urgency) {
        if (urgency == null) return "normal";
        String u = urgency.toLowerCase(Locale.ROOT).trim();
        if (u.contains("urgent") || u.contains("asap") || u.contains("now") || u.contains("instant")) return "urgent";
        if (u.contains("cheap") || u.contains("cost") || u.contains("low fee") || u.contains("save")) return "cheap";
        return "normal";
    }
}
