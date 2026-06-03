"""
Translate a CREATE TABLE DDL file into a draft strata tables.yaml.

Designed for the "I have the data mart DDL on an air-gapped machine and need
to bootstrap a strata config" workflow. The DDL never leaves the laptop.

What it does:

  * Extracts every CREATE TABLE statement from the input SQL file.
  * For each table, emits a YAML block with:
      - source_table     (lowercased table name)
      - primary_key      (from PRIMARY KEY constraint, or first column as fallback)
      - watermark_column (best-guess from column names matching common patterns)
      - partition_spec   (best-guess: days() on a date column if one exists)
  * Leaves explicit TODO markers for fields that need human judgment:
      - domain
      - watermark column when no candidate matched
      - partition_spec when row volume can't be inferred
      - sort_order
  * Preserves the column list as a YAML comment block under each table so
    the reviewer can sanity-check what was parsed.

What it does NOT do:

  * Handle every DDL dialect quirk. It tries Oracle-, PostgreSQL-, and
    MySQL-style CREATE TABLE syntax but won't grok exotic constructs
    (computed columns, table-level CHECK constraints with subqueries,
    Oracle XMLTYPE, etc.). For those, hand-edit the output.
  * Recommend partition_spec for fact tables. Partitioning depends on row
    volume and query patterns, which DDL alone can't tell you. The script
    suggests `days(<date>)` as a starting point; you decide whether to add
    `bucket(N, <pk>)` based on expected daily volume.

Usage::

    # Single DDL file:
    python local/scripts/ddl_to_yaml.py path/to/datamart.ddl.sql > tables.draft.yaml

    # Multiple files (e.g. one per schema):
    python local/scripts/ddl_to_yaml.py schema1.sql schema2.sql > tables.draft.yaml

    # With a specific default domain (otherwise emits TODO for each):
    python local/scripts/ddl_to_yaml.py --default-domain payments  ddl.sql > tables.draft.yaml

    # Incremental update — merge new DDL into an existing tables.yaml so the
    # human-resolved fields are preserved:
    python local/scripts/ddl_to_yaml.py \\
        --merge local/config/tables.local.yaml \\
        path/to/updated.ddl.sql > tables.merged.yaml

In merge mode, the script:
  - Keeps every existing table's resolved fields verbatim
  - Adds new tables that appear in the DDL but not in the existing YAML
  - Flags removed tables (in YAML, not in DDL) with a WARNING comment
  - Prints a summary to stderr: N added, M removed, K unchanged

After producing the draft, open it next to an AI assistant (Copilot in VS Code
on the office laptop works; the @workspace /add-table prompt is the relevant
one) and walk through each TODO. Most can be resolved in seconds once you know
the table's role.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Heuristics — adjust these per your shop's naming conventions if they differ
# --------------------------------------------------------------------------- #

# Column names commonly used as the watermark — case insensitive, order matters
# (first match wins).
WATERMARK_CANDIDATES = [
    "last_updated_time",
    "last_updated",
    "last_update_date",
    "last_modified_time",
    "last_modified",
    "modified_at",
    "modified_time",
    "updated_at",
    "updated_time",
    "update_time",
    "lst_upd_dt",
    "lstupddt",
    "etl_updated_at",
    "etl_load_ts",
    "row_updated_at",
    "sysmodified",
]

# Column names commonly used as date partition keys for facts.
PARTITION_DATE_CANDIDATES = [
    "value_date",
    "transaction_date",
    "trade_date",
    "settlement_date",
    "posting_date",
    "business_date",
    "event_date",
    "balance_date",
    "as_of_date",
    "asof_date",
    "snapshot_date",
    "report_date",
    "date",
]

# Types that look like dates/timestamps — used to find candidate partition
# columns when the name heuristic misses.
DATE_TYPE_PATTERNS = [
    r"^date\b",
    r"^timestamp",
    r"^datetime",
    r"^smalldatetime\b",
]


@dataclass
class Column:
    name: str
    type: str  # raw declared type, lowercased


@dataclass
class TableDef:
    schema: str | None
    name: str  # lowercase
    columns: list[Column] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

_CREATE_TABLE_RE = re.compile(
    r"""
    \bCREATE\s+(?:GLOBAL\s+TEMPORARY\s+)?TABLE\s+
    (?:IF\s+NOT\s+EXISTS\s+)?
    (?:(?P<schema>[A-Za-z_][\w$]*)\.)?
    (?P<name>"?[A-Za-z_][\w$]*"?)
    \s*\(
    (?P<body>.*?)
    \)\s*(?:;|$)
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

