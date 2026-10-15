"""Polite fetcher for GovInfo's Bulk Data Repository (https://www.govinfo.gov/bulkdata/).

One connector, two sources:

* ``us-govinfo-bulk-statutes``: the Statutes at Large (``STATUTE/<volume>/STATUTE-<volume>.xml``, one file per
  volume), public and private laws (``PLAW/<congress>/{public,private}/PLAW-<congress>-{public,private}.zip``) and the
  statute compilations (``COMPS/COMPS.zip``);
* ``us-govinfo-bulk-cfr-fr``: the annual editions of the Code of Federal Regulations (one zip per title and year,
  ``CFR/<year>/title-<n>/CFR-<year>-title-<n>.zip``) and the Federal Register (one zip per month,
  ``FR/<year>/<month>/FR-<year>-<month>.zip``).

The manifest lists the files (``scripts/build_govinfo_manifests.py`` writes it from the repository's own directory
listings). A file of kind ``xml`` is yielded as received. A file of kind ``zip`` is downloaded once and each ``.xml``
member is yielded as its own document, byte for byte as it is inside the archive, so that one amended act or one CFR
volume is one stored source document and the archive is never held parsed in memory.

Rules (docs/onboarding-a-jurisdiction.md): ``robots.txt`` is read first and applied to every URL (it disallows
neither ``/bulkdata/`` nor the sitemaps, and sets no Crawl-delay); an identifying User-Agent; at least 5 s between
requests; HTTPS to ``www.govinfo.gov`` only; everything cached under ``<cache_dir>/www.govinfo.gov/``. A file that is
missing (404) or cannot be fetched is reported in ``fetch_errors`` and the run goes on; a refusal (401/403/429...) or a
path that robots.txt disallows stops it. Nothing outside the collections of the source is ever requested: the manifest
is checked against a pattern per source.
"""

from __future__ import annotations

import hashlib
import io
import re
import time
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.govinfo import BASE_URL, GovInfoClient, load_manifest
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError

MAX_MEMBER_BYTES = 512 * 1024 * 1024  # largest XML member read from an archive
MAX_MEMBERS = 20_000
MAX_UNPACKED_BYTES = 8 * 1024**3
_MEMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.xml$")


def _looks_like_xml(data: bytes) -> bool:
    """An XML document, not the HTML error or block page a server may send in its place."""
    head = data[:300].lstrip(b"\xef\xbb\xbf").lstrip().lower()
    return head.startswith(b"<") and not head.startswith((b"<!doctype html", b"<html"))


def _natural(name: str) -> list[Any]:
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name)]


class GovInfoBulkConnector:
    # Paths (below the host) a manifest of this source may list.
    allowed: ClassVar[tuple[re.Pattern[str], ...]] = ()

    def __init__(
        self,
        manifest_path: Path,
        expected_source_id: str,
        cache_dir: Path,
        *,
        offline: bool = False,
        sleep: Any = time.sleep,
    ) -> None:
        self.manifest_path = manifest_path
        self.expected_source_id = expected_source_id
        self.cache_dir = cache_dir
        self.offline = offline
        self._sleep = sleep
        self.client: GovInfoClient | None = None
        self.fetch_errors: list[str] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = load_manifest(self.manifest_path, self.expected_source_id)
        files = manifest.get("files") or []
        if not files:
            raise ManifestError("the manifest lists no file")
        seen: set[str] = set()
        for entry in files:
            path = entry.get("path")
            if not isinstance(path, str) or not any(p.fullmatch(path) for p in self.allowed):
                raise ManifestError(f"{path!r} is not a path of this source's collections")
            if path in seen:
                raise ManifestError(f"{path} is listed twice")
            seen.add(path)
            if entry.get("kind") not in ("xml", "zip"):
                raise ManifestError(f"{path}: kind must be 'xml' or 'zip'")
            if path.endswith(".zip") != (entry["kind"] == "zip"):
                raise ManifestError(f"{path}: kind {entry['kind']!r} does not match the file name")
            pin = entry.get("sha256")
            if pin is not None and not re.fullmatch(r"[0-9a-f]{64}", str(pin)):
                raise ManifestError(f"{path}: sha256 must be 64 hexadecimal characters")
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.fetch_errors = []
        self.client = GovInfoClient(manifest, self.cache_dir, offline=self.offline, sleep=self._sleep)
        for entry in manifest["files"]:
            path = str(entry["path"])
            url = f"{BASE_URL}/{path}"
            try:
                got = self.client.get(url, cache_key=path)
            except FetchError as exc:
                if "refusal is respected" in str(exc) or "disallowed by" in str(exc):
                    raise  # a refusal, or a path robots.txt forbids: the run stops
                self.fetch_errors.append(f"{path}: {exc}")
                continue
            if got is None:
                self.fetch_errors.append(f"{url}: not found (404)")
                continue
            data, meta = got
            sha = hashlib.sha256(data).hexdigest()
            if entry.get("sha256") and sha != entry["sha256"]:
                raise ManifestError(f"{path}: sha256 does not match the manifest pin")
            extra = {k: v for k, v in entry.items() if k not in ("path", "kind", "sha256")}
            retrieved = datetime.fromisoformat(meta["retrieved_at"])
            if entry["kind"] == "xml":
                if not _looks_like_xml(data):
                    self.fetch_errors.append(
                        f"{url}: the response is not XML ({meta.get('content_type')}); not ingested"
                    )
                    continue
                yield RawDocument(
                    external_id=f"govinfo:{path.removeprefix('bulkdata/')}",
                    uri=meta.get("final_url") or url,
                    media_type="application/xml",
                    content=data,
                    retrieved_at=retrieved,
                    metadata={"path": path, **extra, "cached_fetch": meta},
                )
                continue
            yield from self._members(path, url, data, sha, meta, retrieved, extra)

    def _members(
        self,
        path: str,
        url: str,
        data: bytes,
        sha: str,
        meta: dict[str, Any],
        retrieved: datetime,
        extra: dict[str, Any],
    ) -> Iterator[RawDocument]:
        if not data.startswith(b"PK\x03\x04"):
            self.fetch_errors.append(f"{url}: the response is not a zip archive ({meta.get('content_type')})")
            return
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            self.fetch_errors.append(f"{url}: not a readable zip archive: {exc}")
            return
        with archive:
            members = sorted((i for i in archive.infolist() if not i.is_dir()), key=lambda i: _natural(i.filename))
            if len(members) > MAX_MEMBERS or sum(i.file_size for i in members) > MAX_UNPACKED_BYTES:
                self.fetch_errors.append(f"{url}: archive is larger than the limits ({len(members)} members)")
                return
            for info in members:
                name = info.filename
                if not _MEMBER.fullmatch(name):  # a path, a PDF, a text file: not a document of this source
                    continue
                if info.file_size > MAX_MEMBER_BYTES:
                    self.fetch_errors.append(f"{url}!{name}: larger than {MAX_MEMBER_BYTES} bytes; not ingested")
                    continue
                yield RawDocument(
                    external_id=f"govinfo:{path.removeprefix('bulkdata/')}!{name}",
                    uri=f"{url}#{name}",
                    media_type="application/xml",
                    content=archive.read(info),
                    retrieved_at=retrieved,
                    metadata={"path": path, "member": name, "archive_sha256": sha, **extra, "cached_fetch": meta},
                )


