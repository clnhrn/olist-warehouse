# Progress

Phases from [implementation-plan.md](implementation-plan.md).

- [x] **Phase 0: Setup.** GCP project with a budget alert, Terraform state bucket, tools installed in WSL, dataset downloaded and profiled (with Polars). Grain and star schema design written up in [star-schema-design.md](star-schema-design.md). Still open: the GitHub repo (needed by Phase 8).
- [x] **Phase 1: Ingestion.** Data contracts, `land` and `ingest` commands, Terraform foundation, Cloud Run Job. Loaded the full dataset into `dev_bronze` and `bronze`; counts match the source.
- [x] **Phase 2: Idempotency.** `check-rerun` runs ingest plus `dbt build` twice and covers bronze, silver, and gold (all 28 tables unchanged, locally and in dev BigQuery).
- [x] **Phase 3: Silver models in dbt.** 9 silver models (incremental merge, latest row per key by `_load_date` then `_loaded_at`), 102 tests plus 3 unit tests, DuckDB and BigQuery targets, SQLFluff and pre-commit. Daily Scheduler -> Workflow -> ingestion job -> dbt job; prod silver built, counts match dev.
- [x] **Phase 4: Gold layer, star schema.** 4 facts and 5 dimensions with enforced contracts on both engines, unknown members, SCD2-shaped `dim_seller`, monthly partitions and clustering on BigQuery, 63 tests. Built in DuckDB and dev BigQuery with identical results; prod gold built by the pipeline, counts match dev.
- [ ] Phase 5: Schema evolution
- [ ] Phase 6: SCD2
- [ ] Phase 7: Data quality, contracts, and monitoring
- [ ] Phase 8: CI/CD
- [ ] Phase 9: AI layer, text-to-SQL agent
- [ ] Phase 10: Polish
