"""BigQuery bronze loads: one load job per table, replacing one _load_date partition."""

from __future__ import annotations

import logging
from datetime import date

from google.cloud import bigquery

from contracts.loader import TableContract
from ingestion.config import GcpSettings

logger = logging.getLogger(__name__)

PARTITION_FIELD = "_load_date"


def _schema(contract: TableContract) -> list[bigquery.SchemaField]:
    return [bigquery.SchemaField(name, bq_type) for name, bq_type in contract.bronze_columns()]


def _partitioning() -> bigquery.TimePartitioning:
    return bigquery.TimePartitioning(type_=bigquery.TimePartitioningType.DAY, field=PARTITION_FIELD)


class BigQueryWarehouse:
    def __init__(self, settings: GcpSettings) -> None:
        self.settings = settings
        self.client = bigquery.Client(project=settings.project_id, location=settings.region)

    def _table_id(self, contract: TableContract) -> str:
        s = self.settings
        return f"{s.project_id}.{s.bronze_dataset}.{contract.table}"

    def ensure_table(self, contract: TableContract) -> None:
        table = bigquery.Table(self._table_id(contract), schema=_schema(contract))
        table.time_partitioning = _partitioning()
        table.description = contract.description
        self.client.create_table(table, exists_ok=True)

    def load_partition(self, contract: TableContract, parquet_uri: str, load_date: date) -> int:
        """Replace exactly one partition, so rerunning a load date never duplicates rows."""
        self.ensure_table(contract)
        destination = f"{self._table_id(contract)}${load_date:%Y%m%d}"
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            schema=_schema(contract),
            time_partitioning=_partitioning(),
            schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
        )
        job = self.client.load_table_from_uri(parquet_uri, destination, job_config=job_config)
        job.result()
        logger.info("Loaded %s rows into %s", job.output_rows, destination)
        return int(job.output_rows or 0)
