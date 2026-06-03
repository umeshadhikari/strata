#!/usr/bin/env bash
# Package the strata package and upload to S3 for AWS Glue.
#   $1 = scripts bucket (terraform output `scripts_bucket`)

set -euo pipefail

SCRIPTS_BUCKET="${1:?usage: $0 <scripts-bucket>}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Packaging strata/ as zip for Glue workers..."
(cd "$HERE/src" && zip -rq "$TMP/strata.zip" strata -x '*.pyc' -x '__pycache__/*' -x '*.dist-info/*')

echo "Writing entry-point stub..."
cat > "$TMP/main.py" <<'PY'
"""Glue entry point — delegates to strata.ingest.main()."""
from strata.ingest import main

if __name__ == "__main__":
    main()
PY

echo "Uploading to s3://$SCRIPTS_BUCKET/scripts/..."
aws s3 cp "$TMP/main.py"               "s3://$SCRIPTS_BUCKET/scripts/main.py"
aws s3 cp "$TMP/strata.zip"            "s3://$SCRIPTS_BUCKET/scripts/strata.zip"
aws s3 cp "$HERE/examples/tables.yaml" "s3://$SCRIPTS_BUCKET/config/tables.yaml"

echo
echo "Done. Trigger a test run:"
echo "  aws glue start-job-run --job-name <customer>-strata-ingest \\"
echo "    --arguments '{\"--TABLE_NAME\":\"DIM_CURRENCY\",\"--FULL_REFRESH\":\"true\"}'"
