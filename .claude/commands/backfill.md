---
description: Plan and execute a backfill of one or more tables.
---

Help the user plan and run a backfill of strata tables.

Ask the user for:

1. Which tables to backfill (or "all").
2. Whether this is a fresh deployment (no data yet) or a partial backfill of specific dates.
3. The customer/environment to target.

Then:

1. If "all" — recommend running `./scripts/backfill_all.sh <customer-id>` which orchestrates dims first then facts.
2. If specific tables — produce one `aws glue start-job-run` command per table with `--FULL_REFRESH=true`.
3. If partial date range — explain that this requires resetting the DynamoDB watermark first (see `docs/operational-runbook.md` "reprocess yesterday's data"), then produce the exact AWS CLI commands.

After producing commands, remind the user:
- Glue has a default concurrent run limit (~10). Pace large backfills.
- Dims must complete before facts that reference them (semantically — Iceberg has no FK enforcement, but BI queries will look wrong).
- Watch CloudWatch metrics during the backfill: `RowsWritten`, `Failures`, `DurationSeconds`.
