# Testing strata's incremental data movement

This guide is the operational playbook for verifying that strata's
watermark-bounded incremental ingestion behaves correctly on your laptop.
It covers four scenarios — insert-only delta, updates re-flow, idempotency
on retry, and failure recovery — using the helper scripts under
`local/scripts/`.

If you're new to the architecture, skim [docs/reliability.md](reliability.md)
first. The TL;DR is that every run:

- Reads `current_watermark` from the state DB and freezes `upper = now()` at run start
- Extracts `WHERE last_updated_time > lower AND last_updated_time <= upper`
- Writes an Iceberg snapshot tagged with `glue.run_id`, `glue.watermark_lower`,
  `glue.watermark_upper`, `glue.row_count`
- Advances `current_watermark` in the state DB on success

That's the contract these tests prove holds.

## Prerequisites

You should already have the local stack running per
[docs/local-runtime.md](local-runtime.md):

```bash
# starts Postgres + Spark + Trino + Superset
./local/scripts/setup.sh

# seeds Postgres and runs the first ingest
./local/scripts/run-all.sh
```

> **zsh on Mac**: trailing `# comment` on a command line is treated as
> arguments, not a comment, unless you've run `setopt interactive_comments`.
> Throughout this doc, comments are kept on their own line for that reason.
> If you paste a command and see an "unrecognized arguments" error, strip
> any trailing `# ...` from the line.

After `run-all.sh` you have ~3K payments, ~6K balances, ~6K transactions,
and ~2.7K FX rates in Postgres (defaults: 30 days × per-day counts in
`bootstrap.py`); the same row counts in Iceberg under
`iceberg.silver_payments.*` and `iceberg.silver_balances.*`; and a per-table
watermark in the SQLite state DB pointing to whenever the seeder finished.

## The three helper scripts

| Script | What it does |
|---|---|
| `./local/scripts/inspect-state.sh` | Prints state from Postgres + SQLite + Iceberg side by side. Defaults to `FACT_PAY_PAYMENT`; override with `--table`. Add `--json` for diffable output. |
| `./local/scripts/add-incremental-data.sh` | Injects test data into Postgres with `last_updated_time = NOW()`. Supports `--inserts N`, `--updates N`, `--mixed N` against any of the four facts via `--table` (default `fact_pay_payment`). |
| `./local/scripts/run-and-verify.sh` | Inspects state, runs ingest, inspects again, prints the delta. Defaults to `FACT_PAY_PAYMENT`; override with `--table`. Add `--expect-delta N` to make it a pass/fail check. |

All three execute inside the `spark` container via `docker compose exec`,
so they share the same Postgres credentials and SQLite mount that
`local_ingest.py` uses. No host-level Python venv needed.

## Test 1 — Insert-only delta

Goal: prove the watermark window is honored — a second run pulls only the
new rows, not all 3K again.

```bash
# 1. Baseline
./local/scripts/inspect-state.sh

# 2. Inject 100 brand-new payments (default --table fact_pay_payment)
./local/scripts/add-incremental-data.sh --inserts 100 --label test1

# 3. Run ingest and check delta
./local/scripts/run-and-verify.sh --expect-delta 100
```

Expected output from step 3:

```
=== ingest delta ===
  Postgres rows delta : +100
  Iceberg  rows delta : +100
  Watermark           : 2026-06-02T21:13:42.000Z → 2026-06-03T05:40:11.000Z
  New run_id          : local::FACT_PAY_PAYMENT::20260603T054011Z
  New snapshot_id     : 8472...
  Snapshot row_count  : 100
  last_run_status     : COMPLETED
  last_run_rows       : 100
  ✓ expected iceberg delta +100 matches actual +100
```

What it tells you:

- The Iceberg delta matches the Postgres delta (no duplicates, no drops)
- `Snapshot row_count: 100` confirms only 100 rows traveled — not 3,100
- `Watermark` advanced past the previous high-water mark
- A new run_id was assigned

If you see `Snapshot row_count: 3100`, the watermark was ignored — that's
a bug in extract or in how `compute_window()` reads `current_watermark`.

### The same test against the other facts

All four fact tables share `last_updated_time` as their watermark, so the
same three commands work for balances, transactions, and FX rates — just
point `inspect-state.sh`, `add-incremental-data.sh`, and `run-and-verify.sh`
at the right `--table`:

