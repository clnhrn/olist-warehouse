# Olist Warehouse: Implementation Plan

A portfolio project that shows GCP pipelines, Kimball modeling, idempotency, schema evolution, SCD2, and an AI query layer on top.

## 1. Goals

- Build a small but production-style warehouse on the Olist Brazilian E-Commerce dataset (~100k orders, 9 CSV files, from Kaggle).
- Show these skills clearly in the repo and README:
  - GCP ingestion and loading
  - Kimball dimensional modeling (star schema)
  - Idempotency
  - Schema evolution handling
  - SCD2 history tracking
  - Data quality checks and data contracts
  - CI/CD and infrastructure as code
  - A text-to-SQL agent (the Gen AI / agentic AI layer)

## 2. Terms used in this plan

- **Medallion architecture:** a layered way to organize a warehouse. Bronze is raw data as it arrived, silver is cleaned and standardized, gold is business-ready (star schema and metrics). Each layer improves the quality of the one before it.
- **Kimball modeling:** organizing data into fact tables (events, like order items) and dimension tables (descriptions, like customers and products).
- **Grain:** what one row in a fact table represents. Decide this first.
- **Accumulating snapshot:** a fact table with one row per process instance (like one order) that gets updated as the process moves forward (purchased, shipped, delivered).
- **Idempotency:** running a pipeline twice gives the same result, with no duplicates.
- **Schema evolution:** the source adds, removes, or renames columns and the pipeline does not break.
- **SCD2 (slowly changing dimension, type 2):** when an attribute changes, keep the old row and add a new one with start and end dates.
- **Data contract:** a written agreement on column names, types, and rules that the data must meet.
- **dbt model contract:** a dbt setting (`contract: enforced`) that makes a model fail to build if its columns or types differ from what you declared.
- **dbt:** a tool that builds and tests SQL models in a warehouse.
- **Workload Identity Federation (WIF):** lets GitHub Actions prove its identity to GCP directly, so you never store a long-lived service account key.
- **Semantic layer:** one central definition of business metrics (like "revenue") so every query uses the same logic.
- **Eval set:** a fixed list of test questions with known correct answers, used to score the AI agent.

## 3. Architecture

```
Olist CSVs (Kaggle)
   -> Cloud Storage landing (raw CSVs uploaded to landing/load_date=YYYY-MM-DD/, never modified)
   -> Ingestion job (validate, add load metadata, write Parquet)
   -> BigQuery bronze (as-is, append-only by batch)
   -> dbt silver (clean types, rename, dedupe)
   -> dbt gold (star schema and metrics)
   -> Text-to-SQL agent (reads gold only)
```

Layer mapping, with the dbt naming equivalents you may see elsewhere:

| Medallion layer | BigQuery dataset | dbt convention | Contents |
|---|---|---|---|
| Bronze | `bronze` | raw / sources | Source data as-is, append-only by batch, with load metadata |
| Silver | `silver` | staging | Typed, renamed, deduplicated tables (latest row per key) |
| Gold | `gold` | marts | Facts, dimensions, and metrics |

Environments: each layer exists twice, as prod datasets (`bronze`, `silver`, `gold`) and dev datasets (`dev_bronze`, `dev_silver`, `dev_gold`). CI builds into dev, never into prod. The ingestion job takes an `--env dev|prod` flag, so the same code fills `dev_bronze` for CI and local testing.

Location: everything (bucket, datasets, Cloud Run, Workflows, Artifact Registry) lives in `us-central1`. The GCS always-free tier only covers regional buckets in `us-central1`, `us-east1`, or `us-west1`, not the `US` multi-region. Keeping BigQuery in the same region keeps GCS loads simple.

Supporting pieces:
- **Orchestration:** Cloud Scheduler triggers a Cloud Workflow (a free-tier-friendly runner that chains steps). The workflow runs the ingestion Cloud Run Job first, then a second Cloud Run Job that runs `dbt build`. The ingestion job exits early when no new `load_date=` folder is in landing, so a daily schedule on this static dataset costs nothing and adds no rows. Avoid Cloud Composer here because it costs money even when idle.
- **IaC:** Terraform for buckets, datasets (prod and dev), service accounts, Workload Identity Federation, Cloud Run Jobs, and the workflow. Terraform owns all of these. The only exception is the remote state bucket and three bootstrap APIs, created once by `setup/bootstrap-gcp.sh` because Terraform needs them before it can run.
- **CI/CD:** GitHub Actions runs lint, `terraform plan`, `dbt build` on the dev datasets, and tests on every pull request. It authenticates to GCP with Workload Identity Federation.
- **Secrets:** the LLM API key lives in Secret Manager, never in code or CI variables.
- **Local dev:** the ingestion job has a local sink that writes the same bronze Parquet (with the same `_` metadata columns) to `data/bronze/`. DuckDB runs the same dbt project on those files, so sources match between DuckDB and BigQuery and you can iterate for free.
- **Access separation:** local sessions impersonate a dev service account that can write only to `dev_*` datasets. The real protection against touching prod is IAM, not tool settings.
- **Processing:** Polars for fast ingestion and light cleaning in the load job. Pandas only where a library requires it.
- **Monitoring:** Cloud Monitoring alert on failed Cloud Run Job executions, plus a `pipeline_runs` log table (see phase 7).

