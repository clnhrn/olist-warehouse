from datetime import date
from pathlib import Path

import pytest

from contracts.loader import ContractRegistry, TableContract, load_registry
from ingestion.config import LOCAL_RAW_DIR

# The first batch, validated against v1 of every contract.
FIRST_LOAD_DATE = date(2016, 1, 1)

SELLERS_CSV = (
    '"seller_id","seller_zip_code_prefix","seller_city","seller_state"\n'
    '"3442f8959a84dea7ee197c632cb2df15","01037",campinas,SP\n'
    '"d1b65fc7debc3361ea86b5f14c68d2e2","13844",mogi guacu,SP\n'
)

ORDERS_CSV = (
    '"order_id","customer_id","order_status","order_purchase_timestamp","order_approved_at",'
    '"order_delivered_carrier_date","order_delivered_customer_date","order_estimated_delivery_date"\n'
    "e481f51cbdc54678b7cc49136f2d6af7,9ef432eb6251297304e76186b10a928d,delivered,"
    "2017-10-02 10:56:33,2017-10-02 11:07:15,2017-10-04 19:55:00,2017-10-10 21:25:13,"
    "2017-10-18 00:00:00\n"
    "53cdb2fc8bc7dce0b6741e2150273451,b0830fb4747a6c6d20dea0b8c802d7ef,shipped,"
    "2018-07-24 20:41:37,,,,2018-08-13 00:00:00\n"
)


@pytest.fixture(scope="session")
def registry() -> ContractRegistry:
    return load_registry()


@pytest.fixture(scope="session")
def contracts(registry) -> dict[str, TableContract]:
    """The v1 contracts, in force for the first batch."""
    return registry.for_date(FIRST_LOAD_DATE)


@pytest.fixture
def two_tables(registry) -> ContractRegistry:
    return registry.select(["sellers", "orders"])


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    """A raw folder with two small valid source files."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "olist_sellers_dataset.csv").write_text(SELLERS_CSV)
    (raw / "olist_orders_dataset.csv").write_text(ORDERS_CSV)
    return raw


@pytest.fixture
def real_raw_dir() -> Path:
    if not (LOCAL_RAW_DIR / "olist_orders_dataset.csv").is_file():
        pytest.skip("Olist CSVs not downloaded (run setup/download-data.sh)")
    return LOCAL_RAW_DIR
