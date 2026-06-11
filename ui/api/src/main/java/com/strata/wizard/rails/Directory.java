package com.strata.wizard.rails;

import org.springframework.stereotype.Component;

import java.util.*;
import java.util.regex.Pattern;

/**
 * In-memory directory of debit accounts (ours), saved beneficiaries
 * (counterparties paid before), and their payment histories.
 *
 * Static seed data — a real implementation would source these from
 * dim_account / dim_data_owner / a saved-beneficiaries table.
 *
 * The histories are sized for the RecurringDetector — Smith & Holland and
 * Cloudflare have clean monthly cadences (will surface as "due for you"),
 * Acme has a fresh monthly that's not yet due, Itau is quarterly,
 * Paris and Mumbai are ad-hoc, Tokyo and Singapore are one-off.
 */
@Component
public class Directory {

    // ─── Debit accounts ─────────────────────────────────────────────── //
    public static final List<Map<String, Object>> DEBIT_ACCOUNTS = List.of(
            acc(1, "OPS-USD-01",   "Operations USD",         "USD", 1_250_000.00, "0987654321 (Chase 021000021)",        "operating"),
            acc(2, "OPS-EUR-01",   "Operations EUR",         "EUR",   850_000.00, "DE89 3704 0044 0532 0130 00",         "operating"),
            acc(3, "TREAS-EUR",    "Treasury EUR Reserve",   "EUR", 5_400_000.00, "FR14 2004 1010 0505 0001 3M02 606",   "treasury"),
            acc(4, "OPS-GBP-01",   "Operations GBP",         "GBP",   320_000.00, "GB29 NWBK 6016 1331 9268 19",         "operating"),
            acc(5, "PAYROLL-USD",  "Payroll USD",            "USD",   175_000.00, "1122334455 (BoA 026009593)",          "payroll"),
            acc(6, "OPS-INR",      "Operations INR (India)", "INR", 8_500_000.00, "ICIC0001234 / 12345678901234",        "operating"),
            acc(7, "OPS-BRL",      "Operations BRL (Brazil)","BRL",   420_000.00, "PIX: finance@strata.com.br",          "operating")
    );

    private static Map<String, Object> acc(int id, String code, String name, String currency,
                                           double balance, String identifier, String kind) {
        return Map.of("id", id, "code", code, "name", name, "currency", currency,
                "balance", balance, "identifier", identifier, "kind", kind);
    }

