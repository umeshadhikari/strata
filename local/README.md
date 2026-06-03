# Local development environment

Run the full strata pipeline on your laptop with no AWS account. Same code,
different backends.

| AWS in production | Local substitute |
|---|---|
| Aurora PostgreSQL | PostgreSQL in Docker |
| S3 storage | `local/data/warehouse/` mounted into containers |
| Apache Iceberg + Glue Catalog | Apache Iceberg + **JDBC catalog in PostgreSQL** |
| DynamoDB watermarks | SQLite at `local/data/state/strata.db` |
| Secrets Manager | JSON file at `local/secrets/db.local.json` |
| AWS Glue PySpark | PySpark running in a `bitnami/spark` container |
| CloudWatch metrics | stdout (`METRIC ...` log lines) |
| **Athena** | **Trino** at `http://localhost:8080` |
| **QuickSight** | **Apache Superset** at `http://localhost:8088` |

Both Spark and Trino share the **same Iceberg JDBC catalog** (metadata in
PostgreSQL) and the **same warehouse directory** (`./local/data/warehouse`).
Tables created by Spark are immediately visible to Trino — and therefore to
Superset.

## Architecture

```
┌──────────────┐         ┌──────────────┐
│ Postgres     │◀────────│ Spark        │  writes Iceberg via JDBC catalog
│ (data_mart)  │ JDBC    │ (ingestion)  │  reads source via JDBC
│              │         └──────────────┘
│ + Iceberg    │                │
│   catalog    │                ▼
│   tables     │         ┌──────────────┐
└──────────────┘         │ /data/       │  shared volume
        ▲                │ warehouse    │  Parquet + Iceberg metadata
        │                └──────────────┘
        │ JDBC                  ▲
        │ catalog               │
        ▼                       │
┌──────────────┐                │
│ Trino        │────────────────┘
│ (queries)    │  reads same files via same catalog
└──────────────┘
        ▲
        │ SQLAlchemy
        │
┌──────────────┐
│ Superset     │
│ (dashboards) │
└──────────────┘
```

## Service ports

| Service | Default host port | In-container port | Override env var |
|---|---|---|---|
| PostgreSQL | `5433` | `5432` | `POSTGRES_HOST_PORT` |
| Trino | `8080` | `8080` | `TRINO_HOST_PORT` |
| Superset | `8088` | `8088` | `SUPERSET_HOST_PORT` |

The Postgres host port defaults to **5433** so we don't conflict with a
Homebrew or Postgres.app install on `5432`. Inside the Docker network,
Spark and Trino always reach Postgres at `postgres:5432` — only the host-side
port changes.

To override (e.g., if 8080 is taken by another tool):

```bash
TRINO_HOST_PORT=18080 SUPERSET_HOST_PORT=18088 ./local/scripts/setup.sh
```

## Prerequisites

- Docker + Docker Compose (Docker Desktop, OrbStack, etc.)
- ~4 GB free RAM allocated to Docker
- ~2 GB free disk

## One-time setup

```bash
./local/scripts/setup.sh
```

This brings up Postgres + Spark + Trino + Superset, seeds the
production-shape data mart (15 tables — 11 dims + 4 facts: 30 days of
synthetic balances, transactions, FX rates, and payments), and verifies
all four services are healthy. Expect 2–5 minutes on first run (image
pulls + first-time Iceberg JAR download).

## Run an ingestion

The ingest job runs inside the `strata-spark` container. Tables are
listed in dim-then-fact order in `local/scripts/run-all.sh`:

```bash
# A handful of dims (full list in run-all.sh)
./local/scripts/ingest.sh DIM_CURRENCY  --full-refresh
./local/scripts/ingest.sh DIM_ACCOUNT   --full-refresh
./local/scripts/ingest.sh DIM_USER      --full-refresh

# The four facts
./local/scripts/ingest.sh FACT_AS_BALANCE          --full-refresh
./local/scripts/ingest.sh FACT_AS_TRANSACTION      --full-refresh
./local/scripts/ingest.sh FACT_AS_CURRENCY_EXCHANGE --full-refresh
./local/scripts/ingest.sh FACT_PAY_PAYMENT         --full-refresh

# Or run all 15 in dependency order:
./local/scripts/run-all.sh --full-refresh
```

