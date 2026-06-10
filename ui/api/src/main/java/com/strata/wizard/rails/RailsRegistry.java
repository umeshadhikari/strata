package com.strata.wizard.rails;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import jakarta.annotation.PostConstruct;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.io.InputStream;
import java.util.*;

/**
 * In-memory cache of the rail registry. Loaded once from
 * src/main/resources/rails/registry.yaml at startup — the FastAPI port
 * does the same on first access.
 *
 * Kept as Map<String, Object> on purpose: the YAML schema is intentionally
 * loose (different rails have different field shapes) and the dynamic form
 * renderer on the Angular side wants the same raw shape via JSON.
 */
@Component
public class RailsRegistry {

    private Map<String, Object> root = Map.of();

    @PostConstruct
    public void load() throws IOException {
        ObjectMapper mapper = new ObjectMapper(new YAMLFactory());
        try (InputStream in = new ClassPathResource("rails/registry.yaml").getInputStream()) {
            //noinspection unchecked
            root = mapper.readValue(in, Map.class);
        }
    }

    /** Whole registry envelope — used by GET /api/wizard/rails. */
    public Map<String, Object> asMap() {
        return Map.of(
                "rails", rails(),
                "common_fields", commonFields()
        );
    }

    @SuppressWarnings("unchecked")
    public Map<String, Map<String, Object>> rails() {
        return (Map<String, Map<String, Object>>) root.getOrDefault("rails", Map.of());
    }

    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> commonFields() {
        return (List<Map<String, Object>>) root.getOrDefault("common_fields", List.of());
    }

    public Map<String, Object> getRail(String railId) {
        return railId == null ? null : rails().get(railId);
    }

    /** Common + rail-specific fields, in render order. */
    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> fieldsForRail(String railId) {
        List<Map<String, Object>> out = new ArrayList<>(commonFields());
        Map<String, Object> rail = getRail(railId);
        if (rail != null) {
            List<Map<String, Object>> fields = (List<Map<String, Object>>) rail.getOrDefault("fields", List.of());
            out.addAll(fields);
        }
        return out;
    }

    public List<String> fieldIdsForRail(String railId) {
        return fieldsForRail(railId).stream()
                .map(f -> (String) f.get("id"))
                .filter(Objects::nonNull)
                .toList();
    }

    public Map<String, Object> fieldDef(String railId, String fieldId) {
        if (fieldId == null) return null;
        for (Map<String, Object> f : fieldsForRail(railId)) {
            if (fieldId.equals(f.get("id"))) return f;
        }
        return null;
    }
}
