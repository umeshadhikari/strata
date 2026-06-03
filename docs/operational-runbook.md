# Operational runbook

For on-call engineers maintaining a strata deployment.

## Monitoring

### CloudWatch metrics namespace: `StrataIngest`

| Metric | Watch for | Action |
|---|---|---|
| `RowsWritten` (sum over 24h == 0 for a fact) | Source went silent or extract is broken | Check the source; check Glue Job runs |
| `DurationSeconds` (p95 > expected) | Performance regression | Check source query plan; check Glue worker scale |
| `Failures` (sum > 0 over 1h) | Run failed after all retries | See "job failures" below |
| `SchemaDriftAlerts` (sum > 0) | Source schema changed in a breaking way | See "schema drift" below |
| `StateInconsistencyAlerts` (sum > 0) | Auto-reconciliation could not fix DynamoDB/Iceberg | Manual investigation required |
| `ConcurrentRunSkips` (recurring) | Two runs overlapping consistently | Probably benign; check scheduling |
| `IdempotentSkips` (occasional) | Glue auto-retry caught a prior successful commit | Expected — no action |

### Daily sanity check

```sql
-- Every active table should have advanced its watermark in the last 24h.
-- Replace the placeholders with your environment values.
SELECT table_name, current_watermark, last_run_completed_at, last_run_status
FROM "<dynamodb-table>"
WHERE last_run_completed_at < NOW() - INTERVAL '24 hours'
  AND last_run_status != 'RECONCILED_FROM_ICEBERG';
```

```sql
-- Row counts from yesterday should be non-zero for fact tables.
SELECT COUNT(*) FROM silver_payments.fact_payment
WHERE _ingest_date = CURRENT_DATE - INTERVAL '1' DAY;
```

## Common scenarios

### Scenario: "A job has been failing for 6 hours"

1. Open CloudWatch Logs for the affected Glue job, sort by recent.
2. Search for `EVENT=` lines — they show structured event names.
3. Identify the failure category from the last `EVENT=` before the stack trace:

   | Event | Meaning | Action |
   |---|---|---|
   | `EVENT=source_unreachable` | Network or DB connection failed | Check the data mart; check VPC; check security groups |
   | `EVENT=config_error` | Bad credentials or malformed config | Update the Secrets Manager secret or `tables.yaml` |
   | `EVENT=schema_drift` | Source schema broke compatibility | Run `ALTER TABLE` (see below) |
   | `EVENT=state_inconsistent` | Iceberg/DynamoDB cannot reconcile | Manual investigation (see below) |
   | `EVENT=permanent_failure` | Unrecoverable error | Read the trace; fix the cause; rerun |
   | `EVENT=transient_failure_exhausted` | Retries ran out | Often resolves on next scheduled run |

4. Once the root cause is fixed, trigger a manual run:
   ```bash
   aws glue start-job-run --job-name <job-name> \
     --arguments '{"--TABLE_NAME":"<TABLE>","--FULL_REFRESH":"false"}'
   ```
5. Recovery kicks in automatically. The next run will resume from the correct
   watermark and write only new data.

### Scenario: "Schema drift detected"

The Glue job fails with `SchemaDriftError: Schema drift in <table>: <col>: ...`.

1. Identify the change: did a column get renamed, dropped, or have its type changed?
2. Decide the action:
   - **Type widening** (e.g., int → bigint) — alter Iceberg to match:
     ```sql
     ALTER TABLE silver_<domain>.<table>
     ALTER COLUMN <col> TYPE BIGINT;
     ```
   - **New column on source, dropped from existing target** — add the column to Iceberg:
     ```sql
     ALTER TABLE silver_<domain>.<table>
     ADD COLUMN <col> <type>;
     ```
   - **Column renamed** — rename in Iceberg:
     ```sql
     ALTER TABLE silver_<domain>.<table>
     RENAME COLUMN <old> TO <new>;
     ```
3. Trigger a manual run to verify.

### Scenario: "Need to reprocess yesterday's data"

This is the safest pattern:

