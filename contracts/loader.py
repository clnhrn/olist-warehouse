"""Load data contracts and build Pandera and BigQuery schemas from them.

Bronze keeps every source column as STRING. The contract's `parse` kind says
what each value must look like, so validation fails loudly on bad data without
changing the stored value. Silver does the casting.

Contracts are versioned by folder (v1/, v2/, ...). A version folder holds only
the tables that changed in it, and each contract says from which load date it
applies, so every load date is validated against the version in force then
(reruns of old batches keep using the old version). Loading the contracts
checks each table's history and refuses a breaking change.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import pandera.polars as pa
import polars as pl
import yaml
from pandera.api.polars.types import PolarsData

CONTRACTS_DIR = Path(__file__).parent
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
# A contract without effective_from (every v1) applies from the first batch.
FIRST_BATCH = date.min

# Lineage columns added to every bronze table, with their BigQuery types.
METADATA_COLUMNS: dict[str, str] = {
    "_loaded_at": "TIMESTAMP",
    "_load_date": "DATE",
    "_source_file": "STRING",
}

# Rows that failed their contract, one table for every source table. The
# table_name and load_date partition keys come from the file path.
QUARANTINE_COLUMNS: dict[str, str] = {
    "source_row": "INTEGER",
    "failed_checks": "STRING",
    "raw_record": "STRING",
    "_loaded_at": "TIMESTAMP",
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


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class ColumnContract:
    name: str
    parse: str
    nullable: bool
    # The column's name in the previous version, if the source renamed it.
    renamed_from: str | None = None


@dataclass(frozen=True)
class TableContract:
    version: int
    effective_from: date
    table: str
    source_file: str
    description: str
    primary_key: tuple[str, ...]
    columns: tuple[ColumnContract, ...]

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


def bronze_schema(source_columns: list[str]) -> list[tuple[str, str]]:
    """(name, BigQuery type) for a bronze table: source columns then metadata."""
    return [(name, "STRING") for name in source_columns] + list(METADATA_COLUMNS.items())


def breaking_changes(old: TableContract, new: TableContract) -> list[str]:
    """What in `new` could break consumers of `old`. Adding a column never does.

    Breaking: a removed column (a rename must say `renamed_from`), a changed
    parse kind, a required column that becomes nullable, a new primary key.
    """
    problems = []
    if new.primary_key != old.primary_key:
        problems.append(f"primary key changed from {old.primary_key} to {new.primary_key}")

    old_columns = {c.name: c for c in old.columns}
    successors: dict[str, ColumnContract] = {}
    for column in new.columns:
        previous = column.renamed_from or column.name
        if column.renamed_from and previous not in old_columns:
            problems.append(f"{column.name} is renamed from unknown column {previous}")
        elif previous in old_columns:
            successors[previous] = column

    for name, before in old_columns.items():
        after = successors.get(name)
        if after is None:
            problems.append(f"column {name} was removed")
            continue
        if after.parse != before.parse:
            problems.append(
                f"{after.name}: parse kind changed from {before.parse} to {after.parse}"
            )
        if after.nullable and not before.nullable:
            problems.append(f"{after.name}: required column became nullable")
    return problems


def _check_history(table: str, versions: list[TableContract]) -> None:
    for old, new in zip(versions, versions[1:], strict=False):
        if new.effective_from <= old.effective_from:
            raise ContractError(
                f"{table} v{new.version} must take effect after v{old.version} "
                f"({new.effective_from} <= {old.effective_from})"
            )
        problems = breaking_changes(old, new)
        if problems:
            raise ContractError(
                f"{table} v{new.version} breaks v{old.version}: " + "; ".join(problems)
            )


@dataclass(frozen=True)
class ContractRegistry:
    """Every contract version per bronze table, oldest first."""

    versions: dict[str, tuple[TableContract, ...]]

    @property
    def tables(self) -> list[str]:
        return sorted(self.versions)

    def select(self, tables: list[str] | None) -> ContractRegistry:
        if not tables:
            return self
        unknown = sorted(set(tables) - set(self.versions))
        if unknown:
            raise ValueError(f"Unknown tables {unknown}. Known: {self.tables}")
        return replace(self, versions={t: self.versions[t] for t in tables})

    def for_date(self, load_date: date) -> dict[str, TableContract]:
        """The contract in force on a load date, per table."""
        contracts = {}
        for table, versions in self.versions.items():
            in_force = [c for c in versions if c.effective_from <= load_date]
            if not in_force:
                raise ContractError(f"No {table} contract is in force on {load_date}")
            contracts[table] = in_force[-1]
        return contracts

    def bronze_columns(self, table: str) -> list[str]:
        """Every source column any version has had, in order of first appearance.

        A renamed column keeps its old name too: old batches fill the old
        column, new batches the new one, and silver merges them.
        """
        names = (c.name for version in self.versions[table] for c in version.columns)
        return list(dict.fromkeys(names))


def _parse_column(raw: dict) -> ColumnContract:
    column = ColumnContract(
        name=raw["name"],
        parse=raw["parse"],
        nullable=bool(raw["nullable"]),
        renamed_from=raw.get("renamed_from"),
    )
    if column.parse not in PARSE_KINDS:
        raise ContractError(f"Column {column.name}: unknown parse kind {column.parse!r}")
    return column


def load_contract(path: Path) -> TableContract:
    raw = yaml.safe_load(path.read_text())
    effective_from = raw.get("effective_from")
    return TableContract(
        version=int(raw["version"]),
        effective_from=date.fromisoformat(str(effective_from)) if effective_from else FIRST_BATCH,
        table=raw["table"],
        source_file=raw["source_file"],
        description=raw.get("description", ""),
        primary_key=tuple(raw.get("primary_key", [])),
        columns=tuple(_parse_column(c) for c in raw["columns"]),
    )


def _version_dirs(contracts_dir: Path) -> list[tuple[int, Path]]:
    dirs = [
        (int(p.name[1:]), p)
        for p in contracts_dir.glob("v*")
        if p.is_dir() and p.name[1:].isdigit()
    ]
    return sorted(dirs)


def load_registry(contracts_dir: Path = CONTRACTS_DIR) -> ContractRegistry:
    """All contract versions, checked: v1 covers every table, no change is breaking."""
    dirs = _version_dirs(contracts_dir)
    if not dirs:
        raise FileNotFoundError(f"No contract versions (v1/, v2/, ...) in {contracts_dir}")

    history: dict[str, list[TableContract]] = defaultdict(list)
    for number, folder in dirs:
        for path in sorted(folder.glob("*.yaml")):
            contract = load_contract(path)
            if contract.version != number:
                raise ContractError(f"{path} says version {contract.version}, folder is v{number}")
            if number > 1 and contract.table not in history:
                raise ContractError(f"{path}: {contract.table} has no v1 contract")
            history[contract.table].append(contract)

    for table, versions in history.items():
        _check_history(table, versions)
    return ContractRegistry(versions={t: tuple(v) for t, v in sorted(history.items())})


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
