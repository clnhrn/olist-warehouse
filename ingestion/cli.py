"""Command line entry point.

python -m ingestion land   --load-date D [--sink local|gcs] [--env dev|prod]
python -m ingestion ingest --load-date D | --all-dates [--sink local|gcs] [--env dev|prod] [--force]
python -m ingestion check-rerun --load-date D [--sink local|gcs] [--no-dbt]   (dev only)
All accept --tables to limit the run to some bronze tables.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from contracts.loader import ContractError, load_registry
from ingestion.config import (
    DBT_PROJECT_DIR,
    LOCAL_DUCKDB_PATH,
    LOCAL_LAKE_DIR,
    LOCAL_RAW_DIR,
    Env,
    Sink,
    gcp_settings,
)
from ingestion.idempotency import (
    DBT_LAYERS,
    check_rerun,
    local_bronze_counts,
    local_warehouse_counts,
    run_dbt_build,
)
from ingestion.ingest import ContractViolationError, ingest, ingest_landed
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
    common.add_argument("--sink", type=Sink, choices=list(Sink), default=Sink.LOCAL)
    common.add_argument("--env", type=Env, choices=list(Env), default=Env.DEV)
    common.add_argument("--tables", nargs="+", help="Subset of bronze tables (default: all).")
    one_date = argparse.ArgumentParser(add_help=False)
    one_date.add_argument("--load-date", type=date.fromisoformat, required=True)

    land_parser = sub.add_parser(
        "land", parents=[common, one_date], help="Copy raw CSVs into landing."
    )
    land_parser.add_argument("--source-dir", type=Path, default=LOCAL_RAW_DIR)

    ingest_parser = sub.add_parser("ingest", parents=[common], help="Load landing into bronze.")
    dates = ingest_parser.add_mutually_exclusive_group(required=True)
    dates.add_argument("--load-date", type=date.fromisoformat)
    dates.add_argument(
        "--all-dates",
        action="store_true",
        help="Every landed date, oldest first; unchanged dates are skipped (the scheduled run).",
    )
    ingest_parser.add_argument("--force", action="store_true", help="Reload even if unchanged.")

    check_parser = sub.add_parser(
        "check-rerun",
        parents=[common, one_date],
        help="Run ingest and dbt twice for a load date; fail if any row count changes.",
    )
    check_parser.add_argument("--no-dbt", action="store_true", help="Check bronze only.")
    return parser.parse_args(argv)


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


def _counter(warehouse: BigQueryWarehouse | None, dbt: bool) -> Callable[[], dict[str, int]]:
    layers = DBT_LAYERS if dbt else ()
    if warehouse is None:
        return lambda: {
            **local_bronze_counts(LOCAL_LAKE_DIR),
            **local_warehouse_counts(LOCAL_DUCKDB_PATH, layers),
        }
    return lambda: warehouse.row_counts(("bronze", *layers))


def _rerun_step(args: argparse.Namespace, storage, warehouse, registry) -> Callable[[], None]:
    target = "local" if warehouse is None else "dev"

    def step() -> None:
        ingest(storage, warehouse, registry, args.load_date, force=True)
        if not args.no_dbt:
            run_dbt_build(DBT_PROJECT_DIR, target)

    return step


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = _parse_args(argv)
    try:
        if args.command == "check-rerun" and args.env is Env.PROD:
            raise ValueError("check-rerun writes data, so it runs against dev only.")
        registry = load_registry().select(args.tables)
        storage, warehouse = _build(args.sink, args.env)
        if args.command == "land":
            land(storage, registry.for_date(args.load_date), args.source_dir, args.load_date)
        elif args.command == "check-rerun":
            changes = check_rerun(
                run=_rerun_step(args, storage, warehouse, registry),
                count=_counter(warehouse, dbt=not args.no_dbt),
            )
            if changes:
                logger.error("Rerun of %s changed %d table(s).", args.load_date, len(changes))
                return 1
            logger.info("Rerun of %s left every row count unchanged.", args.load_date)
        else:
            if args.all_dates:
                results = ingest_landed(storage, warehouse, registry, force=args.force)
            else:
                results = {
                    args.load_date: ingest(
                        storage, warehouse, registry, args.load_date, force=args.force
                    )
                }
            for load_date, result in results.items():
                for table, rows in sorted(result.rows.items()):
                    quarantined = result.quarantined.get(table, 0)
                    logger.info(
                        "%s %-30s %10d rows%s%s",
                        load_date,
                        table,
                        rows,
                        f", {quarantined} quarantined" if quarantined else "",
                        " (skipped)" if result.skipped else "",
                    )
    except subprocess.CalledProcessError as exc:
        logger.error("dbt build failed with exit code %d.", exc.returncode)
        return 1
    except (
        ContractError,
        ContractViolationError,
        LandingExistsError,
        FileNotFoundError,
        ValueError,
    ) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
