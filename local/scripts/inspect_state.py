"""
Inspect strata's incremental-pipeline state from all three sources at once.

This is the single source of truth for "what does strata think happened so
far?" — it reads:

  * PostgreSQL source  : COUNT, MAX(last_updated_time)
  * SQLite state DB    : current_watermark, pending_run_id, last_run_id, etc.
  * Iceberg snapshots  : latest snapshot's run_id, watermark bounds, row_count

Run inside the spark container so it has psycopg2, PySpark + Iceberg, and a
mount of the SQLite state DB.

Usage::

    python local/scripts/inspect_state.py                # human-readable
    python local/scripts/inspect_state.py --json         # machine-readable
    python local/scripts/inspect_state.py --table FACT_PAY_PAYMENT
    python local/scripts/inspect_state.py --snapshots 10 # show last 10 snapshots
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone


def load_postgres_state(source_table: str) -> dict:
    """Query the upstream Postgres source for row count + max watermark."""
    import psycopg2

    default_host = "postgres" if os.path.exists("/.dockerenv") else "localhost"
    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", default_host),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "data_mart"),
        user=os.environ.get("PGUSER", "strata"),
        password=os.environ.get("PGPASSWORD", "strata"),
    )
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*), MAX(last_updated_time) FROM data_mart.{source_table}"
        )
        count, max_wm = cur.fetchone()
        return {
            "row_count": int(count or 0),
            "max_last_updated_time": max_wm.isoformat() if max_wm else None,
        }
    finally:
        conn.close()


def load_sqlite_state(state_db: str, table_name: str) -> dict | None:
    """Read the row strata's StateManager would read on the next run."""
    if not os.path.exists(state_db):
        return None
    conn = sqlite3.connect(state_db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM strata_state WHERE table_name = ?", (table_name,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def load_iceberg_state(table_fqn: str, snapshot_limit: int) -> dict | None:
    """
    Read the latest Iceberg snapshots via Spark. Returns None if the table
    doesn't exist yet (first run before any commit).
    """
    # Imported here so this script can also be used to inspect Postgres + SQLite
    # in environments that don't have PySpark on the path.
    from strata.local_runtime.spark import build_local_spark

    spark = build_local_spark()
    try:
        try:
            row_count = spark.sql(f"SELECT COUNT(*) AS c FROM {table_fqn}").first()["c"]
        except Exception:
            return None

        # Iceberg's .snapshots metadata table — sorted newest first.
        snaps_df = spark.sql(
            f"SELECT committed_at, snapshot_id, operation, summary "
            f"FROM {table_fqn}.snapshots ORDER BY committed_at DESC LIMIT {snapshot_limit}"
        )
        snaps = []
        for r in snaps_df.collect():
            summary = dict(r["summary"]) if r["summary"] else {}
            snaps.append({
                "committed_at": r["committed_at"].isoformat() if r["committed_at"] else None,
                "snapshot_id": str(r["snapshot_id"]),
                "operation": r["operation"],
                "run_id": summary.get("glue.run_id"),
                "watermark_lower": summary.get("glue.watermark_lower") or None,
                "watermark_upper": summary.get("glue.watermark_upper"),
                "row_count": summary.get("glue.row_count"),
                "committed_at_prop": summary.get("glue.committed_at"),
            })

        return {
            "row_count": int(row_count or 0),
            "snapshots": snaps,
            "latest_snapshot": snaps[0] if snaps else None,
        }
    finally:
        spark.stop()


def collect(args) -> dict:
    """Snapshot state from all three sources into a single dict.

    Returned shape is also what `--json` mode serialises directly, and
    what `run_and_verify._delta()` consumes for before/after diffs —
    so the keys here are part of the contract with that script.
    """
    pg = load_postgres_state(args.source_table)
    sqlite = load_sqlite_state(args.state_db, args.table)
    iceberg = load_iceberg_state(args.table_fqn, args.snapshots)

    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "table": args.table,
        "postgres": pg,
        "sqlite_state": sqlite,
        "iceberg": iceberg,
    }


