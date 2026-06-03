# Terraform — strata infrastructure

Provisions everything needed to run strata in one AWS account:

| Resource | Purpose |
|---|---|
| S3 bucket `<customer>-strata-lake` | Iceberg data files |
| S3 bucket `<customer>-strata-scripts` | Job code and YAML config |
| KMS Customer-Managed Key | Encryption at rest |
| Glue databases (per domain) | Catalog organization |
| DynamoDB table `<customer>-strata-watermarks` | State machine |
| Secrets Manager secret | Source-DB credentials |
| IAM role + policies | Glue execution identity |
| Glue Connection | VPC route to source DB |
| Glue Job | The parameterised ingest job |
| Glue Workflow + Trigger | Daily 06:00 UTC schedule |
| CloudWatch Alarms | Schema drift, state inconsistency, failures |

## Quick start

```bash
cp examples/single-customer/terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars
terraform init
terraform apply
```

## Required variables

See `variables.tf`. The minimum set:

- `customer_id` — resource prefix
- `data_mart_jdbc_url` — JDBC URL for the Glue Connection
- `data_mart_subnet_id` — subnet Glue uses to reach the source DB
- `data_mart_security_group_ids` — security group(s) for the connection
- `data_mart_availability_zone` — AZ of the subnet

## After apply

1. Populate the Secrets Manager secret:
   ```bash
   aws secretsmanager put-secret-value \
     --secret-id "$(terraform output -raw secret_name)" \
     --secret-string '{"engine":"oracle","host":"...","port":"1521","service_name":"...","username":"...","password":"...","driver":"oracle.jdbc.OracleDriver"}'
   ```

2. Upload code and config:
   ```bash
   cd ../scripts
   ./deploy.sh "$(cd ../terraform && terraform output -raw scripts_bucket)"
   ```

3. Run an initial test:
   ```bash
   aws glue start-job-run \
     --job-name "$(terraform output -raw job_name)" \
     --arguments '{"--TABLE_NAME":"DIM_CURRENCY","--FULL_REFRESH":"true"}'
   ```

4. Backfill everything:
   ```bash
   ../scripts/backfill_all.sh "$(terraform output -raw customer_id)"
   ```

## Per-customer deployment

For multi-customer dedicated deployments, deploy this module once per customer
account (or once per customer in a vendor account with `customer_id` namespacing).
The module is designed to be the "customer stamp" — apply it cleanly with no
external state requirements beyond standard Terraform backends.

## Cost (per customer, typical)

| Item | Approx monthly |
|---|---|
| Glue PySpark (30 DPU-hours) | ~$13 |
| S3 storage (500 GB) | ~$12 |
| DynamoDB on-demand | ~$1 |
| KMS keys + requests | ~$5 |
| Secrets Manager | ~$1 |
| CloudWatch logs + metrics | ~$15 |
| **Total** | **~$50/month** |

Plus Athena queries paid by consumers.
