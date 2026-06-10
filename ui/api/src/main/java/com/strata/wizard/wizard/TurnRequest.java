package com.strata.wizard.wizard;

import java.util.Map;

/** POST /api/wizard/turn payload. */
public record TurnRequest(String user_text, Map<String, Object> form_state) {}
