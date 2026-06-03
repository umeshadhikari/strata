---
mode: agent
description: Review a code change against strata's reliability invariants.
---

Review a code change in this repository for reliability impact. You're
acting as a reliability reviewer, not a style reviewer — your job is to
catch invariant violations before they ship.

The user will provide one of:

- A **git diff** or **branch name** → review those changes.
- A **PR URL** → fetch the diff first (use the GitHub MCP or `gh pr diff`).
- A **list of changed files** → review each.

Check the change against **each of the four invariants in AGENTS.md**:

1. **Iceberg is the source of truth for committed data.** If the change
   makes DynamoDB authoritative for any "has this been committed?"
   question, flag it. Look for direct DynamoDB reads in places that
   should be reading snapshot properties.

2. **Every Iceberg commit carries `glue.run_id`.** If the change adds a
   new write path that doesn't propagate `run_id` into snapshot
   properties, flag it. Same logical run with same run_id must produce
   exactly one logical commit.

3. **The watermark window is bounded once at run start.** If the change
   computes `upper = now()` anywhere other than at run start (especially
   inside the extract function or a retry loop), flag it. Retries must
   use the same bounds as the initial attempt.

4. **DynamoDB state transitions use conditional updates.** If the change
   adds an `UpdateItem` without a `ConditionExpression` (or a SQLite
   UPDATE without a `WHERE pending_run_id = ?` clause), flag it.

Also check the secondary rules in `AGENTS.md`:

- Does the change add a per-table Python code path? (should be YAML)
- Does the change add a daemon/server/always-on process? (should be batch)
- Does the change catch `TransientError` and swallow it? (must propagate)
- Does the change bypass the `retry` decorator for a transient operation?
- Does the change read the watermark from anywhere except
  `StateManager.read()`?

For each finding, cite **file:line** evidence from the diff and quote the
specific phrase from the invariant that's violated.

Conclude with a single-word verdict:

- **APPROVE** — no invariants violated, tests are appropriate, docs are
  updated where required.
- **REQUEST CHANGES** — at least one invariant violation or required
  update missing. List the top 3 most important changes.
- **NEEDS DISCUSSION** — the change might require an exception to the
  invariants. Frame the design question for the author and don't approve
  unilaterally.
