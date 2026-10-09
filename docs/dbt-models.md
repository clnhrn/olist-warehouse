# dbt models: silver and gold

The [`dbt/`](../dbt/) project turns bronze into a typed, deduplicated silver layer and a Kimball star schema in gold. Column-level design for gold, with the data findings behind each choice: [star-schema-design.md](star-schema-design.md). Seller history: [scd2.md](scd2.md).

## One project, two engines

| Target | Engine | Reads | Writes | Credentials |
|---|---|---|---|---|
| `local` | DuckDB (`data/warehouse.duckdb`) | `data/bronze/` Parquet | `dev_*` schemas | none |
| `dev` | BigQuery | `dev_bronze` | `dev_silver`, `dev_gold` | ADC as `sa-dev` locally, `sa-ci-pr` in CI |
| `prod` | BigQuery (Cloud Run only) | `bronze` | `silver`, `gold` | the job's service account |

A schema macro maps every target to the layer datasets (`prod` writes `silver`, everything else `dev_silver`). Locally, an `on-run-start` hook exposes the Parquet as DuckDB views named like the BigQuery tables, so sources resolve the same way on both engines. The profile holds no secrets: BigQuery targets use Application Default Credentials.

## Silver

One model per bronze table (`silver_<table>`), with a description and tests for every column.

- Casts every column from bronze's STRING to its real type, renames columns (`order_delivered_carrier_date` to `shipped_at`, and the `lenght` typos to `length`), trims strings, and turns blank review text into null.
- City names are lowercased, trimmed, and stripped of accents (`São Paulo` to `sao paulo`), with one macro per engine.
- Timestamps stay `TIMESTAMP` on both engines. They are Brazil local time as the source gives them.
- Category names stay in Portuguese; gold joins the English names (plus a seed for the 2 missing ones) in `dim_product`.
- Geolocation has no business key, so it is rebuilt from the latest batch with exact duplicates removed (1,000,163 to 738,332 rows). Gold picks one point and spelling per zip.

### Incremental deduplication

Every other model is incremental with `merge` on its key: each run reads only bronze rows loaded since the last run and merges the latest row per key into silver. The incoming rows also compete with the current silver row for the same key. Without that, a rerun of an old batch would pass the `_loaded_at` filter and overwrite newer data (see [idempotency.md](idempotency.md)). Two macros, [`bronze_since_last_run` and `latest_row_per_key`](../dbt/macros/silver.sql), carry the rule for every model. An incoming row is kept only if silver has no newer row for its key, and only the key and lineage columns of the existing table are read, so a model that gains a column still runs incrementally.

Unit tests pin it down: batch order beats load time on a full build, an old batch rerun keeps the newer row on an incremental run, a genuinely newer batch replaces the row, and a rename carries across contract versions.

## Gold: the star schema

| Table | Grain | Rows |
|---|---|---|
| `fact_order_items` | one row per order item | 112,650 |
| `fact_orders` | one row per order (accumulating snapshot), including 775 orders with no items | 99,441 |
| `fact_payments` | one row per payment record | 103,886 |
| `fact_reviews` | one row per review per order | 99,224 |
| `dim_customer` | one row per person (`customer_unique_id`) | 96,096 + unknown |
| `dim_location` | one row per zip code prefix (customer and seller roles) | 19,177 + unknown |
| `dim_product` | one row per product, with English category names | 32,951 + unknown |
| `dim_seller` | one row per seller per version (SCD2) | 3,115 + unknown |
| `dim_date` | one row per day, 2016 to 2020 | 1,827 + unknown |

An accumulating snapshot has one row per process (an order) that fills in as the process moves forward: approved, shipped, delivered.

### Design choices

- **Why these grains.** Item revenue lives at item level, because 1,278 orders have items from several sellers. Delivery measures (days to deliver, lateness) live on `fact_orders`, so they are never repeated per item. Payments get their own fact and are never split across items: they differ from the item total for 303 orders (interest, vouchers).
- **Surrogate keys.** Dimension keys are hashes of the natural key (`dbt_utils.generate_surrogate_key`). They are stable across rebuilds, so a rerun never reassigns keys. `dim_date` uses a readable `YYYYMMDD` integer. `order_id` stays on the facts as a degenerate dimension.
- **Unknown members.** Every dimension has a `'-1'` row (`-1` for dates), and every fact foreign key is non-null. A milestone that has not happened (an undelivered order's delivery date) points to `-1` instead of null, so inner joins never drop facts.
- **Rebuilt, not merged.** Gold tables are rebuilt from silver on every run. At this size that costs seconds, and a full rebuild is idempotent by construction.

### Contracts

Every gold model has an enforced dbt model contract: the build fails if a column's name or type differs from the YAML. The contracts use types both engines accept (`string`, `int64`, `bool`, `date`, `timestamp`, `numeric(p, s)`), so the same YAML enforces on DuckDB and BigQuery. BigQuery does not allow `numeric(18, 2)` in a `CAST`, only in a column definition, so a `to_numeric` macro rounds in SQL and the contract's DDL (the `CREATE TABLE` statement) sets the precision. Key columns also carry `not_null` constraints, which both engines enforce.

### Partitioning and clustering

`fact_order_items` and `fact_orders` are partitioned by purchase month and clustered by their common filters (`seller_key`, `product_key`; `customer_key`, `order_status`). Partitioning splits a table into chunks by a column, so a query filtered on that column scans only the matching chunks. Clustering sorts data within each chunk, so filters on those columns skip blocks.

Each fact table is tens of MB, far below where this changes cost, but the text-to-SQL agent queries these tables, and on real volumes it is what keeps a date-filtered query cheap. Monthly partitions, not daily, because BigQuery caps a table at 10,000 partitions and daily partitions this small add overhead. DuckDB has no equivalent, so the config applies on BigQuery only.

## Tests

**Silver (102):** `unique` and `not_null` on every key (`unique_combination_of_columns` for composite keys), `relationships` between orders, items, payments, reviews, customers, products, and sellers, `accepted_values` for statuses, payment types, scores, and the 27 state codes, plus value checks (`price > 0`). Bronze sources have freshness checks on `_loaded_at`; the dataset is a one-time snapshot, so they only warn.

**Gold (63), on top of the contracts:**

- Primary keys: `unique` (`unique_combination_of_columns` for the composite fact keys).
- Every fact foreign key exists in its dimension (`relationships`).
- Fact row counts equal silver (`equal_rowcount`), so no join fans out or drops rows.
- `fact_orders` totals add up to `fact_order_items` (a singular test).
- SCD2 rules on `dim_seller` ([scd2.md](scd2.md#tests)).

**Known source issues** are tests at warn severity, so they stay visible without failing the build. Their failing rows are stored in `ops.failures_*` ([data-quality.md](data-quality.md#stored-test-failures)):

| Warning | Rows |
|---|---|
| Payments with 0 installments | 2 |
| Delivered orders without a delivery date | 8 |
| Products whose category is missing from the translation table | 13 |

## Linting

SQLFluff lints the models through its dbt templater against the local target, so it needs no credentials or data (it runs the same way in CI). Pre-commit runs ruff, SQLFluff, `terraform fmt`, and basic file checks on every commit, all through uv, so versions come from `uv.lock`.

## Result

The same numbers come out of both engines: 775 orders with no items, 14 status and date mismatches, 6,535 late deliveries, and revenue (excluding canceled and unavailable orders) of BRL 13,494,400.74. Prod gold matches dev in all 9 tables.
