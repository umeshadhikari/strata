"""
strata.ingest — Parameterised Glue Job Orchestrator.

Pipeline:
    1. Parse args + load config + validate.
    2. Build Spark with Iceberg + Glue catalog.
    3. RECONCILE state: cross-reference DynamoDB with Iceberg snapshots.
       If they disagree, Iceberg wins.
    4. ACQUIRE lock: claim the table for this run via DynamoDB conditional update.
    5. EXTRACT: JDBC query bounded by (lower, upper] watermark window.
    6. WRITE: idempotent Iceberg commit tagged with run_id.
    7. COMMIT state: advance DynamoDB watermark, release lock.
    8. On any failure mid-pipeline: release the lock so retries can proceed.

Run via::

    aws glue start-job-run \\
      --job-name strata-ingest \\
      --arguments '{
        "--TABLE_NAME": "FACT_PAYMENT",
        "--FULL_REFRESH": "false"
      }'
"""

import logging
import sys
import time
import traceback

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext

from .config import TableConfig, get_db_credentials, load_config
from .exceptions import (
    ConcurrentRunError,
    ConfigError,
    PermanentError,
    SchemaDriftError,
    StateConsistencyError,
    TransientError,
)
from .extract import ExtractWindow, extract_jdbc
from .metrics import Metrics, log_event
from .recovery import reconcile_state
from .retry import retry
from .state import StateManager, iso, now_utc
from .writer import write_iceberg

# --------------------------------------------------------------------------- #
# Logging setup
# --------------------------------------------------------------------------- #
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("strata.ingest")
logging.getLogger("py4j").setLevel(logging.WARN)
logging.getLogger("botocore").setLevel(logging.WARN)


# --------------------------------------------------------------------------- #
# Arg resolution
# --------------------------------------------------------------------------- #
REQUIRED = ["JOB_NAME", "TABLE_NAME", "FULL_REFRESH"]
OPTIONAL_DEFAULTS = {
    "CONFIG_S3_URI": "s3://strata-config/tables.yaml",
    "LAKE_S3_URI": "s3://strata-lake/silver/",
    "SECRET_NAME": "strata/data-mart/credentials",
    "WATERMARK_TABLE": "strata-watermarks",
    "CUSTOMER_ID": "default",
}


def resolve_args() -> dict:
    """Resolve Glue job arguments, applying defaults for the optional ones.

    Glue's `getResolvedOptions` raises if an "optional" arg isn't passed,
    so we check `sys.argv` first for each optional key and only call it
    when present. Returns a flat dict ready for use.
    """
    args = getResolvedOptions(sys.argv, REQUIRED)
    for k, v in OPTIONAL_DEFAULTS.items():
        if f"--{k}" in sys.argv:
            args[k] = getResolvedOptions(sys.argv, [k])[k]
        else:
            args[k] = v
    args["FULL_REFRESH"] = str(args["FULL_REFRESH"]).lower() == "true"
    return args


# --------------------------------------------------------------------------- #
# Spark + Iceberg session
# --------------------------------------------------------------------------- #
def build_spark(lake_s3_uri: str):
    """Construct the Glue Spark session with Iceberg + Glue Catalog wired in.

    Sets the warehouse to S3, registers GlueCatalog as the metadata layer,
    and turns on adaptive query execution. Returns the (SparkContext,
    GlueContext, SparkSession) triple — Glue jobs traditionally need all
    three for different APIs.
    """
    sc = SparkContext.getOrCreate()
    sc.setLogLevel("WARN")

    conf = {
        "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        "spark.sql.catalog.glue_catalog": "org.apache.iceberg.spark.SparkCatalog",
        "spark.sql.catalog.glue_catalog.warehouse": lake_s3_uri,
        "spark.sql.catalog.glue_catalog.catalog-impl": "org.apache.iceberg.aws.glue.GlueCatalog",
        "spark.sql.catalog.glue_catalog.io-impl": "org.apache.iceberg.aws.s3.S3FileIO",
        "spark.sql.defaultCatalog": "glue_catalog",
        "spark.sql.adaptive.enabled": "true",
        "spark.sql.adaptive.coalescePartitions.enabled": "true",
    }
    for k, v in conf.items():
        sc._jsc.hadoopConfiguration().set(k, v)

    glue = GlueContext(sc)
    spark = glue.spark_session
    for k, v in conf.items():
        spark.conf.set(k, v)
    return sc, glue, spark


