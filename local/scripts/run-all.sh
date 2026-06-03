#!/usr/bin/env bash
# Run the full local ingestion across every table in tables.local.yaml.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE/.."

TABLES=(
  DIM_DATE
  DIM_DATA_OWNER
  DIM_USER
  DIM_CLASSIFICATION
  DIM_ROUTING
  DIM_AS_TRANSACTION_TYPE
  DIM_AS_CHARACTERISTICS
  DIM_PAY_BANK_STATUS
  DIM_PAY_CHARACTERISTICS
  DIM_CURRENCY
  DIM_ACCOUNT
  FACT_AS_CURRENCY_EXCHANGE
  FACT_AS_BALANCE
  FACT_AS_TRANSACTION
  FACT_PAY_PAYMENT
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
