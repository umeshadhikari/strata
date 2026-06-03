---
mode: agent
description: Plan and execute a backfill of one or more strata tables.
---

Help the user plan and run a backfill of strata tables.

Collect from the user:

1. **Which tables** to backfill — a specific list, all dims, all facts, or `all`.
2. **Backfill scope** — fresh deployment (no data yet), full-refresh of an
   existing table (the table-level Iceberg state will be overwritten), or
   partial backfill of specific dates.
3. **Customer / environment** to target. The Glue job name is conventionally
   `<customer-id>-strata-ingest`.

Then produce the right commands for the scope:

### Scope: `all`

Recommend the orchestrated script — it runs dims first then facts, paces
itself against Glue's concurrent-run limit, and logs each job ID:

```bash
./scripts/backfill_all.sh <customer-id>
```

If the user prefers manual control, list dims then facts in two
phases and produce one `aws glue start-job-run` per table for each
phase. Don't kick off facts until dims complete — Iceberg has no FK
enforcement, but downstream BI queries will look wrong.

### Scope: specific tables, full refresh

Produce one command per table:

```bash
aws glue start-job-run \
  --job-name <customer>-strata-ingest \
  --arguments '{"--TABLE_NAME":"<TABLE>","--FULL_REFRESH":"true"}'
```

`--FULL_REFRESH=true` ignores the DynamoDB watermark and overwrites the
target Iceberg table. The next regular incremental run will resume from
the new watermark naturally.

### Scope: partial date range

This is the trickiest case. Full refresh isn't right (you want to keep
data outside the range), and the incremental path won't re-pull rows
whose `last_updated_time` is already below the current watermark.

The procedure is documented in `docs/operational-runbook.md` under
"reprocess yesterday's data" — summarise it:

1. **Reset the DynamoDB watermark** to the start of the desired range:
   ```bash
   aws dynamodb update-item \
     --table-name <customer>-strata-state \
     --key '{"table_name":{"S":"<TABLE>"}}' \
     --update-expression "SET current_watermark = :wm" \
     --expression-attribute-values '{":wm":{"S":"YYYY-MM-DDTHH:MM:SSZ"}}'
   ```
2. **Run the job incrementally** (not `--FULL_REFRESH`) — it will re-extract
   everything from the new lower bound up to `now()`.
3. **The Iceberg writer's idempotency check** prevents double-commit of
   rows that were already there: same `(payment_id, _ingest_run_id)`
   combination won't appear twice in any snapshot. But payment_ids that
   fall in the date range *will* appear twice in the silver layer (the
   original row + the re-ingested row), distinguished by
   `_ingest_timestamp`. See `docs/testing-incremental.md` Test 2 for
   how downstream queries should handle this.

### After producing the commands, remind the user

- **Glue's default concurrent-run limit is ~10.** Don't kick off 50
  tables at once. The `backfill_all.sh` script paces this automatically.
- **Dims must complete before facts.** Iceberg doesn't enforce this;
  semantic correctness depends on it.
- **Monitor CloudWatch metrics during the backfill.** Namespace
  `StrataIngest`, dimension `Customer = <customer-id>`:
  - `RowsWritten` per table — establishes the baseline volume.
  - `Failures` — should be 0; if non-zero, stop and run `/debug`.
  - `DurationSeconds` per table — useful for sizing future capacity.
- **Backfills are the right time to validate schema changes.** If a
  source DDL changed since the last full refresh, you'll see the drift
  here rather than mid-week.

If the user mentions wanting to "reprocess yesterday" or similar
date-bounded language, gently push them toward Scope 3 (partial date
range) rather than full refresh, which is wasteful.
