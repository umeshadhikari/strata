# Trax Silver Ingestion — AWS Infrastructure
# ============================================
# Provisions: S3 buckets, Glue databases, Glue job, IAM roles, Secrets Manager,
# DynamoDB watermark table, EventBridge daily trigger.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# --------------------------------------------------------------------------- #
# S3 buckets — lake + jobs/config
# --------------------------------------------------------------------------- #
resource "aws_s3_bucket" "lake" {
  bucket = "${var.customer_id}-strata-lake"
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.lake.arn
    }
  }
}

resource "aws_s3_bucket" "scripts" {
  bucket = "${var.customer_id}-strata-scripts"
}

resource "aws_kms_key" "lake" {
  description             = "Trax Silver lake encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

# --------------------------------------------------------------------------- #
# Glue databases (one per domain)
# --------------------------------------------------------------------------- #
resource "aws_glue_catalog_database" "domains" {
  for_each = toset(["silver_payments", "silver_balances", "silver_shared"])
  name     = each.value

  description = "Trax Silver — ${each.value}"
}

# --------------------------------------------------------------------------- #
# DynamoDB — ingest watermark state
# --------------------------------------------------------------------------- #
resource "aws_dynamodb_table" "watermarks" {
  name         = "${var.customer_id}-strata-watermarks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "table_name"

  # Other attributes (current_watermark, pending_run_id, pending_window_lower,
  # pending_window_upper, pending_started_at, pending_expires_at, last_run_id,
  # last_run_status, last_run_rows, last_run_completed_at, last_run_error,
  # version) are schemaless attributes managed by the application code.
  attribute {
    name = "table_name"
    type = "S"
  }

  point_in_time_recovery { enabled = true }

  # Cross-region replication for DR
  # Uncomment to enable Global Tables:
  # replica {
  #   region_name = "eu-west-2"
  # }
}

# --------------------------------------------------------------------------- #
# Secrets Manager — data mart JDBC credentials
# --------------------------------------------------------------------------- #
resource "aws_secretsmanager_secret" "data_mart" {
  name = "${var.customer_id}/strata/data-mart/credentials"
}

# Operator populates the secret value out of band with:
# aws secretsmanager put-secret-value --secret-id <name> --secret-string '{
#   "engine": "oracle",  # or "postgresql"
#   "host": "...",
#   "port": "1521",
#   "service_name": "...",
#   "username": "...",
#   "password": "...",
#   "driver": "oracle.jdbc.OracleDriver"
# }'

# --------------------------------------------------------------------------- #
# IAM — Glue job execution role
# --------------------------------------------------------------------------- #
resource "aws_iam_role" "glue_job" {
  name = "${var.customer_id}-strata-glue-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue_job.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

resource "aws_iam_role_policy" "glue_job_inline" {
  role = aws_iam_role.glue_job.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.lake.arn,
          "${aws_s3_bucket.lake.arn}/*",
          aws_s3_bucket.scripts.arn,
          "${aws_s3_bucket.scripts.arn}/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.data_mart.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = [aws_dynamodb_table.watermarks.arn]
      },
      {
        Effect = "Allow"
        Action = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey",
                  "kms:DescribeKey", "kms:ReEncryptFrom", "kms:ReEncryptTo"]
        Resource = [aws_kms_key.lake.arn]
      },
      {
        Effect = "Allow"
        Action = ["glue:*Table*", "glue:*Database*", "glue:*Partition*"]
        Resource = ["*"]
      },
    ]
  })
}

# --------------------------------------------------------------------------- #
# Glue Connection — to data mart VPC
# --------------------------------------------------------------------------- #
resource "aws_glue_connection" "data_mart" {
  name            = "${var.customer_id}-trax-data-mart"
  connection_type = "JDBC"

  connection_properties = {
    JDBC_CONNECTION_URL = var.data_mart_jdbc_url   # set in terraform.tfvars
    USERNAME            = "placeholder"            # actual creds come from Secrets Manager
    PASSWORD            = "placeholder"
  }

  physical_connection_requirements {
    availability_zone      = var.data_mart_availability_zone
    security_group_id_list = var.data_mart_security_group_ids
    subnet_id              = var.data_mart_subnet_id
  }
}

