"""
DynamoDB-backed state machine with snapshot-based recovery.

Core principle: the Iceberg snapshot is the source of truth for what data has
been committed. DynamoDB is a cache that gets reconciled at run start. If
DynamoDB and Iceberg disagree, Iceberg wins.

State machine per table:
    IDLE → PENDING (acquired by run R) → IDLE (R committed) or IDLE (R failed)

DynamoDB item shape:
    table_name           (S, PK)
    current_watermark    (S)        — upper bound of most recent committed Iceberg snapshot
    pending_run_id       (S | NULL) — run currently holding the lock
    pending_window_lower (S | NULL) — lower bound of in-progress window
    pending_window_upper (S | NULL) — upper bound of in-progress window
    pending_started_at   (S | NULL) — ISO timestamp
    pending_expires_at   (S | NULL) — TTL on the lock; if past, lock is stale
    last_run_id          (S)
    last_run_status      (S)        — COMPLETED | FAILED
    last_run_rows        (N)
    last_run_completed_at(S)
    last_run_error       (S | NULL)
    version              (N)        — optimistic concurrency

Locking model:
    acquire():
        Conditional update: pending_run_id is NULL OR pending_expires_at <= now
        On success: pending_run_id = my_run_id, pending_expires_at = now + LOCK_TTL
    complete(my_run_id):
        Conditional update: pending_run_id = my_run_id
        On success: advance current_watermark, clear pending_*, set last_run_status=COMPLETED
    fail(my_run_id):
        Conditional update: pending_run_id = my_run_id
        On success: clear pending_*, set last_run_status=FAILED, record error
"""

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .exceptions import (
    ConcurrentRunError,
    StateConsistencyError,
    TransientError,
)

log = logging.getLogger(__name__)

_BOTO_RETRY = Config(retries={"max_attempts": 10, "mode": "adaptive"})
LOCK_TTL_SECONDS = int(os.environ.get("TRAX_LOCK_TTL_SECONDS", 7200))  # 2 hours


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


@dataclass
class TableState:
    """Snapshot of DynamoDB state for a single table."""

    table_name: str
    current_watermark: str | None
    pending_run_id: str | None
    pending_window_lower: str | None
    pending_window_upper: str | None
    pending_started_at: str | None
    pending_expires_at: str | None
    last_run_id: str | None
    last_run_status: str | None
    last_run_error: str | None
    version: int

    @property
    def is_locked(self) -> bool:
        if not self.pending_run_id:
            return False
        if not self.pending_expires_at:
            return True
        return self.pending_expires_at > iso(now_utc())


def _from_item(item: dict[str, Any] | None, table_name: str) -> TableState:
    if not item:
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

    def get(k: str) -> str | None:
        if k not in item:
            return None
        return item[k].get("S")

    def get_n(k: str) -> int:
        if k not in item:
            return 0
        return int(item[k].get("N", "0"))

    return TableState(
        table_name=table_name,
        current_watermark=get("current_watermark"),
        pending_run_id=get("pending_run_id"),
        pending_window_lower=get("pending_window_lower"),
        pending_window_upper=get("pending_window_upper"),
        pending_started_at=get("pending_started_at"),
        pending_expires_at=get("pending_expires_at"),
        last_run_id=get("last_run_id"),
        last_run_status=get("last_run_status"),
        last_run_error=get("last_run_error"),
        version=get_n("version"),
    )


