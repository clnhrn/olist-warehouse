# Olist Warehouse

A production-style data warehouse on GCP, built on the [Olist Brazilian E-Commerce dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) (~100k orders, 9 source files). It shows ingestion with data contracts, a medallion architecture (bronze, silver, gold), Kimball modeling, idempotent reloads, schema evolution, SCD2 history, CI/CD with Terraform, and a text-to-SQL agent on top.

Status: see [docs/progress.md](docs/progress.md). Full plan: [docs/implementation-plan.md](docs/implementation-plan.md).

## Architecture

```
Olist CSVs (Kaggle)
   -> GCS landing      landing/load_date=YYYY-MM-DD/*.csv   (immutable)
   -> ingestion job    validate against contracts, add lineage columns, write Parquet
   -> BigQuery bronze  as-is, all STRING, one partition per load date
   -> dbt silver       typed, renamed, deduplicated, incremental merge
   -> dbt gold         star schema and metrics                       (Phase 4)
   -> text-to-SQL agent, reads gold only                             (Phase 9)

Daily at 06:00 (America/Sao_Paulo): Cloud Scheduler -> Workflow -> ingestion job -> dbt job
```

| Layer | Datasets (prod / dev) | Guarantees |
|---|---|---|
| Bronze | `bronze` / `dev_bronze` | Source data exactly as received, plus `_loaded_at`, `_load_date`, `_source_file`. Every batch passed its contract. |
| Silver | `silver` / `dev_silver` | Typed and deduplicated, latest row per key. |
| Gold | `gold` / `dev_gold` | Facts and dimensions with enforced dbt model contracts. |

Everything runs in `us-central1`: the GCS always-free tier only covers regional buckets in a few US regions.

## Phase 1: Ingestion

### Data contracts

Each source file has a versioned contract in [`contracts/v1/`](contracts/v1/): its columns, which may be null, the primary key, and a `parse` kind per column (`id32`, `zip`, `int`, `decimal`, `timestamp`, `state`, `text`). One YAML file produces both:

- a strict Pandera schema used at ingestion: exact column set, values must match their parse kind, timestamps must be real dates, required columns cannot be null;
- the BigQuery bronze schema: every source column as STRING, plus the three lineage columns.

Bronze stores everything as STRING on purpose. Bronze is a faithful copy of the source, so a value is never changed or lost on the way in: zip codes keep their leading zeros (`01037`), and a surprising value fails the batch instead of being silently cast. Silver does the casting.

Quirks the contracts were written against, found by profiling the full dataset:

- Zip code prefixes have leading zeros, which type inference would destroy.
- `product_category_name_translation.csv` starts with a UTF-8 BOM (Polars strips it).
- Review comments contain line breaks inside quoted fields.
- `review_id` alone is not unique; the key is `(review_id, order_id)`.
- Product columns keep the source typos (`product_name_lenght`); silver renames them.

### The ingestion job

```
python -m ingestion land   --load-date D --env dev|prod --sink gcs|local
python -m ingestion ingest --load-date D --env dev|prod --sink gcs|local [--force] [--tables ...]
```

- `land` copies the raw CSVs into `landing/load_date=D/`. It refuses a date that already has files, so landing is never modified.
- `ingest` reads each landing CSV with every column as a string, validates it against its contract, adds the lineage columns, writes `bronze/<table>/load_date=D/data.parquet`, and loads that file into the table's `D` partition in BigQuery.
- `--sink local` writes the same layout under `data/` with no GCP access, for development and DuckDB.

Bucket layout (the same keys in GCS and in local `data/`):

```
landing/load_date=2016-01-01/olist_orders_dataset.csv
bronze/orders/load_date=2016-01-01/data.parquet
_manifests/load_date=2016-01-01.json
```

### Idempotency

Rerunning a load date can never duplicate rows:

- Each table load uses `WRITE_TRUNCATE` on its own partition (`orders$20160101`), so it replaces exactly that date and nothing else.
- A manifest records the MD5 of every landing file it loaded. If the files have not changed, a rerun skips the date. `--force` reloads anyway.

Checked against `dev_bronze` with the full dataset: a forced rerun left every row count unchanged, and each partition held only the second run's `_loaded_at`. Phase 2 turns this into a repeatable check, `check-rerun` (below).

### Environments and access

| Identity | Can write | Used by |
|---|---|---|
| `sa-ingestion-prod` | prod lake bucket, `bronze` | the ingestion job |
| `sa-dbt-prod` | `silver`, `gold` (reads `bronze`) | the dbt job (Phase 3) |
| `sa-orchestrator-prod` | nothing; can only start the two jobs | the workflow and its schedule (Phase 3) |
| `sa-dev` | dev lake bucket, `dev_bronze`, `dev_silver`, `dev_gold` | local work, by impersonation |

