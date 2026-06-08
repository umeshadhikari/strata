"""GET /api/tables — list tables.

GET /api/tables/{name} — paginated rows.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from psycopg2 import sql

from ..db import connect, dict_cursor
from ..settings import settings

router = APIRouter()


@router.get("")
def list_tables() -> dict[str, list[dict[str, Any]]]:
    """Enumerate every table in the configured schema with its row count.

    Row counts come from ``COUNT(*)`` — fine for tens of thousands of rows,
    but in a real production UI you'd swap to ``pg_class.reltuples``.
    """
    with connect() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            """
            SELECT c.table_name
              FROM information_schema.tables c
             WHERE c.table_schema = %s
               AND c.table_type = 'BASE TABLE'
             ORDER BY c.table_name
            """,
            (settings.pg_schema,),
        )
        names = [r["table_name"] for r in cur.fetchall()]

        out: list[dict[str, Any]] = []
        for name in names:
            cur.execute(
                sql.SQL("SELECT COUNT(*) AS n FROM {}.{}").format(
                    sql.Identifier(settings.pg_schema), sql.Identifier(name)
                )
            )
            n = cur.fetchone()["n"]
            out.append(
                {
                    "name": name,
                    "row_count": int(n or 0),
                    "kind": _classify(name),
                }
            )
        cur.close()
        return {"tables": out}


@router.get("/{name}")
def read_table(
    name: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return ``limit`` rows from ``name`` starting at ``offset``.

    Column names come back as ``columns``; rows as a list of dicts. We
    parameterise the schema/name via ``psycopg2.sql.Identifier`` so the
    path arg can't be used for SQL injection.
    """
    _ensure_table_exists(name)

    with connect() as conn:
        cur = dict_cursor(conn)

        # Column list — first row's keys would only show populated columns,
        # so query information_schema for a stable order.
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = %s AND table_name = %s
             ORDER BY ordinal_position
            """,
            (settings.pg_schema, name),
        )
        columns = [r["column_name"] for r in cur.fetchall()]

        # Total count (exact, for the pager).
        cur.execute(
            sql.SQL("SELECT COUNT(*) AS n FROM {}.{}").format(
                sql.Identifier(settings.pg_schema), sql.Identifier(name)
            )
        )
        total = int(cur.fetchone()["n"] or 0)

        # Page of rows — order by id if it exists so paging is stable;
        # otherwise leave it unordered.
        order_clause = sql.SQL("")
        if "id" in columns:
            order_clause = sql.SQL(" ORDER BY id ASC")

        cur.execute(
            sql.SQL("SELECT * FROM {}.{}{} LIMIT %s OFFSET %s").format(
                sql.Identifier(settings.pg_schema),
                sql.Identifier(name),
                order_clause,
            ),
            (limit, offset),
        )
        rows = cur.fetchall()
        cur.close()

        return {
            "name": name,
            "schema": settings.pg_schema,
            "columns": columns,
            "rows": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
        }


# --- helpers ---------------------------------------------------------------- #


def _classify(name: str) -> str:
    """Coarse bucket by naming convention — drives row colour in the UI."""
    n = name.lower()
    if n.startswith("dim_"):
        return "dimension"
    if n.startswith("fact_"):
        return "fact"
    return "other"


def _ensure_table_exists(name: str) -> None:
    with connect() as conn:
        cur = dict_cursor(conn)
        cur.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_schema = %s AND table_name = %s
            """,
            (settings.pg_schema, name),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail=f"table {name!r} not found")
        cur.close()
