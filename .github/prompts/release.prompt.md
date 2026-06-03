---
mode: agent
description: Prepare a strata release — changelog, version bump, tests, tag, draft notes. Do not push without explicit confirmation.
---

Prepare a new strata release. Walk through the procedure step by step.
**Never push tags or merge to main without explicit user confirmation.**

## Pre-release checklist

Before starting, confirm:

1. **On `main` and up to date** — `git checkout main && git pull`.
2. **Working tree is clean** — `git status` reports nothing to commit.
3. **CI is green** — the latest run of `.github/workflows/ci.yml` is green.
4. **No unresolved PRs** targeting this release.

If any check fails, stop and surface it.

## Step 1: Decide the version bump

Semver:

- **patch** (`0.1.0` → `0.1.1`) — bug fixes, no behavior change for users.
- **minor** (`0.1.0` → `0.2.0`) — new features, backward compatible.
- **major** (`0.1.0` → `1.0.0`) — breaking changes; require migration notes.

For pre-1.0 (`0.x.y`) the rules shift: breaking changes only bump
**minor** (0.x → 0.x+1). Strict semver kicks in at 1.0.0.

Ask the user which type. If they describe breaking changes but ask for
minor (post-1.0), challenge it.

## Step 2: Review the changelog

Open `CHANGELOG.md`. The `[Unreleased]` section should list everything
since the last release. Audit:

- Are all merged PRs reflected? Cross-check with `git log --oneline
  $(git describe --tags --abbrev=0)..HEAD`.
- Are entries in the right Keep-a-Changelog category (`Added`,
  `Changed`, `Fixed`, `Removed`, `Deprecated`, `Security`)?
- Is there a **migration note** for any breaking change?

If anything's missing, prompt the user to add it before proceeding.

## Step 3: Run the full test suite locally

```bash
pip install -e ".[dev]"
ruff check src tests
mypy src/strata             # continue-on-error is OK at this stage
pytest                       # all unit tests
```

If anything fails, stop and fix it. Don't release with red tests.

## Step 4: Bump the version — two places

1. `pyproject.toml`:
   ```toml
   version = "X.Y.Z"
   ```
2. `src/strata/__init__.py`:
   ```python
   __version__ = "X.Y.Z"
   ```

Both must match. CI fails fast if they don't.

## Step 5: Update the changelog

Move `[Unreleased]` content into a new dated section:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- ...

### Fixed
- ...
```

Leave an empty `[Unreleased]` at the top for the next cycle.

Update the link references at the bottom of `CHANGELOG.md`:

```markdown
[Unreleased]: https://github.com/your-org/strata/compare/vX.Y.Z...HEAD
[X.Y.Z]: https://github.com/your-org/strata/compare/v(previous)...vX.Y.Z
```

## Step 6: Commit the release prep

```bash
git add CHANGELOG.md pyproject.toml src/strata/__init__.py
git commit -m "chore: prepare release X.Y.Z"
```

## Step 7: Tag

```bash
git tag -a "vX.Y.Z" -m "Release X.Y.Z"
```

## Step 8: Verify the tag locally

```bash
git show "vX.Y.Z"
git tag -l "v*" --sort=-v:refname | head -5
```

The new tag should be at the top.

## Step 9: Draft release notes

For the GitHub release UI, prepare:

- **Title**: `strata X.Y.Z`
- **Body**: the changelog section for this version, plus a "Highlights"
  paragraph at the top summarising what's new.
- **Breaking changes** section if any, with migration instructions.
- **Contributors**: list PR authors since the last tag (use
  `git log $(previous-tag)..HEAD --format="%an"` and dedupe).

**Leave the GitHub release as a draft.** Don't click "Publish" — that's
the user's call.

## Step 10: Confirm before pushing

Show the user:

1. The bumped version files (diff of `pyproject.toml`, `__init__.py`).
2. The changelog section that's about to be released.
3. The tag (`git show vX.Y.Z`).
4. The draft release notes.

**Wait for explicit confirmation** before:

```bash
git push origin main
git push origin "vX.Y.Z"
```

If the user is silent or ambiguous about whether to push, default to
**not pushing** and ask again.

## Step 11: Post-release

After the push and any publish-to-PyPI workflow completes:

- Notify consumer / customer teams that the new version is available.
- Open a tracking issue for any follow-ups discovered during the release
  (a common one: "doc update for X discovered while writing changelog").

## What this workflow does NOT do

- It does not push tags or merge to main without user confirmation.
- It does not modify code beyond version bumps and changelog updates.
- It does not skip failing tests, ever — even with "we'll fix it next
  release" pressure.

## Common release mistakes — flag these if you see them

- Bumping major for an additive change. (Usually means minor.)
- Forgetting to update both `pyproject.toml` and `__init__.py`. CI
  catches this but it's avoidable.
- Bundling dependency bumps with a feature release. Those belong in their
  own commit and PR.
- Mixing `feat` and `fix` content in the same release without separating
  them in the changelog. Users can't tell what's safe to upgrade for.
- Forgetting to update the link references at the bottom of
  `CHANGELOG.md`. The release page links break.