1. **Don't stop the schedule.** Reprocessing is a separate manual action.
2. Read the current DynamoDB watermark and note its value V.
3. Reset the watermark to V minus 24 hours:
   ```bash
   aws dynamodb update-item \
     --table-name <watermarks-table> \
     --key '{"table_name":{"S":"<TABLE>"}}' \
     --update-expression "SET current_watermark = :wm" \
     --expression-attribute-values '{":wm":{"S":"2026-05-31T00:00:00+00:00"}}'
   ```
4. Trigger a manual run.
5. If the table is in `merge` mode, existing rows are updated in place.
   If in `append` mode, you'll get duplicate rows for the overlap window.
   You can either accept that (analytical queries should be group-aware) or
   run a one-time dedup pass on Silver.
6. Watermark advances to the new max; subsequent scheduled runs continue
   normally.

### Scenario: "Need to drop and rebuild from scratch"

1. Stop the scheduled trigger:
   ```bash
   aws events disable-rule --name <schedule-rule>
   ```
2. Drop the Iceberg table:
   ```sql
   DROP TABLE silver_<domain>.<table>;
   ```
3. Delete the DynamoDB watermark row:
   ```bash
   aws dynamodb delete-item \
     --table-name <watermarks-table> \
     --key '{"table_name":{"S":"<TABLE>"}}'
   ```
4. Trigger a full refresh:
   ```bash
   aws glue start-job-run --job-name <job-name> \
     --arguments '{"--TABLE_NAME":"<TABLE>","--FULL_REFRESH":"true"}'
   ```
5. Once it completes, re-enable the schedule:
   ```bash
   aws events enable-rule --name <schedule-rule>
   ```

### Scenario: "State inconsistency alarm"

Cause: DynamoDB shows a pending lock from run R, but the recovery logic
couldn't find a matching Iceberg snapshot AND the lock has not expired.

This means either:
(a) Run R is genuinely still in progress and you're seeing a false alarm.
(b) The lock TTL is too long for your environment.
(c) Something stranger — operator review needed.

Investigation:

1. Check if any Glue Job run with that JOB_RUN_ID is currently running:
   ```bash
   aws glue get-job-run --job-name <job-name> --run-id <run-id>
   ```
2. If `JobRunState = RUNNING`, it's case (a). Wait or kill it manually.
3. If `JobRunState = FAILED`, it's case (b)/(c). The lock is stale.

Manual lock release:

```bash
aws dynamodb update-item \
  --table-name <watermarks-table> \
  --key '{"table_name":{"S":"<TABLE>"}}' \
  --update-expression "REMOVE pending_run_id, pending_window_lower, pending_window_upper, pending_started_at, pending_expires_at SET last_run_status = :failed, last_run_error = :err" \
  --expression-attribute-values '{":failed":{"S":"FAILED_MANUAL_RELEASE"},":err":{"S":"Operator released stale lock"}}'
```

Then trigger a new run. Recovery will read latest Iceberg snapshot's watermark
and proceed from there.

## Lock TTL

Default lock TTL is **2 hours**. Set via the `TRAX_LOCK_TTL_SECONDS` environment
variable on the Glue job (or update the source if you want a different default).

Pick a TTL slightly longer than your worst-case ingest time. Too short → stale
locks during legitimately slow runs. Too long → faster recovery from crashes.

## Capacity tuning

| Symptom | Cause | Fix |
|---|---|---|
| Glue worker OOM | Too few workers for table size | Increase `number_of_workers` in Terraform |
| JDBC read slow | Single-threaded extract | Add or increase `parallel_extract` config |
| Iceberg compaction slow | Too many small files | Lower bucket count or increase compaction frequency |
| Athena query timeouts | Files not compacting | Run `system.rewrite_data_files` manually |

## On-call playbook summary

| Alert | Severity | Response time |
|---|---|---|
| `Failures` | P2 | Investigate within 1h |
| `SchemaDriftAlerts` | P2 | Investigate within 1h; operator action required |
| `StateInconsistencyAlerts` | P1 | Page on-call immediately |
| `ConcurrentRunSkips` (one-off) | P3 | Note, no action |
| `RowsWritten = 0` for a fact (24h) | P2 | Verify source data is flowing |
