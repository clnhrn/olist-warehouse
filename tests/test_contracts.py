import io

import polars as pl
import pytest

from contracts.loader import METADATA_COLUMNS
from ingestion.ingest import ContractViolationError, read_landing_csv, validate
from tests.conftest import ORDERS_CSV, SELLERS_CSV


def _frame(csv: str) -> pl.DataFrame:
    return read_landing_csv(csv.encode())


def test_all_nine_contracts_load(contracts):
    assert len(contracts) == 9
    assert {"orders", "order_items", "sellers", "geolocation"} <= set(contracts)


def test_bronze_schema_is_strings_plus_metadata(contracts):
    columns = dict(contracts["sellers"].bronze_columns())
    assert columns["seller_zip_code_prefix"] == "STRING"
    for name, bq_type in METADATA_COLUMNS.items():
        assert columns[name] == bq_type


def test_valid_frames_pass(contracts):
    validate(_frame(SELLERS_CSV), contracts["sellers"])
    validate(_frame(ORDERS_CSV), contracts["orders"])


def test_zip_keeps_leading_zero():
    assert _frame(SELLERS_CSV)["seller_zip_code_prefix"][0] == "01037"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('"01037"', '"1037"'),  # zip lost its leading zero
        ("SP\n", "Sao Paulo\n"),  # state not 2 letters
        ("3442f8959a84dea7ee197c632cb2df15", "not-an-id"),  # bad id
    ],
)
def test_bad_seller_values_fail(contracts, old, new):
    with pytest.raises(ContractViolationError):
        validate(_frame(SELLERS_CSV.replace(old, new, 1)), contracts["sellers"])


def test_impossible_timestamp_fails(contracts):
    bad = ORDERS_CSV.replace("2017-10-02 10:56:33", "2017-13-45 10:56:33")
    with pytest.raises(ContractViolationError):
        validate(_frame(bad), contracts["orders"])


def test_null_in_required_column_fails(contracts):
    bad = ORDERS_CSV.replace("2018-07-24 20:41:37", "")
    with pytest.raises(ContractViolationError):
        validate(_frame(bad), contracts["orders"])


def test_missing_column_fails(contracts):
    df = _frame(SELLERS_CSV).drop("seller_city")
    with pytest.raises(ContractViolationError):
        validate(df, contracts["sellers"])


def test_extra_column_fails(contracts):
    df = _frame(SELLERS_CSV).with_columns(pl.lit("x").alias("surprise"))
    with pytest.raises(ContractViolationError):
        validate(df, contracts["sellers"])


def test_bom_header_is_stripped(contracts):
    csv = "﻿product_category_name,product_category_name_english\nbeleza_saude,health_beauty\n"
    df = read_landing_csv(csv.encode("utf-8"))
    assert df.columns[0] == "product_category_name"
    validate(df, contracts["product_category_translation"])


def test_real_data_sample_passes(contracts, real_raw_dir):
    """First 50 rows of every real source file satisfy its contract."""
    for contract in contracts.values():
        lines = (real_raw_dir / contract.source_file).read_bytes().splitlines(keepends=True)
        sample = b"".join(lines[:51])
        # Reviews contain quoted newlines; reading lines may cut a record, so parse leniently.
        df = pl.read_csv(io.BytesIO(sample), infer_schema=False, truncate_ragged_lines=True)
        validate(df.drop_nulls(subset=[contract.columns[0].name]), contract)
