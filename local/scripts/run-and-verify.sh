#!/usr/bin/env bash
# Run strata ingest and print a before/after delta so you can verify the
# incremental window did what you expected.
#
# Usage:
#   ./local/scripts/run-and-verify.sh
#   ./local/scripts/run-and-verify.sh --table FACT_BALANCE
#   ./local/scripts/run-and-verify.sh --expect-delta 100
#   ./local/scripts/run-and-verify.sh --full-refresh
#
# run-and-verify imports inspect_state, so we copy both helpers in.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$HERE"

COMPOSE="docker compose -f local/docker-compose.yml"

$COMPOSE exec -T spark mkdir -p /tmp/strata_scripts
$COMPOSE cp local/scripts/inspect_state.py    spark:/tmp/strata_scripts/inspect_state.py >/dev/null
$COMPOSE cp local/scripts/run_and_verify.py   spark:/tmp/strata_scripts/run_and_verify.py >/dev/null

$COMPOSE exec -T spark python /tmp/strata_scripts/run_and_verify.py "$@"
