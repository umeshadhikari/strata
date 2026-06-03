---
description: Resolve a SchemaDriftError using the schema-drift-resolver agent.
---

Use the `schema-drift-resolver` agent to handle a source schema change that broke an ingest run.

Ask the user for:

1. The SchemaDriftError message from the failed run (full text).
2. The table affected.
3. What changed in the source, if known.

Dispatch the agent. After it returns, present:
- The SQL ALTER statements to run in Athena.
- The Glue command to re-trigger the failed run.
- The verification query to confirm the fix worked.

Remind the user that schema changes should ideally be coordinated with the data-mart team in advance, with a change ticket — this is reactive, not preventive.
