"""Private, content-addressed storage for raw source documents.

Raw bytes are addressed by the SHA-256 digest of the bytes exactly as retrieved, which makes storage
idempotent and lets any later reader verify integrity.  The local store keeps each object
zstd-compressed on disk (lossless: :meth:`LocalObjectStore.get` returns the original bytes and checks
them against the digest before returning them); the key never changes with the compression.  The local
implementation is for development, tests and single-server deployments; other deployments implement the
same protocol over private object storage (e.g. an S3-compatible bucket with versioning, server-side
encryption and no public access).
"""

from __future__ import annotations

import hashlib
import itertools
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import zstandard

from ulis.settings import DEFAULT_OBJECT_COMPRESSION_LEVEL as DEFAULT_COMPRESSION_LEVEL
from ulis.settings import MAX_OBJECT_COMPRESSION_LEVEL as MAX_COMPRESSION_LEVEL
from ulis.settings import Settings

HEX = set("0123456789abcdef")
ZST_SUFFIX = ".zst"
# Leftover temporary files (a write interrupted by a crash) older than this are removed by `compact-objects`.
STALE_TMP_SECONDS = 24 * 3600
COMPACT_BATCH = 1000


class IntegrityError(RuntimeError):
    pass


class ObjectStore(Protocol):
    def put(self, data: bytes) -> str: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_compression_level(level: int) -> int:
    if not 0 <= level <= MAX_COMPRESSION_LEVEL:
        raise ValueError(f"object compression level must be 0 (uncompressed) to {MAX_COMPRESSION_LEVEL}, got {level}")
    return level


def compress(data: bytes, level: int) -> bytes:
    """One zstd frame with the content size and a checksum of its own (the SHA-256 check comes on top)."""
    return zstandard.ZstdCompressor(level=level, write_checksum=True, write_content_size=True).compress(data)


def decompress(blob: bytes) -> bytes:
    """The bytes of a zstd frame (or of concatenated frames); :class:`IntegrityError` when it is not valid zstd."""
    dctx = zstandard.ZstdDecompressor()
    try:
        try:
            return dctx.decompress(blob)
        except zstandard.ZstdError:
            # A frame written without its content size (e.g. by a streaming tool): decode it as a stream.
            return dctx.stream_reader(blob, read_across_frames=True).read()
    except zstandard.ZstdError as exc:
        raise IntegrityError(f"stored object is not a valid zstd frame: {exc}") from exc


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@dataclass
class CompactReport:
    objects: int = 0
    converted: int = 0  # legacy uncompressed objects written compressed
    recompressed: int = 0  # compressed objects rewritten smaller at the requested level
    unchanged: int = 0
    duplicates_removed: int = 0  # an uncompressed copy left next to a verified compressed one (an interrupted run)
    stale_tmp_removed: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "objects": self.objects,
            "converted": self.converted,
            "recompressed": self.recompressed,
            "unchanged": self.unchanged,
            "duplicates_removed": self.duplicates_removed,
            "stale_tmp_removed": self.stale_tmp_removed,
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "errors": self.errors,
        }


