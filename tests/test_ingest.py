import json
from datetime import date

import polars as pl
import pytest

from ingestion.config import bronze_key, landed_dates, landing_key, quarantine_key
from ingestion.ingest import ContractViolationError, ingest, ingest_landed
from ingestion.land import LandingExistsError, land
from ingestion.runs import LocalRunLog
from ingestion.storage import LocalStorage
from tests.conftest import FIRST_LOAD_DATE

LOAD_DATE = FIRST_LOAD_DATE


@pytest.fixture
def lake(tmp_path):
    return LocalStorage(tmp_path / "lake")


def _bronze(lake: LocalStorage, table: str) -> pl.DataFrame:
    return pl.read_parquet(lake.uri(bronze_key(table, LOAD_DATE)))


def test_land_then_ingest_writes_bronze_with_lineage(lake, two_tables, source_dir):
    land(lake, two_tables.for_date(LOAD_DATE), source_dir, LOAD_DATE)
    result = ingest(lake, None, two_tables, LOAD_DATE)

    assert not result.skipped
    assert result.rows == {"sellers": 2, "orders": 2}
    sellers = _bronze(lake, "sellers")
    assert sellers.schema["_loaded_at"] == pl.Datetime("us", "UTC")
    assert sellers.schema["_load_date"] == pl.Date
    assert sellers["_load_date"].unique().to_list() == [LOAD_DATE]
    assert sellers["_source_file"][0].endswith(landing_key(LOAD_DATE, "olist_sellers_dataset.csv"))
    assert sellers.schema["seller_zip_code_prefix"] == pl.Utf8


def test_landing_is_immutable(lake, two_tables, source_dir):
    land(lake, two_tables.for_date(LOAD_DATE), source_dir, LOAD_DATE)
    with pytest.raises(LandingExistsError):
        land(lake, two_tables.for_date(LOAD_DATE), source_dir, LOAD_DATE)


def test_rerun_skips_when_landing_unchanged(lake, two_tables, source_dir):
    land(lake, two_tables.for_date(LOAD_DATE), source_dir, LOAD_DATE)
    ingest(lake, None, two_tables, LOAD_DATE)
    second = ingest(lake, None, two_tables, LOAD_DATE)
    assert second.skipped
    assert second.rows == {"sellers": 2, "orders": 2}


def test_forced_rerun_is_idempotent(lake, two_tables, source_dir):
    land(lake, two_tables.for_date(LOAD_DATE), source_dir, LOAD_DATE)
    ingest(lake, None, two_tables, LOAD_DATE)
    forced = ingest(lake, None, two_tables, LOAD_DATE, force=True)

    assert not forced.skipped
    assert _bronze(lake, "sellers").height == 2
    assert _bronze(lake, "orders").height == 2


def test_contract_violation_stops_batch(lake, two_tables, source_dir):
    path = source_dir / "olist_sellers_dataset.csv"
    path.write_text(path.read_text().replace('"01037"', '"1037"'))
    land(lake, two_tables.for_date(LOAD_DATE), source_dir, LOAD_DATE)
    with pytest.raises(ContractViolationError):
        ingest(lake, None, two_tables, LOAD_DATE)
    assert not lake.exists("_manifests/load_date=2016-01-01.json")


def test_missing_landing_file_fails(lake, two_tables):
    with pytest.raises(FileNotFoundError):
        ingest(lake, None, two_tables, LOAD_DATE)


def test_ingest_landed_loads_every_date_then_skips(lake, two_tables, source_dir):
    second_date = date(2016, 1, 15)  # still under v1, like the fixture files
    land(lake, two_tables.for_date(LOAD_DATE), source_dir, LOAD_DATE)
    land(lake, two_tables.for_date(second_date), source_dir, second_date)
    assert landed_dates(lake) == [LOAD_DATE, second_date]

    first = ingest_landed(lake, None, two_tables)
    again = ingest_landed(lake, None, two_tables)

    assert list(first) == [LOAD_DATE, second_date]
    assert not any(r.skipped for r in first.values())
    assert all(r.skipped for r in again.values())


def test_ingest_landed_without_landing_is_a_no_op(lake, two_tables):
    assert ingest_landed(lake, None, two_tables) == {}


def _many_sellers(rows: int, bad: dict[int, str]) -> str:
    """A sellers file with `rows` valid records; record i becomes bad[i] if given."""
    lines = ["seller_id,seller_zip_code_prefix,seller_city,seller_state"]
    for i in range(1, rows + 1):
        lines.append(bad.get(i, f"{i:032x},01037,campinas,SP"))
    return "\n".join(lines) + "\n"


def _quarantine(lake: LocalStorage, table: str) -> pl.DataFrame:
    return pl.read_parquet(lake.uri(quarantine_key(table, LOAD_DATE)))


def test_bad_rows_go_to_quarantine_and_the_rest_load(lake, two_tables, source_dir):
    bad = {
        7: f"{7:032x},1037,campinas,SP",  # zip lost its leading zero
        150: f"{150:032x},01037,campinas,",  # missing required state
    }
    (source_dir / "olist_sellers_dataset.csv").write_text(_many_sellers(200, bad))
    land(lake, two_tables.for_date(LOAD_DATE), source_dir, LOAD_DATE)

    result = ingest(lake, None, two_tables, LOAD_DATE)

    assert result.rows["sellers"] == 198
    assert result.quarantined == {"sellers": 2, "orders": 0}
    assert _bronze(lake, "sellers").height == 198
    rejected = _quarantine(lake, "sellers")
    assert rejected["source_row"].to_list() == [7, 150]
    assert "seller_zip_code_prefix:str_matches" in rejected["failed_checks"][0]
    assert rejected["failed_checks"][1] == "seller_state:not_nullable"
    assert json.loads(rejected["raw_record"][0])["seller_zip_code_prefix"] == "1037"
    assert rejected["_source_file"][0].endswith("olist_sellers_dataset.csv")
    assert _quarantine(lake, "orders").is_empty()


