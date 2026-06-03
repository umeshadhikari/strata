---
description: Walk the user through standing up the strata local dev environment.
---

Walk the user through standing up strata locally on their laptop. The
goal is a working pipeline they can hack on: Postgres source, Iceberg
warehouse, Trino + Superset for querying — same code that runs on AWS,
different backends.

For the actual step-by-step walkthrough, follow the same procedure
documented in [`.github/prompts/setup-local.prompt.md`](../../.github/prompts/setup-local.prompt.md)
and [`docs/local-runtime.md`](../../docs/local-runtime.md). The seven
steps are:

1. Verify Docker + Compose + ports — `docker info`, `docker compose version`.
2. Bring up the stack — `./local/scripts/setup.sh`.
3. Seed Postgres — `python /app/local/postgres/bootstrap.py` inside the spark container.
4. First ingest — `./local/scripts/run-all.sh`.
5. Verify in Trino — `./local/scripts/trino.sh` and a count query.
6. Open Superset at `http://localhost:8088` (admin/admin), confirm the
   Trino connection.
7. Optional but recommended: run the incremental test
   (`./local/scripts/inspect-state.sh`, `./local/scripts/add-incremental-data.sh --inserts 100`,
   `./local/scripts/run-and-verify.sh --expect-delta 100`).

If the user is trying to deploy to AWS instead, redirect them to the
`/new-customer` command. The local setup and the AWS setup are very
different — don't blend the two procedures.

Common gotchas to watch for and surface proactively:

- Homebrew Postgres on 5432 (we map to 5433 on the host)
- Trino properties file (use the minimal version in the repo)
- Superset Flask app context (model imports inside the
  `with app.app_context():` block in `superset_init.sh`)
- The deprecated `bitnami/spark:3.5.0` image — we build our own from
  `eclipse-temurin:17-jdk-jammy`, so don't suggest pulling Bitnami's
  image even if a Google result says to.
