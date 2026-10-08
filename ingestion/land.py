"""Copy raw source CSVs into landing/load_date=D/. Landing is immutable."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from contracts.loader import TableContract
from ingestion.config import landing_key, landing_prefix
from ingestion.storage import Storage

logger = logging.getLogger(__name__)


class LandingExistsError(RuntimeError):
    pass


def land(
    storage: Storage,
    contracts: dict[str, TableContract],
    source_dir: Path,
    load_date: date,
) -> list[str]:
    """Upload one CSV per contract. Refuses to touch a load date that already has files."""
    existing = storage.list_keys(landing_prefix(load_date))
    if existing:
        raise LandingExistsError(
            f"Landing for {load_date} already has {len(existing)} files. "
            "Landing is immutable: use a new load date."
        )

    missing = [
        c.source_file for c in contracts.values() if not (source_dir / c.source_file).is_file()
    ]
    if missing:
        raise FileNotFoundError(f"Missing source files in {source_dir}: {missing}")

    uris = []
    for contract in contracts.values():
        key = landing_key(load_date, contract.source_file)
        storage.upload_file(source_dir / contract.source_file, key)
        uris.append(storage.uri(key))
        logger.info("Landed %s", storage.uri(key))
    return uris
