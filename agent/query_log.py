"""Log every question: the SQL, cost, and outcome, for review.

One row per question goes to `<ops>.agent_queries` (Terraform owns the table
and its schema; the agent account may append to that table only) with a load
job, which is free, or to `data/_agent/queries.jsonl` for the local target.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from agent.config import LOCAL_AGENT_DIR, AgentSettings, Target
from agent.llm import AgentAnswer

if TYPE_CHECKING:
    import google.auth.credentials

logger = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 2000

# Same columns and types as google_bigquery_table.agent_queries (terraform/agent.tf).
LOG_COLUMNS: dict[str, str] = {
    "query_id": "STRING",
    "session_id": "STRING",
    "asked_at": "TIMESTAMP",
    "target": "STRING",
    "question": "STRING",
    "sql": "STRING",
    "status": "STRING",
    "error": "STRING",
    "bytes_billed": "INTEGER",
    "rows_returned": "INTEGER",
    "latency_ms": "INTEGER",
    "model": "STRING",
    "input_tokens": "INTEGER",
    "output_tokens": "INTEGER",
    "attempts": "INTEGER",
}
# Columns the table declares REQUIRED. A load job's schema must match the
# table's modes exactly, or BigQuery rejects it.
REQUIRED_COLUMNS = frozenset({"query_id", "asked_at", "target", "question", "status"})


def log_row(
    answer: AgentAnswer, target: Target, session_id: str | None, asked_at: datetime
) -> dict[str, Any]:
    """Build the log row for one answered question.

    Args:
        answer: The agent's answer.
        target: Warehouse that was queried.
        session_id: UI or CLI session id.
        asked_at: When the question was asked.

    Returns:
        A JSON-ready row with exactly the `LOG_COLUMNS` keys. Input tokens
        include cached ones; long text is truncated.
    """
    usage = answer.usage
    error = answer.error[:MAX_TEXT_LENGTH] if answer.error else None
    return {
        "query_id": answer.query_id,
        "session_id": session_id,
        "asked_at": asked_at.astimezone(UTC).isoformat(),
        "target": str(target),
        "question": answer.question[:MAX_TEXT_LENGTH],
        "sql": answer.sql,
        "status": answer.status,
        "error": error,
        "bytes_billed": answer.bytes_billed,
        "rows_returned": len(answer.result.rows) if answer.result else None,
        "latency_ms": answer.latency_ms,
        "model": answer.model,
        "input_tokens": usage.input_tokens
        + usage.cache_creation_input_tokens
        + usage.cache_read_input_tokens,
        "output_tokens": usage.output_tokens,
        "attempts": len(answer.attempts),
    }


class QueryLog(Protocol):
    """Append-only sink for agent_queries rows."""

    def write(self, row: dict[str, Any]) -> None:
        """Append one row.

        Args:
            row: A row from `log_row`.
        """
        ...


class LocalQueryLog:
    """`QueryLog` that appends to a JSON Lines file."""

    def __init__(self, path: Path = LOCAL_AGENT_DIR / "queries.jsonl") -> None:
        """Create a local log.

        Args:
            path: The JSON Lines file.
        """
        self.path = path

    def write(self, row: dict[str, Any]) -> None:
        """See `QueryLog.write`."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as out:
            out.write(json.dumps(row) + "\n")


class BigQueryQueryLog:
    """`QueryLog` that appends to `<ops>.agent_queries` with a free load job."""

    def __init__(
        self, settings: AgentSettings, credentials: google.auth.credentials.Credentials
    ) -> None:
        """Create a BigQuery log.

        Args:
            settings: Agent settings for the dev or prod target.
            credentials: Agent account credentials.
        """
        from google.cloud import bigquery

        self._bigquery = bigquery
        self.client = bigquery.Client(
            project=settings.project_id, location=settings.region, credentials=credentials
        )
        self.project_id = settings.project_id or ""
        self.ops_table = f"{settings.ops_dataset}.agent_queries"
        self.table_id = f"{settings.project_id}.{self.ops_table}"

    def write(self, row: dict[str, Any]) -> None:
        """See `QueryLog.write`. The table must exist (Terraform creates it)."""
        from google.api_core import exceptions

        bigquery = self._bigquery
        schema = [
            bigquery.SchemaField(name, kind, mode="REQUIRED" if name in REQUIRED_COLUMNS else None)
            for name, kind in LOG_COLUMNS.items()
        ]
        job_config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            create_disposition=bigquery.CreateDisposition.CREATE_NEVER,
        )
        try:
            job = self.client.load_table_from_json([row], self.table_id, job_config=job_config)
            job.result()
        except exceptions.GoogleAPICallError as error:
            # BigQuery's message names the project; keep it out of the logs.
            details = getattr(error, "errors", None) or [{}]
            message = details[0].get("message") or type(error).__name__
            raise RuntimeError(message.replace(self.project_id, "<project>")) from None
        logger.info("Logged question %s to %s", row["query_id"], self.ops_table)


def record(log: QueryLog, row: dict[str, Any]) -> None:
    """Write a row, logging instead of raising if the write fails.

    A failed log write must not lose the user's answer.

    Args:
        log: Where to write.
        row: The row.
    """
    try:
        log.write(row)
    except Exception as error:  # Logging must never lose the answer.
        # No traceback: client tracebacks carry request URLs with the project id.
        logger.error("Could not log question %s: %s", row.get("query_id"), error)


def now() -> datetime:
    """Current time in UTC.

    Returns:
        A timezone-aware datetime.
    """
    return datetime.now(UTC)
