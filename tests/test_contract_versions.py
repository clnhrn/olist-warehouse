"""Contract versions: which version applies to a load date, which changes are
breaking, and how bronze carries batches of different versions."""

from dataclasses import replace
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from contracts.loader import (
    ColumnContract,
    ContractError,
    ContractRegistry,
    breaking_changes,
    load_registry,
)
from ingestion.config import bronze_key
from ingestion.ingest import ContractViolationError, conform_to_bronze, ingest, ingest_landed
from ingestion.land import land
from ingestion.storage import LocalStorage
from tests.conftest import SELLERS_CSV

V1_DATE = date(2016, 1, 1)
V2_DATE = date(2016, 2, 1)

SELLERS_V1 = """\
version: 1
table: sellers
source_file: olist_sellers_dataset.csv
primary_key: [seller_id]
columns:
  - {name: seller_id, parse: id32, nullable: false}
  - {name: seller_zip_code_prefix, parse: zip, nullable: false}
  - {name: seller_city, parse: text, nullable: false}
  - {name: seller_state, parse: state, nullable: false}
"""

# v2 renames seller_city to seller_city_name and adds seller_updated_at.
SELLERS_V2 = """\
version: 2
effective_from: 2016-02-01
table: sellers
source_file: olist_sellers_dataset.csv
primary_key: [seller_id]
columns:
  - {name: seller_id, parse: id32, nullable: false}
  - {name: seller_zip_code_prefix, parse: zip, nullable: false}
  - {name: seller_city_name, parse: text, nullable: false, renamed_from: seller_city}
  - {name: seller_state, parse: state, nullable: false}
  - {name: seller_updated_at, parse: timestamp, nullable: false}
"""

SELLERS_V2_CSV = (
    "seller_id,seller_zip_code_prefix,seller_city_name,seller_state,seller_updated_at\n"
    '3442f8959a84dea7ee197c632cb2df15,"01037",campinas,SP,2016-01-01 00:00:00\n'
)


def _write_contracts(root: Path, versions: dict[str, str]) -> Path:
    for folder, body in versions.items():
        (root / folder).mkdir(parents=True)
        (root / folder / "sellers.yaml").write_text(body)
    return root


@pytest.fixture
def two_versions(tmp_path) -> ContractRegistry:
    return load_registry(
        _write_contracts(tmp_path / "contracts", {"v1": SELLERS_V1, "v2": SELLERS_V2})
    )


@pytest.fixture
def v1(two_versions):
    return two_versions.versions["sellers"][0]


def _column(name: str, parse: str = "text", nullable: bool = False, renamed_from=None):
    return ColumnContract(name=name, parse=parse, nullable=nullable, renamed_from=renamed_from)


def _with_columns(contract, *columns):
    return replace(contract, version=2, columns=tuple(columns))


# Which version applies


def test_each_date_gets_the_version_in_force(two_versions):
    assert two_versions.for_date(V1_DATE)["sellers"].version == 1
    assert two_versions.for_date(date(2016, 1, 31))["sellers"].version == 1
    assert two_versions.for_date(V2_DATE)["sellers"].version == 2
    assert two_versions.for_date(date(2018, 1, 1))["sellers"].version == 2


def test_bronze_keeps_every_column_any_version_had(two_versions):
    assert two_versions.bronze_columns("sellers") == [
        "seller_id",
        "seller_zip_code_prefix",
        "seller_city",
        "seller_state",
        "seller_city_name",
        "seller_updated_at",
    ]


def test_real_contracts_load_and_pass_their_history(registry):
    for versions in registry.versions.values():
        assert versions[0].version == 1


# Breaking changes


def test_added_column_is_not_breaking(v1):
    new = _with_columns(v1, *v1.columns, _column("seller_updated_at", "timestamp"))
    assert breaking_changes(v1, new) == []


def test_declared_rename_is_not_breaking(v1):
    renamed = [
        _column("seller_city_name", renamed_from="seller_city") if c.name == "seller_city" else c
        for c in v1.columns
    ]
    assert breaking_changes(v1, _with_columns(v1, *renamed)) == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda c: None if c.name == "seller_city" else c, "column seller_city was removed"),
        (
            lambda c: replace(c, name="city") if c.name == "seller_city" else c,
            "column seller_city was removed",
        ),
        (
            lambda c: replace(c, parse="text") if c.name == "seller_zip_code_prefix" else c,
            "parse kind changed from zip to text",
        ),
        (
            lambda c: replace(c, nullable=True) if c.name == "seller_state" else c,
            "required column became nullable",
        ),
        (
            lambda c: replace(c, renamed_from="no_such_column") if c.name == "seller_city" else c,
            "renamed from unknown column no_such_column",
        ),
    ],
    ids=["removed", "undeclared-rename", "parse-kind", "nullable", "rename-unknown"],
)
def test_breaking_changes_are_reported(v1, change, message):
    new = _with_columns(v1, *[c for c in map(change, v1.columns) if c is not None])
    assert any(message in problem for problem in breaking_changes(v1, new))