After the first full refresh, subsequent runs are incremental:

```bash
# Simulate a new batch landing in the source (any of the 4 facts works)
./local/scripts/add-incremental-data.sh --inserts 100
./local/scripts/add-incremental-data.sh --table fact_as_balance --inserts 100

# Watermark-driven incremental run
./local/scripts/ingest.sh FACT_PAY_PAYMENT
./local/scripts/ingest.sh FACT_AS_BALANCE
```

## Query the lake with Trino

### CLI

```bash
./local/scripts/trino.sh
```

```sql
trino> SHOW SCHEMAS FROM iceberg;
trino> USE iceberg.silver_payments;
trino> SELECT COUNT(*), MAX(last_updated_time) FROM fact_pay_payment;
trino> SELECT data_owner_id, SUM(amount) FROM fact_pay_payment GROUP BY 1;

-- balances domain
trino> SELECT SUM(amount_in_default_currency)
       FROM iceberg.silver_balances.fact_as_balance;
```

A full set of sample queries is in [`local/queries/sample.sql`](queries/sample.sql)
— covers row counts, freshness, distributions, Iceberg snapshot history, and
partition pruning verification.

### Web UI

The Trino coordinator UI is at **http://localhost:8080**. Useful for
inspecting query history, examining query plans, and watching active queries
during a backfill.

## Build dashboards in Superset

Open **http://localhost:8088** in your browser.

**Login**: `admin` / `admin`

The Trino datasource is pre-registered as **"Trino (strata)"**. To build a
dashboard:

1. **SQL Lab** → **SQL Lab** — write a query against the Trino datasource:
   ```sql
   SELECT creation_date, SUM(amount) AS total_amount
   FROM iceberg.silver_payments.fact_pay_payment
   GROUP BY creation_date
   ORDER BY creation_date
   ```
2. Click **"Explore"** to turn it into a chart.
3. Pick a visualization (line, bar, big number, etc.) and configure axes.
4. **Save**, then add to a dashboard.

For datasets that you'll reuse:

1. **Data** → **Datasets** → **+ Dataset**
2. Database: `Trino (strata)`
3. Schema: `silver_payments` (or `silver_balances`, `silver_shared`)
4. Table: e.g., `fact_pay_payment`
5. **Add**

Then you can drag-and-drop chart fields without writing SQL.

## Inspect state and Iceberg internals

### Watermark state (the DynamoDB substitute)

```bash
docker compose -f local/docker-compose.yml exec spark \
  sqlite3 /data/state/strata.db "SELECT table_name, current_watermark, last_run_status FROM strata_state"
```

### Iceberg catalog metadata (lives in Postgres)

```bash
docker compose -f local/docker-compose.yml exec postgres \
  psql -U strata -d data_mart -c "\dn"
# Look for the iceberg JDBC catalog tables under their own schema.
```

### Warehouse files on disk

```bash
ls local/data/warehouse/
# silver_payments/  silver_balances/  silver_shared/

ls local/data/warehouse/silver_payments/fact_pay_payment/
# data/       metadata/

# Iceberg snapshot manifests — the source of truth for what's committed
ls local/data/warehouse/silver_payments/fact_pay_payment/metadata/
```

## Test failure scenarios

The local environment is a great place to deliberately break things.

### Recovery from a crash