# --------------------------------------------------------------------------- #
# Glue Job — the single parameterised ingest job
# --------------------------------------------------------------------------- #
resource "aws_glue_job" "ingest" {
  name              = "${var.customer_id}-strata-ingest"
  role_arn          = aws_iam_role.glue_job.arn
  glue_version      = "4.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  max_retries       = 1       # AWS retry preserves JOB_RUN_ID → our idempotency uses it
  timeout           = 90      # minutes; lock TTL is 120 min so retry has buffer

  connections = [aws_glue_connection.data_mart.name]

  command {
    name            = "glueetl"
    script_location = "s3://${aws_s3_bucket.scripts.id}/scripts/ingest_table.py"
    python_version  = "3"
  }

  default_arguments = {
    "--job-language"             = "python"
    "--enable-glue-datacatalog"  = "true"
    "--enable-metrics"           = "true"
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-job-insights"      = "true"

    # Iceberg + Glue catalog
    "--datalake-formats"         = "iceberg"
    "--conf"                     = "spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions"

    # Make our common/ package importable on workers
    "--extra-py-files"           = "s3://${aws_s3_bucket.scripts.id}/scripts/common.zip"

    # Custom job args (defaults; overridden per run)
    "--CONFIG_S3_URI"            = "s3://${aws_s3_bucket.scripts.id}/config/tables.yaml"
    "--LAKE_S3_URI"              = "s3://${aws_s3_bucket.lake.id}/silver/"
    "--SECRET_NAME"              = aws_secretsmanager_secret.data_mart.name
    "--WATERMARK_TABLE"          = aws_dynamodb_table.watermarks.name
    "--CUSTOMER_ID"              = var.customer_id
    "--FULL_REFRESH"             = "false"

    # Extra Python libs
    "--additional-python-modules" = "pyyaml==6.0.1"
  }
}

# CloudWatch alarms on the reliability metrics
resource "aws_cloudwatch_metric_alarm" "schema_drift" {
  alarm_name          = "${var.customer_id}-strata-schema-drift"
  alarm_description   = "Source schema changed in a way Iceberg can't auto-handle"
  metric_name         = "SchemaDriftAlerts"
  namespace           = "StrataIngest"
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 300
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  dimensions = {
    Customer = var.customer_id
  }
}

resource "aws_cloudwatch_metric_alarm" "state_inconsistent" {
  alarm_name          = "${var.customer_id}-strata-state-inconsistent"
  alarm_description   = "DynamoDB / Iceberg state could not be auto-reconciled"
  metric_name         = "StateInconsistencyAlerts"
  namespace           = "StrataIngest"
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 300
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  dimensions = {
    Customer = var.customer_id
  }
}

resource "aws_cloudwatch_metric_alarm" "failures" {
  alarm_name          = "${var.customer_id}-strata-failures"
  alarm_description   = "Ingest job failed after retries"
  metric_name         = "Failures"
  namespace           = "StrataIngest"
  statistic           = "Sum"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  period              = 900
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  dimensions = {
    Customer = var.customer_id
  }
}

# --------------------------------------------------------------------------- #
# Glue Workflow — DAG that fans out per table
# --------------------------------------------------------------------------- #
resource "aws_glue_workflow" "daily_ingest" {
  name        = "${var.customer_id}-strata-daily-ingest"
  description = "Daily fan-out of data-mart-to-Iceberg ingestion across all configured tables"
}

resource "aws_glue_trigger" "daily_schedule" {
  name          = "${var.customer_id}-strata-daily-trigger"
  type          = "SCHEDULED"
  schedule      = "cron(0 6 * * ? *)"   # 06:00 UTC daily
  workflow_name = aws_glue_workflow.daily_ingest.name

  actions {
    job_name = aws_glue_job.ingest.name
    arguments = {
      "--TABLE_NAME"   = "FACT_PAYMENT"
      "--FULL_REFRESH" = "false"
    }
  }
}

# Repeat the trigger pattern (or use a Step Functions DAG for fan-out) for every
# table you want in the daily workflow. For >10 tables, switch to Step Functions
# Standard workflow — much cleaner than Glue triggers per table.
