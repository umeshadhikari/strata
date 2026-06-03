"""
Configuration loading and validation.

Reads tables.yaml from S3 and validates the entry for the requested table.
Fails fast (PermanentError) if config is malformed.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import boto3
import yaml
from botocore.config import Config

from .exceptions import ConfigError

log = logging.getLogger(__name__)

_BOTO_RETRY = Config(retries={"max_attempts": 10, "mode": "adaptive"})


@dataclass
class PartitionTransform:
    """An Iceberg partition transform: identity, days, months, years, bucket, truncate."""

    transform: str
    column: str
    n: int | None = None  # for bucket / truncate


@dataclass
class ParallelExtract:
    """JDBC parallel-read configuration for Spark."""

    column: str
    lower_bound: int
    upper_bound: int
    num_partitions: int = 4


@dataclass
class TableConfig:
    """Validated per-table configuration loaded from tables.yaml."""

    table_name: str
    source_table: str
    source_schema: str
    domain: str
    watermark_column: str | None
    primary_key: list[str]
    write_mode: str = "append"
    partition_spec: list[PartitionTransform] = field(default_factory=list)
    sort_order: list[str] = field(default_factory=list)
    parallel_extract: ParallelExtract | None = None
    fetch_size: int = 10_000
    full_refresh_on_first_run: bool = True
    target_database: str = ""

    def __post_init__(self):
        valid_modes = {"append", "overwrite", "merge"}
        if self.write_mode not in valid_modes:
            raise ConfigError(
                f"Table {self.table_name}: write_mode={self.write_mode} not in {valid_modes}"
            )
        if self.write_mode == "merge" and not self.primary_key:
            raise ConfigError(
                f"Table {self.table_name}: write_mode=merge requires primary_key"
            )
        if not self.watermark_column and self.write_mode == "append":
            log.warning(
                "Table %s: no watermark_column with append mode — "
                "every run will full-scan source",
                self.table_name,
            )
        # Auto-derive target_database from domain if not explicitly set
        if not self.target_database:
            self.target_database = f"silver_{self.domain}"


def _parse_partition_spec(raw: list[dict[str, Any]] | None) -> list[PartitionTransform]:
    """Validate and parse the YAML `partition_spec` list.

    Each item must specify `transform` (one of identity/days/months/
    years/bucket/truncate) and `column`. `bucket` and `truncate` also
    require `n`. Invalid input raises ConfigError so failures happen at
    config-load time rather than during a Spark write.
    """
    if not raw:
        return []
    out: list[PartitionTransform] = []
    valid = {"identity", "days", "months", "years", "bucket", "truncate"}
    for p in raw:
        t = p.get("transform")
        if t not in valid:
            raise ConfigError(f"Unknown partition transform '{t}'. Valid: {valid}")
        if t in {"bucket", "truncate"} and "n" not in p:
            raise ConfigError(f"Partition transform '{t}' requires 'n'")
        out.append(PartitionTransform(transform=t, column=p["column"], n=p.get("n")))
    return out


def _parse_parallel(raw: dict[str, Any] | None) -> ParallelExtract | None:
    """Validate and parse the optional `parallel_extract` YAML block.

    Returns None when absent (sequential read). When present, requires
    `column`, `lower_bound`, `upper_bound`; `num_partitions` defaults to 4.
    """
    if not raw:
        return None
    return ParallelExtract(
        column=raw["column"],
        lower_bound=int(raw["lower_bound"]),
        upper_bound=int(raw["upper_bound"]),
        num_partitions=int(raw.get("num_partitions", 4)),
    )


def load_config(config_s3_uri: str, table_name: str) -> TableConfig:
    """Load tables.yaml from S3 and return the validated entry for table_name."""
    if not config_s3_uri.startswith("s3://"):
        raise ConfigError(f"CONFIG_S3_URI must start with s3://, got: {config_s3_uri}")

    bucket, _, key = config_s3_uri[len("s3://") :].partition("/")
    if not bucket or not key:
        raise ConfigError(f"Malformed CONFIG_S3_URI: {config_s3_uri}")

    s3 = boto3.client("s3", config=_BOTO_RETRY)
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as exc:
        raise ConfigError(f"Cannot read config from {config_s3_uri}: {exc}") from exc

    try:
        all_cfg = yaml.safe_load(body) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_s3_uri}: {exc}") from exc

    defaults = all_cfg.get("defaults", {})
    tables = all_cfg.get("tables", {})

    if table_name not in tables:
        available = list(tables.keys())
        raise ConfigError(
            f"Table '{table_name}' not in {config_s3_uri}. Available: {available}"
        )

    entry = {**defaults, **tables[table_name]}
    domain = entry.get("domain", "core")

    return TableConfig(
        table_name=table_name,
        source_table=entry.get("source_table", table_name),
        source_schema=entry.get("source_schema", ""),
        domain=domain,
        watermark_column=entry.get("watermark_column"),
        primary_key=entry.get("primary_key") or [],
        write_mode=entry.get("write_mode", "append"),
        partition_spec=_parse_partition_spec(entry.get("partition_spec")),
        sort_order=entry.get("sort_order") or [],
        parallel_extract=_parse_parallel(entry.get("parallel_extract")),
        fetch_size=int(entry.get("fetch_size", 10_000)),
        full_refresh_on_first_run=bool(entry.get("full_refresh_on_first_run", True)),
        target_database=entry.get("target_database", f"silver_{domain}"),
    )


def get_db_credentials(secret_name: str) -> dict[str, Any]:
    """Fetch JDBC credentials from Secrets Manager."""
    sm = boto3.client("secretsmanager", config=_BOTO_RETRY)
    try:
        raw = sm.get_secret_value(SecretId=secret_name)["SecretString"]
    except Exception as exc:
        raise ConfigError(f"Cannot read secret {secret_name}: {exc}") from exc

    creds = json.loads(raw)
    required = {"host", "port", "username", "password"}
    missing = required - set(creds.keys())
    if missing:
        raise ConfigError(f"Secret {secret_name} missing fields: {missing}")
    return creds