    // ─── Saved beneficiaries with payment history + bank alias ─────── //
    public static final List<Map<String, Object>> BENEFICIARIES = List.of(
            ben(1, "Acme GmbH", "DE", "EUR", "sepa_inst", "2026-06-02", 12,
                "Commerzbank Köln",
                Map.of("iban", "DE89370400440532013000", "bic", "COBADEFFXXX"),
                List.of(
                    pe("2026-06-02", 5000),
                    pe("2026-05-05", 5200),
                    pe("2026-04-03", 4800),
                    pe("2026-03-04", 5000),
                    pe("2026-02-02", 5000),
                    pe("2026-01-03", 5100)
                )),
            ben(2, "Cloudflare Inc", "US", "USD", "us_ach", "2026-05-14", 8,
                "Chase Bank",
                Map.of("routing_number", "021000021", "account_number", "987654321", "account_type", "checking"),
                List.of(
                    pe("2026-05-14", 400),
                    pe("2026-04-15", 400),
                    pe("2026-03-14", 400),
                    pe("2026-02-15", 400),
                    pe("2026-01-14", 400),
                    pe("2025-12-13", 400),
                    pe("2025-11-15", 400),
                    pe("2025-10-12", 400)
                )),
            ben(3, "Paris Logistique SARL", "FR", "EUR", "sepa_inst", "2026-04-15", 5,
                "La Banque Postale",
                Map.of("iban", "FR1420041010050500013M02606", "bic", "PSSTFRPPXXX"),
                List.of(
                    pe("2026-04-15", 3200),
                    pe("2026-02-14", 2800),
                    pe("2025-12-10", 3500),
                    pe("2025-09-20", 2200),
                    pe("2025-06-30", 3000)
                )),
            ben(4, "Smith & Holland LLP", "GB", "GBP", "uk_fps", "2026-05-13", 18,
                "Barclays Bank",
                Map.of("sort_code", "20-30-40", "account_number", "12345678", "reference", "INV-2026"),
                List.of(
                    pe("2026-05-13", 2500),
                    pe("2026-04-12", 2500),
                    pe("2026-03-15", 2400),
                    pe("2026-02-13", 2500),
                    pe("2026-01-15", 2600),
                    pe("2025-12-12", 2500),
                    pe("2025-11-14", 2500),
                    pe("2025-10-12", 2500)
                )),
            ben(5, "Mumbai Consulting Pvt Ltd", "IN", "INR", "india_imps", "2026-05-10", 3,
                "HDFC Bank",
                Map.of("india_mode", "account", "ifsc_code", "HDFC0001234", "account_number", "123456789012"),
                List.of(
                    pe("2026-05-10", 75000),
                    pe("2026-02-15", 90000),
                    pe("2025-11-20", 60000)
                )),
            ben(6, "Itau Consultoria", "BR", "BRL", "brazil_pix", "2026-05-25", 4,
                "Banco Itaú",
                Map.of("pix_key", "consultor@itau.com.br"),
                List.of(
                    pe("2026-05-25", 200),
                    pe("2026-02-20", 200),
                    pe("2025-11-25", 200),
                    pe("2025-08-20", 200)
                )),
            ben(7, "Tokyo Software KK", "JP", "JPY", "swift_mt103", "2026-03-01", 2,
                "Bank of Tokyo-Mitsubishi UFJ",
                Map.of("bic", "BOTKJPJT", "account_number", "1234567",
                        "beneficiary_address", "1-2-3 Marunouchi, Chiyoda-ku, Tokyo 100-0005, Japan",
                        "charges_code", "SHA"),
                List.of(
                    pe("2026-03-01", 5_000_000),
                    pe("2025-09-15", 8_000_000)
                )),
            ben(8, "Singapore Trade Pte", "SG", "SGD", "swift_mt103", "2026-02-20", 1,
                "DBS Bank",
                Map.of("bic", "DBSSSGSG", "account_number", "0012345678",
                        "beneficiary_address", "12 Marina Boulevard, Singapore 018982",
                        "charges_code", "SHA"),
                List.of(
                    pe("2026-02-20", 25000)
                ))
    );

    /** Build a beneficiary record. Insertion-ordered so JSON serialises predictably. */
    private static Map<String, Object> ben(int id, String name, String country, String currency,
                                           String rail, String lastPaid, int count,
                                           String bankAlias, Map<String, Object> fields,
                                           List<Map<String, Object>> history) {
        LinkedHashMap<String, Object> m = new LinkedHashMap<>();
        m.put("id", id);
        m.put("name", name);
        m.put("country", country);
        m.put("preferred_currency", currency);
        m.put("preferred_rail", rail);
        m.put("fields", fields);
        m.put("last_paid", lastPaid);
        m.put("payment_count", count);
        m.put("bank_alias", bankAlias);
        m.put("payment_history", history);
        return m;
    }

    /** Single payment event for history seed. */
    private static Map<String, Object> pe(String date, double amount) {
        return Map.of("date", date, "amount", amount);
    }

    // ─── Search ─────────────────────────────────────────────────────── //
    public List<Map<String, Object>> findAccounts(String currency, String query) {
        return DEBIT_ACCOUNTS.stream()
                .filter(a -> currency == null || ((String) a.get("currency")).equalsIgnoreCase(currency))
                .filter(a -> {
                    if (query == null || query.isBlank()) return true;
                    String q = query.toLowerCase();
                    return ((String) a.get("code")).toLowerCase().contains(q)
                            || ((String) a.get("name")).toLowerCase().contains(q)
                            || ((String) a.get("kind")).toLowerCase().contains(q);
                })
                .toList();
    }

