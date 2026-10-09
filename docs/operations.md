# Operations

Infrastructure, the daily pipeline, how to run everything, what went wrong on rollout, and what it costs. One-time setup (tools, GCP bootstrap, dataset): [setup/README.md](../setup/README.md).

## Infrastructure

Terraform in [`terraform/`](../terraform/) manages the APIs, both lake buckets (one per environment, so IAM stays per bucket), all eight datasets (`bronze`, `silver`, `gold`, `ops` and their `dev_` copies), the service accounts and their IAM, Workload Identity Federation, the Artifact Registry repository, both Cloud Run jobs, the workflow, the schedule, and the alert policies. Remote state lives in a versioned GCS bucket, created once by [`setup/bootstrap-gcp.sh`](../setup/bootstrap-gcp.sh) because Terraform needs it before it can run. Bronze tables are created by the ingestion job from the contracts, so each schema has a single source.

Everything runs in `us-central1`: the GCS always-free tier only covers regional buckets in a few US regions.

### Images

Both images are `python:3.12-slim` plus a uv-installed virtualenv, running as a non-root user.

- `ingestion`: ~118 MB compressed, mostly the Polars engine.
- `dbt`: only `dbt-core` and `dbt-bigquery`, with the dbt packages baked in. A uv override drops `google-cloud-aiplatform`, which dbt-bigquery needs only for Python models: ~250 MB compressed.

Each is pushed as a single `linux/amd64` manifest without build attestations, so each push is one registry version and the cleanup policy (keep 2 per image) counts whole images. CI builds and pushes them, tagged with the commit SHA ([security-and-ci.md](security-and-ci.md)).

## The daily pipeline

```
Cloud Scheduler (06:00 America/Sao_Paulo)
  -> Workflow "pipeline"  (runs as sa-orchestrator-prod)
       -> Cloud Run Job "ingestion"   ingest --all-dates                          (sa-ingestion-prod)
       -> Cloud Run Job "dbt"         dbt build --target prod, no unit tests      (sa-dbt-prod)
```

- `ingest --all-dates` walks every landed date, oldest first. The manifest skips dates whose files have not changed, so a daily run on this static dataset loads nothing.
- Each workflow step waits for its job to finish, and a failed job fails the workflow, so dbt never runs after a failed load.
- The workflow passes its execution id to both jobs as `OLIST_RUN_ID`, so their `ops.pipeline_runs` rows share one run id ([data-quality.md](data-quality.md#pipeline_runs)).
- `sa-orchestrator-prod` can start the two jobs and nothing else. Waiting for a job means polling its long-running operation (`run.operations.get`), which `run.invoker` does not include, so it also has a custom role with that single permission.

## How to run

```bash
uv sync                      # Python deps, dbt, dbt-duckdb, sqlfluff, pre-commit
uv run pre-commit install    # once

# Local, no GCP: lake under data/, warehouse in DuckDB
uv run python -m ingestion land   --load-date 2016-01-01 --sink local
uv run python -m ingestion ingest --load-date 2016-01-01 --sink local
cd dbt && uv run dbt build --target local && cd ..

# Dev BigQuery, as sa-dev
gcloud auth application-default login --impersonate-service-account=sa-dev@<project-id>.iam.gserviceaccount.com
export OLIST_PROJECT_ID=<project-id>
uv run python -m ingestion land   --load-date 2016-01-01 --env dev --sink gcs
uv run python -m ingestion ingest --load-date 2016-01-01 --env dev --sink gcs
cd dbt && uv run dbt build --target dev && cd ..

# Rerun check (dev only)
uv run python -m ingestion check-rerun --load-date 2016-01-01 --sink local

# Tests and lint (CI runs the same)
uv run pytest
uv run ruff check . && uv run ruff format --check .
cd dbt && uv run sqlfluff lint models

# Prod: the schedule runs daily, a merge to main deploys. By hand:
gcloud workflows run pipeline --location=us-central1
gcloud run jobs execute ingestion --region=us-central1 --wait \
  --args=ingest,--env,prod,--sink,gcs,--load-date,2016-01-01
```

Simulated batches 2 and 3: [schema-evolution.md](schema-evolution.md#how-to-run) and [scd2.md](scd2.md#how-to-run).

## Found on rollout

- Enabling the Workflows API creates its service agent shortly afterward, so the first `apply` failed creating the workflow. A second apply a minute later succeeded.
- The first workflow run failed with 403 on `run.operations.get`, which led to the custom role above.
- Schema evolution needed two fixes the plan did not cover: adding contract columns to bronze before loading, and incremental silver runs that read only key and lineage columns ([schema-evolution.md](schema-evolution.md#why)).
- Days skipped before quarantine existed had no quarantine files, so the external table had nothing to read; skipped dates now get an empty file.
- The first deploy was refused a token because of GitHub's immutable OIDC subject, and the first PR run printed the project id in one job ([security-and-ci.md](security-and-ci.md)).

## Cost

About zero; everything stays inside the free tiers.

| Item | Use | Free tier |
|---|---|---|
| GCS lake buckets | ~400 MB each (3 batches of CSV and Parquet) | 5 GB |
| BigQuery storage | ~870 MB bronze, plus silver, gold, ops | 10 GB |
| BigQuery queries | a few hundred MB per daily dbt build | 1 TB / month |
| BigQuery load jobs | 9 per batch | free |
| Artifact Registry | ~0.4 GB (2 versions per image, shared layers) | 0.5 GB |
| Cloud Run jobs | ~4 minutes a day on 1 CPU | Cloud Run free tier |
| Cloud Scheduler | 1 job | 3 jobs |
| Workflows | ~4 steps a day | 5,000 steps / month |
| Alert policies | 2, on built-in metrics | free |
| GitHub Actions | ~10 runner minutes per PR | 2,000 min / month private, unlimited public |
