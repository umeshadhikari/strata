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
    pairs = " ".join(f"{k}={v!r}" for k, v in kv.items())
    log.info("EVENT=%s %s", event, pairs)
