"""Tests for config parsing and validation."""

import pytest

from strata.config import (
    ParallelExtract,
    PartitionTransform,
    TableConfig,
    _parse_parallel,
    _parse_partition_spec,
)
from strata.exceptions import ConfigError


class TestTableConfig:
    def test_minimal_valid(self):
        cfg = TableConfig(
            table_name="FACT_X",
            source_table="FACT_X",
            source_schema="DM",
            domain="payments",
            watermark_column="LAST_UPDATED",
            primary_key=["X_ID"],
        )
        assert cfg.write_mode == "append"
        assert cfg.target_database == "silver_payments"

    def test_merge_requires_primary_key(self):
        with pytest.raises(ConfigError, match="primary_key"):
            TableConfig(
                table_name="FACT_X",
                source_table="FACT_X",
                source_schema="DM",
                domain="payments",
                watermark_column=None,
                primary_key=[],
                write_mode="merge",
            )

    def test_invalid_write_mode(self):
        with pytest.raises(ConfigError, match="write_mode"):
            TableConfig(
                table_name="FACT_X",
                source_table="FACT_X",
                source_schema="DM",
                domain="payments",
                watermark_column="LAST_UPDATED",
                primary_key=["X_ID"],
                write_mode="upsert_blast",
            )

    def test_custom_target_database(self):
        cfg = TableConfig(
            table_name="FACT_X",
            source_table="FACT_X",
            source_schema="DM",
            domain="payments",
            watermark_column="LAST_UPDATED",
            primary_key=["X_ID"],
            target_database="custom_db",
        )
        assert cfg.target_database == "custom_db"


class TestPartitionSpec:
    def test_empty(self):
        assert _parse_partition_spec(None) == []
        assert _parse_partition_spec([]) == []

    def test_days_transform(self):
        spec = _parse_partition_spec(
            [{"transform": "days", "column": "VALUE_DATE"}]
        )
        assert spec == [
            PartitionTransform(transform="days", column="VALUE_DATE", n=None)
        ]

    def test_bucket_transform(self):
        spec = _parse_partition_spec(
            [{"transform": "bucket", "column": "DATA_OWNER_ID", "n": 16}]
        )
        assert spec == [
            PartitionTransform(transform="bucket", column="DATA_OWNER_ID", n=16)
        ]

    def test_bucket_without_n_raises(self):
        with pytest.raises(ConfigError, match="requires 'n'"):
            _parse_partition_spec(
                [{"transform": "bucket", "column": "DATA_OWNER_ID"}]
            )

    def test_unknown_transform_raises(self):
        with pytest.raises(ConfigError, match="Unknown partition transform"):
            _parse_partition_spec(
                [{"transform": "tessellate", "column": "X"}]
            )

    def test_composite_spec(self):
        spec = _parse_partition_spec(
            [
                {"transform": "days", "column": "VALUE_DATE"},
                {"transform": "bucket", "column": "DATA_OWNER_ID", "n": 16},
            ]
        )
        assert len(spec) == 2
        assert spec[0].transform == "days"
        assert spec[1].n == 16


class TestParallelExtract:
    def test_none(self):
        assert _parse_parallel(None) is None

    def test_minimal(self):
        p = _parse_parallel(
            {"column": "ID", "lower_bound": 1, "upper_bound": 1000000}
        )
        assert p == ParallelExtract(
            column="ID", lower_bound=1, upper_bound=1000000, num_partitions=4
        )

    def test_with_num_partitions(self):
        p = _parse_parallel(
            {
                "column": "ID",
                "lower_bound": 1,
                "upper_bound": 1000000,
                "num_partitions": 16,
            }
        )
        assert p is not None
        assert p.num_partitions == 16
