# Configuration reference

Every table is defined in a single YAML file (`tables.yaml`) that strata reads
at the start of each job run.

## Top-level structure

```yaml
defaults:           # optional; merged into every table entry
  source_schema: DATA_MART
  fetch_size: 10000

tables:
  FACT_PAYMENT:     # logical name; passed as --TABLE_NAME
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

## Per-table fields

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `source_table` | string | yes | — | Table name in the source database |
| `source_schema` | string | no | `""` | Schema/owner prefix; left blank uses the connection default |
| `domain` | string | no | `core` | Logical domain; determines the Glue database name (`silver_<domain>`) |
| `watermark_column` | string | no | — | Column for incremental extraction. Required for `append` mode. |
| `primary_key` | list[string] | no | `[]` | Required for `merge` write mode |
| `write_mode` | enum | no | `append` | One of `append`, `overwrite`, `merge` |
| `partition_spec` | list[object] | no | `[]` | Iceberg partition transforms (see below) |
| `sort_order` | list[string] | no | `[]` | Columns to sort by within partitions |
| `parallel_extract` | object | no | — | JDBC parallel read configuration |
| `fetch_size` | int | no | `10000` | JDBC fetch size |
| `target_database` | string | no | `silver_<domain>` | Override Glue database name |

## Write modes

| Mode | Behavior |
|---|---|
| `append` | Insert new rows. Standard for fact tables with monotonic watermarks. |
| `overwrite` | Replace data in matching partitions. Standard for dimensions and full refreshes. |
| `merge` | UPSERT by `primary_key`. Use when source updates existing rows. |

## Partition transforms

Each entry in `partition_spec` is one transform. They are applied in order to
produce composite partitions.

```yaml
partition_spec:
  - { transform: days,     column: VALUE_DATE }
  - { transform: bucket,   column: DATA_OWNER_ID, n: 16 }
```

Available transforms:

| Transform | Args | Effect |
|---|---|---|
| `identity` | column | One partition per unique value |
| `days` | column (date or timestamp) | One partition per day |
| `months` | column | One partition per month |
| `years` | column | One partition per year |
| `bucket` | column, `n` | Hash into `n` buckets |
| `truncate` | column, `n` | Truncate strings to `n` chars |

See [partitioning.md](partitioning.md) for guidance on what to partition by
and how to size buckets.

## Sort order

```yaml
sort_order: [VALUE_DATE, PAYMENT_ID]
```

Translates to `ALTER TABLE ... WRITE ORDERED BY ...` on table creation. Rows
are sorted within each partition during write, which clusters values and
enables Iceberg's file-level min/max pruning.

## Parallel extract

For tables larger than ~10M rows per extract, fan out the JDBC read across
multiple Spark partitions on an integer column:

```yaml
parallel_extract:
  column: PAYMENT_ID
  lower_bound: 1
  upper_bound: 100000000
  num_partitions: 8
```

The bounds don't need to be exact — Spark uses them only to compute partition
boundaries. Setting `num_partitions` higher than the number of Glue workers
× 4 provides no benefit.

## Defaults block

The `defaults:` block at the top of `tables.yaml` is merged into every table
entry. Use it for values that are the same across tables:

```yaml
defaults:
  source_schema: DATA_MART
  fetch_size: 20000

tables:
  FACT_PAYMENT:      # inherits source_schema and fetch_size
    source_table: FACT_PAYMENT
    ...
```

Table-level fields override the defaults.

## Validation

Configuration is validated on every job run. Common errors:

| Error | Cause |
|---|---|
| `Table 'X' not in config` | Missing entry in `tables.yaml` |
| `write_mode=merge requires primary_key` | Missing `primary_key` list |
| `Unknown partition transform 'X'` | Typo in `transform:` field |
| `Partition transform 'bucket' requires 'n'` | Missing `n:` for bucket/truncate |
| `Secret missing fields` | Secrets Manager secret incomplete |

A failed config validation raises `ConfigError`, which is a `PermanentError`
that fails fast without retry.
