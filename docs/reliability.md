# Reliability and Failure Recovery

This pipeline is designed for at-least-once delivery with idempotent commits.
After any failure, the next run resumes correctly with no data loss and no
duplication. This doc explains how.

## Core principle

**The Iceberg snapshot is the source of truth.** DynamoDB is a cache of
"latest known committed watermark." Every run starts by reconciling them.
If they disagree, Iceberg wins.

Every Iceberg commit carries these properties in its snapshot:

| Property | Purpose |
|---|---|
| `glue.run_id` | Unique per attempted run. Same on retries (inherits Glue's JOB_RUN_ID). |
| `glue.watermark_lower` | Exclusive lower bound of the source query window |
| `glue.watermark_upper` | Inclusive upper bound. Becomes the new current_watermark. |
| `glue.row_count` | Rows in this commit |
| `glue.committed_at` | ISO timestamp |

These properties are what makes recovery deterministic.

## Run lifecycle

```
START
  │
  ▼
[1] Reconcile DynamoDB ↔ Iceberg (snapshot-based recovery, below)
  │
  ▼
[2] Compute extract window:
    lower = current_watermark
    upper = now()
  │
  ▼
[3] Acquire DynamoDB lock (conditional update; fails if another run is live)
  │
  ▼
[4] JDBC extract from data mart with retries on transient errors
  │
  ▼
[5] Iceberg write with run_id in snapshot properties
    - First check: snapshot with this run_id already exists? → skip (idempotent)
    - Schema evolution validated; drift fails fast
    - Atomic Iceberg commit
  │
  ▼
[6] Update DynamoDB:
    current_watermark = MAX(watermark_column from extracted rows)
    last_run_status = COMPLETED
    pending_* cleared
  │
  ▼
END
```

If anything fails between steps, the next run picks up cleanly.

## Watermark window semantics

Every run computes a bounded window:

    window = (lower, upper]
           = (previous_committed_watermark, now()]

`upper` is captured **once** at the run start. Same value on every retry.
This means:

* Two concurrent attempts of the same run produce identical query results.
* A row inserted into the data mart at `upper + 1ms` is not included in this
  window; it'll be picked up by the next run with `lower = upper`.
* The query is `WHERE watermark_column > :lower AND watermark_column <= :upper`,
  so each row is included in exactly one window (no gaps, no overlaps).

## Recovery scenarios

The first thing every run does is `reconcile_state()`. It handles five cases:

### A. Clean state, all consistent
DynamoDB shows no pending lock. Latest Iceberg snapshot's `glue.watermark_upper`
matches DynamoDB's `current_watermark`. Nothing to do. Proceed to extract.

### B. DynamoDB cache is stale
DynamoDB shows watermark = X, but Iceberg has a more recent snapshot with
watermark = Y > X. This means a previous run committed to Iceberg but its
DynamoDB update was lost (e.g., AWS API blip, job killed mid-commit).

**Action:** Fast-forward DynamoDB watermark to Y, marking it
`last_run_status = RECONCILED_FROM_ICEBERG`. No data was lost; the cache just
caught up. Proceed normally.

### C. Pending lock + matching Iceberg snapshot exists
DynamoDB shows pending lock from run R. An Iceberg snapshot tagged with R's
run_id exists. This means R wrote data successfully, then died before updating
DynamoDB.

**Action:** Auto-complete the run: set DynamoDB watermark to the snapshot's
`glue.watermark_upper`, mark run COMPLETED, release lock. Then this new run
starts fresh.

### D. Pending lock + no matching snapshot + lock not expired
Another run is currently in progress. The lock is valid.

**Action:** Exit cleanly with `ConcurrentRunSkips` metric incremented. The
next scheduled run will try again. (This is normal in busy environments where
two backfills overlap.)

### E. Pending lock + no matching snapshot + lock expired (TTL passed)
A previous run crashed before writing any Iceberg snapshot, and its lock TTL
has expired (default 2 hours).

**Action:** Reap the stale lock by marking it FAILED. The current run can
then acquire its own lock.

## Idempotency guarantees

**Iceberg write is idempotent on run_id.** Before writing, the code scans the
recent snapshot history for any snapshot whose `glue.run_id` matches this run's
run_id. If found, the write is skipped — the previous attempt already wrote
this data. Metric: `IdempotentSkips`.

This means a retry that completes Iceberg write but doesn't manage to update
DynamoDB will, on the next attempt:

1. Find the existing snapshot in step [5] → skip the actual write
2. Read the snapshot's `glue.watermark_upper` and `glue.row_count` from the summary
3. Advance DynamoDB watermark and release the lock

No duplicate data; no data loss.

## Glue auto-retry behaviour

The Glue job is configured with `max_retries = 1`. AWS retries failed runs
**preserving the same JOB_RUN_ID**. Our run_id is derived from JOB_RUN_ID:

    run_id = f"{JOB_NAME}::{TABLE_NAME}::{JOB_RUN_ID}"

so the retry uses the same run_id and:
* If the first attempt succeeded on Iceberg but failed on DynamoDB update, the
  retry detects the snapshot and completes the state update.
* If the first attempt failed before any Iceberg commit, the retry re-extracts
  the same bounded window (lower, upper] and writes fresh.

## Failure mode reference

| What goes wrong | Detection | Behaviour | Data loss? | Duplication? |
|---|---|---|---|---|
| Data mart unreachable | JDBC connection error → `SourceUnreachableError` | Retries 3× with exponential backoff; if still failing, marks FAILED and releases lock | No — next run retries from same watermark | No |
| Data mart query times out | `SourceQueryError` | Same as above | No | No |
| Bad credentials in Secrets Manager | `ConfigError` (auth detected) | Fails fast; alarm fires | No | No |
| Source schema changed (new column) | Detected pre-write; Iceberg auto-evolves | Logs `Schema evolution: new column X` | No | No |
| Source schema changed (column renamed / type changed) | `SchemaDriftError` | Fails fast with explicit message; alarm `SchemaDriftAlerts` | No — operator must intervene | No |
| Glue worker dies during extract | Spark task retries within the job; if exhausted, run fails | State released for next retry | No | No |
| Glue worker dies between extract and write | Job retried by AWS with same JOB_RUN_ID | Same window re-extracted; write proceeds | No | No |
| Glue worker dies between write and DynamoDB update | Snapshot exists in Iceberg, DynamoDB still has pending lock | Next run's reconciliation detects Case C, force-completes state | No | No |
| Two job runs start simultaneously | Conditional DynamoDB update fails for the second | Second run exits cleanly with `ConcurrentRunSkips` metric | No | No |
| Iceberg commit fails (concurrent writer) | `WriteCommitError` | Retried with backoff; if exhausted, marks FAILED | No | No |
| DynamoDB unavailable mid-run | Boto3 retries (10×, adaptive) | If still failing after retries, marks FAILED with logged error | No | No |
| Stale lock (job killed without releasing) | `pending_expires_at <= now` detected on next run | Lock reaped, new run proceeds | No | No |
| Late-arriving data in source (row with `LAST_UPDATED_TIME` < current_watermark) | Not handled by this pipeline directly | Row is missed by watermark-based extraction | Yes (rare) | No |

The last row is the only unhandled case: **late-arriving data**. If the data
mart sometimes writes rows with backdated `LAST_UPDATED_TIME` (which would be
unusual — normally that column reflects when the row was modified, not its
business date), they'd be skipped. Two mitigations if this is a concern:

1. **Lag the upper bound.** Set `upper = now() - 5 minutes` so the window
   trails real time. Rows that get backdated by a few seconds are still
   captured.
2. **Periodic full refresh.** Run `--FULL_REFRESH=true` weekly or monthly to
   catch any drift. Iceberg's overwritePartitions makes this safe and atomic.

The first is configured via a job parameter; the second is just a separate
scheduled trigger.

## Operational checks

### Per-table monitoring (CloudWatch metrics in namespace `StrataIngest`)

| Metric | Alarm condition | What it means |
|---|---|---|
| `RowsWritten` | Sum over 24h == 0 for a fact table | Source has gone silent or extract is broken |
| `DurationSeconds` | p95 > expected | Performance regression |
| `Failures` | Sum > 0 over 1h | A run failed and didn't recover |
| `SchemaDriftAlerts` | Sum > 0 ever | Schema drift detected; manual intervention needed |
| `StateInconsistencyAlerts` | Sum > 0 ever | DynamoDB / Iceberg cannot be auto-reconciled; on-call investigates |
| `ConcurrentRunSkips` | Sum repeatedly | Scheduling overlap (probably benign, but check) |
| `IdempotentSkips` | Sum > 0 occasionally | Glue auto-retry caught a successful prior commit; expected |

### Daily sanity check

```sql
-- Every table should have advanced its watermark in the last 24 hours
SELECT table_name, current_watermark, last_run_completed_at
FROM dynamodb.trax_silver_watermarks
WHERE last_run_completed_at < NOW() - INTERVAL '24 hours';

-- Counts in Iceberg should match source row counts within tolerance
SELECT COUNT(*) FROM silver_payments.fact_payment WHERE _ingest_date = CURRENT_DATE;
-- compare against the data mart's row count for the same day
```

### Recovery runbook

**"Job has been failing for 6 hours"** —
1. Check CloudWatch Logs for the most recent failure cause.
2. If `SchemaDriftError` — operator runs `ALTER TABLE` to reconcile, then triggers a retry.
3. If `SourceUnreachableError` — check the data mart and the Glue VPC connection.
4. If `StateConsistencyError` — read the DynamoDB row and the Iceberg snapshot
   history; manual reconcile may be needed (rare).
5. After fixing the root cause: trigger a manual job run. Recovery kicks in
   automatically and the next run resumes from the right watermark.

**"Need to reprocess yesterday's data"** —
1. Stop the scheduled trigger.
2. Read DynamoDB watermark for the table; record its current value V.
3. Update the watermark to V - 24h: `aws dynamodb update-item ...`
4. Trigger a manual job run.
5. The job will re-extract data with `LAST_UPDATED_TIME > V-24h`.
6. Iceberg `MERGE` mode (if configured) dedups by primary key; otherwise
   accept duplicate rows or run a one-time dedup query in Silver.
7. Re-enable the schedule.

**"Need to drop and rebuild from scratch"** —
1. Stop the scheduled trigger.
2. `DROP TABLE silver_payments.fact_payment` in Athena.
3. `DELETE` the DynamoDB row for the table.
4. Trigger a manual run with `--FULL_REFRESH=true`.
5. Full reload completes; resume schedule.

## What this design does NOT protect against

For honest expectations:

* **The data mart being wrong.** If the source has duplicate or bad data,
  this pipeline faithfully copies it. Data quality is a separate concern.
* **Logical bugs in business logic.** This pipeline does no transformations;
  it copies tables as-is. Bugs in your derived Gold tables (later pipeline)
  are not protected by this code.
* **AWS regional outage.** Cross-region replication (S3 CRR, DynamoDB Global
  Tables) is enabled in the Terraform, but recovery to the DR region requires
  manual failover documented separately in the DR runbook.
* **Schema drift you actively want to handle silently.** The default is to
  fail loudly on column rename / type change. If your data mart routinely
  changes schemas, accept the alarm and write an automated `ALTER TABLE`
  responder. Don't silently ignore drift — that hides real bugs.

## Summary

After any failure, the next run:
1. Reconciles DynamoDB with Iceberg snapshot history (Iceberg wins).
2. Either resumes from the last committed watermark, or detects a
   never-committed previous attempt and reprocesses its window.
3. Writes with the same run_id, so previous successful writes are detected
   and skipped (no duplicates).
4. Advances the watermark only after a confirmed Iceberg commit.

This is **at-least-once with idempotent commits**: a row from the source
appears in Iceberg exactly once, eventually, regardless of how many transient
failures occur along the way.
