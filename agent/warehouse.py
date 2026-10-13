"""Run guarded SQL on DuckDB (local) or BigQuery (dev, prod), as the agent account.

BigQuery queries run as `sa-agent-<env>`, which can read its gold dataset and
nothing else. Every query is dry-run first and refused above the byte cap,
then run with `maximum_bytes_billed` set, so BigQuery itself enforces the cap.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

from agent.config import AgentSettings, Target

if TYPE_CHECKING:
    import google.auth.credentials

logger = logging.getLogger(__name__)

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
QUERY_TIMEOUT_SECONDS = 120


class WarehouseError(RuntimeError):
    """The warehouse rejected or failed the query. The message goes to the LLM."""


class QueryTooExpensiveError(WarehouseError):
    """The dry run estimated more bytes than the cap allows."""


@dataclass(frozen=True)
class QueryResult:
    """Rows from one query.

    Attributes:
        columns: Column names, in order.
        rows: Rows as tuples of JSON-friendly values.
        bytes_billed: BigQuery bytes billed; 0 on DuckDB.
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    bytes_billed: int = 0

    def records(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return rows as dicts keyed by column name.

        Args:
            limit: Return at most this many rows. None returns all.

        Returns:
            The rows as dicts.
        """
        rows = self.rows if limit is None else self.rows[:limit]
        return [dict(zip(self.columns, row, strict=True)) for row in rows]


def _plain(value: Any) -> Any:
    """Convert warehouse values to JSON-friendly ones."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    return value


class Warehouse(Protocol):
    """Something that runs guarded SQL."""

    def run(self, sql: str) -> QueryResult:
        """Run a query.

        Args:
            sql: SQL that passed `agent.guard.guard_sql`.

        Returns:
            The query's rows.

        Raises:
            WarehouseError: If the query fails or is refused.
        """
        ...


class DuckDBWarehouse:
    """Local DuckDB file, read-only, with file system access disabled."""

    def __init__(self, settings: AgentSettings) -> None:
        """Open the DuckDB file.

        Args:
            settings: Agent settings with `duckdb_path`.

        Raises:
            FileNotFoundError: If the DuckDB file does not exist.
        """
        import duckdb

        if not settings.duckdb_path.exists():
            raise FileNotFoundError(
                f"{settings.duckdb_path} not found; build it with dbt build --target local."
            )
        # read_only: no writes. enable_external_access=False: no reading
        # files, no extensions, and it cannot be switched back on.
        self._duckdb = duckdb
        self.connection = duckdb.connect(
            str(settings.duckdb_path), read_only=True, config={"enable_external_access": False}
        )

    def run(self, sql: str) -> QueryResult:
        """See `Warehouse.run`."""
        try:
            relation = self.connection.sql(sql)
            columns = tuple(relation.columns)
            rows = tuple(tuple(_plain(v) for v in row) for row in relation.fetchall())
        except self._duckdb.Error as error:
            raise WarehouseError(str(error)) from error
        return QueryResult(columns=columns, rows=rows)


def gcp_credentials(settings: AgentSettings) -> google.auth.credentials.Credentials:
    """Get credentials for the agent account.

    Args:
        settings: Agent settings. With `service_account` set, the ambient
            credentials (your gcloud login, or CI's account) impersonate it.

    Returns:
        Credentials for BigQuery and Secret Manager calls.
    """
    import google.auth
    from google.auth import impersonated_credentials

    source, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
    if not settings.service_account:
        return source
    return impersonated_credentials.Credentials(
        source_credentials=source,
        target_principal=settings.service_account,
        target_scopes=[CLOUD_PLATFORM_SCOPE],
    )


class BigQueryWarehouse:
    """BigQuery, as the agent account, with a dry run and a byte cap."""

    def __init__(
        self,
        settings: AgentSettings,
        credentials: google.auth.credentials.Credentials | None = None,
    ) -> None:
        """Create a BigQuery client for the agent account.

        Args:
            settings: Agent settings for the dev or prod target.
            credentials: Agent credentials; by default from `gcp_credentials`.
        """
        from google.cloud import bigquery

        self._bigquery = bigquery
        self.settings = settings
        self.client = bigquery.Client(
            project=settings.project_id,
            location=settings.region,
            credentials=credentials or gcp_credentials(settings),
        )

    def _job_config(self, *, dry_run: bool) -> Any:
        return self._bigquery.QueryJobConfig(
            dry_run=dry_run,
            use_query_cache=not dry_run,
            maximum_bytes_billed=self.settings.max_bytes_billed,
            labels={"component": "agent", "target": str(self.settings.target)},
        )

    def _error_message(self, error: Exception) -> str:
        """BigQuery's own message, without the request URL or the project id.

        The message goes to the LLM and the query log, so it must not carry
        the project id.
        """
        details = getattr(error, "errors", None) or []
        message = (details[0].get("message") if details else None) or str(error)
        return message.replace(self.settings.project_id or "\0", "<project>")

    def run(self, sql: str) -> QueryResult:
        """See `Warehouse.run`. Raises `QueryTooExpensiveError` above the cap."""
        from google.api_core import exceptions

        try:
            dry = self.client.query(sql, job_config=self._job_config(dry_run=True))
            estimate = dry.total_bytes_processed or 0
            if estimate > self.settings.max_bytes_billed:
                raise QueryTooExpensiveError(
                    f"Query would scan {estimate:,} bytes, over the cap of "
                    f"{self.settings.max_bytes_billed:,}. Select fewer columns or filter "
                    "on purchased_at, the partitioning column."
                )
            job = self.client.query(sql, job_config=self._job_config(dry_run=False))
            result = job.result(timeout=QUERY_TIMEOUT_SECONDS)
            columns = tuple(field.name for field in result.schema)
            rows = tuple(tuple(_plain(v) for v in row.values()) for row in result)
        except exceptions.GoogleAPICallError as error:
            raise WarehouseError(self._error_message(error)) from error
        bytes_billed = job.total_bytes_billed or 0
        logger.info("Query billed %d bytes, returned %d rows", bytes_billed, len(rows))
        return QueryResult(columns=columns, rows=rows, bytes_billed=bytes_billed)


def open_warehouse(settings: AgentSettings) -> Warehouse:
    """Open the warehouse for a target.

    Args:
        settings: Agent settings.

    Returns:
        DuckDB for the local target, BigQuery otherwise.
    """
    if settings.target is Target.LOCAL:
        return DuckDBWarehouse(settings)
    return BigQueryWarehouse(settings)
