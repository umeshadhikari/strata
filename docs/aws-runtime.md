# AWS runtime — end-to-end walkthrough

A complete step-by-step guide to deploying strata into a customer's AWS
account, from `git clone` to the first scheduled production run, with Athena
and QuickSight wired in.

The local equivalent is in [`docs/local-runtime.md`](local-runtime.md). The
two flows are deliberately parallel — same logical pipeline, different
backends.

## Prerequisites

| Required | Version | Check |
|---|---|---|
| AWS account with admin or platform-admin role | — | `aws sts get-caller-identity` |
| AWS CLI v2 | 2.13+ | `aws --version` |
| Terraform | 1.5+ | `terraform version` |
| `jq` | 1.6+ | `jq --version` |
| A reachable source database | Oracle 19c+ or PostgreSQL 13+ | — |
| A VPC subnet + security group that can reach the source DB | — | — |

You do **not** need the source DB to be in AWS — it can be on-prem reachable
via Direct Connect or a VPN, or hosted in another cloud. The Glue Connection
just needs a network path.

## Step 0 — Clone and inspect

```bash
git clone https://github.com/your-org/strata.git
cd strata
```

## Step 1 — Configure Terraform

```bash
cd terraform
cp examples/single-customer/terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`. Required values:

```hcl
aws_region   = "eu-west-1"            # or whichever region the source DB is in
customer_id  = "acme"                 # used as resource prefix

# JDBC URL — adjust for your source engine
data_mart_jdbc_url = "jdbc:oracle:thin:@//db.internal:1521/PROD"
# or for PostgreSQL:
# data_mart_jdbc_url = "jdbc:postgresql://db.internal:5432/data_mart"

# Network — the subnet and security group Glue uses to reach the source DB
data_mart_subnet_id          = "subnet-0123456789abcdef0"
data_mart_security_group_ids = ["sg-0123456789abcdef0"]
data_mart_availability_zone  = "eu-west-1a"
```

The subnet must have an outbound route to the source DB. The security group
must allow inbound traffic from Glue's elastic network interfaces (Glue uses
the same SG it deploys into, so usually a self-referencing rule).

## Step 2 — Provision infrastructure

```bash
terraform init
terraform plan      # review what will be created
terraform apply     # type "yes" to confirm
```

Expected resources created (≈ 25):

- 2 S3 buckets (`<customer>-strata-lake`, `<customer>-strata-scripts`)
- 1 KMS Customer-Managed Key (lake encryption)
- 3 Glue Catalog databases (`silver_payments`, `silver_balances`, `silver_shared`)
- 1 DynamoDB table (`<customer>-strata-watermarks`)
- 1 Secrets Manager secret (empty placeholder)
- 1 IAM role + policies for the Glue job
- 1 Glue Connection (data-mart VPC route)
- 1 Glue Job (`<customer>-strata-ingest`)
- 1 Glue Workflow + EventBridge trigger (daily 06:00 UTC)
- 3 CloudWatch Alarms (`SchemaDriftAlerts`, `StateInconsistencyAlerts`, `Failures`)

Capture useful outputs:

```bash
export STRATA_JOB="$(terraform output -raw job_name)"
export STRATA_SECRET="$(terraform output -raw secret_name)"
export STRATA_SCRIPTS_BUCKET="$(terraform output -raw scripts_bucket)"
export STRATA_LAKE_BUCKET="$(terraform output -raw lake_bucket)"
export STRATA_WATERMARKS="$(terraform output -raw watermark_table)"
```

## Step 3 — Populate the secret with source credentials

**Out of band — never paste credentials in git or chat logs:**

```bash
aws secretsmanager put-secret-value \
  --secret-id "$STRATA_SECRET" \
  --secret-string '{
    "engine": "oracle",
    "host": "db.internal",
    "port": "1521",
    "service_name": "PROD",
    "username": "strata_reader",
    "password": "<paste-real-password>",
    "driver": "oracle.jdbc.OracleDriver"
  }'
```

For PostgreSQL:

```json
{
  "engine": "postgresql",
  "host": "db.internal",
  "port": "5432",
  "database": "data_mart",
  "username": "strata_reader",
  "password": "<paste-real-password>",
  "driver": "org.postgresql.Driver"
}
```