# --------------------------------------------------------------------------- #
# Metadata column enrichment
# --------------------------------------------------------------------------- #
def add_metadata(df, run_id: str, source_table: str, committed_at: str):
    """Attach the four strata lineage columns to a freshly-extracted DataFrame.

    The four columns — `_ingest_run_id`, `_ingest_timestamp`,
    `_source_table`, `_ingest_date` — let downstream consumers dedupe
    by latest ingest, trace each row back to a specific Glue run, and
    partition by ingest date. Match the corresponding logic in
    `strata.local_ingest.add_metadata` exactly.
    """
    from pyspark.sql import functions as F

    return (
        df.withColumn("_ingest_run_id", F.lit(run_id))
        .withColumn("_ingest_timestamp", F.lit(committed_at).cast("timestamp"))
        .withColumn("_source_table", F.lit(source_table))
        .withColumn("_ingest_date", F.current_date())
    )


# --------------------------------------------------------------------------- #
# Window computation
# --------------------------------------------------------------------------- #
def compute_window(
    cfg: TableConfig,
    current_watermark: str | None,
    full_refresh: bool,
) -> ExtractWindow:
    """
    Bound the source query by (lower, upper] timestamps captured at run start.

    The upper bound is set to *now* at the moment the run starts. This is
    important: on retry, the same upper bound is used (because run_id and
    state are deterministic), so the source query is repeatable.

    Lower bound:
      * For full_refresh: None (extract everything ≤ upper)
      * For first run on this table: None
      * Otherwise: current_watermark from state
    """
    upper = iso(now_utc())
    if full_refresh:
        return ExtractWindow(lower=None, upper=upper, full_refresh=True)
    return ExtractWindow(lower=current_watermark, upper=upper, full_refresh=False)


def compute_new_watermark(df, cfg: TableConfig, fallback_upper: str) -> str:
    """
    Watermark to commit. Prefer MAX(watermark_column) over rows actually written;
    fall back to the query upper bound if the column isn't in the DataFrame or
    no rows were ingested.
    """
    from pyspark.sql import functions as F

    if not cfg.watermark_column:
        return fallback_upper

    wm_col_lower = cfg.watermark_column.lower()
    df_cols_lower = [c.lower() for c in df.columns]
    if wm_col_lower not in df_cols_lower:
        log.warning(
            "Watermark column %s not in DataFrame; using window upper bound",
            cfg.watermark_column,
        )
        return fallback_upper

    actual_col = df.columns[df_cols_lower.index(wm_col_lower)]
    row = df.agg(F.max(F.col(actual_col)).cast("string").alias("m")).first()
    if row is None or row["m"] is None:
        return fallback_upper
    return row["m"]


