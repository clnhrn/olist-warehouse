"""Rerun check: run the pipeline twice for one load date, then compare row counts.

Each run force-ingests bronze and then runs `dbt build`, so one check covers
every layer. Counts are keyed "<layer>.<table>".
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

RowCounts = dict[str, int]
DBT_LAYERS = ("silver", "gold")


@dataclass(frozen=True)
class CountChange:
    table: str
    first: int | None
    second: int | None


def local_bronze_counts(lake_dir: Path) -> RowCounts:
    """Rows per bronze table across all load_date partitions of the local lake."""
    bronze = lake_dir / "bronze"
    if not bronze.is_dir():
        return {}
    counts: RowCounts = {}
    for table_dir in sorted(p for p in bronze.iterdir() if p.is_dir()):
        files = sorted(table_dir.glob("load_date=*/data.parquet"))
        if files:
            rows = pl.scan_parquet(files).select(pl.len()).collect().item()
            counts[f"bronze.{table_dir.name}"] = int(rows)
    return counts


def local_warehouse_counts(duckdb_path: Path, layers: tuple[str, ...] = DBT_LAYERS) -> RowCounts:
    """Rows per table in the local DuckDB warehouse, for the dev_<layer> schemas."""
    if not duckdb_path.is_file():
        return {}
    import duckdb  # dev dependency (with dbt-duckdb), not in the job image

    schemas = [f"dev_{layer}" for layer in layers]
    with duckdb.connect(str(duckdb_path), read_only=True) as con:
        tables = con.execute(
            "select table_schema, table_name from information_schema.tables"
            " where table_type = 'BASE TABLE' and list_contains(?, table_schema)",
            [schemas],
        ).fetchall()
        return {
            f"{schema.removeprefix('dev_')}.{name}": int(
                con.execute(f'select count(*) from "{schema}"."{name}"').fetchone()[0]
            )
            for schema, name in sorted(tables)
        }


def run_dbt_build(project_dir: Path, target: str) -> None:
    """Run `dbt build` from the project folder, where profiles.yml lives."""
    dbt = shutil.which("dbt")
    if dbt is None:
        raise FileNotFoundError("dbt not found. Run `uv sync` to install the dbt group.")
    logger.info("Running dbt build --target %s", target)
    subprocess.run([dbt, "build", "--target", target], cwd=project_dir, check=True)


def diff_counts(first: RowCounts, second: RowCounts) -> list[CountChange]:
    return [
        CountChange(table, first.get(table), second.get(table))
        for table in sorted(first.keys() | second.keys())
        if first.get(table) != second.get(table)
    ]


def check_rerun(run: Callable[[], None], count: Callable[[], RowCounts]) -> list[CountChange]:
    """Run twice and return every table whose row count changed (empty means idempotent)."""
    run()
    first = count()
    run()
    second = count()

    changes = diff_counts(first, second)
    for table in sorted(second.keys() - {c.table for c in changes}):
        logger.info("%-40s %10d rows, unchanged", table, second[table])
    for change in changes:
        logger.error("%-40s changed: %s -> %s", change.table, change.first, change.second)
    return changes
