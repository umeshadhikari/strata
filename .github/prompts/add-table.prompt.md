---
mode: agent
description: Add a new source table to strata's tables.yaml.
---

Add a new source table to `examples/tables.yaml` (or `local/config/tables.local.yaml`
if the request is about local-only ingestion).

Before writing any YAML, collect from the user any of the following that
aren't already specified in the request:

1. **Source DDL** or at minimum: table name, columns, primary key, and
   the watermark column (the timestamp/sequence column that monotonically
   advances when a row changes).
2. **Logical domain** — one of `payments`, `balances`, `shared`, or a new
   domain name. This becomes the silver schema (`silver_<domain>`).
3. **Expected daily row volume** — used to recommend partition strategy.
4. **Common query patterns** (optional) — used to recommend sort order.

Then produce the YAML entry following the conventions in `AGENTS.md`:

- The block goes under `tables:` keyed by the **uppercase logical name**
  (e.g. `FACT_NEW_THING`).
- Required fields: `source_table`, `domain`, `watermark_column`,
  `primary_key`.
- Recommend a `partition_spec` based on volume:
  - < 1M rows/year → no partitions
  - 1M–100M → `days(<date_column>)` only
  - \> 100M or hot partition risk → `days(<date_column>) + bucket(N, <pk>)`
- Add `sort_order` if a clear query pattern was given.

After writing the YAML, also list:

- The **backfill command** to run after deployment (`python -m strata.ingest
  --table <NAME> --full-refresh`).
- Any **downstream changes** that may be needed (Trino views, Superset
  dataset registration, dashboard updates).
- The **CHANGELOG entry** under `[Unreleased]` (`feat: add <NAME> source
  table`).

If the proposed table seems to require a per-table Python code path
rather than a pure YAML change, stop and discuss the design with the
user before writing anything — that's a signal of a misshapen feature
per AGENTS.md's "Don't add per-table Python code paths" rule.
