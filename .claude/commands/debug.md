---
description: Debug a failed strata Glue run using the glue-debugger agent.
---

Use the `glue-debugger` agent to diagnose a failed AWS Glue job run.

Ask the user for one of:

1. A CloudWatch log excerpt around the failure.
2. A Glue JOB_RUN_ID (the agent will fetch logs itself).
3. The error message + stack trace.

Also collect:

- Table name affected.
- Whether this is one-off or recurring.
- Any recent changes (new tables, schema changes, infra changes).

Dispatch the glue-debugger agent. After it returns, present the diagnosis and recovery action concisely.
