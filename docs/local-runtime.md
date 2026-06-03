# Local runtime — end-to-end walkthrough

A complete step-by-step guide to running strata locally on your laptop, from
clone to dashboard, using PostgreSQL + Spark + Trino + Superset all in Docker.

Architecture overview is in [`local/README.md`](../local/README.md). This file
is the literal click-by-click walkthrough.

## Prerequisites

| Required | Version | Check |
|---|---|---|
| Docker Desktop / OrbStack / colima | 24+ | `docker --version` |
| Docker Compose | v2 | `docker compose version` |
| RAM allocated to Docker | ≥ 4 GB | Docker Desktop settings |
| Disk free | ≥ 2 GB | `df -h .` |
| Web browser | any | for Superset UI |

You do **not** need Python, Spark, or any AWS account on your laptop — the
pipeline runs entirely inside containers.

## Step 0 — Clone and enter the repo

```bash
git clone https://github.com/your-org/strata.git
cd strata
```

## Step 1 — Start the stack (one command)

```bash
./local/scripts/setup.sh
```

This script does the following, with progress printed at each step:

1. `docker compose up -d` — pulls images and starts:
   - `strata-postgres` — source database + Iceberg JDBC catalog
   - `strata-spark` — Spark runtime (long-running, used via `exec`)
   - `strata-trino` — query engine
   - `strata-superset` — dashboard tool
2. Waits for Postgres to be healthy.
3. Copies `local/secrets/db.local.json.example` to `local/secrets/db.local.json`
   if it doesn't exist. **Note**: if you cloned the repo with an older
   `.gitignore` that excluded the whole `local/secrets/` directory, the
   `.example` file may be missing too. In that case `setup.sh` will fail
   loudly and tell you how to write the JSON by hand. The current
   `.gitignore` keeps `*.example` and `.gitkeep` inside `secrets/`.

   The PostgreSQL JDBC driver JAR (`postgresql-42.7.3.jar`) is **baked
   into the Spark Docker image** via `local/spark/Dockerfile`. It's
   downloaded once during `docker compose build` and placed in
   `$PYSPARK_HOME/jars/`. We do not rely on Spark's `spark.jars.packages`
   Ivy mechanism for the driver because on a fresh laptop that
   sometimes lands the JAR where the Iceberg catalog finds it but the
   `spark.read.format("jdbc")` source reader doesn't — manifesting as a
   confusing `ClassCastException: org.postgresql.Driver` during the
   extract step. If you see that error, rebuild the image:
   `docker compose -f local/docker-compose.yml build spark && docker compose -f local/docker-compose.yml up -d --force-recreate spark`.
4. Installs Python dependencies inside the Spark container.
5. **Runs the bootstrap seeder via `./local/scripts/seed.sh`** which:
   - Stages `bootstrap.py` into the running spark container (no
     PyPI/Docker Hub dependency — corporate-network safe)
   - Runs it with `--reset` so facts are fresh
   - Verifies `data_mart.fact_payment` row count afterwards and
     exits non-zero if the seed silently failed
   - Populates: 6 currencies, 5 data owners, 8 accounts, 6 payment
     methods, 15,000 payments over 30 days, 720 balance rows

   If the seed fails, `setup.sh` aborts with an actionable message
   rather than continuing. **You can re-run the seeder by itself any
   time** with `./local/scripts/seed.sh` (add `--wipe-state` to also
   clear the SQLite watermark + Iceberg warehouse so the next ingest
   starts clean).
6. Waits for Trino and Superset to be healthy.

Expected wall-clock time on first run: **3–5 minutes** (image pulls). On
subsequent runs: **30–60 seconds**.

When it finishes you'll see:

```
Setup complete. Next:
  ./local/scripts/ingest.sh DIM_CURRENCY --full-refresh
  ./local/scripts/ingest.sh FACT_PAYMENT --full-refresh
  ./local/scripts/trino.sh
  open http://localhost:8088
```

