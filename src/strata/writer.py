"""
Idempotent Iceberg writer.

Every commit carries snapshot properties that uniquely identify the run:
    glue.run_id              — unique per attempted run
    glue.watermark_lower     — exclusive lower bound of source window
    glue.watermark_upper     — inclusive upper bound of source window
    glue.row_count           — rows in this snapshot's contribution
    glue.committed_at        — ISO timestamp

Idempotency property:
  Before writing, we scan the snapshot history for any snapshot whose
  glue.run_id matches the current run. If found, the write already happened
  (from a previous attempt that crashed before clearing DynamoDB state). We
  skip the write and proceed to state advancement.

Schema evolution:
  Iceberg supports additive schema changes (new columns) automatically.
  Type changes, renames, and removals raise SchemaDriftError so an operator
  can decide whether to ALTER the table.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # pyspark is only needed when the module actually does Spark work.
    # Keeping it lazy lets the pure helpers (`_is_type_compatible`,
    # `_render_partition_clause`, etc) be imported and unit-tested without
    # a Spark runtime installed.
    from pyspark.sql import DataFrame, SparkSession
    from pyspark.sql.types import StructType

from .config import PartitionTransform, TableConfig
from .exceptions import SchemaDriftError, WriteCommitError

log = logging.getLogger(__name__)


@dataclass
class WriteResult:
    rows_written: int
    snapshot_id: int | None
    was_idempotent_skip: bool = False


# --------------------------------------------------------------------------- #
# Schema handling
# --------------------------------------------------------------------------- #

# Spark types Iceberg doesn't natively have — promote on table creation so
# behavior is consistent across writes.
_SPARK_TO_ICEBERG_TYPE_MAP = {
    "tinyint": "int",
    "smallint": "int",
}


# Type pairs (incoming, existing) that are safe to write without an error.
# All "incoming X into existing Y" combinations where X fits into Y.
_SAFE_WIDENINGS: set[tuple[str, str]] = {
    # Integer widening
    ("tinyint", "smallint"),
    ("tinyint", "int"),
    ("tinyint", "bigint"),
    ("smallint", "int"),
    ("smallint", "bigint"),
    ("int", "bigint"),
    # Floating-point widening
    ("float", "double"),
}


def _is_type_compatible(incoming: str, existing: str) -> bool:
    """
    True if the incoming column type can be safely inserted into the existing
    column type without data loss.

    Handles exact match, integer widening, float widening, and decimal
    precision widening.
    """
    if incoming == existing:
        return True
    if (incoming, existing) in _SAFE_WIDENINGS:
        return True
    # decimal(p1, s) → decimal(p2, s) where p2 >= p1
    if incoming.startswith("decimal(") and existing.startswith("decimal("):
        try:
            inc_p, inc_s = (int(x) for x in incoming[len("decimal(") : -1].split(","))
            ex_p, ex_s = (int(x) for x in existing[len("decimal(") : -1].split(","))
            return inc_s == ex_s and inc_p <= ex_p
        except (ValueError, IndexError):
            pass
    return False


def _check_schema_evolution(spark: SparkSession, table_fqn: str, df: DataFrame) -> None:
    """
    Compare incoming DF schema against existing table schema.
      * New columns → allowed (Iceberg auto-evolves on write).
      * Dropped columns → allowed if the operator pre-NULLs them upstream.
      * Type widening (smallint→int, int→bigint, float→double, etc) → allowed.
      * Type narrowing or incompatible change → fail with SchemaDriftError.
    """
    if not spark.catalog.tableExists(table_fqn):
        return

    existing: StructType = spark.table(table_fqn).schema
    incoming: StructType = df.schema

    existing_fields = {f.name.lower(): f for f in existing.fields}
    incoming_fields = {f.name.lower(): f for f in incoming.fields}

    incompatible: list[str] = []
    for name, inc_field in incoming_fields.items():
        if name not in existing_fields:
            log.info("Schema evolution: new column %s (%s)", name, inc_field.dataType)
            continue
        exist_field = existing_fields[name]
        inc_type = inc_field.dataType.simpleString()
        exist_type = exist_field.dataType.simpleString()
        if not _is_type_compatible(inc_type, exist_type):
            incompatible.append(
                f"{name}: incoming {inc_type} vs existing {exist_type}"
            )
        elif inc_type != exist_type:
            log.info(
                "Schema evolution: safe widening %s: %s → %s",
                name, inc_type, exist_type,
            )

    if incompatible:
        raise SchemaDriftError(
            f"Schema drift in {table_fqn}:\n  - "
            + "\n  - ".join(incompatible)
            + "\n\nOperator action required: ALTER TABLE to align, or drop+recreate."
        )


# --------------------------------------------------------------------------- #
# Idempotency check via Iceberg snapshot history
# --------------------------------------------------------------------------- #
def find_snapshot_by_run_id(
    spark: SparkSession, table_fqn: str, run_id: str
) -> dict[str, Any] | None:
    """Return snapshot summary if a previous attempt of this run_id committed."""
    if not spark.catalog.tableExists(table_fqn):
        return None
    snapshots = spark.sql(
        f"SELECT snapshot_id, committed_at, summary FROM {table_fqn}.snapshots "
        f"ORDER BY committed_at DESC LIMIT 100"
    ).collect()
    for snap in snapshots:
        summary = snap["summary"] or {}
        if summary.get("glue.run_id") == run_id:
            return {
                "snapshot_id": snap["snapshot_id"],
                "committed_at": snap["committed_at"],
                "summary": summary,
            }
    return None


def latest_snapshot_watermark(
    spark: SparkSession, table_fqn: str
) -> tuple[str, str] | None:
    """
    Find the latest committed snapshot's watermark range.
    Returns (run_id, watermark_upper) or None if no such snapshot exists.
    """
    if not spark.catalog.tableExists(table_fqn):
        return None
    snapshots = spark.sql(
        f"SELECT summary FROM {table_fqn}.snapshots "
        f"WHERE operation IN ('append', 'overwrite') "
        f"ORDER BY committed_at DESC LIMIT 10"
    ).collect()
    for snap in snapshots:
        summary = snap["summary"] or {}
        wm = summary.get("glue.watermark_upper")
        if wm:
            return summary.get("glue.run_id", "unknown"), wm
    return None


# --------------------------------------------------------------------------- #
# Table creation with partition spec
# --------------------------------------------------------------------------- #
def _render_partition_clause(spec: list[PartitionTransform]) -> str:
    """Render the Iceberg partition spec as SQL DDL: 'days(col), bucket(N, col)'.

    Returns "" if the spec is empty.
    """
    if not spec:
        return ""
    parts: list[str] = []
    for p in spec:
        if p.transform == "identity":
            parts.append(p.column)
        elif p.transform in ("days", "months", "years"):
            parts.append(f"{p.transform}({p.column})")
        elif p.transform in ("bucket", "truncate"):
            parts.append(f"{p.transform}({p.n}, {p.column})")
        else:
            raise ValueError(f"Unknown partition transform: {p.transform}")
    return "PARTITIONED BY (" + ", ".join(parts) + ")"


def _render_columns(df: DataFrame) -> str:
    """
    Render the DataFrame schema as a SQL column list for CREATE TABLE.

    Promotes types Iceberg doesn't natively support (smallint, tinyint) up to
    `int` so the catalog is consistent regardless of how the JDBC driver
    happens to report column types on different reads.
    """
    cols = []
    for field in df.schema.fields:
        spark_type = field.dataType.simpleString()
        iceberg_type = _SPARK_TO_ICEBERG_TYPE_MAP.get(spark_type, spark_type)
        cols.append(f"`{field.name}` {iceberg_type}")
    return ",\n  ".join(cols)


def ensure_table_exists(
    spark: SparkSession,
    table_fqn: str,
    df: DataFrame,
    cfg: TableConfig,
) -> bool:
    """
    Create the Iceberg table on first run. Returns True if newly created.
    Idempotent: a race between two runs is handled by Iceberg's catalog.

    Uses SQL DDL (not the DataFrame writer) so Iceberg's partition transforms
    (days, bucket, etc.) are interpreted by Iceberg's SQL extensions rather
    than by Spark's standard SQL function registry.
    """
    if spark.catalog.tableExists(table_fqn):
        return False

    log.info("Creating Iceberg table %s", table_fqn)
    columns_sql = _render_columns(df)
    partition_sql = _render_partition_clause(cfg.partition_spec)

    create_sql = f"""
        CREATE TABLE {table_fqn} (
          {columns_sql}
        )
        USING iceberg
        {partition_sql}
        TBLPROPERTIES (
          'format-version' = '2',
          'write.format.default' = 'parquet',
          'write.parquet.compression-codec' = 'zstd',
          'write.metadata.delete-after-commit.enabled' = 'true',
          'write.metadata.previous-versions-max' = '20'
        )
    """

    try:
        spark.sql(create_sql)
    except Exception as exc:
        # Concurrent table creation race — both runs may attempt this.
        # The second one fails but the table now exists; treat as success.
        if "already exists" in str(exc).lower():
            log.info("Table %s already exists (concurrent create)", table_fqn)
            return False
        log.error("CREATE TABLE failed. SQL was:\n%s", create_sql)
        raise

    if cfg.sort_order:
        sort_clause = ", ".join(cfg.sort_order)
        spark.sql(f"ALTER TABLE {table_fqn} WRITE ORDERED BY {sort_clause}")

    return True


# --------------------------------------------------------------------------- #
# The write
# --------------------------------------------------------------------------- #
def write_iceberg(
    spark: SparkSession,
    df: DataFrame,
    table_fqn: str,
    cfg: TableConfig,
    run_id: str,
    watermark_lower: str | None,
    watermark_upper: str,
    committed_at: str,
) -> WriteResult:
    """
    Write `df` to the Iceberg table tagged with `run_id` in snapshot properties.

    Idempotency: if a snapshot already exists with this run_id, skip the write.
    """
    # Idempotency check
    existing = find_snapshot_by_run_id(spark, table_fqn, run_id)
    if existing is not None:
        rc = int(existing["summary"].get("glue.row_count", 0))
        log.warning(
            "Idempotent skip: snapshot %s already exists for run %s (%d rows)",
            existing["snapshot_id"], run_id, rc,
        )
        return WriteResult(
            rows_written=rc,
            snapshot_id=existing["snapshot_id"],
            was_idempotent_skip=True,
        )

    # Schema validation (only if table exists)
    _check_schema_evolution(spark, table_fqn, df)

    # First-time table creation
    ensure_table_exists(spark, table_fqn, df, cfg)

    # Count rows (this triggers the JDBC extract)
    row_count = df.count()
    if row_count == 0:
        log.info("No rows to write for %s in window (%s, %s]",
                 table_fqn, watermark_lower, watermark_upper)
        # Still emit a zero-row snapshot so we can advance the watermark idempotently
        # by tagging an empty append.
        empty_props = {
            "glue.run_id": run_id,
            "glue.watermark_lower": watermark_lower or "",
            "glue.watermark_upper": watermark_upper,
            "glue.row_count": "0",
            "glue.committed_at": committed_at,
        }
        try:
            (
                df.limit(0)
                .writeTo(table_fqn)
                .options(**{f"snapshot-property.{k}": v for k, v in empty_props.items()})
                .append()
            )
        except Exception as exc:
            log.warning("Empty snapshot append failed (continuing): %s", exc)
        return WriteResult(rows_written=0, snapshot_id=None)

    # Real write — tagged with run_id for idempotency
    snapshot_props = {
        "glue.run_id": run_id,
        "glue.watermark_lower": watermark_lower or "",
        "glue.watermark_upper": watermark_upper,
        "glue.row_count": str(row_count),
        "glue.committed_at": committed_at,
    }
    log.info("Writing %d rows to %s (run_id=%s)", row_count, table_fqn, run_id)

    write_mode = cfg.write_mode
    try:
        if write_mode == "overwrite":
            (
                df.writeTo(table_fqn)
                .options(**{f"snapshot-property.{k}": v for k, v in snapshot_props.items()})
                .overwritePartitions()
            )
        elif write_mode == "merge":
            if not cfg.primary_key:
                raise ValueError("merge requires primary_key in config")
            df.createOrReplaceTempView(f"_src_{cfg.table_name.lower()}")
            on_clause = " AND ".join(
                [f"t.{pk} = s.{pk}" for pk in cfg.primary_key]
            )
            spark.sql(f"""
                MERGE INTO {table_fqn} AS t
                USING _src_{cfg.table_name.lower()} AS s
                ON {on_clause}
                WHEN MATCHED THEN UPDATE SET *
                WHEN NOT MATCHED THEN INSERT *
            """)
            # MERGE doesn't take snapshot properties via writeTo; set them after
            spark.sql(f"""
                ALTER TABLE {table_fqn}
                SET TBLPROPERTIES (
                  'glue.last_run_id' = '{run_id}',
                  'glue.last_watermark_upper' = '{watermark_upper}'
                )
            """)
        else:  # append
            (
                df.writeTo(table_fqn)
                .options(**{f"snapshot-property.{k}": v for k, v in snapshot_props.items()})
                .append()
            )
    except Exception as exc:
        msg = str(exc).lower()
        if "commit" in msg or "concurrent" in msg or "conflict" in msg:
            raise WriteCommitError(f"Iceberg commit failed: {exc}") from exc
        raise

    # Verify the commit took effect
    committed = find_snapshot_by_run_id(spark, table_fqn, run_id)
    snap_id = committed["snapshot_id"] if committed else None
    if not committed and write_mode != "merge":
        raise WriteCommitError(
            f"Write to {table_fqn} appeared to succeed but no snapshot with "
            f"run_id={run_id} found. Investigate Iceberg state."
        )

    log.info(
        "Wrote %d rows to %s; snapshot_id=%s, run_id=%s",
        row_count, table_fqn, snap_id, run_id,
    )
    return WriteResult(rows_written=row_count, snapshot_id=snap_id)