Local sessions impersonate `sa-dev`, so a local run cannot touch prod: reading the prod bucket or `bronze` as `sa-dev` returns 403. Writes to prod happen only inside Cloud Run.

Two caveats, stated plainly:

- The project owner's own credentials can still write to prod. Impersonation is a convention for local work; IAM is the real barrier for the job, CI, and the agent.
- Enabling Cloud Run created the default compute service account with `roles/editor` on the project. Nothing here uses it, so that grant was removed by hand (`gcloud projects remove-iam-policy-binding`). Terraform cannot remove grants it did not create, so this lives outside the IaC. Enabling another Google API later can add it back, so recheck after enabling one.

### Infrastructure

Terraform in [`terraform/`](terraform/) manages the APIs, both lake buckets, all six datasets, the service accounts and their IAM, the Artifact Registry repository, and the Cloud Run Job. Remote state lives in a versioned GCS bucket, created once by [`setup/bootstrap-gcp.sh`](setup/bootstrap-gcp.sh) because Terraform needs it before it can run. Bronze tables are created by the job from the contracts, so each schema has a single source.

The job image is `python:3.12-slim` plus a uv-installed virtualenv, running as a non-root user (~118 MB compressed, mostly the Polars engine). It is pushed as a single `linux/amd64` manifest without build attestations, so each push is one registry version and the cleanup policy (keep 2 per image since Phase 3) counts whole images. The first image was pushed by hand as a one-time bootstrap; from Phase 8, CI builds and pushes images.

### How to run

One-time setup (tools, GCP bootstrap, dataset): see [setup/README.md](setup/README.md).

```bash
uv sync

# Local, no GCP
uv run python -m ingestion land   --load-date 2016-01-01 --sink local
uv run python -m ingestion ingest --load-date 2016-01-01 --sink local

# Dev BigQuery, as sa-dev
gcloud auth application-default login --impersonate-service-account=sa-dev@<project-id>.iam.gserviceaccount.com
export OLIST_PROJECT_ID=<project-id>
uv run python -m ingestion land   --load-date 2016-01-01 --env dev --sink gcs
uv run python -m ingestion ingest --load-date 2016-01-01 --env dev --sink gcs

# Prod, inside Cloud Run (after landing the prod files)
gcloud run jobs execute ingestion --region=us-central1 --wait \
  --args=ingest,--env,prod,--sink,gcs,--load-date,2016-01-01

# Tests and lint
uv run pytest
uv run ruff check .
```

The tests use small synthetic CSVs, so they run without the dataset. One extra test checks a sample of every real file when `data/raw/` exists.

### Cost

Phase 1 runs at about zero: about 160 MB in each lake bucket and about 125 MB in Artifact Registry (free tiers: 5 GB and 0.5 GB). A full load is 9 BigQuery load jobs, which are free; the job runs about 4 minutes on 1 CPU, inside the Cloud Run free tier.

## Phase 2: Idempotency

Idempotent means running the same load date twice leaves the warehouse exactly as one run would. It matters because a scheduler retries failed runs, and a retry must never double the data.

### The rerun check

```
python -m ingestion check-rerun --load-date D --sink local          # local lake + DuckDB
python -m ingestion check-rerun --load-date D --sink gcs --env dev  # dev_* datasets
```

One run step is a forced ingest of the date followed by `dbt build`. The check runs that step, records the row count of every table in every layer, runs it again, and exits non-zero if any count changed. Counts are keyed by layer (`bronze.orders`, `silver.silver_orders`, `gold.fact_orders`). `--no-dbt` checks bronze only. It refuses `--env prod`, because it writes data. In BigQuery the counts come from table metadata, so counting runs no queries and costs nothing.

On the full dataset, all 9 bronze, 10 silver (9 models and 1 seed), and 9 gold tables kept their counts, both locally and in dev BigQuery (for example, 99,441 orders in every layer, 1,000,163 bronze and 738,332 silver geolocation rows, 112,650 rows in `fact_order_items`).

### Rerunning an old batch

Bronze keeps every batch, so silver has to pick the latest row per business key. The order it uses matters:

| Batch | `_load_date` | `_loaded_at` |
|---|---|---|
| Batch 1, rerun today | 2016-01-01 | today |
| Batch 2, loaded last week | 2016-02-01 | last week |

Ordering by `_loaded_at` alone would let the rerun of batch 1 win, and old data would overwrite new data. Silver orders by `_load_date` first, then `_loaded_at`, so batch 2 still wins. dbt unit tests cover exactly this case, for a full build and for an incremental run (see Phase 3).

## Phase 3: Silver models in dbt

### The dbt project

[`dbt/`](dbt/) holds one silver model per bronze table (`silver_<table>`), with a description and tests for every column. The same project runs on two engines:

