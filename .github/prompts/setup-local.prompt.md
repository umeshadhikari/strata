---
mode: agent
description: Stand up the strata local development environment on the user's laptop — Docker Compose stack, seed data, first ingest, dashboard.
---

Walk the user through standing up strata locally on their laptop. The
goal is a working pipeline they can hack on: Postgres source, Iceberg
warehouse, Trino + Superset for querying — same code that runs on AWS,
different backends.

If the user wants to deploy to AWS instead, redirect them to the
`/new-customer` prompt or `docs/aws-runtime.md`.

## Prerequisites — confirm before starting

1. **Docker Desktop** (or Colima / Rancher Desktop) installed and
   running. Confirm with `docker info`.
2. **Docker Compose v2** — should be bundled with Docker Desktop.
   Confirm with `docker compose version`.
3. **No process on ports 5433, 8080, or 8088.** These map to Postgres,
   Trino, and Superset respectively. Defaults can be overridden by
   environment variable (see `local/docker-compose.yml`).
4. **Working directory is the repo root.** All scripts below assume so.

If Homebrew Postgres is already running on 5432, that's fine — strata
uses 5433 on the host by default.

## Step 1: Bring up the stack

```bash
./local/scripts/setup.sh
```

This builds the custom `strata-spark` image (first time only, ~2
minutes), then `docker compose up -d` brings up:

- `postgres` — source DB (data mart) + Iceberg JDBC catalog backend
- `spark` — PySpark runtime where ingestion runs
- `trino` — Athena substitute, queries Iceberg
- `superset` — QuickSight substitute, connects to Trino

Healthcheck targets:

- `docker compose -f local/docker-compose.yml ps` shows all four as `running` and `healthy` where applicable
- `curl -s http://localhost:8080/v1/info | jq .nodeVersion` returns Trino's version
- `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8088/health` returns `200`

If anything is unhealthy, run `docker compose -f local/docker-compose.yml logs <service>` and triage. The most common issue is a port collision.

## Step 2: Seed Postgres with synthetic data

```bash
docker compose -f local/docker-compose.yml exec -T spark \
  python /app/local/postgres/bootstrap.py
```

This populates:

- 6 currencies, 5 data owners, 8 accounts, 6 payment methods
- 15,000 payments over 30 days, randomly distributed
- 720 balance records (8 accounts × 30 days × 3 currencies)

Takes ~30 seconds. Confirm with:

```bash
docker compose -f local/docker-compose.yml exec -T postgres \
  psql -U strata -d data_mart -c \
  "SELECT COUNT(*) AS payments FROM data_mart.fact_payment;"
```

Expected: `15000`.

## Step 3: Run the first ingest

```bash
./local/scripts/run-all.sh
```

This iterates through every table in `local/config/tables.local.yaml`
(dims first, then facts) and runs `python -m strata.local_ingest` for
each. Expected wall-clock: ~3 minutes total.

The log output should end with one `EVENT=run_completed` line per
table. Each one carries `rows_written: <N>` matching the source count.

## Step 4: Verify in Trino

Open Trino's web UI at `http://localhost:8080` or query from the CLI:

```bash
./local/scripts/trino.sh
```

In the Trino shell:

```sql
SHOW SCHEMAS FROM iceberg;
-- silver_balances, silver_payments, silver_shared, information_schema

SELECT COUNT(*) FROM iceberg.silver_payments.fact_payment;
-- 15000

SELECT counterparty_country, SUM(amount) AS volume
FROM iceberg.silver_payments.fact_payment
GROUP BY counterparty_country
ORDER BY volume DESC;
```

If you see 12 country rows with volumes in the $25M-$35M range, the
pipeline is healthy.

## Step 5: Open Superset

Browse to `http://localhost:8088`. Log in with `admin / admin` (created
by `superset_init.sh` on first boot).

The Trino database connection is auto-registered. Confirm:

- Settings → Database Connections → "Trino (strata)" exists.
- Click into it → Test Connection → should succeed.

If the database connection is missing, recreate it manually:

- Connection URL: `trino://admin@trino:8080/iceberg`
- Database name: `Trino (strata)`

## Step 6: Try the testing-incremental workflow (optional but recommended)

This is the fastest way to confirm strata's watermark contract actually
holds on your laptop. Three commands:

```bash
./local/scripts/inspect-state.sh
./local/scripts/add-incremental-data.sh --inserts 100
./local/scripts/run-and-verify.sh --expect-delta 100
```

Expected output ends with:

```
✓ expected iceberg delta +100 matches actual +100
```

