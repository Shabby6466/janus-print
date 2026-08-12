"""Encrypted archive of every print job body.

Threat model (PLAN.md §6): this store is a searchable copy of everything the company
prints. It is a higher-value target than most of what it protects. So:

  * every object gets its own random content key
  * that key is encrypted under a master key and stored in the DB, never beside the object
  * an attacker who walks off with the bucket gets ciphertext and nothing else
  * reads are logged by the caller (see archive.access)
"""

from __future__ import annotations

import abc
import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config import Settings, get_settings


def _master_fernet(settings: Settings) -> Fernet:
    """Derive a stable Fernet key from the configured master secret."""
    if settings.archive_master_key == "INSECURE-DEV-KEY-CHANGE-ME" and not settings.dev_mode:
        raise RuntimeError(
            "JANUS_PRINT_ARCHIVE_MASTER_KEY is still the default. Set a real key, or set "
            "JANUS_PRINT_DEV_MODE=true if this is the lab."
        )
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"janus-print/archive/v1",
        info=b"master-key-wrap",
    ).derive(settings.archive_master_key.encode())
    import base64

    return Fernet(base64.urlsafe_b64encode(raw))


class Blobs(abc.ABC):
    """Bytes in, bytes out. Encryption happens above this layer."""

    @abc.abstractmethod
    def put(self, key: str, data: bytes) -> None: ...

    @abc.abstractmethod
    def get(self, key: str) -> bytes: ...

    @abc.abstractmethod
    def delete(self, key: str) -> None: ...

    @abc.abstractmethod
    def exists(self, key: str) -> bool: ...


class FilesystemBlobs(Blobs):
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Keys are generated internally (date/uuid); reject traversal anyway.
        if ".." in key or key.startswith("/"):
            raise ValueError(f"unsafe archive key: {key!r}")
        return self.root / key

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Blobs(Blobs):
    def __init__(self, settings: Settings) -> None:
        import boto3

        self.bucket = settings.archive_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint or None,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False


class ArchiveStore:
    """Envelope-encrypted document store."""

    def __init__(self, settings: Settings | None = None, blobs: Blobs | None = None) -> None:
        self.settings = settings or get_settings()
        if blobs is not None:
            self.blobs = blobs
        elif self.settings.archive_backend == "s3":
            self.blobs = S3Blobs(self.settings)
        else:
            self.blobs = FilesystemBlobs(self.settings.archive_path)
        self._wrapper = _master_fernet(self.settings)

    def store(self, job_id: str, data: bytes) -> tuple[str, bytes, str, datetime]:
        """Encrypt and persist. Returns (key, wrapped_key, sha256, purge_after)."""
        digest = hashlib.sha256(data).hexdigest()
        content_key = Fernet.generate_key()
        ciphertext = Fernet(content_key).encrypt(data)

        now = datetime.now(UTC)
        key = f"{now:%Y/%m/%d}/{job_id}.bin"
        self.blobs.put(key, ciphertext)

        wrapped = self._wrapper.encrypt(content_key)
        purge_after = now + timedelta(days=self.settings.archive_retention_days)
        return key, wrapped, digest, purge_after

    def load(self, key: str, wrapped_key: bytes) -> bytes:
        content_key = self._wrapper.decrypt(wrapped_key)
        return Fernet(content_key).decrypt(self.blobs.get(key))

    def delete(self, key: str) -> None:
        self.blobs.delete(key)


_store: ArchiveStore | None = None


def get_archive() -> ArchiveStore:
    global _store
    if _store is None:
        _store = ArchiveStore()
    return _store


def reset_archive() -> None:
    """Test hook."""
    global _store
    _store = None
