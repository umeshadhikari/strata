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
  cp local/secrets/db.local.json.example local/secrets/db.local.json
  echo "  → wrote local/secrets/db.local.json"
fi

echo "[4/6] Verifying the spark container is healthy..."
docker compose -f local/docker-compose.yml exec -T spark python --version

echo "[5/6] Bootstrapping reference data + 30 days × 500 payments/day..."
# Mount the local/postgres directory into the spark container for the bootstrap script
docker run --rm \
  --network strata-network \
  -e PGHOST=postgres -e PGUSER=strata -e PGPASSWORD=strata -e PGDATABASE=data_mart \
  -v "$(pwd)/local/postgres:/work:ro" \
  python:3.11-slim \
  sh -c "pip install --quiet psycopg2-binary && python /work/bootstrap.py --reset"

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
echo "  ./local/scripts/ingest.sh DIM_CURRENCY --full-refresh"
echo "  ./local/scripts/ingest.sh FACT_PAYMENT --full-refresh"
echo "  ./local/scripts/trino.sh                  # Trino CLI"
echo "  open http://localhost:8088                # Superset (admin/admin)"
