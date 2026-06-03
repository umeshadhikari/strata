# Translating a data-mart DDL into strata's `tables.yaml`

You have a complete CREATE TABLE DDL for your source data mart. You want
to ingest it through strata, which means producing a `tables.yaml` that
lists every table to replicate. This guide describes the recommended
workflow, including a script that does the mechanical extraction so you
only have to make judgment calls.

The DDL itself never has to leave the laptop — both the script and the
AI assistant review run locally.

## What's in scope for the translation

Each strata `tables.yaml` entry has six pieces:

| Field | Source | Automation |
|---|---|---|
| Logical key (e.g. `FACT_PAYMENT`) | Table name, uppercased | Script does it |
| `source_table` | Table name, lowercased | Script does it |
| `primary_key` | `PRIMARY KEY` constraint | Script does it |
| `watermark_column` | Heuristic on column names | Script proposes; you confirm |
| `partition_spec` | Heuristic on date columns | Script proposes a `days()` start; you refine |
| `domain` | Business categorisation | You decide |
| `sort_order` (optional) | Query patterns | You decide |

The script handles the mechanical 50%; the judgment 50% is where Copilot
on your office laptop helps.

## Step 1 — Generate the draft on the office laptop

You don't need Docker, Spark, or anything strata-stack-specific for
this step. Just Python 3.10+.

```bash
# From the repo root
python3 local/scripts/ddl_to_yaml.py path/to/your/datamart.ddl.sql > tables.draft.yaml
```

If the DDL is split across multiple files (one per schema, one per
domain, etc.), pass them all in:

```bash
python3 local/scripts/ddl_to_yaml.py \
  schemas/payments.sql \
  schemas/balances.sql \
  schemas/shared.sql \
  > tables.draft.yaml
```

If you already know every table belongs to the same domain, save
keystrokes:

```bash
python3 local/scripts/ddl_to_yaml.py --default-domain payments your.ddl.sql > tables.draft.yaml
```

You should see something like this on stderr:

```
# Extracted 47 tables from your.ddl.sql
```

If you see `ERROR: no CREATE TABLE statements found`, the script
couldn't parse the file — usually because the DDL is in a dialect
the regex doesn't recognise (e.g. SQL Server's bracketed identifiers
without `IF NOT EXISTS`). Open the SQL in an editor, paste a single
`CREATE TABLE` block into a fresh file, and re-run the script on
just that to see whether it parses. If it does, the issue was
something else in the file — probably an unrelated DDL construct.

## Step 2 — Review the draft

Open `tables.draft.yaml` in VS Code. Every table will look something
like this:

```yaml
  # ----- FACT_PAYMENT -----
  # columns: payment_id:number(18) | data_owner_id:number(10) | ...
  FACT_PAYMENT:
    source_table: fact_payment
    domain: TODO_domain  # one of: shared, payments, balances, etc.
    watermark_column: last_updated_time
    primary_key: [payment_id]
    partition_spec:
      - { transform: days, column: value_date }
      # TODO: for large facts add a bucket transform on the PK, e.g.:
      # - { transform: bucket, column: payment_id, n: 16 }
    # sort_order: TODO_sort  # optional Iceberg sort; depends on query patterns
```

Three things to resolve per table:

### Resolve TODO\_domain

Pick from your shop's silver schemas: typically `shared` for conformed
dimensions (currency, account, data_owner), `<area>` for facts and
area-specific dimensions (`payments`, `balances`, `trades`,
`positions`, etc.). The conventional rule of thumb: anything used by
multiple facts goes under `shared`; everything else under the
fact's own domain.

### Verify `watermark_column`

The script picks the first column matching a known pattern
(`last_updated_time`, `updated_at`, etc.). Confirm it actually
monotonically advances — some DDLs have a column called
`updated_time` that's only populated on initial insert, not on
update. If that's the case, find the real "changed since" timestamp
and substitute.

If the script left `watermark_column: TODO_watermark`, no candidate
matched. Pick the column that the source system advances every time a
row changes. If there isn't one, you have a CDC problem larger than
strata — talk to the data-mart team.

### Refine `partition_spec`

The script always suggests `days(<date>)` if there's a plausible
business-date column. Apply this rule of thumb based on annual row
volume:

- **< 1M rows / year** → drop the partition spec: `partition_spec: []`. The
  overhead of metadata files exceeds the query benefit.
