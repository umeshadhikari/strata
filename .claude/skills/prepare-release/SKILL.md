---
name: prepare-release
description: Prepare a strata release. Walks through changelog update, version bump, full test suite, tag, and release notes. Use when cutting a new release. Triggers include: "release", "cut a release", "tag v", "bump version".
---

# Skill: prepare-release

## When to use this skill

Time to ship a new version of strata. Common triggers:

- A milestone has been hit (feature complete, bug fixed).
- Scheduled release cadence (e.g., monthly).
- Customer needs a specific fix or feature deployed.

## Pre-release checklist

Before starting:

1. **You're on `main` and up to date** — `git checkout main && git pull`.
2. **Working tree is clean** — `git status`.
3. **CI is green** — check the latest run of `.github/workflows/ci.yml`.
4. **No unresolved PRs targeting the release** — check open PRs.

## Release procedure

### Step 1: Decide the version bump

Semver:
- **patch** (`0.1.0` → `0.1.1`) — bug fixes, no behavior change for users.
- **minor** (`0.1.0` → `0.2.0`) — new features, backward compatible.
- **major** (`0.1.0` → `1.0.0`) — breaking changes; explicitly call out migration steps.

For 0.x.y releases, the rules are slightly different: breaking changes only bump minor (0.x → 0.x+1). Strict semver kicks in at 1.0.0.

Ask the user which type.

### Step 2: Review the changelog

Open `CHANGELOG.md`. The `[Unreleased]` section should list everything since the last release. Review:

- Are all merged PRs reflected?
- Are entries in the right category (`Added`, `Changed`, `Fixed`, `Removed`, `Deprecated`, `Security`)?
- Is there a migration note for any breaking change?

If anything's missing, ask the user to add it before proceeding.

### Step 3: Run the full test suite locally

```bash
pip install -e ".[dev]"
ruff check src tests
mypy src/strata             # continue-on-error is OK at this stage
pytest                       # all unit tests
```

If anything fails, stop and fix it. Don't release with red tests.

### Step 4: Bump the version

Two places:

1. `pyproject.toml`:
   ```toml
   version = "X.Y.Z"
   ```
2. `src/strata/__init__.py`:
   ```python
   __version__ = "X.Y.Z"
   ```

Both must match. If they don't, CI catches it.

### Step 5: Update the changelog

Move the `[Unreleased]` content into a new section with the version and today's date:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- ...

### Fixed
- ...
```

Leave an empty `[Unreleased]` at the top for the next development cycle.

Update the bottom of the file with the new diff link:

```markdown
[Unreleased]: https://github.com/your-org/strata/compare/vX.Y.Z...HEAD
[X.Y.Z]: https://github.com/your-org/strata/compare/v(previous)...vX.Y.Z
```

### Step 6: Commit the release prep

```bash
git add CHANGELOG.md pyproject.toml src/strata/__init__.py
git commit -m "chore: prepare release X.Y.Z"
```

### Step 7: Tag

```bash
git tag -a "vX.Y.Z" -m "Release X.Y.Z"
```

### Step 8: Verify the tag

```bash
git show "vX.Y.Z"
git tag -l "v*" --sort=-v:refname | head -5
```

The new tag should be at the top.

### Step 9: Draft release notes

For the GitHub release UI, prepare:

- **Title**: `strata X.Y.Z`
- **Body**: copy of the changelog section for this version, plus a "Highlights" paragraph at the top summarizing what's new.
- **Breaking changes** section if any, with migration instructions.
- **Contributors** section listing PR authors since the last release.

Do not click "Publish" yet — leave the draft for the user to review.

### Step 10: Confirm before pushing

Show the user:

1. The bumped version files (diff of pyproject.toml, __init__.py).
2. The changelog section that's about to be released.
3. The tag.
4. The draft release notes.

**Wait for explicit confirmation** before:

```bash
git push origin main
git push origin "vX.Y.Z"
```

### Step 11: Post-release

After push, if there's a publish-to-PyPI workflow, monitor it. Otherwise:

- Notify any consumers / customer teams that an update is available.
- Open a tracking issue for any follow-ups discovered during the release.

## What this skill does NOT do

- It does not push tags or merge to main without user confirmation.
- It does not modify code beyond version bumps and changelog updates.
- It does not skip failing tests, ever.

## Common mistakes to avoid

- Bumping major instead of minor for an additive change.
- Forgetting to update both `pyproject.toml` and `__init__.py` (CI catches but it's avoidable).
- Including dependency bumps in a release commit (they belong in their own commit).
- Mixing "fix" and "feature" in the same release without separating them in the changelog.
- Forgetting to update the link references at the bottom of `CHANGELOG.md`.
