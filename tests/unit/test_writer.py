"""Tests for the schema-compatibility logic in strata.writer.

We test the pure helper functions (`_is_type_compatible`, `_render_columns`,
`_render_partition_clause`) without spinning up Spark.
"""

import pytest

from strata.config import PartitionTransform
from strata.writer import (
    _SAFE_WIDENINGS,
    _SPARK_TO_ICEBERG_TYPE_MAP,
    _is_type_compatible,
    _render_partition_clause,
)


class TestIsTypeCompatible:
    def test_identical_types_compatible(self):
        for t in ("int", "bigint", "string", "double", "smallint"):
            assert _is_type_compatible(t, t)

    @pytest.mark.parametrize(
        "incoming,existing",
        sorted(_SAFE_WIDENINGS),
    )
    def test_safe_widenings_compatible(self, incoming, existing):
        assert _is_type_compatible(incoming, existing)

    def test_smallint_into_int_is_safe(self):
        # The exact case from the FACT/DIM_ACCOUNT failure
        assert _is_type_compatible("smallint", "int")

    @pytest.mark.parametrize(
        "incoming,existing",
        [
            ("int", "smallint"),       # narrowing
            ("bigint", "int"),         # narrowing
            ("double", "float"),       # narrowing
            ("string", "int"),         # unrelated types
            ("int", "string"),         # unrelated types
            ("date", "timestamp"),     # different temporal types
        ],
    )
    def test_narrowing_or_unrelated_incompatible(self, incoming, existing):
        assert not _is_type_compatible(incoming, existing)

    def test_decimal_precision_widening_compatible(self):
        assert _is_type_compatible("decimal(10,2)", "decimal(18,2)")
        assert _is_type_compatible("decimal(5,2)", "decimal(10,2)")

    def test_decimal_precision_narrowing_incompatible(self):
        assert not _is_type_compatible("decimal(18,2)", "decimal(10,2)")

    def test_decimal_scale_mismatch_incompatible(self):
        # Same precision but different scale changes meaning of digits
        assert not _is_type_compatible("decimal(10,2)", "decimal(10,4)")

    def test_decimal_malformed_falls_back_to_false(self):
        assert not _is_type_compatible("decimal(garbage)", "decimal(10,2)")


class TestSparkToIcebergTypeMap:
    def test_smallint_promotes_to_int(self):
        assert _SPARK_TO_ICEBERG_TYPE_MAP["smallint"] == "int"

    def test_tinyint_promotes_to_int(self):
        assert _SPARK_TO_ICEBERG_TYPE_MAP["tinyint"] == "int"

    def test_native_types_not_remapped(self):
        for t in ("int", "bigint", "string", "double", "decimal(18,2)"):
            assert t not in _SPARK_TO_ICEBERG_TYPE_MAP


class TestRenderPartitionClause:
    def test_empty_spec_returns_empty_string(self):
        assert _render_partition_clause([]) == ""

    def test_identity_transform(self):
        spec = [PartitionTransform(transform="identity", column="region")]
        assert _render_partition_clause(spec) == "PARTITIONED BY (region)"

    def test_days_transform(self):
        spec = [PartitionTransform(transform="days", column="value_date")]
        assert _render_partition_clause(spec) == "PARTITIONED BY (days(value_date))"

    def test_bucket_transform(self):
        spec = [PartitionTransform(transform="bucket", column="data_owner_id", n=16)]
        assert _render_partition_clause(spec) == "PARTITIONED BY (bucket(16, data_owner_id))"

    def test_truncate_transform(self):
        spec = [PartitionTransform(transform="truncate", column="account_code", n=4)]
        assert _render_partition_clause(spec) == "PARTITIONED BY (truncate(4, account_code))"

    def test_composite_spec(self):
        spec = [
            PartitionTransform(transform="days", column="value_date"),
            PartitionTransform(transform="bucket", column="data_owner_id", n=16),
        ]
        assert (
            _render_partition_clause(spec)
            == "PARTITIONED BY (days(value_date), bucket(16, data_owner_id))"
        )

    def test_unknown_transform_raises(self):
        spec = [PartitionTransform(transform="tessellate", column="x", n=1)]
        with pytest.raises(ValueError, match="Unknown partition transform"):
            _render_partition_clause(spec)