```bash
# Start an ingestion in the background
./local/scripts/ingest.sh FACT_PAY_PAYMENT &
SHELL_PID=$!

# Kill the Spark JVM in the middle of the run
sleep 8
docker compose -f local/docker-compose.yml exec spark pkill -9 java || true
wait $SHELL_PID || true

# Inspect the orphaned lock
docker compose -f local/docker-compose.yml exec spark \
  sqlite3 /data/state/strata.db \
  "SELECT pending_run_id, pending_expires_at FROM strata_state WHERE table_name='FACT_PAY_PAYMENT'"

# Run again — recovery should detect the orphan and proceed cleanly
./local/scripts/ingest.sh FACT_PAY_PAYMENT
# Expect "Case C" or "Case E" recovery log line
```

### Concurrent runs

```bash
./local/scripts/ingest.sh FACT_PAY_PAYMENT &
./local/scripts/ingest.sh FACT_PAY_PAYMENT &
wait
# One succeeds; the other exits cleanly with ConcurrentRunSkips
```

### Schema drift

```bash
# Additive change — Iceberg auto-evolves (safe)
docker compose -f local/docker-compose.yml exec postgres \
  psql -U strata -d data_mart \
  -c "ALTER TABLE data_mart.fact_pay_payment ADD COLUMN new_col VARCHAR(100)"
./local/scripts/ingest.sh FACT_PAY_PAYMENT
# Log line: "Schema evolution: new column new_col"

# Breaking change — SchemaDriftError fires
docker compose -f local/docker-compose.yml exec postgres \
  psql -U strata -d data_mart \
  -c "ALTER TABLE data_mart.fact_pay_payment ALTER COLUMN amount TYPE VARCHAR(50)"
./local/scripts/ingest.sh FACT_PAY_PAYMENT
# Exit code 3, log line: "Schema drift: amount: incoming string vs existing decimal(18,2)"
# Operator action required — see docs/operational-runbook.md
```

## Switching back to AWS mode

The same code (`src/strata/`) runs in both places. The differences are:

| Concern | Local | AWS |
|---|---|---|
| Entry point | `python -m strata.local_ingest` | `python -m strata.ingest` (Glue auto-launches) |
| State | `LocalStateManager` (SQLite) | `StateManager` (DynamoDB) |
| Config | `load_local_config` (file) | `load_config` (S3) |
| Secrets | `load_local_credentials` (file) | `get_db_credentials` (Secrets Manager) |
| Metrics | `LocalMetrics` (stdout) | `Metrics` (CloudWatch) |
| Catalog | Iceberg JDBC catalog (PostgreSQL) | Iceberg Glue catalog |
| Warehouse | `file:///data/warehouse` | `s3://<bucket>/silver/` |

To deploy to AWS, use `terraform/` and `scripts/deploy.sh`. The local stack
exists for fast iteration; production paths are unchanged.

## Reset

Five flavours of reset, ordered from "lightest touch" to "nuke from
orbit." Pick the lowest one that matches what you actually broke —
unnecessarily wiping volumes throws away any Superset dashboards you've
built.

### 1. Re-seed Postgres only (keep the lake + Superset dashboards)

When you want more rows in the source mart but everything else is fine:

```bash
./local/scripts/seed.sh --no-reset --days 7 --payments-per-day 200
./local/scripts/run-all.sh                       # incremental — picks up new rows
```

### 2. Clear strata state + warehouse (start ingest fresh, keep Postgres + Superset)

When the SQLite watermark, the Iceberg warehouse, and the JDBC catalog
have drifted apart (typically after a manual `rm -rf` of one of them).
This is the one most worth memorizing — it clears the three storage
tiers atomically so they can't end up out of sync:

```bash
./local/scripts/seed.sh --wipe-state              # rebuilds Postgres + clears state/warehouse/catalog
./local/scripts/run-all.sh --full-refresh         # repopulates Iceberg
```

### 3. Reset to a different schema (e.g. when the source DDL changes)

When `examples/sample_datamart.ddl.sql`,
`local/postgres/init.sql`, or `local/config/tables.local.yaml` has
changed — for example, when picking up the production-shape 15-table
schema for the first time, or pulling DDL updates from your office
laptop. Postgres only re-runs `init.sql` against an empty data dir, so
the volume must come down before the new DDL applies.