### Step 1a (optional) — Verify the source data populated

```bash
docker compose -f local/docker-compose.yml exec postgres \
  psql -U strata -d data_mart -c "
    SELECT 'dim_currency', COUNT(*) FROM data_mart.dim_currency
    UNION ALL SELECT 'dim_data_owner', COUNT(*) FROM data_mart.dim_data_owner
    UNION ALL SELECT 'dim_account', COUNT(*) FROM data_mart.dim_account
    UNION ALL SELECT 'dim_payment_method', COUNT(*) FROM data_mart.dim_payment_method
    UNION ALL SELECT 'fact_payment', COUNT(*) FROM data_mart.fact_payment
    UNION ALL SELECT 'fact_balance', COUNT(*) FROM data_mart.fact_balance;"
```

Expected:

```
     ?column?       | count
--------------------+-------
 dim_currency       |     6
 dim_data_owner     |     5
 dim_account        |     8
 dim_payment_method |     6
 fact_payment       | 15000
 fact_balance       |   720
```

## Step 2 — Run the first ingestion

Start with dimensions (they're small, fast, and other tables depend on them
semantically):

```bash
./local/scripts/ingest.sh DIM_CURRENCY --full-refresh
```

You'll see structured log lines like:

```
EVENT=run_started run_id='local::DIM_CURRENCY::20260601T120000Z' ...
Window: lower=None upper=2026-06-01T12:00:00+00:00 full_refresh=True
EVENT=state_reconciled current_watermark=None pending=None
Extracting query: (SELECT * FROM data_mart.dim_currency) AS extract_query
Writing 6 rows to iceberg.silver_shared.dim_currency
EVENT=run_completed rows_written=6 new_watermark=... duration_s=12.34
```

First ingest takes longer (Spark cold start downloads Iceberg JARs from
Maven). Subsequent ingests are 3–5× faster.

### Run the rest

```bash
./local/scripts/ingest.sh DIM_DATA_OWNER --full-refresh
./local/scripts/ingest.sh DIM_ACCOUNT --full-refresh
./local/scripts/ingest.sh DIM_PAYMENT_METHOD --full-refresh
./local/scripts/ingest.sh FACT_BALANCE --full-refresh
./local/scripts/ingest.sh FACT_PAYMENT --full-refresh
```

Or in one shot, in dependency order:

```bash
./local/scripts/run-all.sh --full-refresh
```

## Step 3 — Query the lake via Trino

### Quick verification from CLI

```bash
./local/scripts/trino.sh
```

```sql
trino> SHOW SCHEMAS FROM iceberg;
       Schema
--------------------
 information_schema
 silver_balances
 silver_payments
 silver_shared

trino> USE iceberg.silver_payments;

trino:silver_payments> SHOW TABLES;
   Table
-------------
 fact_payment

trino:silver_payments> SELECT COUNT(*), MAX(last_updated_time) FROM fact_payment;
 _col0 |          _col1
-------+--------------------------
 15000 | 2026-05-31 23:59:59.000
```

### Run the sample query pack

```bash
docker compose -f local/docker-compose.yml exec -T trino \
  trino --catalog iceberg < local/queries/sample.sql
```

This runs all queries in [`local/queries/sample.sql`](../local/queries/sample.sql) —
row counts, daily volumes, distributions, Iceberg snapshot history, and
file-level statistics.

### Inspect Iceberg snapshots (proves idempotency works)

```sql
trino> SELECT
         committed_at,
         operation,
         summary['glue.run_id'] AS run_id,
         summary['glue.row_count'] AS rows
       FROM iceberg.silver_payments."fact_payment$snapshots"
       ORDER BY committed_at DESC;
```

Every commit is tagged with the `glue.run_id` that produced it. This is the
idempotency mechanism: a retry with the same `run_id` skips the write if a
matching snapshot already exists.

## Step 4 — Build a dashboard in Superset

