#!/usr/bin/env bash
# Run an ingestion inside the spark container.
#
# Usage:
#   ./local/scripts/ingest.sh FACT_PAYMENT
#   ./local/scripts/ingest.sh FACT_PAYMENT --full-refresh

set -euo pipefail

TABLE="${1:?usage: $0 <TABLE_NAME> [--full-refresh]}"
shift || true
EXTRA_ARGS="$@"

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE/.."

docker compose -f local/docker-compose.yml exec -T spark \
  python -m strata.local_ingest --table "$TABLE" $EXTRA_ARGS