class LocalObjectStore:
    """Stores objects under ``<root>/sha256/ab/cd/<digest>.zst`` (zstd) with owner-only permissions.

    ``<digest>`` is the SHA-256 of the ORIGINAL bytes. Objects written before compression existed (or with
    ``compression_level=0``) are plain files named ``<digest>``; both kinds are read, and ``compact-objects``
    converts the plain ones in place.
    """

    def __init__(self, root: Path, compression_level: int = DEFAULT_COMPRESSION_LEVEL):
        self.root = root
        self.compression_level = check_compression_level(compression_level)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    # ------------------------------------------------------------------ paths
    @staticmethod
    def _digest(key: str) -> str:
        algo, _, digest = key.partition(":")
        if algo != "sha256" or len(digest) != 64 or not set(digest) <= HEX:
            raise ValueError(f"invalid object key {key!r}")
        return digest

    def _path(self, key: str) -> Path:
        """The plain (uncompressed, legacy) file of an object."""
        digest = self._digest(key)
        return self.root / "sha256" / digest[:2] / digest[2:4] / digest

    def _zst_path(self, key: str) -> Path:
        plain = self._path(key)
        return plain.with_name(plain.name + ZST_SUFFIX)

    def locate(self, key: str) -> Path | None:
        """The file holding the object (the compressed one when both exist), or None."""
        for path in (self._zst_path(key), self._path(key)):
            if path.exists():
                return path
        return None

    # ------------------------------------------------------------------ protocol
    def put(self, data: bytes) -> str:
        key = f"sha256:{sha256_hex(data)}"
        if self.exists(key):
            return key
        if self.compression_level:
            self._write_atomic(self._zst_path(key), compress(data, self.compression_level))
        else:
            self._write_atomic(self._path(key), data)
        return key

    def get(self, key: str) -> bytes:
        zst, plain = self._zst_path(key), self._path(key)
        # The last attempt covers `compact-objects` replacing the plain file by the compressed one meanwhile.
        for path, compressed in ((zst, True), (plain, False), (zst, True)):
            try:
                blob = path.read_bytes()
            except FileNotFoundError:
                continue
            data = decompress(blob) if compressed else blob
            if f"sha256:{sha256_hex(data)}" != key:
                raise IntegrityError(f"stored object {key} does not match its digest")
            return data
        raise FileNotFoundError(f"object {key} not found under {self.root}")

    def exists(self, key: str) -> bool:
        return self._zst_path(key).exists() or self._path(key).exists()

    # ------------------------------------------------------------------ maintenance
    @staticmethod
    def _write_atomic(path: Path, payload: bytes, *, durable: bool = False) -> None:
        """Write to a unique temporary file in the same directory, then rename it over ``path`` (atomic)."""
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                if durable:
                    fh.flush()
                    os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if durable:
            _fsync_dir(path.parent)

    def iter_keys(self) -> Iterator[str]:
        """Every object key in the store, once (whether its file is compressed, plain or both)."""
        base = self.root / "sha256"
        if not base.is_dir():
            return
        for first in sorted(base.iterdir()):
            if not first.is_dir() or first.is_symlink():
                continue
            for second in sorted(first.iterdir()):
                if not second.is_dir() or second.is_symlink():
                    continue
                seen: set[str] = set()
                for entry in sorted(second.iterdir()):
                    digest = entry.name.removesuffix(ZST_SUFFIX)
                    if len(digest) == 64 and set(digest) <= HEX and digest not in seen and entry.is_file():
                        seen.add(digest)
                        yield f"sha256:{digest}"

    def compact_object(self, key: str, level: int, *, recompress: bool = False) -> tuple[str, int, int]:
        """Store one object compressed at ``level``; returns (outcome, bytes before, bytes after).

        Nothing is replaced before the new file has been written, synced, read back from disk and its
        decompressed bytes checked against the digest. Outcomes: ``converted``, ``recompressed``, ``unchanged``,
        ``duplicate_removed``. Raises :class:`IntegrityError` (and changes nothing) when the stored bytes do not
        match the key.
        """
        check_compression_level(level)
        if level == 0:
            raise ValueError("compaction needs a compression level of 1 or more")
        zst, plain = self._zst_path(key), self._path(key)
        zst_size = zst.stat().st_size if zst.exists() else 0
        plain_size = plain.stat().st_size if plain.exists() else 0
        before = zst_size + plain_size
        if zst_size and plain_size:
            # An interrupted earlier run: keep the compressed copy if it is sound, else rebuild it from the plain one.
            try:
                self._read_verified(zst, key, compressed=True)
            except IntegrityError:
                zst.unlink()
            else:
                plain.unlink()
                _fsync_dir(plain.parent)
                if not recompress:
                    return "duplicate_removed", before, zst_size
                plain_size, before = 0, zst_size
        if zst.exists():
            if not recompress:
                return "unchanged", before, before
            data = self._read_verified(zst, key, compressed=True)
            candidate = compress(data, level)
            if len(candidate) >= zst_size:
                return "unchanged", before, before
            self._replace_verified(zst, candidate, key)
            return "recompressed", before, len(candidate)
        if not plain.exists():
            raise FileNotFoundError(f"object {key} not found under {self.root}")
        data = self._read_verified(plain, key, compressed=False)
        self._replace_verified(zst, compress(data, level), key)
        plain.unlink()
        _fsync_dir(plain.parent)
        return "converted", before, zst.stat().st_size

    def _read_verified(self, path: Path, key: str, *, compressed: bool) -> bytes:
        blob = path.read_bytes()
        data = decompress(blob) if compressed else blob
        if f"sha256:{sha256_hex(data)}" != key:
            raise IntegrityError(f"stored object {key} ({path.name}) does not match its digest")
        return data

    def _replace_verified(self, path: Path, payload: bytes, key: str) -> None:
        if f"sha256:{sha256_hex(decompress(payload))}" != key:  # never write what would not read back
            raise IntegrityError(f"compressing {key} did not round-trip")
        self._write_atomic(path, payload, durable=True)
        self._read_verified(path, key, compressed=True)  # what is on disk now, not what was in memory

    def remove_stale_tmp(self, older_than: float = STALE_TMP_SECONDS, now: float | None = None) -> int:
        """Delete temporary files left by interrupted writes (``*.tmp`` older than ``older_than`` seconds)."""
        base = self.root / "sha256"
        if not base.is_dir():
            return 0
        cutoff = (time.time() if now is None else now) - older_than
        removed = 0
        for path in base.glob("*/*/*.tmp"):
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def compact(
        self,
        level: int,
        *,
        recompress: bool = False,
        jobs: int = 1,
        progress: Callable[[int], None] | None = None,
    ) -> CompactReport:
        """Convert every plain object to zstd at ``level`` (and, with ``recompress``, rewrite compressed objects that
        get smaller at that level). Idempotent and resumable: each object is replaced only after its new file was
        verified, so an interrupted run leaves every object readable and a re-run continues where it stopped."""
        check_compression_level(level)
        if level == 0:
            raise ValueError("compaction needs a compression level of 1 or more")
        report = CompactReport(stale_tmp_removed=self.remove_stale_tmp())

        def one(key: str) -> tuple[str, str, int, int]:
            try:
                outcome, before, after = self.compact_object(key, level, recompress=recompress)
            except (IntegrityError, OSError) as exc:
                size = sum(p.stat().st_size for p in (self._zst_path(key), self._path(key)) if p.exists())
                return key, f"error: {exc}", size, size
            return key, outcome, before, after

        counters = {
            "converted": "converted",
            "recompressed": "recompressed",
            "unchanged": "unchanged",
            "duplicate_removed": "duplicates_removed",
        }
        keys = self.iter_keys()
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            while batch := list(itertools.islice(keys, COMPACT_BATCH)):  # bounded memory on a store of millions
                for key, outcome, before, after in pool.map(one, batch):
                    report.objects += 1
                    report.bytes_before += before
                    report.bytes_after += after
                    if outcome.startswith("error: "):
                        report.errors.append(f"{key}: {outcome.removeprefix('error: ')}")
                    else:
                        attr = counters[outcome]
                        setattr(report, attr, getattr(report, attr) + 1)
                    if progress is not None:
                        progress(report.objects)
        return report


def open_object_store(settings: Settings) -> LocalObjectStore:
    """The object store the settings describe (directory and compression level)."""
    return LocalObjectStore(settings.object_store_dir, compression_level=settings.object_compression_level)
