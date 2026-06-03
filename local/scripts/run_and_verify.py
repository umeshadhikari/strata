"""
Runs ingest and diffs the state before/after so you can read the result at
a glance instead of squinting at logs.

This is the script you actually run inside a test loop. It:

  1. Snapshots state via inspect_state.collect()
  2. Shells out to ``python -m strata.local_ingest --table <T>``
  3. Snapshots state again
  4. Prints a delta: rows added, watermark advanced, new snapshot run_id

Run inside the spark container so it shares pyspark + the SQLite mount.

Usage::

    python local/scripts/run_and_verify.py
    python local/scripts/run_and_verify.py --table FACT_PAYMENT
    python local/scripts/run_and_verify.py --full-refresh
    python local/scripts/run_and_verify.py --expect-delta 100
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone

# Reuse the inspector to avoid two implementations drifting. The shell
# wrapper docker-cps both helpers to /tmp/strata_scripts, but for the
# alternative (mounted) layout we also check /app/local/scripts and the
# current file's directory.
import os as _os
for _p in ("/tmp/strata_scripts", "/app/local/scripts", _os.path.dirname(__file__)):
    if _os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
from inspect_state import collect, parse_args as inspect_parse_args  # noqa: E402


def _inspect(table: str) -> dict:
    """Snapshot state via the inspector's collect() — same dict shape
    the inspector emits with --json."""
    args = inspect_parse_args(["--table", table, "--snapshots", "3"])
    return collect(args)


def _run_ingest(table: str, full_refresh: bool) -> int:
    """Shell out to `python -m strata.local_ingest`. We run it as a
    subprocess (rather than calling local_ingest.main directly) so its
    Spark session is fully torn down before the post-inspect runs —
    otherwise we'd hit "two Spark sessions in one process" errors."""
    cmd = ["python", "-m", "strata.local_ingest", "--table", table]
    if full_refresh:
        cmd.append("--full-refresh")
    print(f"\n>>> ingest: {' '.join(cmd)}")
    return subprocess.call(cmd)


def _delta(before: dict, after: dict) -> dict:
    """Compute the human-meaningful diff between two state snapshots."""
    pg_b, pg_a = before["postgres"], after["postgres"]
    s_b, s_a = before["sqlite_state"] or {}, after["sqlite_state"] or {}
    ib_b, ib_a = before["iceberg"] or {}, after["iceberg"] or {}

    latest_b = (ib_b.get("latest_snapshot") or {}) if ib_b else {}
    latest_a = (ib_a.get("latest_snapshot") or {}) if ib_a else {}

    return {
        "postgres_rows_delta": pg_a["row_count"] - pg_b["row_count"],
        "iceberg_rows_delta": (ib_a.get("row_count") or 0) - (ib_b.get("row_count") or 0),
        "watermark_before": s_b.get("current_watermark"),
        "watermark_after":  s_a.get("current_watermark"),
        "watermark_advanced": (s_b.get("current_watermark") != s_a.get("current_watermark")),
        "new_run_id": (
            latest_a.get("run_id")
            if latest_a.get("run_id") != latest_b.get("run_id")
            else None
        ),
        "new_snapshot_id": (
            latest_a.get("snapshot_id")
            if latest_a.get("snapshot_id") != latest_b.get("snapshot_id")
            else None
        ),
        "new_snapshot_row_count": latest_a.get("row_count"),
        "last_run_status": s_a.get("last_run_status"),
        "last_run_rows":   s_a.get("last_run_rows"),
    }


def _render_delta(d: dict) -> str:
    """Format the delta dict as the readable seven-line report block."""
    lines = ["\n=== ingest delta ==="]

    arrow = "→" if d["watermark_advanced"] else "= (unchanged)"
    lines.append(f"  Postgres rows delta : +{d['postgres_rows_delta']:,}")
    lines.append(f"  Iceberg  rows delta : +{d['iceberg_rows_delta']:,}")
    lines.append(f"  Watermark           : {d['watermark_before']} {arrow} {d['watermark_after']}")
    lines.append(f"  New run_id          : {d['new_run_id'] or '(none — no new snapshot)'}")
    lines.append(f"  New snapshot_id     : {d['new_snapshot_id'] or '(none)'}")
    lines.append(f"  Snapshot row_count  : {d['new_snapshot_row_count'] or '(n/a)'}")
    lines.append(f"  last_run_status     : {d['last_run_status']}")
    lines.append(f"  last_run_rows       : {d['last_run_rows']}")
    return "\n".join(lines) + "\n"


def _check_expectations(d: dict, expect_delta: int | None) -> int:
    """Return 0 on pass, non-zero on assertion failure."""
    if expect_delta is None:
        return 0
    actual = d["iceberg_rows_delta"]
    if actual == expect_delta:
        print(f"  ✓ expected iceberg delta {expect_delta:+,} matches actual {actual:+,}")
        return 0
    print(
        f"  ✗ FAIL: expected iceberg delta {expect_delta:+,} but got {actual:+,}",
        file=sys.stderr,
    )
    return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args. `--expect-delta` is the assertion knob — set it
    in CI or scripted tests to fail-fast on a wrong row count."""
    p = argparse.ArgumentParser(description="run strata ingest and diff state")
    p.add_argument("--table", default="FACT_PAYMENT")
    p.add_argument("--full-refresh", action="store_true",
                   help="Pass --full-refresh through to local_ingest")
    p.add_argument("--expect-delta", type=int, default=None,
                   help="Assertion: expected Δ rows in Iceberg. "
                        "Exit 1 if actual differs.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run ingest with before/after state diff. Exits with the worse of
    the ingest exit code and the `--expect-delta` assertion result, so
    a failed assertion fails the script even if the ingest itself
    returned 0."""
    args = parse_args(argv)
    table = args.table

    started = datetime.now(timezone.utc).isoformat()
    print(f"=== run_and_verify @ {started}  table={table} ===")

    before = _inspect(table)
    print(f"  before: pg={before['postgres']['row_count']:,}  "
          f"iceberg={(before['iceberg'] or {}).get('row_count', 'n/a')}  "
          f"wm={(before['sqlite_state'] or {}).get('current_watermark')}")

    rc = _run_ingest(table, args.full_refresh)
    if rc != 0:
        print(f"\n  ! ingest exited with code {rc}", file=sys.stderr)

    after = _inspect(table)
    d = _delta(before, after)
    print(_render_delta(d))

    fail = _check_expectations(d, args.expect_delta)
    return rc or fail


if __name__ == "__main__":
    sys.exit(main())
