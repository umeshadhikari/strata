# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Local development stack: Docker Compose with PostgreSQL (source + Iceberg
  JDBC catalog backend), Spark (PySpark runtime), Trino (Athena substitute),
  and Apache Superset (QuickSight substitute).
- `strata.local_ingest` entry point that delegates to the same recovery,
  writer, and extract code paths as `strata.ingest`.
- `LocalStateManager` (SQLite) implementing the same interface as `StateManager`
  so the snapshot-based recovery works unchanged.
- File-based config loader and credentials loader (substitutes for S3 and
  Secrets Manager).
- `LocalMetrics` (stdout) substitute for CloudWatch.
- Spark session builder using Iceberg JDBC catalog so Spark and Trino share
  metadata.
- Bootstrap script (`local/postgres/bootstrap.py`) that populates dims and
  generates synthetic facts in one command.
- Convenience scripts: `setup.sh`, `ingest.sh`, `run-all.sh`, `trino.sh`.
- Trino + Superset Docker configurations.
- Sample Trino queries (`local/queries/sample.sql`).
- Documentation: `docs/local-runtime.md` (laptop end-to-end walkthrough) and
  `docs/aws-runtime.md` (AWS end-to-end walkthrough).

## [0.1.0] - 2026-06-01

### Added

- Initial release.
- Parameterised AWS Glue PySpark job that ingests RDBMS tables into Apache Iceberg.
- DynamoDB-backed state machine with snapshot-based recovery.
- Idempotent Iceberg commits tagged with `glue.run_id` in snapshot properties.
- Five-case recovery logic that reconciles DynamoDB cache with Iceberg history.
- Bounded watermark windows: `(lower, upper]` captured once per run.
- Schema-evolution detection: additive changes auto-apply; breaking changes fail loudly.
- Iceberg partition spec from YAML with `days`, `months`, `years`, `bucket`, `truncate` transforms.
- Optional sort order within partitions.
- Parallel JDBC extracts via Spark `partitionColumn`/`lowerBound`/`upperBound`/`numPartitions`.
- CloudWatch metrics emitted per table: `RowsWritten`, `DurationSeconds`, `Failures`, `SchemaDriftAlerts`, `StateInconsistencyAlerts`, `ConcurrentRunSkips`, `IdempotentSkips`.
- Terraform module: S3, KMS, Glue catalog, Glue job, Glue connection, DynamoDB watermarks, Secrets Manager, EventBridge schedule, CloudWatch alarms.
- Example `tables.yaml` covering 19 tables from a payment-domain data mart.
- Documentation: architecture, reliability, partitioning, raw-Parquet variant, operational runbook.

[Unreleased]: https://github.com/your-org/strata/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/your-org/strata/releases/tag/v0.1.0