- **1M – 100M rows / year** → keep just `days(<date>)`. Reads pruning to
  one or a few days are dramatically faster.
- **\> 100M rows / year, or hot daily partitions (one customer / data
  owner dominates)** → add a `bucket` transform on the PK. The
  TODO comment in the draft shows the syntax.

If you don't know the volume, ask the data-mart team for daily row
counts before guessing.

### Optionally pick `sort_order`

Uncomment and fill in `sort_order: [col1, col2]` only if you know a
specific query pattern. Two examples:

- Trade tables that are usually queried `WHERE symbol = 'X'`:
  `sort_order: [symbol, trade_date, trade_id]`
- Payment tables queried `WHERE counterparty_country = 'X'`:
  `sort_order: [counterparty_country, value_date, payment_id]`

If no clear pattern, leave it out. Iceberg will sort by partition key
by default, which is usually fine.

## Step 3 — Use Copilot to power through the TODOs

Once you have the draft, open it alongside your DDL in VS Code. With
GitHub Copilot Chat enabled, the most efficient pattern is:

```
@workspace /add-table

I have a draft tables.yaml entry for FACT_PAYMENT in tables.draft.yaml.
Here's the table's annual row volume: 800M.
Common query patterns: WHERE value_date BETWEEN X AND Y
                       WHERE data_owner_id = N AND value_date = X
Please review the draft and finalise the partition_spec and sort_order.
```

Copilot, reading `.github/prompts/add-table.prompt.md` and `AGENTS.md`,
will produce the recommended partition spec (likely `days(value_date) +
bucket(N, data_owner_id)`) and a sort order. Repeat per table.

For the conformed dimensions (`shared` domain), Copilot doesn't need
much input — they're tiny and don't need partitioning. You can usually
just set `domain: shared` and `partition_spec: []` in one pass.

## Step 4 — Sanity-check before committing

Two quick checks before the YAML goes into the repo.

**A: YAML parses cleanly.** Tactical, mechanical check:

```bash
python3 -c "import yaml, sys; yaml.safe_load(open('tables.draft.yaml')); print('✓ YAML valid')"
```

**B: Every required field is present and no TODO markers remain.**
Strata's config validator (`src/strata/config.py:load_config`) fails
fast on missing fields, but it's nicer to catch them locally:

```bash
grep -n TODO tables.draft.yaml && echo "✗ TODOs still present, resolve them"
```

If both pass, you're ready to commit.

## Incremental updates — when the DDL evolves

Your data mart will grow. New tables will be added; occasionally one
will be dropped. You don't want to lose the resolved domains, partition
specs, and watermark choices you put into `tables.yaml` over time, but
you also don't want to hand-track diffs against a fresh-extract draft.

The script's `--merge` mode does this for you:

```bash
python3 local/scripts/ddl_to_yaml.py \
    --merge local/config/tables.local.yaml \
    path/to/updated/datamart.ddl.sql > tables.merged.yaml
```

What happens in merge mode:

- **Tables that exist in both the existing YAML and the new DDL** are
  copied verbatim from the existing YAML. Your resolved fields
  (`domain`, `partition_spec`, `sort_order`, the watermark you
  picked over the heuristic) stay exactly as you set them.
- **New tables in the DDL but not in the existing YAML** get added at
  the bottom under a `NEW TABLES FROM LATEST DDL` header, with fresh
  TODO markers ready for you to resolve.
- **Tables in the existing YAML but not in the new DDL** are kept,
  with a `# WARNING: not found in latest DDL — drop from ingest
  schedule?` comment so you can decide whether to remove them or
  whether their absence is a temporary outage.
- **Summary on stderr**: `# Merge summary: N added, M removed, K
  unchanged`.

This means your operational rhythm becomes:

1. **Every time you receive a new DDL** (or your DBA pushes a schema
   change), re-run the script with `--merge` pointing at your current
   `tables.yaml`.
2. **Review the output** — focus on the `NEW TABLES` block at the
   bottom and any `WARNING` comments. Both are visually distinct in the
   merged YAML.
3. **Promote the merged file** by overwriting your existing
   `tables.yaml` once you're satisfied.

The merge preserves YAML structure but uses PyYAML's standard dumper,
which means key order within each table entry will be alphabetised. If
you have strong opinions about key order, format the file with a YAML
formatter or `yamllint` after merging.

## Bonus — seed data for the production-shape schema

