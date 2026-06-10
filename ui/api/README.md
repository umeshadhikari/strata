# strata UI backend (Spring Boot)

The Java/Spring Boot service that backs the Angular UI module. Reads the
same `data_mart` schema strata extracts from, exposes the rail registry
and saved beneficiaries for the A2UI payment wizard, and proxies a local
Ollama instance for the LLM-driven turns.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness probe |
| `GET` | `/api/tables` | List `data_mart.*` tables with row counts |
| `GET` | `/api/tables/{name}?limit&offset` | Paginated rows from a table |
| `GET` | `/api/payments/form-options` | Dropdown data for the New Payment form |
| `POST` | `/api/payments` | Insert one row into `fact_pay_payment` |
| `GET` | `/api/wizard/rails` | Full rail registry — drives the dynamic form |
| `GET` | `/api/wizard/accounts?currency&q` | Debit accounts, currency-filterable |
| `GET` | `/api/wizard/beneficiaries?q&country` | Saved beneficiaries for typeahead |
| `GET` | `/api/wizard/beneficiaries/{id}` | Single beneficiary record |
| `GET` | `/api/wizard/iban-lookup?iban=` | IBAN → BIC + bank name |
| `POST` | `/api/wizard/select-rail` | Deterministic rail selector (debug) |
| `POST` | `/api/wizard/turn` | One conversational turn — proxies Qwen via Ollama, returns tool calls + validation + auto-derived fields |

## Run

The `api` service in `local/docker-compose.yml` runs this automatically
alongside the rest of the strata stack. After `./local/scripts/setup.sh`:

```bash
http://localhost:8000/api/health        # API
http://localhost:8000/actuator/health   # liveness/readiness
http://localhost:8000/actuator/prometheus
```

Standalone, without Docker:

```bash
cd ui/api
./mvnw spring-boot:run
# or
PGHOST=localhost PGPORT=5433 OLLAMA_URL=http://localhost:11434 \
  ./mvnw spring-boot:run
```

## Config

Everything is env-var driven. Defaults match the local Docker stack.

| Var | Default | Notes |
|---|---|---|
| `PGHOST` | `postgres` | Postgres host |
| `PGPORT` | `5432` | Postgres port |
| `PGDATABASE` | `data_mart` | Database name |
| `PGUSER` / `PGPASSWORD` | `strata` / `strata` | DB credentials |
| `PGSCHEMA` | `data_mart` | Schema for tables endpoints |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama base URL. Linux: `http://172.17.0.1:11434` |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Model to call. Pull it ahead of time: `ollama pull qwen2.5:7b` |
| `CORS_ORIGINS` | `http://localhost:4200,http://localhost:8080` | Comma-separated allow-list |

## Architecture

- **Rail registry stays in YAML.** `src/main/resources/rails/registry.yaml`
  is loaded once at startup by `RailsRegistry`. The same file works for
  any consumer that wants the registry, so the Angular form renderer
  receives it raw via `GET /api/wizard/rails`.

- **Rail selection is deterministic, not AI.** `Selector.selectRails(...)`
  encodes `(country, currency, amount)` → candidate rails in plain Java.
  The LLM only picks from the candidates the selector produced — it
  cannot invent a rail.

- **Prose-fallback parser.** When Qwen 2.5 7B emits tool calls as text
  instead of via the function-calling interface, `ProseParser` finds the
  `set_field(args)` patterns in the raw content and converts them to
  real `ToolCall` objects. Without this, terse prompts occasionally
  produce silent stalls on the 7B model.

- **Auto-derive BIC.** When `set_field(iban, …)` is processed, the
  service runs the IBAN through `Lookups.ibanToBank` and adds `bic` +
  `_bank_name` to the response's `derived` map. The Angular layer
  highlights the auto-filled BIC in soft green.

- **Virtual threads.** `spring.threads.virtual.enabled=true` lets a
  single backend instance hold thousands of in-flight `/turn` requests
  cheaply while they wait on Ollama. The blocking `RestClient` code is
  unchanged.

- **Resilience4j on the Ollama call.** `OllamaClient.chatCompletion` is
  wrapped in `@Bulkhead` (caps concurrency), `@CircuitBreaker` (opens
  when Ollama is unhealthy), and `@Retry` (transient network only). A
  fallback method returns a synthetic message so a failed turn surfaces
  in the chat strip as "AI assistant temporarily unavailable — please
  fill the form manually" rather than hanging.

- **Operational visibility.** Spring Actuator + Micrometer Prometheus
  registry expose JVM, HTTP, JDBC, and Resilience4j metrics at
  `/actuator/prometheus`.

## Build / test

```bash
./mvnw verify                       # unit + integration tests
./mvnw spring-boot:build-image      # OCI image without Dockerfile
docker build -t strata-api:local .  # via the multi-stage Dockerfile
```

## Adding a payment rail

1. Append the rail's field list to `src/main/resources/rails/registry.yaml`.
2. If any of those fields use a new validator name (e.g. `validate: clabe`),
   add a corresponding method to `Validators.java` and wire it into the
   `table` map in the same file.
3. If the rail has new beneficiaries, add them to `Directory.BENEFICIARIES`.
4. Rebuild — the Angular form picks up the new rail automatically.

No frontend code changes needed. The dynamic form renderer reads the
registry shape directly.
