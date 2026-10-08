"""Landing CSV -> validate against contract -> add lineage -> bronze Parquet (-> BigQuery).

Rerunning a load date overwrites that date's Parquet and replaces that date's
BigQuery partition, so retries never create duplicates.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pandera.errors as pa_errors
import polars as pl

from contracts.loader import DEFAULT_VERSION, TableContract, pandera_schema
from ingestion.config import bronze_key, landing_key
from ingestion.manifest import Manifest, TableEntry, read_manifest, write_manifest
from ingestion.storage import Storage
from ingestion.warehouse import BigQueryWarehouse

logger = logging.getLogger(__name__)

MAX_FAILURES_LOGGED = 20


class ContractViolationError(RuntimeError):
    pass


@dataclass(frozen=True)
class IngestResult:
    skipped: bool
    rows: dict[str, int]


def read_landing_csv(data: bytes) -> pl.DataFrame:
    """Every column as Utf8, so bronze stays as-is (zip codes keep leading zeros).

    Reads from bytes because landing may be GCS; the largest file is ~60 MB.
    Polars strips a UTF-8 BOM from the header.
    """
    return pl.read_csv(io.BytesIO(data), infer_schema=False)


def validate(df: pl.DataFrame, contract: TableContract) -> None:
    try:
        pandera_schema(contract).validate(df, lazy=True)
    except pa_errors.SchemaErrors as exc:
        failures = exc.failure_cases
        logger.error(
            "Contract violations in %s (%d failure cases, first %d):\n%s",
            contract.table,
            failures.height,
            MAX_FAILURES_LOGGED,
            failures.head(MAX_FAILURES_LOGGED),
        )
        raise ContractViolationError(
            f"{contract.table} violates contract v{contract.version}"
        ) from exc


def add_metadata(
    df: pl.DataFrame, loaded_at: datetime, load_date: date, source_uri: str
) -> pl.DataFrame:
    return df.with_columns(
        pl.lit(loaded_at, dtype=pl.Datetime("us", "UTC")).alias("_loaded_at"),
        pl.lit(load_date, dtype=pl.Date).alias("_load_date"),
        pl.lit(source_uri, dtype=pl.Utf8).alias("_source_file"),
    )


def to_parquet_bytes(df: pl.DataFrame) -> bytes:
    buffer = io.BytesIO()
    df.write_parquet(buffer)
    return buffer.getvalue()


def _ingest_table(
    storage: Storage,
    warehouse: BigQueryWarehouse | None,
    contract: TableContract,
    load_date: date,
    loaded_at: datetime,
) -> int:
    source_key = landing_key(load_date, contract.source_file)
    df = read_landing_csv(storage.read_bytes(source_key))
    validate(df, contract)
    df = add_metadata(df, loaded_at, load_date, storage.uri(source_key))

    parquet_key = bronze_key(contract.table, load_date)
    storage.write_bytes(parquet_key, to_parquet_bytes(df))
    logger.info("Wrote %d rows to %s", df.height, storage.uri(parquet_key))

    if warehouse is None:
        return df.height
    loaded = warehouse.load_partition(contract, storage.uri(parquet_key), load_date)
    if loaded != df.height:
        raise RuntimeError(f"{contract.table}: wrote {df.height} rows but BigQuery loaded {loaded}")
    return loaded


def ingest(
    storage: Storage,
    warehouse: BigQueryWarehouse | None,
    contracts: dict[str, TableContract],
    load_date: date,
    force: bool = False,
) -> IngestResult:
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
    manifest = read_manifest(storage, load_date)
    if manifest and manifest.matches(landing_md5s) and not force:
        logger.info("Load date %s already loaded from identical files, skipping.", load_date)
        return IngestResult(skipped=True, rows={t: manifest.tables[t].rows for t in contracts})

    manifest = manifest or Manifest(
        load_date=load_date.isoformat(), contract_version=DEFAULT_VERSION
    )
    loaded_at = datetime.now(UTC)
    rows: dict[str, int] = {}
    for table, contract in contracts.items():
        rows[table] = _ingest_table(storage, warehouse, contract, load_date, loaded_at)
        manifest.tables[table] = TableEntry(landing_md5=landing_md5s[table], rows=rows[table])

    manifest.loaded_at = loaded_at.isoformat()
    write_manifest(storage, load_date, manifest)
    return IngestResult(skipped=False, rows=rows)
