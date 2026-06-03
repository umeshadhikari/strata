# Iceberg partitioning guide

The source database has no partitioning, but the lake should. Iceberg's hidden
partitioning lets us choose layout purely on query patterns, with no impact on
the SQL consumers write.

## Why partition at all

Two reasons:
1. **Query performance** — partition pruning skips files that can't match
   `WHERE` predicates. Reduces TB-scale scans to GB-scale.
2. **Compaction efficiency** — Iceberg's compaction operates per partition.
   Smaller, well-sized partitions compact faster.

## What to partition by

Two heuristics, in priority order:

**1. The column you filter on most.** For fact tables this is almost always
a business date (`VALUE_DATE`, `TRANSACTION_DATE`, `BALANCE_DATE`). For
dimensions it's typically nothing — they're small enough to scan.

**2. A column that drives row-level access control.** If you use Lake Formation
row filters by `DATA_OWNER_ID`, bucketing by that column dramatically improves
RBAC query performance.

## Recommended patterns

| Table size | Pattern | Example |
|---|---|---|
| Dimension (< 1 GB) | No partitioning | `partition_spec: []` |
| Slowly-changing dim (1–10 GB) | Maybe by year | `[{ transform: years, column: EFFECTIVE_DATE }]` |
| Small fact (< 100 GB) | Days by business date | `[{ transform: days, column: VALUE_DATE }]` |
| Medium fact (100 GB – 1 TB) | Days + bucket by RBAC col | `[days(VALUE_DATE), bucket(16, DATA_OWNER_ID)]` |
| Large fact (1 TB+) | Days + bigger bucket | `[days(VALUE_DATE), bucket(32, DATA_OWNER_ID)]` |
| Append-only audit log | Days by event timestamp | `[{ transform: days, column: EVENT_TIME }]` |

## Bucket count selection

The right bucket count balances two things:

- **Pruning** — more buckets = better pruning when queries filter on the
  bucket column.
- **File count** — each bucket × each date partition is at least one file.
  Too many buckets = too many small files = slow Iceberg metadata.

Heuristic: number of buckets × number of date partitions = number of files.
Target ~50,000 files per table. For a 3-year-history table:
- 3 years × 365 days = ~1,100 date partitions
- 50,000 / 1,100 = ~45 buckets per partition
- Round to a power of 2: 32 or 64

For your typical payment-hub customer with 20 data owners and 1M tx/day:
- 16 buckets gives ~1–2 data owners per bucket
- 32 buckets gives ~0.5–1 data owners per bucket (fragments rows of large owners)

Start with 16. Bump to 32 only if the fact table exceeds 5 TB.

## What NOT to partition by

- **High-cardinality columns** (e.g., `PAYMENT_ID`). Creates millions of
  partitions — Iceberg metadata becomes a bottleneck.
- **Slowly-evolving categorical columns** unless you actually filter on them.
- **`_ingest_date`** by itself. Useful as a debugging hint but useless for
  business queries.

## Sort order

Sorting within partitions clusters values and enables file-level pruning via
Iceberg's min/max stats. Specify in `tables.yaml`:

```yaml
partition_spec:
  - { transform: days, column: VALUE_DATE }
sort_order:
  - VALUE_DATE
  - PAYMENT_ID
```

Queries filtering by `PAYMENT_ID = X` will scan only the files whose
min/max range includes X. Dramatic speedup for point lookups.

## Adding partitioning to an existing table

Iceberg supports partition evolution. New rows land partitioned; old rows
stay in their original layout. Queries combine them seamlessly:

```sql
ALTER TABLE silver_payments.fact_payment
ADD PARTITION FIELD days(value_date);

ALTER TABLE silver_payments.fact_payment
ADD PARTITION FIELD bucket(16, data_owner_id);
```

To physically rewrite old data so it matches the new spec:

```sql
CALL system.rewrite_data_files(
  table => 'silver_payments.fact_payment',
  options => map('target-file-size-bytes', '268435456')
);
```

## Compaction

Daily ingestion creates one file per worker per partition. Without compaction,
you accumulate thousands of small files. Schedule a nightly compaction job:

```sql
CALL system.rewrite_data_files(
  table => 'silver_payments.fact_payment',
  options => map(
    'target-file-size-bytes', '268435456',
    'min-input-files', '5'
  )
);

CALL system.rewrite_manifests('silver_payments.fact_payment');

CALL system.expire_snapshots('silver_payments.fact_payment', current_date - 30);
```

A separate strata mode for compaction is on the roadmap; for now, run via a
separate Glue job calling these procedures.

## Performance reference

For a 5 TB `FACT_PAYMENT` partitioned by `days(VALUE_DATE)` and
`bucket(16, DATA_OWNER_ID)`:

| Query | Scanned | Latency | Cost |
|---|---|---|---|
| Single day, single data_owner | ~30 MB | 1–3 s | $0.0002 |
| Single month, single data_owner | ~1 GB | 3–10 s | $0.005 |
| Single month, all data_owners | ~15 GB | 10–30 s | $0.08 |
| Full year, single data_owner | ~12 GB | 20–60 s | $0.06 |
| Full scan (all years, all owners) | 5 TB | 5–15 min | $25 |

The last is what your workgroup `bytes_scanned_cutoff_per_query` should prevent.
