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
    contract_version: int = 1
    rows_quarantined: int = 0


@dataclass
class Manifest:
    load_date: str
    loaded_at: str = ""
    tables: dict[str, TableEntry] = field(default_factory=dict)

    def matches(self, landing_md5s: dict[str, str], versions: dict[str, int]) -> bool:
        """True if every requested table was loaded from identical landing files
        under the contract version now in force for this date."""
        return all(
            table in self.tables
            and self.tables[table].landing_md5 == md5
            and self.tables[table].contract_version == versions[table]
            for table, md5 in landing_md5s.items()
        )


def read_manifest(storage: Storage, load_date: date) -> Manifest | None:
    key = manifest_key(load_date)
    if not storage.exists(key):
        return None
    raw = json.loads(storage.read_bytes(key))
    # Manifests written before contracts were versioned per table.
    raw.pop("contract_version", None)
    tables = {name: TableEntry(**entry) for name, entry in raw.pop("tables", {}).items()}
    return Manifest(**raw, tables=tables)


def write_manifest(storage: Storage, load_date: date, manifest: Manifest) -> None:
    body = json.dumps(asdict(manifest), indent=2, sort_keys=True).encode()
    storage.write_bytes(manifest_key(load_date), body)
