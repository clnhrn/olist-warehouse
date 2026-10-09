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
    """What was loaded for one table.

    Attributes:
        landing_md5: Base64 MD5 of the landing CSV it was loaded from.
        rows: Rows written to bronze.
        contract_version: Contract version it was validated against.
        rows_quarantined: Rows sent to quarantine.
    """

    landing_md5: str
    rows: int
    contract_version: int = 1
    rows_quarantined: int = 0


@dataclass
class Manifest:
    """What was loaded for one load date.

    Attributes:
        load_date: The batch's load date, ISO format.
        loaded_at: When the batch was last loaded, ISO format.
        tables: Entry per table.
    """

    load_date: str
    loaded_at: str = ""
    tables: dict[str, TableEntry] = field(default_factory=dict)

    def matches(self, landing_md5s: dict[str, str], versions: dict[str, int]) -> bool:
        """Check whether a load would repeat this one exactly.

        Args:
            landing_md5s: Current MD5 of each table's landing file.
            versions: Contract version now in force, per table.

        Returns:
            True if every table was loaded from identical landing files under
            the contract version now in force.
        """
        return all(
            table in self.tables
            and self.tables[table].landing_md5 == md5
            and self.tables[table].contract_version == versions[table]
            for table, md5 in landing_md5s.items()
        )


def read_manifest(storage: Storage, load_date: date) -> Manifest | None:
    """Read a load date's manifest.

    Args:
        storage: The lake.
        load_date: The batch's load date.

    Returns:
        The manifest, or None if the date was never loaded.
    """
    key = manifest_key(load_date)
    if not storage.exists(key):
        return None
    raw = json.loads(storage.read_bytes(key))
    # Manifests written before contracts were versioned per table.
    raw.pop("contract_version", None)
    tables = {name: TableEntry(**entry) for name, entry in raw.pop("tables", {}).items()}
    return Manifest(**raw, tables=tables)


def write_manifest(storage: Storage, load_date: date, manifest: Manifest) -> None:
    """Write a load date's manifest, replacing any earlier one.

    Args:
        storage: The lake.
        load_date: The batch's load date.
        manifest: The manifest to write.
    """
    body = json.dumps(asdict(manifest), indent=2, sort_keys=True).encode()
    storage.write_bytes(manifest_key(load_date), body)
