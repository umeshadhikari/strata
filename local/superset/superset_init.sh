#!/bin/bash
# Superset bootstrap — minimal and resilient.
# Idempotent: safe to re-run. Survives package mirror flakiness and pip retries.

set -euo pipefail

# superset_config.py is bind-mounted in from local/superset/superset_config.py
# and selected via SUPERSET_CONFIG_PATH in docker-compose.yml. Keeping the
# config out of this init script means it stays effective even if this
# script never re-runs.

echo "[superset] Installing Trino driver..."
# Retry pip up to 3 times in case of network issues
for i in 1 2 3; do
  pip install --no-cache-dir \
      "trino[sqlalchemy]==0.327.0" \
      "sqlalchemy-trino==0.5.0" \
    && break
  echo "  pip retry $i/3..."
  sleep 5
done

echo "[superset] Running DB upgrade..."
superset db upgrade

echo "[superset] Creating admin user (admin / admin)..."
superset fab create-admin \
  --username admin --firstname Admin --lastname User \
  --email admin@example.com --password admin || true

echo "[superset] Initializing roles..."
superset init

echo "[superset] Registering Trino database (best-effort)..."
python <<'PY' || echo "  (datasource registration skipped — register via UI: Settings → Database Connections → + DATABASE)"
# Import order matters: create_app() FIRST so the app context exists, THEN
# import models. Importing models at module scope triggers app.config reads
# that fail with "Working outside of application context" otherwise.
from superset.app import create_app

app = create_app()
with app.app_context():
    from superset import db
    from superset.models.core import Database

    name = "Trino (strata)"
    uri = "trino://admin@trino:8080/iceberg"
    existing = db.session.query(Database).filter_by(database_name=name).first()
    if existing:
        existing.sqlalchemy_uri = uri
        print(f"  → updated {name}")
    else:
        db.session.add(Database(database_name=name, sqlalchemy_uri=uri))
        print(f"  → registered {name} = {uri}")
    db.session.commit()
PY

echo "[superset] Starting gunicorn on :8088..."
exec gunicorn --bind 0.0.0.0:8088 --workers 1 --threads 4 --timeout 120 \
    "superset.app:create_app()"
