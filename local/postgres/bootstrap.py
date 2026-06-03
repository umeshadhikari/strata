"""
First-run bootstrap: populate the local PostgreSQL data mart with a clean
set of test data so the strata pipeline has something to ingest.

This script is idempotent — safe to re-run. It will:

  1. Verify the connection works.
  2. Ensure the schema and tables exist (init.sql is normally applied at
     container startup, but this script can recover from a partial setup).
  3. Upsert the reference dimensions (currency, data owner, account,
     payment method).
  4. Generate `--days` × `--payments-per-day` synthetic payments.
  5. Generate the corresponding daily balances.
  6. Print a summary of what's in the database.

Usage::

    # Inside the spark container (recommended — same Docker network):
    docker compose -f local/docker-compose.yml exec spark \\
        python /app/../local/postgres/bootstrap.py

    # Or from the host (psycopg2 needed):
    pip install psycopg2-binary
    python local/postgres/bootstrap.py

Options:
    --days N                   Days of history to generate (default 30)
    --payments-per-day N       Payments per day per data-owner (default 500)
    --reset                    Truncate fact tables before inserting
    --start-date YYYY-MM-DD    Override the window start (default today − days)
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    print("ERROR: psycopg2 not installed.")
    print("  In container: pip install psycopg2-binary")
    print("  On host:      pip install psycopg2-binary")
    sys.exit(1)


# --------------------------------------------------------------------------- #
# Reference data — small, idempotent, complete on every bootstrap run
# --------------------------------------------------------------------------- #
CURRENCIES = [
    (1, "USD", "US Dollar",      2),
    (2, "EUR", "Euro",           2),
    (3, "GBP", "British Pound",  2),
    (4, "JPY", "Japanese Yen",   0),
    (5, "CHF", "Swiss Franc",    2),
    (6, "SGD", "Singapore Dollar", 2),
]

DATA_OWNERS = [
    (100, "Acme Treasury",     "enterprise"),
    (101, "Globex Treasury",   "enterprise"),
    (102, "Initech Treasury",  "enterprise"),
    (103, "Hooli Treasury",    "enterprise"),
    (104, "Pied Piper Treasury", "smb"),
]

ACCOUNTS = [
    # (account_id, account_number, account_name, is_iban, data_owner_id)
    (10001, "0123456789",                 "Acme Operating",   0, 100),
    (10002, "GB29NWBK60161331926819",     "Acme UK",          1, 100),
    (10003, "DE89370400440532013000",     "Acme EU",          1, 100),
    (10004, "9876543210",                 "Globex Operating", 0, 101),
    (10005, "FR1420041010050500013M02606", "Globex FR",      1, 101),
    (10006, "1111222233",                 "Initech US",       0, 102),
    (10007, "CH9300762011623852957",      "Hooli CH",         1, 103),
    (10008, "5555666677",                 "Pied Piper",       0, 104),
]

PAYMENT_METHODS = [
    (1, "WIRE",  "Wire Transfer"),
    (2, "ACH",   "Automated Clearing House"),
    (3, "SEPA",  "SEPA Credit Transfer"),
    (4, "SWIFT", "SWIFT MT103"),
    (5, "RTP",   "Real-Time Payment"),
    (6, "BACS",  "BACS Direct Credit"),
]

COUNTRIES = ["US", "GB", "DE", "FR", "JP", "SG", "CH", "NL", "ES", "IT", "PA", "MX"]
STATUSES = ["APPROVED", "APPROVED", "APPROVED", "APPROVED", "REJECTED", "PENDING"]
APPROVERS = [f"u_{i:03d}" for i in range(1, 51)]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def connect():
    """Open a psycopg2 connection to the local data_mart Postgres.

    Auto-detects whether we're inside the spark Docker container
    (hostname `postgres`) or running on the host (`localhost`). All
    other connection params come from PG* env vars with sensible
    docker-compose-aligned defaults.
    """
    default_host = "postgres" if os.path.exists("/.dockerenv") else "localhost"
    return psycopg2.connect(
        host=os.environ.get("PGHOST", default_host),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "data_mart"),
        user=os.environ.get("PGUSER", "strata"),
        password=os.environ.get("PGPASSWORD", "strata"),
    )


def ensure_schema(cur) -> None:
    """init.sql normally handles this, but ensure idempotency."""
    cur.execute("CREATE SCHEMA IF NOT EXISTS data_mart")


def upsert_dims(cur) -> None:
    """Insert-or-update the reference dimensions."""

    print("  → upserting DIM_CURRENCY")
    execute_values(
        cur,
        """INSERT INTO data_mart.DIM_CURRENCY
           (currency_id, currency_code, currency_name, decimal_places, last_updated_time)
           VALUES %s
           ON CONFLICT (currency_id) DO UPDATE
           SET currency_code = EXCLUDED.currency_code,
               currency_name = EXCLUDED.currency_name,
               decimal_places = EXCLUDED.decimal_places,
               last_updated_time = EXCLUDED.last_updated_time""",
        [(c[0], c[1], c[2], c[3], datetime.now()) for c in CURRENCIES],
    )

    print("  → upserting DIM_DATA_OWNER")
    execute_values(
        cur,
        """INSERT INTO data_mart.DIM_DATA_OWNER
           (data_owner_id, data_owner_name, data_owner_type, last_updated_time)
           VALUES %s
           ON CONFLICT (data_owner_id) DO UPDATE
           SET data_owner_name = EXCLUDED.data_owner_name,
               data_owner_type = EXCLUDED.data_owner_type,
               last_updated_time = EXCLUDED.last_updated_time""",
        [(d[0], d[1], d[2], datetime.now()) for d in DATA_OWNERS],
    )

    print("  → upserting DIM_ACCOUNT")
    execute_values(
        cur,
        """INSERT INTO data_mart.DIM_ACCOUNT
           (account_id, account_number, account_name, is_iban_account,
            data_owner_id, last_updated_time)
           VALUES %s
           ON CONFLICT (account_id) DO UPDATE
           SET account_number = EXCLUDED.account_number,
               account_name = EXCLUDED.account_name,
               is_iban_account = EXCLUDED.is_iban_account,
               data_owner_id = EXCLUDED.data_owner_id,
               last_updated_time = EXCLUDED.last_updated_time""",
        [(a[0], a[1], a[2], a[3], a[4], datetime.now()) for a in ACCOUNTS],
    )

    print("  → upserting DIM_PAYMENT_METHOD")
    execute_values(
        cur,
        """INSERT INTO data_mart.DIM_PAYMENT_METHOD
           (payment_method_id, payment_method_code, payment_method_name,
            last_updated_time)
           VALUES %s
           ON CONFLICT (payment_method_id) DO UPDATE
           SET payment_method_code = EXCLUDED.payment_method_code,
               payment_method_name = EXCLUDED.payment_method_name,
               last_updated_time = EXCLUDED.last_updated_time""",
        [(p[0], p[1], p[2], datetime.now()) for p in PAYMENT_METHODS],
    )


def truncate_facts(cur) -> None:
    """Empty the two fact tables. Called only when `--reset` is passed."""
    print("  → TRUNCATE FACT_PAYMENT, FACT_BALANCE")
    cur.execute("TRUNCATE TABLE data_mart.FACT_PAYMENT, data_mart.FACT_BALANCE")


def generate_payments(start_date: date, days: int, per_day: int, start_pk: int):
    """Generate synthetic payment rows distributed across the date range.

    Returns a (rows, next_pk) tuple where rows is a list of tuples ready
    for `execute_values` and next_pk is one past the highest PK used.
    Each row gets `last_updated_time = input_time`, which means
    incremental ingests after seeding see "everything is old" until
    fresh data is injected (`add_incremental_data.py`).
    """
    rows = []
    pk = start_pk
    account_ids = [a[0] for a in ACCOUNTS]
    payment_method_ids = [pm[0] for pm in PAYMENT_METHODS]
    currency_ids = [c[0] for c in CURRENCIES]
    account_owner = {a[0]: a[4] for a in ACCOUNTS}

    for d in range(days):
        value_date = start_date + timedelta(days=d)
        for _ in range(per_day):
            account_id = random.choice(account_ids)
            data_owner_id = account_owner[account_id]
            currency_id = random.choice(currency_ids)
            method = random.choice(payment_method_ids)
            amount = Decimal(str(round(random.uniform(10, 50_000), 2)))
            input_time = datetime.combine(
                value_date, datetime.min.time()
            ) + timedelta(seconds=random.randint(0, 86_400 - 1))
            status = random.choice(STATUSES)
            approval_time = (
                input_time + timedelta(seconds=random.randint(60, 3600))
                if status != "PENDING"
                else None
            )
            rows.append(
                (
                    pk,
                    data_owner_id,
                    account_id,
                    currency_id,
                    method,
                    amount,
                    amount,  # AMOUNT_IN_DEFAULT_CURRENCY — simplified
                    value_date,
                    input_time,
                    approval_time,
                    random.choice(APPROVERS),
                    random.choice(COUNTRIES),
                    status,
                    input_time,  # LAST_UPDATED_TIME
                )
            )
            pk += 1
    return rows, pk


def generate_balances(start_date: date, days: int, start_pk: int):
    """Generate one balance row per account × day × subset of currencies.

    Each account gets balances in 3 randomly-chosen currencies per day
    (rather than all 6) so the data has realistic sparsity. Opening and
    closing balances are independent random values; closing is derived
    by adding a swing in ±$200K to opening.
    """
    rows = []
    pk = start_pk
    account_owner = {a[0]: a[4] for a in ACCOUNTS}
    currency_ids = [c[0] for c in CURRENCIES]

    for d in range(days):
        bal_date = start_date + timedelta(days=d)
        for account_id, data_owner_id in account_owner.items():
            # Only generate balances for the account's primary currencies
            for currency_id in random.sample(currency_ids, k=min(3, len(currency_ids))):
                opening = Decimal(str(round(random.uniform(100_000, 5_000_000), 2)))
                closing = opening + Decimal(
                    str(round(random.uniform(-200_000, 200_000), 2))
                )
                rows.append(
                    (
                        pk,
                        account_id,
                        currency_id,
                        bal_date,
                        opening,
                        closing,
                        data_owner_id,
                        datetime.combine(bal_date, datetime.min.time())
                        + timedelta(hours=23, minutes=59),
                    )
                )
                pk += 1
    return rows, pk


def insert_facts(cur, payments, balances) -> None:
    """Bulk-insert generated fact rows in 500-row batches."""
    print(f"  → inserting {len(payments):,} payments")
    execute_values(
        cur,
        """INSERT INTO data_mart.FACT_PAYMENT
           (payment_id, data_owner_id, account_id, currency_id,
            payment_method_id, amount, amount_in_default_currency, value_date,
            input_time, approval_time, approver_user_id, counterparty_country,
            status, last_updated_time)
           VALUES %s""",
        payments,
        page_size=500,
    )
    print(f"  → inserting {len(balances):,} balances")
    execute_values(
        cur,
        """INSERT INTO data_mart.FACT_BALANCE
           (balance_id, account_id, currency_id, balance_date, opening_balance,
            closing_balance, data_owner_id, last_updated_time)
           VALUES %s""",
        balances,
        page_size=500,
    )


def print_summary(cur) -> None:
    """Print a one-line summary of every data_mart table — row count
    plus max watermark. Used as the final step in main() so a developer
    knows immediately whether the seed worked."""
    print()
    print("Database summary:")
    for table in [
        "dim_currency",
        "dim_data_owner",
        "dim_account",
        "dim_payment_method",
        "fact_payment",
        "fact_balance",
    ]:
        cur.execute(f"SELECT COUNT(*), MAX(last_updated_time) FROM data_mart.{table}")
        n, wm = cur.fetchone()
        print(f"  {table:24s} rows={n:>8,}  max(last_updated_time)={wm}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    """Seed the local Postgres data mart with synthetic data.

    Idempotent across re-runs: dims are upserted (ON CONFLICT DO UPDATE),
    facts are appended unless `--reset` is passed. Pass `--reset` when
    you want a clean slate before running tests that count rows from
    zero.
    """
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--payments-per-day", type=int, default=500)
    p.add_argument("--reset", action="store_true", help="Truncate facts before insert")
    p.add_argument(
        "--start-date",
        default=None,
        help="ISO start date (default: today − days + 1)",
    )
    args = p.parse_args()

    start = (
        date.fromisoformat(args.start_date)
        if args.start_date
        else date.today() - timedelta(days=args.days - 1)
    )

    print(f"Bootstrap: {args.days} days × {args.payments_per_day} payments/day "
          f"starting {start}")
    print()

    conn = connect()
    print(f"Connected to {conn.dsn}")
    cur = conn.cursor()

    print("\n[1/4] Ensuring schema...")
    ensure_schema(cur)
    conn.commit()

    print("\n[2/4] Upserting dimensions...")
    upsert_dims(cur)
    conn.commit()

    if args.reset:
        print("\n[2b]  Resetting fact tables...")
        truncate_facts(cur)
        conn.commit()

    cur.execute("SELECT COALESCE(MAX(payment_id), 0) FROM data_mart.fact_payment")
    pay_pk = cur.fetchone()[0] + 1
    cur.execute("SELECT COALESCE(MAX(balance_id), 0) FROM data_mart.fact_balance")
    bal_pk = cur.fetchone()[0] + 1

    print(f"\n[3/4] Generating synthetic facts (starting "
          f"payment_id={pay_pk}, balance_id={bal_pk})...")
    payments, _ = generate_payments(start, args.days, args.payments_per_day, pay_pk)
    balances, _ = generate_balances(start, args.days, bal_pk)
    insert_facts(cur, payments, balances)
    conn.commit()

    print("\n[4/4] Summary:")
    print_summary(cur)

    cur.close()
    conn.close()

    print("\nBootstrap complete. Next:")
    print("  ./local/scripts/ingest.sh DIM_CURRENCY --full-refresh")
    print("  ./local/scripts/run-all.sh --full-refresh")


if __name__ == "__main__":
    main()
