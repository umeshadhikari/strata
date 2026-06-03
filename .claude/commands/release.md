---
description: Prepare a strata release using the prepare-release skill.
---

Invoke the `prepare-release` skill to walk through the release checklist.

Ask the user:
1. What version bump (patch / minor / major)?
2. Are there any breaking changes that need a migration note?

The skill will guide through:
- Updating `CHANGELOG.md`
- Bumping version in `pyproject.toml` and `src/strata/__init__.py`
- Running the full test suite
- Tagging the release
- Drafting the release notes

Do not actually push tags or merge to main without explicit user confirmation.
