"""Landing CSV -> validate against contract -> add lineage -> bronze Parquet (-> BigQuery).

Rows that fail their contract go to quarantine instead of bronze, as long as
they are a small share of the table; a structural break or a mostly bad file
still stops the batch. Rerunning a load date overwrites that date's Parquet
(bronze and quarantine) and replaces that date's BigQuery partition, so
retries never create duplicates.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pandera.errors as pa_errors
import polars as pl

from contracts.loader import ContractRegistry, TableContract, bronze_schema, pandera_schema
from ingestion.config import bronze_key, landed_dates, landing_key, quarantine_key
from ingestion.manifest import Manifest, TableEntry, read_manifest, write_manifest
from ingestion.runs import RunLog, RunRecord
from ingestion.storage import Storage
from ingestion.warehouse import BigQueryWarehouse

logger = logging.getLogger(__name__)

MAX_FAILURES_LOGGED = 20
# Above this share of failing rows, the file is treated as broken, not as
# holding a few bad records, and the batch stops.
MAX_QUARANTINE_FRACTION = 0.01


class ContractViolationError(RuntimeError):
    pass


@dataclass(frozen=True)
class IngestResult:
    skipped: bool
    rows: dict[str, int]
    quarantined: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class TableLoad:
    rows_in: int
    rows_out: int
    rows_quarantined: int


def read_landing_csv(data: bytes) -> pl.DataFrame:
    """Every column as Utf8, so bronze stays as-is (zip codes keep leading zeros).

    Reads from bytes because landing may be GCS; the largest file is ~60 MB.
    Polars strips a UTF-8 BOM from the header.
    """
    return pl.read_csv(io.BytesIO(data), infer_schema=False)


def _violation(contract: TableContract, failures: pl.DataFrame, reason: str) -> Exception:
    logger.error(
        "Contract violations in %s, %s (%d failure cases, first %d):\n%s",
        contract.table,
        reason,
        failures.height,
        MAX_FAILURES_LOGGED,
        failures.head(MAX_FAILURES_LOGGED),
    )
    return ContractViolationError(
        f"{contract.table} violates contract v{contract.version}: {reason}"
    )


def empty_rejected() -> pl.DataFrame:
    return pl.DataFrame(
        schema={"source_row": pl.Int64, "failed_checks": pl.Utf8, "raw_record": pl.Utf8}
    )


def split_valid(df: pl.DataFrame, contract: TableContract) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Valid rows, and rejected rows with the checks they failed.

    Pandera gives each row-level failure (bad format, missing required value)
    the row's index; a structural failure (a missing, extra, or renamed
    column) has none and stops the batch, as does a mostly bad file.
    """
    try:
        pandera_schema(contract).validate(df, lazy=True)
        return df, empty_rejected()
    except pa_errors.SchemaErrors as exc:
        failures = exc.failure_cases
        if failures["index"].null_count() > 0:
            raise _violation(contract, failures, "structural change") from exc

    per_row = failures.group_by(pl.col("index").cast(pl.Int64)).agg(
        pl.format("{}:{}", "column", "check").unique().sort().str.join("; ").alias("failed_checks")
    )
    if per_row.height > MAX_QUARANTINE_FRACTION * df.height:
        raise _violation(
            contract,
            failures,
            f"{per_row.height} of {df.height} rows fail (limit {MAX_QUARANTINE_FRACTION:.0%})",
        )

    rows = df.with_row_index("_row").with_columns(pl.col("_row").cast(pl.Int64))
    rejected = (
        rows.join(per_row, left_on="_row", right_on="index")
        .select(
            # Record number in the file, header not counted. Not a line
            # number: quoted values (review comments) can span lines.
            (pl.col("_row") + 1).alias("source_row"),
            "failed_checks",
            pl.struct(contract.column_names).struct.json_encode().alias("raw_record"),
        )
        .sort("source_row")
    )
    valid = rows.filter(~pl.col("_row").is_in(per_row["index"].implode())).drop("_row")
    logger.warning(
        "%s: %d of %d rows fail contract v%d and go to quarantine",
        contract.table,
        rejected.height,
        df.height,
        contract.version,
    )
    return valid, rejected


