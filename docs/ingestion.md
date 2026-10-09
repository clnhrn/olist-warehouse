# Ingestion

How raw CSVs become bronze tables: data contracts, the `land` and `ingest` commands, and the lake layout. Rejected rows and the audit log are in [data-quality.md](data-quality.md); reruns in [idempotency.md](idempotency.md); source schema changes in [schema-evolution.md](schema-evolution.md).

## Data contracts

Each source file has a versioned contract in [`contracts/v1/`](../contracts/v1/): its columns, which may be null, the primary key, and a `parse` kind per column (`id32`, `zip`, `int`, `decimal`, `timestamp`, `state`, `text`). One YAML file produces both:

- a strict Pandera schema used at ingestion: exact column set, values must match their parse kind, timestamps must be real dates, required columns cannot be null;
- the BigQuery bronze schema: every source column as STRING, plus the three lineage columns.

Bronze stores everything as STRING on purpose. Bronze is a faithful copy of the source, so a value is never changed or lost on the way in: zip codes keep their leading zeros (`01037`), and a surprising value fails the batch instead of being silently cast. Silver does the casting.

Quirks the contracts were written against, found by profiling the full dataset:

- Zip code prefixes have leading zeros, which type inference would destroy.
- `product_category_name_translation.csv` starts with a UTF-8 BOM (Polars strips it).
- Review comments contain line breaks inside quoted fields.
- `review_id` alone is not unique; the key is `(review_id, order_id)`.
- Product columns keep the source typos (`product_name_lenght`); silver renames them.

## The ingestion job

```
python -m ingestion land   --load-date D --env dev|prod --sink gcs|local
python -m ingestion ingest --load-date D --env dev|prod --sink gcs|local [--force] [--tables ...]
python -m ingestion ingest --all-dates  ...   # every landed date, oldest first (the scheduled run)
```

- `land` copies the raw CSVs into `landing/load_date=D/`. It refuses a date that already has files, so landing is never modified.
- `ingest` reads each landing CSV with every column as a string, validates it against its contract, adds the lineage columns (`_loaded_at`, `_load_date`, `_source_file`), writes `bronze/<table>/load_date=D/data.parquet`, and loads that file into the table's `D` partition in BigQuery.
- Rows that fail their contract go to quarantine, up to 1% of a table; beyond that, or on a structural change, the batch stops. See [data-quality.md](data-quality.md#quarantine).
- `--sink local` writes the same layout under `data/` with no GCP access, for development and DuckDB.

Bucket layout (the same keys in GCS and in local `data/`):

```
landing/load_date=2016-01-01/olist_orders_dataset.csv
bronze/orders/load_date=2016-01-01/data.parquet
quarantine/table_name=orders/load_date=2016-01-01/data.parquet
_manifests/load_date=2016-01-01.json
```

## Tests

The tests use small synthetic CSVs, so they run without the dataset (and in CI). One extra test checks a sample of every real file when `data/raw/` exists. Contract loading, including the breaking-change check, is also tested.

## Result

The full dataset is loaded into `dev_bronze` and `bronze`, and row counts match the source files. A full load is 9 BigQuery load jobs; the job runs about 4 minutes on 1 CPU.
