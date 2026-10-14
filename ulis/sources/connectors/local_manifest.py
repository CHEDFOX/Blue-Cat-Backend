"""Reads documents listed in a local manifest, verifying each file's pinned SHA-256."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ulis.sources.connectors.base import RawDocument


class ManifestError(ValueError):
    pass


class LocalManifestConnector:
    def __init__(self, manifest_path: Path, expected_source_id: str):
        self.manifest_path = manifest_path.resolve()
        self.expected_source_id = expected_source_id

    def fetch(self) -> Iterator[RawDocument]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1:
            raise ManifestError("unsupported manifest schema_version")
        if manifest.get("source_id") != self.expected_source_id:
            raise ManifestError(f"manifest belongs to {manifest.get('source_id')!r}, not {self.expected_source_id!r}")
        base = self.manifest_path.parent
        for entry in manifest["documents"]:
            path = (base / entry["path"]).resolve()
            if base not in path.parents:
                raise ManifestError(f"path escapes manifest directory: {entry['path']}")
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if digest != entry.get("sha256"):
                raise ManifestError(f"{entry['path']}: sha256 {digest} does not match manifest pin")
            yield RawDocument(
                external_id=entry["external_id"],
                uri=f"manifest:{entry['path']}",
                media_type=entry["media_type"],
                content=content,
                retrieved_at=datetime.now(UTC),
                metadata={"path": entry["path"]},
            )
