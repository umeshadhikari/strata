# AGENTS.md — strata project guide for AI coding assistants

This file is the source of truth for how any AI assistant (Claude Code,
Cowork, GitHub Copilot, Cursor, Aider, Continue, etc.) should work in
this repository. Anything not specified here defaults to the project's
prevailing conventions. Read this before writing any code.

> If you're using Claude, this file is referenced by `CLAUDE.md`. If
> you're using GitHub Copilot, it's referenced by
> `.github/copilot-instructions.md`. The content below is the same
> regardless of which tool is reading it.

## What strata is

A Python framework that copies tables from a relational source (Oracle,
PostgreSQL, MySQL) into Apache Iceberg tables on S3, registered in AWS Glue
Data Catalog. One parameterised AWS Glue PySpark job handles every table,
driven by a single YAML config.

The framework's job is **at-least-once delivery with idempotent commits**.
After any failure mode, the next run resumes cleanly with no data loss and
no duplication. The implementation of this guarantee is the most important
invariant in the codebase.

## Setup paths — where to send a user who wants to run strata

If a user asks how to set up, install, run, or deploy strata, send them
to the right canonical doc based on what they're trying to do. Don't
reinvent setup instructions inline — these docs are the single source
of truth and stay in sync with the actual code and Terraform.

| User wants to… | Send them to | Copilot prompt |
|---|---|---|
| Run strata on their laptop with no AWS account | [`docs/local-runtime.md`](docs/local-runtime.md) + [`local/README.md`](local/README.md) | `/setup-local` |
| Verify incremental ingestion works (testing watermark, idempotency) | [`docs/testing-incremental.md`](docs/testing-incremental.md) | (combine `/setup-local` then the test scripts) |
| Deploy strata to their own AWS sandbox for dev/test | [`docs/aws-runtime.md`](docs/aws-runtime.md) | `/new-customer` (set `customer_id = sandbox-<your-name>`) |
| Onboard a real customer to production | [`docs/aws-runtime.md`](docs/aws-runtime.md) + the operational runbook | `/new-customer` |
| Understand the architecture before doing anything | [`docs/architecture.md`](docs/architecture.md) | (no prompt — read the doc) |
| Run a backfill after deploying | [`docs/operational-runbook.md`](docs/operational-runbook.md) | `/backfill` |

The TL;DR for orientation:

- **Local dev** = Docker Compose stack (Postgres + Spark + Trino +
  Superset). Same code, different backends. Iceberg metadata lives in
  PostgreSQL via the JDBC catalog; warehouse is a local directory.
  Takes ~5 minutes to stand up, ~3 minutes to seed and run the first
  ingest. No AWS account needed.
- **AWS dev** = a personal sandbox account with the full Terraform
  module applied, a small source DB pointed at, and `customer_id` set
  to something like `sandbox-<your-name>`. Same workflow as a customer
  deployment, just with disposable credentials.
- **AWS production** = the customer onboarding workflow (`/new-customer`
  prompt or `docs/aws-runtime.md`). 25 resources, 10 deployment steps,
  ends with a scheduled 06:00 UTC trigger.

## Architectural invariants — do not violate

These four properties make the recovery guarantees work. Any change that
weakens them is a bug, even if it passes tests.

1. **Iceberg snapshots are the source of truth for committed data.**
   DynamoDB is a cache. They get reconciled at the start of every run. If
   they disagree, Iceberg wins. Never make DynamoDB authoritative for
   "what has been committed."

2. **Every Iceberg commit carries `glue.run_id` in snapshot properties.**
   This is the linchpin of idempotency. The same `run_id` on retry must
   produce the same logical commit. Don't write paths that bypass the
   `run_id` tagging.

3. **The watermark window is bounded once at run start.**
   `lower = current_watermark`, `upper = now()`. Same bounds on retry.
   Never compute `upper = now()` inside the extract function — it would
   make retries non-deterministic and create gaps or overlaps.

4. **DynamoDB state transitions use conditional updates.**
   Acquire → conditional on `attribute_not_exists` or lock expiry.
   Complete → conditional on `pending_run_id = my_run_id`.
   Fail → same. Never write code that updates state unconditionally.

If a feature seems to require violating one of these, that's a signal to
discuss the design before writing code.

## Module map

| Module | Responsibility | Lines roughly |
|---|---|---|
| `src/strata/ingest.py` | Pipeline orchestrator. The Glue job entry point. | 250 |
| `src/strata/config.py` | YAML loading, validation, dataclasses. | 180 |
| `src/strata/state.py` | DynamoDB state machine with conditional locking. | 270 |
| `src/strata/recovery.py` | Five-case reconciliation between DynamoDB and Iceberg. | 110 |
| `src/strata/extract.py` | JDBC reads with bounded windows. | 130 |
| `src/strata/writer.py` | Iceberg writes with idempotency check + schema evolution. | 220 |
| `src/strata/retry.py` | Decorator for exponential backoff on transient errors. | 60 |
| `src/strata/metrics.py` | CloudWatch metrics + structured logging. | 60 |
| `src/strata/exceptions.py` | TransientError / PermanentError hierarchy. | 60 |

When a request says "add a feature to handle X," the first step is figuring
out which module owns that concern. Don't sprinkle related logic across
modules.

## Conventions

### Python

- **Target Python 3.10+.** Use PEP 604 union syntax (`str | None`), not
  `Optional[str]`. Use `list[X]`, not `List[X]`. Use match statements where
  they improve clarity.
