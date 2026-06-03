# strata

> Idempotent, watermark-driven replication from any RDBMS into Apache Iceberg on Amazon S3.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Glue 4.0](https://img.shields.io/badge/AWS%20Glue-4.0-orange.svg)](https://docs.aws.amazon.com/glue/)
[![Iceberg 1.x](https://img.shields.io/badge/Apache%20Iceberg-1.x-blue.svg)](https://iceberg.apache.org/)

`strata` is a production-grade ingestion framework that copies tables from a relational source (Oracle, PostgreSQL, MySQL) into Apache Iceberg tables on S3, registered in the AWS Glue Data Catalog. One parameterised Glue PySpark job handles every table, driven by a single YAML config.

Built for the "data mart → lake" pattern where the source is already enriched (e.g., a star-schema warehouse) and you want a faithful, queryable copy in open format with no manual partition management, no data loss on failure, and no duplicate rows after retry.

## Highlights

- **One job, every table.** Add a table by editing YAML. No code changes.
- **At-least-once with idempotent commits.** Iceberg snapshots tagged with run IDs; retries are deduplicated automatically.
- **Snapshot-based recovery.** DynamoDB watermark is a cache; Iceberg history is the source of truth. They get reconciled at the start of every run.
- **Bounded watermark windows.** Source queries use `(lower, upper]` predicates captured once per run, so retries produce identical results.
- **Schema evolution.** New source columns add automatically; breaking changes fail loudly.
- **Cloud-native partitioning.** Iceberg hidden partitioning by date and bucket, even when the source has no partitioning.
- **Serverless-first.** Glue PySpark, S3, DynamoDB on-demand, Secrets Manager, CloudWatch. No always-on services.

## Quick start

### Try it locally first (no AWS account needed)

```bash
./local/scripts/setup.sh                       # brings up Postgres + Spark + Trino + Superset
./local/scripts/ingest.sh FACT_PAYMENT --full-refresh
./local/scripts/trino.sh                       # CLI query
open http://localhost:8088                     # dashboards (admin/admin)
```

Full walkthrough: **[docs/local-runtime.md](docs/local-runtime.md)**.

### Deploy to AWS

```bash
cd terraform
cp examples/single-customer/terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars
terraform init && terraform apply
# Populate the secret, upload code, backfill — see the full walkthrough.
```

Full walkthrough: **[docs/aws-runtime.md](docs/aws-runtime.md)**.

## How it works

```
┌──────────────────┐                                                         
│  Source RDBMS    │                                                         
│  (Oracle / PG)   │                                                         
│  data mart       │                                                         
└────────┬─────────┘                                                         
         │ JDBC: SELECT * WHERE watermark > :lower AND watermark <= :upper   
         ▼                                                                   
┌──────────────────┐    ┌───────────────────────┐                            
│ strata (Glue     │ ── │ DynamoDB              │  state machine             
│ PySpark job)     │    │ table watermarks      │  + locking                 
│                  │    └───────────────────────┘                            
│ - reconcile      │                                                         
│ - acquire lock   │    ┌───────────────────────┐                            
│ - extract        │ ── │ Secrets Manager       │  JDBC creds                
│ - write Iceberg  │    └───────────────────────┘                            
│ - advance state  │                                                         
└────────┬─────────┘                                                         
         │ atomic Iceberg snapshot                                           
         ▼                                                                   
┌──────────────────┐    ┌───────────────────────┐                            
│ S3 + Apache      │ ── │ AWS Glue Data Catalog │  catalog                   
│ Iceberg          │    │ silver_<domain>.<tbl> │                            
└────────┬─────────┘    └───────────┬───────────┘                            
         │                          │                                       
         ▼                          ▼                                       
┌──────────────────┐    ┌───────────────────────┐                            
│  Athena          │    │  QuickSight / BI      │                            
│  ad-hoc SQL      │    │  dashboards           │                            
└──────────────────┘    └───────────────────────┘                            
```

Detailed architecture in [`docs/architecture.md`](docs/architecture.md). Failure handling and recovery in [`docs/reliability.md`](docs/reliability.md).

## Reliability guarantees

After **any** failure mode, the next run resumes cleanly. See the [full failure matrix](docs/reliability.md#failure-mode-reference). Summary:

| Failure | Data loss? | Duplication? |
|---|---|---|
| Source DB unreachable | No (retried) | No |
| Glue worker dies during extract | No | No |
| Glue worker dies between Iceberg write and DynamoDB update | No (snapshot-based recovery detects it) | No |
| AWS auto-retry fires after a partial commit | No | No (idempotent via run_id check) |
| Two concurrent job runs | No | No (conditional lock) |
| Source adds a new column | No | No (Iceberg auto-evolves schema) |
| Source renames or changes column type | No | No (fails loudly with `SchemaDriftAlerts`) |

## Project layout

```
strata/
├── src/strata/             # Python package (the Glue job and helpers)
├── terraform/              # AWS infrastructure
├── examples/               # Example tables.yaml
├── scripts/                # deploy.sh, backfill_all.sh, package.sh
├── docs/                   # Architecture, reliability, operational runbook
└── tests/                  # Unit tests
```

## Configuration

Every table is one YAML entry. Example:

```yaml
tables:
  FACT_PAYMENT:
    source_table: FACT_PAYMENT
    domain: payments
    watermark_column: LAST_UPDATED_TIME
    primary_key: [PAYMENT_ID]
    write_mode: append
    partition_spec:
      - { transform: days,   column: VALUE_DATE }
      - { transform: bucket, column: DATA_OWNER_ID, n: 16 }
    sort_order: [VALUE_DATE, PAYMENT_ID]
    parallel_extract:
      column: PAYMENT_ID
      lower_bound: 1
      upper_bound: 100000000
      num_partitions: 8
```

Full reference in [`docs/configuration.md`](docs/configuration.md). Concrete example in [`examples/tables.yaml`](examples/tables.yaml).

## Documentation

### Runtime walkthroughs (start here)

- **[Local runtime](docs/local-runtime.md)** — step-by-step end-to-end on your
  laptop with PostgreSQL + Spark + Trino + Superset in Docker. Zero AWS
  credentials needed.
- **[AWS runtime](docs/aws-runtime.md)** — step-by-step end-to-end deploying
  into an AWS account with Glue + Athena + QuickSight.

### Reference

- [Architecture](docs/architecture.md) — components, data flow, design choices
- [Configuration](docs/configuration.md) — `tables.yaml` reference
- [Reliability](docs/reliability.md) — failure modes, recovery, idempotency proofs
- [Partitioning](docs/partitioning.md) — Iceberg partition specs, performance tuning
- [Operational runbook](docs/operational-runbook.md) — on-call procedures
- [Raw Parquet variant](docs/raw-parquet-variant.md) — if you can't use Iceberg
- [Local development setup](local/README.md) — architecture detail of the Docker stack

## Requirements

- AWS account with permissions to create Glue, S3, IAM, KMS, DynamoDB, Secrets Manager, EventBridge resources
- A network path from AWS Glue to your source RDBMS (VPC subnet + security group)
- Source database with a monotonic column per table (typically `LAST_UPDATED_TIME` or similar)
- Glue 4.0 (Spark 3.3, Python 3.10) — managed by AWS

## License

Apache 2.0 — see [`LICENSE`](LICENSE).

## AI assistant setup

This repository is designed to be worked on with AI coding assistants —
Claude, GitHub Copilot, Cursor, Aider, Continue, and anything that
follows the emerging conventions. The setup is intentionally minimal:

```
AGENTS.md                              ← single source of truth
CLAUDE.md                              ← pointer to AGENTS.md
.github/copilot-instructions.md        ← pointer to AGENTS.md
.claude/agents/      .claude/commands/      .claude/skills/
.github/prompts/     .github/instructions/
```

[`AGENTS.md`](./AGENTS.md) is the canonical project guide. Every tool
above either reads it directly (Copilot Coding Agent, Cursor, Aider,
newer Claude Code) or reads a one-line pointer that references it
(Claude via `CLAUDE.md`, Copilot Chat via
`.github/copilot-instructions.md`). When you change architectural
guidance, edit AGENTS.md — everything else picks it up automatically.

**For Claude users.** Sub-agents, slash commands, and skills live under
`.claude/`. Read `.claude/agents/*.md` for what each specialist does and
`.claude/commands/*.md` for what each `/command` invokes.

**For Copilot users.** The Claude commands have Copilot-equivalent
prompts under [`.github/prompts/`](./.github/prompts/) — invoke them in
Copilot Chat as `@workspace /<name>`. Path-scoped instructions go under
`.github/instructions/` with an `applyTo` frontmatter field.

**Adding a new assistant.** Most tools default to reading
`AGENTS.md`. If yours doesn't, add a thin pointer file at whatever path
it expects. Don't duplicate the content — the pointer pattern keeps the
guide in one place.

## Contributing

Issues and pull requests welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for guidelines.
