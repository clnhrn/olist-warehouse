"""Per-load-date manifest: what was loaded, from which landing files, and how many rows.

The manifest makes reruns cheap: if the landing files have not changed since
the last successful load, the job skips the date.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date

from ingestion.config import manifest_key
from ingestion.storage import Storage


@dataclass
class TableEntry:
    landing_md5: str
    rows: int


@dataclass
class Manifest:
    load_date: str
    contract_version: str
    loaded_at: str = ""
    tables: dict[str, TableEntry] = field(default_factory=dict)

    def matches(self, landing_md5s: dict[str, str]) -> bool:
        """True if every requested table was loaded from identical landing files."""
        return all(
            table in self.tables and self.tables[table].landing_md5 == md5
            for table, md5 in landing_md5s.items()
        )


def read_manifest(storage: Storage, load_date: date) -> Manifest | None:
    key = manifest_key(load_date)
    if not storage.exists(key):
        return None
    raw = json.loads(storage.read_bytes(key))
    tables = {name: TableEntry(**entry) for name, entry in raw.pop("tables", {}).items()}
    return Manifest(**raw, tables=tables)


def write_manifest(storage: Storage, load_date: date, manifest: Manifest) -> None:
    body = json.dumps(asdict(manifest), indent=2, sort_keys=True).encode()
    storage.write_bytes(manifest_key(load_date), body)