- **4-space indentation, 100-char line length.** Enforced by ruff.
- **Module docstring at the top of every file** describing purpose.
- **Public functions get a one-line docstring** explaining what they do.
- **Don't catch bare `Exception` or `BaseException`** except at the very top of
  the orchestrator. Inside helpers, catch specific types from
  `strata.exceptions`.
- **Use the `TransientError` / `PermanentError` hierarchy** to communicate
  retry semantics. New error types should subclass one of these.

### Tests

- **Every new function needs at least one unit test.** Place under
  `tests/unit/test_<module>.py`.
- **Tests must not require AWS credentials.** Use `moto` for AWS mocks if
  you genuinely need to test AWS behavior; otherwise design the code so the
  AWS-facing parts are thin and the testable parts are pure.
- **Pytest fixtures for setup**, not module-level state.

### Configuration

- **The YAML config is the only source of per-table specifics.** Don't
  hardcode table names, watermark columns, or schemas in Python.
- **Adding a new table is a YAML edit only.** If a feature you're building
  forces a per-table Python branch, redesign.
- **Validation lives in `config.py`** — fail fast in `__post_init__` or
  during parsing. Don't push validation errors into the pipeline.

### Terraform

- **One module: the customer stamp.** Resources are prefixed with
  `var.customer_id`. Per-customer state lives in customer-specific Terraform
  workspaces or per-customer state files.
- **Outputs are the public API of the module.** When you add a resource that
  callers need to reference, add an output for it.
- **Sensitive variables are marked `sensitive = true`.** Never log them.

### Docs

- **Operational behavior changes update `docs/operational-runbook.md`.**
  If a new alarm fires, what should the on-call engineer do? Write it down.
- **Reliability-affecting changes update `docs/reliability.md`.** New
  failure modes get rows in the failure matrix.
- **Configuration changes update `docs/configuration.md`** and the example
  in `examples/tables.yaml`.

## Common tasks — and where to find specialised help

| Task | Claude (`/cmd` or agent) | Copilot (prompt) |
|---|---|---|
| **Set up local dev environment** | `/setup-local` | `/setup-local` |
| **Set up AWS environment (dev or customer)** | `/new-customer` or `new-customer-stamp` skill | `/new-customer` |
| Add a new source table | `/add-table` or `table-author` agent | `/add-table` |
| Debug a failed Glue run | `/debug` or `glue-debugger` agent | `/debug` |
| Review a PR for reliability impact | `/review` or `reliability-reviewer` agent | `/review` |
| Run or plan a backfill | `/backfill` | `/backfill` |
| Resolve a `SchemaDriftError` | `/schema-drift` or `schema-drift-resolver` agent | `/schema-drift` |
| Prep a release | `/release` or `prepare-release` skill | `/release` |
| Recommend partition spec for a new table | `partition-tuner` agent | follow `docs/partitioning.md` |
| Write tests for a new function | `test-author` agent | use the test conventions above |

The Claude-specific configurations live in `.claude/agents/` and
`.claude/commands/`; the Copilot-specific ones live in
`.github/prompts/`. Both call the same underlying guidance — they're
just different invocation surfaces.

## What NOT to do

- **Don't add a daemon, server, or always-on process.** This is a batch
  framework. Long-running services break the cost model.
- **Don't introduce a new AWS service without justifying it against the
  serverless-first principle.** Glue, S3, DynamoDB on-demand, Secrets
  Manager, CloudWatch, EventBridge are the existing set. New additions need
  a strong reason.
- **Don't add per-table Python code paths.** If different tables need
  different behavior, parameterise it via YAML.
- **Don't write to DynamoDB outside the `StateManager` class.** All state
  transitions go through that class so the conditional-update invariants
  are enforced in one place.
- **Don't bypass the `retry` decorator for transient operations.** Mixing
  manual try/except with retries is error-prone.
- **Don't catch `TransientError` and swallow it.** It must propagate so
  the orchestrator can release the lock and let the next run try again.
- **Don't read the watermark from anywhere except `StateManager.read()`.**
  Reading directly from DynamoDB bypasses the dataclass validation.

## Decision rules

When in doubt:

- **Reliability over performance.** If a change makes things faster but
  makes recovery less clear, don't make it.
- **Simplicity over flexibility.** If two people might use the feature one
  way and three might use it another way, build the first way and let the
  others migrate later.
- **Convention over configuration.** New config knobs need a strong
  justification. Defaults should serve 80% of cases.
- **Fail loudly, recover clearly.** A schema drift should fail visibly and
  page the on-call. It should not silently keep running with the wrong data.

## Git

- **One concern per commit.** If you're fixing a bug AND adding a feature,
  send two commits or two PRs.
- **Conventional Commits** for messages: `feat:`, `fix:`, `docs:`, `test:`,
  `refactor:`, `chore:`, `ci:`.
- **Update `CHANGELOG.md` under `[Unreleased]`** for every behavior-visible
  change. Format: one bullet per change, past tense.

## When uncertain

- Read the relevant module's docstring. Most concerns are documented at the
  module level.
- Read `docs/reliability.md` for failure-mode questions.
- Read `docs/partitioning.md` for performance / layout questions.
- Read `docs/operational-runbook.md` for "what should an operator do" questions.
- Read `docs/testing-incremental.md` for "how do I verify incremental ingest" questions.
- If still uncertain, ask. Don't guess at architectural decisions.
