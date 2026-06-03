#!/usr/bin/env bash
# Run the full local ingestion across every table in tables.local.yaml.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE/.."

TABLES=(
  DIM_CURRENCY
  DIM_DATA_OWNER
  DIM_ACCOUNT
  DIM_PAYMENT_METHOD
  FACT_BALANCE
  FACT_PAYMENT
)

EXTRA_ARGS="${1:-}"

for T in "${TABLES[@]}"; do
  echo
  echo "=== $T ==="
  ./local/scripts/ingest.sh "$T" $EXTRA_ARGS
done

echo
echo "Done. Query with:"
echo "  ./local/scripts/trino.sh"
echo "  open http://localhost:8088"
