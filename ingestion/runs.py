"""Audit log: one pipeline_runs row per table per load date, for every ingestion run.

The scheduled workflow passes its execution id as OLIST_RUN_ID, so ingestion
and dbt rows of one run share a run id. Rows go to <ops>.pipeline_runs in
BigQuery (a load job, which is free, unlike streaming inserts), or to
data/_runs/<run_id>.jsonl locally, where dbt copies them into DuckDB.
The log is append-only: every run adds rows, reruns included.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from google.cloud import bigquery

from ingestion.config import GcpSettings

logger = logging.getLogger(__name__)

# Same columns and types as the dbt hook's table (dbt/macros/pipeline_runs.sql).
RUN_COLUMNS: dict[str, str] = {
    "run_id": "STRING",
    "component": "STRING",
    "layer": "STRING",
    "object": "STRING",
    "load_date": "DATE",
    "started_at": "TIMESTAMP",
    "ended_at": "TIMESTAMP",
    "rows_in": "INTEGER",
    "rows_out": "INTEGER",
    "rows_quarantined": "INTEGER",
    "status": "STRING",
    "message": "STRING",
}
MAX_MESSAGE_LENGTH = 1000


def new_run_id() -> str:
    """Get the run id for this process.

    Returns:
        `OLIST_RUN_ID` (the workflow execution id) if set, else a new UUID.
    """
    return os.environ.get("OLIST_RUN_ID") or uuid.uuid4().hex


@dataclass(frozen=True)
class RunRecord:
    """One pipeline_runs row: the outcome for one table and load date.

    Attributes:
        run_id: Id shared by every row of one pipeline run.
        object: Table the row is about.
        load_date: The batch's load date.
        started_at: When work on the table started.
        ended_at: When it ended.
        status: Outcome: `loaded`, `skipped` or `failed`.
        rows_in: Rows read from the source.
        rows_out: Rows written to the layer.
        rows_quarantined: Rows that failed their contract.
        message: Error or skip reason, truncated when written.
        component: Pipeline component that wrote the row.
        layer: Layer the row is about.
    """

    run_id: str
    object: str
    load_date: date
    started_at: datetime
    ended_at: datetime
    status: str
    rows_in: int | None = None
    rows_out: int | None = None
    rows_quarantined: int | None = None
    message: str | None = None
    component: str = "ingestion"
    layer: str = "bronze"

    def to_json(self) -> dict[str, object]:
        """Convert the record to a JSON-ready row.

        Returns:
            The fields, with dates as ISO strings and the message truncated to
            `MAX_MESSAGE_LENGTH`.
        """
        row = asdict(self)
        for key, value in row.items():
            if isinstance(value, date | datetime):
                row[key] = value.isoformat()
        if row["message"]:
            row["message"] = row["message"][:MAX_MESSAGE_LENGTH]
        return row


class RunLog(Protocol):
    """Append-only sink for pipeline_runs rows.

    Attributes:
        run_id: Id of the run whose rows this log writes.
    """

    run_id: str

    def write(self, records: list[RunRecord]) -> None:
        """Append rows. Does nothing when `records` is empty.

        Args:
            records: Rows to append.
        """
        ...


class LocalRunLog:
    """`RunLog` that appends to `<runs_dir>/<run_id>.jsonl`."""

    def __init__(self, runs_dir: Path, run_id: str) -> None:
        """Create a local run log.

        Args:
            runs_dir: Folder for the JSON Lines files.
            run_id: Id of the current run.
        """
        self.path = runs_dir / f"{run_id}.jsonl"
        self.run_id = run_id

    def write(self, records: list[RunRecord]) -> None:
        """See `RunLog.write`."""
        if not records:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as out:
            out.writelines(json.dumps(r.to_json()) + "\n" for r in records)


class BigQueryRunLog:
    """`RunLog` that appends to `<ops>.pipeline_runs` with a free load job."""

    def __init__(self, settings: GcpSettings, run_id: str) -> None:
        """Create a BigQuery run log.

        Args:
            settings: GCP settings of the target environment.
            run_id: Id of the current run.
        """
        self.client = bigquery.Client(project=settings.project_id, location=settings.region)
        self.table_id = f"{settings.project_id}.{settings.dataset('ops')}.pipeline_runs"
        self.run_id = run_id

    def write(self, records: list[RunRecord]) -> None:
        """See `RunLog.write`. Creates the table on first use."""
        if not records:
            return
        job_config = bigquery.LoadJobConfig(
            schema=[bigquery.SchemaField(name, kind) for name, kind in RUN_COLUMNS.items()],
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        )
        rows = [r.to_json() for r in records]
        self.client.load_table_from_json(rows, self.table_id, job_config=job_config).result()
        logger.info("Logged %d rows to %s (run %s)", len(rows), self.table_id, self.run_id)