class StateManager:
    """Owns the DynamoDB row for one source table."""

    def __init__(self, dynamo_table: str, source_table: str):
        self.dynamo_table = dynamo_table
        self.source_table = source_table
        self.client = boto3.client("dynamodb", config=_BOTO_RETRY)

    # -------- READ -------- #
    def read(self) -> TableState:
        try:
            resp = self.client.get_item(
                TableName=self.dynamo_table,
                Key={"table_name": {"S": self.source_table}},
                ConsistentRead=True,
            )
            return _from_item(resp.get("Item"), self.source_table)
        except ClientError as exc:
            raise TransientError(f"DynamoDB read failed: {exc}") from exc

    # -------- ACQUIRE LOCK -------- #
    def acquire(
        self,
        run_id: str,
        window_lower: str | None,
        window_upper: str,
    ) -> None:
        """
        Claim the table for this run. Idempotent: re-acquiring with the same
        run_id (because of a retry) succeeds without resetting started_at.
        """
        started = iso(now_utc())
        expires = iso(now_utc() + timedelta(seconds=LOCK_TTL_SECONDS))

        try:
            self.client.update_item(
                TableName=self.dynamo_table,
                Key={"table_name": {"S": self.source_table}},
                UpdateExpression=(
                    "SET pending_run_id = :rid, "
                    "    pending_window_lower = :wl, "
                    "    pending_window_upper = :wu, "
                    "    pending_started_at = if_not_exists(pending_started_at, :st), "
                    "    pending_expires_at = :exp, "
                    "    version = if_not_exists(version, :zero) + :one"
                ),
                ConditionExpression=(
                    "attribute_not_exists(pending_run_id) "
                    "OR pending_run_id = :rid "
                    "OR pending_expires_at <= :now"
                ),
                ExpressionAttributeValues={
                    ":rid": {"S": run_id},
                    ":wl": {"S": window_lower} if window_lower else {"NULL": True},
                    ":wu": {"S": window_upper},
                    ":st": {"S": started},
                    ":exp": {"S": expires},
                    ":now": {"S": iso(now_utc())},
                    ":zero": {"N": "0"},
                    ":one": {"N": "1"},
                },
            )
            log.info(
                "Lock acquired by run %s for table %s (expires %s)",
                run_id, self.source_table, expires,
            )
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "ConditionalCheckFailedException":
                state = self.read()
                raise ConcurrentRunError(
                    f"Table {self.source_table} is locked by run "
                    f"{state.pending_run_id} (expires {state.pending_expires_at})"
                ) from exc
            raise TransientError(f"DynamoDB acquire failed: {exc}") from exc

    # -------- RELEASE / COMPLETE -------- #
    def complete(
        self,
        run_id: str,
        new_watermark: str,
        rows_written: int,
    ) -> None:
        """Mark run successful and advance watermark."""
        try:
            self.client.update_item(
                TableName=self.dynamo_table,
                Key={"table_name": {"S": self.source_table}},
                UpdateExpression=(
                    "SET current_watermark = :wm, "
                    "    last_run_id = :rid, "
                    "    last_run_status = :ok, "
                    "    last_run_rows = :rc, "
                    "    last_run_completed_at = :ts, "
                    "    version = version + :one "
                    "REMOVE pending_run_id, pending_window_lower, "
                    "       pending_window_upper, pending_started_at, "
                    "       pending_expires_at, last_run_error"
                ),
                ConditionExpression="pending_run_id = :rid",
                ExpressionAttributeValues={
                    ":wm": {"S": new_watermark},
                    ":rid": {"S": run_id},
                    ":ok": {"S": "COMPLETED"},
                    ":rc": {"N": str(rows_written)},
                    ":ts": {"S": iso(now_utc())},
                    ":one": {"N": "1"},
                },
            )
            log.info(
                "Run %s COMPLETED; watermark advanced to %s (%d rows)",
                run_id, new_watermark, rows_written,
            )
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "ConditionalCheckFailedException":
                state = self.read()
                raise StateConsistencyError(
                    f"complete() failed: pending_run_id={state.pending_run_id}, "
                    f"expected {run_id}. Likely the lock expired and another run "
                    f"claimed it."
                ) from exc
            raise TransientError(f"DynamoDB complete failed: {exc}") from exc

    def fail(self, run_id: str, error: str) -> None:
        """Mark run failed, release lock."""
        try:
            self.client.update_item(
                TableName=self.dynamo_table,
                Key={"table_name": {"S": self.source_table}},
                UpdateExpression=(
                    "SET last_run_id = :rid, "
                    "    last_run_status = :st, "
                    "    last_run_error = :err, "
                    "    last_run_completed_at = :ts, "
                    "    version = version + :one "
                    "REMOVE pending_run_id, pending_window_lower, "
                    "       pending_window_upper, pending_started_at, "
                    "       pending_expires_at"
                ),
                ConditionExpression="pending_run_id = :rid",
                ExpressionAttributeValues={
                    ":rid": {"S": run_id},
                    ":st": {"S": "FAILED"},
                    ":err": {"S": error[:1000]},
                    ":ts": {"S": iso(now_utc())},
                    ":one": {"N": "1"},
                },
            )
            log.info("Run %s marked FAILED", run_id)
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "ConditionalCheckFailedException":
                # The lock was already released — log but don't fail
                log.warning(
                    "fail() found pending_run_id != %s; nothing to release",
                    run_id,
                )
                return
            log.error("Cannot mark failure in DynamoDB: %s", exc)

    # -------- RECONCILE -------- #
    def force_sync_watermark(self, watermark: str, source_run_id: str) -> None:
        """
        Force-update the watermark from an Iceberg snapshot we found that
        the cached state didn't know about. Used during recovery.
        """
        try:
            self.client.update_item(
                TableName=self.dynamo_table,
                Key={"table_name": {"S": self.source_table}},
                UpdateExpression=(
                    "SET current_watermark = :wm, "
                    "    last_run_id = :rid, "
                    "    last_run_status = :ok, "
                    "    last_run_completed_at = :ts, "
                    "    version = if_not_exists(version, :zero) + :one "
                    "REMOVE pending_run_id, pending_window_lower, "
                    "       pending_window_upper, pending_started_at, "
                    "       pending_expires_at, last_run_error"
                ),
                ExpressionAttributeValues={
                    ":wm": {"S": watermark},
                    ":rid": {"S": source_run_id},
                    ":ok": {"S": "RECONCILED_FROM_ICEBERG"},
                    ":ts": {"S": iso(now_utc())},
                    ":zero": {"N": "0"},
                    ":one": {"N": "1"},
                },
            )
            log.warning(
                "Reconciled watermark from Iceberg snapshot %s: %s",
                source_run_id, watermark,
            )
        except ClientError as exc:
            raise TransientError(f"DynamoDB reconcile failed: {exc}") from exc

    # -------- LEASE RENEWAL -------- #
    def heartbeat(self, run_id: str) -> None:
        """
        Extend the lock TTL while a long-running job is still going.
        Safe to call repeatedly. Call e.g. before each major phase.
        """
        expires = iso(now_utc() + timedelta(seconds=LOCK_TTL_SECONDS))
        try:
            self.client.update_item(
                TableName=self.dynamo_table,
                Key={"table_name": {"S": self.source_table}},
                UpdateExpression="SET pending_expires_at = :exp",
                ConditionExpression="pending_run_id = :rid",
                ExpressionAttributeValues={
                    ":rid": {"S": run_id},
                    ":exp": {"S": expires},
                },
            )
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "ConditionalCheckFailedException":
                raise StateConsistencyError(
                    f"Heartbeat for run {run_id} failed: lock no longer ours"
                ) from exc
            log.warning("Heartbeat error (continuing): %s", exc)
