---
mode: agent
description: Diagnose a failed strata Glue run.
---

Diagnose a failed AWS Glue job run (or local ingest run).

Collect from the user whichever of these they have:

1. A **CloudWatch log excerpt** around the failure (preferred — includes
   timestamps and the stack trace).
2. A **Glue JOB_RUN_ID** — you can fetch logs yourself via the AWS CLI
   or boto3 if you have access.
3. The **error message + stack trace** as text.

Also collect:

- The **table name** affected.
- Whether this is a **one-off or recurring** failure.
- Any **recent changes** (new tables, schema changes, infra changes,
  source DB schema migrations).

Then diagnose in this order — each step has a "what to look for":

1. **Was the lock released?** Check the SQLite (local) or DynamoDB (AWS)
   state for `pending_run_id` matching the failed run. If set, that's
   the first recovery action.
2. **Did the Iceberg snapshot commit before the failure?** Read the
   latest snapshot's `glue.run_id` property. If it matches the failed
   run_id, the data is already committed — it's an orphan snapshot
   (Case C in `recovery.py`). Recovery: roll state DB forward to match.
3. **Is this schema drift?** Look for `SchemaDriftError` in the trace.
   Diagnose using the rules in `docs/reliability.md#schema-drift`.
4. **Is this a transient infra error?** `TransientError` types should
   have been retried automatically. If the failure persists past
   retries, it's worth escalating from Transient to Permanent in the
   code path.
5. **Did the source RDBMS change?** Check `last_updated_time` shape and
   range — if it shrank or went backwards, the watermark could be
   "stuck in the future" and pulling no rows.

Present the diagnosis as:

- **Root cause** (one sentence).
- **Recovery action** (what to do right now to get the next run green).
- **Prevention** (what config or code change would have caught this).

Reference the relevant invariant from `AGENTS.md` if the failure was
caused by violating one. Reference the failure-matrix row from
`docs/reliability.md` if there is one.