The `strata_reader` user needs `SELECT` on every table you plan to ingest,
plus permission to read its own session metadata.

## Step 4 — Update tables.yaml for your real data mart

Edit `examples/tables.yaml` to reflect your actual source. The shipped
example is for a payment-domain data mart; replace it with your tables.

For each table, set:

- `source_table` — exact table name in the source
- `source_schema` — in `defaults:` for the common case, or per-table override
- `domain` — drives the Glue database name (`silver_<domain>`)
- `watermark_column` — typically `LAST_UPDATED_TIME` or equivalent
- `primary_key` — list of PK columns
- `write_mode` — `append` for facts, `overwrite` for dims
- `partition_spec` — see [`docs/partitioning.md`](partitioning.md)

Validate the YAML before uploading:

```bash
python -c "import yaml; print(len(yaml.safe_load(open('examples/tables.yaml'))['tables']))"
```

## Step 5 — Upload code and config

```bash
cd ..        # back to repo root
./scripts/deploy.sh "$STRATA_SCRIPTS_BUCKET"
```

This:

1. Packages `src/strata/` as `strata.zip`.
2. Writes a small `main.py` entrypoint that calls `strata.ingest.main()`.
3. Uploads both to `s3://<scripts-bucket>/scripts/`.
4. Uploads `examples/tables.yaml` to `s3://<scripts-bucket>/config/`.

## Step 6 — Smoke-test with a single small table

Start with a dimension — small, fast, no partition complexity:

```bash
aws glue start-job-run \
  --job-name "$STRATA_JOB" \
  --arguments '{"--TABLE_NAME":"DIM_CURRENCY","--FULL_REFRESH":"true"}' \
  | jq -r '.JobRunId'
```

Watch the run:

```bash
RUN_ID="<paste from above>"
aws glue get-job-run --job-name "$STRATA_JOB" --run-id "$RUN_ID" \
  | jq '{State: .JobRun.JobRunState, Started: .JobRun.StartedOn, Args: .JobRun.Arguments}'
```

States: `STARTING → RUNNING → SUCCEEDED` (cold start adds 60–90 s).

If `FAILED`, retrieve the logs:

```bash
aws glue get-job-run --job-name "$STRATA_JOB" --run-id "$RUN_ID" \
  | jq -r '.JobRun.LogGroupName'

# Then in CloudWatch Logs Insights:
# fields @timestamp, @message
# | filter @logStream like /<RUN_ID>/
# | filter @message like /EVENT=/
# | sort @timestamp desc
```

Look for the latest `EVENT=` marker. Map it to a recovery action via the
`glue-debugger` agent or [`docs/operational-runbook.md`](operational-runbook.md).

## Step 7 — Verify in Athena

Open the Athena console in the AWS region you deployed to.

```sql
-- Check that the database is registered and contains the table
SHOW DATABASES;
USE silver_shared;
SHOW TABLES;

-- Row count + freshness
SELECT COUNT(*), MAX(last_updated_time)
FROM silver_shared.dim_currency;

-- Inspect the Iceberg snapshot history — the source of truth for what
-- has been committed
SELECT
    snapshot_id,
    committed_at,
    operation,
    summary['glue.run_id']           AS run_id,
    summary['glue.watermark_upper']  AS watermark_upper,
    summary['glue.row_count']        AS rows_in_this_commit
FROM "silver_shared"."dim_currency$snapshots"
ORDER BY committed_at DESC;
```

You should see one snapshot with your full-refresh run's `run_id`.

## Step 8 — Full backfill

Once the smoke test succeeds, kick off the full backfill across all tables:

```bash
./scripts/backfill_all.sh "$(terraform -chdir=terraform output -raw customer_id)"
```

This iterates through the table list in the script, running each as a
separate Glue job run. Glue has a default concurrency limit (~10 simultaneous
runs); the script paces with `sleep 3` between submissions.

Watch progress:

```bash
aws glue get-job-runs --job-name "$STRATA_JOB" --max-results 30 \
  | jq '.JobRuns[] | {State: .JobRunState, Args: .Arguments["--TABLE_NAME"], Started: .StartedOn}' \
  | head -100
```