def add_metadata(
    df: pl.DataFrame, loaded_at: datetime, load_date: date, source_uri: str
) -> pl.DataFrame:
    return df.with_columns(
        pl.lit(loaded_at, dtype=pl.Datetime("us", "UTC")).alias("_loaded_at"),
        pl.lit(load_date, dtype=pl.Date).alias("_load_date"),
        pl.lit(source_uri, dtype=pl.Utf8).alias("_source_file"),
    )


def quarantine_frame(rejected: pl.DataFrame, loaded_at: datetime, source_uri: str) -> pl.DataFrame:
    """Rejected rows with lineage. The load date is in the file path (a partition key)."""
    return rejected.with_columns(
        pl.lit(loaded_at, dtype=pl.Datetime("us", "UTC")).alias("_loaded_at"),
        pl.lit(source_uri, dtype=pl.Utf8).alias("_source_file"),
    )


def backfill_empty_quarantine(
    storage: Storage, contracts: dict[str, TableContract], load_date: date, loaded_at: datetime
) -> None:
    """Empty quarantine files for a loaded date that has none.

    Dates loaded before quarantine existed passed with zero rejects (a bad row
    failed the batch then), so an empty file is their true record. It also
    gives the BigQuery external table files to read once every date skips.
    """
    for table, contract in contracts.items():
        key = quarantine_key(table, load_date)
        if not storage.exists(key):
            source_uri = storage.uri(landing_key(load_date, contract.source_file))
            empty = quarantine_frame(empty_rejected(), loaded_at, source_uri)
            storage.write_bytes(key, to_parquet_bytes(empty))
            logger.info("Wrote empty quarantine file %s", storage.uri(key))


def conform_to_bronze(df: pl.DataFrame, bronze_columns: list[str]) -> pl.DataFrame:
    """Every column the bronze table has, in its order. Columns this batch's
    contract version lacks (added later, or renamed since) are null."""
    missing = [name for name in bronze_columns if name not in df.columns]
    return df.with_columns(pl.lit(None, dtype=pl.Utf8).alias(name) for name in missing).select(
        bronze_columns
    )


def to_parquet_bytes(df: pl.DataFrame) -> bytes:
    buffer = io.BytesIO()
    df.write_parquet(buffer)
    return buffer.getvalue()


def _ingest_table(
    storage: Storage,
    warehouse: BigQueryWarehouse | None,
    contract: TableContract,
    bronze_columns: list[str],
    load_date: date,
    loaded_at: datetime,
) -> TableLoad:
    source_key = landing_key(load_date, contract.source_file)
    raw = read_landing_csv(storage.read_bytes(source_key))
    df, rejected = split_valid(raw, contract)
    # Written even when empty, so a rerun replaces an earlier batch's rejects.
    storage.write_bytes(
        quarantine_key(contract.table, load_date),
        to_parquet_bytes(quarantine_frame(rejected, loaded_at, storage.uri(source_key))),
    )
    df = conform_to_bronze(df, bronze_columns)
    df = add_metadata(df, loaded_at, load_date, storage.uri(source_key))

    parquet_key = bronze_key(contract.table, load_date)
    storage.write_bytes(parquet_key, to_parquet_bytes(df))
    logger.info("Wrote %d rows to %s", df.height, storage.uri(parquet_key))

    result = TableLoad(rows_in=raw.height, rows_out=df.height, rows_quarantined=rejected.height)
    if warehouse is None:
        return result
    loaded = warehouse.load_partition(
        contract, bronze_schema(bronze_columns), storage.uri(parquet_key), load_date
    )
    if loaded != df.height:
        raise RuntimeError(f"{contract.table}: wrote {df.height} rows but BigQuery loaded {loaded}")
    return result


def sync_bronze_schemas(warehouse: BigQueryWarehouse, registry: ContractRegistry) -> None:
    """Add any column a contract version introduced to its bronze table, before
    loading or skipping, so silver can select it even before a batch fills it."""
    for table, versions in registry.versions.items():
        warehouse.ensure_table(versions[-1], bronze_schema(registry.bronze_columns(table)))


