---
name: new-customer-stamp
description: Stand up a complete strata deployment for a new customer. Covers Terraform apply, Secrets Manager population, code upload, initial backfill, and verification. Use when onboarding a new customer to the strata pipeline. Triggers include: "new customer", "onboard customer", "stamp out customer", "deploy strata for".
---

# Skill: new-customer-stamp

## When to use this skill

A new customer needs a strata deployment in their dedicated AWS account (or in a vendor-owned account dedicated to them). This skill produces the full onboarding checklist and walks through each step.

Trigger phrases: "new customer", "onboard <name>", "stamp out a customer", "deploy strata for <customer>".

## Prerequisites checklist

Before starting, confirm:

1. **AWS account** — customer-owned or vendor-owned, decided. Customer ID established.
2. **AWS credentials** with permission to create the Terraform-managed resources (S3, KMS, Glue, IAM, DynamoDB, Secrets Manager, EventBridge, CloudWatch).
3. **Data mart access** — JDBC URL, the subnet and security group that can reach it, and the AZ.
4. **Data mart credentials** — username + password. **These are entered separately into Secrets Manager and never written to terraform state or chat logs.**
5. **Source DB engine and version** (Oracle / PostgreSQL).
6. **Terraform 1.5+** installed.
7. **`aws` CLI** configured with appropriate credentials.

If any are missing, stop and surface the gap.

## Deployment checklist

### Step 1: Configure Terraform variables

```bash
cd terraform
cp examples/single-customer/terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` with the customer-specific values:

```hcl
aws_region                    = "..."
customer_id                   = "..."
data_mart_jdbc_url            = "jdbc:oracle:thin:@//..."
data_mart_subnet_id           = "subnet-..."
data_mart_security_group_ids  = ["sg-..."]
data_mart_availability_zone   = "..."
```

### Step 2: Apply Terraform

```bash
terraform init
terraform plan      # review the plan
terraform apply
```

Expected resources created: ~25 (S3 buckets, KMS, Glue databases, Glue job, DynamoDB, Secrets Manager, IAM, EventBridge, CloudWatch alarms).

### Step 3: Populate Secrets Manager

**Out of band** — never paste credentials in chat:

```bash
aws secretsmanager put-secret-value \
  --secret-id "$(terraform output -raw secret_name)" \
  --secret-string '{
    "engine": "oracle",
    "host": "...",
    "port": "1521",
    "service_name": "...",
    "username": "...",
    "password": "...",
    "driver": "oracle.jdbc.OracleDriver"
  }'
```

For PostgreSQL, use `"engine":"postgresql"`, port 5432, `"database":"..."`, and driver `"org.postgresql.Driver"`.

### Step 4: Upload code and config

```bash
cd ..
./scripts/deploy.sh "$(cd terraform && terraform output -raw scripts_bucket)"
```

This packages `src/strata/` as a zip, uploads it plus `examples/tables.yaml` to the scripts bucket where the Glue job will find them.

### Step 5: Test with a single small table

Start with `DIM_CURRENCY` (small, no PII concerns):

```bash
JOB_NAME="$(cd terraform && terraform output -raw job_name)"

aws glue start-job-run \
  --job-name "$JOB_NAME" \
  --arguments '{"--TABLE_NAME":"DIM_CURRENCY","--FULL_REFRESH":"true"}'
```

Watch in CloudWatch Logs for the `EVENT=run_completed` line. Expect ~3 minutes wall-clock.

### Step 6: Verify the table is queryable

In Athena:

```sql
SELECT COUNT(*), MAX(_ingest_timestamp)
FROM silver_shared.dim_currency;
```

Should return a count > 0 and a recent timestamp.

### Step 7: Full backfill

```bash
./scripts/backfill_all.sh "$(cd terraform && terraform output -raw customer_id)"
```

This iterates through dims first then facts. Expected wall-clock: 20–60 minutes for a typical customer depending on data volume.

### Step 8: Schedule check

The EventBridge daily trigger is already wired by Terraform. Confirm:

```bash
aws events list-rules \
  | jq '.Rules[] | select(.Name | contains("strata")) | {Name, State, ScheduleExpression}'
```

State should be `ENABLED`, schedule `cron(0 6 * * ? *)` for 06:00 UTC.

### Step 9: Monitor first scheduled run

Wait for the next 06:00 UTC. Check CloudWatch metrics in namespace `StrataIngest` with `Customer = <customer-id>`:
- `RowsWritten` per table — should be the daily delta volume.
- `DurationSeconds` per table — establishes the baseline.
- `Failures` — should be 0.

### Step 10: Tag the customer as live

In your central customer registry (or however your team tracks deployments):
- Customer ID
- Version of strata deployed
- AWS account ID
- Date of go-live

## Post-deployment

Once live, the system runs itself. Operational responsibilities:
- Watch CloudWatch alarms (configured by Terraform).
- Apply strata version updates via `git pull` + `./scripts/deploy.sh`.
- Add new tables via the `add-source-table` skill.

## Common issues during onboarding

| Symptom | Cause | Fix |
|---|---|---|
| Terraform apply fails: "InvalidIdentityToken" | AWS credentials expired | Refresh `aws sso login` or static credentials |
| Glue job stuck in STARTING | Connection can't reach source | Verify VPC route + security group ingress |
| First backfill fails: ORA-01017 | Wrong credentials in Secret | Update Secrets Manager value |
| First backfill fails: ORA-00942 | `source_schema` wrong in tables.yaml | Update `defaults.source_schema` |
| Athena query: "Table not found" | First-run write didn't commit | Check CloudWatch logs for the actual error |

## What this skill does NOT do

- It does not modify code in this repo. If customer-specific behavior is needed, that's a YAML edit at most.
- It does not handle disaster recovery setup beyond Terraform defaults. DR is documented separately.
- It does not configure Lake Formation cross-account shares for client analytics. That's a separate skill (UC2 / UC3 sharing setup).