def test_new_primary_key_is_breaking(v1):
    new = replace(v1, version=2, primary_key=("seller_id", "seller_state"))
    assert any("primary key" in p for p in breaking_changes(v1, new))


def test_registry_refuses_a_breaking_version(tmp_path):
    removed_state = SELLERS_V2.replace(
        "  - {name: seller_state, parse: state, nullable: false}\n", ""
    )
    root = _write_contracts(tmp_path, {"v1": SELLERS_V1, "v2": removed_state})
    with pytest.raises(ContractError, match="column seller_state was removed"):
        load_registry(root)


def test_registry_refuses_a_version_that_does_not_take_effect_later(tmp_path):
    root = _write_contracts(tmp_path, {"v1": SELLERS_V1, "v2": SELLERS_V2})
    (root / "v1" / "sellers.yaml").write_text(SELLERS_V1 + "effective_from: 2016-03-01\n")
    with pytest.raises(ContractError, match="must take effect after"):
        load_registry(root)


def test_registry_refuses_a_version_number_that_does_not_match_its_folder(tmp_path):
    root = _write_contracts(tmp_path, {"v1": SELLERS_V1, "v3": SELLERS_V2})
    with pytest.raises(ContractError, match="folder is v3"):
        load_registry(root)


# Bronze across versions


def test_conform_to_bronze_adds_missing_columns_as_null_in_bronze_order():
    df = pl.DataFrame({"b": ["1"], "a": ["2"]})
    out = conform_to_bronze(df, ["a", "b", "c"])
    assert out.columns == ["a", "b", "c"]
    assert out.schema["c"] == pl.Utf8
    assert out["c"].to_list() == [None]


def _land(lake: LocalStorage, registry: ContractRegistry, csv: str, load_date: date, tmp: Path):
    source = tmp / f"raw-{load_date}"
    source.mkdir()
    (source / "olist_sellers_dataset.csv").write_text(csv)
    land(lake, registry.for_date(load_date), source, load_date)


def test_batches_under_two_versions_share_one_bronze_schema(tmp_path, two_versions):
    lake = LocalStorage(tmp_path / "lake")
    _land(lake, two_versions, SELLERS_CSV, V1_DATE, tmp_path)
    _land(lake, two_versions, SELLERS_V2_CSV, V2_DATE, tmp_path)

    results = ingest_landed(lake, None, two_versions)

    assert {d: r.rows["sellers"] for d, r in results.items()} == {V1_DATE: 2, V2_DATE: 1}
    first = pl.read_parquet(lake.uri(bronze_key("sellers", V1_DATE)))
    second = pl.read_parquet(lake.uri(bronze_key("sellers", V2_DATE)))
    assert first.columns == second.columns
    assert first["seller_city"].to_list() == ["campinas", "mogi guacu"]
    assert first["seller_city_name"].null_count() == 2
    assert second["seller_city"].null_count() == 1
    assert second["seller_city_name"].to_list() == ["campinas"]
    assert second["seller_updated_at"].to_list() == ["2016-01-01 00:00:00"]


def test_old_schema_after_new_version_takes_effect_fails(tmp_path, two_versions):
    lake = LocalStorage(tmp_path / "lake")
    _land(lake, two_versions, SELLERS_CSV, V2_DATE, tmp_path)
    with pytest.raises(ContractViolationError, match="contract v2"):
        ingest(lake, None, two_versions, V2_DATE)


def test_new_contract_version_for_a_loaded_date_reloads_it(tmp_path, two_versions):
    """Changing which version applies to a date invalidates the manifest skip."""
    lake = LocalStorage(tmp_path / "lake")
    _land(lake, two_versions, SELLERS_V2_CSV, V2_DATE, tmp_path)
    ingest(lake, None, two_versions, V2_DATE)

    v1_only = load_registry(_write_contracts(tmp_path / "v1-only", {"v1": SELLERS_V1}))
    with pytest.raises(ContractViolationError, match="contract v1"):
        ingest(lake, None, v1_only, V2_DATE)