### Login

Open **http://localhost:8088** in a browser. Login:

| Username | Password |
|---|---|
| `admin` | `admin` |

The Trino database connection is already registered as **"Trino (strata)"**.

### Create a dataset

1. Top nav: **Data → Datasets → + DATASET**
2. **Database**: `Trino (strata)`
3. **Schema**: `silver_payments`
4. **Table**: `fact_payment`
5. Click **CREATE DATASET AND CREATE CHART**

### Build your first chart

1. **Chart type**: Bar Chart
2. **Time column**: `value_date`
3. **Time grain**: Day
4. **Metric**: SUM(amount)
5. Click **RUN**
6. Click **SAVE**, name it "Daily Payment Volume"

### Add it to a dashboard

1. Top nav: **Dashboards → + DASHBOARD**
2. Drag your chart from the right panel onto the canvas
3. Save as "Payment Operations"

### Try the SQL Lab

For ad-hoc queries: **SQL → SQL Lab**, select `Trino (strata)` as the
database, schema `silver_payments`. Paste any query from
[`local/queries/sample.sql`](../local/queries/sample.sql).

## Step 5 — Test incremental ingestion

So far we've done `--full-refresh`. Now exercise the watermark path:

```bash
# Add one more day to the source (without --reset, this appends)
docker run --rm --network strata-network \
  -e PGHOST=postgres -e PGUSER=strata -e PGPASSWORD=strata -e PGDATABASE=data_mart \
  -v "$(pwd)/local/postgres:/work:ro" \
  python:3.11-slim \
  sh -c "pip install --quiet psycopg2-binary && python /work/bootstrap.py --days 1 --payments-per-day 250"

# Now run incrementally (no --full-refresh flag)
./local/scripts/ingest.sh FACT_PAYMENT
```

You'll see:

```
Window: lower='2026-05-31T23:59:59' upper='2026-06-01T12:34:56' full_refresh=False
Extracting query: (SELECT * FROM data_mart.fact_payment
                   WHERE last_updated_time > TIMESTAMP '2026-05-31T23:59:59'
                   AND last_updated_time <= TIMESTAMP '2026-06-01T12:34:56') AS extract_query
Writing 250 rows to iceberg.silver_payments.fact_payment
EVENT=run_completed rows_written=250
```

Only the new rows came across. The watermark in SQLite advances.

### Inspect the watermark state

```bash
docker compose -f local/docker-compose.yml exec spark \
  sqlite3 /data/state/strata.db \
  "SELECT table_name, current_watermark, last_run_status, last_run_rows
   FROM strata_state"
```

## Step 6 — Test recovery scenarios

### Simulate a crash

```bash
# Start an ingestion in the background
./local/scripts/ingest.sh FACT_PAYMENT &
INGEST_PID=$!

# Wait a bit, then kill the Spark process mid-flight
sleep 8
docker compose -f local/docker-compose.yml exec spark pkill -9 java
wait $INGEST_PID || true

# Inspect the orphaned lock
docker compose -f local/docker-compose.yml exec spark \
  sqlite3 /data/state/strata.db \
  "SELECT pending_run_id, pending_expires_at FROM strata_state
   WHERE table_name='FACT_PAYMENT'"

# Run again — recovery handles the orphan automatically
./local/scripts/ingest.sh FACT_PAYMENT
```

Look for log lines like `Case C: previous run ... wrote N rows but didn't
advance state. Completing it now.` That's the snapshot-based recovery in
action.

### Schema drift

