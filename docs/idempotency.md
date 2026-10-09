# Idempotency

Idempotent means running the same load date twice leaves the warehouse exactly as one run would. It matters because a scheduler retries failed runs, and a retry must never double the data.

## Bronze

Rerunning a load date can never duplicate rows:

- Each table load uses `WRITE_TRUNCATE` on its own partition (`orders$20160101`), so it replaces exactly that date and nothing else.
- A manifest records the MD5 of every landing file it loaded. If the files have not changed, a rerun skips the date. `--force` reloads anyway.

## Silver: rerunning an old batch

Bronze keeps every batch, so silver has to pick the latest row per business key. The order it uses matters:

| Batch | `_load_date` | `_loaded_at` |
|---|---|---|
| Batch 1, rerun today | 2016-01-01 | today |
| Batch 2, loaded last week | 2016-02-01 | last week |

Ordering by `_loaded_at` alone would let the rerun of batch 1 win, and old data would overwrite new data. Silver orders by `_load_date` first, then `_loaded_at`, so batch 2 still wins. The incremental models apply the same rule against the rows already in silver ([dbt-models.md](dbt-models.md#incremental-deduplication)), and dbt unit tests cover it for a full build and for an incremental run.

## Gold

Gold tables are rebuilt from silver on every run, so they are idempotent by construction.

## The rerun check

```
python -m ingestion check-rerun --load-date D --sink local          # local lake + DuckDB
python -m ingestion check-rerun --load-date D --sink gcs --env dev  # dev_* datasets
```

One run step is a forced ingest of the date followed by `dbt build`. The check runs that step, records the row count of every table in every layer, runs it again, and exits non-zero if any count changed. Counts are keyed by layer (`bronze.orders`, `silver.silver_orders`, `gold.fact_orders`). `--no-dbt` checks bronze only. It refuses `--env prod`, because it writes data. In BigQuery the counts come from table metadata, so counting runs no queries and costs nothing.

## Result

- On the full dataset, all 9 bronze, 10 silver (9 models and 1 seed), and 9 gold tables kept their counts, both locally and in dev BigQuery (for example, 99,441 orders in every layer, 1,000,163 bronze and 738,332 silver geolocation rows, 112,650 rows in `fact_order_items`).
- A forced bronze rerun left each partition holding only the second run's `_loaded_at`.
- Later batches kept the property: all 28 tables unchanged for batch 2 (and for batch 1 rerun under contract v1 after v2 took effect), all 29 for batch 3.