| Target | Engine | Reads | Writes | Credentials |
|---|---|---|---|---|
| `local` | DuckDB (`data/warehouse.duckdb`) | `data/bronze/` Parquet | `dev_silver` | none |
| `dev` | BigQuery | `dev_bronze` | `dev_silver` | ADC as `sa-dev` |
| `prod` | BigQuery (Cloud Run only) | `bronze` | `silver` | the job's service account |

A schema macro maps every target to the layer datasets (`prod` writes `silver`, everything else `dev_silver`). Locally, an `on-run-start` hook exposes the Parquet as DuckDB views named like the BigQuery tables, so sources resolve the same way on both engines. The profile holds no secrets: BigQuery targets use Application Default Credentials.

### What silver does

- Casts every column from bronze's STRING to its real type, renames columns (`order_delivered_carrier_date` to `shipped_at`, and the `lenght` typos to `length`), trims strings, and turns blank review text into null.
- City names are lowercased, trimmed, and stripped of accents (`São Paulo` to `sao paulo`), with one macro per engine.
- Timestamps stay `TIMESTAMP` on both engines. They are Brazil local time as the source gives them.
- Category names stay in Portuguese; gold joins the English names (plus a seed for the 2 missing ones) in `dim_product`.
- Geolocation has no business key, so it is rebuilt from the latest batch with exact duplicates removed (1,000,163 to 738,332 rows). Gold picks one point and spelling per zip.

### Incremental deduplication

Every other model is incremental with `merge` on its key: each run reads only bronze rows loaded since the last run and merges the latest row per key into silver. The incoming rows also compete with the current silver row for the same key. Without that, a rerun of an old batch would pass the `_loaded_at` filter and overwrite newer data. Two macros, [`bronze_since_last_run` and `latest_row_per_key`](dbt/macros/silver.sql), carry the rule for every model. Three dbt unit tests pin it down: batch order beats load time on a full build, an old batch rerun keeps the newer row on an incremental run, and a genuinely newer batch replaces the row.

### Tests

102 tests per build: `unique` and `not_null` on every key (`unique_combination_of_columns` for composite keys), `relationships` between orders, items, payments, reviews, customers, products, and sellers, `accepted_values` for statuses, payment types, scores, and the 27 state codes, plus value checks (`price > 0`). Known source issues are tests at warn severity, so they stay visible without failing the build:

| Warning | Rows |
|---|---|
| Payments with 0 installments | 2 |
| Delivered orders without a delivery date | 8 |
| Products whose category is missing from the translation table | 13 |

Bronze sources have freshness checks on `_loaded_at`. The dataset is a one-time snapshot, so they only warn.

### Linting

SQLFluff lints the models through its dbt templater against the local target, so it needs no credentials. Pre-commit runs ruff, SQLFluff, `terraform fmt`, and basic file checks on every commit, all through uv, so versions come from `uv.lock`.

### Orchestration

```
Cloud Scheduler (06:00 America/Sao_Paulo)
  -> Workflow "pipeline"  (runs as sa-orchestrator-prod)
       -> Cloud Run Job "ingestion"   ingest --all-dates                          (sa-ingestion-prod)
       -> Cloud Run Job "dbt"         dbt build --target prod, no unit tests      (sa-dbt-prod)
```

- `ingest --all-dates` walks every landed date, oldest first. The manifest skips dates whose files have not changed, so a daily run on this static dataset loads nothing.
- Each workflow step waits for its job to finish, and a failed job fails the workflow, so dbt never runs after a failed load.
- The dbt image installs only `dbt-core` and `dbt-bigquery` and bakes in the dbt packages. A uv override drops `google-cloud-aiplatform`, which dbt-bigquery needs only for Python models: ~250 MB compressed.
- `sa-orchestrator-prod` can start the two jobs and nothing else. Waiting for a job means polling its long-running operation (`run.operations.get`), which `run.invoker` does not include, so it also has a custom role with that single permission.

First prod run: the workflow succeeded, ingestion skipped all 9 tables, and dbt built every silver table with the same row counts as dev (99 pass, the same 3 warnings).

Found on rollout:

- Enabling the Workflows API creates its service agent shortly afterward, so the first `apply` failed creating the workflow. A second apply a minute later succeeded.
- The first workflow run failed with 403 on `run.operations.get`, which led to the custom role above.

### How to run

```bash
uv sync                      # includes dbt, dbt-duckdb, sqlfluff, pre-commit
uv run pre-commit install    # once

cd dbt
uv run dbt build --target local    # DuckDB, needs data/bronze from a local ingest
uv run dbt build --target dev      # dev BigQuery, ADC as sa-dev, OLIST_PROJECT_ID set
uv run sqlfluff lint models

# Prod, by hand (the schedule does this daily)
gcloud workflows run pipeline --location=us-central1
```

### Cost

