package com.strata.wizard;

import com.strata.wizard.config.AppSettings;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/** /api/health — liveness probe matching the FastAPI route. */
@RestController
public class HealthController {

    private final AppSettings settings;

    public HealthController(AppSettings settings) {
        this.settings = settings;
    }

    @GetMapping("/api/health")
    public Map<String, String> health() {
        return Map.of("status", "ok", "schema", settings.getPg().getSchema());
    }
}
