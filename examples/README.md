# Examples

## tables.yaml

A complete `tables.yaml` covering a payment-domain data mart with:

- 5 fact tables: `FACT_PAYMENT`, `FACT_TRANSACTION`, `FACT_BALANCE`,
  `FACT_CURRENCY_EXCHANGE`, `FACT_TRANSACTION_BALANCE`
- 14 dimensions across `payments`, `balances`, and `shared` domains
- Parallel-extract configurations for the high-volume facts

Adapt the table list and field names to your data mart, upload to your
`scripts` S3 bucket, and you're ready to run.

## Adding a table

Append an entry under `tables:`:

```yaml
tables:
  YOUR_NEW_TABLE:
    source_table: YOUR_NEW_TABLE
    domain: your_domain
    watermark_column: LAST_UPDATED_TIME
    primary_key: [YOUR_PK]
    write_mode: append
    # Partitioning is optional but recommended for fact tables:
    partition_spec:
      - { transform: days, column: YOUR_DATE_COLUMN }
```

Then trigger a backfill:

```bash
aws glue start-job-run \
  --job-name <customer>-strata-ingest \
  --arguments '{"--TABLE_NAME":"YOUR_NEW_TABLE","--FULL_REFRESH":"true"}'
```

The next scheduled run picks up incremental loads automatically.
