-- Sample Trino queries against the local Iceberg lake.
--
-- Run from the Trino CLI:
--   ./local/scripts/trino.sh
--
-- Or paste into Superset's SQL Lab (http://localhost:8088 → SQL → SQL Lab).
--
-- Targets the production-shape 15-table schema described in
-- examples/sample_datamart.ddl.sql.

-- ------------------------------------------------------------------ --
-- Show what's in the lake
-- ------------------------------------------------------------------ --
SHOW SCHEMAS FROM iceberg;
SHOW TABLES FROM iceberg.silver_payments;
SHOW TABLES FROM iceberg.silver_balances;
SHOW TABLES FROM iceberg.silver_shared;

-- ------------------------------------------------------------------ --
-- Row counts + freshness across the four facts
-- ------------------------------------------------------------------ --
SELECT 'fact_pay_payment'          AS table_name,
       COUNT(*)                    AS row_count,
       MAX(last_updated_time)      AS max_watermark
FROM iceberg.silver_payments.fact_pay_payment
UNION ALL
SELECT 'fact_as_balance',         COUNT(*), MAX(last_updated_time)
FROM iceberg.silver_balances.fact_as_balance
UNION ALL
SELECT 'fact_as_transaction',     COUNT(*), MAX(last_updated_time)
FROM iceberg.silver_balances.fact_as_transaction
UNION ALL
SELECT 'fact_as_currency_exchange', COUNT(*), MAX(last_updated_time)
FROM iceberg.silver_balances.fact_as_currency_exchange;

-- ------------------------------------------------------------------ --
-- Daily payment volume + value
-- ------------------------------------------------------------------ --
SELECT
    CAST(creation_date AS DATE) AS day,
    COUNT(*)                    AS payment_count,
    SUM(amount)                 AS total_amount,
    AVG(amount)                 AS avg_amount
FROM iceberg.silver_payments.fact_pay_payment
GROUP BY CAST(creation_date AS DATE)
ORDER BY day;

-- ------------------------------------------------------------------ --
-- Distribution by data owner + currency
-- ------------------------------------------------------------------ --
SELECT
    p.data_owner_id,
    c.code              AS currency_code,
    COUNT(*)            AS payments,
    SUM(p.amount)       AS total
FROM iceberg.silver_payments.fact_pay_payment p
JOIN iceberg.silver_shared.dim_currency c
  ON c.id = p.currency_id
GROUP BY p.data_owner_id, c.code
ORDER BY total DESC;

-- ------------------------------------------------------------------ --
-- Cross-domain: balances roll-up by counterparty country of the payment
-- ------------------------------------------------------------------ --
SELECT
    p.counterparty_country_code,
    SUM(b.amount_in_default_currency) AS total_balance_default_ccy
FROM iceberg.silver_payments.fact_pay_payment p
JOIN iceberg.silver_balances.fact_as_balance b
  ON b.account_id = p.ordering_account_id
GROUP BY p.counterparty_country_code
ORDER BY total_balance_default_ccy DESC
LIMIT 10;

-- ------------------------------------------------------------------ --
-- Iceberg metadata — snapshots and watermarks
-- ------------------------------------------------------------------ --
-- Most recent snapshots and their glue.run_id provenance
SELECT
    committed_at,
    operation,
    summary['glue.run_id']           AS run_id,
    summary['glue.watermark_upper']  AS watermark_upper,
    summary['glue.row_count']        AS rows_in_this_snapshot
FROM iceberg.silver_payments."fact_pay_payment$snapshots"
ORDER BY committed_at DESC
LIMIT 10;

-- File-level statistics (proves partition pruning works)
SELECT file_path, record_count, file_size_in_bytes
FROM iceberg.silver_payments."fact_pay_payment$files"
LIMIT 10;

-- ------------------------------------------------------------------ --
-- A query that prunes via the partition spec [days(creation_date)]
-- (see local/config/tables.local.yaml :: FACT_PAY_PAYMENT.partition_spec)
-- ------------------------------------------------------------------ --
SELECT COUNT(*)
FROM iceberg.silver_payments.fact_pay_payment
WHERE creation_date BETWEEN TIMESTAMP '2026-05-01 00:00:00'
                        AND TIMESTAMP '2026-05-15 23:59:59';
-- EXPLAIN ANALYZE this to see "rows: X / partitions: Y" — should prune most.
