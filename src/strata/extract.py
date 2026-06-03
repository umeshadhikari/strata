"""
JDBC extraction from data mart with retry, timeouts, and parallel reads.

Key properties:
  * The query window is bounded by (lower, upper] timestamps captured
    at run start. Same bounds on retry — no drift.
  * Spark JDBC reads are lazy; the action that triggers them is in
    iceberg writer. Therefore retry logic wraps the write call as well.
  * Connection errors → TransientError (worth retrying).
  * Auth / driver errors → ConfigError (don't retry).
"""

import logging
from dataclasses import dataclass
from typing import Any

from pyspark.sql import DataFrame, SparkSession

from .config import TableConfig
from .exceptions import ConfigError, SourceQueryError, SourceUnreachableError

log = logging.getLogger(__name__)


@dataclass
class ExtractWindow:
    """Bounded time window for an incremental extract."""

    lower: str | None   # exclusive lower bound on watermark column. None = from start of time.
    upper: str          # inclusive upper bound. Captured once per run.
    full_refresh: bool

    def is_unchanged(self) -> bool:
        """Are we re-extracting the same window we just extracted?"""
        return self.lower is not None and self.lower == self.upper


def build_jdbc_url(creds: dict[str, Any]) -> str:
    """Build a JDBC URL from credentials. Supports Oracle and PostgreSQL."""
    engine = creds.get("engine", "oracle").lower()
    if engine == "oracle":
        return f"jdbc:oracle:thin:@//{creds['host']}:{creds['port']}/{creds['service_name']}"
    if engine in ("postgres", "postgresql"):
        return f"jdbc:postgresql://{creds['host']}:{creds['port']}/{creds['database']}"
    raise ConfigError(f"Unsupported engine: {engine}")


def _quote_watermark(value: str, engine: str) -> str:
    """Wrap a watermark value in engine-appropriate SQL literal syntax."""
    if engine == "oracle":
        return f"TIMESTAMP '{value}'"
    if engine in ("postgres", "postgresql"):
        return f"TIMESTAMP '{value}'"
    return f"'{value}'"


def build_extract_query(cfg: TableConfig, window: ExtractWindow, engine: str) -> str:
    """Build the SELECT query with the bounded window predicate."""
    qualified = (
        f"{cfg.source_schema}.{cfg.source_table}"
        if cfg.source_schema
        else cfg.source_table
    )

    if window.full_refresh or not cfg.watermark_column:
        return f"(SELECT * FROM {qualified}) AS extract_query"

    upper = _quote_watermark(window.upper, engine)
    if window.lower is None:
        # First incremental run after table existed — no lower bound
        return (
            f"(SELECT * FROM {qualified} "
            f"WHERE {cfg.watermark_column} <= {upper}) AS extract_query"
        )

    lower = _quote_watermark(window.lower, engine)
    return (
        f"(SELECT * FROM {qualified} "
        f"WHERE {cfg.watermark_column} > {lower} "
        f"AND {cfg.watermark_column} <= {upper}) AS extract_query"
    )


def extract_jdbc(
    spark: SparkSession,
    cfg: TableConfig,
    creds: dict[str, Any],
    window: ExtractWindow,
) -> DataFrame:
    """Build the JDBC DataFrameReader and load the bounded window."""
    engine = creds.get("engine", "oracle").lower()
    jdbc_url = build_jdbc_url(creds)
    query = build_extract_query(cfg, window, engine)
    log.info("Extracting query: %s", query)

    reader = (
        spark.read.format("jdbc")
        .option("url", jdbc_url)
        .option("dbtable", query)
        .option("user", creds["username"])
        .option("password", creds["password"])
        .option("driver", creds.get("driver", "oracle.jdbc.OracleDriver"))
        .option("fetchsize", str(cfg.fetch_size))
        .option("queryTimeout", str(creds.get("query_timeout_seconds", 1800)))
        # Force JDBC connection failures to be loud
        .option("connectionInitFailureRetries", "0")
    )

    if cfg.parallel_extract and not window.full_refresh:
        p = cfg.parallel_extract
        reader = (
            reader.option("partitionColumn", p.column)
            .option("lowerBound", str(p.lower_bound))
            .option("upperBound", str(p.upper_bound))
            .option("numPartitions", str(p.num_partitions))
        )

    try:
        df = reader.load()
    except Exception as exc:
        msg = str(exc).lower()
        if any(s in msg for s in ("authentication", "invalid username", "ora-01017")):
            raise ConfigError(f"Authentication failed: {exc}") from exc
        if any(
            s in msg
            for s in ("connection", "network", "timeout", "no listener", "tns")
        ):
            raise SourceUnreachableError(f"Source unreachable: {exc}") from exc
        raise SourceQueryError(f"JDBC extract failed: {exc}") from exc

    return df
