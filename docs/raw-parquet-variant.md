# Raw Parquet Variant (if you can't use Iceberg)

If a hard constraint requires raw Parquet output instead of Iceberg, change two
things in `jobs/ingest_table.py` and remove the Iceberg-specific Spark config.

## 1. Replace `write_iceberg()` with `write_parquet()`

```python
def write_parquet(df, target_s3_uri, mode, partition_cols):
    count = df.count()
    if count == 0:
        return 0
    writer = (
        df.write.format("parquet")
        .mode("overwrite" if mode == "overwrite" else "append")
        .option("compression", "snappy")
    )
    if partition_cols:
        writer = writer.partitionBy(*partition_cols)
    writer.save(target_s3_uri)
    return count
```

## 2. Drop the Iceberg Spark config

Remove from `build_spark_context()`:
```python
"spark.sql.extensions": "...",
"spark.sql.catalog.glue_catalog": "...",
"spark.sql.catalog.glue_catalog.warehouse": "...",
# etc.
```

## 3. Trade-offs you accept

| What you lose | What you keep |
|---|---|
| Atomic snapshot commits — partial failures leave half-written files | Open file format (Parquet) |
| Schema evolution — adding a column requires re-writing | Athena queryability |
| Time travel + snapshot expiration | Cheap S3 storage |
| Easy MERGE / UPSERT in later phases | Familiarity (most teams know plain Parquet) |
| Automatic Glue Catalog partition discovery | |

## 4. You still need Glue Catalog

Even with raw Parquet, register the tables in Glue Catalog manually or via a
Glue Crawler. Without a catalog, Athena can't query the data efficiently.

```bash
aws glue create-table --database-name silver_payments --table-input '{
  "Name": "fact_payment",
  "StorageDescriptor": {
    "Columns": [...],
    "Location": "s3://<lake>/silver/silver_payments/fact_payment/",
    "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
    "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
    "SerdeInfo": {
      "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }
  },
  "PartitionKeys": [{"Name": "_ingest_date", "Type": "date"}],
  "TableType": "EXTERNAL_TABLE"
}'
```

You'd also need a Glue Crawler scheduled daily to pick up new partitions, OR
your job needs to add partitions explicitly via `aws glue create-partition`
after each write. That's the operational overhead Iceberg removes.

## Bottom line

Raw Parquet works but is operationally more painful for almost no benefit.
Iceberg gives you everything raw Parquet does plus the things you'd otherwise
have to script manually. Recommend you only fall back to raw Parquet if there's
a compliance or governance rule that genuinely forbids Iceberg.
