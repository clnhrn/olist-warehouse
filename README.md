# Olist Warehouse

A production-style data warehouse on GCP, built on the [Olist Brazilian E-Commerce dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) (~100k orders, 9 source files). It shows ingestion with data contracts, a medallion architecture (bronze, silver, gold), Kimball modeling, idempotent reloads, schema evolution, SCD2 history, CI/CD with Terraform, and a text-to-SQL agent on top.

Status: see [docs/progress.md](docs/progress.md). Full plan: [docs/implementation-plan.md](docs/implementation-plan.md).

## Architecture

```
Olist CSVs (Kaggle)
   -> GCS landing      landing/load_date=YYYY-MM-DD/*.csv   (immutable)
   -> ingestion job    validate against contracts, add lineage columns, write Parquet
   -> BigQuery bronze  as-is, all STRING, one partition per load date
   -> dbt silver       typed, renamed, deduplicated                  (Phase 3)
   -> dbt gold         star schema and metrics                       (Phase 4)
   -> text-to-SQL agent, reads gold only                             (Phase 9)
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

Checked against `dev_bronze` with the full dataset: a forced rerun left every row count unchanged, and each partition held only the second run's `_loaded_at`. Phase 2 turns this into an automated test across all layers.

### Environments and access

| Identity | Can write | Used by |
|---|---|---|
| `sa-ingestion-prod` | prod lake bucket, `bronze` | the Cloud Run Job |
| `sa-dev` | dev lake bucket, `dev_bronze`, `dev_silver`, `dev_gold` | local work, by impersonation |

Local sessions impersonate `sa-dev`, so a local run cannot touch prod: reading the prod bucket or `bronze` as `sa-dev` returns 403. Writes to prod happen only inside Cloud Run.

Two caveats, stated plainly:

- The project owner's own credentials can still write to prod. Impersonation is a convention for local work; IAM is the real barrier for the job, CI, and the agent.
- Enabling Cloud Run created the default compute service account with `roles/editor` on the project. Nothing here uses it, so that grant was removed by hand (`gcloud projects remove-iam-policy-binding`). Terraform cannot remove grants it did not create, so this lives outside the IaC. Enabling another Google API later can add it back, so recheck after enabling one.

### Infrastructure

Terraform in [`terraform/`](terraform/) manages the APIs, both lake buckets, all six datasets, the service accounts and their IAM, the Artifact Registry repository, and the Cloud Run Job. Remote state lives in a versioned GCS bucket, created once by [`setup/bootstrap-gcp.sh`](setup/bootstrap-gcp.sh) because Terraform needs it before it can run. Bronze tables are created by the job from the contracts, so each schema has a single source.

The job image is `python:3.12-slim` plus a uv-installed virtualenv, running as a non-root user (~118 MB compressed, mostly the Polars engine). It is pushed as a single `linux/amd64` manifest without build attestations, so each push is one registry version and the keep-3 cleanup policy really keeps 3 images. The first image was pushed by hand as a one-time bootstrap; from Phase 8, CI builds and pushes images.

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
