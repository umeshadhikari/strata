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

`setup.sh` from Step 1 already ran the seeder as step 5 — but if you
ever need to re-seed by hand (Postgres got wiped, you want fresh
random data, the earlier seed silently failed on a prior version of
the setup script):

```bash
./local/scripts/seed.sh
```

This stages `bootstrap.py` into the running spark container (no PyPI
or Docker Hub network needed — the container already has psycopg2),
runs it with `--reset`, and verifies row counts afterwards. Failure
exits non-zero with an actionable message. To also wipe the SQLite
state DB so the next ingest's watermark starts clean (the safer
choice if you're re-seeding because previous ingests landed on empty
data):

```bash
./local/scripts/seed.sh --wipe-state
```

The seeder populates the **production-shape 15-table data mart**:

- 9 dimensions seeded by the DDL itself (`dim_date`, `dim_data_owner`,
  `dim_user`, `dim_classification`, `dim_routing`,
  `dim_as_transaction_type`, `dim_as_characteristics`,
  `dim_pay_characteristics`, `dim_pay_bank_status`)
- 2 dimensions seeded by `bootstrap.py` (`dim_currency` — 10 ISO
  currencies; `dim_account` — 100 accounts)
- 4 fact tables — 30 days × per-day counts
  (`fact_as_balance`, `fact_as_transaction`,
  `fact_as_currency_exchange`, `fact_pay_payment`)

Confirm with:

```bash
docker compose -f local/docker-compose.yml exec -T postgres \
  psql -U strata -d data_mart -c \
  "SELECT COUNT(*) AS payments FROM data_mart.fact_pay_payment;"
```

Expected: `3000` (defaults: 30 days × 100 payments/day).

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

SELECT COUNT(*) FROM iceberg.silver_payments.fact_pay_payment;
-- 3000

SELECT counterparty_country_code, SUM(amount) AS volume
FROM iceberg.silver_payments.fact_pay_payment
GROUP BY counterparty_country_code
ORDER BY volume DESC;
```

If you see ~15 country rows with volumes spread across alpha-2 codes
(US, GB, DE, FR, CH, JP, SG, HK, AU, CA, BR, MX, IN, AE, ZA), the
payments pipeline is healthy. For the balances domain:

```sql
SELECT SUM(amount_in_default_currency) AS total_balance_usd
FROM iceberg.silver_balances.fact_as_balance;
-- Expect ~tens of billions on default 30-day seed.