Expected wall-clock: **20–60 minutes** for a customer with the example table
set and typical row volumes. Heavier tables (`FACT_TRANSACTION` if present)
benefit from the `parallel_extract` config in `tables.yaml`.

## Step 9 — Inspect state in DynamoDB

The watermark state machine lives in DynamoDB:

```bash
aws dynamodb scan --table-name "$STRATA_WATERMARKS" \
  | jq '.Items[] | {table: .table_name.S, watermark: .current_watermark.S, status: .last_run_status.S}'
```

Expected output:

```json
{"table": "DIM_CURRENCY", "watermark": "2026-06-01T12:34:56+00:00", "status": "COMPLETED"}
{"table": "FACT_PAYMENT", "watermark": "2026-06-01T12:35:30+00:00", "status": "COMPLETED"}
...
```

## Step 10 — Set up QuickSight

### Enable QuickSight (one-time per AWS account)

In the AWS Console: **QuickSight → Sign up**. Choose:

- **Standard** or **Enterprise** edition (Enterprise required for embedded /
  RLS / per-session pricing)
- Region: same as your deployment
- IAM role: let QuickSight create one, OR use a pre-created role with
  permissions on the Glue Catalog, Athena, and the lake S3 bucket
- Workgroup: `primary` (default)

### Grant QuickSight access to the Athena tables

```bash
aws quicksight describe-iam-policy-assignment \
  --aws-account-id "$(aws sts get-caller-identity --query Account --output text)" \
  --namespace default \
  --assignment-name QuickSightAccessForUsers || true

# Or via the QuickSight admin UI:
# QuickSight → Manage QuickSight → Security & permissions → IAM Role
# → Select S3 buckets → check the lake bucket
```

### Create a dataset

1. **QuickSight → Datasets → New dataset → Athena**
2. **Data source name**: `strata-athena`
3. **Workgroup**: `primary` (or whichever workgroup Terraform created)
4. Click **Validate connection** then **Create data source**
5. **Database**: `silver_payments`
6. **Tables**: select `fact_payment`
7. **Edit / Preview data** → uncheck the four `_ingest_*` metadata columns
   to clean up the field list (they remain queryable, just hidden from the UI)
8. **Save & visualize**

### Build a chart

1. Visualization type: **Line chart** (or whatever you prefer)
2. **X axis**: `value_date` (aggregation: Day)
3. **Value**: `amount` (aggregation: SUM)
4. **Color**: `data_owner_id`
5. Save as **"Daily Payment Volume by Owner"**

### Build a dashboard

1. Top right: **Share → Publish dashboard**
2. Name: **"Payment Operations"**
3. Add to favorites for quick access

## Step 11 — Configure row-level security (RBAC)

If your data mart's `data_owner_id` column drives access permissions, set up
QuickSight row-level security so each user sees only their permitted rows.
See [`docs/configuration.md`](configuration.md) — the full pattern uses Lake
Formation tags + QuickSight RLS dataset. Outline:

1. Create a permissions table in S3 mapping user emails to allowed
   `data_owner_id` values.
2. Register it as a QuickSight dataset.
3. **Datasets → fact_payment → Row-level security → Permissions dataset**:
   select the permissions dataset.
4. Test by signing in as a different user — they should see only their
   permitted rows.

## Step 12 — Verify the daily schedule

The EventBridge trigger fires at 06:00 UTC daily. Confirm it's active:

```bash
aws events list-rules \
  | jq '.Rules[] | select(.Name | contains("strata")) | {Name, State, ScheduleExpression}'
```

State should be `ENABLED`, schedule `cron(0 6 * * ? *)`.

To trigger manually for testing:

```bash
aws events put-events --entries '[{
  "Source": "strata.manual",
  "DetailType": "TestTrigger",
  "Detail": "{}"
}]'
```

Or just wait until the next 06:00 UTC.

## Step 13 — Monitoring

### CloudWatch Alarms (created by Terraform)

| Alarm | Fires when | Severity |
|---|---|---|
| `<customer>-strata-failures` | A run exhausted retries and failed | P2 |
| `<customer>-strata-schema-drift` | `SchemaDriftError` raised | P2 |
| `<customer>-strata-state-inconsistent` | Recovery couldn't auto-reconcile | P1 |

