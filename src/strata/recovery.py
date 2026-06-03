"""
Recovery logic — runs at the start of every job invocation.

Cross-references DynamoDB state with Iceberg snapshot history. If they
disagree, reconciles them (Iceberg is source of truth). After this runs,
the table is in one of two clean states:
  * IDLE — no pending lock, ready to be acquired by this run
  * LOCKED by another run — this run exits with ConcurrentRunError

Cases handled:
  A. DynamoDB shows no pending lock, watermark matches latest Iceberg snapshot.
     → IDLE. Proceed normally.
  B. DynamoDB shows watermark < latest Iceberg snapshot's upper bound.
     → Iceberg has data DynamoDB doesn't know about. Force-sync DynamoDB.
  C. DynamoDB shows pending lock for run R, snapshot with R's run_id EXISTS.
     → Previous run wrote successfully but crashed before clearing state.
       Force-complete the run: advance watermark, release lock.
  D. DynamoDB shows pending lock for run R, no snapshot with R's run_id exists,
     lock has NOT expired.
     → Another run is genuinely in progress. Fail with ConcurrentRunError.
  E. DynamoDB shows pending lock for run R, no snapshot, lock HAS expired.
     → Previous run crashed before writing. Release the lock so this run
       can acquire it.
"""

import logging

from pyspark.sql import SparkSession

from .exceptions import ConcurrentRunError
from .state import StateManager, TableState, iso, now_utc
from .writer import find_snapshot_by_run_id, latest_snapshot_watermark

log = logging.getLogger(__name__)


def reconcile_state(
    spark: SparkSession,
    state_mgr: StateManager,
    table_fqn: str,
) -> TableState:
    """
    Reconcile DynamoDB state with Iceberg snapshot history.

    Returns the post-reconciliation TableState. Raises ConcurrentRunError if
    another run is currently holding the lock.
    """
    state = state_mgr.read()
    log.info("Initial state: %s", state)

    # ---- Case A/B: no pending lock -------------------------------------- #
    if not state.pending_run_id:
        # Is DynamoDB watermark behind Iceberg?
        iceberg = latest_snapshot_watermark(spark, table_fqn)
        if iceberg:
            ice_run_id, ice_wm = iceberg
            if (state.current_watermark is None) or (state.current_watermark < ice_wm):
                log.warning(
                    "Case B: DynamoDB watermark (%s) behind Iceberg (%s, run %s). "
                    "Fast-forwarding DynamoDB.",
                    state.current_watermark, ice_wm, ice_run_id,
                )
                state_mgr.force_sync_watermark(ice_wm, ice_run_id)
                state = state_mgr.read()
        return state

    # ---- Pending lock exists. Decide between C, D, E. ------------------ #
    pending_run_id = state.pending_run_id
    pending_lock_expired = (
        state.pending_expires_at is not None
        and state.pending_expires_at <= iso(now_utc())
    )

    # Did the pending run actually commit data?
    snap = find_snapshot_by_run_id(spark, table_fqn, pending_run_id)

    if snap is not None:
        # ---- Case C: write committed, state not advanced ----------------- #
        wm_upper = snap["summary"].get("glue.watermark_upper")
        rows = int(snap["summary"].get("glue.row_count", 0))
        if not wm_upper:
            log.error(
                "Case C anomaly: snapshot for run %s has no glue.watermark_upper. "
                "Cannot recover automatically. Operator review required.",
                pending_run_id,
            )
            raise ConcurrentRunError(
                f"Inconsistent state: snapshot exists for run {pending_run_id} but "
                f"has no watermark property. Manual intervention needed."
            )
        log.warning(
            "Case C: previous run %s wrote %d rows but didn't advance state. "
            "Completing it now.",
            pending_run_id, rows,
        )
        try:
            state_mgr.complete(pending_run_id, wm_upper, rows)
        except Exception as exc:
            # If complete() fails because the row mutated, re-read and try again
            log.warning("Auto-complete failed (%s); re-reading state", exc)
            state = state_mgr.read()
            return reconcile_state(spark, state_mgr, table_fqn)
        return state_mgr.read()

    # ---- snapshot does NOT exist — D or E ------------------------------- #
    if pending_lock_expired:
        # ---- Case E: stale lock, no write happened ---------------------- #
        log.warning(
            "Case E: stale lock from run %s (started %s, expired %s). "
            "Releasing so this run can acquire.",
            pending_run_id,
            state.pending_started_at,
            state.pending_expires_at,
        )
        try:
            state_mgr.fail(
                pending_run_id,
                "Lock expired without commit; reaped by recovery",
            )
        except Exception as exc:
            log.warning("Releasing stale lock failed (%s); continuing", exc)
        return state_mgr.read()

    # ---- Case D: another run is genuinely live ------------------------- #
    raise ConcurrentRunError(
        f"Another run ({pending_run_id}) holds the lock on {state.table_name}. "
        f"Started {state.pending_started_at}, expires {state.pending_expires_at}. "
        f"Try again later or wait."
    )
