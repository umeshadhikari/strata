---
description: Add a new source table to strata's tables.yaml using the table-author agent.
---

Use the `table-author` agent to add a new source table to `examples/tables.yaml`.

Ask the user for any of the following that aren't already specified:

1. Source DDL or at minimum table name + columns + primary key + watermark column.
2. Logical domain (`payments`, `balances`, `shared`, or new).
3. Expected daily row volume.
4. Common query patterns (optional).

Then dispatch the table-author agent with that information. After it returns, summarize what was added and what backfill command to run.
