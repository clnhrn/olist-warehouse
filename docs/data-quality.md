# Data quality and monitoring

Each layer checks what it is responsible for. Ingestion checks shape and types against the Pandera contracts, dbt tests check business rules, and the gold contracts check what consumers see. Every run leaves a record behind, and a failure sends an email.

## Quarantine

A row that fails its contract (bad format, missing required value) goes to quarantine, and the rest of the file loads:

- Rejects are written to `quarantine/table_name=<table>/load_date=YYYY-MM-DD/data.parquet` in the lake bucket. Each one keeps its record number in the file, the checks it failed (`column:check`), the raw record as JSON, `_loaded_at`, and `_source_file`.
- `bronze.quarantine` is a BigQuery external table over those files, meaning BigQuery reads them in place. A rerun overwrites the date's file, so quarantine can never hold duplicates and there is nothing to keep in sync. Dates that skip (already loaded) get an empty file if they have none, so the table always has files to read.
- Limits: if more than 1% of a table's rows fail, the file is treated as broken, not as having a few bad records, and the batch stops. A structural change (a missing, extra, or renamed column) also stops the batch, because that is a contract change, not bad data ([schema-evolution.md](schema-evolution.md)).

## pipeline_runs

`ops.pipeline_runs` (`dev_ops` in dev) is the audit log, append-only, one row per object per run:

| Column | Meaning |
|---|---|
| `run_id` | The workflow execution id, passed to both jobs as `OLIST_RUN_ID`, so ingestion and dbt rows of one run share it |
| `component`, `layer`, `object` | `ingestion` (bronze, one row per table and load date) or `dbt` (one row per model, plus one `tests` summary row) |
| `load_date`, `started_at`, `ended_at` | When, and for which batch |
| `rows_in`, `rows_out`, `rows_quarantined` | Rows read, loaded, and quarantined. For the tests row, `rows_in` is the number of tests |
| `status`, `message` | `loaded`, `skipped`, or `failed` for ingestion; dbt's status and message (for tests: `pass=177 warn=3 fail=0 ...`) |

Ingestion writes its rows with a BigQuery load job (free, unlike streaming inserts), even when the load fails. dbt writes its rows from an `on-run-end` hook, which also runs when the build fails. Locally, ingestion writes `data/_runs/<run_id>.jsonl`, and the hook copies those rows into DuckDB.

## Stored test failures

The three warning-level tests (delivered orders without a delivery date, payments with zero installments, untranslated product categories) keep their failing rows in `ops.failures_*` tables. A warning then points to the actual rows, not just a count.

## Row count anomalies

The plan listed Soda Core as optional. Freshness is already covered by dbt source freshness, so the only gap was row count anomalies. A small dbt generic test (`batch_row_count_change`) on every bronze source covers it. It warns when the newest batch's row count differs by more than 50% from the batch before, for example a truncated or duplicated file. That is one test file instead of another tool, config, and container dependency.

## Alerts

Two Cloud Monitoring alert policies email the developer: one when a Cloud Run job execution fails, one when the pipeline workflow fails. Alerting on these built-in metrics is free. A failed job also fails the workflow, so one failure usually sends two emails; the workflow alert alone also catches failures outside the jobs, such as a permission error. Tested by running the ingestion job on a date with no files (`2099-01-01`); the email arrived.

## Audit trail

Together these answer the usual audit questions without extra tooling:

- **Where did a row come from?** `_source_file`, `_load_date`, and `_loaded_at` on every bronze row, and landing files are never modified.
- **What was rejected, and why?** `bronze.quarantine`, with the raw record and the failed checks.
- **What ran, when, and with what result?** `ops.pipeline_runs`, append-only, one run id across ingestion and dbt.
- **Which rows broke a business rule?** The `ops.failures_*` tables.
- **Which rules applied?** The contracts are versioned in git, by `effective_from` load date.

## Result

- Locally: 2 corrupted seller rows were quarantined with their record number and failed check, the other 3,093 loaded, and the run row shows `rows_quarantined=2`.
- Dev and prod: `bronze.quarantine` queries cleanly and has 0 rows (the real data passes its contracts). Reruns still leave every table unchanged.
- Prod run `a8bff5fe` (2026-10-08): 27 ingestion rows (`skipped`), 20 dbt model rows and the tests row (`pass=177 warn=3 fail=0`) under one run id.

## Queries

```sql
-- The last run, ingestion and dbt together
select component, layer, object, status, rows_out, rows_quarantined, message
from ops.pipeline_runs
where run_id = (select run_id from ops.pipeline_runs order by started_at desc limit 1)
order by started_at;

-- Rejected rows
select table_name, load_date, source_row, failed_checks, raw_record
from bronze.quarantine;
```