class GovInfoStatutesConnector(GovInfoBulkConnector):
    allowed = (
        re.compile(r"bulkdata/STATUTE/(?:[1-9]\d{0,2})/STATUTE-(?:[1-9]\d{0,2})\.xml"),
        re.compile(r"bulkdata/PLAW/\d{3}/(?:public|private)/PLAW-\d{3}-(?:public|private)\.zip"),
        re.compile(r"bulkdata/PLAW/\d{3}/(?:public|private)/PLAW-\d{3}(?:publ|pvtl)\d+\.xml"),
        re.compile(r"bulkdata/COMPS/COMPS(?:-\d+\.xml|\.zip)"),
    )

    def _manifest(self) -> dict[str, Any]:
        manifest = super()._manifest()
        for entry in manifest["files"]:
            m = re.fullmatch(r"bulkdata/STATUTE/(\d+)/STATUTE-(\d+)\.xml", entry["path"])
            if m and m.group(1) != m.group(2):
                raise ManifestError(f"{entry['path']}: the folder and the file name name different volumes")
        return manifest


class GovInfoCfrFrConnector(GovInfoBulkConnector):
    allowed = (
        re.compile(
            r"bulkdata/CFR/(?:19|20)\d{2}/title-(?:[1-9]|[1-4]\d|50)/CFR-(?:19|20)\d{2}-title-(?:[1-9]|[1-4]\d|50)\.zip"
        ),
        re.compile(r"bulkdata/FR/(?:19|20)\d{2}/(?:0[1-9]|1[0-2])/FR-(?:19|20)\d{2}-(?:0[1-9]|1[0-2])\.zip"),
        re.compile(
            r"bulkdata/FR/(?:19|20)\d{2}/(?:0[1-9]|1[0-2])/FR-(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\.xml"
        ),
    )

    def _manifest(self) -> dict[str, Any]:
        manifest = super()._manifest()
        for entry in manifest["files"]:
            m = re.fullmatch(r"bulkdata/CFR/(\d{4})/title-(\d+)/CFR-(\d{4})-title-(\d+)\.zip", entry["path"])
            if m and (m.group(1), m.group(2)) != (m.group(3), m.group(4)):
                raise ManifestError(f"{entry['path']}: the folder and the file name name different editions")
            m = re.fullmatch(r"bulkdata/FR/(\d{4})/(\d{2})/FR-(\d{4})-(\d{2})(?:-\d{2})?\.(?:zip|xml)", entry["path"])
            if m and (m.group(1), m.group(2)) != (m.group(3), m.group(4)):
                raise ManifestError(f"{entry['path']}: the folder and the file name name different months")
        return manifest
