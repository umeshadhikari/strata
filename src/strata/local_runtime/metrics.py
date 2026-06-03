"""Stdout-only metrics — the local substitute for CloudWatch."""

import logging
from typing import Any

log = logging.getLogger(__name__)


class LocalMetrics:
    """Best-effort metric printer. Same interface as `strata.metrics.Metrics`."""

    def __init__(self, table_name: str, customer_id: str = "local"):
        self.table = table_name
        self.customer = customer_id

    def emit(
        self,
        name: str,
        value: float,
        unit: str = "Count",
        extra_dimensions: dict[str, str] | None = None,
    ) -> None:
        """Print a metric line that mirrors CloudWatch PutMetricData shape.

        Format: ``METRIC table=<T> customer=local <Name>=<Value> <Unit>``.
        Grep-friendly so a developer can pull, say, all `RowsWritten`
        values out of a long run log with `grep "METRIC.*RowsWritten"`.
        """
        extras = (
            " " + " ".join(f"{k}={v}" for k, v in extra_dimensions.items())
            if extra_dimensions
            else ""
        )
        log.info(
            "METRIC table=%s customer=%s %s=%s %s%s",
            self.table, self.customer, name, value, unit, extras,
        )


def log_event(event: str, **kv: Any) -> None:
    """Print a structured event line, parallel to strata.metrics.log_event.

    Format: ``EVENT=<name> k1='v1' k2='v2' ...``. Used at run boundaries
    (``run_started``, ``run_completed``, ``state_reconciled``) so an
    operator can reconstruct the run timeline from log output alone.
    """
    pairs = " ".join(f"{k}={v!r}" for k, v in kv.items())
    log.info("EVENT=%s %s", event, pairs)
