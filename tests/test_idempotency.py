import duckdb

from ingestion.cli import main
from ingestion.idempotency import (
    CountChange,
    check_rerun,
    diff_counts,
    local_bronze_counts,
    local_warehouse_counts,
)
from ingestion.ingest import ingest
from ingestion.land import land
from ingestion.storage import LocalStorage
from tests.conftest import FIRST_LOAD_DATE

LOAD_DATE = FIRST_LOAD_DATE


def test_rerun_of_same_date_keeps_bronze_counts(tmp_path, two_tables, source_dir):
    lake = LocalStorage(tmp_path / "lake")
    land(lake, two_tables.for_date(LOAD_DATE), source_dir, LOAD_DATE)

    changes = check_rerun(
        run=lambda: ingest(lake, None, two_tables, LOAD_DATE, force=True),
        count=lambda: local_bronze_counts(tmp_path / "lake"),
    )

    assert changes == []
    assert local_bronze_counts(tmp_path / "lake") == {"bronze.orders": 2, "bronze.sellers": 2}


def test_check_flags_a_run_that_appends():
    counts = iter([{"bronze.orders": 2}, {"bronze.orders": 4}])
    changes = check_rerun(run=lambda: None, count=lambda: next(counts))
    assert changes == [CountChange("bronze.orders", 2, 4)]


def test_diff_reports_added_and_missing_tables():
    changes = diff_counts({"bronze.a": 1, "bronze.b": 1}, {"bronze.b": 1, "bronze.c": 1})
    assert changes == [CountChange("bronze.a", 1, None), CountChange("bronze.c", None, 1)]


def test_check_rerun_refuses_prod():
    assert main(["check-rerun", "--load-date", "2016-01-01", "--env", "prod"]) == 1


def test_local_warehouse_counts_reads_layer_tables_only(tmp_path):
    path = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("create schema dev_silver; create schema dev_bronze")
        con.execute("create table dev_silver.silver_orders as select * from range(3)")
        con.execute("create view dev_silver.orders_view as select 1")
        con.execute("create table dev_bronze.orders as select 1")

    assert local_warehouse_counts(path) == {"silver.silver_orders": 3}
    assert local_warehouse_counts(tmp_path / "missing.duckdb") == {}
