# strata UI module

A small Angular + Spring Boot module on top of the strata stack. Four screens:

| Screen | What it does |
|---|---|
| **Tables** | Lists every base table in `data_mart` with row counts. Click a table to page through its rows (50/page, ordered by `id` when present). |
| **Dashboards** | Embeds Superset's full UI (home, dashboard list, chart list, drag-and-drop editor) in an iframe. A toolbar of strata buttons — Home, All dashboards, All charts, + New dashboard, + New chart — drives the iframe via direct `src` navigation. |
| **New payment** | Form-driven insert into `data_mart.fact_pay_payment`. Sets `last_updated_time = NOW()` so the next strata ingest's watermark window propagates the row into Iceberg. |
| **AI payment wizard** | Conversational, A2UI-style payment entry powered by Qwen 2.5 via Ollama. The user describes the payment in natural language; the model emits tool calls (`set_field`, `select_rail`, `ask`, `explain`) that patch a form rendered dynamically from a rail registry. Form fields morph by rail — IBAN/BIC for SEPA, sort code for UK FPS, routing+account for US ACH, IFSC or UPI for India, a single PIX key for Brazil, full beneficiary address + charges code for SWIFT. |

## Layout

```
ui/
├── api/               # Spring Boot 3.3 backend (Java 21)
│   ├── src/main/java/com/strata/wizard/   # source
│   ├── src/main/resources/                # registry.yaml, application.yaml
│   ├── pom.xml
│   ├── Dockerfile
│   └── README.md
└── web/               # Angular 17 standalone app
    ├── src/
    ├── package.json
    ├── angular.json
    ├── nginx.conf
    └── Dockerfile
```

Both services are wired into `local/docker-compose.yml`:

| Service | Host port | What it is |
|---|---|---|
| `api`   | `8000`    | Spring Boot — Postgres for the Tables/New-payment screens, Ollama proxy + rail registry for the AI payment wizard |
| `web`   | `4200`    | Angular bundle served by nginx. nginx also reverse-proxies `/api/* → api:8000`, `/api/v1/* → superset:8088`, and Superset's top-level paths (`/superset/`, `/dashboard/`, `/chart/`, `/static/`, `/login/`, `/sqllab/`, …) → `superset:8088`. Everything is one origin: no CORS, no third-party cookie issues. |

Override ports via env vars: `API_HOST_PORT`, `UI_HOST_PORT`.

## Run

The whole UI module comes up with the rest of the strata stack:

```bash
./local/scripts/setup.sh     # builds + starts everything

# Once the stack is healthy:
open http://localhost:4200                 # Angular UI
open http://localhost:8000/actuator/health # Spring Boot health probe
open http://localhost:8000/actuator/prometheus  # metrics for Prom
```

First-time builds take ~3-4 minutes (Maven downloads dependencies, Node
downloads Angular CLI). Subsequent restarts are ~10 seconds.

Bringing just the UI up after the rest is running:

```bash
docker compose -f local/docker-compose.yml up -d --build api web
```

Rebuilding after a code change in `ui/api/` or `ui/web/`:

```bash
docker compose -f local/docker-compose.yml build api web
docker compose -f local/docker-compose.yml up -d api web
```

## Architecture sketch

```
  Browser ──http──▶ nginx :80 (web)
                    │
                    ├── /                     → Angular SPA (static)
                    ├── /api/*                → api:8000      (Spring Boot)
                    ├── /api/v1/*             → superset:8088 (Superset REST)
                    └── /superset/, /dashboard/, /chart/,     → superset:8088
                        /login/, /sqllab/, /static/, …

  api:8000 ──JdbcTemplate──▶ postgres:5432
          ──RestClient────▶ ollama:11434 (qwen2.5:7b, host network)
```

Everything the browser talks to is on `localhost:4200` — including the
embedded Superset UI. That keeps the Superset session cookie first-party,
so the iframe sees the same admin login the user has in any other tab,
and clicks inside the iframe (chart editor, dataset picker, etc.) work
without any cross-origin patchwork.

## Superset embedding notes

The compose file bind-mounts `local/superset/superset_config.py` and
points Superset at it via `SUPERSET_CONFIG_PATH`. The relevant settings:

```python
# Drop the SAMEORIGIN / strict-CSP defaults so we can iframe at all.
TALISMAN_ENABLED = False
HTTP_HEADERS = {"X-Frame-Options": "ALLOWALL"}

# Same-origin iframe load → SameSite=Lax is correct (and avoids
# Chrome's SameSite=None + Secure=True requirement on plain HTTP).
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = False  # localhost is http; flip to True in prod
SESSION_COOKIE_HTTPONLY = True

# Trust X-Forwarded-Host (= "localhost:4200") from nginx so Superset
# builds redirect URLs with the right host:port.
ENABLE_PROXY_FIX = True

# Optional read-only access for anonymous viewers.
PUBLIC_ROLE_LIKE = "Gamma"
```

