# SCD2: seller history

SCD2 (slowly changing dimension, type 2) keeps the old row when an attribute changes and adds a new one with its own validity range. An order is then reported with the seller's address at purchase time, not today's.

## Batch 3

Olist has no seller changes, so batch 3 (`load_date=2016-03-01`) simulates them. [`scripts/make_batch3.py`](../scripts/make_batch3.py) copies batch 2 and moves 20 sellers to another state on 2018-01-01. Those are the first 20 by `seller_id` with orders both before and after that date. Each takes the address of a different unchanged seller, so the new zip is a known location, and gets `seller_updated_at = 2018-01-01`. The script is deterministic, and contract v2 still applies.

## History from bronze, not a dbt snapshot

The plan called for a dbt snapshot on silver sellers. A snapshot copies the rows it sees each time dbt runs, but silver keeps only the latest row per seller. The history would then depend on dbt having run between batches. A fresh rebuild, or two batches arriving before one dbt run, would lose the 2016 versions, and the pre-2018 orders of the 20 moved sellers would fall to the unknown seller.

Bronze is append-only and already keeps every batch, so `silver_seller_versions` rebuilds the history from it on every run:

- One version per `(seller_id, updated_at)`. When several batches carry the same version, the latest batch wins (`_load_date`, then `_loaded_at`), the same rule as silver deduplication. Batches 1 and 2 both carry the 2016-01-01 baseline (batch 1 predates the column), so they collapse into one version.
- `valid_from` is the business `updated_at`, never the run time. `valid_to` is the next version's start, ordered by `updated_at` rather than by batch, so a late batch with an older change still slots in between the right versions.
- `dim_seller` reads it, with keys hashed from `seller_id` and `valid_from` (so baseline keys did not change when the history arrived), and `fact_order_items` joins the version valid at purchase: `valid_from <= purchased_at < coalesce(valid_to, '9999-12-31')`.
- `dim_location` takes seller zips from every version, so old addresses keep their location row.

It is a small table (3,115 rows), so a full rebuild each run costs nothing.

## Tests

- `dim_seller`: exactly one current row per seller (unique where `is_current`, plus a singular test that no seller has zero), and `dbt_utils.mutually_exclusive_ranges` so a seller's versions neither overlap nor leave gaps.
- `assert_no_order_item_falls_to_unknown_seller`: fails if lost history pushes any order item onto the unknown seller.
- Unit tests: the batch 1, 2, and 3 versions; a version redelivered with a correction; a change that arrives late; and the fact join picking the old version for a 2017 order and the new one for a 2018 order.

## Result

Locally (DuckDB), in dev BigQuery, and in prod:

- `dim_seller`: 3,115 versions plus unknown, 3,095 current. The 20 moved sellers have 707 order items on their 2016 version (last purchase 2017-12-31) and 697 on their 2018 version.
- `fact_order_items` kept 112,650 rows, none on the unknown seller. Revenue is unchanged at BRL 13,494,400.74.
- A fresh DuckDB build over all 3 batches in one `dbt build` gives the same `silver_seller_versions`, `dim_seller`, `dim_location`, and `fact_order_items` as the batch-by-batch warehouse, row for row. That is the case a snapshot would have failed.
- `check-rerun` for batch 3 left all 29 tables unchanged. Prod matches dev in all 29.

## How to run

```bash
uv run python scripts/make_batch3.py     # data/raw-batch2 -> data/raw-batch3
uv run python -m ingestion land   --load-date 2016-03-01 --source-dir data/raw-batch3 --sink local
uv run python -m ingestion ingest --load-date 2016-03-01 --sink local
cd dbt && uv run dbt build --target local
```