## 4. Repo structure

```
olist-warehouse/
  README.md
  terraform/
  ingestion/          # Python load job (Polars)
  dbt/
    models/silver/
    models/gold/
    seeds/
    tests/
  contracts/          # Pandera / schema definitions
  agent/              # text-to-SQL agent + evals
  .github/workflows/
  docs/               # diagrams, decisions, cost notes
```

## 5. Phases

### Phase 0: Setup (1 to 2 days)
- Create a GCP project with a budget alert (set it low, for example $5).
- Create the GitHub repo and the Terraform remote state bucket (bootstrap step, `us-central1`, versioning on).
- Install tools inside WSL: gcloud SDK (not the Windows install, whose credentials WSL Python cannot see), Terraform, uv, and the Kaggle CLI. dbt and DuckDB go in the project's uv environment.
- Service accounts are created by Terraform in Phase 1, and Workload Identity Federation in Phase 8, never by hand.
- The Secret Manager secret for the LLM API key waits until Phase 9.
- Download the dataset and explore it in DuckDB.
- Write down the fact table grain and star schema design on paper first.

### Phase 1: Ingestion (2 to 3 days)
- Terraform: two lake buckets (`<project>-lake-dev`, `<project>-lake-prod`, so IAM stays per bucket), BigQuery datasets (`bronze`, `silver`, `gold` and the `dev_` versions), Artifact Registry, and service accounts (`sa-ingestion-prod`, and `sa-dev` with write access to `dev_*` only). Everything in `us-central1`. The CI service account and Workload Identity Federation wait until Phase 8, when CI exists.
- Upload the raw CSVs with `python -m ingestion land` to `gs://<bucket>/landing/load_date=YYYY-MM-DD/`. Landing is never modified: `land` refuses a date that already has files.
- Python job, `python -m ingestion ingest` (`--env dev|prod`, `--load-date`, `--sink gcs|local`):
  - Read the CSVs from landing, or from `data/raw/` for the local sink.
  - Validate with Pandera. Checks are on columns and parsing only, so bronze stays as-is. In Phase 1, a failed batch stops the job loudly. Row-level quarantine comes in Phase 7.
  - Add `_loaded_at`, `_load_date`, and `_source_file` for lineage (tracing where data came from).
  - Write Parquet to `bronze/<table>/load_date=YYYY-MM-DD/`, then load it into BigQuery with `WRITE_TRUNCATE` on the `_load_date` partition. The local sink writes the same Parquet to `data/bronze/`.
  - Exit early if that `load_date` is already loaded and has not changed.
- Bronze tables use explicit schemas defined in `contracts/`, never autodetect, so types cannot drift between batches. Partition them by `_load_date`. Allow `ALLOW_FIELD_ADDITION` on loads now, so Phase 5 does not need a rewrite.
- Package as a Cloud Run Job. The Workflow and Scheduler wait until Phase 3, when there is a dbt job to chain.
- The first image is pushed by hand (a one-time bootstrap, tagged with the git SHA). From Phase 8, CI builds and pushes images.

### Phase 2: Idempotency (1 day)
- Bronze is append-only across batches, but rerunning the same `load_date` replaces only that date's partition, so a retry never creates duplicates.
- Silver keeps the latest row per business key, ordered by `_load_date` and then `_loaded_at`. Never order by `_loaded_at` alone: rerunning an old batch would give it the newest `_loaded_at`, and old data would win. Silver is built as dbt incremental models using `merge` on a unique key.
- Gold is rebuilt or merged from silver the same way.
- Test it: run the job twice for the same date, then assert row counts are unchanged in every layer.
- Built in steps, because silver and gold do not exist yet: `check-rerun` covers bronze now, silver joins it in Phase 3 (with a test that rerunning an old batch does not beat newer data), and gold in Phase 4. Phase 2 is ticked off once gold is in the check.
- Document this test in the README.

### Phase 3: Silver models in dbt (2 days)
- One silver model per source table: cast types, rename columns, trim strings, deduplicate.
- Notable Olist issues to handle:
  - Duplicate rows in the geolocation table
  - Product category names in Portuguese (join the translation table)
  - Typos in the product table's column names (`product_name_lenght`)
  - Orders with missing delivery timestamps
