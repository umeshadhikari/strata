package com.strata.wizard.rails;

import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.Set;

/**
 * IBAN → BIC + bank-name lookup. Port of lookups.py. Demo registry of a
 * handful of common European banks so the "auto-derive BIC from IBAN" beat
 * lands cleanly.
 */
@Component
public class Lookups {

    public record Bank(String bic, String name) {}

    private static final Map<String, Map<String, Bank>> IBAN_BANK_CODES = Map.of(
            "DE", Map.of(
                    "37040044", new Bank("COBADEFFXXX", "Commerzbank Köln"),
                    "10000000", new Bank("MARKDEF1100", "Deutsche Bundesbank Berlin"),
                    "50010517", new Bank("INGDDEFFXXX", "ING-DiBa Frankfurt"),
                    "76020070", new Bank("HYVEDEMM417", "UniCredit Bank Nürnberg"),
                    "20030000", new Bank("DEUTDEHHXXX", "Deutsche Bank Hamburg"),
                    "70070010", new Bank("DEUTDEMMXXX", "Deutsche Bank München")),
            "FR", Map.of(
                    "30002", new Bank("CRLYFRPPXXX", "Crédit Lyonnais"),
                    "30003", new Bank("SOGEFRPPXXX", "Société Générale"),
                    "30004", new Bank("BNPAFRPPXXX", "BNP Paribas"),
                    "30056", new Bank("CCFRFRPPXXX", "HSBC France")),
            "NL", Map.of(
                    "ABNA", new Bank("ABNANL2AXXX", "ABN AMRO"),
                    "INGB", new Bank("INGBNL2AXXX", "ING Bank Nederland"),
                    "RABO", new Bank("RABONL2UXXX", "Rabobank"),
                    "BUNQ", new Bank("BUNQNL2AXXX", "bunq")),
            "GB", Map.of(
                    "BARC", new Bank("BARCGB22XXX", "Barclays Bank"),
                    "NWBK", new Bank("NWBKGB2LXXX", "NatWest"),
                    "LOYD", new Bank("LOYDGB21XXX", "Lloyds Bank"),
                    "MIDL", new Bank("MIDLGB22XXX", "HSBC UK")),
            "ES", Map.of(
                    "2100", new Bank("CAIXESBBXXX", "CaixaBank"),
                    "0049", new Bank("BSCHESMMXXX", "Banco Santander"),
                    "0182", new Bank("BBVAESMMXXX", "BBVA"))
    );

    private static final Set<String> EIGHT_DIGIT = Set.of("DE");
    private static final Set<String> FIVE_DIGIT = Set.of("FR", "ES");
    private static final Set<String> FOUR_LETTER = Set.of("NL", "GB");

    /** Return {bic, name} for the bank an IBAN belongs to, or null. */
    public Bank ibanToBank(String iban) {
        if (iban == null) return null;
        String v = iban.replaceAll("\\s+", "").toUpperCase();
        if (v.length() < 9) return null;
        String cc = v.substring(0, 2);
        String bankCode;
        if (EIGHT_DIGIT.contains(cc)) bankCode = v.substring(4, 12);
        else if (FIVE_DIGIT.contains(cc)) bankCode = v.substring(4, 9);
        else if (FOUR_LETTER.contains(cc)) bankCode = v.substring(4, 8);
        else return null;
        Map<String, Bank> byCode = IBAN_BANK_CODES.get(cc);
        return byCode == null ? null : byCode.get(bankCode);
    }

    private static final Map<String, String> COUNTRY_CURRENCY = Map.ofEntries(
            Map.entry("DE", "EUR"), Map.entry("FR", "EUR"), Map.entry("NL", "EUR"),
            Map.entry("IT", "EUR"), Map.entry("ES", "EUR"), Map.entry("BE", "EUR"),
            Map.entry("AT", "EUR"), Map.entry("IE", "EUR"), Map.entry("PT", "EUR"),
            Map.entry("FI", "EUR"), Map.entry("GR", "EUR"), Map.entry("LU", "EUR"),
            Map.entry("EE", "EUR"), Map.entry("LV", "EUR"), Map.entry("LT", "EUR"),
            Map.entry("SK", "EUR"), Map.entry("SI", "EUR"), Map.entry("CY", "EUR"),
            Map.entry("MT", "EUR"), Map.entry("HR", "EUR"),
            Map.entry("GB", "GBP"), Map.entry("US", "USD"), Map.entry("CA", "CAD"),
            Map.entry("AU", "AUD"), Map.entry("NZ", "NZD"), Map.entry("JP", "JPY"),
            Map.entry("CH", "CHF"), Map.entry("SE", "SEK"), Map.entry("NO", "NOK"),
            Map.entry("DK", "DKK"), Map.entry("IN", "INR"), Map.entry("BR", "BRL"),
            Map.entry("MX", "MXN"), Map.entry("SG", "SGD"), Map.entry("HK", "HKD"),
            Map.entry("ZA", "ZAR")
    );

    public String currencyForCountry(String country) {
        return country == null ? null : COUNTRY_CURRENCY.get(country.toUpperCase());
    }
}
