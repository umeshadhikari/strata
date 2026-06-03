#!/usr/bin/env bash
# Inject incremental rows into Postgres so the next ingest has work to do.
#
# Usage:
#   ./local/scripts/add-incremental-data.sh --inserts 100
#   ./local/scripts/add-incremental-data.sh --updates 50
#   ./local/scripts/add-incremental-data.sh --mixed 100 --label test-3
#   ./local/scripts/add-incremental-data.sh --inserts 100 --seed 42
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
