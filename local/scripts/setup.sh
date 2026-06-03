#!/usr/bin/env bash
# One-time local environment setup.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE/.."

echo "[1/6] Building the strata-spark image + bringing up the full stack..."
docker compose -f local/docker-compose.yml up -d --build

echo "[2/6] Waiting for Postgres to be healthy..."
for _ in {1..30}; do
  if docker compose -f local/docker-compose.yml exec -T postgres \
      pg_isready -U strata -d data_mart > /dev/null 2>&1; then
    break
  fi
  sleep 1
done
echo "  → postgres ready"

echo "[3/6] Copying example secrets if needed..."
if [ ! -f local/secrets/db.local.json ]; then
  if [ ! -f local/secrets/db.local.json.example ]; then
    cat >&2 <<'ERR'

  ✗ Missing local/secrets/db.local.json.example.

  This usually means an old .gitignore excluded the whole secrets/
  directory at clone time. Either pull latest (the .gitignore has been
  fixed to keep .example files), or create the file yourself:

    mkdir -p local/secrets
    cat > local/secrets/db.local.json <<'EOF'
    {
      "engine": "postgresql",
      "host": "postgres",
      "port": "5432",
      "database": "data_mart",
      "username": "strata",
      "password": "strata",
      "driver": "org.postgresql.Driver"
    }
    EOF

  Then re-run this script.
ERR
    exit 1
  fi
  cp local/secrets/db.local.json.example local/secrets/db.local.json
  echo "  → wrote local/secrets/db.local.json"
else
  echo "  → local/secrets/db.local.json already exists, leaving alone"
fi

echo "[4/6] Verifying the spark container is healthy..."
docker compose -f local/docker-compose.yml exec -T spark python --version

echo "[5/6] Bootstrapping reference data + 30 days × 500 payments/day..."
# Delegated to seed.sh, which runs bootstrap.py inside the spark container
# (no PyPI / Docker Hub network reach needed) and verifies row counts.
# If the seed fails, the whole setup fails — silent empty data marts are
# the worst kind of bug because the dashboard "works" but shows nothing.
if ! ./local/scripts/seed.sh; then
  cat >&2 <<'ERR'

  ✗ Bootstrap seeding failed. The stack is up but the data mart is empty,
    so any ingest job will write zero rows and the dashboards will be blank.

  Re-run the seeder by itself to see the underlying error:
    ./local/scripts/seed.sh

  Once that succeeds, run:
    ./local/scripts/run-all.sh --full-refresh
ERR
  exit 1
fi

echo "[6/6] Waiting for Trino and Superset to be healthy..."
for _ in {1..60}; do
  if curl -sf http://localhost:8080/v1/info > /dev/null 2>&1; then
    break
  fi
  sleep 2
done
echo "  → trino ready at http://localhost:8080"

for _ in {1..60}; do
  if curl -sf http://localhost:8088/health > /dev/null 2>&1; then
    break
  fi
  sleep 2
done
echo "  → superset ready at http://localhost:8088 (admin/admin)"

echo
echo "Setup complete. Next:"
echo "  ./local/scripts/run-all.sh --full-refresh    # ingest every table into Iceberg"
echo "  ./local/scripts/inspect-state.sh             # verify watermark + Iceberg state"
echo "  ./local/scripts/trino.sh                     # Trino CLI"
echo "  open http://localhost:8088                   # Superset (admin/admin)"
echo
echo "If something looks off later:"
echo "  ./local/scripts/seed.sh --wipe-state         # re-seed Postgres + clear strata state"
