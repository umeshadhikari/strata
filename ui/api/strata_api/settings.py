"""Runtime settings.

All values come from environment variables so the same container image runs
locally (via docker-compose) and in any future deployment without rebuilds.
Defaults match the local Docker stack.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Source Postgres — same DB strata extracts from.
    pg_host: str = os.environ.get("PGHOST", "postgres")
    pg_port: int = int(os.environ.get("PGPORT", "5432"))
    pg_db: str = os.environ.get("PGDATABASE", "data_mart")
    pg_user: str = os.environ.get("PGUSER", "strata")
    pg_password: str = os.environ.get("PGPASSWORD", "strata")
    pg_schema: str = os.environ.get("PGSCHEMA", "data_mart")

    # Superset — used to list dashboards for the embed page.
    # Inside Docker, Superset is at http://superset:8088. From the user's
    # browser (which is where the iframe loads) it's http://localhost:8088.
    superset_internal_url: str = os.environ.get(
        "SUPERSET_INTERNAL_URL", "http://superset:8088"
    )
    superset_public_url: str = os.environ.get(
        "SUPERSET_PUBLIC_URL", "http://localhost:8088"
    )
    superset_user: str = os.environ.get("SUPERSET_USER", "admin")
    superset_password: str = os.environ.get("SUPERSET_PASSWORD", "admin")

    # CORS — the Angular dev origin. In Docker the UI is served at 4200 on
    # the host; in production this would be the deployed UI URL.
    cors_origins: tuple[str, ...] = tuple(
        s.strip()
        for s in os.environ.get(
            "CORS_ORIGINS", "http://localhost:4200,http://localhost:8080"
        ).split(",")
        if s.strip()
    )


settings = Settings()
