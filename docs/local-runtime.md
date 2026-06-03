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
   if it doesn't exist.
4. Installs Python dependencies inside the Spark container.
5. **Runs the bootstrap script** to populate the data mart with:
   - 6 currencies (USD, EUR, GBP, JPY, CHF, SGD)
   - 5 data owners
   - 8 accounts (mix of US and IBAN)
   - 6 payment methods
   - 30 days × 500 payments = 15,000 transactions
   - 30 days × accounts × 3 currencies of balance rows
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

| Symptom | Fix |
|---|---|
| `setup.sh` fails on `pg_isready` | Postgres container not started; check `docker ps` |
| Ingest fails with "connection refused" | Spark container can't reach postgres; check both are on `strata-network` |
| Trino UI shows "no tables" | Restart Trino: `docker compose -f local/docker-compose.yml restart trino` |
| Superset login fails | Wait longer; first-time init runs migrations. Re-check `docker compose ... logs superset` |
| First Spark ingest is very slow | Iceberg JARs downloading from Maven Central; subsequent runs are warm |
| `Permission denied` on warehouse files | Linux only: `sudo chown -R $(id -u):$(id -g) local/data` |
| Need fresh data without resetting state | `python local/postgres/bootstrap.py --days 7 --payments-per-day 200` (without `--reset`) |

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
