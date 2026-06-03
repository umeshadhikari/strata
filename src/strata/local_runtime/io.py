"""
Local substitutes for S3-backed config loading and Secrets Manager.

Both delegate to plain files on disk. Same return shape as the AWS versions
so the rest of the pipeline doesn't know the difference.
"""

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from ..config import (
    TableConfig,
    _parse_parallel,
    _parse_partition_spec,
)
from ..exceptions import ConfigError

log = logging.getLogger(__name__)


def load_local_config(path: str, table_name: str) -> TableConfig:
    """Load tables.yaml from a local path and return the entry for table_name."""
    p = Path(path).expanduser()
    if not p.exists():
        raise ConfigError(f"Config file not found: {p}")
    try:
        all_cfg = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {p}: {exc}") from exc

    defaults = all_cfg.get("defaults", {})
    tables = all_cfg.get("tables", {})
    if table_name not in tables:
        raise ConfigError(
            f"Table '{table_name}' not in {p}. Available: {list(tables.keys())}"
        )

    entry: dict[str, Any] = {**defaults, **tables[table_name]}
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


def load_local_credentials(path: str) -> dict[str, Any]:
    """Load JDBC credentials from a JSON file."""
    p = Path(path).expanduser()
    if not p.exists():
        raise ConfigError(
            f"Local secrets file not found: {p}. "
            f"Copy local/secrets/db.local.json.example to that path."
        )
    creds = json.loads(p.read_text())
    required = {"host", "port", "username", "password"}
    missing = required - set(creds.keys())
    if missing:
        raise ConfigError(f"Secrets file {p} missing fields: {missing}")
    return creds
