"""Fetches documents from a public git repository pinned to one commit.

The manifest names the repository, the exact commit and every file with its SHA-256.
The connector clones (shallow, that commit only) into a private cache, refuses any file
whose hash differs from the pin, and records the commit-qualified URL as provenance.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess  # noqa: S404 - fixed argv, no shell
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_REPO = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _git(args: list[str], cwd: Path) -> str:
    env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,  # noqa: S603, S607
        timeout=900,
    )
    return result.stdout.strip()


class GitSnapshotConnector:
    def __init__(self, manifest_path: Path, expected_source_id: str, cache_dir: Path):
        self.manifest_path = manifest_path.resolve()
        self.expected_source_id = expected_source_id
        self.cache_dir = cache_dir

    def _manifest(self) -> dict:  # type: ignore[type-arg]
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not _REPO.match(manifest.get("repository", "")) or not _COMMIT.match(manifest.get("commit", "")):
            raise ManifestError("manifest needs an https://github.com/<owner>/<repo> URL and a full commit hash")
        return manifest

    def checkout(self) -> Path:
        manifest = self._manifest()
        repo, commit = manifest["repository"], manifest["commit"]
        target = self.cache_dir / repo.removeprefix("https://github.com/").replace("/", "__")
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not (target / ".git").exists():
            _git(["init", "-q"], target)
            _git(["remote", "add", "origin", repo], target)
        try:
            head = _git(["rev-parse", "HEAD"], target)
        except subprocess.CalledProcessError:
            head = ""
        if head != commit:
            _git(["fetch", "-q", "--depth", "1", "origin", commit], target)
            _git(["checkout", "-q", "--detach", commit], target)
        if _git(["rev-parse", "HEAD"], target) != commit:
            raise ManifestError("checked-out commit does not match the manifest")
        return target

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        root = self.checkout().resolve()
        for entry in manifest["documents"]:
            path = (root / entry["path"]).resolve()
            if root not in path.parents:
                raise ManifestError(f"path escapes repository: {entry['path']}")
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if digest != entry["sha256"]:
                raise ManifestError(f"{entry['path']}: sha256 {digest} does not match manifest pin")
            yield RawDocument(
                external_id=entry["external_id"],
                uri=f"{manifest['repository']}/blob/{manifest['commit']}/{entry['path']}",
                media_type=entry.get("media_type", "application/xml"),
                content=content,
                retrieved_at=datetime.now(UTC),
                metadata={"repository": manifest["repository"], "commit": manifest["commit"], "path": entry["path"]},
            )
