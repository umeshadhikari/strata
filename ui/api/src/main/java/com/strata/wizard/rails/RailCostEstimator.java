package com.strata.wizard.rails;

import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Estimates the user-visible cost of sending an amount via a given rail.
 *
 * Reads the rail's {@code cost} block from the registry:
 *   - fixed_fee                  scalar in the rail's native currency
 *   - percentage_bps             basis points charged on the amount (rarely non-zero)
 *   - fx_spread_bps              bps margin baked into the FX rate (SWIFT mostly)
 *   - correspondent_fee_range    [min, max] in USD for SWIFT-style corridors
 *
 * The estimator does NOT do real FX conversion — the user's currency is left
 * alongside the rail's native cost currency and the UI shows both when they
 * differ. For the demo this is honest: showing "approx €27" when we don't
 * actually know today's EURUSD would be worse than showing "$25 + ~$30 corr."
 */
@Component
public class RailCostEstimator {

    /** Cost estimate result. All amounts in the rail's native cost currency. */
    public record Estimate(
            double fixed_fee,
            double percentage_fee,
            double fx_spread_estimate,
            double correspondent_fee_low,
            double correspondent_fee_high,
            double total_low,
            double total_high,
            String currency,         // currency of the fees (rail-native)
            String headline          // short string for the candidate card e.g. "Free", "$25–55", "₹5"
    ) {}

    @SuppressWarnings("unchecked")
    public Estimate estimate(Map<String, Object> rail, Double amount) {
        Map<String, Object> cost = rail == null ? null : (Map<String, Object>) rail.get("cost");
        if (cost == null) {
            return new Estimate(0, 0, 0, 0, 0, 0, 0, "USD", "—");
        }
        String currency = (String) cost.getOrDefault("currency", "USD");
        double fixed = asDouble(cost.get("fixed_fee"), 0.0);
        double percBps = asDouble(cost.get("percentage_bps"), 0.0);
        double fxBps = asDouble(cost.get("fx_spread_bps"), 0.0);
        double amt = amount == null ? 0 : amount;
        double percFee = amt * percBps / 10_000.0;
        double fxFee = amt * fxBps / 10_000.0;

        double corrLow = 0, corrHigh = 0;
        Object corrRange = cost.get("correspondent_fee_range");
        if (corrRange instanceof List<?> list && list.size() == 2) {
            corrLow = asDouble(list.get(0), 0.0);
            corrHigh = asDouble(list.get(1), 0.0);
        }

        double totalLow = fixed + percFee + fxFee + corrLow;
        double totalHigh = fixed + percFee + fxFee + corrHigh;

        return new Estimate(
                fixed, percFee, fxFee, corrLow, corrHigh, totalLow, totalHigh,
                currency, headline(totalLow, totalHigh, currency, fxBps > 0)
        );
    }

    /** "Free" / "$25.30" / "$25–55 + ~0.5% FX" / "₹5". */
    private static String headline(double low, double high, String currency, boolean hasFxSpread) {
        if (low == 0 && high == 0) return "Free";
        String sym = symbol(currency);
        String body;
        if (low == high) {
            body = sym + format(low);
        } else {
            body = sym + format(low) + "–" + format(high);
        }
        return hasFxSpread ? body + " + FX" : body;
    }

    private static String format(double v) {
        if (v >= 100) return String.format(Locale.US, "%.0f", v);
        if (v == Math.floor(v)) return String.format(Locale.US, "%.0f", v);
        return String.format(Locale.US, "%.2f", v);
    }

    private static String symbol(String currency) {
        return switch (currency) {
            case "USD" -> "$";
            case "EUR" -> "€";
            case "GBP" -> "£";
            case "INR" -> "₹";
            case "BRL" -> "R$";
            case "JPY" -> "¥";
            default     -> currency + " ";
        };
    }

    private static double asDouble(Object v, double fallback) {
        if (v instanceof Number n) return n.doubleValue();
        if (v instanceof String s) {
            try { return Double.parseDouble(s); } catch (NumberFormatException ignored) {}
        }
        return fallback;
    }
}
