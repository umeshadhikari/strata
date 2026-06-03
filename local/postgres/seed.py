"""
Legacy synthetic-data seeder for the local Postgres data mart.

**Superseded by `bootstrap.py`** — the current `setup.sh` calls
bootstrap, which generates richer data (6 currencies vs 4, more
accounts, more countries) and upserts dims rather than truncating.
This file is kept for backwards compatibility with older test scripts
that import its generators directly.

Usage:
    python local/postgres/seed.py --days 30 --payments-per-day 1000

Run after `docker compose up postgres` and the init.sql has applied.
"""

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
    print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


CURRENCIES = [1, 2, 3, 4]
PAYMENT_METHODS = [1, 2, 3, 4]
COUNTRIES = ["US", "GB", "DE", "FR", "JP", "SG", "CH", "NL", "ES", "IT"]
STATUSES = ["APPROVED", "APPROVED", "APPROVED", "APPROVED", "REJECTED", "PENDING"]
APPROVERS = [f"u_{i:03d}" for i in range(1, 51)]


def connect():
    """Auto-detecting Postgres connection. See bootstrap.py:connect()
    for the canonical version."""
    # When run from the spark container, postgres is reachable as 'postgres'.
    # When run from the host, it's localhost. Auto-detect.
    default_host = "postgres" if os.path.exists("/.dockerenv") else "localhost"
    return psycopg2.connect(
        host=os.environ.get("PGHOST", default_host),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "data_mart"),
        user=os.environ.get("PGUSER", "strata"),
        password=os.environ.get("PGPASSWORD", "strata"),
    )


def generate_payments(start_date: date, days: int, per_day: int, start_pk: int):
    """Generate synthetic payment rows. See bootstrap.py for the richer
    current generator; this one is the simpler legacy version."""
    rows = []
    pk = start_pk
    for d in range(days):
        value_date = start_date + timedelta(days=d)
        for _ in range(per_day):
            account_id = random.choice([10001, 10002, 10003])
            data_owner_id = 100 if account_id in (10001, 10002) else 101
            currency_id = random.choice(CURRENCIES)
            method = random.choice(PAYMENT_METHODS)
            amount = Decimal(str(round(random.uniform(10, 50000), 2)))
            input_time = datetime.combine(
                value_date,
                datetime.min.time(),
            ) + timedelta(seconds=random.randint(0, 86400 - 1))
            approval_time = input_time + timedelta(seconds=random.randint(60, 3600))
            status = random.choice(STATUSES)

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
                    approval_time if status != "PENDING" else None,
                    random.choice(APPROVERS),
                    random.choice(COUNTRIES),
                    status,
                    input_time,  # LAST_UPDATED_TIME
                )
            )
            pk += 1
    return rows, pk


def generate_balances(start_date: date, days: int, start_pk: int):
    """Generate one balance row per account × day × currency. Three
    fixed account_ids — the legacy generator uses a smaller universe
    than bootstrap.py."""
    rows = []
    pk = start_pk
    for d in range(days):
        bal_date = start_date + timedelta(days=d)
        for account_id in (10001, 10002, 10003):
            for currency_id in CURRENCIES:
                data_owner_id = 100 if account_id in (10001, 10002) else 101
                opening = Decimal(str(round(random.uniform(100_000, 5_000_000), 2)))
                closing = opening + Decimal(str(round(random.uniform(-200_000, 200_000), 2)))
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


def main():
    """Seed Postgres with the legacy generator. Most local-dev work
    should use bootstrap.py via setup.sh instead."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="Days of history to generate")
    parser.add_argument(
        "--payments-per-day", type=int, default=500, help="Payments per day"
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="ISO start date (default: today - days)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Truncate facts before inserting",
    )
    args = parser.parse_args()

    start_date = (
        date.fromisoformat(args.start_date)
        if args.start_date
        else date.today() - timedelta(days=args.days - 1)
    )

    conn = connect()
    cur = conn.cursor()

    if args.reset:
        print("Truncating fact tables...")
        cur.execute("TRUNCATE TABLE data_mart.FACT_PAYMENT, data_mart.FACT_BALANCE")
        conn.commit()

    cur.execute("SELECT COALESCE(MAX(PAYMENT_ID), 0) FROM data_mart.FACT_PAYMENT")
    pay_pk = cur.fetchone()[0] + 1

    cur.execute("SELECT COALESCE(MAX(BALANCE_ID), 0) FROM data_mart.FACT_BALANCE")
    bal_pk = cur.fetchone()[0] + 1

    print(
        f"Generating {args.days} days × {args.payments_per_day} payments "
        f"from {start_date} (starting PK: payment={pay_pk}, balance={bal_pk})"
    )

    payments, next_pay = generate_payments(
        start_date, args.days, args.payments_per_day, pay_pk
    )
    balances, next_bal = generate_balances(start_date, args.days, bal_pk)

    print(f"Inserting {len(payments):,} payments...")
    execute_values(
        cur,
        """INSERT INTO data_mart.FACT_PAYMENT
        (PAYMENT_ID, DATA_OWNER_ID, ACCOUNT_ID, CURRENCY_ID, PAYMENT_METHOD_ID,
         AMOUNT, AMOUNT_IN_DEFAULT_CURRENCY, VALUE_DATE, INPUT_TIME,
         APPROVAL_TIME, APPROVER_USER_ID, COUNTERPARTY_COUNTRY, STATUS,
         LAST_UPDATED_TIME)
        VALUES %s""",
        payments,
        page_size=500,
    )

    print(f"Inserting {len(balances):,} balances...")
    execute_values(
        cur,
        """INSERT INTO data_mart.FACT_BALANCE
        (BALANCE_ID, ACCOUNT_ID, CURRENCY_ID, BALANCE_DATE, OPENING_BALANCE,
         CLOSING_BALANCE, DATA_OWNER_ID, LAST_UPDATED_TIME)
        VALUES %s""",
        balances,
        page_size=500,
    )

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. Next free PKs: payment={next_pay}, balance={next_bal}")


if __name__ == "__main__":
    main()