If you also need synthetic data to populate the data mart for testing
strata against (without exposing real production data), use
[`examples/seed_full_datamart.py`](../examples/seed_full_datamart.py).

It seeds the six tables that the DDL doesn't already populate via
INSERT statements:

- `dim_account` — 100 synthetic accounts with bank + holder context
- `dim_currency` — 10 major currencies
- `fact_as_balance` — daily balances per account × currency
- `fact_as_transaction` — N transactions per day across accounts
- `fact_as_currency_exchange` — daily exchange rate per currency pair
- `fact_pay_payment` — daily payments with full counterparty context

It skips the tables that are populated by INSERTs in the DDL itself
(`dim_date`, `dim_as_characteristics`, `dim_data_owner`, `dim_user`,
`dim_classification`, `dim_routing`, `dim_as_transaction_type`,
`dim_pay_characteristics`, `dim_pay_bank_status`).

The seeder is idempotent on dimensions (uses ON CONFLICT on natural
keys) and append-only on facts. Run with `--reset` to truncate facts
before re-seeding.

```bash
# default volumes (30 days, 100 accounts, 200 txns/day, 100 payments/day)
python examples/seed_full_datamart.py

# scale down for quick smoke testing
python examples/seed_full_datamart.py --days 7 --accounts 20 \
                                     --txns-per-day 50 --payments-per-day 25

# clean re-seed
python examples/seed_full_datamart.py --reset
```

Requires `psycopg2-binary` — install with pip on the office laptop, or
run inside the spark container if you're testing locally.

## Step 5 — Wire it in

Replace whichever YAML strata is currently using:

- **Local stack** — copy/rename to `local/config/tables.local.yaml`
- **Production AWS deployment** — replace `examples/tables.yaml`, then
  redeploy with `./scripts/deploy.sh`
- **Per-customer overlays** — use whatever your team's per-customer
  config layout is

Then run the validation:

```bash
# Local stack
docker compose -f local/docker-compose.yml exec -T spark \
  python -c "from strata.config import load_config; \
  [load_config('/app/config/tables.local.yaml', t) for t in ['FACT_PAYMENT', 'DIM_CURRENCY', ...]]"
```

For AWS, just trigger one of the small dims as a first ingest. If it
completes with `rows_written > 0`, your config is shaped correctly.
Then iterate through the rest.

## Edge cases

**The DDL has CHECK constraints / generated columns / materialised
views.** The parser ignores them. CHECK constraints don't affect strata
behaviour; generated columns will be read like any other column;
materialised views can be ingested if they appear in `pg_tables` or
`USER_TABLES` — strata doesn't care if it's a view or a table on the
source side as long as JDBC can SELECT from it.

**The DDL has no PRIMARY KEY declarations.** The script emits
`primary_key: [TODO_pk]`. You must fill this in — strata uses the PK
for partition bucketing recommendations and downstream dedup queries
need it to identify the latest row per entity. If the source truly has
no PK, pick the natural key (e.g. `(account_id, posting_date)`).

**Two tables have the same name in different schemas.** The
script emits both under the same logical key (uppercased name), which
will be a YAML duplicate-key error. Rename one (e.g.
`PAYMENTS_TXN_LOG` vs `RECONCILE_TXN_LOG`) before re-running.

**The DDL is too dialect-specific for the regex.** Run the script
file-by-file rather than concatenating everything; whichever file
errors is the one to look at. The parser handles Oracle, PostgreSQL,
and MySQL `CREATE TABLE` forms cleanly. SQL Server, DB2, Snowflake,
and exotic Oracle constructs (XMLTYPE, NESTED TABLE, etc.) may need
hand-editing the input or the script.

## Why this is structured this way

Strata's design centre is the rule from `AGENTS.md`: **adding a new
table is a YAML edit only**. That works because the YAML carries
exactly the per-table information the pipeline needs and no more.
The DDL-to-YAML script enforces that boundary: only fields strata
actually uses (table identity, PK, watermark, partition spec)
appear in the output. The richer DDL semantics (column types,
constraints, indexes) are present in the source database where
they belong; strata reads them at runtime via JDBC schema
introspection rather than duplicating them in YAML.

The judgment fields (`domain`, `partition_spec`, `sort_order`)
require business and operational context that the DDL doesn't
contain. That's why this guide is workflow-oriented rather than
fully automated.
