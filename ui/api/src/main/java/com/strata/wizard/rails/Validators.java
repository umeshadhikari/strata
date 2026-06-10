package com.strata.wizard.rails;

import org.springframework.stereotype.Component;

import java.math.BigInteger;
import java.util.Map;
import java.util.function.Function;
import java.util.regex.Pattern;

/**
 * Like-for-like port of validators.py — IBAN mod-97, ABA mod-10, format
 * checks for BIC/sort code/IFSC/UPI/PIX. Demo-grade.
 */
@Component
public class Validators {

    public record Result(boolean ok, String error) {
        // Factories renamed to avoid clashing with the auto-generated `ok()`
        // accessor that records emit for the boolean component above.
        public static Result pass() { return new Result(true, null); }
        public static Result fail(String error) { return new Result(false, error); }
    }

    private final Map<String, Function<String, Result>> table = Map.of(
            "iban", this::iban,
            "bic", this::bic,
            "uk_sort_code", this::ukSortCode,
            "uk_account_number", this::ukAccountNumber,
            "aba_routing", this::abaRouting,
            "ifsc_code", this::ifscCode,
            "upi_vpa", this::upiVpa,
            "pix_key", this::pixKey
    );

    /** Run a named validator; unknown names are treated as pass (don't fail-closed). */
    public Result validate(String name, String value) {
        Function<String, Result> fn = table.get(name);
        return fn == null ? Result.pass() : fn.apply(value == null ? "" : value);
    }

    // ── individual checks ────────────────────────────────────────────── //

    private static final Pattern IBAN_RX = Pattern.compile("^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$");

    public Result iban(String raw) {
        String v = raw.replaceAll("\\s+", "").toUpperCase();
        if (!IBAN_RX.matcher(v).matches()) {
            return Result.fail("IBAN: country (2 letters) + check digits (2) + 11–30 chars");
        }
        String rearr = v.substring(4) + v.substring(0, 4);
        StringBuilder digits = new StringBuilder();
        for (char c : rearr.toCharArray()) {
            if (Character.isLetter(c)) digits.append(c - 55);
            else digits.append(c);
        }
        try {
            return new BigInteger(digits.toString()).mod(BigInteger.valueOf(97))
                    .equals(BigInteger.ONE) ? Result.pass() : Result.fail("IBAN checksum failed");
        } catch (NumberFormatException ex) {
            return Result.fail("IBAN contains invalid characters");
        }
    }

    private static final Pattern BIC_RX = Pattern.compile("^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$");

    public Result bic(String raw) {
        String v = raw.replaceAll("\\s+", "").toUpperCase();
        return BIC_RX.matcher(v).matches()
                ? Result.pass() : Result.fail("BIC must be 8 or 11 chars (e.g. DEUTDEFF or DEUTDEFFXXX)");
    }

    public Result ukSortCode(String raw) {
        String v = raw.replaceAll("[-\\s]", "");
        return v.matches("^\\d{6}$") ? Result.pass() : Result.fail("Sort code must be 6 digits (e.g. 20-30-40)");
    }

    public Result ukAccountNumber(String raw) {
        return raw.replaceAll("\\s+", "").matches("^\\d{8}$")
                ? Result.pass() : Result.fail("Account number must be 8 digits");
    }

    public Result abaRouting(String raw) {
        String v = raw.replaceAll("[-\\s]", "");
        if (!v.matches("^\\d{9}$")) return Result.fail("ABA routing must be 9 digits");
        int[] weights = {3, 7, 1, 3, 7, 1, 3, 7, 1};
        int total = 0;
        for (int i = 0; i < 9; i++) total += (v.charAt(i) - '0') * weights[i];
        return total % 10 == 0 ? Result.pass() : Result.fail("ABA routing checksum failed");
    }

    private static final Pattern IFSC_RX = Pattern.compile("^[A-Z]{4}0[A-Z0-9]{6}$");

    public Result ifscCode(String raw) {
        String v = raw.replaceAll("\\s+", "").toUpperCase();
        return IFSC_RX.matcher(v).matches()
                ? Result.pass() : Result.fail("IFSC must be 4 bank letters + 0 + 6 alphanumeric (e.g. HDFC0001234)");
    }

    public Result upiVpa(String raw) {
        return raw.matches("^[a-zA-Z0-9._-]+@[a-zA-Z][a-zA-Z0-9]*$")
                ? Result.pass() : Result.fail("UPI ID must be name@bank (e.g. acme@hdfc)");
    }

    public Result pixKey(String raw) {
        String v = raw.trim();
        if (v.matches("^[\\w.+-]+@[\\w-]+\\.[\\w.-]+$")) return Result.pass();          // email
        if (v.replaceAll("\\s+", "").matches("^\\+55\\d{10,11}$")) return Result.pass(); // phone
        String digits = v.replaceAll("[.\\-/]", "");
        if (digits.matches("^\\d{11}$")) return Result.pass();                           // CPF
        if (digits.matches("^\\d{14}$")) return Result.pass();                           // CNPJ
        if (v.toLowerCase().matches("^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"))
            return Result.pass();                                                        // EVP UUID
        return Result.fail("PIX key must be email, +55 phone, CPF, CNPJ, or 32-char UUID");
    }
}
