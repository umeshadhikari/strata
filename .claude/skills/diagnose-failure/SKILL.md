---
name: diagnose-failure
description: Diagnose a failed strata Glue job and recommend recovery action. Walks through CloudWatch logs to find the EVENT marker, classifies the exception, and produces a concrete recovery plan. Use when a job fails or an alarm fires. Triggers include: "job failed", "ingest broken", "investigate failure", "Glue run failed", "SchemaDriftAlerts", "Failures alarm".
---

# Skill: diagnose-failure

## When to use this skill

A strata Glue job has failed and you need to figure out why and what to do about it. Common triggers:

- A `Failures` or `SchemaDriftAlerts` or `StateInconsistencyAlerts` CloudWatch alarm.
- An operator reports "the ingest is broken."
- A scheduled run shows up as FAILED in Glue Job Runs.

## Diagnostic procedure

### Step 1: Get the right log lines

For a known JOB_RUN_ID:

```bash
aws glue get-job-run --job-name <job-name> --run-id <run-id>
```

This returns `LogGroupName` and a timestamp. Then in CloudWatch Logs Insights:

```
fields @timestamp, @message
| filter @logStream like /<job-run-id>/
| sort @timestamp desc
| limit 200
```

For an unknown failure (just the alarm), find the most recent FAILED run:

```bash
aws glue get-job-runs --job-name <job-name> --max-results 10 \
  | jq '.JobRuns[] | select(.JobRunState=="FAILED") | {Id, StartedOn, ErrorMessage}'
```

### Step 2: Find the EVENT marker

strata logs structured events with the pattern `EVENT=<name>`. Search for the last one before the stack trace. The event tells you the category:

| EVENT | Category | First action |
|---|---|---|
| `EVENT=source_unreachable` | Source connectivity | Check VPC route + DB health |
| `EVENT=config_error` | Misconfiguration | Check secret + tables.yaml |
| `EVENT=schema_drift` | Source schema change | Run schema-drift skill |
| `EVENT=state_inconsistent` | DynamoDB ↔ Iceberg mismatch | Manual investigation (P1) |
| `EVENT=concurrent_run_detected` | Lock held by another run | Wait or investigate scheduling |
| `EVENT=permanent_failure` | Various non-retriable | Read trace |
| `EVENT=transient_failure_exhausted` | Retries exhausted | Often resolves next run |
| `EVENT=unexpected_failure` | Bug | File issue + investigate |
| `EVENT=nothing_to_do` | Empty window | Not a failure — informational |

### Step 3: Identify the exception class

Look for the Python exception type in the stack trace. Cross-reference with `src/strata/exceptions.py`:

| Exception | Why it fires | Recovery |
|---|---|---|
| `ConfigError` | Bad credentials, bad YAML, missing table | Fix config; rerun |
| `SchemaDriftError` | Source schema diverged | Operator ALTER TABLE; then rerun |
| `ConcurrentRunError` | Lock held | Wait, or investigate why two runs are racing |
| `SourceUnreachableError` | Network / auth / DB down | Already retried; verify infra |
| `SourceQueryError` | JDBC query died mid-flight | Already retried; check source DB load/locks |
| `WriteCommitError` | Iceberg commit conflict | Already retried; check for concurrent writers |
| `StateConsistencyError` | Recovery couldn't auto-reconcile | Manual reconcile — see runbook |

### Step 4: Cross-reference with the runbook

`docs/operational-runbook.md` has procedures for each category. For uncommon cases, find the closest match.

### Step 5: Recommend the recovery action

Produce a concise diagnosis:

```
DIAGNOSIS: <one sentence>

ROOT CAUSE: <why it failed, 1–2 sentences>

ACTION:
1. <exact step>
2. <exact step>
3. <exact step>

WILL IT RECOVER AUTOMATICALLY: <yes / no / with operator action>
```

If the failure is something the next scheduled run will resolve, say so explicitly. If it requires operator action (schema drift, state inconsistency), produce the exact commands.

## Special cases

### "Logs say success but data not in Athena"

Check Iceberg snapshot history:

```sql
SELECT snapshot_id, committed_at, summary
FROM silver_<domain>.<table>.snapshots
ORDER BY committed_at DESC
LIMIT 5;
```

If the latest snapshot's `summary.glue.run_id` matches the run that "succeeded," the data IS there — the query problem is elsewhere (Athena cache, wrong partition predicate, etc.).

### "Watermark hasn't advanced for hours but no error"

Check the latest run status in DynamoDB:

```bash
aws dynamodb get-item \
  --table-name <watermarks-table> \
  --key '{"table_name":{"S":"<TABLE>"}}'
```

Look at `last_run_status` and `last_run_error`. If status is `RECONCILED_FROM_ICEBERG`, recovery happened automatically. If `pending_run_id` is populated and `pending_expires_at` is in the past, you have a stale lock — see the runbook for manual release.

### "Two runs racing"

`ConcurrentRunSkips` metric incrementing means the second run exited cleanly because the first held the lock. This is the SYSTEM WORKING. It's a problem only if:
- The first run is hung (check `pending_started_at`).
- Scheduling has overlapping triggers (check EventBridge rules).

### "IdempotentSkips incrementing"

The system worked correctly — a Glue auto-retry found that the previous attempt had committed the snapshot, so it skipped the write. No action needed. This is expected behavior, not a bug.

## What this skill does NOT do

- It does not run automated recovery actions. It tells the operator what to do.
- It does not modify source code. If the diagnosis reveals a bug, file an issue instead.
- It does not touch DynamoDB or Iceberg directly. Operators do that with explicit commands.

## Output format

Always end with the four-block format:

```
DIAGNOSIS: <one sentence>
ROOT CAUSE: <2 sentences max>
ACTION:
  1. <command>
  2. <command>
WILL IT RECOVER AUTOMATICALLY: <yes/no/with-intervention>
```