```bash
# fact_as_balance
./local/scripts/inspect-state.sh --table FACT_AS_BALANCE
./local/scripts/add-incremental-data.sh --table fact_as_balance --inserts 100
./local/scripts/run-and-verify.sh --table FACT_AS_BALANCE --expect-delta 100

# fact_as_transaction
./local/scripts/add-incremental-data.sh --table fact_as_transaction --inserts 100
./local/scripts/run-and-verify.sh --table FACT_AS_TRANSACTION --expect-delta 100

# fact_as_currency_exchange
./local/scripts/add-incremental-data.sh --table fact_as_currency_exchange --inserts 90
./local/scripts/run-and-verify.sh --table FACT_AS_CURRENCY_EXCHANGE --expect-delta 90
```

## Test 2 — Updates re-flow

Goal: prove that when a row's `last_updated_time` advances, strata picks it
up on the next run.

```bash
# 1. Baseline
./local/scripts/inspect-state.sh

# 2. Flip 50 payments' bank_status_id and bump last_updated_time
./local/scripts/add-incremental-data.sh --updates 50 --label test2

# 3. Run ingest
./local/scripts/run-and-verify.sh --expect-delta 50
```

Expected: Iceberg row count increases by 50. **This is intentional** —
strata's silver layer is append-only. The original row stays, and an
updated copy lands with a later `_ingest_timestamp`. Downstream queries
against silver should always be deduped by `id` with a row-number window
function:

```sql
SELECT * FROM (
    SELECT *, ROW_NUMBER() OVER (
      PARTITION BY id ORDER BY _ingest_timestamp DESC
    ) AS rn
    FROM iceberg.silver_payments.fact_pay_payment
) WHERE rn = 1
```

The update flavour varies per table — see the per-fact "what changes" notes
in [`local/scripts/add_incremental_data.py`](../local/scripts/add_incremental_data.py):

| Table | What `--updates N` touches |
|---|---|
| `fact_pay_payment` | flips `bank_status_id`, bumps `last_updated_time` |
| `fact_as_balance` | nudges `balance_amount` +0.1%, bumps `last_updated_time` |
| `fact_as_transaction` | flips `debit_credit_mark` (DBIT↔CRDT), bumps `last_updated_time` |
| `fact_as_currency_exchange` | nudges `exchange_rate` by ±1%, bumps `last_updated_time` |

If you want a deduped gold view, that lives in a separate gold-layer build,
not in strata.

## Test 3 — Idempotency on retry

Goal: prove that re-running the *same logical work* doesn't double-write.
This is the linchpin of strata's at-least-once delivery guarantee.

Strata's idempotency check in `writer.py` (~line 158) reads:

```python
if summary.get("glue.run_id") == run_id:
    return WriteResult(rows_written=0, was_idempotent_skip=True)
```

So if Iceberg already has a snapshot with this run's `run_id`, the write
is skipped. To exercise that path we need to force the same `run_id` twice
— and the current `local_ingest.py` generates one fresh per run.

**Two ways to force a fixed run_id**:

**Option A — patch the orchestrator to accept `--run-id`**. Add an arg
to `parse_args()` in `local_ingest.py`:

```python
p.add_argument("--run-id", default=None,
               help="Override generated run_id (for idempotency testing)")
```

And use it where the run_id is computed:

```python
run_id = args.run_id or f"local::{table}::{datetime.now(timezone.utc).strftime(...)}"
```

Then:

```bash
./local/scripts/add-incremental-data.sh --inserts 100 --label test3
RUN_ID="test3-$(date +%s)"
docker compose -f local/docker-compose.yml exec -T spark \
    python -m strata.local_ingest --table FACT_PAY_PAYMENT --run-id "$RUN_ID"
# Expect: 100 rows written

docker compose -f local/docker-compose.yml exec -T spark \
    python -m strata.local_ingest --table FACT_PAY_PAYMENT --run-id "$RUN_ID"
# Expect: "idempotent skip" path. rows_written=0, watermark still advances.
```

Confirm with the inspector — `Iceberg rows delta : +0` and the existing
snapshot's `run_id` matches `$RUN_ID`.

**Option B — directly invoke the writer with a hard-coded run_id in a
small pytest**. See `tests/unit/test_writer.py` for the harness; the
idempotency test should call `write_iceberg()` twice with identical args
and assert the second returns `was_idempotent_skip=True`.

Either way, the assertion is: **same run_id → exactly one logical commit**.

## Test 4 — Failure recovery

Goal: prove that a crash mid-run doesn't lose data and doesn't double-write.

There are five recovery cases enumerated in `recovery.py`. The two
interesting ones for local testing:

**Case B — stale DynamoDB / no Iceberg snapshot.**  
The job crashed *before* the Iceberg commit. State DB holds a `pending_run_id`,
Iceberg has nothing for that run. Recovery: lock is released (or expires),
next run re-extracts from the same watermark, lands the rows under a new
run_id.

