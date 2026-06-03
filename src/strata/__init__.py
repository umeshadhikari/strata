"""strata — idempotent RDBMS-to-Iceberg replication on AWS Glue."""

__version__ = "0.1.0"

from .exceptions import (
    ConcurrentRunError,
    ConfigError,
    IngestError,
    PermanentError,
    SchemaDriftError,
    SourceQueryError,
    SourceUnreachableError,
    StateConsistencyError,
    TransientError,
    WriteCommitError,
)

__all__ = [
    "__version__",
    "ConcurrentRunError",
    "ConfigError",
    "IngestError",
    "PermanentError",
    "SchemaDriftError",
    "SourceQueryError",
    "SourceUnreachableError",
    "StateConsistencyError",
    "TransientError",
    "WriteCommitError",
]