```bash
# Add a column (additive — auto-evolves)
docker compose -f local/docker-compose.yml exec postgres \
  psql -U strata -d data_mart \
  -c "ALTER TABLE data_mart.fact_payment ADD COLUMN new_col VARCHAR(100)"

./local/scripts/ingest.sh FACT_PAYMENT
# Log: "Schema evolution: new column new_col"

# Now break it (incompatible type change)
docker compose -f local/docker-compose.yml exec postgres \
  psql -U strata -d data_mart \
  -c "ALTER TABLE data_mart.fact_payment ALTER COLUMN amount TYPE VARCHAR(50)"

./local/scripts/ingest.sh FACT_PAYMENT
# Exit code 3, log: "Schema drift: amount: incoming string vs existing decimal(18,2)"
# Manual ALTER TABLE in Trino required to resolve. See docs/operational-runbook.md
```

## Step 7 — Reset between iterations

```bash
# Drop just the lake (keeps source data + watermarks)
rm -rf local/data/warehouse local/data/state/strata.db

# Drop everything and start fresh
docker compose -f local/docker-compose.yml down -v
rm -rf local/data/
./local/scripts/setup.sh
```

## Step 8 — Teardown

```bash
# Stop containers but keep volumes
docker compose -f local/docker-compose.yml down

# Stop containers + delete all volumes
docker compose -f local/docker-compose.yml down -v
```

## Troubleshooting

The canonical reference for "what just went wrong on my laptop." Every
row corresponds to a real failure mode someone has hit, with the
mechanical cause and the fix command. If you hit something not in this
table, add it.

### Stack-level (containers, services, ports)

| Symptom | Cause | Fix |
|---|---|---|
| `setup.sh` fails on `pg_isready` | Postgres container not started or unhealthy | Check `docker ps`. If postgres isn't listed, look at `docker compose -f local/docker-compose.yml logs postgres` for the underlying error. |
| Ingest fails with "connection refused" | Spark container can't reach postgres | Confirm both are on the `strata-network` Docker network. `docker network inspect strata-network` should list both. |
| Trino UI shows "no tables" or "no schemas" | Trino caches catalog state at startup; if Spark wrote after Trino booted, Trino may not see it yet | Restart Trino: `docker compose -f local/docker-compose.yml restart trino`. Wait ~10s, retry. |
| Superset login fails or hangs | First-time Superset init runs DB migrations and creates the admin user; takes 30-60s on first boot | Wait longer. Check progress with `docker compose -f local/docker-compose.yml logs -f superset`. Default creds are `admin / admin`. |
| First Spark ingest is very slow (~2 min on first run) | Iceberg JARs downloading from Maven Central via `spark.jars.packages` | Expected. Subsequent runs are warm because the Ivy cache at `/root/.ivy2/cache/` inside the spark container is populated. |
| `Permission denied` on warehouse files | Linux-host UID mismatch with container UID | `sudo chown -R $(id -u):$(id -g) local/data` |

### Configuration & secrets

