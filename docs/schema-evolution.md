# Schema evolution: what is accepted, what blocks

A source schema change reaches the warehouse in one way only: a new contract version in `contracts/vN/`, merged through review. Changes the contracts do not announce stop the batch. Changes they do announce flow through without manual steps in BigQuery, as long as they are additive or a declared rename.

## How a version applies

- A version folder holds only the tables that changed in it. Each contract has an `effective_from` load date (v1 has none and applies from the first batch).
- Every load date is validated against the version in force on that date. Rerunning batch 1 still uses v1, so reruns never break after a schema change.
- Loading the contracts checks each table's history (at job start and in `pytest`). A breaking change between two versions stops everything before any data moves.

## Decisions

| Change | Accepted? | Where it is stopped or handled |
|---|---|---|
| New column, declared in a new contract version | Accepted | Ingestion adds it to the bronze table before loading (null for older batches). Silver picks it up when a model selects it (`on_schema_change: append_new_columns`). Gold only changes when its contract YAML changes. |
| Rename, declared with `renamed_from` | Accepted, with a code change | Bronze keeps both names: old batches fill the old column, new batches the new one. The silver model must `coalesce` them in the same pull request. Silver and gold column names stay the same. |
| New column the contract does not list | Blocks | Pandera (`strict=True`) fails the batch. The producer must announce it first. |
| Column missing from the file, or the old file format after a new version takes effect | Blocks | Pandera fails the batch. |
| Removed column, or rename without `renamed_from` | Blocks | Loading the contracts fails: "column X was removed". |
| Parse kind change (for example `int` to `decimal`) | Blocks | Loading the contracts fails. Silver casts depend on the parse kind. |
| Required column becomes nullable | Blocks | Loading the contracts fails. Silver tests and gold `not_null` constraints assume it is present. |
| Primary key change | Blocks | Loading the contracts fails. Silver merges on the key. |
| Silver column type change | Manual | `append_new_columns` only adds columns. A type change needs `dbt build --full-refresh` on that model (bronze holds every batch, so this is safe). |
| Gold column added, removed, or retyped | Blocks until the contract is updated | Gold tables are rebuilt on every run, and their enforced dbt contracts fail the build on any difference from the YAML. |

A blocked change is a code change, not an operations task: add the contract version (and the silver `coalesce` for a rename), and the next scheduled run picks it up.

## Why

- **Version by load date, not by sniffing the header.** If ingestion picked whichever known version matched the file, an old-format file arriving after the cutover would pass without anyone noticing. Tying a version to a date makes the cutover explicit and reviewable, and keeps reruns of old batches on their own version.
- **Bronze keeps both names after a rename.** Bronze is a faithful copy of each batch. Renaming the column in bronze would rewrite history, and old partitions would need a backfill. Silver is where names are standardized.
- **The schema is added before loading, not only during it.** A BigQuery load job can add columns (`ALLOW_FIELD_ADDITION`, still set), but only when a batch with the new column loads. The scheduled run skips unchanged dates, so a new dbt image could reach silver before any batch adds the column. Ingestion adds every contract column to its bronze table on each run, including runs that skip. It only ever adds columns.
- **Silver uses `append_new_columns`.** `fail` would force a full refresh for every additive change. `sync_all_columns` would also drop columns, silently. Adding is the only change that is always safe for existing rows.
- **Silver incremental runs read only key and lineage columns from the existing table.** That is what makes `append_new_columns` work: an incremental run that reads every existing column would fail on the first run after a model gains a column, before dbt could add it.

## Batch 2

Batch 2 (`load_date=2016-02-01`) simulates both accepted cases. It is a full snapshot of all 9 files, built from the originals by [`scripts/make_batch2.py`](../scripts/make_batch2.py):

- `products` v2: `product_name_lenght` and `product_description_lenght` renamed to `..._length`.
- `sellers` v2: new required column `seller_updated_at`. No seller changes yet, so every value is the 2016-01-01 baseline. Phase 6 adds seller changes in a later batch.
