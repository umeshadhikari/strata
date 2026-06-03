-- Sample Trino queries against the local Iceberg lake.
--
-- Run from the Trino CLI:
--   ./local/scripts/trino.sh
--
-- Or paste into Superset's SQL Lab (http://localhost:8088 → SQL → SQL Lab).

-- ------------------------------------------------------------------ --
-- Show what's in the lake
-- ------------------------------------------------------------------ --
SHOW SCHEMAS FROM iceberg;
SHOW TABLES FROM iceberg.silver_payments;
SHOW TABLES FROM iceberg.silver_balances;
SHOW TABLES FROM iceberg.silver_shared;

-- ------------------------------------------------------------------ --
-- Row counts + freshness
-- ------------------------------------------------------------------ --
SELECT
    'fact_payment'             AS table_name,
    COUNT(*)                   AS row_count,
    MAX(last_updated_time)     AS max_watermark
FROM iceberg.silver_payments.fact_payment
UNION ALL
SELECT 'fact_balance', COUNT(*), MAX(last_updated_time)
FROM iceberg.silver_balances.fact_balance;

-- ------------------------------------------------------------------ --
-- Daily payment volume + value
-- ------------------------------------------------------------------ --
SELECT
    value_date          AS day,
    COUNT(*)            AS payment_count,
    SUM(amount)         AS total_amount,
    AVG(amount)         AS avg_amount
FROM iceberg.silver_payments.fact_payment
GROUP BY value_date
ORDER BY value_date;

-- ------------------------------------------------------------------ --
-- Distribution by data owner + currency
-- ------------------------------------------------------------------ --
SELECT
    p.data_owner_id,
    c.currency_code,
    COUNT(*)            AS payments,
    SUM(p.amount)       AS total
FROM iceberg.silver_payments.fact_payment p
JOIN iceberg.silver_shared.dim_currency c
  ON c.currency_id = p.currency_id
GROUP BY p.data_owner_id, c.currency_code
ORDER BY total DESC;

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
FROM iceberg.silver_payments."fact_payment$snapshots"
ORDER BY committed_at DESC
LIMIT 10;

-- File-level statistics (proves partition pruning works)
SELECT file_path, record_count, file_size_in_bytes
FROM iceberg.silver_payments."fact_payment$files"
LIMIT 10;

-- ------------------------------------------------------------------ --
-- A query that prunes via the partition spec [days(value_date), bucket(4, data_owner_id)]
-- ------------------------------------------------------------------ --
SELECT COUNT(*)
FROM iceberg.silver_payments.fact_payment
WHERE value_date BETWEEN DATE '2026-05-01' AND DATE '2026-05-15'
  AND data_owner_id = 100;
-- EXPLAIN ANALYZE this to see "rows: X / partitions: Y" — should prune most.
