"""Command line entry point.

python -m ingestion land   --load-date D [--sink local|gcs] [--env dev|prod]
python -m ingestion ingest --load-date D [--sink local|gcs] [--env dev|prod] [--force]
python -m ingestion check-rerun --load-date D [--sink local|gcs]   (dev only)
All accept --tables to limit the run to some bronze tables.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from contracts.loader import TableContract, load_contracts
from ingestion.config import LOCAL_LAKE_DIR, LOCAL_RAW_DIR, Env, Sink, gcp_settings
from ingestion.idempotency import check_rerun, local_bronze_counts
from ingestion.ingest import ContractViolationError, ingest
from ingestion.land import LandingExistsError, land
from ingestion.storage import GcsStorage, LocalStorage, Storage
from ingestion.warehouse import BigQueryWarehouse

logger = logging.getLogger("ingestion")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ingestion",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--load-date", type=date.fromisoformat, required=True)
    common.add_argument("--sink", type=Sink, choices=list(Sink), default=Sink.LOCAL)
    common.add_argument("--env", type=Env, choices=list(Env), default=Env.DEV)
    common.add_argument("--tables", nargs="+", help="Subset of bronze tables (default: all).")

    land_parser = sub.add_parser("land", parents=[common], help="Copy raw CSVs into landing.")
    land_parser.add_argument("--source-dir", type=Path, default=LOCAL_RAW_DIR)

    ingest_parser = sub.add_parser("ingest", parents=[common], help="Load landing into bronze.")
    ingest_parser.add_argument("--force", action="store_true", help="Reload even if unchanged.")

    sub.add_parser(
        "check-rerun",
        parents=[common],
        help="Force-ingest a load date twice and fail if any row count changes.",
    )
    return parser.parse_args(argv)


def _select(
    contracts: dict[str, TableContract], tables: list[str] | None
) -> dict[str, TableContract]:
    if not tables:
        return contracts
    unknown = sorted(set(tables) - set(contracts))
    if unknown:
        raise ValueError(f"Unknown tables {unknown}. Known: {sorted(contracts)}")
    return {t: contracts[t] for t in tables}


def _build(sink: Sink, env: Env) -> tuple[Storage, BigQueryWarehouse | None]:
    if sink is Sink.LOCAL:
        return LocalStorage(LOCAL_LAKE_DIR), None
    settings = gcp_settings(env)
    logger.info(
        "Target: project=%s bucket=%s dataset=%s",
        settings.project_id,
        settings.lake_bucket,
        settings.bronze_dataset,
    )
    return GcsStorage(settings.lake_bucket, settings.project_id), BigQueryWarehouse(settings)


def _counter(warehouse: BigQueryWarehouse | None) -> Callable[[], dict[str, int]]:
    if warehouse is None:
        return lambda: local_bronze_counts(LOCAL_LAKE_DIR)
    return warehouse.row_counts


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = _parse_args(argv)
    try:
        if args.command == "check-rerun" and args.env is Env.PROD:
            raise ValueError("check-rerun writes data, so it runs against dev only.")
        contracts = _select(load_contracts(), args.tables)
        storage, warehouse = _build(args.sink, args.env)
        if args.command == "land":
            land(storage, contracts, args.source_dir, args.load_date)
        elif args.command == "check-rerun":
            changes = check_rerun(
                run=lambda: ingest(storage, warehouse, contracts, args.load_date, force=True),
                count=_counter(warehouse),
            )
            if changes:
                logger.error("Rerun of %s changed %d table(s).", args.load_date, len(changes))
                return 1
            logger.info("Rerun of %s left every row count unchanged.", args.load_date)
        else:
            result = ingest(storage, warehouse, contracts, args.load_date, force=args.force)
            for table, rows in sorted(result.rows.items()):
                logger.info(
                    "%-30s %10d rows%s", table, rows, " (skipped)" if result.skipped else ""
                )
    except (ContractViolationError, LandingExistsError, FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
