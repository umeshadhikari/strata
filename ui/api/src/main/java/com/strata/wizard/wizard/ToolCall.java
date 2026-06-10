package com.strata.wizard.wizard;

import java.util.Map;

/** One structured tool call the model (or prose parser) emitted. */
public record ToolCall(String name, Map<String, Object> args) {}
