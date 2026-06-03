---
description: Review a pull request for reliability impact using the reliability-reviewer agent.
---

Use the `reliability-reviewer` agent to assess a code change against strata's reliability invariants.

If the user provides:
- A git diff or branch name → review those changes.
- A PR URL → fetch the diff first.
- A list of changed files → review each.

The agent will check the four invariants (Iceberg-as-truth, run-id idempotency, bounded windows, conditional updates), check missing concerns, and produce a VERDICT (APPROVE / REQUEST CHANGES / NEEDS DISCUSSION) with file:line evidence.

After it returns, summarize the verdict and the top 3 most important changes requested.
