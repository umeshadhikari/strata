package com.strata.wizard.wizard;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Component;

import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Fallback for when Qwen 2.5 7B emits tool calls as prose text instead of
 * via the function-calling interface (which it occasionally does on terse
 * prompts). Direct port of parse_prose_tool_calls() in the Python wizard.
 *
 * Input:  "set_field(amount, 200, 0.99), set_field(currency, \"BRL\", 0.95)"
 * Output: two ToolCall objects equivalent to what the model should have emitted.
 */
@Component
public class ProseParser {

    private static final Pattern CALL_RX =
            Pattern.compile("(set_field|select_rail|ask|explain)\\s*\\(([^()]*)\\)");

    private static final Map<String, List<String>> ARG_NAMES = Map.of(
            "set_field",   List.of("field_id", "value", "confidence"),
            "select_rail", List.of("rail_id", "why"),
            "ask",         List.of("field_id", "prompt", "choices"),
            "explain",     List.of("topic", "body")
    );

    private final ObjectMapper json = new ObjectMapper();

    public List<ToolCall> parse(String raw) {
        if (raw == null || raw.isBlank()) return List.of();
        List<ToolCall> out = new ArrayList<>();
        Matcher m = CALL_RX.matcher(raw);
        while (m.find()) {
            String name = m.group(1);
            List<String> parts = splitArgs(m.group(2));
            List<String> names = ARG_NAMES.getOrDefault(name, List.of());
            Map<String, Object> args = new LinkedHashMap<>();
            for (int i = 0; i < Math.min(parts.size(), names.size()); i++) {
                args.put(names.get(i), coerce(parts.get(i)));
            }
            out.add(new ToolCall(name, args));
        }
        return out;
    }

    /** Comma-split that honours string quoting. */
    private static List<String> splitArgs(String s) {
        List<String> out = new ArrayList<>();
        StringBuilder buf = new StringBuilder();
        boolean inStr = false;
        char strCh = 0;
        int depth = 0;
        for (char ch : s.toCharArray()) {
            if (inStr) {
                buf.append(ch);
                if (ch == strCh) inStr = false;
            } else if (ch == '\'' || ch == '"') {
                inStr = true;
                strCh = ch;
                buf.append(ch);
            } else if ("[{(".indexOf(ch) >= 0) {
                depth++;
                buf.append(ch);
            } else if ("]})".indexOf(ch) >= 0) {
                depth--;
                buf.append(ch);
            } else if (ch == ',' && depth == 0) {
                out.add(buf.toString().trim());
                buf.setLength(0);
            } else {
                buf.append(ch);
            }
        }
        if (buf.length() > 0) out.add(buf.toString().trim());
        return out;
    }

    /** Best effort: JSON literal → string-with-quotes-stripped → bare ident. */
    private Object coerce(String raw) {
        String r = raw.trim();
        if (r.isEmpty()) return null;
        try {
            return json.readValue(r, Object.class);
        } catch (Exception ignored) {}
        if ((r.startsWith("\"") && r.endsWith("\"")) || (r.startsWith("'") && r.endsWith("'"))) {
            return r.substring(1, r.length() - 1);
        }
        return r;
    }
}
