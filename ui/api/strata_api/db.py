"""Postgres connection helpers.

Uses the same pattern as ``local/postgres/bootstrap.py``: explicit
``search_path`` via libpq options so unqualified table names always resolve
into ``data_mart`` regardless of whether the database has the persistent
ALTER DATABASE applied.
"""
from __future__ import annotations

import contextlib
from typing import Iterator

import psycopg2
import psycopg2.extras

from .settings import settings


@contextlib.contextmanager
def connect() -> Iterator[psycopg2.extensions.connection]:
    """Open a connection with autocommit OFF and search_path pinned.

    Yields the connection; commits on success, rolls back on exception, and
    always closes. Use as ``with connect() as conn:``.
    """
    conn = psycopg2.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        dbname=settings.pg_db,
        user=settings.pg_user,
        password=settings.pg_password,
        options=f"-c search_path={settings.pg_schema},public",
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dict_cursor(conn: psycopg2.extensions.connection):
    """Return a cursor whose ``fetchall()`` yields dicts (JSON-ready)."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
