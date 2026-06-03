"""
Local query CLI — the Athena substitute for development.

Uses DuckDB to read the Iceberg tables directly from the local warehouse.
DuckDB has native Iceberg support (Hadoop-catalog tables work via
`iceberg_scan('/path/to/table')`).

Usage::

    python local/queries/explore.py "SELECT count(*) FROM fact_payment"
    python local/queries/explore.py --shell
"""

import argparse
import sys
from pathlib import Path

try:
    import duckdb
except ImportError:
    print("ERROR: duckdb not installed. Run: pip install duckdb")
    sys.exit(1)


def find_iceberg_tables(warehouse: Path) -> dict[str, Path]:
    """Discover Iceberg tables under the warehouse: silver_<db>.<table>."""
    tables: dict[str, Path] = {}
    if not warehouse.exists():
        return tables
    for db_dir in warehouse.iterdir():
        if not db_dir.is_dir():
            continue
        for table_dir in db_dir.iterdir():
            if not table_dir.is_dir():
                continue
            if (table_dir / "metadata").exists():
                # Use simple table name; "fact_payment" rather than "silver_payments.fact_payment"
                tables[table_dir.name] = table_dir
    return tables


def build_connection(warehouse: Path):
    con = duckdb.connect(":memory:")
    con.execute("INSTALL iceberg")
    con.execute("LOAD iceberg")

    # Register every Iceberg table as a DuckDB view so they can be queried by name
    for name, path in find_iceberg_tables(warehouse).items():
        try:
            con.execute(
                f"CREATE OR REPLACE VIEW {name} AS "
                f"SELECT * FROM iceberg_scan('{path}', allow_moved_paths = true)"
            )
        except duckdb.IOException as exc:
            print(f"  warning: skipping {name}: {exc}", file=sys.stderr)
    return con


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", help="SQL to run; omit to enter shell")
    parser.add_argument(
        "--warehouse",
        default="local/data/warehouse",
        help="Path to the Iceberg warehouse",
    )
    parser.add_argument("--shell", action="store_true", help="Interactive shell")
    args = parser.parse_args()

    warehouse = Path(args.warehouse).resolve()
    con = build_connection(warehouse)

    tables = find_iceberg_tables(warehouse)
    if not tables:
        print(f"No Iceberg tables found under {warehouse}", file=sys.stderr)
        print("Run an ingestion first:", file=sys.stderr)
        print("  python -m strata.local_ingest --table DIM_CURRENCY", file=sys.stderr)
        return 1

    print(f"Loaded {len(tables)} tables: {', '.join(sorted(tables))}", file=sys.stderr)

    if args.query:
        result = con.execute(args.query).fetchdf()
        print(result.to_string(index=False))
        return 0

    if args.shell or not args.query:
        print("\nType SQL (one query per line), or .tables / .quit\n")
        while True:
            try:
                line = input("strata> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not line:
                continue
            if line in {".q", ".quit", "exit"}:
                return 0
            if line == ".tables":
                print(", ".join(sorted(tables)))
                continue
            try:
                result = con.execute(line).fetchdf()
                if result.empty:
                    print("(no rows)")
                else:
                    print(result.to_string(index=False))
            except Exception as exc:
                print(f"ERROR: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