```bash
# 1. Confirm the new DDL + YAML are in place (already committed in the repo).
git pull
ls local/postgres/init.sql       # the schema-creation DDL
ls local/config/tables.local.yaml # the strata table config

# 2. Stop the stack AND drop the postgres data volume so init.sql will re-apply.
docker compose -f local/docker-compose.yml down -v

# 3. Wipe the strata-side state too (catalog references the OLD tables otherwise).
rm -rf local/data/state local/data/warehouse

# 4. Bring it back up — Postgres applies init.sql, the seeder runs against the
#    new schema, run-all.sh ingests all tables listed in the new YAML.
./local/scripts/setup.sh
./local/scripts/run-all.sh --full-refresh

# 5. (Optional) Verify in Trino that the schemas + tables match the new YAML.
./local/scripts/trino.sh -e "SHOW TABLES FROM iceberg.silver_payments"
./local/scripts/trino.sh -e "SHOW TABLES FROM iceberg.silver_balances"
./local/scripts/trino.sh -e "SHOW TABLES FROM iceberg.silver_shared"
```

Superset's metadata DB lives on a named volume too, so step 2 wipes any
dashboards you've built. To preserve them, replace step 2 with
`docker compose -f local/docker-compose.yml down` (no `-v`) and instead
target just the Postgres data volume:

```bash
docker compose -f local/docker-compose.yml down
docker volume rm $(docker compose -f local/docker-compose.yml config --format json \
                   | jq -r '.volumes | keys[]' | grep postgres-data) || true
docker compose -f local/docker-compose.yml up -d
```

(jq + the named-volume incantation isn't required for steps 1, 2, and 4 —
it only matters when you want to scalpel-out one volume.)

### 4. Wipe everything (factory reset)

```bash
docker compose -f local/docker-compose.yml down -v
rm -rf local/data/
./local/scripts/setup.sh
```

This drops Postgres data, the Iceberg warehouse, the SQLite state DB,
*and* the Superset metadata DB. You'll re-seed automatically, but any
dashboards you've built will be gone.

### 5. Rebuild the spark image too (e.g. after Dockerfile changes)

```bash
docker compose -f local/docker-compose.yml down -v
docker compose -f local/docker-compose.yml build --no-cache spark
rm -rf local/data/
./local/scripts/setup.sh
```

Use this when bumping JAR versions in `local/spark/Dockerfile`, fixing
the baked-in PostgreSQL driver, or recovering from a
`ClassCastException: org.postgresql.Driver`.

## Performance notes

| Operation | Wall-clock on M-class laptop |
|---|---|
| First `setup.sh` run (pulls images, downloads Iceberg JAR) | 3–5 min |
| Subsequent stack start | 30–60 s |
| Spark cold start inside container (first ingest after restart) | 30–60 s |
| Spark warm start | 5–10 s |
| Ingest of a dimension (10–100 rows) | 5–10 s |
| Ingest of FACT_PAY_PAYMENT (~3k rows) | 10–20 s |
| Trino query against the 15k-row table | 50–200 ms |
| Superset dashboard render | 0.5–2 s |

Spark cold start in Docker is the slowest piece. Keeping the spark container
running (it does by default — `tail -f /dev/null`) means second and later
ingests are warm-start fast.

## Going to production — translating your own DDL

The local stack uses a small synthetic schema (`local/postgres/init.sql`)
to exercise strata end-to-end. When you're ready to point strata at a
real data mart, you'll need a `tables.yaml` describing every source
table, and you may need seed data for the tables that aren't populated
by the DDL itself.

Three tools in the repo make this a guided process rather than from-scratch
authoring:

