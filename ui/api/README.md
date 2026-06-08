# strata UI backend (FastAPI)

Lightweight FastAPI service that backs the Angular UI module. Reads from the
same Postgres `data_mart` schema strata extracts from. The dashboards UX is
an embedded Superset iframe served same-origin through nginx, so no
dashboard API surface is exposed here.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness probe |
| `GET` | `/api/tables` | List `data_mart.*` tables with row counts |
| `GET` | `/api/tables/{name}?limit&offset` | Paginated rows from a table |
| `GET` | `/api/payments/form-options` | Dropdown data for the New Payment form |
| `POST` | `/api/payments` | Insert one row into `fact_pay_payment` |

## Run inside Docker (recommended)

The `api` service in `local/docker-compose.yml` runs this automatically alongside
the rest of the strata stack. After `./local/scripts/setup.sh`:

```
http://localhost:8000/api/health        # API
http://localhost:8000/docs              # OpenAPI explorer
```

## Run locally without Docker

```bash
cd ui/api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PGHOST=localhost PGPORT=5433 uvicorn strata_api.main:app --reload
```

## Config

All via environment variables. Defaults match the Docker stack:

| Var | Default | Notes |
|---|---|---|
| `PGHOST` | `postgres` | Postgres hostname |
| `PGPORT` | `5432` | Postgres port |
| `PGDATABASE` | `data_mart` | Database name |
| `PGUSER` | `strata` | DB user |
| `PGPASSWORD` | `strata` | DB password |
| `PGSCHEMA` | `data_mart` | Schema to enumerate |
| `SUPERSET_INTERNAL_URL` | `http://superset:8088` | API calls (server→server) |
| `SUPERSET_PUBLIC_URL` | `http://localhost:8088` | iframe src (browser→Superset) |
| `SUPERSET_USER` / `SUPERSET_PASSWORD` | `admin / admin` | Superset login |
| `CORS_ORIGINS` | `http://localhost:4200,http://localhost:8080` | Comma-separated allow-list |
