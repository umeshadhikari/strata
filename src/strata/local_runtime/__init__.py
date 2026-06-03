"""
strata.local_runtime — local dev substitutes for AWS services.

Used by `python -m strata.local_ingest` to run the pipeline against
PostgreSQL + local filesystem + SQLite instead of Aurora + S3 + DynamoDB.

Same logical behavior as the AWS path. Same exceptions. Same state machine
semantics. Just different backends.
"""
