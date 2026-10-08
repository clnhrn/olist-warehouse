"""Environment settings and the lake layout shared by every sink.

No project id lives in code: it comes from OLIST_PROJECT_ID, or from the
active Google credentials when that is unset.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_RAW_DIR = REPO_ROOT / "data" / "raw"
LOCAL_LAKE_DIR = REPO_ROOT / "data"
DEFAULT_REGION = "us-central1"


class Env(StrEnum):
    DEV = "dev"
    PROD = "prod"


class Sink(StrEnum):
    GCS = "gcs"
    LOCAL = "local"


@dataclass(frozen=True)
class GcpSettings:
    project_id: str
    env: Env
    region: str = DEFAULT_REGION

    @property
    def lake_bucket(self) -> str:
        return f"{self.project_id}-lake-{self.env}"

    @property
    def bronze_dataset(self) -> str:
        return "bronze" if self.env is Env.PROD else "dev_bronze"


def resolve_project_id() -> str:
    project_id = os.environ.get("OLIST_PROJECT_ID")
    if project_id:
        return project_id
    import google.auth

    _, project_id = google.auth.default()
    if not project_id:
        raise RuntimeError("Set OLIST_PROJECT_ID or configure a default project for gcloud.")
    return project_id


def gcp_settings(env: Env) -> GcpSettings:
    return GcpSettings(
        project_id=resolve_project_id(),
        env=env,
        region=os.environ.get("OLIST_REGION", DEFAULT_REGION),
    )


# Lake layout: identical keys in the GCS bucket and in the local data/ folder.
def _partition(load_date: date) -> str:
    return f"load_date={load_date.isoformat()}"


def landing_key(load_date: date, source_file: str) -> str:
    return f"landing/{_partition(load_date)}/{source_file}"


def landing_prefix(load_date: date) -> str:
    return f"landing/{_partition(load_date)}/"


def bronze_key(table: str, load_date: date) -> str:
    return f"bronze/{table}/{_partition(load_date)}/data.parquet"


def manifest_key(load_date: date) -> str:
    return f"_manifests/{_partition(load_date)}.json"
