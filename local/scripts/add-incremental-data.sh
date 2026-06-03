#!/usr/bin/env bash
# Inject incremental rows into Postgres so the next ingest has work to do.
#
# Default target is the payments fact (fact_pay_payment); use --table to
# point at the balances domain instead.
#
# Usage:
#   ./local/scripts/add-incremental-data.sh --inserts 100
#   ./local/scripts/add-incremental-data.sh --updates 50
#   ./local/scripts/add-incremental-data.sh --mixed 100 --label test-3
#   ./local/scripts/add-incremental-data.sh --inserts 100 --seed 42
#
# Pick a different fact table (all four share last_updated_time as the
# watermark column, so the same flags work):
#   ./local/scripts/add-incremental-data.sh --table fact_as_balance --inserts 200
#   ./local/scripts/add-incremental-data.sh --table fact_as_transaction --mixed 500
#   ./local/scripts/add-incremental-data.sh --table fact_as_currency_exchange --inserts 90
#
# Logical names also work (FACT_PAY_PAYMENT, FACT_AS_BALANCE, etc.) so the
# shell call lines up with the table name strata uses in its log lines.
#
# See inspect-state.sh for the note on why we docker-cp instead of relying
# on a volume mount.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$HERE"

COMPOSE="docker compose -f local/docker-compose.yml"

$COMPOSE exec -T spark mkdir -p /tmp/strata_scripts
$COMPOSE cp local/scripts/add_incremental_data.py spark:/tmp/strata_scripts/add_incremental_data.py >/dev/null

$COMPOSE exec -T spark python /tmp/strata_scripts/add_incremental_data.py "$@"
