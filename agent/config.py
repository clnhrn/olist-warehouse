"""Agent settings: which warehouse to query, as whom, and within which limits.

Targets:
    local: DuckDB `data/warehouse.duckdb` (read-only), schema `dev_gold`. Free.
    dev: BigQuery `dev_gold`, as `sa-agent-dev` (impersonated).
    prod: BigQuery `gold`, as `sa-agent-prod`. Only inside Cloud Run, where the
        service runs as that account; local sessions never query prod.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ingestion.config import DEFAULT_REGION, LOCAL_DUCKDB_PATH, REPO_ROOT, resolve_project_id

AGENT_DIR = Path(__file__).resolve().parent
GOLD_MODELS_DIR = REPO_ROOT / "dbt" / "models" / "gold"
SEMANTIC_LAYER_PATH = AGENT_DIR / "semantic_layer.yml"
LOCAL_AGENT_DIR = REPO_ROOT / "data" / "_agent"

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_MAX_BYTES_BILLED = 500_000_000  # 500 MB per query; gold is far smaller.
DEFAULT_ROW_LIMIT = 1000
DEFAULT_MAX_ATTEMPTS = 3
API_KEY_SECRET = "anthropic-api-key"


class Target(StrEnum):
    """Warehouse the agent queries."""

    LOCAL = "local"
    DEV = "dev"
    PROD = "prod"


@dataclass(frozen=True)
class AgentSettings:
    """Everything the agent needs to know about where and how it runs.

    Attributes:
        target: Warehouse to query.
        project_id: GCP project id. None for the local target.
        region: BigQuery location.
        service_account: Account to impersonate for BigQuery and Secret
            Manager. None means the ambient credentials (Cloud Run).
        model: Claude model id.
        max_bytes_billed: Per-query BigQuery byte cap.
        row_limit: Maximum rows a query may return.
        max_attempts: SQL attempts per question, retries included.
        duckdb_path: DuckDB file for the local target.
    """

    target: Target
    project_id: str | None = None
    region: str = DEFAULT_REGION
    service_account: str | None = None
    model: str = DEFAULT_MODEL
    max_bytes_billed: int = DEFAULT_MAX_BYTES_BILLED
    row_limit: int = DEFAULT_ROW_LIMIT
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    duckdb_path: Path = LOCAL_DUCKDB_PATH

    @property
    def gold_dataset(self) -> str:
        """Dataset (BigQuery) or schema (DuckDB) holding the gold tables."""
        return "gold" if self.target is Target.PROD else "dev_gold"

    @property
    def ops_dataset(self) -> str:
        """Dataset holding the `agent_queries` log table."""
        return "ops" if self.target is Target.PROD else "dev_ops"

    @property
    def dialect(self) -> str:
        """Dialect name of the target warehouse, as sqlglot spells it."""
        return "duckdb" if self.target is Target.LOCAL else "bigquery"


def running_on_cloud_run() -> bool:
    """Tell whether this process runs as a Cloud Run service.

    Returns:
        True when Cloud Run's `K_SERVICE` variable is set.
    """
    return bool(os.environ.get("K_SERVICE"))


def agent_settings(target: Target) -> AgentSettings:
    """Build the settings for a target, from defaults and `OLIST_AGENT_*` variables.

    Args:
        target: Warehouse to query.

    Returns:
        The settings. For dev, the account defaults to `sa-agent-dev` in the
        project; `OLIST_AGENT_SERVICE_ACCOUNT` overrides it.

    Raises:
        RuntimeError: If the prod target is requested outside Cloud Run.
    """
    env = os.environ
    limits = {
        "model": env.get("OLIST_AGENT_MODEL", DEFAULT_MODEL),
        "max_bytes_billed": int(env.get("OLIST_AGENT_MAX_BYTES", DEFAULT_MAX_BYTES_BILLED)),
        "row_limit": int(env.get("OLIST_AGENT_ROW_LIMIT", DEFAULT_ROW_LIMIT)),
        "max_attempts": int(env.get("OLIST_AGENT_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)),
    }
    if target is Target.LOCAL:
        duckdb_path = Path(env.get("OLIST_DUCKDB_PATH", LOCAL_DUCKDB_PATH))
        return AgentSettings(target=target, duckdb_path=duckdb_path, **limits)

    if target is Target.PROD and not running_on_cloud_run():
        raise RuntimeError("The prod target runs only in Cloud Run; use --target dev locally.")

    project_id = resolve_project_id()
    default_account = (
        f"sa-agent-dev@{project_id}.iam.gserviceaccount.com" if target is Target.DEV else None
    )
    return AgentSettings(
        target=target,
        project_id=project_id,
        region=env.get("OLIST_REGION", DEFAULT_REGION),
        service_account=env.get("OLIST_AGENT_SERVICE_ACCOUNT", default_account),
        **limits,
    )
