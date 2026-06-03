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

This brings up Postgres + Spark + Trino + Superset, seeds 30 days of synthetic
payment data, and verifies all four services are healthy. Expect 2–5 minutes
on first run (image pulls + first-time Iceberg JAR download).

## Run an ingestion

The ingest job runs inside the `strata-spark` container:

```bash
./local/scripts/ingest.sh DIM_CURRENCY --full-refresh
./local/scripts/ingest.sh DIM_DATA_OWNER --full-refresh
./local/scripts/ingest.sh DIM_ACCOUNT --full-refresh
./local/scripts/ingest.sh DIM_PAYMENT_METHOD --full-refresh
./local/scripts/ingest.sh FACT_BALANCE --full-refresh
./local/scripts/ingest.sh FACT_PAYMENT --full-refresh

# Or run all of them in dependency order:
./local/scripts/run-all.sh --full-refresh
```

After the first full refresh, subsequent runs are incremental:

```bash
# Simulate a new day landing in the source
docker compose -f local/docker-compose.yml exec spark \
  python /app/../local/postgres/seed.py --days 1 --payments-per-day 100

# Watermark-driven incremental run
./local/scripts/ingest.sh FACT_PAYMENT
```

## Query the lake with Trino

### CLI

```bash
./local/scripts/trino.sh
```

```sql
trino> SHOW SCHEMAS FROM iceberg;
trino> USE iceberg.silver_payments;
trino> SELECT COUNT(*), MAX(last_updated_time) FROM fact_payment;
trino> SELECT data_owner_id, SUM(amount) FROM fact_payment GROUP BY 1;
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
   SELECT value_date, SUM(amount) AS total_amount
   FROM iceberg.silver_payments.fact_payment
   GROUP BY value_date
   ORDER BY value_date
   ```
2. Click **"Explore"** to turn it into a chart.
3. Pick a visualization (line, bar, big number, etc.) and configure axes.
4. **Save**, then add to a dashboard.

For datasets that you'll reuse:

1. **Data** → **Datasets** → **+ Dataset**
2. Database: `Trino (strata)`
3. Schema: `silver_payments` (or `silver_balances`, `silver_shared`)
4. Table: e.g., `fact_payment`
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

ls local/data/warehouse/silver_payments/fact_payment/
# data/       metadata/

# Iceberg snapshot manifests — the source of truth for what's committed
ls local/data/warehouse/silver_payments/fact_payment/metadata/
```

## Test failure scenarios

The local environment is a great place to deliberately break things.

### Recovery from a crash

```bash
# Start an ingestion in the background
./local/scripts/ingest.sh FACT_PAYMENT &
SHELL_PID=$!

# Kill the Spark JVM in the middle of the run
sleep 8
docker compose -f local/docker-compose.yml exec spark pkill -9 java || true
wait $SHELL_PID || true

# Inspect the orphaned lock
docker compose -f local/docker-compose.yml exec spark \
  sqlite3 /data/state/strata.db \
  "SELECT pending_run_id, pending_expires_at FROM strata_state WHERE table_name='FACT_PAYMENT'"

# Run again — recovery should detect the orphan and proceed cleanly
./local/scripts/ingest.sh FACT_PAYMENT
# Expect "Case C" or "Case E" recovery log line
```

### Concurrent runs

```bash
./local/scripts/ingest.sh FACT_PAYMENT &
./local/scripts/ingest.sh FACT_PAYMENT &
wait
# One succeeds; the other exits cleanly with ConcurrentRunSkips
```

### Schema drift

```bash
# Additive change — Iceberg auto-evolves (safe)
docker compose -f local/docker-compose.yml exec postgres \
  psql -U strata -d data_mart \
  -c "ALTER TABLE data_mart.FACT_PAYMENT ADD COLUMN new_col VARCHAR(100)"
./local/scripts/ingest.sh FACT_PAYMENT
# Log line: "Schema evolution: new column new_col"

# Breaking change — SchemaDriftError fires
docker compose -f local/docker-compose.yml exec postgres \
  psql -U strata -d data_mart \
  -c "ALTER TABLE data_mart.FACT_PAYMENT ALTER COLUMN amount TYPE VARCHAR(50)"
./local/scripts/ingest.sh FACT_PAYMENT
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

```bash
# Drop everything except source data
rm -rf local/data/warehouse local/data/state/*

# Drop everything (source data + lake)
docker compose -f local/docker-compose.yml down -v
rm -rf local/data/
./local/scripts/setup.sh
```

## Performance notes

| Operation | Wall-clock on M-class laptop |
|---|---|
| First `setup.sh` run (pulls images, downloads Iceberg JAR) | 3–5 min |
| Subsequent stack start | 30–60 s |
| Spark cold start inside container (first ingest after restart) | 30–60 s |
| Spark warm start | 5–10 s |
| Ingest of a dimension (10–100 rows) | 5–10 s |
| Ingest of FACT_PAYMENT (15k rows) | 15–30 s |
| Trino query against the 15k-row table | 50–200 ms |
| Superset dashboard render | 0.5–2 s |

Spark cold start in Docker is the slowest piece. Keeping the spark container
running (it does by default — `tail -f /dev/null`) means second and later
ingests are warm-start fast.

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
