"""
SQLite-backed state manager — local substitute for DynamoDB.

Provides the same public interface as `strata.state.StateManager` so that
`strata.recovery.reconcile_state` works without modification. The same
conditional-update semantics are preserved using SQL transactions.
"""

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from ..exceptions import ConcurrentRunError, StateConsistencyError, TransientError
from ..state import TableState, iso, now_utc  # reuse the dataclass + helpers

log = logging.getLogger(__name__)

LOCK_TTL_SECONDS = int(os.environ.get("STRATA_LOCK_TTL_SECONDS", 7200))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS strata_state (
    table_name              TEXT PRIMARY KEY,
    current_watermark       TEXT,
    pending_run_id          TEXT,
    pending_window_lower    TEXT,
    pending_window_upper    TEXT,
    pending_started_at      TEXT,
    pending_expires_at      TEXT,
    last_run_id             TEXT,
    last_run_status         TEXT,
    last_run_error          TEXT,
    last_run_rows           INTEGER,
    last_run_completed_at   TEXT,
    version                 INTEGER NOT NULL DEFAULT 0
);
"""


def _row_to_state(row: sqlite3.Row | None, table_name: str) -> TableState:
    """Convert a SQLite row into the same TableState dataclass that
    `strata.state._from_item` produces from DynamoDB. Returning the
    same shape from both backends is what lets `strata.recovery`
    work unchanged across local and AWS runtimes."""
    if row is None:
        return TableState(
            table_name=table_name,
            current_watermark=None,
            pending_run_id=None,
            pending_window_lower=None,
            pending_window_upper=None,
            pending_started_at=None,
            pending_expires_at=None,
            last_run_id=None,
            last_run_status=None,
            last_run_error=None,
            version=0,
        )
    return TableState(
        table_name=table_name,
        current_watermark=row["current_watermark"],
        pending_run_id=row["pending_run_id"],
        pending_window_lower=row["pending_window_lower"],
        pending_window_upper=row["pending_window_upper"],
        pending_started_at=row["pending_started_at"],
        pending_expires_at=row["pending_expires_at"],
        last_run_id=row["last_run_id"],
        last_run_status=row["last_run_status"],
        last_run_error=row["last_run_error"],
        version=row["version"],
    )


class LocalStateManager:
    """
    SQLite-backed equivalent of `strata.state.StateManager`.

    All transitions are wrapped in IMMEDIATE transactions to provide the same
    conditional-update guarantees as DynamoDB's ConditionExpression.
    """

    _connections: dict[int, sqlite3.Connection] = {}
    _lock = threading.Lock()

    def __init__(self, db_path: str, source_table: str):
        self.db_path = db_path
        self.source_table = source_table
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        thread_id = threading.get_ident()
        with self._lock:
            if thread_id not in self._connections:
                conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=30.0)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA foreign_keys = ON")
                self._connections[thread_id] = conn
            conn = self._connections[thread_id]
        try:
            yield conn
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(_SCHEMA)

    # -------- READ -------- #
    def read(self) -> TableState:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM strata_state WHERE table_name = ?",
                (self.source_table,),
            ).fetchone()
        return _row_to_state(row, self.source_table)

    # -------- ACQUIRE -------- #
    def acquire(
        self,
        run_id: str,
        window_lower: str | None,
        window_upper: str,
    ) -> None:
        started = iso(now_utc())
        expires = iso(now_utc() + timedelta(seconds=LOCK_TTL_SECONDS))
        now = iso(now_utc())

        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT * FROM strata_state WHERE table_name = ?",
                    (self.source_table,),
                ).fetchone()

                if existing is None:
                    conn.execute(
                        """INSERT INTO strata_state
                           (table_name, pending_run_id, pending_window_lower,
                            pending_window_upper, pending_started_at,
                            pending_expires_at, version)
                           VALUES (?, ?, ?, ?, ?, ?, 1)""",
                        (
                            self.source_table,
                            run_id,
                            window_lower,
                            window_upper,
                            started,
                            expires,
                        ),
                    )
                else:
                    held = existing["pending_run_id"]
                    not_expired = (
                        existing["pending_expires_at"] is not None
                        and existing["pending_expires_at"] > now
                    )
                    if held and held != run_id and not_expired:
                        conn.execute("ROLLBACK")
                        raise ConcurrentRunError(
                            f"Table {self.source_table} is locked by run {held} "
                            f"(expires {existing['pending_expires_at']})"
                        )
                    conn.execute(
                        """UPDATE strata_state
                           SET pending_run_id = ?,
                               pending_window_lower = ?,
                               pending_window_upper = ?,
                               pending_started_at = COALESCE(pending_started_at, ?),
                               pending_expires_at = ?,
                               version = version + 1
                           WHERE table_name = ?""",
                        (
                            run_id,
                            window_lower,
                            window_upper,
                            started,
                            expires,
                            self.source_table,
                        ),
                    )
                conn.execute("COMMIT")
                log.info(
                    "Lock acquired by run %s for table %s (expires %s)",
                    run_id, self.source_table, expires,
                )
            except sqlite3.OperationalError as exc:
                raise TransientError(f"SQLite acquire failed: {exc}") from exc

    # -------- COMPLETE -------- #
    def complete(self, run_id: str, new_watermark: str, rows_written: int) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT pending_run_id FROM strata_state WHERE table_name = ?",
                (self.source_table,),
            ).fetchone()
            if row is None or row["pending_run_id"] != run_id:
                conn.execute("ROLLBACK")
                state = self.read()
                raise StateConsistencyError(
                    f"complete() failed: pending_run_id={state.pending_run_id}, "
                    f"expected {run_id}. Likely the lock expired and another run "
                    f"claimed it."
                )
            conn.execute(
                """UPDATE strata_state
                   SET current_watermark = ?,
                       last_run_id = ?,
                       last_run_status = 'COMPLETED',
                       last_run_rows = ?,
                       last_run_completed_at = ?,
                       last_run_error = NULL,
                       pending_run_id = NULL,
                       pending_window_lower = NULL,
                       pending_window_upper = NULL,
                       pending_started_at = NULL,
                       pending_expires_at = NULL,
                       version = version + 1
                   WHERE table_name = ?""",
                (new_watermark, run_id, rows_written, iso(now_utc()), self.source_table),
            )
            conn.execute("COMMIT")
        log.info(
            "Run %s COMPLETED; watermark advanced to %s (%d rows)",
            run_id, new_watermark, rows_written,
        )

    # -------- FAIL -------- #
    def fail(self, run_id: str, error: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT pending_run_id FROM strata_state WHERE table_name = ?",
                (self.source_table,),
            ).fetchone()
            if row is None or row["pending_run_id"] != run_id:
                conn.execute("ROLLBACK")
                log.warning(
                    "fail() found pending_run_id != %s; nothing to release", run_id
                )
                return
            conn.execute(
                """UPDATE strata_state
                   SET last_run_id = ?,
                       last_run_status = 'FAILED',
                       last_run_error = ?,
                       last_run_completed_at = ?,
                       pending_run_id = NULL,
                       pending_window_lower = NULL,
                       pending_window_upper = NULL,
                       pending_started_at = NULL,
                       pending_expires_at = NULL,
                       version = version + 1
                   WHERE table_name = ?""",
                (run_id, error[:1000], iso(now_utc()), self.source_table),
            )
            conn.execute("COMMIT")
        log.info("Run %s marked FAILED", run_id)

    # -------- HEARTBEAT -------- #
    def heartbeat(self, run_id: str) -> None:
        expires = iso(now_utc() + timedelta(seconds=LOCK_TTL_SECONDS))
        with self._conn() as conn:
            conn.execute(
                """UPDATE strata_state SET pending_expires_at = ?
                   WHERE table_name = ? AND pending_run_id = ?""",
                (expires, self.source_table, run_id),
            )

    # -------- FORCE SYNC FROM ICEBERG -------- #
    def force_sync_watermark(self, watermark: str, source_run_id: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT 1 FROM strata_state WHERE table_name = ?",
                (self.source_table,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO strata_state
                       (table_name, current_watermark, last_run_id,
                        last_run_status, last_run_completed_at, version)
                       VALUES (?, ?, ?, 'RECONCILED_FROM_ICEBERG', ?, 1)""",
                    (self.source_table, watermark, source_run_id, iso(now_utc())),
                )
            else:
                conn.execute(
                    """UPDATE strata_state
                       SET current_watermark = ?,
                           last_run_id = ?,
                           last_run_status = 'RECONCILED_FROM_ICEBERG',
                           last_run_completed_at = ?,
                           pending_run_id = NULL,
                           pending_window_lower = NULL,
                           pending_window_upper = NULL,
                           pending_started_at = NULL,
                           pending_expires_at = NULL,
                           last_run_error = NULL,
                           version = version + 1
                       WHERE table_name = ?""",
                    (watermark, source_run_id, iso(now_utc()), self.source_table),
                )
            conn.execute("COMMIT")
        log.warning(
            "Reconciled watermark from Iceberg snapshot %s: %s",
            source_run_id, watermark,
        )