| Symptom | Cause | Fix |
|---|---|---|
| Ingest fails: `secret file not found in /app/secrets/db.local.json` (or `setup.sh` complains about a missing `.example`) | An older `.gitignore` excluded the whole `local/secrets/` directory at clone time, so neither the real secret nor the `.example` template made it into the working tree | Pull latest (the `.gitignore` now keeps `*.example` and `.gitkeep` inside `secrets/`). If you can't pull, create the file by hand: `setup.sh` step 3 prints the exact JSON to paste, or see [setup-local.prompt.md](../.github/prompts/setup-local.prompt.md). |
| Ingest fails: `Config error: Invalid YAML in /app/config/tables.local.yaml: expected '<document start>', but found '<block mapping start>'` at line 5 column 1 | Hidden UTF-8 BOM at the top of `tables.local.yaml` (3 bytes: `EF BB BF`). PyYAML 6.x treats it as content and gets confused about document boundaries | Confirm BOM with `head -1 local/config/tables.local.yaml \| hexdump -C \| head -1` (first three bytes are `ef bb bf` if it's a BOM). Reset from the repo: `git checkout HEAD -- local/config/tables.local.yaml`. Or strip in place: `sed -i.bak '1s/^\xEF\xBB\xBF//' local/config/tables.local.yaml`. Prevent recurrence by setting your editor to "UTF-8" not "UTF-8 with BOM". |

### Spark / JDBC driver

| Symptom | Cause | Fix |
|---|---|---|
| Ingest fails: `ClassCastException: org.postgresql.Driver` or `ClassNotFoundException: org.postgresql.Driver` during the extract step (after Iceberg catalog already connected fine) | Spark's `spark.read.format("jdbc")` source reader can't find the driver class. `spark.jars.packages` lands the JAR where the Iceberg catalog finds it but not always where Spark's reflection-based driver lookup finds it | The repo's Spark Dockerfile bakes the JAR into `$PYSPARK_HOME/jars/`. Pull latest, then rebuild the image: `docker compose -f local/docker-compose.yml build --no-cache spark && docker compose -f local/docker-compose.yml up -d --force-recreate spark`. Verify: `docker compose -f local/docker-compose.yml exec spark ls /usr/local/lib/python3.10/dist-packages/pyspark/jars/ \| grep postgresql` should print `postgresql-42.7.3.jar`. |

### Iceberg catalog / warehouse consistency

| Symptom | Cause | Fix |
|---|---|---|
| Ingest fails: `org.apache.iceberg.exceptions.NotFoundException: Failed to open input stream for file: file:/data/warehouse/silver_*/metadata/...metadata.json` | Stale Iceberg JDBC catalog. The catalog (in Postgres tables `public.iceberg_tables` and `public.iceberg_namespace_properties`) points at metadata files that were wiped from the warehouse. Catalog + warehouse must stay in lockstep. | Drop the orphan catalog rows: `docker compose -f local/docker-compose.yml exec -T postgres psql -U strata -d data_mart -c "DROP TABLE IF EXISTS public.iceberg_tables; DROP TABLE IF EXISTS public.iceberg_namespace_properties;"`. Then re-run: `./local/scripts/run-all.sh --full-refresh`. Going forward, `./local/scripts/seed.sh --wipe-state` cleans all three storage tiers (SQLite state + warehouse + Iceberg catalog) atomically. |
| Ingests succeed but `rows_written=0` for every table; dashboards empty; Trino sees the schemas but zero rows | Postgres data mart is empty (bootstrap seeder silently failed on an earlier setup). Strata is doing exactly what it's supposed to: extracting from an empty source and committing a zero-row snapshot. Watermark advances, so subsequent runs would also write zero. | `./local/scripts/seed.sh --wipe-state` (re-seeds Postgres + clears stale watermark + drops orphan catalog), then `./local/scripts/run-all.sh --full-refresh`. Should see `rows_written=15000` for `FACT_PAYMENT`. |

### Day-to-day operations

| Symptom | Cause | Fix |
|---|---|---|
| Need fresh data without losing state | You want more synthetic data layered on top of what's already there | `./local/scripts/seed.sh --no-reset --days 7 --payments-per-day 200` |
| Want a completely clean slate | Test something from scratch | `docker compose -f local/docker-compose.yml down -v && rm -rf local/data/state local/data/warehouse && ./local/scripts/setup.sh` |
| Bash script error: `EXTRA_ARGS[@]: unbound variable` from `seed.sh` | Empty array expanded under bash `set -u` strict mode. Fixed in repo by using `${ARR[@]+"${ARR[@]}"}` form. | Pull latest. |

## What you've verified

After completing this walkthrough you've exercised:

- ✓ Source database → Iceberg lake on local Spark
- ✓ Iceberg JDBC catalog shared between Spark (writer) and Trino (reader)
- ✓ SQLite-backed watermark state machine
- ✓ Full-refresh and incremental modes
- ✓ Snapshot-based recovery from a simulated crash
- ✓ Schema evolution (additive and breaking)
- ✓ Query via Trino SQL
- ✓ Dashboard building in Superset

The same logical pipeline runs in AWS — see [`docs/aws-runtime.md`](aws-runtime.md).
