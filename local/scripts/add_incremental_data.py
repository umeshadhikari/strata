"""
Inject incremental rows into the upstream PostgreSQL data mart for testing
strata's watermark-bounded incremental ingestion.

Three modes mirror the production scenarios:

  --inserts N    : N brand-new payments (never seen by strata)
  --updates N    : N existing payments get a new status + advanced
                   last_updated_time (forces re-ingest of those rows)
  --mixed N      : roughly half inserts, half updates

Every row touched gets ``last_updated_time = NOW()`` so the next ingest's
watermark window picks it up.

Run inside the spark container so the helper shares its venv with the
ingest job and seed.py.

Usage::

    python local/scripts/add_incremental_data.py --inserts 100
    python local/scripts/add_incremental_data.py --updates 50
    python local/scripts/add_incremental_data.py --mixed 100
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg2
from psycopg2.extras import execute_values

# Mirror seed.py so we generate plausibly-distributed rows.
CURRENCIES = [1, 2, 3, 4, 5, 6]
PAYMENT_METHODS = [1, 2, 3, 4, 5, 6]
COUNTRIES = ["US", "GB", "DE", "FR", "JP", "SG", "CH", "NL", "ES", "IT", "MX", "PA"]
STATUSES = ["APPROVED", "APPROVED", "APPROVED", "REJECTED", "PENDING"]
APPROVERS = [f"u_{i:03d}" for i in range(1, 51)]


def connect():
    """Connect to the local Postgres data mart.

    Auto-detects whether we're inside the spark Docker container (uses
    the compose-network hostname `postgres`) or running on the host
    (uses `localhost`). Credentials and database name match the
    docker-compose defaults; override via PG* env vars.
    """
    default_host = "postgres" if os.path.exists("/.dockerenv") else "localhost"
    return psycopg2.connect(
        host=os.environ.get("PGHOST", default_host),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "data_mart"),
        user=os.environ.get("PGUSER", "strata"),
        password=os.environ.get("PGPASSWORD", "strata"),
    )


def insert_new_payments(conn, n: int, run_label: str) -> int:
    """Insert N net-new payments with last_updated_time = NOW()."""
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(PAYMENT_ID), 0) FROM data_mart.FACT_PAYMENT")
    next_pk = cur.fetchone()[0] + 1

    rows = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    value_date = date.today()
    for i in range(n):
        account_id = random.choice([10001, 10002, 10003])
        data_owner_id = 100 if account_id in (10001, 10002) else 101
        amount = Decimal(str(round(random.uniform(10, 50000), 2)))
        input_time = now - timedelta(seconds=random.randint(0, 300))
        approval_time = input_time + timedelta(seconds=random.randint(60, 600))
        status = random.choice(STATUSES)
        rows.append((
            next_pk + i,
            data_owner_id,
            account_id,
            random.choice(CURRENCIES),
            random.choice(PAYMENT_METHODS),
            amount,
            amount,
            value_date,
            input_time,
            approval_time if status != "PENDING" else None,
            random.choice(APPROVERS),
            random.choice(COUNTRIES),
            status,
            now,  # LAST_UPDATED_TIME — the watermark column
        ))

    execute_values(
        cur,
        """INSERT INTO data_mart.FACT_PAYMENT
        (PAYMENT_ID, DATA_OWNER_ID, ACCOUNT_ID, CURRENCY_ID, PAYMENT_METHOD_ID,
         AMOUNT, AMOUNT_IN_DEFAULT_CURRENCY, VALUE_DATE, INPUT_TIME,
         APPROVAL_TIME, APPROVER_USER_ID, COUNTERPARTY_COUNTRY, STATUS,
         LAST_UPDATED_TIME)
        VALUES %s""",
        rows,
        page_size=500,
    )
    conn.commit()
    cur.close()
    print(f"[inserts] +{n:,} new payments  PK range [{next_pk}, {next_pk + n - 1}]  "
          f"last_updated_time = {now.isoformat()}  label={run_label}")
    return n


def update_existing_payments(conn, n: int, run_label: str) -> int:
    """
    Update N existing payments — flip their STATUS and advance LAST_UPDATED_TIME.

    Targets PENDING payments first (the realistic "approval just came through"
    scenario). Falls back to any rows if there aren't enough pending.
    """
    cur = conn.cursor()
    # Prefer PENDING since flipping them to APPROVED is the realistic case.
    cur.execute(
        """SELECT PAYMENT_ID FROM data_mart.FACT_PAYMENT
           WHERE STATUS = 'PENDING' ORDER BY PAYMENT_ID LIMIT %s""",
        (n,),
    )
    targets = [r[0] for r in cur.fetchall()]
    if len(targets) < n:
        # Backfill with any rows we haven't already touched.
        cur.execute(
            """SELECT PAYMENT_ID FROM data_mart.FACT_PAYMENT
               WHERE PAYMENT_ID NOT IN %s
               ORDER BY RANDOM() LIMIT %s""",
            (tuple(targets) or (-1,), n - len(targets)),
        )
        targets.extend(r[0] for r in cur.fetchall())

    if not targets:
        print("[updates] no rows found to update")
        return 0

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Mostly flip to APPROVED with new approval_time; rest stay PENDING but
    # still get the watermark bump (e.g. metadata change).
    cur.execute(
        """UPDATE data_mart.FACT_PAYMENT
           SET STATUS = CASE WHEN random() < 0.85 THEN 'APPROVED' ELSE STATUS END,
               APPROVAL_TIME = CASE
                   WHEN STATUS = 'PENDING' AND random() < 0.85 THEN %s
                   ELSE APPROVAL_TIME
               END,
               LAST_UPDATED_TIME = %s
           WHERE PAYMENT_ID = ANY(%s)""",
        (now, now, targets),
    )
    affected = cur.rowcount
    conn.commit()
    cur.close()
    print(f"[updates] ~{affected:,} payments touched  "
          f"last_updated_time = {now.isoformat()}  label={run_label}")
    return affected


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args. Exactly one of --inserts/--updates/--mixed is
    required so the caller's intent is explicit; `--label` lets you tie
    a batch back to a test name in logs; `--seed` makes synthetic data
    reproducible across runs."""
    p = argparse.ArgumentParser(
        description="Inject incremental test data into Postgres"
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--inserts", type=int, help="N new payments")
    g.add_argument("--updates", type=int, help="N existing payments to update")
    g.add_argument("--mixed", type=int,
                   help="Total rows; ~50/50 split between inserts and updates")
    p.add_argument("--seed", type=int, default=None,
                   help="Random seed for reproducible synthetic data")
    p.add_argument("--label", default=None,
                   help="Free-form label printed to the log "
                        "(handy for tying changes to a test name)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Inject rows into Postgres according to the chosen mode.

    Exits 0 on success. Errors propagate naturally as psycopg2
    exceptions — there's no recovery to do because this script makes no
    promises beyond "the insert/update either committed or it didn't."
    """
    args = parse_args(argv)
    if args.seed is not None:
        random.seed(args.seed)

    label = args.label or datetime.now(timezone.utc).strftime("test-%Y%m%dT%H%M%SZ")
    conn = connect()
    try:
        if args.inserts:
            insert_new_payments(conn, args.inserts, label)
        elif args.updates:
            update_existing_payments(conn, args.updates, label)
        elif args.mixed:
            half = args.mixed // 2
            insert_new_payments(conn, half, label)
            update_existing_payments(conn, args.mixed - half, label)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
