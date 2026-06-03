#!/usr/bin/env bash
# One-time full backfill across all configured tables.
# Run AFTER infrastructure is provisioned and tables.yaml is uploaded.
#
# Usage: ./backfill_all.sh <customer-id>

set -euo pipefail

CUSTOMER_ID="${1:?usage: $0 <customer-id>}"
JOB_NAME="${CUSTOMER_ID}-strata-ingest"

# Dimensions first (smaller, faster, populate reference data)
# Facts last (biggest, slowest)
TABLES=(
  DIM_DATE
  DIM_CURRENCY
  DIM_DATA_OWNER
  DIM_ACCOUNT
  DIM_CLASSIFICATION
  DIM_PAYMENT_METHOD
  DIM_PAYMENT_CHARACTERISTICS
  DIM_FX_PAY_CHARACTERISTICS
  DIM_TRANSACTION_TYPE
  DIM_TRANSACTION_CHARACTERISTICS
  DIM_FUNCT_OBJECT
  DIM_ROUTING
  DIM_BALANCE_CHARACTERISTICS
  DIM_BALANCE_TYPE
  FACT_BALANCE
  FACT_CURRENCY_EXCHANGE
  FACT_TRANSACTION_BALANCE
  FACT_PAYMENT
  FACT_TRANSACTION
)

echo "Backfilling ${#TABLES[@]} tables on job ${JOB_NAME}..."
for T in "${TABLES[@]}"; do
  echo "Starting ${T}..."
  RUN_ID=$(aws glue start-job-run \
    --job-name "${JOB_NAME}" \
    --arguments "{\"--TABLE_NAME\": \"${T}\", \"--FULL_REFRESH\": \"true\"}" \
    --query 'JobRunId' --output text)
  echo "  → run ${RUN_ID}"
  sleep 3
done

echo
echo "All backfill runs started. Monitor with:"
echo "  aws glue get-job-runs --job-name ${JOB_NAME} --max-results 30"