- Declare bronze tables as dbt sources and add source freshness checks.
- Add dbt tests: `unique`, `not_null`, `relationships`, `accepted_values`. Use `dbt_utils` for extra tests.
- Add SQLFluff (a SQL linter) and pre-commit hooks.
- Package `dbt build` as a second Cloud Run Job, then add the Cloud Workflow (ingestion job, then dbt job) and the Cloud Scheduler trigger. The trigger is safe to run daily because ingestion does nothing when there is no new batch.

### Phase 4: Gold layer, star schema (3 to 4 days)
Grains confirmed on 2026-10-08. Full design, with columns, keys, and the data findings behind each choice: [star-schema-design.md](star-schema-design.md). All of these live in the `gold` dataset:

| Table | Type | Grain |
|---|---|---|
| `fact_order_items` | Fact | One row per order item, `(order_id, order_item_id)` |
| `fact_orders` | Fact (accumulating snapshot) | One row per order, including orders with no items, with purchase, approval, shipping, and delivery dates |
| `fact_payments` | Fact | One row per payment record, `(order_id, payment_sequential)` |
| `fact_reviews` | Fact | One row per review per order, `(review_id, order_id)` |
| `dim_customer` | Dimension | One row per person (`customer_unique_id`) |
| `dim_location` | Dimension | One row per zip code prefix, used for customer and seller location |
| `dim_seller` | Dimension (SCD2) | One row per seller per version |
| `dim_product` | Dimension | One row per product |
| `dim_date` | Dimension | One row per day, 2016-01-01 to 2020-12-31 |

- Use surrogate keys (generated IDs, not the source IDs) in dimensions. Keep `order_id` on the facts as a degenerate dimension (an identifier with no dimension table of its own).
- Put delivery measures (delivery days, delay vs estimate) on `fact_orders`, so they are not repeated for every item in an order. Item-level measures (item revenue, freight share) stay on `fact_order_items`.
- Enforce dbt model contracts on every gold model.
- Partition fact tables by date and cluster by common filter keys. Note in the README why, even though the data is small.
- Add dbt docs and column descriptions as you go.

### Phase 5: Schema evolution (1 to 2 days)
- Simulate a source change: a second batch with a new column and a renamed column.
- Handle it at each layer:
  - Bronze load: allow new columns (BigQuery schema update options).
  - dbt: set `on_schema_change` on incremental models (`append_new_columns` or `fail`).
  - Contracts: version them (v1, v2) and fail loudly on breaking changes.
- Write a short decision doc: which changes are auto-accepted and which block the pipeline.
- Decided while building (see [schema-evolution.md](schema-evolution.md)):
  - Batch 2 (`load_date=2016-02-01`) is a full snapshot: products renames the two "lenght" columns, sellers adds `seller_updated_at` (all at the 2016-01-01 baseline). Built by `scripts/make_batch2.py`.
  - Each contract has an `effective_from` load date, and every date is validated against the version in force then, so reruns of old batches keep working. A rename is declared with `renamed_from`; loading the contracts refuses breaking changes.
  - Bronze keeps every column any version had (both spellings after a rename). Ingestion adds new columns to the bronze tables on every run, even when it skips, so silver never selects a missing column.
  - The silver incremental macro reads only key and lineage columns from the existing table, so `append_new_columns` works without a full refresh.

### Phase 6: SCD2 (1 to 2 days)
- Olist is a one-time snapshot, so simulate change. Since Phase 5, the source sends `seller_updated_at` (contract v2, batch 2), and silver sellers exposes it as `updated_at`:
  - Batch 1 (the original data, contract v1) has no value: use the baseline 2016-01-01 for it. Batch 2 already sends that baseline.
  - Batch 3: pick some sellers, change their city or state, and set `seller_updated_at` to a date inside the order history, for example 2018-01-01. That way some orders fall before the change and some after.
- Version sellers by the business `updated_at`, never by run time, so `valid_from` is the day the seller changed, not the day dbt ran.
- Decided 2026-10-08: build the versions with a model over all bronze batches (`silver_seller_versions`), not a dbt snapshot on silver. Silver keeps only the latest row per seller, so a snapshot records only the states it happens to see. On a fresh rebuild, or when several batches arrive between dbt runs, it loses versions that bronze still holds. The model rebuilds the same history every time.
- Build `dim_seller` in gold from `silver_seller_versions`.
- Batch 3 (`load_date=2016-03-01`, `scripts/make_batch3.py`): 20 sellers with orders on both sides of 2018-01-01 move to another state on that date.
- Join facts to the correct seller version where the order purchase date falls between `dbt_valid_from` and `dbt_valid_to`.
- Add tests: every seller has exactly one current row, and no seller's version ranges overlap.