def ingest(
    storage: Storage,
    warehouse: BigQueryWarehouse | None,
    registry: ContractRegistry,
    load_date: date,
    force: bool = False,
    run_log: RunLog | None = None,
) -> IngestResult:
    """Load one date, each table validated against the contract in force on that date.

    Every table gets a pipeline_runs row (loaded, skipped, or failed), written
    even when the load fails, before the error propagates.
    """
    run_id = run_log.run_id if run_log else ""
    records: list[RunRecord] = []
    started_at = datetime.now(UTC)
    try:
        return _ingest_date(storage, warehouse, registry, load_date, force, run_id, records)
    except Exception as exc:
        if not any(r.status == "failed" for r in records):
            # Failed before any table started (missing files, no contract).
            records.append(
                RunRecord(
                    run_id,
                    "*",
                    load_date,
                    started_at,
                    datetime.now(UTC),
                    "failed",
                    message=str(exc),
                )
            )
        raise
    finally:
        if run_log:
            run_log.write(records)


def _ingest_date(
    storage: Storage,
    warehouse: BigQueryWarehouse | None,
    registry: ContractRegistry,
    load_date: date,
    force: bool,
    run_id: str,
    records: list[RunRecord],
) -> IngestResult:
    started_at = datetime.now(UTC)
    contracts = registry.for_date(load_date)
    if warehouse is not None:
        sync_bronze_schemas(warehouse, registry)
        warehouse.ensure_quarantine_table()
    missing = [
        c.source_file
        for c in contracts.values()
        if not storage.exists(landing_key(load_date, c.source_file))
    ]
    if missing:
        raise FileNotFoundError(f"No landing files for {load_date}: {missing}")

    landing_md5s = {
        table: storage.md5(landing_key(load_date, c.source_file)) for table, c in contracts.items()
    }
    versions = {table: c.version for table, c in contracts.items()}
    manifest = read_manifest(storage, load_date)
    if manifest and manifest.matches(landing_md5s, versions) and not force:
        logger.info("Load date %s already loaded from identical files, skipping.", load_date)
        loaded_at = datetime.fromisoformat(manifest.loaded_at) if manifest.loaded_at else started_at
        backfill_empty_quarantine(storage, contracts, load_date, loaded_at)
        ended_at = datetime.now(UTC)
        records.extend(
            RunRecord(
                run_id,
                t,
                load_date,
                started_at,
                ended_at,
                "skipped",
                rows_out=manifest.tables[t].rows,
                rows_quarantined=manifest.tables[t].rows_quarantined,
            )
            for t in contracts
        )
        return IngestResult(
            skipped=True,
            rows={t: manifest.tables[t].rows for t in contracts},
            quarantined={t: manifest.tables[t].rows_quarantined for t in contracts},
        )

    manifest = manifest or Manifest(load_date=load_date.isoformat())
    loaded_at = datetime.now(UTC)
    rows: dict[str, int] = {}
    quarantined: dict[str, int] = {}
    for table, contract in contracts.items():
        bronze_columns = registry.bronze_columns(table)
        table_started_at = datetime.now(UTC)
        try:
            load = _ingest_table(storage, warehouse, contract, bronze_columns, load_date, loaded_at)
        except Exception as exc:
            records.append(
                RunRecord(
                    run_id,
                    table,
                    load_date,
                    table_started_at,
                    datetime.now(UTC),
                    "failed",
                    message=str(exc),
                )
            )
            raise
        records.append(
            RunRecord(
                run_id,
                table,
                load_date,
                table_started_at,
                datetime.now(UTC),
                "loaded",
                rows_in=load.rows_in,
                rows_out=load.rows_out,
                rows_quarantined=load.rows_quarantined,
            )
        )
        rows[table] = load.rows_out
        quarantined[table] = load.rows_quarantined
        manifest.tables[table] = TableEntry(
            landing_md5=landing_md5s[table],
            rows=load.rows_out,
            contract_version=contract.version,
            rows_quarantined=load.rows_quarantined,
        )

    manifest.loaded_at = loaded_at.isoformat()
    write_manifest(storage, load_date, manifest)
    return IngestResult(skipped=False, rows=rows, quarantined=quarantined)


def ingest_landed(
    storage: Storage,
    warehouse: BigQueryWarehouse | None,
    registry: ContractRegistry,
    force: bool = False,
    run_log: RunLog | None = None,
) -> dict[date, IngestResult]:
    """Ingest every landed date, oldest first. The scheduled run: unchanged dates skip."""
    dates = landed_dates(storage)
    if not dates:
        logger.info("No load dates in landing, nothing to ingest.")
    return {d: ingest(storage, warehouse, registry, d, force=force, run_log=run_log) for d in dates}