If the delta is wrong, see `docs/testing-incremental.md` Troubleshooting.

## Step 7: (Optional) Build the Payments Analytics dashboard

If the user wants to play with dashboards, `docs/local-runtime.md` has a
walkthrough for building the Payments Analytics dashboard (world map,
Sankey, payment method mix, top approvers). Five charts, ~10 minutes if
done by hand, or import from the dashboard JSON if exported.

## Common issues — and the fix

| Symptom | Cause | Fix |
|---|---|---|
| Ingest fails: `secret file not found in /app/secrets/db.local.json` (or `setup.sh` complains about a missing `.example`) | An older `.gitignore` excluded the whole `local/secrets/` directory at clone time, so neither the real secret nor the `.example` template made it into the working tree. | Pull latest (the `.gitignore` now keeps `*.example` and `.gitkeep`), then `cp local/secrets/db.local.json.example local/secrets/db.local.json`. Or write the JSON inline — see `setup.sh` for the exact shape (postgres / 5432 / data_mart / strata / strata). |
| Ingest fails: `ClassCastException: org.postgresql.Driver` or `ClassNotFoundException: org.postgresql.Driver` during full refresh | Spark's JDBC source reader can't find the driver class on its classpath. With `spark.jars.packages` alone the JAR sometimes lands where the Iceberg catalog can see it but the source reader can't. The fix bakes the JAR directly into `$PYSPARK_HOME/jars/` via the Spark Dockerfile. | Pull latest, then **rebuild the image**: `docker compose -f local/docker-compose.yml build spark && docker compose -f local/docker-compose.yml up -d --force-recreate spark`. Verify with `docker compose -f local/docker-compose.yml exec spark ls /usr/local/lib/python3.10/dist-packages/pyspark/jars/ \| grep postgresql` — should show `postgresql-42.7.3.jar`. |
| `setup.sh` fails: `image bitnami/spark:3.5.0 not found` | Bitnami changed their tagging. Our `Dockerfile` builds from `eclipse-temurin:17-jdk-jammy` instead. | Pull latest; the Dockerfile in `local/spark/` should be current |
| Port 5432 already in use | Local Postgres (Homebrew) | Default config maps to 5433 — no action needed unless you also have something on 5433 |
| `connector.name=iceberg` error in Trino logs | Wrong properties in `local/trino/etc/catalog/iceberg.properties` | Use the minimal version in the repo; don't add `iceberg.metadata-cache.enabled` or `fs.native-local.enabled` |
| Superset shows "Working outside of application context" | Flask app context imports out of order in `superset_init.sh` | Reorder so model imports are inside `with app.app_context():` |
| `Schema drift: is_iban_account: incoming smallint vs existing int` | Type promotion needed | The writer's `_SAFE_WIDENINGS` matrix handles this; if you still see the error, ensure `smallint → int` is in the set |
| Sankey shows "Pick exactly 2 columns" | Superset's standard Sankey is 2-stage only | Use two side-by-side 2-stage Sankeys, or build a virtual dataset with UNION ALL |

## After setup, your day-to-day commands

| What | Command |
|---|---|
| Bring stack up | `docker compose -f local/docker-compose.yml up -d` |
| Bring stack down (keep data) | `docker compose -f local/docker-compose.yml down` |
| Wipe everything (start clean) | `docker compose -f local/docker-compose.yml down -v && rm -rf local/data/state local/data/warehouse` |
| Run ingest for one table | `./local/scripts/ingest.sh FACT_PAYMENT` |
| Run ingest for all | `./local/scripts/run-all.sh` |
| Inspect strata state | `./local/scripts/inspect-state.sh` |
| Inject test data + ingest + verify | `./local/scripts/add-incremental-data.sh --inserts 100 && ./local/scripts/run-and-verify.sh --expect-delta 100` |
| Open Trino CLI | `./local/scripts/trino.sh` |

For the deeper picture, point the user at:

- [`local/README.md`](../../local/README.md) — substitute services and shared catalog details
- [`docs/local-runtime.md`](../../docs/local-runtime.md) — the full walkthrough with every detail
- [`docs/testing-incremental.md`](../../docs/testing-incremental.md) — verifying the watermark + idempotency contract

## What this workflow does NOT do

- It does not configure a real AWS account. For that, use the
  `/new-customer` prompt or `docs/aws-runtime.md`.
- It does not seed production-realistic data. The synthetic data is
  uniform and predictable — useful for testing, not for performance
  benchmarking.
- It does not run the unit test suite. Use `pytest` from the repo root
  for that.
