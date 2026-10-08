"""Build the simulated batch 3 from batch 2: some sellers move.

Olist is a one-time snapshot, so seller changes are made up. Batch 3 is a
full snapshot under contract v2, like batch 2:

- sellers: 20 sellers move to another state on 2018-01-01, inside the order
  history, so some of their orders fall before the move and some after. Each
  takes the address (zip, city, state) of a different, unchanged seller, so
  the new zip is a known location. Their seller_updated_at becomes
  2018-01-01; every other seller keeps the 2016-01-01 baseline.
- the other 8 files are copied byte for byte.

Deterministic: the same input always gives the same files.

    uv run python scripts/make_batch2.py
    uv run python scripts/make_batch3.py
    uv run python -m ingestion land --load-date 2016-03-01 --source-dir data/raw-batch3 ...
"""

from __future__ import annotations

import argparse
import logging
import shutil
from datetime import datetime
from pathlib import Path

import polars as pl

REPO_ROOT = Path(__file__).resolve().parent.parent
SELLERS = "olist_sellers_dataset.csv"
ORDERS = "olist_orders_dataset.csv"
ORDER_ITEMS = "olist_order_items_dataset.csv"
CHANGE_AT = datetime(2018, 1, 1)
CHANGE_AT_TEXT = "2018-01-01 00:00:00"
SELLERS_TO_MOVE = 20
ADDRESS = ["seller_zip_code_prefix", "seller_city", "seller_state"]

logger = logging.getLogger("make_batch3")


def _read(path: Path) -> pl.DataFrame:
    """All columns as text, like ingestion reads them, so values stay as delivered."""
    return pl.read_csv(path, infer_schema=False)


def sellers_with_orders_on_both_sides(source_dir: Path) -> list[str]:
    """Sellers with at least one order before CHANGE_AT and one after, sorted by id."""
    orders = pl.scan_csv(source_dir / ORDERS, infer_schema=False).select(
        "order_id",
        pl.col("order_purchase_timestamp").str.to_datetime("%Y-%m-%d %H:%M:%S"),
    )
    items = pl.scan_csv(source_dir / ORDER_ITEMS, infer_schema=False).select(
        "order_id", "seller_id"
    )
    return (
        items.join(orders, on="order_id")
        .group_by("seller_id")
        .agg(
            (pl.col("order_purchase_timestamp") < CHANGE_AT).any().alias("before"),
            (pl.col("order_purchase_timestamp") >= CHANGE_AT).any().alias("after"),
        )
        .filter(pl.col("before") & pl.col("after"))
        .sort("seller_id")
        .collect()["seller_id"]
        .to_list()
    )


def plan_moves(sellers: pl.DataFrame, movers: list[str]) -> dict[str, dict[str, str]]:
    """New address per mover: the first unused, unchanged seller in another state."""
    rows = {row["seller_id"]: row for row in sellers.sort("seller_id").iter_rows(named=True)}
    donors = [seller_id for seller_id in rows if seller_id not in set(movers)]
    used: set[str] = set()
    moves: dict[str, dict[str, str]] = {}
    for mover in movers:
        donor = next(
            d
            for d in donors
            if d not in used and rows[d]["seller_state"] != rows[mover]["seller_state"]
        )
        used.add(donor)
        moves[mover] = {column: rows[donor][column] for column in ADDRESS}
    return moves


def apply_moves(sellers: pl.DataFrame, moves: dict[str, dict[str, str]]) -> pl.DataFrame:
    moved = pl.col("seller_id").is_in(list(moves))
    return sellers.with_columns(
        *(
            pl.when(moved)
            .then(
                pl.col("seller_id").replace_strict(
                    {mover: address[column] for mover, address in moves.items()}, default=None
                )
            )
            .otherwise(pl.col(column))
            .alias(column)
            for column in ADDRESS
        ),
        pl.when(moved)
        .then(pl.lit(CHANGE_AT_TEXT))
        .otherwise(pl.col("seller_updated_at"))
        .alias("seller_updated_at"),
    )


def make_batch3(source_dir: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(source_dir.glob("*.csv")):
        if path.name != SELLERS:
            shutil.copyfile(path, out_dir / path.name)

    sellers = _read(source_dir / SELLERS)
    movers = sellers_with_orders_on_both_sides(source_dir)[:SELLERS_TO_MOVE]
    if len(movers) < SELLERS_TO_MOVE:
        raise ValueError(f"Only {len(movers)} sellers have orders on both sides of {CHANGE_AT}")
    moves = plan_moves(sellers, movers)
    apply_moves(sellers, moves).write_csv(out_dir / SELLERS)
    for mover, address in moves.items():
        logger.info("Seller %s moves to %s", mover, ", ".join(address.values()))
    logger.info("Wrote batch 3 to %s: %d of %d sellers moved", out_dir, len(moves), sellers.height)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-dir", type=Path, default=REPO_ROOT / "data" / "raw-batch2")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data" / "raw-batch3")
    args = parser.parse_args()
    make_batch3(args.source_dir, args.out_dir)


if __name__ == "__main__":
    main()
