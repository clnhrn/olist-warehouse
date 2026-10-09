"""Object storage behind one small interface: a GCS bucket or a local folder."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    """Key-value object storage. Keys are `/`-separated paths, like GCS names."""

    def exists(self, key: str) -> bool:
        """Check whether an object exists.

        Args:
            key: Object key.

        Returns:
            True if the object exists.
        """
        ...

    def list_keys(self, prefix: str) -> list[str]:
        """List the keys under a prefix.

        Args:
            prefix: Key prefix, such as `landing/`.

        Returns:
            Matching keys, sorted.
        """
        ...

    def read_bytes(self, key: str) -> bytes:
        """Read an object.

        Args:
            key: Object key.

        Returns:
            The object's content.
        """
        ...

    def write_bytes(self, key: str, data: bytes) -> None:
        """Write an object, replacing any existing one.

        Args:
            key: Object key.
            data: Content to write.
        """
        ...

    def upload_file(self, path: Path, key: str) -> None:
        """Copy a local file into storage.

        Args:
            path: Local file to copy.
            key: Destination key.
        """
        ...

    def md5(self, key: str) -> str:
        """Get an object's MD5 hash.

        Args:
            key: Object key.

        Returns:
            Base64-encoded MD5, the format GCS reports.
        """
        ...

    def uri(self, key: str) -> str:
        """Build a human-readable location for an object, for logs and lineage.

        Args:
            key: Object key.

        Returns:
            A `gs://` URI or a local path.
        """
        ...


def _md5_base64(data: bytes) -> str:
    """Same format GCS reports in blob.md5_hash, so manifests compare across sinks."""
    return base64.b64encode(hashlib.md5(data).digest()).decode()


class LocalStorage:
    """`Storage` on a local folder, with keys as relative paths."""

    def __init__(self, root: Path) -> None:
        """Create local storage.

        Args:
            root: Folder that keys are relative to.
        """
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key

    def exists(self, key: str) -> bool:
        """See `Storage.exists`."""
        return self._path(key).is_file()

    def list_keys(self, prefix: str) -> list[str]:
        """See `Storage.list_keys`. The prefix must be a folder."""
        base = self._path(prefix)
        if not base.is_dir():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file())

    def read_bytes(self, key: str) -> bytes:
        """See `Storage.read_bytes`."""
        return self._path(key).read_bytes()

    def write_bytes(self, key: str, data: bytes) -> None:
        """See `Storage.write_bytes`. Creates parent folders as needed."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def upload_file(self, path: Path, key: str) -> None:
        """See `Storage.upload_file`."""
        self.write_bytes(key, path.read_bytes())

    def md5(self, key: str) -> str:
        """See `Storage.md5`."""
        return _md5_base64(self.read_bytes(key))

    def uri(self, key: str) -> str:
        """See `Storage.uri`."""
        return str(self._path(key))


class GcsStorage:
    """`Storage` on a GCS bucket."""

    def __init__(self, bucket_name: str, project_id: str) -> None:
        """Create a client for a bucket.

        Args:
            bucket_name: Bucket name, without `gs://`.
            project_id: GCP project the client bills to.
        """
        from google.cloud import storage

        self.bucket = storage.Client(project=project_id).bucket(bucket_name)

    def exists(self, key: str) -> bool:
        """See `Storage.exists`."""
        return self.bucket.blob(key).exists()

    def list_keys(self, prefix: str) -> list[str]:
        """See `Storage.list_keys`."""
        return sorted(b.name for b in self.bucket.list_blobs(prefix=prefix))

    def read_bytes(self, key: str) -> bytes:
        """See `Storage.read_bytes`."""
        return self.bucket.blob(key).download_as_bytes()

    def write_bytes(self, key: str, data: bytes) -> None:
        """See `Storage.write_bytes`."""
        self.bucket.blob(key).upload_from_string(data)

    def upload_file(self, path: Path, key: str) -> None:
        """See `Storage.upload_file`."""
        self.bucket.blob(key).upload_from_filename(str(path))

    def md5(self, key: str) -> str:
        """See `Storage.md5`.

        Raises:
            FileNotFoundError: If the object does not exist.
        """
        blob = self.bucket.get_blob(key)
        if blob is None:
            raise FileNotFoundError(self.uri(key))
        return blob.md5_hash

    def uri(self, key: str) -> str:
        """See `Storage.uri`."""
        return f"gs://{self.bucket.name}/{key}"