About zero. The schedule uses 1 of the 3 free Cloud Scheduler jobs and about 4 Workflows steps a day (5,000 free a month). Both jobs run about 4 minutes a day in total, inside the Cloud Run free tier. A daily dbt build scans a few hundred MB, inside the free 1 TB of queries. Artifact Registry holds about 0.4 GB because versions share layers; keeping 2 versions per image keeps it near the 0.5 GB free tier.

## Phase 4: Gold layer, star schema

Gold is a Kimball star schema: facts hold events and measures, dimensions describe them. The full design, with every column and the data findings behind each choice, is in [docs/star-schema-design.md](docs/star-schema-design.md).

### Tables

| Table | Grain | Rows |
|---|---|---|
| `fact_order_items` | one row per order item | 112,650 |
| `fact_orders` | one row per order (accumulating snapshot), including 775 orders with no items | 99,441 |
| `fact_payments` | one row per payment record | 103,886 |
| `fact_reviews` | one row per review per order | 99,224 |
| `dim_customer` | one row per person (`customer_unique_id`) | 96,096 + unknown |
| `dim_location` | one row per zip code prefix (customer and seller roles) | 19,177 + unknown |
| `dim_product` | one row per product, with English category names | 32,951 + unknown |
| `dim_seller` | one row per seller per version (SCD2 from Phase 6) | 3,095 + unknown |
| `dim_date` | one row per day, 2016 to 2020 | 1,827 + unknown |

An accumulating snapshot has one row per process (an order) that fills in as the process moves forward: approved, shipped, delivered.

### Design choices

- **Why these grains.** Item revenue lives at item level, because 1,278 orders have items from several sellers. Delivery measures (days to deliver, lateness) live on `fact_orders`, so they are never repeated per item. Payments get their own fact and are never split across items: they differ from the item total for 303 orders (interest, vouchers).
- **Surrogate keys.** Dimension keys are hashes of the natural key (`dbt_utils.generate_surrogate_key`). They are stable across rebuilds, so a rerun never reassigns keys. `dim_date` uses a readable `YYYYMMDD` integer.
- **Unknown members.** Every dimension has a `'-1'` row (`-1` for dates), and every fact foreign key is non-null. A milestone that has not happened (an undelivered order's delivery date) points to `-1` instead of null, so inner joins never drop facts.
- **Seller versions already.** `dim_seller` has the SCD2 columns (`valid_from`, `valid_to`, `is_current`) with one version per seller from a 2016-01-01 baseline. `fact_order_items` already joins the version valid at purchase time, so Phase 6 only swaps the source for a snapshot.
- **Rebuilt, not merged.** Gold tables are rebuilt from silver on every run. At this size that costs seconds, and a full rebuild is idempotent by construction.

### Contracts

Every gold model has an enforced dbt model contract: the build fails if a column's name or type differs from the YAML. The contracts use types both engines accept (`string`, `int64`, `bool`, `date`, `timestamp`, `numeric(p, s)`), so the same YAML enforces on DuckDB and BigQuery. BigQuery does not allow `numeric(18, 2)` in a `CAST`, only in a column definition, so a `to_numeric` macro rounds in SQL and the contract's DDL (the `CREATE TABLE` statement) sets the precision. Key columns also carry `not_null` constraints, which both engines enforce.

### Partitioning and clustering

`fact_order_items` and `fact_orders` are partitioned by purchase month and clustered by their common filters (`seller_key`, `product_key`; `customer_key`, `order_status`). Partitioning splits a table into chunks by a column, so a query filtered on that column scans only the matching chunks. Clustering sorts data within each chunk, so filters on those columns skip blocks. Each fact table is tens of MB, far below where this changes cost, but the agent in Phase 9 queries these tables, and on real volumes it is what keeps a date-filtered query cheap. Monthly partitions, not daily, because BigQuery caps a table at 10,000 partitions and daily partitions this small add overhead. DuckDB has no equivalent, so the config applies on BigQuery only.

### Tests

63 tests on gold, on top of the contracts:

- Primary keys: `unique` (`unique_combination_of_columns` for the composite fact keys).
- Every fact foreign key exists in its dimension (`relationships`).
- Fact row counts equal silver (`equal_rowcount`), so no join fans out or drops rows.
- `fact_orders` totals add up to `fact_order_items` (a singular test).
- Exactly one current version per seller.

The same numbers come out of both engines: 775 orders with no items, 14 status and date mismatches, 6,535 late deliveries, and revenue (excluding canceled and unavailable orders) of BRL 13,494,400.74.

### How to run

```bash
cd dbt
uv run dbt build --target local --select models/gold+   # DuckDB
uv run dbt build --target dev --select models/gold+     # dev BigQuery, ADC as sa-dev
```

### Cost

About zero. Building the 9 gold models in dev BigQuery scans about 185 MB (plus the tests), inside the free 1 TB of queries a month. The tables store about 115 MB, inside the free 10 GB.
