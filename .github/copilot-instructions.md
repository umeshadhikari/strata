# GitHub Copilot instructions — strata

The canonical project guide for AI coding assistants — including Copilot —
lives in [`AGENTS.md`](../AGENTS.md) at the repository root.

This file exists so Copilot Chat picks up the guide automatically. The
same content is also exposed to Claude via `CLAUDE.md`. Single source of
truth, multiple discovery paths.

**Always read `AGENTS.md` before suggesting code changes in this repository.**

The headlines:

- strata's job is **at-least-once delivery with idempotent commits**. Any
  change that weakens that guarantee is a bug, even if the tests pass.
- **Four architectural invariants** are non-negotiable: Iceberg is the
  source of truth for committed data, every commit carries `glue.run_id`,
  the watermark window is bounded once at run start, and DynamoDB
  transitions use conditional updates. See AGENTS.md for the full
  statement.
- **Adding a new source table is a YAML edit only** — never a Python
  branch. If you find yourself wanting a per-table code path, redesign.
- The module map in AGENTS.md tells you which file owns which concern.
  Don't sprinkle related logic across modules.

For Copilot-specific reusable prompts (the slash-command equivalents
used by the Claude tooling), see `.github/prompts/`.

For path-scoped guidance (test conventions, terraform conventions,
etc.), the simplest approach is to read the relevant section of
AGENTS.md. If you need stricter scoping, put it in
`.github/instructions/<scope>.instructions.md` with an `applyTo`
frontmatter field.