SELECT COUNT(*) FROM iceberg.silver_balances.fact_as_currency_exchange;
-- 2700 (30 days × 10×9 pairs).
```

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
| Ingest fails: `Config error: Invalid YAML in /app/config/tables.local.yaml: expected '<document start>', but found '<block mapping start>'` at line 5 col 1 | Hidden UTF-8 BOM at top of `tables.local.yaml`. PyYAML 6.x treats it as content and fails parsing. The file in the repo is clean; the BOM gets added when an editor (VS Code with "UTF-8 with BOM" encoding, Windows Notepad, etc.) saves over it. | Confirm with `head -1 local/config/tables.local.yaml \| hexdump -C \| head -1` — first three bytes `ef bb bf` means it's a BOM. Reset from repo: `git checkout HEAD -- local/config/tables.local.yaml`. Or strip in place: `sed -i.bak '1s/^\xEF\xBB\xBF//' local/config/tables.local.yaml`. Prevent recurrence by setting your editor encoding to plain "UTF-8" (no BOM). |
| Ingest fails: `ClassCastException: org.postgresql.Driver` or `ClassNotFoundException: org.postgresql.Driver` during the extract step (after Iceberg catalog already connected fine) | Spark's `spark.read.format("jdbc")` source reader can't find the driver class. `spark.jars.packages` lands the JAR where the Iceberg catalog finds it but not always where Spark's reflection-based driver lookup finds it. | Repo's Spark Dockerfile bakes the JAR into `$PYSPARK_HOME/jars/`. Pull latest, then rebuild: `docker compose -f local/docker-compose.yml build --no-cache spark && docker compose -f local/docker-compose.yml up -d --force-recreate spark`. Verify: `docker compose -f local/docker-compose.yml exec spark ls /usr/local/lib/python3.10/dist-packages/pyspark/jars/ \| grep postgresql` should print `postgresql-42.7.3.jar`. |
| Ingest fails: `org.apache.iceberg.exceptions.NotFoundException: Failed to open input stream for file: file:/data/warehouse/silver_*/metadata/...metadata.json` | Stale Iceberg JDBC catalog. The catalog (Postgres tables `public.iceberg_tables` and `public.iceberg_namespace_properties`) points at metadata files that were wiped from the warehouse. Catalog + warehouse must stay in lockstep — they are two separate storage tiers. | Drop the orphan catalog rows: `docker compose -f local/docker-compose.yml exec -T postgres psql -U strata -d data_mart -c "DROP TABLE IF EXISTS public.iceberg_tables; DROP TABLE IF EXISTS public.iceberg_namespace_properties;"`. Then re-run: `./local/scripts/run-all.sh --full-refresh`. Going forward, `./local/scripts/seed.sh --wipe-state` cleans all three tiers atomically (SQLite + warehouse + catalog). |
| Ingests succeed but `rows_written=0` for every table; dashboards empty; Trino sees the schemas but zero rows | Postgres data mart is empty — bootstrap seeder never ran or failed silently. Strata is doing exactly what it's supposed to: extracting from an empty source and committing a zero-row snapshot. Watermark advances, so subsequent runs would also write zero. | `./local/scripts/seed.sh --wipe-state` (re-seeds Postgres + clears stale watermark + drops orphan catalog), then `./local/scripts/run-all.sh --full-refresh`. Should see `rows_written=3000` for `FACT_PAY_PAYMENT` (and ~9000 / 6000 / 2700 for `FACT_AS_BALANCE` / `FACT_AS_TRANSACTION` / `FACT_AS_CURRENCY_EXCHANGE`). |
| `./local/scripts/seed.sh` errors with `EXTRA_ARGS[@]: unbound variable` at line 89 | Bash strict mode (`set -u`) doesn't tolerate `"${EMPTY_ARRAY[@]}"` expansion. Fixed in the repo. | Pull latest. |
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
| Re-seed Postgres | `./local/scripts/seed.sh` |
| Re-seed + clear strata state in one go | `./local/scripts/seed.sh --wipe-state` |
| Run ingest for one table | `./local/scripts/ingest.sh FACT_PAY_PAYMENT` |
| Run ingest for all 15 tables | `./local/scripts/run-all.sh` |
| Run ingest for all with full refresh | `./local/scripts/run-all.sh --full-refresh` |
| Inspect strata state | `./local/scripts/inspect-state.sh` |
| Inject test data into payments + ingest + verify | `./local/scripts/add-incremental-data.sh --inserts 100 && ./local/scripts/run-and-verify.sh --expect-delta 100` |
| Same against the balances fact | `./local/scripts/add-incremental-data.sh --table fact_as_balance --inserts 100 && ./local/scripts/run-and-verify.sh --table FACT_AS_BALANCE --expect-delta 100` |
| Open Trino CLI | `./local/scripts/trino.sh` |

## Resetting between iterations

Five flavours of reset, ordered lightest → heaviest. The detailed
reference with copy-paste commands lives in
[`docs/local-runtime.md` §Step 7](../../docs/local-runtime.md#step-7--reset-between-iterations)
and [`local/README.md` §Reset](../../local/README.md#reset). The five
modes in one sentence each:

| # | When to use | One-liner |
|---|---|---|
| 1 | More rows, same schema | `./local/scripts/seed.sh --no-reset --days 7 --payments-per-day 200 && ./local/scripts/run-all.sh` |
| 2 | State + warehouse drifted from Postgres | `./local/scripts/seed.sh --wipe-state && ./local/scripts/run-all.sh --full-refresh` |
| 3 | Source DDL changed (e.g. picking up new tables.local.yaml + init.sql) | `docker compose -f local/docker-compose.yml down -v && rm -rf local/data/state local/data/warehouse && ./local/scripts/setup.sh && ./local/scripts/run-all.sh --full-refresh` |
| 4 | Factory reset everything | `docker compose -f local/docker-compose.yml down -v && rm -rf local/data/ && ./local/scripts/setup.sh` |
| 5 | Spark Dockerfile changed (JAR bump, etc.) | `docker compose -f local/docker-compose.yml down -v && docker compose -f local/docker-compose.yml build --no-cache spark && rm -rf local/data/ && ./local/scripts/setup.sh` |

If the user is asking "I have the old 6-table schema running, how do I
reset to the 15-table production-shape schema?" — that's option 3.

For the deeper picture, point the user at:

- [`local/README.md`](../../local/README.md) — substitute services and shared catalog details
- [`docs/local-runtime.md`](../../docs/local-runtime.md) — the full walkthrough with every detail
- [`docs/testing-incremental.md`](../../docs/testing-incremental.md) — verifying the watermark + idempotency contract

## Going further — strata against the user's own data mart

If the user wants to point strata at a real data mart (not the
synthetic local one), three repo tools handle that transition:

- **`python3 local/scripts/ddl_to_yaml.py <ddl.sql> > tables.yaml`** —
  extracts every CREATE TABLE from a SQL file into a draft
  `tables.yaml`. Mechanical fields auto-filled; judgment fields
  (`domain`, `partition_spec` refinement, `sort_order`) marked TODO.
  Supports `--merge <existing.yaml>` for incremental updates when
  the DDL evolves. Pure Python, no Docker required.
- **`python3 examples/seed_full_datamart.py`** — populates
  `dim_account`, `dim_currency`, and the four facts with synthetic
  data while skipping dims that are already seeded by INSERTs in the
  DDL. Idempotent on dims; pass `--reset` to truncate facts.
- **`docs/translating-ddl-to-yaml.md`** — the full step-by-step
  guide for the office-laptop scenario where the DDL must stay local.

If the user mentions a data mart, DDL, or seeding tables in a
production-shape schema, point them at these three.

## What this workflow does NOT do

- It does not configure a real AWS account. For that, use the
  `/new-customer` prompt or `docs/aws-runtime.md`.
- It does not seed production-realistic data. The synthetic data is
  uniform and predictable — useful for testing, not for performance
  benchmarking.
- It does not run the unit test suite. Use `pytest` from the repo root
  for that.
