# strata UI module

A small Angular + FastAPI module on top of the strata stack. Three screens:

| Screen | What it does |
|---|---|
| **Tables** | Lists every base table in `data_mart` with row counts. Click a table to page through its rows (50/page, ordered by `id` when present). |
| **Dashboards** | Embeds Superset's full UI (home, dashboard list, chart list, drag-and-drop editor) in an iframe. A toolbar of strata buttons — Home, All dashboards, All charts, + New dashboard, + New chart — drives the iframe via direct `src` navigation. |
| **New payment** | Form-driven insert into `data_mart.fact_pay_payment`. Sets `last_updated_time = NOW()` so the next strata ingest's watermark window propagates the row into Iceberg. |

## Layout

```
ui/
├── api/               # FastAPI backend
│   ├── strata_api/    # source
│   ├── requirements.txt
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
| `api`   | `8000`    | FastAPI — reads/writes Postgres for the Tables and New-payment screens |
| `web`   | `4200`    | Angular bundle served by nginx. nginx also reverse-proxies `/api/* → api:8000`, `/api/v1/* → superset:8088`, and Superset's top-level paths (`/superset/`, `/dashboard/`, `/chart/`, `/static/`, `/login/`, `/sqllab/`, …) → `superset:8088`. Everything is one origin: no CORS, no third-party cookie issues. |

Override ports via env vars: `API_HOST_PORT`, `UI_HOST_PORT`.

## Run

The whole UI module comes up with the rest of the strata stack:

```bash
./local/scripts/setup.sh     # builds + starts everything

# Once the stack is healthy:
open http://localhost:4200   # Angular UI
open http://localhost:8000/docs   # FastAPI OpenAPI (debug)
```

First-time builds take ~2 minutes (Node downloads Angular CLI, FastAPI
installs deps). Subsequent restarts are ~10 seconds.

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
                    ├── /api/*                → api:8000      (FastAPI)
                    ├── /api/v1/*             → superset:8088 (Superset REST)
                    └── /superset/, /dashboard/, /chart/,     → superset:8088
                        /login/, /sqllab/, /static/, …

  api:8000 ──psycopg2──▶ postgres:5432
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

## Why FastAPI in a Python project that also has Angular

The strata framework is Python; mixing in a Node API service would add a
second language for the same set of conventions (DB creds, secret files,
search_path pinning). The FastAPI service reuses `psycopg2` and the same
connection patterns as `local/postgres/bootstrap.py`. Only the front-end
is Node.
