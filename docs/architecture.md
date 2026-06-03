# Architecture — Data Mart to Iceberg Silver

```
┌──────────────────┐                                                          
│  Existing data   │  JDBC                                                    
│  mart (Oracle /  │ ◀──┐                                                     
│  PostgreSQL)     │    │                                                     
│                  │    │                                                     
│  FACT_PAYMENT    │    │                                                     
│  FACT_TRANSACTION│    │                                                     
│  FACT_BALANCE    │    │                                                     
│  FACT_CURRENCY...│    │ SELECT * WHERE LAST_UPDATED_TIME > :watermark       
│  DIM_*           │    │                                                     
└──────────────────┘    │                                                     
                        │                                                    
                        │                                                    
              ┌─────────┴──────────────────┐                                  
              │                            │                                 
              │  AWS Glue PySpark Job      │   ◀── reads tables.yaml on each 
              │  (single, parameterised)   │       run, knows what to extract
              │  Runtime: Glue 4.0 (Spark 3.3)│                               
              │  Workers: 2 × G.1X         │                                  
              └─────────┬──────────────────┘                                  
                        │ writes Iceberg snapshot                             
                        ▼                                                    
              ┌────────────────────────────┐                                  
              │   S3 Bucket                │                                  
              │   silver/<glue_db>/<table>/│                                  
              │   ├── data/   (Parquet)    │                                  
              │   ├── metadata/ (Iceberg)  │                                  
              │   └── snapshots/           │                                  
              └────────────┬───────────────┘                                  
                           │                                                  
                           │ registered as Iceberg table                      
                           ▼                                                  
              ┌────────────────────────────┐                                  
              │  AWS Glue Data Catalog     │                                  
              │  databases:                │                                  
              │  - silver_payments         │                                  
              │  - silver_balances         │                                  
              │  - silver_shared           │                                  
              └────────────┬───────────────┘                                  
                           │                                                  
              ┌────────────┴────────────┐                                    
              ▼                         ▼                                    
       ┌────────────┐          ┌───────────────┐                            
       │  Athena    │          │  QuickSight   │                            
       │  ad-hoc    │          │  dashboards   │                            
       │  SQL       │          │  (Phase 2)    │                            
       └────────────┘          └───────────────┘                            
```

## Operational flow per run

1. **Trigger** — EventBridge Scheduler fires daily at 06:00 UTC (or manual via CLI).
2. **Glue Workflow** invokes the ingest job once per table (parallel fan-out).
3. **Job startup** (~30 sec cold) — Glue spins up 2 G.1X workers.
4. **Config load** — job reads `s3://<scripts>/config/tables.yaml` for the table.
5. **Watermark fetch** — job reads `last_watermark` from DynamoDB.
6. **JDBC extract** — `SELECT * FROM TRAX_DM.FACT_X WHERE LAST_UPDATED_TIME > :wm`.
7. **Metadata enrichment** — adds `_ingest_run_id`, `_ingest_timestamp`,
   `_source_table`, `_ingest_date`.
8. **Iceberg write** — appends partition to the Iceberg table; commits snapshot.
9. **Catalog update** — Iceberg writes new metadata file; Glue Catalog reflects it.
10. **Watermark advance** — DynamoDB updated to `MAX(LAST_UPDATED_TIME)` of the batch.
11. **Job commit** — Glue marks success; CloudWatch metrics published.

Typical run for a fact table with daily delta: 3–8 minutes.
Typical run for a dim table: 1–2 minutes.

## What this is NOT

This intentionally does **not** include:

- **No transformations** — data lands exactly as it is in the data mart. Field
  names, types, semantics — all preserved. This is "Silver as-is."
- **No conformance / dedup / SCD logic** — your data mart already did that work.
- **No PII tokenization** — assumes the data mart already tokenized PII upstream.
  If not, add a tokenizer step at the column level before write (see
  `docs/pii-tokenization-add-on.md` — to be added if needed).
- **No Gold layer** — that's a separate downstream pipeline; this stops at Silver.
- **No streaming** — daily/hourly batch only. CDC streaming comes in Phase 4.

## Scaling notes

- For `FACT_TRANSACTION` (heaviest table), enable `parallel_extract` in
  `tables.yaml` — fans the JDBC read into 8 parallel partitions on the PK.
- For tables with bursty change patterns, consider hourly schedule instead
  of daily — just edit the Glue Trigger cron.
- For tables larger than ~50 million rows per day, scale Glue workers from
  G.1X (4 vCPU, 16 GB) to G.2X (8 vCPU, 32 GB) or increase `number_of_workers`.

## Recovery / rerun

- **Single-table replay**: invoke with `--FULL_REFRESH=true`. Watermark is ignored;
  Iceberg snapshot overwrites the table.
- **Single-day replay**: edit DynamoDB watermark for the table to yesterday's value,
  then run normally.
- **Snapshot rollback**: Iceberg supports time travel —
  `CALL system.rollback_to_snapshot('silver_payments.fact_payment', 12345)`.

## Cost — typical customer (1M tx/day, 500 GB curated)

| Item | Approx monthly cost |
|---|---|
| Glue Job runs (30 DPU-hours/mo) | ~$13 |
| S3 storage (500 GB Standard) | ~$12 |
| DynamoDB watermarks (on-demand) | ~$1 |
| KMS encryption requests | ~$2 |
| Secrets Manager (1 secret) | ~$0.40 |
| Glue Data Catalog (free under 1M objects) | $0 |
| Athena (depends on consumer use) | $5–50 |
| **Total** | **~$30–75 / month** |

No idle cost for the ingestion pipeline. Glue jobs are charged per DPU-hour
only when running. The lake itself has no compute footprint.
