"""Load data contracts and build Pandera and BigQuery schemas from them.

Bronze keeps every source column as STRING. The contract's `parse` kind says
what each value must look like, so validation fails loudly on bad data without
changing the stored value. Silver does the casting.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandera.polars as pa
import polars as pl
import yaml
from pandera.api.polars.types import PolarsData

CONTRACTS_DIR = Path(__file__).parent
DEFAULT_VERSION = "v1"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

# Lineage columns added to every bronze table, with their BigQuery types.
METADATA_COLUMNS: dict[str, str] = {
    "_loaded_at": "TIMESTAMP",
    "_load_date": "DATE",
    "_source_file": "STRING",
}

PARSE_PATTERNS: dict[str, str] = {
    "id32": r"^[0-9a-f]{32}$",
    "zip": r"^\d{5}$",
    "int": r"^-?\d+$",
    "decimal": r"^-?\d+(\.\d+)?$",
    "timestamp": r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$",
    "state": r"^[A-Z]{2}$",
}
PARSE_KINDS = {*PARSE_PATTERNS, "text"}


@dataclass(frozen=True)
class ColumnContract:
    name: str
    parse: str
    nullable: bool


@dataclass(frozen=True)
class TableContract:
    version: int
    table: str
    source_file: str
    description: str
    primary_key: tuple[str, ...]
    columns: tuple[ColumnContract, ...]

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def bronze_columns(self) -> list[tuple[str, str]]:
        """(name, BigQuery type) for the bronze table: source columns then metadata."""
        return [(c.name, "STRING") for c in self.columns] + list(METADATA_COLUMNS.items())


def _parse_column(raw: dict) -> ColumnContract:
    column = ColumnContract(name=raw["name"], parse=raw["parse"], nullable=bool(raw["nullable"]))
    if column.parse not in PARSE_KINDS:
        raise ValueError(f"Column {column.name}: unknown parse kind {column.parse!r}")
    return column


def load_contract(path: Path) -> TableContract:
    raw = yaml.safe_load(path.read_text())
    return TableContract(
        version=int(raw["version"]),
        table=raw["table"],
        source_file=raw["source_file"],
        description=raw.get("description", ""),
        primary_key=tuple(raw.get("primary_key", [])),
        columns=tuple(_parse_column(c) for c in raw["columns"]),
    )


def load_contracts(version: str = DEFAULT_VERSION) -> dict[str, TableContract]:
    """All contracts for a version, keyed by bronze table name."""
    paths = sorted((CONTRACTS_DIR / version).glob("*.yaml"))
    if not paths:
        raise FileNotFoundError(f"No contracts found in {CONTRACTS_DIR / version}")
    contracts = [load_contract(p) for p in paths]
    return {c.table: c for c in contracts}


def _timestamp_parses(data: PolarsData) -> pl.LazyFrame:
    """Regex alone accepts 2017-13-45; this rejects values that are not real datetimes."""
    col = pl.col(data.key)
    parsed = col.str.to_datetime(TIMESTAMP_FORMAT, strict=False)
    return data.lazyframe.select(col.is_null() | parsed.is_not_null())


def _checks_for(column: ColumnContract) -> list[pa.Check]:
    if column.parse == "text":
        return []
    checks = [pa.Check.str_matches(PARSE_PATTERNS[column.parse], name=f"parse_{column.parse}")]
    if column.parse == "timestamp":
        checks.append(pa.Check(_timestamp_parses, name="valid_timestamp"))
    return checks


def pandera_schema(contract: TableContract) -> pa.DataFrameSchema:
    """Schema for the raw source frame: all Utf8, exact column set, parse checks."""
    return pa.DataFrameSchema(
        {
            c.name: pa.Column(pl.Utf8, checks=_checks_for(c), nullable=c.nullable)
            for c in contract.columns
        },
        name=contract.table,
        strict=True,
    )
