# Contributing to strata

Thanks for your interest in contributing.

## Getting started

```bash
git clone https://github.com/your-org/strata.git
cd strata
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running tests

```bash
pytest                          # all tests
pytest -m unit                  # unit tests only (no AWS)
pytest tests/unit/test_state.py # one file
```

## Linting and typing

```bash
ruff check .
ruff format .
mypy src/strata
```

## Pull request guidelines

1. **One concern per PR.** If you're fixing a bug AND adding a feature, send two PRs.
2. **Tests required for new features and bug fixes.**
3. **Update CHANGELOG.md** with a one-line description under `[Unreleased]`.
4. **Update docs** if you change behavior visible to operators.

## Local development against AWS

Most contributors don't have a payment-hub data mart handy. For local development:

- Use the moto library (installed via `[dev]`) to mock AWS services.
- Use a local PostgreSQL container as a fake data mart:
  ```bash
  docker run -d --name strata-pg \
    -e POSTGRES_PASSWORD=test \
    -p 5432:5432 \
    postgres:15
  ```
- Seed it with synthetic data using the scripts in `examples/synthetic-source/`.

## Code style

- Python 3.10+ (PEP 604 union syntax: `str | None`, not `Optional[str]`).
- 4-space indentation, 100-char line length.
- Module docstrings on every Python file.
- Public functions documented with a one-line summary.
- Prefer composition over inheritance.
- Don't catch `BaseException` or bare `except`.

## Reporting bugs

Open an issue with:
- AWS region
- Glue runtime version
- Source database engine and version
- Minimal reproducer (table config + error message + stack trace)

## Reporting security issues

Please do not open a public issue for security vulnerabilities. Email the maintainers privately.

## License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.
