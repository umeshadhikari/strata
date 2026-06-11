package com.strata.wizard.rails;

import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.*;
import java.util.stream.Collectors;

/**
 * Cadence-based recurring-payment detector.
 *
 * Given a beneficiary's payment history, computes:
 *   1. Mean interval between payments (μ)
 *   2. Standard deviation of intervals (σ)
 *   3. Regularity = max(0, 1 − σ/μ) — high when payments arrive on a steady beat
 *   4. Mode amount — the value that recurs most often
 *   5. Predicted next-due date = lastPaid + μ
 *   6. Due-ness — 1.0 when today is within 20% of cadence from next-due, drops
 *      linearly outside that window
 *   7. Confidence = (regularity + dueness) / 2 — what surfaces as "due for you"
 *
 * Beneficiaries with regularity < 0.5 OR fewer than 3 prior payments are
 * surfaced as "templates" instead (quick-reuse cards, not predictions).
 *
 * This is honest applied stats: unsupervised pattern detection on a small
 * sample. The math is visible in the response so an analyst can sanity-check
 * what the algorithm decided and why.
 */
@Component
public class RecurringDetector {

    public record Suggestion(
            int beneficiary_id,
            String beneficiary,
            String bank_alias,
            String country,
            String rail_id,
            String currency,
            Double suggested_amount,
            String reason,
            double confidence,
            Integer cadence_days,
            Integer days_since_last,
            Integer days_until_due,
            boolean is_overdue,
            Map<String, Object> fields
    ) {}

    public record Template(
            int beneficiary_id,
            String beneficiary,
            String bank_alias,
            String country,
            String rail_id,
            String currency,
            String last_paid,
            Integer payment_count,
            Map<String, Object> fields
    ) {}

    public record Result(List<Suggestion> suggestions, List<Template> templates) {}

    /** Minimum history size before we attempt cadence detection. */
    private static final int MIN_HISTORY = 3;

    /** Below this regularity score, we don't claim it's recurring. */
    private static final double MIN_REGULARITY = 0.5;

    public Result detect(List<Map<String, Object>> beneficiaries, LocalDate asOf) {
        List<Suggestion> recurring = new ArrayList<>();
        List<Template> templates = new ArrayList<>();

        for (Map<String, Object> b : beneficiaries) {
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> history = (List<Map<String, Object>>) b.getOrDefault("payment_history", List.of());

            if (history.size() < MIN_HISTORY) {
                templates.add(toTemplate(b));
                continue;
            }

            // Sort dates descending (most-recent first).
            List<LocalDate> dates = history.stream()
                    .map(p -> LocalDate.parse((String) p.get("date")))
                    .sorted(Comparator.reverseOrder())
                    .toList();

            // Intervals between consecutive payments.
            List<Long> intervals = new ArrayList<>();
            for (int i = 0; i < dates.size() - 1; i++) {
                intervals.add(ChronoUnit.DAYS.between(dates.get(i + 1), dates.get(i)));
            }

            double mean = intervals.stream().mapToLong(Long::longValue).average().orElse(0);
            double variance = intervals.stream()
                    .mapToDouble(i -> Math.pow(i - mean, 2))
                    .average().orElse(0);
            double stddev = Math.sqrt(variance);
            double regularity = mean > 0 ? Math.max(0, 1 - stddev / mean) : 0;

            if (regularity < MIN_REGULARITY) {
                templates.add(toTemplate(b));
                continue;
            }

            // Due-ness against today.
            LocalDate lastPaid = dates.get(0);
            int daysSinceLast = (int) ChronoUnit.DAYS.between(lastPaid, asOf);
            int daysUntilDue = (int) Math.round(mean - daysSinceLast);
            boolean isOverdue = daysUntilDue < 0;

            double window = mean * 0.2;
            double dueness = Math.abs(daysUntilDue) <= window
                    ? 1.0
                    : Math.max(0, 1 - Math.abs(daysUntilDue) / mean);

            double confidence = (regularity + dueness) / 2;
            double suggestedAmount = computeModeAmount(history);

            String reason = formatReason(mean, daysSinceLast, daysUntilDue, isOverdue, history.size());

            @SuppressWarnings("unchecked")
            Map<String, Object> fields = (Map<String, Object>) b.get("fields");

            recurring.add(new Suggestion(
                    (Integer) b.get("id"),
                    (String) b.get("name"),
                    (String) b.get("bank_alias"),
                    (String) b.get("country"),
                    (String) b.get("preferred_rail"),
                    (String) b.get("preferred_currency"),
                    suggestedAmount,
                    reason,
                    round2(confidence),
                    (int) Math.round(mean),
                    daysSinceLast,
                    daysUntilDue,
                    isOverdue,
                    fields
            ));
        }

        recurring.sort(Comparator.comparingDouble(Suggestion::confidence).reversed());
        return new Result(recurring, templates);
    }

    /**
     * Find the most-common amount; if every amount is distinct, fall back to
     * the median (more robust to outliers than the mean for irregular flows).
     */
    private double computeModeAmount(List<Map<String, Object>> history) {
        Map<Double, Long> counts = history.stream()
                .collect(Collectors.groupingBy(
                        p -> ((Number) p.get("amount")).doubleValue(),
                        Collectors.counting()));

        Optional<Map.Entry<Double, Long>> top = counts.entrySet().stream()
                .max(Comparator.comparingLong(Map.Entry::getValue));

        if (top.isPresent() && top.get().getValue() > 1) {
            return top.get().getKey();
        }
        // Fall back to median.
        List<Double> sorted = history.stream()
                .map(p -> ((Number) p.get("amount")).doubleValue())
                .sorted().toList();
        return sorted.get(sorted.size() / 2);
    }

    private String formatReason(double meanInterval, int daysSinceLast,
                                 int daysUntilDue, boolean overdue, int count) {
        String cadenceLabel = cadenceLabel(meanInterval);
        String dueText;
        if (overdue) {
            dueText = "overdue by " + Math.abs(daysUntilDue) + " days";
        } else if (daysUntilDue == 0) {
            dueText = "due today";
        } else if (daysUntilDue <= 7) {
            dueText = "due in " + daysUntilDue + " days";
        } else {
            dueText = "next likely in ~" + daysUntilDue + " days";
        }
        return String.format("%s · last paid %d days ago · %s · %d prior",
                cadenceLabel, daysSinceLast, dueText, count);
    }

    private String cadenceLabel(double days) {
        if (days <= 8) return "Weekly";
        if (days <= 16) return "Bi-weekly";
        if (days <= 35) return "Monthly";
        if (days <= 70) return "Bi-monthly";
        if (days <= 100) return "Quarterly";
        if (days <= 200) return "Semi-annual";
        return String.format("~Every %d days", (int) days);
    }

    private Template toTemplate(Map<String, Object> b) {
        @SuppressWarnings("unchecked")
        Map<String, Object> fields = (Map<String, Object>) b.get("fields");
        return new Template(
                (Integer) b.get("id"),
                (String) b.get("name"),
                (String) b.get("bank_alias"),
                (String) b.get("country"),
                (String) b.get("preferred_rail"),
                (String) b.get("preferred_currency"),
                (String) b.get("last_paid"),
                (Integer) b.get("payment_count"),
                fields
        );
    }

    private static double round2(double v) {
        return Math.round(v * 100.0) / 100.0;
    }
}
