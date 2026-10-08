from datetime import date

import polars as pl
import pytest

from ingestion.config import bronze_key, landing_key
from ingestion.ingest import ContractViolationError, ingest
from ingestion.land import LandingExistsError, land
from ingestion.storage import LocalStorage

LOAD_DATE = date(2016, 1, 1)


@pytest.fixture
def two_contracts(contracts):
    return {t: contracts[t] for t in ("sellers", "orders")}


@pytest.fixture
def lake(tmp_path):
    return LocalStorage(tmp_path / "lake")


def _bronze(lake: LocalStorage, table: str) -> pl.DataFrame:
    return pl.read_parquet(lake.uri(bronze_key(table, LOAD_DATE)))


def test_land_then_ingest_writes_bronze_with_lineage(lake, two_contracts, source_dir):
    land(lake, two_contracts, source_dir, LOAD_DATE)
    result = ingest(lake, None, two_contracts, LOAD_DATE)

    assert not result.skipped
    assert result.rows == {"sellers": 2, "orders": 2}
    sellers = _bronze(lake, "sellers")
    assert sellers.schema["_loaded_at"] == pl.Datetime("us", "UTC")
    assert sellers.schema["_load_date"] == pl.Date
    assert sellers["_load_date"].unique().to_list() == [LOAD_DATE]
    assert sellers["_source_file"][0].endswith(landing_key(LOAD_DATE, "olist_sellers_dataset.csv"))
    assert sellers.schema["seller_zip_code_prefix"] == pl.Utf8


def test_landing_is_immutable(lake, two_contracts, source_dir):
    land(lake, two_contracts, source_dir, LOAD_DATE)
    with pytest.raises(LandingExistsError):
        land(lake, two_contracts, source_dir, LOAD_DATE)


def test_rerun_skips_when_landing_unchanged(lake, two_contracts, source_dir):
    land(lake, two_contracts, source_dir, LOAD_DATE)
    ingest(lake, None, two_contracts, LOAD_DATE)
    second = ingest(lake, None, two_contracts, LOAD_DATE)
    assert second.skipped
    assert second.rows == {"sellers": 2, "orders": 2}


def test_forced_rerun_is_idempotent(lake, two_contracts, source_dir):
    land(lake, two_contracts, source_dir, LOAD_DATE)
    ingest(lake, None, two_contracts, LOAD_DATE)
    forced = ingest(lake, None, two_contracts, LOAD_DATE, force=True)

    assert not forced.skipped
    assert _bronze(lake, "sellers").height == 2
    assert _bronze(lake, "orders").height == 2


def test_contract_violation_stops_batch(lake, two_contracts, source_dir):
    path = source_dir / "olist_sellers_dataset.csv"
    path.write_text(path.read_text().replace('"01037"', '"1037"'))
    land(lake, two_contracts, source_dir, LOAD_DATE)
    with pytest.raises(ContractViolationError):
        ingest(lake, None, two_contracts, LOAD_DATE)
    assert not lake.exists("_manifests/load_date=2016-01-01.json")


def test_missing_landing_file_fails(lake, two_contracts):
    with pytest.raises(FileNotFoundError):
        ingest(lake, None, two_contracts, LOAD_DATE)
