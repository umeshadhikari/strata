"""
Custom exception hierarchy.

Exception type drives error handling:
  - TransientError       → retry with backoff
  - PermanentError       → fail fast, alert
  - ConcurrentRunError   → another run holds the lock; this run exits without retry
  - SchemaDriftError     → operator intervention required
  - StateConsistencyError → DynamoDB / Iceberg cannot be auto-reconciled
"""


class IngestError(Exception):
    """Base for all ingestion errors."""

    transient = False


class TransientError(IngestError):
    """A retriable error. Job should retry with backoff."""

    transient = True


class PermanentError(IngestError):
    """A non-retriable error. Job should fail fast."""

    transient = False


class ConfigError(PermanentError):
    """Bad configuration — table not in YAML, missing required field, etc."""


class SchemaDriftError(PermanentError):
    """
    Source schema changed in a way Iceberg can't handle automatically.
    Examples: column renamed, type changed from string to int.
    """


class ConcurrentRunError(PermanentError):
    """
    Another run holds the lock on this table. Not a failure — just exit cleanly.
    The other run will complete and the next scheduled invocation will pick up.
    """


class SourceUnreachableError(TransientError):
    """Cannot connect to data mart. Network, auth, or DB down."""


class SourceQueryError(TransientError):
    """Query failed mid-execution. Worth retrying."""


class WriteCommitError(TransientError):
    """
    Iceberg commit failed (likely concurrent writer conflict).
    Retry with backoff — Iceberg uses optimistic concurrency.
    """


class StateConsistencyError(PermanentError):
    """
    DynamoDB and Iceberg are in an inconsistent state that needs operator review.
    The recovery logic was unable to reconcile them automatically.
    """
