"""
Seed the production-shape data mart described in
``examples/sample_datamart.ddl.sql``.

Scope: this seeder populates only the tables that are NOT already
populated by INSERT statements in the DDL itself. Concretely:

  SEEDS:
    - dim_account                 (~100 rows)
    - dim_currency                (~10 rows)
    - fact_as_balance             (~days × accounts × 3 currencies)
    - fact_as_transaction         (~days × accounts × N transactions/day)
    - fact_as_currency_exchange   (~days × C(currencies, 2) rate pairs)
    - fact_pay_payment            (~days × N payments/day)

  SKIPS (already seeded by the DDL's INSERTs):
    dim_date, dim_as_characteristics, dim_data_owner, dim_user,
    dim_classification, dim_routing, dim_as_transaction_type,
    dim_pay_characteristics, dim_pay_bank_status

The script is idempotent on dimensions (uses ``ON CONFLICT DO NOTHING``
on the natural key) and append-only on facts. Pass ``--reset`` to
truncate facts before inserting if you want a clean re-seed.

Requirements:
    pip install psycopg2-binary

Usage:
    python examples/seed_full_datamart.py
    python examples/seed_full_datamart.py --days 7 --accounts 50
    python examples/seed_full_datamart.py --reset

Environment variables for non-default connections:
    PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD
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
    print(
        "ERROR: psycopg2 not installed. Run: pip install psycopg2-binary",
        file=sys.stderr,
    )
    sys.exit(1)


# --------------------------------------------------------------------------- #
# Reference data — currencies, country codes, classifications
# --------------------------------------------------------------------------- #

CURRENCIES = [
    # (code, name, numeric_code, decimals, major_unit, minor_unit, is_default)
    ("USD", "US Dollar",          "840", 2, "Dollar", "Cent",     "Y"),
    ("EUR", "Euro",               "978", 2, "Euro",   "Cent",     "N"),
    ("GBP", "Pound Sterling",     "826", 2, "Pound",  "Penny",    "N"),
    ("JPY", "Yen",                "392", 0, "Yen",    None,       "N"),
    ("CHF", "Swiss Franc",        "756", 2, "Franc",  "Rappen",   "N"),
    ("AUD", "Australian Dollar",  "036", 2, "Dollar", "Cent",     "N"),
    ("CAD", "Canadian Dollar",    "124", 2, "Dollar", "Cent",     "N"),
    ("SGD", "Singapore Dollar",   "702", 2, "Dollar", "Cent",     "N"),
    ("HKD", "Hong Kong Dollar",   "344", 2, "Dollar", "Cent",     "N"),
    ("INR", "Indian Rupee",       "356", 2, "Rupee",  "Paisa",    "N"),
]

# (country_code, country_name, country_group_code, country_group_name, bank_bic)
COUNTRIES = [
    ("US", "United States",   "AMERICAS", "Americas",      "CHASUS33"),
    ("GB", "United Kingdom",  "EU",       "Europe",        "BARCGB22"),
    ("DE", "Germany",         "EU",       "Europe",        "DEUTDEFF"),
    ("FR", "France",          "EU",       "Europe",        "BNPAFRPP"),
    ("CH", "Switzerland",     "EU_NON_EU","Europe Non-EU", "UBSWCHZH"),
    ("JP", "Japan",           "APAC",     "Asia Pacific",  "BOTKJPJT"),
    ("SG", "Singapore",       "APAC",     "Asia Pacific",  "DBSSSGSG"),
    ("HK", "Hong Kong",       "APAC",     "Asia Pacific",  "HSBCHKHH"),
    ("AU", "Australia",       "APAC",     "Asia Pacific",  "NATAAU33"),
    ("CA", "Canada",          "AMERICAS", "Americas",      "ROYCCAT2"),
    ("BR", "Brazil",           "AMERICAS","Americas",      "BOFABRSP"),
    ("MX", "Mexico",          "AMERICAS", "Americas",      "BNMXMXMM"),
    ("IN", "India",           "APAC",     "Asia Pacific",  "HDFCINBB"),
    ("AE", "United Arab Emirates","MEA",  "Middle East & Africa", "EBILAEAD"),
    ("ZA", "South Africa",    "MEA",      "Middle East & Africa", "SBZAZAJJ"),
]


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #

def connect():
    """Open a psycopg2 connection. Auto-detects in-container vs host.

    Pins ``search_path`` to ``data_mart, public`` at the protocol level so
    every query inside this script resolves bare table names like
    ``fact_pay_payment`` even if the database's default search_path hasn't
    been set via ``ALTER DATABASE`` (older installs that pre-date the
    init.sql change). ``options="-c key=value"`` is libpq's standard way
    to feed startup parameters into a Postgres session.
    """
    default_host = "postgres" if os.path.exists("/.dockerenv") else "localhost"
    return psycopg2.connect(
        host=os.environ.get("PGHOST", default_host),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "data_mart"),
        user=os.environ.get("PGUSER", "strata"),
        password=os.environ.get("PGPASSWORD", "strata"),
        options="-c search_path=data_mart,public",
    )


def table_count(cur, table: str) -> int:
    """Return COUNT(*) for the given fully-qualified or bare table name."""
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# Dimension seeders — idempotent
# --------------------------------------------------------------------------- #

def seed_dim_currency(cur) -> int:
    """Insert the currency reference data; no-op if already populated.

    Uses ON CONFLICT on the natural key (code) so re-runs are safe even
    if the table has additional currencies from another source.
    """
    if table_count(cur, "dim_currency") > 0:
        print("  → dim_currency already populated, skipping")
        return 0

    rows = [
        (code, name, num, dec, maj, minor, default, datetime.now())
        for (code, name, num, dec, maj, minor, default) in CURRENCIES
    ]
    execute_values(
        cur,
        """INSERT INTO dim_currency
           (code, name, numeric_code, number_of_decimals,
            major_unit_name, minor_unit_name, is_default_currency,
            last_updated_time)
           VALUES %s
           ON CONFLICT (code) DO NOTHING""",
        rows,
    )
    print(f"  → dim_currency: inserted {len(rows)} currencies")
    return len(rows)


def seed_dim_account(cur, n_accounts: int) -> int:
    """Create N synthetic accounts with realistic bank + holder fields.

    Re-running with a higher --accounts won't duplicate existing rows
    (the script tops up rather than truncating); pass --reset to start
    over.
    """
    existing = table_count(cur, "dim_account")
    if existing >= n_accounts:
        print(f"  → dim_account already has {existing} rows (≥{n_accounts}), skipping")
        return 0

    needed = n_accounts - existing
    cur.execute("SELECT id FROM dim_data_owner ORDER BY id")
    data_owner_ids = [r[0] for r in cur.fetchall()] or [1]

    rows = []
    for i in range(needed):
        cc, cn, cgc, cgn, bic = random.choice(COUNTRIES)
        ccy, ccy_name, *_ = random.choice(CURRENCIES)
        bank_idx = random.randint(1, 50)
        holder_idx = random.randint(1, 500)
        account_no = f"AC{random.randint(10_000_000, 99_999_999)}"
        rows.append((
            random.choice(data_owner_ids),                      # data_owner_id
            f"ACCT-{existing + i + 1:06d}",                     # code
            f"Account {existing + i + 1}",                      # name
            f"Synthetic account #{existing + i + 1}",           # description
            account_no,                                         # account_number
            ccy,                                                # currency
            "G1", "Group 1",                                    # currency_group_code/name
            "OP", "Operational",                                # type_code/name
            account_no,                                         # identifier
            "IBAN" if cc in ("GB", "DE", "FR", "CH") else "BBA",# identifier_type_code
            "International Bank Account Number" if cc in ("GB","DE","FR","CH")
              else "Basic Bank Account",                        # identifier_type_name
            "G1", "Group 1",                                    # group_code/name
            "BG1", f"Bank Group {bank_idx % 5}",                # bank_group_code/name
            f"B{bank_idx:03d}", f"Bank {bank_idx}",              # bank_code/name
            "COM", "Commercial",                                # bank_type_code/name
            f"BANKID-{bank_idx:05d}",                           # bank_identifier
            "BIC", "Business Identifier Code",                  # bank_identifier_type_code/name
            f"{bank_idx * 7} Bank Street",                      # bank_street
            str(bank_idx),                                       # bank_street_number
            f"{10000 + bank_idx}",                              # bank_postal_code
            cn,                                                  # bank_city
            cn,                                                  # bank_state
            cc, cn, cgc, cgn,                                    # bank_country_*
            bic,                                                 # bank_bic
            f"CL{bank_idx:04d}", f"Clearing System {cc}",       # bank_clearing_code/system
            "HG1", "Holder Group 1",                            # holder_group_code/name
            f"H{holder_idx:04d}", f"Holder {holder_idx}",       # holder_code/name
            "CORP", "Corporate",                                # holder_type_code/name
            f"HID-{holder_idx:05d}",                            # holder_identifier
            "LEI", "Legal Entity Identifier",                   # holder_identifier_type_code/name
            f"{holder_idx} Main Street",                         # holder_street
            str(holder_idx),                                     # holder_street_number
            f"{20000 + holder_idx}",                            # holder_postal_code
            cn,                                                  # holder_city
            cn,                                                  # holder_state
            cc, cn, cgc, cgn,                                    # holder_country_*
            bic[:8] + "01",                                      # holder_bic
            date(2020, 1, 1) + timedelta(days=random.randint(0, 1500)),  # opening_date
            None,                                                # closing_date (open accounts)
            None, None, None, None, None,                       # custom_text1-5
            datetime.now(),                                      # last_updated_time
        ))

    execute_values(
        cur,
        """INSERT INTO dim_account (
            data_owner_id, code, name, description, account_number,
            currency, currency_group_code, currency_group_name,
            type_code, type_name, identifier,
            identifier_type_code, identifier_type_name,
            group_code, group_name,
            bank_group_code, bank_group_name, bank_code, bank_name,
            bank_type_code, bank_type_name, bank_identifier,
            bank_identifier_type_code, bank_identifier_type_name,
            bank_street, bank_street_number, bank_postal_code,
            bank_city, bank_state,
            bank_country_code, bank_country_name,
            bank_country_group_code, bank_country_group_name,
            bank_bic, bank_clearing_code, bank_clearing_system,
            holder_group_code, holder_group_name, holder_code, holder_name,
            holder_type_code, holder_type_name, holder_identifier,
            holder_identifier_type_code, holder_identifier_type_name,
            holder_street, holder_street_number, holder_postal_code,
            holder_city, holder_state,
            holder_country_code, holder_country_name,
            holder_country_group_code, holder_country_group_name,
            holder_bic, opening_date, closing_date,
            custom_text1, custom_text2, custom_text3, custom_text4, custom_text5,
            last_updated_time
        ) VALUES %s""",
        rows,
        page_size=200,
    )
    print(f"  → dim_account: inserted {needed} accounts (now {existing + needed} total)")
    return needed


# --------------------------------------------------------------------------- #
# Fact seeders — append-only
# --------------------------------------------------------------------------- #

def _get_fk_pools(cur) -> dict:
    """Cache the FK ID pools we need across fact tables."""
    pools = {}
    for tbl in ("dim_account", "dim_currency", "dim_date", "dim_data_owner",
                "dim_classification", "dim_routing", "dim_as_transaction_type",
                "dim_pay_characteristics", "dim_pay_bank_status", "dim_user"):
        cur.execute(f"SELECT id FROM {tbl}")
        pools[tbl] = [r[0] for r in cur.fetchall()]
    return pools


def seed_fact_as_balance(cur, pools: dict, days: int, per_account: int = 3) -> int:
    """Daily balances per account in their N most-used currencies."""
    accts = pools["dim_account"]
    ccys = pools["dim_currency"]
    dates = pools["dim_date"]
    owners = pools["dim_data_owner"] or [1]
    routings = pools["dim_routing"] or [1]
    bank_statuses = pools["dim_pay_bank_status"] or [1]
    if not accts or not ccys or not dates:
        print("  ! fact_as_balance: missing FK data, skipping")
        return 0

    now = datetime.now()
    rows = []
    # Take the most recent N dates from dim_date
    sample_dates = dates[-days:] if len(dates) > days else dates
    for d_id in sample_dates:
        for acct_id in accts:
            for ccy_id in random.sample(ccys, min(per_account, len(ccys))):
                opening = Decimal(str(round(random.uniform(10_000, 5_000_000), 2)))
                rows.append((
                    random.choice(bank_statuses),               # balance_type_id
                    acct_id,                                    # account_id
                    ccy_id,                                     # currency_id
                    d_id,                                       # balance_date_id
                    random.choice(owners),                      # data_owner_id
                    random.choice(routings),                    # routing_id
                    opening,                                    # balance_amount
                    opening,                                    # amount_in_default_currency
                    Decimal("1.0000000000"),                   # fx_rate_of_default_currency
                    date.today() - timedelta(days=random.randint(0, days)),  # fx_rate_date
                    random.randint(1, 99999),                   # statement_number
                    random.randint(1, 9999),                    # sequence_number
                    None, None,                                 # account_statement_id, bank_transaction_id
                    None, None, None, None, None,               # custom_text1-5
                    now,                                         # last_updated_time
                ))

    if not rows:
        return 0
    execute_values(
        cur,
        """INSERT INTO fact_as_balance (
            balance_type_id, account_id, currency_id, balance_date_id,
            data_owner_id, routing_id, balance_amount,
            amount_in_default_currency, fx_rate_of_default_currency,
            fx_rate_date, statement_number, sequence_number,
            account_statement_id, bank_transaction_id,
            custom_text1, custom_text2, custom_text3, custom_text4, custom_text5,
            last_updated_time
        ) VALUES %s""",
        rows,
        page_size=500,
    )
    print(f"  → fact_as_balance: inserted {len(rows):,} balances")
    return len(rows)


def seed_fact_as_transaction(cur, pools: dict, days: int, per_day: int = 200) -> int:
    """Daily transactions across accounts. Volume is per_day total."""
    accts = pools["dim_account"]
    ccys = pools["dim_currency"]
    dates = pools["dim_date"]
    owners = pools["dim_data_owner"] or [1]
    classifications = pools["dim_classification"] or [1]
    routings = pools["dim_routing"] or [1]
    txn_types = pools["dim_as_transaction_type"] or [1]
    if not accts or not ccys or not dates:
        print("  ! fact_as_transaction: missing FK data, skipping")
        return 0

    now = datetime.now()
    sample_dates = dates[-days:] if len(dates) > days else dates
    rows = []
    for d_id in sample_dates:
        for _ in range(per_day):
            amt = Decimal(str(round(random.uniform(10, 100_000), 2)))
            rows.append((
                random.choice(accts),                           # account_id
                random.choice(ccys),                            # currency_id
                d_id,                                           # value_date_id
                random.choice(owners),                          # data_owner_id
                random.choice([0, 1]),                          # intraday_id
                random.choice(classifications),                 # classification_id
                random.choice(routings),                        # routing_id
                random.choice(txn_types),                       # transaction_type_id
                amt,                                            # amount
                amt,                                            # amount_in_default_currency
                Decimal("1.0000000000"),                       # fx_rate_of_default_currency
                date.today() - timedelta(days=random.randint(0, days)),  # fx_rate_date
                now - timedelta(seconds=random.randint(0, 86400)),       # creation_date_time
                date.today() - timedelta(days=random.randint(0, days)),  # entry_date
                f"OWN-{random.randint(1_000_000, 9_999_999)}",  # account_owner_ref
                f"SRV-{random.randint(1_000_000, 9_999_999)}",  # account_servicer_ref
                "Auto-generated transaction",                    # supplementary_details
                random.choice(["DBIT", "CRDT"]),                # debit_credit_mark
                random.randint(1, 99999),                       # statement_number
                random.randint(1, 9999),                        # sequence_number
                None, None,                                     # bank_transaction_id, account_statement_id
                None, None, None, None, None,                   # custom_text1-5
                now,                                             # last_updated_time
            ))
    if not rows:
        return 0
    execute_values(
        cur,
        """INSERT INTO fact_as_transaction (
            account_id, currency_id, value_date_id, data_owner_id,
            intraday_id, classification_id, routing_id, transaction_type_id,
            amount, amount_in_default_currency, fx_rate_of_default_currency,
            fx_rate_date, creation_date_time, entry_date,
            account_owner_ref, account_servicer_ref, supplementary_details,
            debit_credit_mark, statement_number, sequence_number,
            bank_transaction_id, account_statement_id,
            custom_text1, custom_text2, custom_text3, custom_text4, custom_text5,
            last_updated_time
        ) VALUES %s""",
        rows,
        page_size=500,
    )
    print(f"  → fact_as_transaction: inserted {len(rows):,} transactions")
    return len(rows)


def seed_fact_as_currency_exchange(cur, pools: dict, days: int) -> int:
    """Daily exchange rate per currency pair (excludes self-pairs)."""
    ccys = pools["dim_currency"]
    dates = pools["dim_date"]
    owners = pools["dim_data_owner"] or [1]
    if not ccys or not dates:
        print("  ! fact_as_currency_exchange: missing FK data, skipping")
        return 0

    now = datetime.now()
    sample_dates = dates[-days:] if len(dates) > days else dates
    rows = []
    for d_id in sample_dates:
        owner = random.choice(owners)
        for from_id in ccys:
            for to_id in ccys:
                if from_id == to_id:
                    continue
                rate = Decimal(str(round(random.uniform(0.1, 150.0), 10)))
                rows.append((
                    owner, d_id, from_id, to_id, rate, now,
                ))
    if not rows:
        return 0
    execute_values(
        cur,
        """INSERT INTO fact_as_currency_exchange (
            data_owner_id, rate_date_id, from_currency_id, to_currency_id,
            exchange_rate, last_updated_time
        ) VALUES %s""",
        rows,
        page_size=500,
    )
    print(f"  → fact_as_currency_exchange: inserted {len(rows):,} rates")
    return len(rows)


def seed_fact_pay_payment(cur, pools: dict, days: int, per_day: int = 100) -> int:
    """Daily payments with full holder/counterparty context."""
    accts = pools["dim_account"]
    ccys = pools["dim_currency"]
    dates = pools["dim_date"]
    owners = pools["dim_data_owner"] or [1]
    classifications = pools["dim_classification"] or [1]
    routings = pools["dim_routing"] or [1]
    chars = pools["dim_pay_characteristics"] or [1]
    statuses = pools["dim_pay_bank_status"] or [1]
    users = pools["dim_user"] or [1]
    if not accts or not ccys or not dates:
        print("  ! fact_pay_payment: missing FK data, skipping")
        return 0

    now = datetime.now()
    sample_dates = dates[-days:] if len(dates) > days else dates
    rows = []
    for _ in sample_dates:
        for _ in range(per_day):
            cc, cn, cgc, cgn, bic = random.choice(COUNTRIES)
            amt = Decimal(str(round(random.uniform(100, 500_000), 2)))
            creation = now - timedelta(
                days=random.randint(0, days),
                seconds=random.randint(0, 86400),
            )
            rows.append((
                random.choice(accts),                       # ordering_account_id
                random.randint(1, 99999),                   # requesting_execution_date (bigint per DDL)
                random.choice([1, 2, 3, 4, 5]),             # payment_method_id (assumed range)
                random.choice(ccys),                        # currency_id
                random.choice(owners),                      # data_owner_id
                random.choice(classifications),             # classification_id
                random.choice(chars),                       # characteristics_id
                random.choice(users),                       # first_approver_id
                random.choice(users),                       # second_approver_id
                random.choice(statuses),                    # bank_status_id
                random.choice(routings),                    # routing_id
                amt,                                        # amount
                amt,                                        # amount_in_default_currency
                Decimal("1.0000000000"),                   # fx_rate_of_default_currency
                f"AC{random.randint(10_000_000, 99_999_999)}",  # counter_account_number
                f"Counter Bank {random.randint(1, 100)}",   # counter_bank_name
                cc, cn,                                     # counter_bank_country_*
                cc, cn, cgc, cgn,                           # counterparty_country_*
                random.randint(1, 99999),                   # own_reference_id
                random.randint(1, 9999),                    # own_reference_batch_id
                f"REF-{random.randint(1_000_000, 9_999_999)}",  # own_reference_first_id
                None, None,                                 # own_reference_second/third_id
                creation,                                    # amount_format_date
                creation + timedelta(minutes=30),           # amount_approval_date
                creation + timedelta(hours=1),              # amount_release_date
                creation + timedelta(minutes=15),           # first_approval_date
                creation + timedelta(minutes=25),           # second_approval_date
                creation + timedelta(minutes=10),           # first_signed_date
                creation + timedelta(minutes=20),           # second_signed_date
                creation,                                    # creation_date
                random.choice(users),                        # creation_user_id
                None,                                        # rejection_reason_description
                None, None, None, None, None,                # custom_text1-5
                now,                                         # last_updated_time
            ))
    if not rows:
        return 0
    execute_values(
        cur,
        """INSERT INTO fact_pay_payment (
            ordering_account_id, requesting_execution_date,
            payment_method_id, currency_id, data_owner_id,
            classification_id, characteristics_id,
            first_approver_id, second_approver_id, bank_status_id,
            routing_id, amount, amount_in_default_currency,
            fx_rate_of_default_currency,
            counter_account_number, counter_bank_name,
            counter_bank_country_code, counter_bank_country_name,
            counterparty_country_code, counterparty_country_name,
            counterparty_country_group_code, counterparty_country_group_name,
            own_reference_id, own_reference_batch_id,
            own_reference_first_id, own_reference_second_id, own_reference_third_id,
            amount_format_date, amount_approval_date, amount_release_date,
            first_approval_date, second_approval_date,
            first_signed_date, second_signed_date,
            creation_date, creation_user_id, rejection_reason_description,
            custom_text1, custom_text2, custom_text3, custom_text4, custom_text5,
            last_updated_time
        ) VALUES %s""",
        rows,
        page_size=300,
    )
    print(f"  → fact_pay_payment: inserted {len(rows):,} payments")
    return len(rows)


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

def truncate_facts(cur) -> None:
    """Empty the four fact tables. Called when --reset is passed."""
    for tbl in ("fact_pay_payment", "fact_as_currency_exchange",
                "fact_as_transaction", "fact_as_balance"):
        cur.execute(f"TRUNCATE TABLE {tbl} CASCADE")
    print("  → truncated all 4 fact tables")


def print_summary(cur) -> None:
    """Print row count + max watermark for every table — both ones we seed
    and ones populated by the DDL's INSERTs/DO blocks. Gives the operator
    a complete view of what's in the data mart after setup."""
    print()
    print("Database summary:")
    all_tables = [
        # Dims populated by the DDL (INSERTs or DO block)
        "dim_date", "dim_data_owner", "dim_user", "dim_classification",
        "dim_routing", "dim_as_transaction_type", "dim_as_characteristics",
        "dim_pay_bank_status", "dim_pay_characteristics",
        # Dims populated by this seeder
        "dim_currency", "dim_account",
        # Facts populated by this seeder
        "fact_as_currency_exchange", "fact_as_balance",
        "fact_as_transaction", "fact_pay_payment",
    ]
    for tbl in all_tables:
        try:
            # Some dims (dim_date, dim_pay_*, dim_as_characteristics) have no
            # last_updated_time column — fall back to a plain count.
            cur.execute(f"""
                SELECT COUNT(*),
                       CASE WHEN EXISTS (
                         SELECT 1 FROM information_schema.columns
                         WHERE table_name = '{tbl}' AND column_name = 'last_updated_time'
                       ) THEN (SELECT MAX(last_updated_time)::text FROM {tbl})
                       ELSE NULL END
                FROM {tbl}
            """)
            n, wm = cur.fetchone()
            wm_str = f"max(last_updated_time)={wm}" if wm else "(no watermark column)"
            print(f"  {tbl:32s} rows={n:>10,}  {wm_str}")
        except psycopg2.Error as exc:
            print(f"  {tbl:32s} ERROR: {exc}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args. Volumes scale linearly with --days and per-day counts."""
    p = argparse.ArgumentParser(
        description="Seed the production-shape data mart with synthetic data"
    )
    p.add_argument("--days", type=int, default=30,
                   help="Days of history to generate (default: 30)")
    p.add_argument("--accounts", type=int, default=100,
                   help="Number of dim_account rows to maintain (default: 100)")
    p.add_argument("--txns-per-day", type=int, default=200,
                   help="Transactions per day (default: 200)")
    p.add_argument("--payments-per-day", type=int, default=100,
                   help="Payments per day (default: 100)")
    p.add_argument("--reset", action="store_true",
                   help="Truncate all 4 facts before inserting")
    p.add_argument("--seed", type=int, default=None,
                   help="Random seed for reproducible synthetic data")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run all seeders in dim-then-fact order. Always returns 0 on success."""
    args = parse_args(argv)
    if args.seed is not None:
        random.seed(args.seed)

    conn = connect()
    cur = conn.cursor()
    try:
        if args.reset:
            print("[reset] truncating facts...")
            truncate_facts(cur)
            conn.commit()

        print("[1/2] Seeding dimensions...")
        seed_dim_currency(cur)
        seed_dim_account(cur, args.accounts)
        conn.commit()

        print("[2/2] Seeding facts...")
        pools = _get_fk_pools(cur)
        seed_fact_as_balance(cur, pools, args.days)
        seed_fact_as_transaction(cur, pools, args.days, args.txns_per_day)
        seed_fact_as_currency_exchange(cur, pools, args.days)
        seed_fact_pay_payment(cur, pools, args.days, args.payments_per_day)
        conn.commit()

        print_summary(cur)
        return 0
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