The matching nginx side lives in `ui/web/nginx.conf`. It forwards
`Host` and `X-Forwarded-Host` as `$http_host` (with port) and lists
every Superset top-level path in a single regex location.

## AI payment wizard (Qwen via Ollama)

The wizard lives at `/wizard`. Server-side, it loads a YAML rail registry
(`ui/api/src/main/resources/rails/registry.yaml`) and exposes:

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/wizard/rails`       | Full rail registry for the dynamic form |
| `GET`  | `/api/wizard/iban-lookup` | IBAN → `{bic, name}` (demo IBAN registry) |
| `POST` | `/api/wizard/select-rail` | Pure deterministic rail selector (debug) |
| `POST` | `/api/wizard/turn`        | Single conversational turn — proxies to Ollama, returns parsed tool calls + validations + auto-derived fields |

The model is restricted to four tool calls (`set_field`, `select_rail`,
`ask`, `explain`); rail selection candidates are computed in code (not by
the LLM); validators (`iban` mod-97, `aba_routing` mod-10, `bic`,
`uk_sort_code`, `ifsc_code`, `upi_vpa`, `pix_key`) reject obviously bad
input and surface errors back to the chat strip.

### Prereqs

```bash
# On the host (NOT inside the docker stack):
ollama serve              # if not already running
ollama pull qwen2.5:7b
```

Ollama listens on `host:11434`. The API container reaches it via
`host.docker.internal:11434` (Docker Desktop) or
`http://172.17.0.1:11434` (Linux) — override with the `OLLAMA_URL` env
var before bringing the stack up.

### Demo script (the four "wow" moments)

1. *"Send 5,000 EUR to Acme GmbH in Germany, IBAN DE89 3704 0044 0532 0130 00"* — form populates, SEPA Inst selected, BIC auto-derived.
2. *"Actually make it 5,000 USD to their US office, routing 021000021 account 7654321"* — form **morphs**: IBAN disappears, routing + account + account-type radio appear.
3. *"Pay 2,000,000 GBP to our London office, same-day"* — rail picker shows FPS (over limit) and SWIFT, user clicks.
4. *"What's a CLABE?"* → inline 2-sentence explanation, form state untouched. Then *"Pay 200 BRL to consultor@itau.com.br via PIX"* → form collapses to a single field.

### Known Superset 3.1 quirk

The `+ DASHBOARD` / `+ CHART` buttons on Superset's own home page
sometimes navigate to port-less URLs from inside an iframe (a
client-side bug in Superset's React code). The Angular shell sidesteps
this by exposing its own **+ New dashboard** / **+ New chart** buttons
in the toolbar — they set the iframe's `src` to `/dashboard/new/` and
`/chart/add` directly, which is reliable because the server-side
redirect preserves the port via `ENABLE_PROXY_FIX`.

## Local API responses

Curl a couple of endpoints to see the shapes the Angular code consumes:

```bash
curl -s http://localhost:8000/api/health | jq .
curl -s http://localhost:8000/api/tables | jq '.tables | length, .tables[0]'
curl -s 'http://localhost:8000/api/tables/dim_currency?limit=3' | jq .
```

## Inserting a payment from the UI

1. Open `http://localhost:4200/new-payment`.
2. Pick an ordering account, currency, data owner; set an amount; choose a
   counterparty country (the name auto-fills); enter a counter account
   number; submit.
3. The new row lands in `data_mart.fact_pay_payment` with the current UTC
   timestamp as `last_updated_time`.
4. Run the incremental ingest to propagate it into Iceberg:

   ```bash
   ./local/scripts/ingest.sh FACT_PAY_PAYMENT
   # or
   ./local/scripts/run-and-verify.sh --expect-delta 1
   ```

5. Re-open the dashboard — the new row is included in
   `iceberg.silver_payments.fact_pay_payment` via the snapshot strata
   just committed.

## Why Spring Boot for the API

This is the production fit for a fintech team: virtual threads (Java 21)
hold thousands of in-flight Ollama waits cheaply, Spring Actuator gives
SREs first-class Prometheus + health endpoints out of the box, and
Resilience4j wraps the LLM call with bulkhead / circuit breaker / retry
so a slow Ollama degrades gracefully instead of cascading. The rail
registry and validators live in `ui/api/` — see its README for the
architectural notes and "add a rail" walkthrough.

(An earlier FastAPI version of the same API lived under `ui/api/`. The
Spring Boot service replaced it like-for-like; same endpoint paths, same
JSON shapes, and the Angular frontend works against either without any
code change.)
