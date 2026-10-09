"""Environment settings and the lake layout shared by every sink.

No project id lives in code: it comes from OLIST_PROJECT_ID, or from the
active Google credentials when that is unset.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ingestion.storage import Storage

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_RAW_DIR = REPO_ROOT / "data" / "raw"
LOCAL_LAKE_DIR = REPO_ROOT / "data"
LOCAL_DUCKDB_PATH = REPO_ROOT / "data" / "warehouse.duckdb"
# pipeline_runs rows from local ingestion; dbt copies them into DuckDB.
LOCAL_RUNS_DIR = REPO_ROOT / "data" / "_runs"
DBT_PROJECT_DIR = REPO_ROOT / "dbt"
DEFAULT_REGION = "us-central1"


class Env(StrEnum):
    """Target environment. Dev writes to `dev_` datasets and the dev bucket."""

    DEV = "dev"
    PROD = "prod"


class Sink(StrEnum):
    """Where the lake lives: a GCS bucket or the local data/ folder."""

    GCS = "gcs"
    LOCAL = "local"


@dataclass(frozen=True)
class GcpSettings:
    """GCP names for one environment.

    Attributes:
        project_id: GCP project id.
        env: Target environment.
        region: Region of the BigQuery datasets and jobs.
    """

    project_id: str
    env: Env
    region: str = DEFAULT_REGION

    @property
    def lake_bucket(self) -> str:
        """Name of the environment's lake bucket."""
        return f"{self.project_id}-lake-{self.env}"

    def dataset(self, layer: str) -> str:
        """Map a layer to its dataset in this environment.

        Args:
            layer: Layer name, such as `bronze` or `ops`.

        Returns:
            The layer name in prod, `dev_<layer>` in dev.
        """
        return layer if self.env is Env.PROD else f"dev_{layer}"

    @property
    def bronze_dataset(self) -> str:
        """Bronze dataset name in this environment."""
        return self.dataset("bronze")


def resolve_project_id() -> str:
    """Find the GCP project id without hardcoding it.

    Returns:
        `OLIST_PROJECT_ID` if set, else the default project of the active
        Google credentials.

    Raises:
        RuntimeError: If neither gives a project id.
    """
    project_id = os.environ.get("OLIST_PROJECT_ID")
    if project_id:
        return project_id
    import google.auth

    _, project_id = google.auth.default()
    if not project_id:
        raise RuntimeError("Set OLIST_PROJECT_ID or configure a default project for gcloud.")
    return project_id


def gcp_settings(env: Env) -> GcpSettings:
    """Build the GCP settings for an environment.

    Args:
        env: Target environment.

    Returns:
        Settings with the resolved project id and `OLIST_REGION` (default
        us-central1).
    """
    return GcpSettings(
        project_id=resolve_project_id(),
        env=env,
        region=os.environ.get("OLIST_REGION", DEFAULT_REGION),
    )


# Lake layout: identical keys in the GCS bucket and in the local data/ folder.
def _partition(load_date: date) -> str:
    return f"load_date={load_date.isoformat()}"


def landing_key(load_date: date, source_file: str) -> str:
    """Build the landing key of a raw CSV.

    Args:
        load_date: The batch's load date.
        source_file: CSV file name.

    Returns:
        `landing/load_date=D/<source_file>`.
    """
    return f"landing/{_partition(load_date)}/{source_file}"


def landing_prefix(load_date: date) -> str:
    """Build the landing prefix that holds one batch's CSVs.

    Args:
        load_date: The batch's load date.

    Returns:
        `landing/load_date=D/`.
    """
    return f"landing/{_partition(load_date)}/"


_LANDING_DATE = re.compile(r"^landing/load_date=(\d{4}-\d{2}-\d{2})/")


def landed_dates(storage: Storage) -> list[date]:
    """List every load date that has files in landing.

    Args:
        storage: The lake to look in.

    Returns:
        Load dates, oldest first.
    """
    dates = {m.group(1) for k in storage.list_keys("landing/") if (m := _LANDING_DATE.match(k))}
    return sorted(date.fromisoformat(d) for d in dates)


def bronze_key(table: str, load_date: date) -> str:
    """Build the key of a bronze table's Parquet file for one batch.

    Args:
        table: Bronze table name.
        load_date: The batch's load date.

    Returns:
        `bronze/<table>/load_date=D/data.parquet`.
    """
    return f"bronze/{table}/{_partition(load_date)}/data.parquet"


def quarantine_key(table: str, load_date: date) -> str:
    """Build the key of a table's quarantined rows for one batch.

    The keys are Hive-style (`table_name=`, `load_date=`), so BigQuery reads
    them as partitions.

    Args:
        table: Bronze table name.
        load_date: The batch's load date.

    Returns:
        `quarantine/table_name=<table>/load_date=D/data.parquet`.
    """
    return f"quarantine/table_name={table}/{_partition(load_date)}/data.parquet"


def manifest_key(load_date: date) -> str:
    """Build the key of a batch's manifest.

    Args:
        load_date: The batch's load date.

    Returns:
        `_manifests/load_date=D.json`.
    """
    return f"_manifests/{_partition(load_date)}.json"
