# Progress

Phases from [implementation-plan.md](implementation-plan.md).

- [x] **Phase 0: Setup.** GCP project with a budget alert, Terraform state bucket, tools installed in WSL, dataset downloaded and profiled (with Polars). Still open: the GitHub repo (needed by Phase 8) and the written grain and star schema design (needed before Phase 4).
- [x] **Phase 1: Ingestion.** Data contracts, `land` and `ingest` commands, Terraform foundation, Cloud Run Job. Loaded the full dataset into `dev_bronze` and `bronze`; counts match the source.
- [ ] Phase 2: Idempotency
- [ ] Phase 3: Silver models in dbt
- [ ] Phase 4: Gold layer, star schema
- [ ] Phase 5: Schema evolution
- [ ] Phase 6: SCD2
- [ ] Phase 7: Data quality, contracts, and monitoring
- [ ] Phase 8: CI/CD
- [ ] Phase 9: AI layer, text-to-SQL agent
- [ ] Phase 10: Polish
