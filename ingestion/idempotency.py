"""Rerun check: run the pipeline twice for one load date, then compare row counts.

Counts are keyed "<layer>.<table>". Bronze is counted now; Phases 3 and 4 add
silver and gold counts to the same check, so one run covers every layer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

RowCounts = dict[str, int]


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
