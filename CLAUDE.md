# CLAUDE.md: Olist Warehouse

Portfolio data warehouse on the Olist Brazilian E-Commerce dataset (GCP, dbt, DuckDB, Polars), with a text-to-SQL agent on top.
Full plan: `docs/implementation-plan.md`. Read it before starting any phase.

## Architecture (medallion)

- Bronze (`bronze`): raw data as loaded, append-only by batch, partitioned by `_load_date`.
- Silver (`silver`): typed, renamed, deduplicated (latest row per key). dbt convention: staging.
- Gold (`gold`): star schema (facts, dimensions, metrics). dbt convention: marts.
- Dev copies: `dev_bronze`, `dev_silver`, `dev_gold`. CI and local work use dev only.
- Flow: CSVs -> GCS (`load_date=YYYY-MM-DD/`) -> bronze -> silver -> gold -> agent (reads gold only).

## Stack

- GCP: BigQuery, Cloud Storage, Cloud Run Jobs, Cloud Workflows, Cloud Scheduler, Secret Manager.
- Terraform for all infrastructure, with remote state in GCS.
- dbt (BigQuery adapter in CI, DuckDB adapter locally).
- Polars for ingestion. Pandas only if a library requires it.
- Pandera for ingestion contracts. dbt tests and model contracts in the warehouse.
- GitHub Actions for CI/CD, authenticated with Workload Identity Federation.

## Commands

Fill these in as the project takes shape.

- One-time setup: see `setup/README.md` (install tools, bootstrap GCP, Kaggle token, download data)
- Install dependencies: `uv sync`
- Ingest locally: `uv run python -m ingestion land --load-date D --sink local`, then `... ingest --load-date D --sink local`
- Ingest to dev BigQuery: impersonate `sa-dev`, set `OLIST_PROJECT_ID`, then `... ingest --load-date D --env dev --sink gcs`
- Ingest to prod: only via `gcloud run jobs execute ingestion --region=us-central1 --wait --args=ingest,--env,prod,--sink,gcs,--load-date,D`, with approval
- Full prod pipeline (ingest, then dbt): `gcloud workflows run pipeline --location=us-central1`, with approval. Also runs daily at 06:00 America/Sao_Paulo
- Rerun check (dev only): `uv run python -m ingestion check-rerun --load-date D --sink local` (or `--sink gcs --env dev`)
- Python tests: `uv run pytest`
- Lint Python: `uv run ruff check .` and `uv run ruff format --check .`
- Terraform: `terraform -chdir=terraform init -backend-config=backend.hcl`, then `plan -out=<name>.tfplan`; apply only with approval. ADC impersonates sa-dev, which cannot read the state, so Terraform runs with a token from your gcloud user login: `GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token) terraform ...`
- Build the images locally (test only, never push; CI pushes): `docker build --platform=linux/amd64 --provenance=false --sbom=false -f ingestion/Dockerfile -t ingestion .` (same with `-f dbt/Dockerfile -t dbt`)
- dbt runs from `dbt/`, where `profiles.yml` and its relative paths live: `cd dbt && uv run dbt build --target local` (DuckDB) or `--target dev` (BigQuery dev, needs `OLIST_PROJECT_ID`)
- Lint SQL: `cd dbt && uv run sqlfluff lint models`
- Pre-commit: `uv run pre-commit install` once; hooks run ruff, sqlfluff, and terraform fmt
- CI/CD: open a PR (`gh pr create`); `.github/workflows/pr.yml` runs lint, tests, `terraform plan`, dbt on dev, and image builds. Merging to main runs `deploy.yml`: terraform apply, push images, update jobs, run the prod dbt job. Merge only with approval. Watch with `gh pr checks <n> --watch` and `gh run watch <id>`

## Rules (always follow)

1. Never run `terraform apply`, `terraform destroy`, or any delete command without my explicit approval.
2. Never write to prod datasets (`bronze`, `silver`, `gold`) from a local session. Use `dev_` datasets or DuckDB.
3. Never create or commit service account key files. Use Workload Identity Federation for CI.
4. Never put secrets, API keys, or project IDs with credentials in code, prompts, or logs. Use Secret Manager.
5. Every BigQuery query written by the agent must set `maximum_bytes_billed` and run as the read-only service account.
6. Confirm the fact table grain with me before building any fact model.
7. Keep costs near zero: no Cloud Composer, `us-central1` for all resources (regional, not the `US` multi-region), small container images.

## Conventions

- Models: `silver_<source_table>` style names in `models/silver/`, `fact_*` and `dim_*` in `models/gold/`.
- Every model has a description and column descriptions in its YAML file.
- Every silver model has `unique` and `not_null` tests on its key. Gold models use enforced dbt model contracts.
- Dimensions use surrogate keys. `order_id` stays on facts as a degenerate dimension.
- SCD2 history is rebuilt from append-only bronze by a model (versions by a business `updated_at`, never run time), not a dbt snapshot, so a fresh rebuild keeps every version.
- Idempotency: rerunning the same `load_date` must not change row counts in any layer. Test this.
- Add `_loaded_at`, `_load_date`, and `_source_file` to every bronze table.
- Silver deduplication orders by `_load_date` then `_loaded_at`, never `_loaded_at` alone.
- Bronze uses explicit schemas from `contracts/`, never BigQuery autodetect.
- Landing (`landing/load_date=YYYY-MM-DD/`) holds raw CSVs and is never modified.
- Local sessions use the dev service account (impersonation) or DuckDB. Run Python through `uv run`.
- Use Polars `scan_csv` and lazy evaluation where possible.
- Code style: type hints in Python, `ruff` for linting, small functions, no print statements (use logging).
- Docstrings follow the Google style (`Args:`, `Returns:`, `Raises:`, `Attributes:`), enforced by ruff's `D` rules. Tests are exempt.

## Working style

- Work one phase at a time, in the order of the plan. Use plan mode for Terraform, authentication, and SCD2.
- Explain any technical term in one short sentence the first time it appears.
- Keep answers short and direct.
- Commit after each working step. Do not mix phases in one commit. Follow the commit rules below.
- After each phase, update the topic doc in `docs/` it belongs to (or add one), add a README highlight only if it earns one, and tick it off in `docs/progress.md`. The README stays a short overview; detail and results go in the docs.
- If something in the plan looks wrong or a better option exists, say so before building.

## Commits (Conventional Commits)

Format: `<type>(<scope>): <short summary>`

- Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`.
- Scopes: `ingestion`, `dbt`, `silver`, `gold`, `snapshots`, `terraform`, `agent`, `contracts`, `ci`, `docs`.
- Summary: imperative mood, lowercase, no trailing period, 72 characters or fewer. Example: `feat(gold): add fact_orders accumulating snapshot`.
- Add a body only when the why is not obvious. Wrap lines at 72 characters.
- Breaking changes: add `!` after the scope (`feat(contracts)!: rename order_status column`) and a `BREAKING CHANGE:` footer.
- One logical change per commit. Never mix infrastructure, dbt models, and docs in one commit.
- Never commit to `main` directly. Use short-lived branches named `<type>/<short-description>`, for example `feat/dim-seller-scd2`.

## Known Olist data issues

- Duplicate rows in the geolocation table.
- Product category names are in Portuguese. Use the translation table.
- The product table has typos in column names (`product_name_lenght`, `product_description_lenght`).
- Some orders have missing delivery timestamps.
- Data is a one-time snapshot. Batch 2 (schema change) and batch 3 (seller changes) are simulated.