**Case C — orphan Iceberg snapshot / state DB didn't advance.**  
The job committed to Iceberg but crashed *before* `state_mgr.complete()`.
Iceberg has the snapshot, state DB still shows the old watermark. Recovery:
`reconcile_state()` scans the latest snapshot, copies its
`glue.watermark_upper` to the state DB's `current_watermark`, marks the run
as `RECONCILED_FROM_ICEBERG`.

To exercise Case B (stale state, no snapshot):

```bash
# 1. Inject data and start ingest, kill mid-run
./local/scripts/add-incremental-data.sh --inserts 200 --label test4b
./local/scripts/ingest.sh FACT_PAY_PAYMENT &
INGEST_PID=$!
sleep 5
kill -9 $INGEST_PID 2>/dev/null

# 2. Confirm the state DB has a pending_run_id but Iceberg is unchanged
./local/scripts/inspect-state.sh
# Look for "Pending run_id is set — a previous run did not finish cleanly"

# 3. Re-run. Reconciliation should detect Case B and let the run proceed.
./local/scripts/run-and-verify.sh --expect-delta 200
```

To exercise Case C (orphan snapshot) you need to interrupt *after* the
Iceberg write but *before* `state_mgr.complete()`. The easiest way is a
temporary `time.sleep(15)` patched into `local_ingest.py` between the
writer call and the `state_mgr.complete()` call — kill the process during
that sleep. Then run the inspector; you'll see:

```
! Iceberg latest run_id (X) != SQLite last_run_id (Y) —
  reconciliation will fire on next run.
```

Run ingest again and the inspector should show `RECONCILED_FROM_ICEBERG`
as `last_run_status` and the watermark advanced to match Iceberg.

## Mixed loads + the dashboard

For an end-to-end soak test that also visually validates the dashboard:

```bash
for i in 1 2 3 4 5; do
  ./local/scripts/add-incremental-data.sh --mixed 200 --label "soak-$i"
  ./local/scripts/run-and-verify.sh --expect-delta 200
  sleep 2
done
```

Then refresh the **Payments Operations** dashboard at
http://localhost:8088/superset/dashboard/1/. Total payment volume should
have grown by ~1000 rows × avg amount; the Country Map and Sankey should
still render cleanly; the daily payment volume bars will have new
buckets.

## Troubleshooting

**`inspect-state.sh` shows "no row" in SQLite state but Iceberg has data.**
That means a previous run crashed before the state write — Iceberg is
ahead of the state DB. The next ingest's `reconcile_state()` will fix it.
Or run `python -m strata.recovery --table FACT_PAY_PAYMENT` (if exposed)
to force reconciliation.

**`run-and-verify.sh` shows `Iceberg rows delta: +0` after inserting data.**
Three likely causes, in order of probability:

1. The data was inserted with `last_updated_time` ≤ the current watermark.
   Re-check the inserter — it should be using `NOW()` UTC.
2. A previous run still holds the lock (`pending_run_id` set, not expired).
   `inspect-state.sh` will show this. Wait for `STRATA_LOCK_TTL_SECONDS`
   (default 2 hours) or manually clear via SQL.
3. The watermark column on the source table doesn't have a value for the
   new rows. Confirm `data_mart.fact_pay_payment.last_updated_time` is NOT
   NULL on every row.

**`run-and-verify.sh` shows a much larger delta than expected.**
Almost always: the state DB was wiped, so strata is doing a full extract
(`lower = NULL`). Check `inspect-state.sh` — if `current_watermark` is
`null`, the next run will pull everything.

**Want to start over?** Wipe both sides cleanly:

```bash
# nukes Postgres + warehouse
docker compose -f local/docker-compose.yml down -v
rm -rf local/data/state local/data/warehouse
./local/scripts/setup.sh
./local/scripts/run-all.sh
```

## What the scripts deliberately don't test

- **Concurrent runs from two workers.** The lock model is correct (see
  `state.py::acquire`) but exercising it requires two processes racing —
  out of scope here. Add a `--simulate-concurrent` flag if you need it.
- **Schema drift.** Use `--table` against a dimension (e.g.
  `FACT_PAY_PAYMENT` or `DIM_ACCOUNT`) and `ALTER` the source schema
  between runs. The `SchemaDriftError` path is unit-tested in
  `tests/unit/test_writer.py`.
- **Recovery Case E (lock expiry).** Set `STRATA_LOCK_TTL_SECONDS=5`,
  kill an ingest mid-run, wait 6 seconds, re-run. The lock should be
  considered expired and reacquirable.

For those, see `docs/reliability.md`'s failure matrix.
