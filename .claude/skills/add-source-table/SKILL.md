---
name: add-source-table
description: Comprehensive workflow for adding a new source table to strata. Covers gathering source DDL, choosing a watermark column, deciding partition spec, validating against config.py, editing examples/tables.yaml, generating the backfill command, and producing a PR-ready change. Use when a new table needs to be ingested. Triggers include: "add a table", "new table", "ingest <table_name>", "FACT_X / DIM_X".
---

# Skill: add-source-table

## When to use this skill

A new source table needs to be added to strata's ingestion pipeline. Common triggers:

- User says "add a new table" or names a specific table (`FACT_PAYMENT`, `DIM_ACCOUNT`).
- A data engineer hands over source DDL and asks for it to be ingested.
- A new domain is being added.

## Workflow

### Step 1: Gather inputs

Before doing anything, collect:

1. **Source DDL** — paste of the `CREATE TABLE` statement, or at minimum:
   - Table name
   - Column names with types
   - Primary key
   - The `LAST_UPDATED_TIME` (or equivalent) column for watermarking
2. **Logical domain** — `payments`, `balances`, `shared`, or a new one.
3. **Expected daily row volume** — used to size partitioning and parallel extract.
4. **Common query patterns** — used to set sort order. Optional but valuable.
5. **Whether the table is** append-only / updates-in-place / full-replacement — drives `write_mode`.

If any of (1)–(3) are missing, ask before proceeding.

### Step 2: Validate the source assumptions

Check for common gotchas:

- **No `updated_at` column?** Fall back to a monotonic numeric PK as the watermark. If neither exists, this table can only be full-refreshed daily — confirm with the user.
- **Composite primary key?** Fine; list all columns in `primary_key`.
- **PII columns?** Note them for the user; tokenization is a separate concern (not handled by strata's stripped-down pipeline).
- **Boolean as `NUMBER(1)`?** Common in Oracle; will come through as `long` in Iceberg. Document this if downstream needs it as boolean.
- **JSON/CLOB columns?** They'll come through as strings. Heavy CLOBs increase per-row size; consider whether they're needed in Silver at all.

### Step 3: Choose the partition spec

Use the decision tree from `docs/partitioning.md`:

```
Dimension < 1 GB              → no partitioning
Dimension 1–10 GB             → [years(effective_date)]
Small fact < 100M rows        → [days(business_date)]
Medium fact 100M–1B rows      → [days(business_date), bucket(16, data_owner_id)]
Large fact > 1B rows          → [days(business_date), bucket(32, data_owner_id)]
```

If the table doesn't have an obvious business date, ask the user. Don't invent one.

### Step 4: Choose the write mode

- `append` — fact tables with monotonic watermarks (default).
- `overwrite` — dimensions that get fully replaced, or static reference tables.
- `merge` — only when the source updates rows by primary key AND downstream needs the update reflected. Requires `primary_key`.

### Step 5: Decide on parallel extract

Add `parallel_extract` if and only if:
- Daily row volume > 10 million, AND
- Numeric primary key (UUIDs can't be partitioned by Spark JDBC).

```yaml
parallel_extract:
  column: <numeric_pk>
  lower_bound: 1
  upper_bound: <approx_max_id>
  num_partitions: 8     # 16 for billion-row tables
```

The bounds don't need to be exact — they're hints for Spark's partition computation.

### Step 6: Construct the YAML entry

```yaml
<TABLE_NAME>:
  source_table: <TABLE_NAME>
  domain: <payments|balances|shared|...>
  watermark_column: LAST_UPDATED_TIME
  primary_key: [<PK_COLUMNS>]
  write_mode: append
  partition_spec:
    - { transform: <transform>, column: <col>, n: <n if bucket/truncate> }
  sort_order: [<COL1>, <COL2>]    # optional but recommended for facts
  parallel_extract:               # optional, for large tables only
    column: <numeric_pk>
    lower_bound: 1
    upper_bound: <approx>
    num_partitions: 8
```

### Step 7: Validate

Mental check (or actually instantiate the `TableConfig`):

- `write_mode == 'merge'` requires non-empty `primary_key`.
- Partition transforms `bucket` and `truncate` require `n`.
- `partition_spec` columns must exist in the source DDL.
- If `watermark_column` is null and `write_mode == 'append'`, that's a yellow flag — every run will full-scan the source.

### Step 8: Insert into examples/tables.yaml

- Find the right domain section in `examples/tables.yaml`.
- Insert alphabetically within the section if existing entries are sorted.
- Match the indentation and style of neighboring entries exactly.

### Step 9: Produce the backfill command

```bash
aws glue start-job-run \
  --job-name <customer>-strata-ingest \
  --arguments '{"--TABLE_NAME":"<TABLE_NAME>","--FULL_REFRESH":"true"}'
```

### Step 10: Hand back a summary

Tell the user:

1. **What was added** — table name, domain, key columns.
2. **What was assumed** — anything you guessed (typical: that `LAST_UPDATED_TIME` exists, that the bounds for parallel extract are reasonable).
3. **Why this partition spec** — one sentence on the reasoning.
4. **The backfill command** — copy-pastable.
5. **What to verify after the backfill** — typically:
   ```sql
   SELECT COUNT(*), MAX(LAST_UPDATED_TIME)
   FROM silver_<domain>.<table_name_lower>;
   ```

## Common mistakes to avoid

- Adding Python code instead of YAML. The entire framework is config-driven.
- Inventing a column name that isn't in the source DDL.
- Choosing `merge` mode "because it's safer." Append is the default for a reason.
- Setting `num_partitions` higher than the number of Glue workers × 4 — no benefit.
- Forgetting to add the table to `scripts/backfill_all.sh` if it should be part of routine backfills.
- Skipping the `primary_key` field — it's optional in append mode but valuable for documentation.

## Files this skill touches

- `examples/tables.yaml` — adds one entry.
- (Maybe) `scripts/backfill_all.sh` — adds to the `TABLES=()` array.

This skill never touches Python source code. If the user request requires Python changes, surface that as a separate task.