_PRIMARY_KEY_INLINE_RE = re.compile(
    r"\bPRIMARY\s+KEY\s*(?:\(([^)]+)\))?", re.IGNORECASE
)
_PRIMARY_KEY_CONSTRAINT_RE = re.compile(
    r"^\s*(?:CONSTRAINT\s+\S+\s+)?PRIMARY\s+KEY\s*\(([^)]+)\)", re.IGNORECASE
)


def strip_block_comments(sql: str) -> str:
    """Remove /* ... */ block comments and -- line comments."""
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", "", sql)
    return sql


def split_top_level(body: str) -> list[str]:
    """Split a parenthesized column list by commas at depth 0."""
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def parse_column_line(line: str) -> Column | None:
    """Parse a single column definition. Returns None if it's not a column
    (e.g. a constraint or index line).

    The type pattern uses `\\w+` for the base type name followed by an
    optional `(...)` group. Crucially the parenthesised group's content
    is matched with `[^)]*` rather than `[^\\s,]*`, which lets it span
    types with multi-argument parens like `numeric(18,4)` and
    `decimal(10,2)` without truncating at the inner comma.
    """
    # Constraints / indexes / partitions — skip them
    upper = line.upper().lstrip()
    if upper.startswith(("CONSTRAINT", "PRIMARY KEY", "UNIQUE", "FOREIGN KEY",
                         "CHECK", "INDEX", "KEY", "PARTITION", "USING", "TABLESPACE")):
        return None

    # Pull the column name (possibly quoted) and the type
    m = re.match(
        r'^\s*"?(?P<name>[A-Za-z_][\w$]*)"?\s+(?P<type>\w+(?:\s*\([^)]*\))?)',
        line,
    )
    if not m:
        return None
    return Column(name=m.group("name").lower(), type=m.group("type").lower())


def parse_create_table(match: re.Match) -> TableDef:
    schema = (match.group("schema") or "").lower() or None
    name = match.group("name").strip('"').lower()
    body = strip_block_comments(match.group("body"))
    parts = split_top_level(body)

    table = TableDef(schema=schema, name=name)

    for part in parts:
        # Table-level PK constraint?
        pk_m = _PRIMARY_KEY_CONSTRAINT_RE.match(part)
        if pk_m:
            cols = [c.strip().strip('"').lower()
                    for c in pk_m.group(1).split(",")]
            table.primary_key = cols
            continue

        col = parse_column_line(part)
        if col is None:
            continue

        # Inline PK on the column? ("col INT PRIMARY KEY" or "col INT NOT NULL PRIMARY KEY")
        if _PRIMARY_KEY_INLINE_RE.search(part) and not table.primary_key:
            table.primary_key = [col.name]

        table.columns.append(col)

    # Fallback: if no PK was found, leave it empty so the YAML emits a TODO.
    return table


def extract_tables(sql: str) -> list[TableDef]:
    """Find every CREATE TABLE in the SQL and parse it."""
    sql_clean = strip_block_comments(sql)
    return [parse_create_table(m) for m in _CREATE_TABLE_RE.finditer(sql_clean)]


# --------------------------------------------------------------------------- #
# YAML emission
# --------------------------------------------------------------------------- #

def guess_watermark(cols: list[Column]) -> str | None:
    """Find the first column whose lowercased name matches a known pattern."""
    names = [c.name for c in cols]
    for candidate in WATERMARK_CANDIDATES:
        if candidate in names:
            return candidate
    # Fallback: any column ending in "_at" or "_time" or "_ts"
    for c in cols:
        if c.name.endswith(("_at", "_time", "_ts")) and "create" not in c.name:
            return c.name
    return None


def guess_partition_date(cols: list[Column]) -> str | None:
    """Find a column that looks like a partition-worthy business date."""
    names = [c.name for c in cols]
    for candidate in PARTITION_DATE_CANDIDATES:
        if candidate in names:
            return candidate
    # Fallback: any DATE-typed column whose name contains 'date'
    for c in cols:
        if "date" in c.name and any(re.match(p, c.type) for p in DATE_TYPE_PATTERNS):
            return c.name
    return None


