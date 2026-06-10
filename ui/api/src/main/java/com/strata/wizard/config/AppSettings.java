package com.strata.wizard.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/**
 * Typed view onto application.yaml — equivalent of FastAPI's Settings dataclass.
 */
@Configuration
@ConfigurationProperties(prefix = "strata")
public class AppSettings {

    private Cors cors = new Cors();
    private Ollama ollama = new Ollama();
    private Pg pg = new Pg();

    public Cors getCors() { return cors; }
    public Ollama getOllama() { return ollama; }
    public Pg getPg() { return pg; }

    public static class Cors {
        /** Comma-separated allow-list, mirrors CORS_ORIGINS env var. */
        private String origins = "";
        public String getOrigins() { return origins; }
        public void setOrigins(String origins) { this.origins = origins; }
        public String[] originsArray() {
            return origins == null || origins.isBlank()
                    ? new String[0]
                    : origins.split("\\s*,\\s*");
        }
    }

    public static class Ollama {
        private String url = "http://host.docker.internal:11434";
        private String model = "qwen2.5:7b";
        private int timeoutSeconds = 60;
        public String getUrl() { return url; }
        public void setUrl(String url) { this.url = url; }
        public String getModel() { return model; }
        public void setModel(String model) { this.model = model; }
        public int getTimeoutSeconds() { return timeoutSeconds; }
        public void setTimeoutSeconds(int timeoutSeconds) { this.timeoutSeconds = timeoutSeconds; }
    }

    public static class Pg {
        private String schema = "data_mart";
        public String getSchema() { return schema; }
        public void setSchema(String schema) { this.schema = schema; }
    }
}
