#!/usr/bin/env bash
# Seed the local Postgres data mart with synthetic payments + balances.
#
# Runs the bootstrap script inside the already-running `spark` container
# rather than spinning up a fresh `python:3.11-slim` (which would need to
# pull from Docker Hub + PyPI — both commonly blocked on corporate
# networks). The spark image has psycopg2-binary baked in, so this works
# anywhere the stack is up.
#
# Idempotent: bootstrap.py upserts dimensions and (with --reset, the
# default) truncates+inserts facts. Safe to re-run any time you want a
# clean slate.
#
# Usage:
#   ./local/scripts/seed.sh                 # default: 30 days × 500/day, --reset
#   ./local/scripts/seed.sh --no-reset      # append to existing facts
#   ./local/scripts/seed.sh --days 7 --payments-per-day 100
#   ./local/scripts/seed.sh --wipe-state    # also clear SQLite/warehouse before seeding

set -euo pipefail

HERE="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$HERE"

COMPOSE="docker compose -f local/docker-compose.yml"
WIPE_STATE=false
RESET="--reset"
EXTRA_ARGS=()

# Parse our flags; pass everything else through to bootstrap.py
while [ "$#" -gt 0 ]; do
  case "$1" in
    --wipe-state)
      WIPE_STATE=true
      shift
      ;;
    --no-reset)
      RESET=""
      shift
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

# --- Pre-flight: is the spark container up? --- #
if ! $COMPOSE ps --status running --services 2>/dev/null | grep -q '^spark$'; then
  cat >&2 <<'ERR'

  ✗ The spark container is not running.

  Bring the stack up first:
    docker compose -f local/docker-compose.yml up -d

  Then re-run this script.
ERR
  exit 1
fi

# --- Optional: wipe stale watermark state + warehouse before re-seeding --- #
# Useful when the previous attempt advanced the watermark on empty data —
# without wiping, the next ingest's window starts in the future and skips
# the freshly-seeded rows.
if [ "$WIPE_STATE" = "true" ]; then
  echo "[seed] wiping SQLite state + Iceberg warehouse on the host..."
  $COMPOSE down >/dev/null 2>&1 || true
  rm -rf local/data/state local/data/warehouse
  echo "[seed] bringing stack back up..."
  $COMPOSE up -d
  echo "[seed] waiting for postgres to be healthy..."
  for _ in {1..30}; do
    if $COMPOSE exec -T postgres pg_isready -U strata -d data_mart >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

# --- Stage bootstrap.py into the spark container --- #
# local/postgres/ isn't mounted into spark by default — docker-cp instead.
echo "[seed] staging bootstrap.py inside the spark container..."
$COMPOSE exec -T spark mkdir -p /tmp/bootstrap
$COMPOSE cp local/postgres/bootstrap.py spark:/tmp/bootstrap/bootstrap.py >/dev/null

# --- Run the seeder --- #
echo "[seed] running bootstrap..."
$COMPOSE exec -T spark python /tmp/bootstrap/bootstrap.py $RESET "${EXTRA_ARGS[@]}"

# --- Verify --- #
# bootstrap.py prints its own row counts on success, but we double-check
# from outside the container in case stdout was lost in the docker plumbing.
echo
echo "[seed] verifying row counts..."
COUNTS=$($COMPOSE exec -T postgres psql -U strata -d data_mart -tA \
  -c "SELECT COUNT(*) FROM data_mart.fact_payment;")

if [ -z "$COUNTS" ] || [ "$COUNTS" = "0" ]; then
  cat >&2 <<'ERR'

  ✗ Seeder reported success but data_mart.fact_payment is still empty.

  Possible causes:
    - Postgres is healthy but on a different database than expected
      (check $COMPOSE exec postgres env | grep POSTGRES).
    - bootstrap.py exited 0 but its insert was rolled back. Re-run with
      verbose output and look for "ROLLBACK" or psycopg2 errors:
        $COMPOSE exec spark python /tmp/bootstrap/bootstrap.py --reset
ERR
  exit 1
fi

echo "  ✓ data_mart.fact_payment has $COUNTS rows"
echo
echo "Seed complete. Next:"
echo "  ./local/scripts/run-all.sh --full-refresh    # ingest everything into Iceberg"
echo "  ./local/scripts/inspect-state.sh             # confirm strata state"
