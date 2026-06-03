output "customer_id" {
  description = "Customer identifier prefix"
  value       = var.customer_id
}

output "lake_bucket" {
  description = "S3 bucket for the Iceberg lake"
  value       = aws_s3_bucket.lake.id
}

output "scripts_bucket" {
  description = "S3 bucket for Glue scripts and config"
  value       = aws_s3_bucket.scripts.id
}

output "job_name" {
  description = "Glue job name"
  value       = aws_glue_job.ingest.name
}

output "watermark_table" {
  description = "DynamoDB watermark table name"
  value       = aws_dynamodb_table.watermarks.name
}

output "secret_name" {
  description = "Secrets Manager secret name for data-mart credentials"
  value       = aws_secretsmanager_secret.data_mart.name
}

output "kms_key_arn" {
  description = "KMS key used to encrypt lake storage"
  value       = aws_kms_key.lake.arn
}

output "glue_databases" {
  description = "Glue Data Catalog databases created"
  value       = [for db in aws_glue_catalog_database.domains : db.name]
}