    public Map<String, Object> getAccount(int id) {
        return DEBIT_ACCOUNTS.stream()
                .filter(a -> ((Integer) a.get("id")).equals(id))
                .findFirst().orElse(null);
    }

    /**
     * Beneficiary search now matches against name, bank alias, country code,
     * and known field hints (IBAN prefix, BIC, sort code). "Lloyds" finds
     * every beneficiary banked at Lloyds; "CO" finds every Commerzbank one.
     */
    public List<Map<String, Object>> findBeneficiaries(String query, String country) {
        return BENEFICIARIES.stream()
                .filter(b -> country == null || ((String) b.get("country")).equalsIgnoreCase(country))
                .filter(b -> {
                    if (query == null || query.isBlank()) return true;
                    String q = query.toLowerCase();
                    if (((String) b.get("name")).toLowerCase().contains(q)) return true;
                    String alias = (String) b.get("bank_alias");
                    if (alias != null && alias.toLowerCase().contains(q)) return true;
                    @SuppressWarnings("unchecked")
                    Map<String, Object> fields = (Map<String, Object>) b.get("fields");
                    if (fields != null) {
                        for (Object v : fields.values()) {
                            if (v != null && v.toString().toLowerCase().contains(q)) return true;
                        }
                    }
                    return false;
                })
                .toList();
    }

    public Map<String, Object> getBeneficiary(int id) {
        return BENEFICIARIES.stream()
                .filter(b -> ((Integer) b.get("id")).equals(id))
                .findFirst().orElse(null);
    }

    // ─── Smart detection from free text ─────────────────────────────── //
    private static final Set<String> BENEFICIARY_STOPWORDS = Set.of(
            "ltd", "limited", "llp", "inc", "corp", "corporation", "co", "company",
            "gmbh", "ag", "sa", "sarl", "kg", "kk", "pte", "pty", "bv", "nv",
            "pvt", "private", "and", "the", "of"
    );
    private static final Pattern NAME_SPLIT = Pattern.compile("[\\s&,.]+");

    public List<Map<String, Object>> detectBeneficiaryMentions(String userText) {
        if (userText == null) return List.of();
        String text = userText.toLowerCase();
        List<Map<String, Object>> matches = new ArrayList<>();
        for (Map<String, Object> b : BENEFICIARIES) {
            String name = ((String) b.get("name")).toLowerCase();
            List<String> tokens = Arrays.stream(NAME_SPLIT.split(name))
                    .filter(t -> t.length() >= 4 && !BENEFICIARY_STOPWORDS.contains(t))
                    .toList();
            if (tokens.isEmpty()) continue;
            if (tokens.stream().allMatch(text::contains)) matches.add(b);
        }
        return matches;
    }

    public List<Map<String, Object>> detectAccountMentions(String userText) {
        if (userText == null) return List.of();
        String text = userText.toLowerCase();
        Set<Integer> seen = new HashSet<>();
        List<Map<String, Object>> matches = new ArrayList<>();
        for (Map<String, Object> a : DEBIT_ACCOUNTS) {
            int id = (Integer) a.get("id");
            if (seen.contains(id)) continue;
            String code = ((String) a.get("code")).toLowerCase();
            String kind = ((String) a.get("kind")).toLowerCase();
            String name = ((String) a.get("name")).toLowerCase();
            boolean hit = text.contains(code) || text.contains(kind)
                    || Arrays.stream(name.split("\\s+"))
                            .filter(w -> w.length() >= 5)
                            .anyMatch(text::contains);
            if (hit) {
                matches.add(a);
                seen.add(id);
            }
        }
        return matches;
    }
}
