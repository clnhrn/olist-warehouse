"""BigQuery bronze loads: one load job per table, replacing one _load_date partition."""

from __future__ import annotations

import logging
from datetime import date

from google.cloud import bigquery

from contracts.loader import QUARANTINE_COLUMNS, TableContract
from ingestion.config import GcpSettings

logger = logging.getLogger(__name__)

PARTITION_FIELD = "_load_date"


def _schema(columns: list[tuple[str, str]]) -> list[bigquery.SchemaField]:
    return [bigquery.SchemaField(name, bq_type) for name, bq_type in columns]


def _partitioning() -> bigquery.TimePartitioning:
    return bigquery.TimePartitioning(type_=bigquery.TimePartitioningType.DAY, field=PARTITION_FIELD)


class BigQueryWarehouse:
    """Bronze tables in BigQuery for one environment."""

    def __init__(self, settings: GcpSettings) -> None:
        """Create a BigQuery client for an environment.

        Args:
            settings: GCP settings of the target environment.
        """
        self.settings = settings
        self.client = bigquery.Client(project=settings.project_id, location=settings.region)

    def _table_id(self, contract: TableContract) -> str:
        s = self.settings
        return f"{s.project_id}.{s.bronze_dataset}.{contract.table}"

    def ensure_table(self, contract: TableContract, columns: list[tuple[str, str]]) -> None:
        """Create the bronze table, or append the columns it lacks.

        Only ever adds columns.

        Args:
            contract: The table's latest contract (for name and description).
            columns: `(name, BigQuery type)` pairs the table must have.
        """
        table = bigquery.Table(self._table_id(contract), schema=_schema(columns))
        table.time_partitioning = _partitioning()
        table.description = contract.description
        table = self.client.create_table(table, exists_ok=True)

        existing = {field.name for field in table.schema}
        added = [field for field in _schema(columns) if field.name not in existing]
        if added:
            table.schema = [*table.schema, *added]
            self.client.update_table(table, ["schema"])
            logger.info("Added columns %s to %s", [f.name for f in added], table.full_table_id)

    def ensure_quarantine_table(self) -> None:
        """Create the external table over the quarantine Parquet in the lake bucket.

        The files are the data: a rerun overwrites them, so the table can never
        hold duplicates, and there is nothing to load or keep in sync. Explicit
        schema; table_name and load_date come from the hive-style paths.
        """
        s = self.settings
        prefix = f"gs://{s.lake_bucket}/quarantine"
        config = bigquery.ExternalConfig(bigquery.ExternalSourceFormat.PARQUET)
        config.source_uris = [f"{prefix}/*"]
        hive = bigquery.HivePartitioningOptions()
        hive.mode = "CUSTOM"
        hive.source_uri_prefix = f"{prefix}/{{table_name:STRING}}/{{load_date:DATE}}"
        config.hive_partitioning = hive

        table = bigquery.Table(
            f"{s.project_id}.{s.bronze_dataset}.quarantine",
            schema=_schema(list(QUARANTINE_COLUMNS.items())),
        )
        table.external_data_configuration = config
        table.description = "Source rows that failed their contract, with the checks they failed."
        self.client.create_table(table, exists_ok=True)

    def load_partition(
        self,
        contract: TableContract,
        columns: list[tuple[str, str]],
        parquet_uri: str,
        load_date: date,
    ) -> int:
        """Replace exactly one partition with a Parquet file.

        Rerunning a load date never duplicates rows.

        Args:
            contract: The table's contract.
            columns: `(name, BigQuery type)` pairs of the bronze table.
            parquet_uri: `gs://` URI of the batch's Parquet file.
            load_date: The partition to replace.

        Returns:
            Rows loaded.
        """
        self.ensure_table(contract, columns)
        destination = f"{self._table_id(contract)}${load_date:%Y%m%d}"
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            schema=_schema(columns),
            time_partitioning=_partitioning(),
            schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
        )
        job = self.client.load_table_from_uri(parquet_uri, destination, job_config=job_config)
        job.result()
        logger.info("Loaded %s rows into %s", job.output_rows, destination)
        return int(job.output_rows or 0)

    def row_counts(self, layers: tuple[str, ...] = ("bronze",)) -> dict[str, int]:
        """Count rows per table from metadata (no query, nothing billed).

        Args:
            layers: Layers whose datasets to count.

        Returns:
            Row counts keyed `<layer>.<table>`.
        """
        counts: dict[str, int] = {}
        for layer in layers:
            dataset = f"{self.settings.project_id}.{self.settings.dataset(layer)}"
            for item in self.client.list_tables(dataset):
                if item.table_type == "TABLE":
                    table = self.client.get_table(item.reference)
                    counts[f"{layer}.{item.table_id}"] = int(table.num_rows or 0)
        return counts
