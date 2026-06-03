"""
CloudWatch metrics emission + structured logging.

Emits per-table metrics so the ops team can build dashboards and alarms:
  StrataIngest/<TABLE>/RowsExtracted
  StrataIngest/<TABLE>/RowsWritten
  StrataIngest/<TABLE>/DurationSeconds
  StrataIngest/<TABLE>/Failures
  StrataIngest/<TABLE>/RecoveryEvents
"""

import logging
from typing import Any

import boto3
from botocore.config import Config

log = logging.getLogger(__name__)

_BOTO_RETRY = Config(retries={"max_attempts": 5, "mode": "adaptive"})
NAMESPACE = "StrataIngest"


class Metrics:
    """Best-effort CloudWatch metrics. Failures here never crash the job."""

    def __init__(self, table_name: str, customer_id: str = "unknown"):
        self.table = table_name
        self.customer = customer_id
        self.cw = boto3.client("cloudwatch", config=_BOTO_RETRY)

    def emit(
        self,
        name: str,
        value: float,
        unit: str = "Count",
        extra_dimensions: dict[str, str] | None = None,
    ) -> None:
        dims = [
            {"Name": "Table", "Value": self.table},
            {"Name": "Customer", "Value": self.customer},
        ]
        for k, v in (extra_dimensions or {}).items():
            dims.append({"Name": k, "Value": str(v)})
        try:
            self.cw.put_metric_data(
                Namespace=NAMESPACE,
                MetricData=[
                    {
                        "MetricName": name,
                        "Dimensions": dims,
                        "Unit": unit,
                        "Value": value,
                    }
                ],
            )
        except Exception as exc:
            log.warning("CloudWatch metric emit failed (%s=%s): %s", name, value, exc)


def log_event(event: str, **kv: Any) -> None:
    """Structured log line. Easy to grep / parse from CloudWatch Logs Insights."""
    pairs = " ".join(f"{k}={v!r}" for k, v in kv.items())
    log.info("EVENT=%s %s", event, pairs)
