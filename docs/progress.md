# Progress

Phases from [implementation-plan.md](implementation-plan.md).

- [x] **Phase 0: Setup.** GCP project with a budget alert, Terraform state bucket, tools installed in WSL, dataset downloaded and profiled (with Polars). Grain and star schema design written up in [star-schema-design.md](star-schema-design.md). Still open: the GitHub repo (needed by Phase 8).
- [x] **Phase 1: Ingestion.** Data contracts, `land` and `ingest` commands, Terraform foundation, Cloud Run Job. Loaded the full dataset into `dev_bronze` and `bronze`; counts match the source.
- [x] **Phase 2: Idempotency.** `check-rerun` runs ingest plus `dbt build` twice and covers bronze, silver, and gold (all 28 tables unchanged, locally and in dev BigQuery).
- [x] **Phase 3: Silver models in dbt.** 9 silver models (incremental merge, latest row per key by `_load_date` then `_loaded_at`), 102 tests plus 3 unit tests, DuckDB and BigQuery targets, SQLFluff and pre-commit. Daily Scheduler -> Workflow -> ingestion job -> dbt job; prod silver built, counts match dev.
- [x] **Phase 4: Gold layer, star schema.** 4 facts and 5 dimensions with enforced contracts on both engines, unknown members, SCD2-shaped `dim_seller`, monthly partitions and clustering on BigQuery, 63 tests. Built in DuckDB and dev BigQuery with identical results; prod gold built by the pipeline, counts match dev.
- [x] **Phase 5: Schema evolution.** Contract versions by `effective_from` load date, with a breaking-change check. Batch 2 (full snapshot) renames two product columns and adds `seller_updated_at`. Bronze adds the new columns before loading, silver merges the renames and adds the new column without a full refresh, and gold is unchanged. Rules in [schema-evolution.md](schema-evolution.md). Verified locally, in dev, and in prod; all 28 tables match.
- [x] **Phase 6: SCD2.** Seller history rebuilt from append-only bronze by `silver_seller_versions` (not a dbt snapshot, which loses versions on a fresh rebuild). Batch 3 moves 20 sellers on 2018-01-01; `dim_seller` has 3,115 versions, and facts join the version valid at purchase. Tests for one current row, no overlaps or gaps, and no item on the unknown seller. A fresh rebuild matches the batch-by-batch build; verified locally, in dev, and in prod (all 29 tables match).
- [x] **Phase 7: Data quality, contracts, and monitoring.** Row-level quarantine (external table, 1% limit, structural changes still fail), `ops.pipeline_runs` audit log shared by ingestion and dbt under the workflow's run id, stored failures for warning tests, a batch row count test instead of Soda, and email alerts on failed jobs and workflows (tested). Verified locally, in dev, and in prod.
- [ ] Phase 8: CI/CD
- [ ] Phase 9: AI layer, text-to-SQL agent
- [ ] Phase 10: Polish