**[`local/scripts/ddl_to_yaml.py`](../local/scripts/ddl_to_yaml.py)** —
parses CREATE TABLE statements from one or more `.sql` files and
emits a draft `tables.yaml` with mechanical fields filled in (table
name, primary key, candidate watermark column, candidate partition
column) and TODO markers on the judgment fields (`domain`,
`partition_spec` refinement, `sort_order`). Pure Python, no Docker.
Supports `--merge` for incremental updates when the DDL evolves.

**[`examples/seed_full_datamart.py`](../examples/seed_full_datamart.py)** —
synthetic-data seeder for the production-shape schema. Populates
`dim_account`, `dim_currency`, and the four fact tables with
realistic synthetic data while skipping the dimensions that are
seeded by INSERT statements in the DDL itself. Idempotent on dims,
configurable volumes via `--days`, `--accounts`, `--txns-per-day`.

**[`docs/translating-ddl-to-yaml.md`](../docs/translating-ddl-to-yaml.md)** —
step-by-step workflow for the office-laptop scenario where the DDL
must stay local. Covers extraction, AI-assisted TODO resolution
(via `@workspace /add-table` in Copilot Chat), validation, and the
recurring `--merge` flow.

## Troubleshooting

The full table of known failure modes (with mechanical causes and exact
fix commands) lives in **[`docs/local-runtime.md` → Troubleshooting](../docs/local-runtime.md#troubleshooting)**.
It's grouped by layer (stack-level, configuration, Spark/JDBC, Iceberg
catalog, day-to-day ops) and covers every failure that's ever bitten
someone on a fresh laptop. Always check there first.

A few highlights for the most common gotchas — see the canonical
reference for the full list:

**"connect: connection refused" when ingest tries to reach postgres** —
The spark container reaches postgres via the `strata-network` Docker
network. Make sure all services are up: `docker compose -f local/docker-compose.yml ps`.

**Trino shows no tables** — Trino caches catalog state. Restart it:
`docker compose -f local/docker-compose.yml restart trino`.

**Superset can't connect to Trino** — The connection URI uses the Docker
service name: `trino://admin@trino:8080/iceberg`. Make sure both containers
share `strata-network` (they do by default).

**"Permission denied" on warehouse files** — On Linux hosts, the bind mount
inherits root ownership from the container. `sudo chown -R $(id -u):$(id -g) local/data`.

**Ingest "succeeds" but every table reports `rows_written=0`, dashboards empty** —
Postgres data mart is empty (bootstrap seeder silently failed). Fix:
`./local/scripts/seed.sh --wipe-state && ./local/scripts/run-all.sh --full-refresh`.

**`ClassCastException: org.postgresql.Driver` during the extract step** —
JDBC driver isn't on Spark's reflection classpath. The repo's Dockerfile
bakes the JAR into `$PYSPARK_HOME/jars/`; rebuild the spark image:
`docker compose -f local/docker-compose.yml build --no-cache spark && docker compose -f local/docker-compose.yml up -d --force-recreate spark`.

**`FileNotFoundException` for a `metadata.json` under `silver_*/`** —
The Iceberg JDBC catalog (stored in Postgres) points at metadata files
that have been wiped from the warehouse. Drop the orphan catalog rows:
`docker compose -f local/docker-compose.yml exec -T postgres psql -U strata -d data_mart -c "DROP TABLE IF EXISTS public.iceberg_tables; DROP TABLE IF EXISTS public.iceberg_namespace_properties;"`,
then re-run the full refresh. Going forward, use
`./local/scripts/seed.sh --wipe-state` which clears all three storage
tiers atomically.

**`Invalid YAML in /app/config/tables.local.yaml` at line 5 col 1** —
Hidden UTF-8 BOM at the top of the file. Reset: `git checkout HEAD -- local/config/tables.local.yaml`.
Prevent recurrence by setting your editor to plain "UTF-8" not "UTF-8 with BOM".

**Iceberg JAR download fails** — First ingest fetches the runtime jars from
Maven Central. If it's slow or fails, pre-download by running:
`docker compose -f local/docker-compose.yml exec spark spark-submit --version`.
