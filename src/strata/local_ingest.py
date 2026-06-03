"""
strata.local_ingest — local pipeline orchestrator.

Same logical pipeline as strata.ingest, but with all AWS services replaced by
local substitutes:
  * Glue catalog       → Iceberg Hadoop catalog (local filesystem)
  * S3                 → Local filesystem under local/data/warehouse
  * DynamoDB           → SQLite under local/data/state.db
  * Secrets Manager    → JSON file (local/secrets/db.local.json)
  * CloudWatch         → stdout
  * Glue PySpark       → Plain PySpark

Run::

    python -m strata.local_ingest --table FACT_PAYMENT
    python -m strata.local_ingest --table FACT_PAYMENT --full-refresh
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import exceptions, recovery, retry, writer
from .extract import ExtractWindow, extract_jdbc
from .local_runtime.io import load_local_config, load_local_credentials
from .local_runtime.metrics import LocalMetrics, log_event
from .local_runtime.spark import build_local_spark
from .local_runtime.state import LocalStateManager
from .state import iso, now_utc

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("strata.local_ingest")
logging.getLogger("py4j").setLevel(logging.WARN)
logging.getLogger("botocore").setLevel(logging.WARN)


DEFAULTS = {
    # Inside the spark Docker container these paths are mounted at /app/...
    "config": os.environ.get("STRATA_CONFIG_PATH", "/app/config/tables.local.yaml"),
    "secrets": os.environ.get("STRATA_SECRETS_PATH", "/app/secrets/db.local.json"),
    "state_db": os.environ.get("STRATA_STATE_DB", "/data/state/strata.db"),
    "catalog": os.environ.get("STRATA_CATALOG_NAME", "iceberg"),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="strata local ingest")
    p.add_argument("--table", required=True, help="Logical table name from tables.yaml")
    p.add_argument(
        "--full-refresh",
        action="store_true",
        help="Ignore watermark and overwrite the table",
    )
    p.add_argument("--config", default=DEFAULTS["config"])
    p.add_argument("--secrets", default=DEFAULTS["secrets"])
    p.add_argument("--state-db", default=DEFAULTS["state_db"])
    p.add_argument("--catalog", default=DEFAULTS["catalog"])
    return p.parse_args(argv)


def add_metadata(df, run_id: str, source_table: str, committed_at: str):
    from pyspark.sql import functions as F

    return (
        df.withColumn("_ingest_run_id", F.lit(run_id))
        .withColumn("_ingest_timestamp", F.lit(committed_at).cast("timestamp"))
        .withColumn("_source_table", F.lit(source_table))
        .withColumn("_ingest_date", F.current_date())
    )


def compute_window(cfg, current_watermark: str | None, full_refresh: bool) -> ExtractWindow:
    upper = iso(now_utc())
    if full_refresh:
        return ExtractWindow(lower=None, upper=upper, full_refresh=True)
    return ExtractWindow(lower=current_watermark, upper=upper, full_refresh=False)


def compute_new_watermark(df, cfg, fallback_upper: str) -> str:
    from pyspark.sql import functions as F

    if not cfg.watermark_column:
        return fallback_upper
    df_cols_lower = [c.lower() for c in df.columns]
    if cfg.watermark_column.lower() not in df_cols_lower:
        return fallback_upper
    actual_col = df.columns[df_cols_lower.index(cfg.watermark_column.lower())]
    row = df.agg(F.max(F.col(actual_col)).cast("string").alias("m")).first()
    return row["m"] if row and row["m"] else fallback_upper


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.time()
    table = args.table
    run_id = f"local::{table}::{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    log_event("run_started", run_id=run_id, table=table, full_refresh=args.full_refresh)

    try:
        cfg = load_local_config(args.config, table)
        creds = load_local_credentials(args.secrets)
    except exceptions.ConfigError as exc:
        log.error("Config error: %s", exc)
        return 2

    spark = build_local_spark(catalog_name=args.catalog)
    target_fqn = f"{args.catalog}.{cfg.target_database}.{table.lower()}"

    # Create the database namespace if it doesn't exist
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {args.catalog}.{cfg.target_database}")

    metrics = LocalMetrics(table_name=table)
    state_mgr = LocalStateManager(args.state_db, table)

    # ---- RECONCILE ---- #
    try:
        state = recovery.reconcile_state(spark, state_mgr, target_fqn)
        log_event(
            "state_reconciled",
            current_watermark=state.current_watermark,
            pending=state.pending_run_id,
        )
    except exceptions.ConcurrentRunError as exc:
        log.warning("Another run holds the lock; exiting cleanly: %s", exc)
        metrics.emit("ConcurrentRunSkips", 1)
        return 0

    # ---- WINDOW ---- #
    committed_at = iso(now_utc())
    window = compute_window(cfg, state.current_watermark, args.full_refresh)
    log.info(
        "Window: lower=%s upper=%s full_refresh=%s",
        window.lower, window.upper, window.full_refresh,
    )

    if window.is_unchanged():
        log.info("Window has no new data; nothing to do")
        metrics.emit("NoOpRuns", 1)
        return 0

    # ---- ACQUIRE LOCK ---- #
    try:
        state_mgr.acquire(run_id, window.lower, window.upper)
    except exceptions.ConcurrentRunError as exc:
        log.warning("Could not acquire lock: %s", exc)
        metrics.emit("ConcurrentRunSkips", 1)
        return 0

    try:
        @retry.retry(max_attempts=3, base_delay_s=2.0)
        def extract_with_retry():
            return extract_jdbc(spark, cfg, creds, window)

        df = extract_with_retry()
        df_with_meta = add_metadata(df, run_id, cfg.source_table, committed_at)

        state_mgr.heartbeat(run_id)

        @retry.retry(max_attempts=3, base_delay_s=1.0)
        def write_with_retry():
            return writer.write_iceberg(
                spark=spark,
                df=df_with_meta,
                table_fqn=target_fqn,
                cfg=cfg,
                run_id=run_id,
                watermark_lower=window.lower,
                watermark_upper=window.upper,
                committed_at=committed_at,
            )

        result = write_with_retry()

        new_wm = (
            compute_new_watermark(df_with_meta, cfg, window.upper)
            if result.rows_written > 0 and not args.full_refresh
            else window.upper
        )

        state_mgr.complete(run_id, new_wm, result.rows_written)

        duration = time.time() - started
        metrics.emit("RowsWritten", result.rows_written)
        metrics.emit("DurationSeconds", duration, unit="Seconds")
        if result.was_idempotent_skip:
            metrics.emit("IdempotentSkips", 1)

        log_event(
            "run_completed",
            run_id=run_id,
            rows_written=result.rows_written,
            new_watermark=new_wm,
            duration_s=round(duration, 2),
            idempotent_skip=result.was_idempotent_skip,
        )
        return 0

    except exceptions.SchemaDriftError as exc:
        log.error("Schema drift: %s", exc)
        metrics.emit("SchemaDriftAlerts", 1)
        state_mgr.fail(run_id, f"SchemaDriftError: {exc}")
        return 3

    except exceptions.StateConsistencyError as exc:
        log.error("State inconsistency: %s", exc)
        metrics.emit("StateInconsistencyAlerts", 1)
        return 4

    except (exceptions.TransientError, exceptions.PermanentError) as exc:
        log.error("Pipeline failed: %s", exc)
        log.debug(traceback.format_exc())
        metrics.emit("Failures", 1)
        state_mgr.fail(run_id, f"{type(exc).__name__}: {exc}")
        return 1

    except Exception as exc:  # noqa: BLE001
        log.exception("Unexpected failure")
        metrics.emit("Failures", 1)
        state_mgr.fail(run_id, f"Unexpected: {type(exc).__name__}: {exc}")
        return 1

    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
