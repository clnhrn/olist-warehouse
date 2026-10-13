"""The LLM's view of the warehouse: gold tables, columns, and metric definitions.

Built from the dbt YAML files of the gold models (the same descriptions dbt
docs show) and agent/semantic_layer.yml, so no dbt run or manifest is needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agent.config import GOLD_MODELS_DIR, SEMANTIC_LAYER_PATH


@dataclass(frozen=True)
class Column:
    """One gold column.

    Attributes:
        name: Column name.
        data_type: Type as declared in the dbt contract, such as `int64`.
        description: Meaning, from the dbt YAML.
    """

    name: str
    data_type: str
    description: str


@dataclass(frozen=True)
class Table:
    """One gold table.

    Attributes:
        name: Table name, such as `fact_orders`.
        description: Grain and meaning, from the dbt YAML.
        columns: Columns in YAML order.
    """

    name: str
    description: str
    columns: tuple[Column, ...]


@dataclass(frozen=True)
class Metric:
    """One semantic layer metric.

    Attributes:
        name: Metric id, such as `revenue`.
        label: Human name.
        description: What it means and when to use it.
        table: Gold table the expression runs over.
        expression: SQL aggregate expression.
        filter: Optional SQL condition the metric always applies.
    """

    name: str
    label: str
    description: str
    table: str
    expression: str
    filter: str | None = None


@dataclass(frozen=True)
class WarehouseSchema:
    """Gold tables and metrics.

    Attributes:
        tables: Tables by name.
        metrics: Metrics in file order.
    """

    tables: dict[str, Table]
    metrics: tuple[Metric, ...] = field(default=())

    @property
    def table_names(self) -> frozenset[str]:
        """Names of every gold table."""
        return frozenset(self.tables)


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def load_tables(models_dir: Path = GOLD_MODELS_DIR) -> dict[str, Table]:
    """Read the gold models from their dbt YAML files.

    Args:
        models_dir: Folder with the `_gold__*.yml` files.

    Returns:
        Tables by name, sorted by name.

    Raises:
        FileNotFoundError: If the folder has no gold YAML files.
    """
    paths = sorted(models_dir.glob("_gold__*.yml"))
    if not paths:
        raise FileNotFoundError(f"No gold model YAML files in {models_dir}")
    tables: dict[str, Table] = {}
    for path in paths:
        for model in yaml.safe_load(path.read_text()).get("models") or []:
            columns = tuple(
                Column(c["name"], c.get("data_type", "unknown"), _clean(c.get("description")))
                for c in model.get("columns", [])
            )
            tables[model["name"]] = Table(model["name"], _clean(model.get("description")), columns)
    return dict(sorted(tables.items()))


def load_metrics(path: Path = SEMANTIC_LAYER_PATH) -> tuple[Metric, ...]:
    """Read the semantic layer.

    Args:
        path: The semantic layer YAML file.

    Returns:
        Metrics in file order.
    """
    raw = yaml.safe_load(path.read_text())["metrics"]
    return tuple(
        Metric(
            name=m["name"],
            label=m["label"],
            description=_clean(m["description"]),
            table=m["table"],
            expression=m["expression"],
            filter=m.get("filter"),
        )
        for m in raw
    )


def load_schema(
    models_dir: Path = GOLD_MODELS_DIR, semantic_layer: Path = SEMANTIC_LAYER_PATH
) -> WarehouseSchema:
    """Load the gold tables and metrics, checking that metrics name real tables.

    Args:
        models_dir: Folder with the gold YAML files.
        semantic_layer: The semantic layer YAML file.

    Returns:
        The warehouse schema.

    Raises:
        ValueError: If a metric names a table that is not in gold.
    """
    tables = load_tables(models_dir)
    metrics = load_metrics(semantic_layer)
    unknown = sorted({m.table for m in metrics} - set(tables))
    if unknown:
        raise ValueError(f"Semantic layer names unknown gold tables: {unknown}")
    return WarehouseSchema(tables=tables, metrics=metrics)


def render_schema(schema: WarehouseSchema) -> str:
    """Render the schema as prompt text.

    The output depends only on the YAML files, never on the request, so it
    stays byte-identical across calls and the prompt cache keeps hitting.

    Args:
        schema: The warehouse schema.

    Returns:
        Markdown listing every table with its columns, then every metric.
    """
    lines = ["# Gold tables", ""]
    for table in schema.tables.values():
        lines += [f"## gold.{table.name}", table.description, ""]
        lines += [f"- {c.name} ({c.data_type}): {c.description}" for c in table.columns]
        lines.append("")
    lines += ["# Metrics (use these definitions exactly)", ""]
    for m in schema.metrics:
        where = f" WHERE {m.filter}" if m.filter else ""
        lines.append(f"- {m.name} ({m.label}): {m.description}")
        lines.append(f"  SQL: SELECT {m.expression} FROM gold.{m.table}{where}")
    return "\n".join(lines) + "\n"
