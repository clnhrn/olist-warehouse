"""Object storage behind one small interface: a GCS bucket or a local folder."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    def exists(self, key: str) -> bool: ...
    def list_keys(self, prefix: str) -> list[str]: ...
    def read_bytes(self, key: str) -> bytes: ...
    def write_bytes(self, key: str, data: bytes) -> None: ...
    def upload_file(self, path: Path, key: str) -> None: ...
    def md5(self, key: str) -> str: ...
    def uri(self, key: str) -> str: ...


def _md5_base64(data: bytes) -> str:
    """Same format GCS reports in blob.md5_hash, so manifests compare across sinks."""
    return base64.b64encode(hashlib.md5(data).digest()).decode()


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_keys(self, prefix: str) -> list[str]:
        base = self._path(prefix)
        if not base.is_dir():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file())

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def upload_file(self, path: Path, key: str) -> None:
        self.write_bytes(key, path.read_bytes())

    def md5(self, key: str) -> str:
        return _md5_base64(self.read_bytes(key))

    def uri(self, key: str) -> str:
        return str(self._path(key))


class GcsStorage:
    def __init__(self, bucket_name: str, project_id: str) -> None:
        from google.cloud import storage

        self.bucket = storage.Client(project=project_id).bucket(bucket_name)

    def exists(self, key: str) -> bool:
        return self.bucket.blob(key).exists()

    def list_keys(self, prefix: str) -> list[str]:
        return sorted(b.name for b in self.bucket.list_blobs(prefix=prefix))

    def read_bytes(self, key: str) -> bytes:
        return self.bucket.blob(key).download_as_bytes()

    def write_bytes(self, key: str, data: bytes) -> None:
        self.bucket.blob(key).upload_from_string(data)

    def upload_file(self, path: Path, key: str) -> None:
        self.bucket.blob(key).upload_from_filename(str(path))

    def md5(self, key: str) -> str:
        blob = self.bucket.get_blob(key)
        if blob is None:
            raise FileNotFoundError(self.uri(key))
        return blob.md5_hash

    def uri(self, key: str) -> str:
        return f"gs://{self.bucket.name}/{key}"
