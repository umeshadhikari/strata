#!/usr/bin/env bash
# Open an interactive Trino CLI against the local stack.

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE/.."

docker compose -f local/docker-compose.yml exec trino \
  trino --server http://localhost:8080 --catalog iceberg