def test_more_than_one_percent_bad_stops_batch(lake, two_tables, source_dir):
    bad = {i: f"{i:032x},1037,campinas,SP" for i in (1, 2, 3)}
    (source_dir / "olist_sellers_dataset.csv").write_text(_many_sellers(200, bad))
    land(lake, two_tables.for_date(LOAD_DATE), source_dir, LOAD_DATE)
    with pytest.raises(ContractViolationError, match="3 of 200 rows fail"):
        ingest(lake, None, two_tables, LOAD_DATE)


def test_structural_change_stops_batch_even_with_few_rows_bad(lake, two_tables, source_dir):
    csv = _many_sellers(200, {}).replace("seller_city", "seller_town", 1)
    (source_dir / "olist_sellers_dataset.csv").write_text(csv)
    land(lake, two_tables.for_date(LOAD_DATE), source_dir, LOAD_DATE)
    with pytest.raises(ContractViolationError, match="structural change"):
        ingest(lake, None, two_tables, LOAD_DATE)


def test_rerun_replaces_quarantine(lake, two_tables, source_dir):
    bad = {7: f"{7:032x},1037,campinas,SP"}
    (source_dir / "olist_sellers_dataset.csv").write_text(_many_sellers(200, bad))
    land(lake, two_tables.for_date(LOAD_DATE), source_dir, LOAD_DATE)
    ingest(lake, None, two_tables, LOAD_DATE)
    again = ingest(lake, None, two_tables, LOAD_DATE, force=True)
    skipped = ingest(lake, None, two_tables, LOAD_DATE)

    assert again.quarantined["sellers"] == 1
    assert skipped.skipped and skipped.quarantined["sellers"] == 1
    assert _quarantine(lake, "sellers").height == 1
    assert _bronze(lake, "sellers").height == 199


def _run_rows(run_log: LocalRunLog) -> list[dict]:
    return [json.loads(line) for line in run_log.path.read_text().splitlines()]


def test_run_log_records_each_table_loaded_then_skipped(lake, two_tables, source_dir, tmp_path):
    bad = {7: f"{7:032x},1037,campinas,SP"}
    (source_dir / "olist_sellers_dataset.csv").write_text(_many_sellers(200, bad))
    land(lake, two_tables.for_date(LOAD_DATE), source_dir, LOAD_DATE)
    run_log = LocalRunLog(tmp_path / "runs", "run-1")

    ingest(lake, None, two_tables, LOAD_DATE, run_log=run_log)
    ingest(lake, None, two_tables, LOAD_DATE, run_log=run_log)

    rows = {(r["object"], r["status"]): r for r in _run_rows(run_log)}
    assert set(rows) == {
        ("sellers", "loaded"),
        ("orders", "loaded"),
        ("sellers", "skipped"),
        ("orders", "skipped"),
    }
    loaded = rows["sellers", "loaded"]
    assert (loaded["rows_in"], loaded["rows_out"], loaded["rows_quarantined"]) == (200, 199, 1)
    assert loaded["run_id"] == "run-1"
    assert (loaded["component"], loaded["layer"], loaded["load_date"]) == (
        "ingestion",
        "bronze",
        "2016-01-01",
    )
    assert rows["sellers", "skipped"]["rows_out"] == 199


def test_run_log_records_failure_before_raising(lake, two_tables, source_dir, tmp_path):
    path = source_dir / "olist_sellers_dataset.csv"
    path.write_text(path.read_text().replace('"01037"', '"1037"'))
    land(lake, two_tables.for_date(LOAD_DATE), source_dir, LOAD_DATE)
    run_log = LocalRunLog(tmp_path / "runs", "run-2")

    with pytest.raises(ContractViolationError):
        ingest(lake, None, two_tables, LOAD_DATE, run_log=run_log)

    failed = [r for r in _run_rows(run_log) if r["status"] == "failed"]
    assert [r["object"] for r in failed] == ["sellers"]
    assert "violates contract" in failed[0]["message"]


def test_run_log_records_failure_without_landing(lake, two_tables, tmp_path):
    run_log = LocalRunLog(tmp_path / "runs", "run-3")
    with pytest.raises(FileNotFoundError):
        ingest(lake, None, two_tables, LOAD_DATE, run_log=run_log)
    assert [(r["object"], r["status"]) for r in _run_rows(run_log)] == [("*", "failed")]


def test_skipped_date_gets_empty_quarantine_when_missing(lake, two_tables, source_dir):
    """A date loaded before quarantine existed has no quarantine file; a skip adds an empty one."""
    land(lake, two_tables.for_date(LOAD_DATE), source_dir, LOAD_DATE)
    ingest(lake, None, two_tables, LOAD_DATE)
    sellers_key = quarantine_key("sellers", LOAD_DATE)
    lake._path(sellers_key).unlink()

    result = ingest(lake, None, two_tables, LOAD_DATE)

    assert result.skipped
    assert _quarantine(lake, "sellers").is_empty()
    assert set(_quarantine(lake, "sellers").columns) == {
        "source_row",
        "failed_checks",
        "raw_record",
        "_loaded_at",
        "_source_file",
    }