Wire each to an SNS topic + your on-call routing:

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "<customer>-strata-failures" \
  --alarm-actions "arn:aws:sns:..."
```

### Per-table metrics

Namespace: `StrataIngest`. Dimensions: `Table`, `Customer`. Metrics:

| Metric | Watch for |
|---|---|
| `RowsWritten` | Should be > 0 daily for active facts |
| `DurationSeconds` | p95 baseline; alarm if 3× baseline |
| `Failures` | Sum > 0 over 1h |
| `SchemaDriftAlerts` | Sum > 0 ever |
| `StateInconsistencyAlerts` | Sum > 0 ever |
| `ConcurrentRunSkips` | Recurring = scheduling overlap; investigate |
| `IdempotentSkips` | Occasional = expected (Glue auto-retry caught a prior commit) |

### Daily sanity SQL

Athena:

```sql
SELECT table_name, current_watermark, last_run_status
FROM "AwsDataCatalog"."<watermark-table>"
WHERE last_run_completed_at < (CURRENT_TIMESTAMP - INTERVAL '24' HOUR);
```

Should return zero rows in a healthy deployment.

## Step 14 — Day-2 operations

| Need | Procedure |
|---|---|
| Add a new source table | Use the [`add-source-table` skill](../.claude/skills/add-source-table/SKILL.md); edit `examples/tables.yaml`; re-upload via `./scripts/deploy.sh`; backfill the one table |
| Debug a failure | Use the [`diagnose-failure` skill](../.claude/skills/diagnose-failure/SKILL.md) or the [`glue-debugger` agent](../.claude/agents/glue-debugger.md) |
| Schema drift | Use the [`schema-drift-resolver` agent](../.claude/agents/schema-drift-resolver.md); run the ALTER it produces in Athena; rerun |
| Reprocess yesterday | Edit the DynamoDB watermark back, trigger a manual run. See [`docs/operational-runbook.md`](operational-runbook.md) |
| Drop and rebuild a table | `DROP TABLE` in Athena, delete the DynamoDB row, rerun with `--FULL_REFRESH=true` |

## Step 15 — Onboarding the next customer

Repeat steps 1–6 for each customer. Each customer is a separate
`terraform.tfvars` in their own state file (or workspace). The same code
artifact (`strata.zip` + `tables.yaml`) is uploaded to each customer's
scripts bucket.

See the [`new-customer-stamp` skill](../.claude/skills/new-customer-stamp/SKILL.md)
for the full operational checklist.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `terraform apply` fails: insufficient permissions | Check IAM role; needs Glue, S3, KMS, IAM, DynamoDB, EventBridge, CloudWatch, SecretsManager create permissions |
| Glue job stuck in `STARTING` | The Glue Connection can't reach the source; check VPC route + SG ingress |
| `ORA-01017` / authentication failed | Wrong credentials in Secrets Manager; update with `put-secret-value` |
| `ORA-00942` / table or view does not exist | `source_schema` wrong in `tables.yaml`; check `defaults.source_schema` |
| Athena: "Table not found" | First-run write didn't commit; check CloudWatch logs for the actual error |
| Athena query slow on large tables | Check partition spec is set; see [`docs/partitioning.md`](partitioning.md) |
| QuickSight: "No data" | Verify the QuickSight IAM role has S3 + Athena access to the lake and workgroup |
| `concurrent runs` alarm fires repeatedly | Two schedules overlapping; check EventBridge rules |

## What you've verified

After completing this walkthrough you have:

- ✓ A production deployment of strata in an AWS account
- ✓ Aurora / source DB → Iceberg lake on S3 via Glue PySpark
- ✓ Glue Data Catalog + Lake Formation governance
- ✓ DynamoDB-backed watermark state machine
- ✓ CloudWatch alarms on the three reliability SLOs
- ✓ Athena queryability + QuickSight dashboards
- ✓ Daily EventBridge schedule
- ✓ Documented runbooks for day-2 operations

The same logical pipeline ran locally in [`docs/local-runtime.md`](local-runtime.md).