### Phase 7: Data quality, contracts, and monitoring (1 to 2 days)
- Pandera schemas at ingestion (shape and types).
- dbt model contracts and dbt tests in the warehouse (business rules).
- Optional: Soda Core checks for freshness and row count anomalies.
- Write failed rows to a quarantine table instead of dropping them silently.
- Write a row per run to a `pipeline_runs` table: run id, layer, start and end time, rows in and out, status.
- Add a Cloud Monitoring alert that emails you when a Cloud Run Job execution fails.
- Add a short note on how this supports audit trails.
- Decided while building:
  - Quarantine: rows that fail a row-level check go to `quarantine/table_name=<table>/load_date=<date>/` Parquet, read by a `bronze.quarantine` external table, so a rerun overwrites them and never duplicates. More than 1% failing rows, or a structural change, still stops the batch. Dates that skip get an empty quarantine file, so the external table always has files to read.
  - `pipeline_runs` lives in a new `ops` dataset (`dev_ops` in dev), outside the medallion layers, with one row per table (ingestion) or model (dbt, from an `on-run-end` hook), plus a tests summary row. The workflow passes its execution id as `OLIST_RUN_ID`, so both jobs share a run id. That needs `roles/run.jobsExecutorWithOverrides` on the orchestrator, because `roles/run.invoker` lacks `run.jobs.runWithOverrides`.
  - The three warning-level tests store their failing rows in `ops.failures_*`.
  - No Soda Core: freshness is already a dbt source check, and row count anomalies are a dbt generic test (`batch_row_count_change`, warns above a 50% change between batches).
  - Two alert policies (failed job execution, failed workflow) on one email channel.

### Phase 8: CI/CD (1 to 2 days)
- Terraform: Workload Identity Federation pool and provider (restricted to this GitHub repo), the CI service account, and the STS API.
- GitHub Actions workflow on pull requests: lint (SQLFluff and Python), `terraform plan`, `dbt build` against the dev datasets, Pandera tests. Authenticate with Workload Identity Federation.
- On merge to main: `terraform apply`, build and push the job images, run `dbt build` on prod. CI becomes the only thing that pushes images.
- Image versions: Terraform owns each job's settings, CI updates the image with `gcloud run jobs deploy`, and the job's image is in `lifecycle { ignore_changes }`, so deploys do not show up as drift in `terraform plan`.

### Phase 9: AI layer, text-to-SQL agent (4 to 5 days)
- Agent flow: user question -> LLM reads the table and column descriptions from dbt docs -> writes SQL -> runs it -> explains the result.
- Guardrails (limits on what the agent can do):
  - Read-only service account (this is the real protection, so say so in the README)
  - Access to the `gold` dataset only
  - Query cost cap (BigQuery maximum bytes billed)
  - SQL validation with a SQL parser such as `sqlglot` (parsing the query properly, not just checking that it starts with SELECT), plus a row limit
- Log every question, generated SQL, bytes scanned, and result to a table for review.
- Add a simple semantic layer: a YAML file defining metrics such as revenue, average delivery time, and late delivery rate. Mention in the README that dbt's Semantic Layer (MetricFlow) is the production version of this idea.
- Build an eval set of 40 to 50 questions with known answers. Score:
  - Whether the SQL runs
  - Whether the result matches the expected answer
  - Cost per question
- Run the eval set in CI (or on demand) so accuracy drops are caught.
- Publish the accuracy number in the README, plus a short list of failure cases and what you learned.
- Optional stretch: expose the warehouse to the agent through an MCP server (a standard way for AI tools to call external tools).

### Phase 10: Polish (2 days)
- Architecture diagram and a data lineage screenshot from dbt docs.
- Short demo (GIF or 2-minute video) of the agent answering questions.
- Tear down or pause costly resources and note the monthly cost.

## 6. Cost control

- Set a GCP budget alert before anything else.
- Use DuckDB locally for development.
- Keep the dataset in BigQuery's free tier (10 GB storage and 1 TB queries per month are free).
- Use a regional `us-central1` bucket. The GCS 5 GB always-free tier does not cover multi-region buckets.
- A budget alert only sends alerts. It does not cap spending.
- Use Cloud Run Jobs, Cloud Workflows, and Cloud Scheduler instead of Composer.
- Set `maximum_bytes_billed` on every BigQuery query the agent runs.
- Keep the container images small (Artifact Registry free storage is 0.5 GB).

## 7. README checklist

- One-paragraph summary and the architecture diagram
- The medallion layers (bronze, silver, gold) and what each one guarantees
- Why these design choices (grain, SCD2 on sellers, Cloud Run instead of Composer)
- How idempotency is tested
- How schema changes are handled
- Data quality approach, contract examples, and monitoring
- Dev and prod environments, and how CI authenticates (Workload Identity Federation)
- Agent design, guardrails, and eval results
- Cost notes and limitations
- How to run it locally with DuckDB

## 8. Suggested timeline

About 3 to 4 weeks part-time. If time is short, do phases 0 to 4 and 9 first, then add 5 to 8 as extras.
