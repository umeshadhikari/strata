---
mode: agent
description: Resolve a SchemaDriftError — classify the drift, produce ALTER statements, recommend in-place ALTER vs full refresh.
---

A strata Glue run failed with `SchemaDriftError`. Your job is to diagnose
the drift, produce exact SQL to resolve it, and re-run the job. This is
reactive maintenance — gently note that source schema changes should
normally be coordinated with the data-mart team in advance via change
ticket, not reacted to after the alarm fires.

## Background — what Iceberg auto-handles vs what it doesn't

Auto-applied (no operator intervention needed):
- Adding **nullable** columns
- Type widening within the safe matrix: `tinyint → int`, `smallint → int`,
  `int → bigint`, `float → double`, `decimal(p,s) → decimal(p',s)` where
  `p' ≥ p` and scale matches.

Requires explicit ALTER:
- Renaming columns
- Changing column type outside the safe widening matrix
- Dropping columns
- Reordering columns *(irrelevant — Iceberg uses field IDs internally)*

When the writer detects a drift it can't auto-handle, it raises
`SchemaDriftError` with details. The job fails fast and the
`SchemaDriftAlerts` CloudWatch alarm fires.

## Inputs to collect from the user

1. **The full `SchemaDriftError` message** from the failed run. Format:
   `Schema drift in <table>:\n  - <col>: incoming <type1> vs existing <type2>\n  ...`
2. **The table name** affected.
3. **The intended source change**, if known (e.g., "we widened
   `amount` from `NUMBER(10,2)` to `NUMBER(18,2)` overnight").

## Step 1: Classify each drift line

For each column in the error message, classify it:

| Pattern in error | Classification | Safe? |
|---|---|---|
| `incoming X vs existing Y` where both are numeric and X is wider than Y per the safe matrix above | Type widening that *should* have auto-applied | **Investigate** — why didn't `_check_schema_evolution` accept it? Probably a writer bug. |
| `incoming X vs existing Y` where types are unrelated (`string` vs `int`, `date` vs `timestamp`) | Incompatible type change | **Unsafe** |
| Column appears in error but isn't in incoming source | Column dropped from source | Safe in Iceberg; ask if user wants to drop or keep with NULLs |
| Column appears in error but isn't in existing Iceberg | New non-nullable column | Add as nullable, backfill, then tighten if needed |

## Step 2: Decide the resolution

### Safe type widening

```sql
ALTER TABLE silver_<domain>.<table>
ALTER COLUMN <col> TYPE <new_type>;
```

Iceberg preserves field IDs, so historical data remains queryable.

### Incompatible type change — the dangerous case

Existing rows have the old type; new rows have the new type. Two options:

**Option A — column rotation** (preserves history under a new name):

```sql
ALTER TABLE silver_<domain>.<table>
ADD COLUMN <col>_new <new_type>;

UPDATE silver_<domain>.<table>
SET <col>_new = CAST(<col> AS <new_type>);   -- if a cast exists

ALTER TABLE silver_<domain>.<table>
DROP COLUMN <col>;

ALTER TABLE silver_<domain>.<table>
RENAME COLUMN <col>_new TO <col>;
```

**Option B — full refresh** (clean cut, all rows uniform type):

```bash
aws glue start-job-run \
  --job-name <customer>-strata-ingest \
  --arguments '{"--TABLE_NAME":"<TABLE>","--FULL_REFRESH":"true"}'
```

Discuss with the user before choosing. Heuristics:
- Table is small and re-extract is cheap → Option B.
- Table is huge and re-extract is expensive → Option A.
- Cast between old and new type is unsafe (e.g., string → int with
  garbage data) → Option B (let the source enforce it).

### Column rename

```sql
ALTER TABLE silver_<domain>.<table>
RENAME COLUMN <old_name> TO <new_name>;
```

**Verify** with the user that the source actually renamed, rather than
dropping the old column and adding a new one — those look identical in
the error message but the resolution is different.

### Column dropped from source

Either keep it in Iceberg (future rows will have NULL) or drop explicitly:

```sql
ALTER TABLE silver_<domain>.<table>
DROP COLUMN <col>;
```

Dropping discards the historical values. Confirm that's the user's intent.

### New non-nullable column added in source

Iceberg can't have non-nullable columns added to an existing table with
existing rows. Add as nullable, backfill, optionally tighten:

```sql
ALTER TABLE silver_<domain>.<table>
ADD COLUMN <col> <type>;   -- always nullable on add
```

Then backfill or accept NULLs for historical rows.

## Step 3: Decide between in-place ALTER and full refresh

Use **full refresh** when:
- The type change is incompatible and you want a clean cut.
- Multiple drifts compound and ALTER would be a lot of statements.
- The table is small enough that full refresh is cheap.

Use **in-place ALTER** when:
- The table is large and re-extraction is expensive.
- The change is safe (widening, rename, drop).

## Step 4: Produce the runbook

Hand back exactly what the operator needs to type, in three blocks:

```sql
-- Run in Athena
ALTER TABLE silver_<domain>.<table>
<operation>;
```

```bash
# Re-trigger the failed job (incremental — DO NOT add --FULL_REFRESH
# unless you went down the full-refresh path)
aws glue start-job-run \
  --job-name <customer>-strata-ingest \
  --arguments '{"--TABLE_NAME":"<TABLE>","--FULL_REFRESH":"false"}'
```

```sql
-- Verify the next ingest landed
SELECT COUNT(*), MAX(last_updated_time)
FROM silver_<domain>.<table>;
```

## Output format

```
DRIFT SUMMARY
-------------
- <col>: <classification> (safe | unsafe)
- ...

RECOMMENDED ACTION
------------------
<in-place ALTER | full refresh> — <one-sentence reason>

SQL TO RUN
----------
<exact statements>

THEN
----
<exact Glue command>

VERIFY WITH
-----------
<exact verification query>

CAVEATS
-------
- <anything the operator needs to know>
- <especially: any data loss the operator is consenting to>
```

## What NOT to do

- Don't suggest editing Iceberg metadata files by hand. SQL or AWS APIs
  only.
- Don't recommend ignoring the drift. The job failed for a reason.
- Don't recommend dropping a column without confirming the user is OK
  losing the historical data.
- Don't recommend renaming a column unless you've established with the
  user that the source actually renamed — `drop + add` and `rename` look
  identical in the error but the right Iceberg operation differs.
- Don't suggest a full refresh of a large table when an in-place ALTER
  would do. Re-extracting tens of GB just to widen a column is wasteful.