# --------------------------------------------------------------------------- #
# Main pipeline
# --------------------------------------------------------------------------- #
def main():
    """Glue job entry point. Runs one logical ingest end-to-end.

    The pipeline, in order:
      1. Parse + validate args.
      2. Build Spark session with GlueCatalog + Iceberg.
      3. Reconcile DynamoDB ↔ Iceberg state (five-case recovery in
         `strata.recovery`). Recovery may force-advance the watermark
         to match an orphan Iceberg snapshot — see Case C there.
      4. Compute the bounded window `(current_watermark, now()]`
         (or `(None, now()]` for full-refresh).
      5. Acquire the lock in DynamoDB via conditional update.
      6. Extract with retry (bounded by the window).
      7. Add lineage columns (`_ingest_run_id` etc.).
      8. Heartbeat the lock so long extracts don't expire it.
      9. Write to Iceberg with idempotency check on `glue.run_id` snapshot
         property — same run_id won't double-commit.
      10. Compute the new watermark from MAX(watermark_column) in the
          actually-written batch.
      11. State.complete(): advance watermark, release lock, conditional
          on `pending_run_id = my run_id`.

    Exit codes (also see strata.local_ingest.main for parity):
      0 — success or no-op,
      1 — pipeline failure (transient or permanent),
      2 — config error,
      3 — schema drift (operator intervention required),
      4 — state inconsistency (recovery couldn't reconcile).
    """
    started_at = time.time()
    args = resolve_args()
    safe_args = {k: v for k, v in args.items() if "password" not in k.lower()}
    log.info("Resolved args: %s", safe_args)

    job_name = args["JOB_NAME"]
    table_name = args["TABLE_NAME"]
    full_refresh = args["FULL_REFRESH"]

    # Use Glue's JOB_RUN_ID for run_id so retries inherit the same id.
    # This is the linchpin of idempotency.
    glue_run_id = (
        args.get("JOB_RUN_ID") or f"manual-{now_utc().strftime('%Y%m%dT%H%M%SZ')}"
    )
    run_id = f"{job_name}::{table_name}::{glue_run_id}"

    log_event("run_started", run_id=run_id, table=table_name, full_refresh=full_refresh)

    # ---- Config ---- #
    try:
        cfg = load_config(args["CONFIG_S3_URI"], table_name)
        creds = get_db_credentials(args["SECRET_NAME"])
    except ConfigError as exc:
        log_event("config_error", error=str(exc))
        raise

    # ---- Spark ---- #
    sc, glue, spark = build_spark(args["LAKE_S3_URI"])
    job = Job(glue)
    job.init(job_name, args)

    target_fqn = f"glue_catalog.{cfg.target_database}.{table_name.lower()}"
    log.info("Target table: %s", target_fqn)

    metrics = Metrics(table_name=table_name, customer_id=args["CUSTOMER_ID"])
    state_mgr = StateManager(args["WATERMARK_TABLE"], table_name)

    # ---- RECONCILE ---- #
    try:
        state = reconcile_state(spark, state_mgr, target_fqn)
        log_event(
            "state_reconciled",
            current_watermark=state.current_watermark,
            pending=state.pending_run_id,
        )
    except ConcurrentRunError as exc:
        log_event("concurrent_run_detected", message=str(exc))
        metrics.emit("ConcurrentRunSkips", 1)
        log.warning("Exiting cleanly; another run holds the lock")
        job.commit()
        return

    # ---- WINDOW ---- #
    committed_at = iso(now_utc())
    window = compute_window(cfg, state.current_watermark, full_refresh)
    log.info(
        "Extract window: lower=%s upper=%s full_refresh=%s",
        window.lower, window.upper, window.full_refresh,
    )

    if window.is_unchanged():
        log.info("Window has no new data since last run; nothing to do")
        log_event("nothing_to_do", lower=window.lower, upper=window.upper)
        metrics.emit("NoOpRuns", 1)
        job.commit()
        return

    # ---- ACQUIRE LOCK ---- #
    try:
        state_mgr.acquire(run_id, window.lower, window.upper)
    except ConcurrentRunError as exc:
        log_event("concurrent_acquire_failed", message=str(exc))
        metrics.emit("ConcurrentRunSkips", 1)
        log.warning("Could not acquire lock; another run got there first")
        job.commit()
        return

    try:
        # ---- EXTRACT with retry on transient errors ---- #
        @retry(max_attempts=3, base_delay_s=5.0)
        def extract_with_retry():
            return extract_jdbc(spark, cfg, creds, window)

        df = extract_with_retry()
        df_with_meta = add_metadata(df, run_id, cfg.source_table, committed_at)

        state_mgr.heartbeat(run_id)

        # ---- WRITE with retry on commit conflicts ---- #
        @retry(max_attempts=3, base_delay_s=2.0)
        def write_with_retry():
            return write_iceberg(
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

        if result.rows_written > 0 and not full_refresh:
            new_wm = compute_new_watermark(df_with_meta, cfg, window.upper)
        else:
            new_wm = window.upper

        @retry(max_attempts=5, base_delay_s=1.0)
        def commit_state():
            state_mgr.complete(run_id, new_wm, result.rows_written)

        commit_state()

        duration = time.time() - started_at
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

    except SchemaDriftError as exc:
        log_event("schema_drift", error=str(exc))
        metrics.emit("SchemaDriftAlerts", 1)
        state_mgr.fail(run_id, f"SchemaDriftError: {exc}")
        raise

    except StateConsistencyError as exc:
        log_event("state_inconsistent", error=str(exc))
        metrics.emit("StateInconsistencyAlerts", 1)
        raise

    except PermanentError as exc:
        log_event("permanent_failure", error=str(exc), trace=traceback.format_exc())
        metrics.emit("Failures", 1)
        state_mgr.fail(run_id, f"{type(exc).__name__}: {exc}")
        raise

    except TransientError as exc:
        log_event(
            "transient_failure_exhausted",
            error=str(exc), trace=traceback.format_exc(),
        )
        metrics.emit("Failures", 1)
        state_mgr.fail(run_id, f"{type(exc).__name__}: {exc}")
        raise

    except Exception as exc:  # noqa: BLE001
        log_event("unexpected_failure", error=str(exc), trace=traceback.format_exc())
        metrics.emit("Failures", 1)
        state_mgr.fail(run_id, f"Unexpected: {type(exc).__name__}: {exc}")
        raise

    finally:
        job.commit()


if __name__ == "__main__":
    main()
