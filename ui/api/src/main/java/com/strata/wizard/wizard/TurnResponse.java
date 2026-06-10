package com.strata.wizard.wizard;

import com.strata.wizard.rails.Selector;

import java.util.List;
import java.util.Map;

/** POST /api/wizard/turn response — mirrors the Python TurnResponse. */
public record TurnResponse(
        List<ToolCall> tool_calls,
        List<Selector.Candidate> candidates,
        List<String> available_fields,
        List<ValidationResult> validation,
        Map<String, Object> derived,
        String raw_message
) {
    public record ValidationResult(String field_id, boolean ok, String error) {}
}