def emit_yaml(tables: list[TableDef], default_domain: str | None) -> str:
    """Render the draft YAML."""
    lines: list[str] = [
        "# Draft tables.yaml generated by local/scripts/ddl_to_yaml.py.",
        "# Resolve every TODO before using this in production.",
        "",
        "defaults:",
        "  source_schema: TODO  # the source schema name; matches data_mart in the local example",
        "  write_mode: append",
        "  fetch_size: 5000",
        "  glue_database_prefix: silver_",
        "",
        "tables:",
        "",
    ]

    for t in tables:
        logical = t.name.upper()
        watermark = guess_watermark(t.columns)
        partition_col = guess_partition_date(t.columns)

        lines.append(f"  # ----- {logical} -----")
        # Use ' | ' as separator since types like NUMBER(18,2) contain commas.
        lines.append(f"  # columns: " + " | ".join(
            f"{c.name}:{c.type}" for c in t.columns[:8]
        ) + (" | ..." if len(t.columns) > 8 else ""))
        if len(t.columns) > 8:
            lines.append(f"  #   ... and {len(t.columns) - 8} more")

        lines.append(f"  {logical}:")
        lines.append(f"    source_table: {t.name}")

        domain_value = default_domain or "TODO_domain  # one of: shared, payments, balances, etc."
        lines.append(f"    domain: {domain_value}")

        if watermark:
            lines.append(f"    watermark_column: {watermark}")
        else:
            lines.append(
                "    watermark_column: TODO_watermark  "
                "# no column matched the heuristic; pick a monotonically-increasing timestamp"
            )

        if t.primary_key:
            pk_str = "[" + ", ".join(t.primary_key) + "]"
            lines.append(f"    primary_key: {pk_str}")
        else:
            lines.append("    primary_key: [TODO_pk]  # DDL had no PRIMARY KEY constraint")

        # Partition spec — naming-convention-aware heuristic.
        # Most warehouses prefix dimension tables `dim_` (or `d_`) and fact
        # tables `fact_` (or `f_`). Dims are usually small and don't benefit
        # from Iceberg partitioning; facts almost always do.
        is_dim = t.name.startswith(("dim_", "d_"))
        is_fact = t.name.startswith(("fact_", "f_"))
        if is_dim:
            lines.append(
                "    partition_spec: []  "
                "# dim_ prefix — assumed small/reference, no partitioning needed"
            )
        elif partition_col and (is_fact or not is_dim):
            lines.append("    partition_spec:")
            lines.append(f"      - {{ transform: days, column: {partition_col} }}")
            lines.append(
                "      # TODO: for large facts add a bucket transform on the PK, e.g.:"
            )
            lines.append(f"      # - {{ transform: bucket, column: {(t.primary_key or ['TODO'])[0]}, n: 16 }}")
        else:
            lines.append(
                "    partition_spec: []  "
                "# TODO: pick a partition strategy if this is a large fact table"
            )

        lines.append(
            "    # sort_order: TODO_sort  "
            "# optional Iceberg sort; depends on query patterns"
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Incremental merge
# --------------------------------------------------------------------------- #

def merge_with_existing(
    new_tables: list[TableDef],
    existing_yaml_path: Path,
    default_domain: str | None,
) -> tuple[str, dict[str, int]]:
    """Combine a newly-extracted table list with a previously-resolved YAML.

    Preserves the user's human edits (resolved TODOs) for tables that are
    still in the DDL. Adds new tables as drafts with TODOs. Keeps tables
    that disappeared from the DDL but tags them with a WARNING comment so
    the operator can decide whether to drop them.

    Returns (merged_yaml_text, summary_dict) where summary_dict has keys
    ``added``, ``removed``, ``unchanged``.
    """
    # Lazy import yaml so the script works without PyYAML if --merge isn't used.
    try:
        import yaml  # type: ignore
    except ImportError:
        print(
            "ERROR: --merge requires PyYAML. Install with `pip install pyyaml` "
            "or run inside the spark container (where it's already installed).",
            file=sys.stderr,
        )
        sys.exit(2)

    if not existing_yaml_path.exists():
        print(
            f"ERROR: --merge target {existing_yaml_path} does not exist. "
            f"Generate it first with a plain `ddl_to_yaml.py <ddl> > {existing_yaml_path}`.",
            file=sys.stderr,
        )
        sys.exit(2)

    with existing_yaml_path.open() as fh:
        existing_doc = yaml.safe_load(fh) or {}

    existing_tables: dict = existing_doc.get("tables", {}) or {}
    new_table_keys = {t.name.upper() for t in new_tables}

    out_lines: list[str] = [
        "# Merged tables.yaml — produced by local/scripts/ddl_to_yaml.py --merge",
        "# Existing fields preserved verbatim; new tables added with TODO markers.",
        "# Tables present in YAML but absent from latest DDL are flagged with WARNING.",
        "",
    ]

    # --- defaults section: prefer existing, fall back to script default --- #
    defaults = existing_doc.get("defaults", {})
    if defaults:
        out_lines.append("defaults:")
        for k, v in defaults.items():
            out_lines.append(f"  {k}: {v}")
        out_lines.append("")
    else:
        out_lines.extend([
            "defaults:",
            "  source_schema: TODO",
            "  write_mode: append",
            "  fetch_size: 5000",
            "  glue_database_prefix: silver_",
            "",
        ])

    out_lines.append("tables:")
    out_lines.append("")

    counts = {"added": 0, "removed": 0, "unchanged": 0}

    # --- Pass 1: emit existing tables in original order, with WARNINGs for drops --- #
    existing_dumped = yaml.dump(
        {"tables": existing_tables}, sort_keys=False, default_flow_style=False
    )
    for logical, cfg in existing_tables.items():
        if logical in new_table_keys:
            counts["unchanged"] += 1
            out_lines.append(f"  # ----- {logical} (preserved from existing YAML) -----")
        else:
            counts["removed"] += 1
            out_lines.append(
                f"  # ----- {logical} -----  "
                f"# WARNING: not found in latest DDL — drop from ingest schedule?"
            )
        # Re-emit the existing entry as YAML (pyyaml output, indented)
        entry_yaml = yaml.dump(
            {logical: cfg}, sort_keys=False, default_flow_style=False
        )
        for line in entry_yaml.splitlines():
            out_lines.append(f"  {line}")
        out_lines.append("")

    # --- Pass 2: append new tables not in the existing YAML --- #
    new_only = [t for t in new_tables if t.name.upper() not in existing_tables]
    if new_only:
        out_lines.append("# ----- NEW TABLES FROM LATEST DDL -----")
        out_lines.append("")
        # Reuse the same emitter for consistency — just the table list portion
        fresh = emit_yaml(new_only, default_domain=default_domain)
        # Strip the prelude (defaults block + 'tables:' header) since we
        # already emitted those.
        in_tables = False
        for line in fresh.splitlines():
            if not in_tables:
                if line.startswith("tables:"):
                    in_tables = True
                continue
            out_lines.append(line)
        counts["added"] = len(new_only)

    return "\n".join(out_lines).rstrip() + "\n", counts


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args. Accepts one or more input .sql files; emits YAML on stdout."""
    p = argparse.ArgumentParser(description="DDL → strata tables.yaml")
    p.add_argument(
        "ddl_files", nargs="+", type=Path,
        help="One or more DDL .sql files (concatenated before parsing)"
    )
    p.add_argument(
        "--default-domain", default=None,
        help="If set, every table gets this domain instead of a TODO marker"
    )
    p.add_argument(
        "--merge", type=Path, default=None, metavar="EXISTING_YAML",
        help="Incremental update: merge new extraction into an existing tables.yaml, "
             "preserving resolved fields and flagging changes."
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Read DDL, extract CREATE TABLE statements, emit YAML to stdout.

    With --merge, the output is a *merged* YAML where existing entries are
    preserved verbatim and new tables are appended with TODO markers.
    Without --merge, the output is a fresh draft.

    Exit codes:
      0 — at least one table extracted
      1 — no CREATE TABLE found (likely wrong file or unsupported syntax)
      2 — --merge requested but its target file or PyYAML is missing
    """
    args = parse_args(argv)
    sql = "\n".join(f.read_text() for f in args.ddl_files)
    tables = extract_tables(sql)
    if not tables:
        print(
            "ERROR: no CREATE TABLE statements found. "
            "Check the file's syntax dialect.",
            file=sys.stderr,
        )
        return 1

    if args.merge:
        text, counts = merge_with_existing(
            tables, args.merge, default_domain=args.default_domain
        )
        print(text)
        print(
            f"# Merge summary: {counts['added']} added, "
            f"{counts['removed']} removed, "
            f"{counts['unchanged']} unchanged "
            f"(extracted {len(tables)} from "
            f"{', '.join(str(f) for f in args.ddl_files)})",
            file=sys.stderr,
        )
    else:
        print(emit_yaml(tables, default_domain=args.default_domain))
        print(
            f"# Extracted {len(tables)} tables from "
            f"{', '.join(str(f) for f in args.ddl_files)}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
