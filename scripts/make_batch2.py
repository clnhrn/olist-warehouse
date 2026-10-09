"""Build the simulated batch 2 from the original Olist CSVs.

Olist is a one-time snapshot, so batch 2 is made up: a full snapshot of all
9 files, delivered under contract v2.

- products: the source fixed its "lenght" typos (two renamed columns).
- sellers: the source now sends seller_updated_at. No seller changed, so every
  value is the 2016-01-01 baseline; Phase 6 adds changes in a later batch.
- the other 7 files are copied byte for byte.

Deterministic: the same input always gives the same files, so landing them
twice would be byte-identical.

    uv run python scripts/make_batch2.py
    uv run python -m ingestion land --load-date 2016-02-01 --source-dir data/raw-batch2 ...
"""

from __future__ import annotations

import argparse
import logging
import shutil
from pathlib import Path

import polars as pl

REPO_ROOT = Path(__file__).resolve().parent.parent
PRODUCTS = "olist_products_dataset.csv"
SELLERS = "olist_sellers_dataset.csv"
PRODUCT_RENAMES = {
    "product_name_lenght": "product_name_length",
    "product_description_lenght": "product_description_length",
}
SELLER_BASELINE = "2016-01-01 00:00:00"

logger = logging.getLogger("make_batch2")


def _read(path: Path) -> pl.DataFrame:
    """All columns as text, like ingestion reads them, so values stay as delivered."""
    return pl.read_csv(path, infer_schema=False)


def make_batch2(source_dir: Path, out_dir: Path) -> None:
    """Write batch 2: renamed product columns, sellers with seller_updated_at.

    Args:
        source_dir: Folder with the original Olist CSVs.
        out_dir: Folder to write batch 2 into; created if missing.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(source_dir.glob("*.csv")):
        if path.name not in {PRODUCTS, SELLERS}:
            shutil.copyfile(path, out_dir / path.name)

    products = _read(source_dir / PRODUCTS).rename(PRODUCT_RENAMES)
    products.write_csv(out_dir / PRODUCTS)

    sellers = _read(source_dir / SELLERS).with_columns(
        pl.lit(SELLER_BASELINE).alias("seller_updated_at")
    )
    sellers.write_csv(out_dir / SELLERS)
    logger.info(
        "Wrote batch 2 to %s: %d products, %d sellers", out_dir, products.height, sellers.height
    )


def main() -> None:
    """Parse the command line and build batch 2."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-dir", type=Path, default=REPO_ROOT / "data" / "raw")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data" / "raw-batch2")
    args = parser.parse_args()
    make_batch2(args.source_dir, args.out_dir)


if __name__ == "__main__":
    main()
