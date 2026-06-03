#!/usr/bin/env bash
# Inspect strata's pipeline state from all three sources at once.
#
# Usage:
#   ./local/scripts/inspect-state.sh                       # human-readable
#   ./local/scripts/inspect-state.sh --json                # JSON for diffing
#   ./local/scripts/inspect-state.sh --table FACT_BALANCE  # different table
#   ./local/scripts/inspect-state.sh --snapshots 10        # more history
#
# Note: local/scripts/ isn't mounted into the spark container by default,
# so we docker-cp the helpers in on every run. To make it permanent, add
#   - ../local/scripts:/app/local/scripts:ro
# to the spark.volumes block in local/docker-compose.yml.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$HERE"

COMPOSE="docker compose -f local/docker-compose.yml"

# Stage the helpers inside the container under /tmp/strata_scripts.
$COMPOSE exec -T spark mkdir -p /tmp/strata_scripts
$COMPOSE cp local/scripts/inspect_state.py spark:/tmp/strata_scripts/inspect_state.py >/dev/null

$COMPOSE exec -T spark python /tmp/strata_scripts/inspect_state.py "$@"