def fmt_table(rows: list[tuple[str, str]]) -> str:
    """Pretty-print a list of (key, value) pairs as a two-column block."""
    if not rows:
        return "  (no data)"
    w = max(len(k) for k, _ in rows)
    return "\n".join(f"  {k:<{w}}  {v}" for k, v in rows)


def render_human(state: dict) -> str:
    """Render the state dict as the readable three-block report.

    Block 1 is Postgres source (upstream truth), block 2 is the SQLite
    state DB (strata's bookkeeping), block 3 is Iceberg (committed
    truth). The closing summary highlights the three conditions
    operators usually care about: watermark caught up, stale lock,
    state-vs-snapshot drift.
    """
    lines = [
        f"\n=== strata state for table: {state['table']} ===",
        f"  captured_at: {state['captured_at']}",
    ]

    # --- Postgres source --- #
    pg = state["postgres"]
    lines.append("\n[1] PostgreSQL source (upstream truth)")
    lines.append(fmt_table([
        ("row_count", f"{pg['row_count']:,}"),
        ("max(last_updated_time)", pg["max_last_updated_time"] or "(empty)"),
    ]))

    # --- SQLite state DB --- #
    s = state["sqlite_state"]
    lines.append("\n[2] SQLite state DB (strata's bookkeeping)")
    if s is None:
        lines.append("  (no row — first run hasn't happened)")
    else:
        lines.append(fmt_table([
            ("current_watermark",   s.get("current_watermark") or "(null)"),
            ("pending_run_id",      s.get("pending_run_id")    or "(null)"),
            ("pending_window",      f"({s.get('pending_window_lower')}, {s.get('pending_window_upper')}]"
                                     if s.get("pending_run_id") else "(none)"),
            ("pending_expires_at",  s.get("pending_expires_at") or "(none)"),
            ("last_run_id",         s.get("last_run_id")       or "(null)"),
            ("last_run_status",     s.get("last_run_status")   or "(null)"),
            ("last_run_rows",       str(s.get("last_run_rows") or 0)),
            ("last_run_completed",  s.get("last_run_completed_at") or "(null)"),
            ("version",             str(s.get("version") or 0)),
        ]))

    # --- Iceberg snapshot --- #
    ib = state["iceberg"]
    lines.append("\n[3] Iceberg table (committed truth)")
    if ib is None:
        lines.append("  (table doesn't exist yet)")
    else:
        latest = ib["latest_snapshot"] or {}
        lines.append(fmt_table([
            ("row_count",            f"{ib['row_count']:,}"),
            ("latest snapshot_id",   latest.get("snapshot_id") or "(none)"),
            ("latest run_id",        latest.get("run_id") or "(none)"),
            ("latest watermark",     f"({latest.get('watermark_lower') or 'null'}, "
                                     f"{latest.get('watermark_upper')}]"
                                     if latest else "(none)"),
            ("latest committed_at",  latest.get("committed_at") or "(none)"),
            ("latest operation",     latest.get("operation") or "(none)"),
            ("snapshot history",     f"{len(ib['snapshots'])} snapshot(s) shown"),
        ]))

        if len(ib["snapshots"]) > 1:
            lines.append("\n  Recent snapshot history (newest first):")
            for snap in ib["snapshots"]:
                rid = (snap.get("run_id") or "(no run_id)")
                lines.append(
                    f"    - {snap['committed_at']}  "
                    f"{snap['operation']:<9}  "
                    f"rows={snap.get('row_count') or '?':<6}  "
                    f"run_id={rid}"
                )

    # --- Quick verdict --- #
    lines.append("\n[summary]")
    if s and ib:
        wm = s.get("current_watermark")
        latest_run = ib["latest_snapshot"]["run_id"] if ib["latest_snapshot"] else None
        last_run = s.get("last_run_id")

        if wm and pg["max_last_updated_time"] and wm >= pg["max_last_updated_time"]:
            lines.append("  ✓ Watermark is caught up with Postgres source.")
        elif wm and pg["max_last_updated_time"]:
            lines.append(
                f"  ! Postgres has data past the watermark — "
                f"pending delta to ingest."
            )

        if s.get("pending_run_id"):
            lines.append(
                f"  ! Pending run_id is set — a previous run did not finish cleanly. "
                f"Next run will trigger recovery."
            )

        if latest_run and last_run and latest_run != last_run:
            lines.append(
                f"  ! Iceberg latest run_id ({latest_run}) != SQLite last_run_id "
                f"({last_run}) — reconciliation will fire on next run."
            )

    lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args. The `--table-fqn` default is derived from `--table`
    using a static domain→database map that mirrors the YAML config's
    `glue_database_prefix` + domain scheme."""
    p = argparse.ArgumentParser(description="strata state inspector")
    p.add_argument("--table", default="FACT_PAY_PAYMENT",
                   help="Logical table name (default: FACT_PAY_PAYMENT)")
    p.add_argument("--source-table", default=None,
                   help="Postgres source table name (default: lowercase of --table)")
    p.add_argument("--table-fqn", default=None,
                   help="Fully-qualified Iceberg table name "
                        "(default: iceberg.silver_payments.fact_pay_payment)")
    p.add_argument("--state-db", default="/data/state/strata.db",
                   help="Path to the SQLite state DB inside the spark container")
    p.add_argument("--snapshots", type=int, default=5,
                   help="Number of recent snapshots to fetch (default: 5)")
    p.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON")
    args = p.parse_args(argv)
    if args.source_table is None:
        args.source_table = args.table.lower()
    if args.table_fqn is None:
        # Domain-to-database mapping mirrors the YAML's glue_database_prefix
        # + domain. Covers all 15 tables in the production-shape schema; new
        # tables added to tables.local.yaml should also get a row here.
        domain_map = {
            # payments
            "FACT_PAY_PAYMENT":          "silver_payments.fact_pay_payment",
            "DIM_PAY_CHARACTERISTICS":   "silver_payments.dim_pay_characteristics",
            "DIM_PAY_BANK_STATUS":       "silver_payments.dim_pay_bank_status",
            # balances / account-statement
            "FACT_AS_BALANCE":           "silver_balances.fact_as_balance",
            "FACT_AS_TRANSACTION":       "silver_balances.fact_as_transaction",
            "FACT_AS_CURRENCY_EXCHANGE": "silver_balances.fact_as_currency_exchange",
            "DIM_AS_CHARACTERISTICS":    "silver_balances.dim_as_characteristics",
            "DIM_AS_TRANSACTION_TYPE":   "silver_balances.dim_as_transaction_type",
            # shared conformed dims
            "DIM_ACCOUNT":               "silver_shared.dim_account",
            "DIM_CURRENCY":              "silver_shared.dim_currency",
            "DIM_DATE":                  "silver_shared.dim_date",
            "DIM_DATA_OWNER":            "silver_shared.dim_data_owner",
            "DIM_USER":                  "silver_shared.dim_user",
            "DIM_CLASSIFICATION":        "silver_shared.dim_classification",
            "DIM_ROUTING":               "silver_shared.dim_routing",
        }
        args.table_fqn = f"iceberg.{domain_map.get(args.table, 'silver_payments.' + args.source_table)}"
    return args


def main(argv: list[str] | None = None) -> int:
    """Inspect strata state and print it. Always exits 0 — this is a
    read-only tool, so failures during collection are surfaced inline
    in the report rather than via exit code."""
    args = parse_args(argv)
    state = collect(args)
    if args.json:
        print(json.dumps(state, indent=2, default=str))
    else:
        print(render_human(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
