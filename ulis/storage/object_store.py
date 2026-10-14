"""Private, content-addressed storage for raw source documents.

Raw bytes are stored exactly as retrieved and addressed by their SHA-256 digest, which
makes storage idempotent and lets any later reader verify integrity.  The local
implementation is for development and tests; production deployments implement the same
protocol over private object storage (e.g. an S3-compatible bucket with versioning,
server-side encryption and no public access).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Protocol

HEX = set("0123456789abcdef")


class IntegrityError(RuntimeError):
    pass


class ObjectStore(Protocol):
    def put(self, data: bytes) -> str: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalObjectStore:
    """Stores objects under ``<root>/sha256/ab/cd/<digest>`` with owner-only permissions."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, key: str) -> Path:
        algo, _, digest = key.partition(":")
        if algo != "sha256" or len(digest) != 64 or not set(digest) <= HEX:
            raise ValueError(f"invalid object key {key!r}")
        return self.root / "sha256" / digest[:2] / digest[2:4] / digest

    def put(self, data: bytes) -> str:
        key = f"sha256:{sha256_hex(data)}"
        path = self._path(key)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = path.with_suffix(".tmp")
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        return key

    def get(self, key: str) -> bytes:
        data = self._path(key).read_bytes()
        if f"sha256:{sha256_hex(data)}" != key:
            raise IntegrityError(f"stored object {key} does not match its digest")
        return data

    def exists(self, key: str) -> bool:
        return self._path(key).exists()
