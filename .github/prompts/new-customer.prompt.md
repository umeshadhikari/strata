---
mode: agent
description: Stand up a new customer environment — Terraform, Secrets Manager, code upload, initial backfill, verification.
---

Onboard a new customer to strata. The deliverable is a working,
scheduled pipeline running in the customer's AWS account (or in a
vendor-owned account dedicated to them).

## Prerequisites — confirm before doing anything

Stop and surface the gap if any of these are missing:

1. **AWS account** — customer-owned or vendor-owned, decided. Customer ID
   established (used as resource prefix).
2. **AWS credentials** with permission to create the Terraform-managed
   resources: S3, KMS, Glue, IAM, DynamoDB, Secrets Manager, EventBridge,
   CloudWatch.
3. **AWS region** chosen.
4. **Data mart connection info** — JDBC URL, subnet ID that can reach it,
   security group ID(s), availability zone.
5. **Data mart credentials** — username + password. **DO NOT ASK THE USER
   TO PASTE THESE IN CHAT.** They go directly into Secrets Manager out of
   band (Step 3).
6. **Source DB engine and version** (Oracle / PostgreSQL).
7. **Terraform 1.5+** installed.
8. **`aws` CLI** configured locally.

## Deployment checklist — 10 steps

### Step 1: Configure Terraform variables

```bash
cd terraform
cp examples/single-customer/terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` with the customer-specific values:

```hcl
aws_region                    = "..."
customer_id                   = "..."
data_mart_jdbc_url            = "jdbc:oracle:thin:@//..."   # or jdbc:postgresql://...
data_mart_subnet_id           = "subnet-..."
data_mart_security_group_ids  = ["sg-..."]
data_mart_availability_zone   = "..."
```

### Step 2: Apply Terraform

```bash
terraform init
terraform plan
terraform apply
```

Expected: ~25 resources created (S3 buckets, KMS keys, Glue databases
and job, DynamoDB tables, Secrets Manager secret, IAM roles, EventBridge
rule, CloudWatch alarms).

### Step 3: Populate Secrets Manager — out of band

The user runs this themselves, in their own terminal, with credentials
they have locally. **Do not write the credentials in the chat
transcript.** Provide the command template:

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

For PostgreSQL, use `"engine":"postgresql"`, port 5432, `"database":"..."`,
and driver `"org.postgresql.Driver"`.

### Step 4: Upload code and config

```bash
cd ..
./scripts/deploy.sh "$(cd terraform && terraform output -raw scripts_bucket)"
```

This packages `src/strata/` as a zip, uploads it plus `examples/tables.yaml`
to the scripts bucket where the Glue job will find them.

### Step 5: Smoke test with one small table

Start with a tiny dimension so you can fail fast on connectivity issues:

```bash
JOB_NAME="$(cd terraform && terraform output -raw job_name)"

aws glue start-job-run \
  --job-name "$JOB_NAME" \
  --arguments '{"--TABLE_NAME":"DIM_CURRENCY","--FULL_REFRESH":"true"}'
```

Watch CloudWatch Logs for the `EVENT=run_completed` line. Expect ~3
minutes wall-clock. If it doesn't complete, debug with the `/debug`
workflow before continuing.

### Step 6: Verify the smoke test in Athena

```sql
SELECT COUNT(*), MAX(_ingest_timestamp)
FROM silver_shared.dim_currency;
```

Should return a row count > 0 and a recent timestamp.

### Step 7: Full backfill

```bash
./scripts/backfill_all.sh "$(cd terraform && terraform output -raw customer_id)"
```

Iterates dims first then facts, paces against Glue's concurrent-run
limit. Expected wall-clock: 20–60 minutes for a typical customer.

### Step 8: Confirm the schedule is wired

```bash
aws events list-rules \
  | jq '.Rules[] | select(.Name | contains("strata")) | {Name, State, ScheduleExpression}'
```

State should be `ENABLED`, schedule `cron(0 6 * * ? *)` for 06:00 UTC by
default. (Adjust per customer contract if needed.)

### Step 9: Monitor the first scheduled run

Wait until the next 06:00 UTC and check CloudWatch metrics:

- Namespace `StrataIngest`, dimension `Customer = <customer-id>`.
- `RowsWritten` per table — establishes daily delta baseline.
- `DurationSeconds` per table — establishes capacity baseline.
- `Failures` — must be 0.

### Step 10: Register the customer as live

In whatever central registry your team uses to track deployments:

- Customer ID
- Strata version deployed (`pip show strata`)
- AWS account ID
- Region
- Go-live date

## Common onboarding issues — and the fix

| Symptom | Cause | Fix |
|---|---|---|
| `terraform apply` fails: `InvalidIdentityToken` | AWS credentials expired | Refresh `aws sso login` or static creds |
| Glue job stuck in `STARTING` for > 5 min | Network can't reach source | Verify VPC route + security group ingress |
| First backfill fails with `ORA-01017` | Wrong credentials in Secret | Update Secrets Manager value |
| First backfill fails with `ORA-00942` | `source_schema` wrong in `tables.yaml` | Update `defaults.source_schema` |
| Athena `Table not found` after a "successful" run | The write didn't actually commit | Read CloudWatch logs for the real error |

## What this workflow does NOT do

- It does not modify code in this repo. If customer-specific behavior is
  needed, that's a YAML edit at most. Code paths per customer violate the
  framework's design.
- It does not set up disaster-recovery cross-region replication beyond
  Terraform defaults. DR is documented separately.
- It does not configure Lake Formation cross-account shares for client
  analytics. That's a separate workflow.

## Post-deployment

Once live, the pipeline runs itself. Operational responsibilities for
the team:

- Watch CloudWatch alarms (Terraform configured the standard set).
- Apply strata version updates via `git pull` + `./scripts/deploy.sh`.
- Add new source tables via the `/add-table` prompt — never as a code change.
