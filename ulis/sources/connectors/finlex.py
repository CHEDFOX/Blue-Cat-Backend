"""Consolidated Finnish statutes from Finlex open data: the daily download package, or the API one document at a time.

Both routes are Finlex's own (https://www.finlex.fi/en/open-data, licence CC BY 4.0) and serve the same Akoma Ntoso
documents: each consolidated statute (``statute-consolidated``) in each language and *temporal version*. A temporal
version is named after the amending statute it incorporates: ``fin@20260380`` is the Finnish text as amended by statute
380/2026, ``fin@20181050`` the text as originally enacted (statute 1050/2018).

**Package** (``mode: package``, the committed manifest's default for a full load). Finlex publishes the whole collection
as one zip archive, ``https://www.finlex.fi/files/open-data/archives/statute-consolidated.zip`` (about 4.4 GB, rebuilt
daily; https://www.finlex.fi/en/open-data/download-materials). It is downloaded into the cache (resumable; see
:meth:`FinlexConnector.download_package`) and read in place as a zip, never extracted. With ``package.pin: latest``
(committed) a run loads the package Finlex serves at that moment: a cached copy is reused while one HEAD request shows
the same ETag, otherwise (or when the cached copy was pruned) the current package is downloaded; its size, SHA-256,
ETag, Last-Modified and retrieval time are recorded in the cache's ``.meta.json`` and in every document's metadata, and
one run reads one package file only. With ``package.pin: exact`` (``bytes``, ``sha256``) only that package is accepted,
for reproducible runs. Its members are
``akn/fi/act/statute-consolidated/<year>/<number>/<fin|swe>@<version>/main.xml``: ``fin@`` with no version is the
statute as enacted (its ``FRBRversionNumber`` is the statute's own number) and ``fin@<version>`` each later temporal
version, so the latest version is the highest-numbered member, or ``fin@`` when there is no other (checked against the
API's ``@latest``: see docs/jurisdictions/fi.md). Only the works and languages the manifest lists are read, and each
yielded document is the member's ``main.xml`` exactly as stored (the parser reads nothing else; images and PDFs in the
package are not used). Where the package is behind the API the document comes from the API instead, as the API route
would fetch it (reported in ``package_fallbacks``): a work or language the package does not hold, a latest version
whose own metadata (or that of another of the work's members) names a newer amending statute that has no member yet
(the package lags the API by days for new amendments), and earlier temporal versions of a ``history`` work that the
package lacks.

**API** (``mode: api``, or ``FinlexConnector(..., mode="api")``; for refreshing single works).
``https://opendata.finlex.fi/finlex/avoindata/v1`` (documented at
https://www.finlex.fi/en/open-data/integration-quick-guide). For every work in the manifest the connector fetches the
latest version in each listed language and, if ``history`` is set, every earlier temporal version: the versions named
after the amending statutes the latest version lists (``finlex:amendedBy``) and after the statute itself. A version
the API does not hold (404) is skipped and reported in the run metadata; nothing is inferred in its place.

Rules from the API documentation are built in through :class:`PoliteHttp`: a User-Agent
header on every call (required by the API), ``Accept-Encoding: gzip`` (recommended), HTTPS
only to ``opendata.finlex.fi`` (and ``www.finlex.fi`` for the package), one connection, the manifest's delay between
request starts (at least 1 s; neither host's robots.txt sets a Crawl-delay - each is read before the first request of
a run, and a Crawl-delay there would win - and no numeric limit is published), back-off on 429 (the API's rate-limit
response; the package download stops at the first 429 instead) and 5xx, any refusal stops the run,
and every response is cached so nothing is fetched twice. Responses arrive gzip-encoded; the
connector yields the decoded XML exactly as served.

The manifest may list every consolidated statute in force (``scripts/build_finlex_manifest.py`` pages the API's own
list endpoint), so a statute the API does not hold in a listed language (404) or cannot serve (a server error that
outlasts the back-off) is reported in ``fetch_errors`` and the run goes on. A work may name the languages the list
endpoint showed for it (``languages: [fin]``), which overrides the manifest's. A refusal (401, 403, 429, 451)
stops the run, and so do ``MAX_CONSECUTIVE_ERRORS`` works failing in a row.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime
from http.client import HTTPException
from pathlib import Path
from typing import Any, BinaryIO

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp, manifest_crawl_delay

API = "https://opendata.finlex.fi/finlex/avoindata/v1"
HOST = "opendata.finlex.fi"
PACKAGE_HOST = "www.finlex.fi"
PACKAGE_PATH = "files/open-data/archives/statute-consolidated.zip"
PACKAGE_URL = f"https://{PACKAGE_HOST}/{PACKAGE_PATH}"
MODES = ("api", "package")
CHUNK = 1 << 20
MAX_DOWNLOAD_FAILURES = 8  # interruptions in a row without progress before a download gives up (it resumes later)
LANGUAGES = ("fin", "swe")
MAX_CONSECUTIVE_ERRORS = 20
_REFUSAL = re.compile(r"HTTP (?:401|403|429|451)\b")
_NUMBER = re.compile(r"^\d{1,5}(?:-\d{3})?$")
_VERSION = re.compile(rb"<FRBRversionNumber value=\"(\d{8})\"")
_AMENDED_BY = re.compile(rb"<finlex:amendedBy>(.*?)</finlex:amendedBy>", re.S)
_AMENDING = re.compile(rb"<finlex:ref href=\"/akn/fi/act/statute/(\d{4})/(\d{1,4})\">")
_MEMBER = re.compile(r"^akn/fi/act/statute-consolidated/(\d{4})/(\d{1,5}(?:-\d{3})?)/(fin|swe)@(\d{8})?/main\.xml$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_AMENDMENT = re.compile(
    rb"<finlex:statuteReference>\s*<finlex:ref href=\"/akn/fi/act/statute/(\d{4})/(\d{1,4})\">[^<]*</finlex:ref>"
    rb"(?:\s*<finlex:inForce>\s*<finlex:dateEntryIntoForce date=\"(\d{4}-\d{2}-\d{2})\")?"
)

__all__ = [
    "PACKAGE_URL",
    "FetchError",
    "FinlexConnector",
    "PackageChanged",
    "history_version_ids",
    "package_index",
    "version_dates",
    "version_id",
]


def version_id(year: int | str, number: str) -> str:
    """Temporal version named after statute ``number/year``: 380/2026 -> ``20260380``."""
    return f"{year}{int(number):04d}"


def history_version_ids(latest: bytes, year: int, number: str) -> list[str]:
    """Earlier temporal versions named in a latest version: the statute itself and every
    amending statute listed in ``finlex:amendedBy``, oldest first."""
    current = _VERSION.search(latest)
    if current is None:
        return []
    ids: set[str] = set()
    if re.fullmatch(r"\d{1,4}", number):
        ids.add(version_id(year, number))
    block = _AMENDED_BY.search(latest)
    if block is not None:
        ids.update(version_id(y.decode(), n.decode()) for y, n in _AMENDING.findall(block.group(1)))
    return sorted(i for i in ids if i < current.group(1).decode())


def version_dates(latest: bytes) -> dict[str, str]:
    """Entry-into-force date (ISO) of each amending statute listed in a latest version, keyed by the
    temporal version named after it; used only to order the versions."""
    block = _AMENDED_BY.search(latest)
    if block is None:
        return {}
    return {version_id(y.decode(), n.decode()): d.decode() for y, n, d in _AMENDMENT.findall(block.group(1)) if d}


def _decode(data: bytes) -> bytes:
    return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


class PackageChanged(FetchError):
    """The publisher has replaced the download package since the manifest pinned it."""


class PackageMemberError(Exception):
    """A member of the package is not the document its path names (reported; the run goes on)."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK * 8):
            digest.update(block)
    return digest.hexdigest()


def _is_exact(pin: dict[str, Any]) -> bool:
    """``package.pin``: ``exact`` (the default; ``bytes`` and ``sha256`` given) or ``latest`` (what Finlex serves
    now)."""
    return pin.get("pin", "exact") != "latest"


def _same_package(meta: dict[str, Any], current: dict[str, Any]) -> bool:
    """Whether the cached package (its record) is the one a HEAD response describes: same ETag (else Last-Modified)
    and, when the response gives one, the same size."""
    if meta.get("etag") and current.get("etag"):
        same = meta["etag"] == current["etag"]
    elif meta.get("last_modified") and current.get("last_modified"):
        same = meta["last_modified"] == current["last_modified"]
    else:
        return False
    return same and (current.get("bytes") is None or current["bytes"] == meta.get("bytes"))


def _check_pin(meta: dict[str, Any], pin: dict[str, Any] | None) -> None:
    if pin is not None and (meta.get("bytes") != pin["bytes"] or meta.get("sha256") != pin["sha256"]):
        raise PackageChanged(
            f"the cached package ({meta.get('bytes')} bytes, SHA-256 {meta.get('sha256')}, Last-Modified "
            f"{meta.get('last_modified')}) is not the one the manifest pins ({pin['bytes']} bytes, SHA-256 "
            f"{pin['sha256']}); load the current package (package.pin: latest) or move the pin with "
            "`python -m scripts.finlex_package --replace --pin`"
        )


def package_index(archive: zipfile.ZipFile) -> dict[tuple[int, str, str], dict[str, str]]:
    """``(year, number, language) -> {version: member name}`` for every ``main.xml`` in the package (version ``""``
    is the member ``<lang>@/main.xml``, the statute as enacted), from the archive's central directory."""
    index: dict[tuple[int, str, str], dict[str, str]] = {}
    for name in archive.namelist():
        m = _MEMBER.match(name)
        if m:
            index.setdefault((int(m.group(1)), m.group(2), m.group(3)), {})[m.group(4) or ""] = name
    return index


def latest_member(members: dict[str, str]) -> str:
    """The version key of the latest member: the highest-numbered temporal version, or ``""`` (as enacted) alone."""
    numbered = [v for v in members if v]
    return max(numbered) if numbered else ""


def _head(archive: zipfile.ZipFile, name: str) -> bytes:
    """The start of a member up to the end of its ``<meta>`` block (where ``finlex:amendedBy`` is), decompressed."""
    data = b""
    with archive.open(name) as handle:
        while block := handle.read(1 << 16):
            data += block
            if b"</meta>" in data:
                break
    return data


def _amending_versions(data: bytes) -> set[str]:
    block = _AMENDED_BY.search(data)
    if block is None:
        return set()
    return {version_id(y.decode(), n.decode()) for y, n in _AMENDING.findall(block.group(1))}


class FinlexConnector:
    def __init__(
        self,
        manifest_path: Path,
        expected_source_id: str,
        cache_dir: Path,
        *,
        offline: bool = False,
        sleep: Any = time.sleep,
        mode: str | None = None,
        package_file: Path | None = None,
    ):
        self.manifest_path = manifest_path
        self.expected_source_id = expected_source_id
        self.cache_dir = cache_dir
        self.offline = offline
        self._sleep = sleep
        # Overrides the manifest's ``mode``: ``"api"`` fetches the listed works one document at a time (refreshing
        # single works), ``"package"`` reads them from the download package.
        self.mode = mode
        # A local copy of the package (tests); otherwise the package is downloaded into the cache.
        self._package_file = package_file
        self.missing_versions: list[str] = []
        self.fetch_errors: list[str] = []
        self.package_fallbacks: list[str] = []  # package mode: documents taken from the API instead, and why
        self.package_meta: dict[str, Any] | None = None
        self.download_events: list[str] = []
        self.http: PoliteHttp | None = None
        self._last_start = 0.0
        self._requests = 0
        self._package_handle: BinaryIO | None = None

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required (API documentation)")
        if manifest.get("base_url", API) != API:
            raise ManifestError(f"base_url must be {API}")
        for lang in manifest.get("languages", LANGUAGES):
            if lang not in LANGUAGES:
                raise ManifestError(f"unsupported language {lang!r}")
        seen: set[tuple[int, str]] = set()
        for work in manifest["works"]:
            if not isinstance(work.get("year"), int) or not _NUMBER.match(str(work.get("number", ""))):
                raise ManifestError(f"invalid work {work!r}")
            for lang in work.get("languages", ()):
                if lang not in LANGUAGES:
                    raise ManifestError(f"unsupported language {lang!r} in {work!r}")
            key = (work["year"], str(work["number"]))
            if key in seen:
                raise ManifestError(f"{work['number']}/{work['year']} is listed twice")
            seen.add(key)
        manifest_crawl_delay(manifest)
        mode = self.mode or manifest.get("mode", "api")
        if mode not in MODES:
            raise ManifestError(f"mode must be one of {MODES}, got {mode!r}")
        if mode == "package" or "package" in manifest:
            pin = manifest.get("package")
            if not isinstance(pin, dict) or pin.get("url") != PACKAGE_URL:
                raise ManifestError(f"mode package needs a package block whose url is {PACKAGE_URL}")
            if pin.get("pin", "exact") not in ("exact", "latest"):
                raise ManifestError("package.pin must be latest (what Finlex serves now) or exact")
            if not _is_exact(pin):
                if "bytes" in pin or "sha256" in pin:
                    raise ManifestError("package.pin latest takes no bytes or sha256: they are recorded on each run")
                approx = pin.get("approx_bytes")
                if approx is not None and (not isinstance(approx, int) or approx <= 0):
                    raise ManifestError("package.approx_bytes must be a size in bytes (for the load plan)")
                return manifest
            if not isinstance(pin.get("bytes"), int) or pin["bytes"] <= 0:
                raise ManifestError("package.bytes must be the package's size in bytes")
            if not _SHA256.match(str(pin.get("sha256", ""))):
                raise ManifestError("package.sha256 must be the package's SHA-256 (64 hex digits)")
            if not pin.get("last_modified") or not pin.get("retrieved_at"):
                raise ManifestError("package.last_modified and package.retrieved_at are required")
            datetime.fromisoformat(str(pin["retrieved_at"]))
        return manifest

    def _mode(self, manifest: dict[str, Any]) -> str:
        return str(self.mode or manifest.get("mode", "api"))

    # ------------------------------------------------------------------ download package

    def package_file(self) -> Path:
        """The package's place in the cache: ``<cache_dir>/www.finlex.fi/files/open-data/archives/<name>.zip``."""
        return self.cache_dir / PACKAGE_HOST / PACKAGE_PATH

    def download_package(self, manifest: dict[str, Any], *, check_pin: bool = True) -> tuple[Path, dict[str, Any]]:
        """The package in the cache, downloaded when needed (resumable); returns its path and its record
        (``.meta.json``: URL, size, SHA-256, ETag, Last-Modified, retrieval time).

        Politeness as for every request: robots.txt of www.finlex.fi is read first (its Crawl-delay, if any, is a floor
        and the package path must be allowed), the identifying User-Agent, HTTPS to www.finlex.fi only, one connection,
        at least the manifest's delay between request starts. An interrupted transfer resumes with a ``Range`` request
        guarded by ``If-Range`` (the response's ETag), so a package the publisher replaced in the meantime is fetched
        whole again instead of being spliced. 401/403/429/451 stop at once (a refusal is never retried); 5xx and broken
        connections are retried with back-off, at most ``MAX_DOWNLOAD_FAILURES`` times in a row without progress.

        ``package.pin: latest`` (the committed manifest): the package Finlex serves now. A cached package is reused when
        one HEAD request shows the same ETag (or Last-Modified) and size; otherwise it is deleted and the current one
        downloaded. A cached package that is gone (pruned) is simply downloaded again.

        ``package.pin: exact`` (``bytes`` and ``sha256`` given; reproducible runs): only that package is accepted - the
        publisher rebuilds it daily under the same URL, so a newer one is refused, not silently loaded. ``check_pin``
        False treats an exact manifest as ``latest`` (``scripts/finlex_package.py``).
        """
        pin = manifest.get("package") or {}
        exact = check_pin and _is_exact(pin)
        self.download_events = []
        dest = self.package_file()
        cached = self._cached_package()
        if cached is not None and exact:
            _check_pin(cached[1], pin)
            return cached
        if self.offline:
            if cached is not None:
                return cached
            raise FetchError(f"offline and not cached: {PACKAGE_URL}")
        user_agent = manifest["user_agent"]
        site = PoliteHttp(
            self.cache_dir,
            user_agent,
            allowed_hosts={PACKAGE_HOST},
            crawl_delay=manifest_crawl_delay(manifest),
            sleep=self._sleep,
            honour_robots_delay=True,
        )
        self._last_start = time.monotonic()
        robots = site.get(f"https://{PACKAGE_HOST}/robots.txt", refresh=True)  # also records its Crawl-delay
        if robots is not None and not robots_allows(robots[0].decode("utf-8", "replace"), user_agent, PACKAGE_URL):
            raise FetchError(f"{PACKAGE_URL}: disallowed by https://{PACKAGE_HOST}/robots.txt; not fetched")
        delay = site.delay_for(PACKAGE_HOST)
        self._requests = site.requests_made
        if cached is not None:
            current = self._head_package(user_agent, delay)
            if current is None:
                self.download_events.append("could not ask Finlex for its current package; using the cached one")
                return cached
            if _same_package(cached[1], current):
                return cached
            self.download_events.append(
                f"the cached package (ETag {cached[1].get('etag')}) is not the one Finlex serves now (ETag "
                f"{current.get('etag')}, Last-Modified {current.get('last_modified')}); downloading that one"
            )
            dest.unlink(missing_ok=True)  # one package on disk at a time
            dest.with_name(dest.name + ".meta.json").unlink(missing_ok=True)
        return self._download(user_agent, delay, pin if exact else None)

    def _cached_package(self) -> tuple[Path, dict[str, Any]] | None:
        """The cached package and its record; ``None`` when either is missing (pruned, or never downloaded). A package
        that changed on disk since it was recorded is hashed again and no longer counts as the publisher's response."""
        dest = self.package_file()
        meta_file = dest.with_name(dest.name + ".meta.json")
        if not (dest.exists() and meta_file.exists()):
            return None
        meta = json.loads(meta_file.read_text())
        stat = dest.stat()
        if stat.st_size != meta.get("bytes") or stat.st_mtime_ns != meta.get("mtime_ns"):
            meta.update(sha256=_sha256(dest), bytes=stat.st_size, mtime_ns=stat.st_mtime_ns, etag=None)
            meta["last_modified"] = None
        return dest, meta

    def _pause(self, delay: float) -> None:
        wait = self._last_start + delay - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._last_start = time.monotonic()
        self._requests += 1

    def _head_package(self, user_agent: str, delay: float) -> dict[str, Any] | None:
        """ETag, Last-Modified and size of the package Finlex serves now (one HEAD; retried on 5xx and broken
        connections). ``None`` when Finlex cannot be asked; a refusal stops the run."""
        for attempt in range(4):
            self._pause(delay if attempt == 0 else min(delay * 2**attempt, 300.0))
            request = urllib.request.Request(PACKAGE_URL, method="HEAD", headers={"User-Agent": user_agent})  # noqa: S310
            try:
                with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - the fixed https URL
                    return {
                        "etag": response.headers.get("ETag"),
                        "last_modified": response.headers.get("Last-Modified"),
                        "bytes": int(response.headers.get("Content-Length") or 0) or None,
                    }
            except urllib.error.HTTPError as exc:
                if exc.code in (500, 502, 503, 504):
                    continue
                if exc.code == 404:
                    raise FetchError(f"{PACKAGE_URL}: not found (404)") from exc
                raise FetchError(f"{PACKAGE_URL}: HTTP {exc.code} (not retried: a refusal is respected)") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError, HTTPException, ssl.SSLError):
                continue
        return None

    def _download(self, user_agent: str, delay: float, pin: dict[str, Any] | None) -> tuple[Path, dict[str, Any]]:
        dest = self.package_file()
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        part = dest.with_name(dest.name + ".part")
        part_meta = dest.with_name(dest.name + ".part.json")  # validators of the response the partial file comes from
        validator: dict[str, Any] = json.loads(part_meta.read_text()) if part.exists() and part_meta.exists() else {}
        if part.exists() and not (validator.get("etag") or validator.get("last_modified")):
            part.unlink()  # a partial file of unknown origin cannot be resumed safely
        total: int | None = validator.get("bytes")
        failures = 0
        started = time.monotonic()
        while True:
            have = part.stat().st_size if part.exists() else 0
            if total is not None and have == total:
                break
            self._pause(delay)
            headers = {"User-Agent": user_agent}
            if have:
                headers["Range"] = f"bytes={have}-"
                headers["If-Range"] = validator.get("etag") or validator["last_modified"]
            request = urllib.request.Request(PACKAGE_URL, headers=headers)  # noqa: S310 - the fixed https URL
            try:
                with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
                    final = urllib.parse.urlsplit(response.geturl())
                    if final.scheme != "https" or final.netloc != PACKAGE_HOST:
                        raise FetchError(f"{PACKAGE_URL}: redirected to {response.geturl()}; not followed")
                    if response.status == 206 and have:
                        first, _, size = response.headers.get("Content-Range", "").removeprefix("bytes ").partition("/")
                        if not first.startswith(f"{have}-"):
                            raise FetchError(f"{PACKAGE_URL}: asked for bytes {have}-, got {first or 'no range'}")
                        total, mode = int(size), "ab"
                    elif response.status == 200:
                        if have:
                            self.download_events.append("the package changed (ETag); starting again from byte 0")
                        total, mode = int(response.headers["Content-Length"]), "wb"
                        fresh = {
                            "etag": response.headers.get("ETag"),
                            "last_modified": response.headers.get("Last-Modified"),
                            "bytes": total,
                        }
                    else:
                        raise FetchError(f"{PACKAGE_URL}: unexpected HTTP {response.status}")
                    if pin is not None and total != pin["bytes"]:
                        raise PackageChanged(
                            f"{PACKAGE_URL} now has {total} bytes (Last-Modified "
                            f"{response.headers.get('Last-Modified')}); the manifest pins {pin['bytes']} bytes of "
                            f"{pin.get('last_modified')}: the publisher has replaced the package; load the current "
                            "one (package.pin: latest) or move the pin with `python -m scripts.finlex_package`"
                        )
                    with part.open(mode) as out:
                        if mode == "wb":  # the partial file and its validators are replaced together
                            validator = fresh
                            part_meta.write_text(json.dumps(validator))
                        shutil.copyfileobj(response, out, CHUNK)
            except urllib.error.HTTPError as exc:
                if exc.code == 416 and have and have == validator.get("bytes"):
                    total = have
                    continue
                if exc.code == 404:
                    raise FetchError(f"{PACKAGE_URL}: not found (404)") from exc
                if exc.code in (500, 502, 503, 504) and failures < MAX_DOWNLOAD_FAILURES:
                    failures += 1
                    self._sleep(min(delay * 2**failures, 300.0))
                    continue
                raise FetchError(f"{PACKAGE_URL}: HTTP {exc.code} (not retried: a refusal is respected)") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError, HTTPException, ssl.SSLError) as exc:
                progressed = part.exists() and part.stat().st_size > have
                failures = 0 if progressed else failures + 1
                kept = part.stat().st_size if part.exists() else 0
                self.download_events.append(f"interrupted at byte {kept} ({exc}); resuming")
                if failures >= MAX_DOWNLOAD_FAILURES:
                    raise FetchError(f"{PACKAGE_URL}: {exc} ({have} bytes kept; run again to resume)") from exc
                self._sleep(min(delay * 2**failures, 300.0))
                continue
        sha = _sha256(part)
        if pin is not None and sha != pin["sha256"]:
            raise FetchError(f"{PACKAGE_URL}: SHA-256 {sha} differs from the pinned {pin['sha256']}; not used")
        part.replace(dest)
        part_meta.unlink(missing_ok=True)
        stat = dest.stat()
        meta = {
            "url": PACKAGE_URL,
            "etag": validator.get("etag"),
            "last_modified": validator.get("last_modified"),
            "bytes": stat.st_size,
            "sha256": sha,
            "mtime_ns": stat.st_mtime_ns,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "requests": self._requests,
            "interruptions": sum(e.startswith("interrupted") for e in self.download_events),
            "download_seconds": round(time.monotonic() - started, 1),
        }
        dest.with_name(dest.name + ".meta.json").write_text(json.dumps(meta, indent=1))
        return dest, meta

    def _get(self, path: str) -> tuple[bytes, dict[str, Any]] | None:
        assert self.http is not None
        got = self.http.get(f"{API}/{path}", headers={"Accept": "application/xml", "Accept-Encoding": "gzip"})
        return (_decode(got[0]), got[1]) if got is not None else None

    def _raw(self, base: str, lang: str, data: bytes, meta: dict[str, Any]) -> RawDocument:
        match = _VERSION.search(data)
        version = match.group(1).decode() if match else "unknown"
        return RawDocument(
            external_id=f"finlex:{base}/{lang}@{version}",
            uri=f"{API}/akn/fi/act/{base}/{lang}@{version}",
            media_type="application/akn+xml",
            content=data,
            retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
            metadata={"work": base, "language": lang, "temporal_version": version, "fetched_url": meta["url"]},
        )

    def _versions(self, work: dict[str, Any], base: str, lang: str) -> list[RawDocument]:
        """The documents of one work in one language, in the order in which they took effect; empty when the
        API does not hold the work in that language (reported in ``fetch_errors``)."""
        latest = self._get(f"akn/fi/act/{base}/{lang}@latest")
        if latest is None:
            self.fetch_errors.append(f"finlex:{base}/{lang}: not found (404); skipped")
            return []
        versions = [self._raw(base, lang, *latest)]
        if work.get("history", False):
            # ``history_versions`` restricts the versions fetched (used for small test fixtures).
            wanted = work.get("history_versions")
            for vid in history_version_ids(latest[0], work["year"], str(work["number"])):
                if wanted is not None and vid not in {str(w) for w in wanted}:
                    continue
                got = self._get(f"akn/fi/act/{base}/{lang}@{vid}")
                if got is None:
                    self.missing_versions.append(f"{base}/{lang}@{vid}")
                    continue
                versions.append(self._raw(base, lang, *got))
        # Point-in-time versions are recorded in the order in which they took effect (amending
        # statutes do not always take effect in the order of their numbers); the version as
        # enacted, and any without a recorded date, first.
        dates = version_dates(latest[0])
        versions.sort(key=lambda r: (dates.get(r.metadata["temporal_version"], ""), r.metadata["temporal_version"]))
        return versions

    # ------------------------------------------------------------------ package route

    def _open_package(self, manifest: dict[str, Any]) -> zipfile.ZipFile:
        """The run's one package, opened once: every document of the run comes from this file, whose size and SHA-256
        (with its ETag, Last-Modified and retrieval time) go into each document's metadata."""
        pin = manifest["package"]
        if self._package_file is not None:  # a local copy (tests)
            path = self._package_file
            stat = path.stat()
            meta = {
                "bytes": stat.st_size,
                "sha256": _sha256(path),
                "mtime_ns": stat.st_mtime_ns,
                "etag": pin.get("etag"),
                "last_modified": pin.get("last_modified"),
                "retrieved_at": pin.get("retrieved_at") or datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            }
            if _is_exact(pin):
                _check_pin(meta, pin)
        else:
            path, meta = self.download_package(manifest)
        handle = path.open("rb")
        stat = os.fstat(handle.fileno())
        if stat.st_size != meta["bytes"] or stat.st_mtime_ns != meta.get("mtime_ns", stat.st_mtime_ns):
            handle.close()
            raise FetchError(f"{path}: changed on disk while the run was starting; run again")
        self.package_meta = {k: meta.get(k) for k in ("bytes", "sha256", "etag", "last_modified", "retrieved_at")}
        try:
            archive = zipfile.ZipFile(handle)
        except zipfile.BadZipFile as exc:
            handle.close()
            raise FetchError(f"{PACKAGE_URL}: not a zip archive ({exc})") from exc
        self._package_handle = handle  # a ZipFile given a file object leaves closing it to the caller (fetch)
        return archive

    def _member_raw(self, archive: zipfile.ZipFile, base: str, lang: str, version: str, name: str) -> RawDocument:
        """The member's ``main.xml`` exactly as stored, after checking that it is the document its path names."""
        data = archive.read(name)  # zipfile checks the member's CRC-32
        expression = f"/akn/fi/act/{base}/{lang}@{version}"
        if f'<FRBRuri value="{expression}"/>'.encode() not in data:
            raise PackageMemberError(f"{PACKAGE_URL}#{name}: the document is not {expression}")
        match = _VERSION.search(data)
        number = match.group(1).decode() if match else "unknown"
        assert self.package_meta is not None
        return RawDocument(
            external_id=f"finlex:{base}/{lang}@{number}",
            uri=f"{PACKAGE_URL}#{name}",
            media_type="application/akn+xml",
            content=data,
            retrieved_at=datetime.fromisoformat(str(self.package_meta["retrieved_at"])),
            metadata={
                "work": base,
                "language": lang,
                "temporal_version": number,
                "archive": PACKAGE_URL,
                "member": name,
                "package_bytes": self.package_meta["bytes"],
                "package_sha256": self.package_meta["sha256"],
                "package_etag": self.package_meta["etag"],
                "package_last_modified": self.package_meta["last_modified"],
                "package_retrieved_at": self.package_meta["retrieved_at"],
            },
        )

    def _package_versions(
        self,
        archive: zipfile.ZipFile,
        index: dict[tuple[int, str, str], dict[str, str]],
        work: dict[str, Any],
        base: str,
        lang: str,
    ) -> list[RawDocument]:
        """The documents the API route would give for one work in one language, read from the package where it holds
        them and fetched from the API where it does not (see the module docstring)."""
        members = index.get((work["year"], str(work["number"]), lang))
        if not members:
            self.package_fallbacks.append(f"finlex:{base}/{lang}: not in the package; fetched from the API")
            return self._versions(work, base, lang)
        key = latest_member(members)
        latest = self._member_raw(archive, base, lang, key, members[key])
        current = latest.metadata["temporal_version"]
        named = _amending_versions(latest.content)
        for other, name in members.items():
            if other != key:
                named |= _amending_versions(_head(archive, name))
        held = {v for v in members if v} | {current}
        newer = sorted(v for v in named if v > current and v not in held)
        if newer:
            self.package_fallbacks.append(
                f"finlex:{base}/{lang}: the package's latest version is {current}, but its metadata names "
                f"{', '.join(newer)}; fetched from the API"
            )
            return self._versions(work, base, lang)
        versions = [latest]
        if work.get("history", False):
            # The member "<lang>@" is the statute as enacted: its version is named after the statute itself.
            by_version = {v: name for v, name in members.items() if v}
            if "" in members and re.fullmatch(r"\d{1,4}", str(work["number"])):
                by_version.setdefault(version_id(work["year"], str(work["number"])), members[""])
            wanted = work.get("history_versions")
            for vid in history_version_ids(latest.content, work["year"], str(work["number"])):
                if wanted is not None and vid not in {str(w) for w in wanted}:
                    continue
                if vid in by_version:
                    member_key = "" if by_version[vid] == members.get("") else vid
                    versions.append(self._member_raw(archive, base, lang, member_key, by_version[vid]))
                    continue
                got = self._get(f"akn/fi/act/{base}/{lang}@{vid}")  # not in the package: the API, as the API route
                if got is None:
                    self.missing_versions.append(f"{base}/{lang}@{vid}")
                    continue
                self.package_fallbacks.append(f"finlex:{base}/{lang}@{vid}: not in the package; fetched from the API")
                versions.append(self._raw(base, lang, *got))
        dates = version_dates(latest.content)
        versions.sort(key=lambda r: (dates.get(r.metadata["temporal_version"], ""), r.metadata["temporal_version"]))
        return versions

    # ------------------------------------------------------------------ both routes

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.fetch_errors, self.missing_versions, self.package_fallbacks = [], [], []
        self.http = PoliteHttp(  # the API: the whole run in API mode, only the fallbacks in package mode
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=manifest_crawl_delay(manifest),
            offline=self.offline,
            sleep=self._sleep,
            honour_robots_delay=True,
        )
        archive: zipfile.ZipFile | None = None
        index: dict[tuple[int, str, str], dict[str, str]] = {}
        if self._mode(manifest) == "package":
            archive = self._open_package(manifest)
            index = package_index(archive)
        try:
            yield from self._works(manifest, archive, index)
        finally:
            if archive is not None:
                archive.close()
            if self._package_handle is not None:
                self._package_handle.close()
                self._package_handle = None

    def _works(
        self,
        manifest: dict[str, Any],
        archive: zipfile.ZipFile | None,
        index: dict[tuple[int, str, str], dict[str, str]],
    ) -> Iterator[RawDocument]:
        failed_in_a_row = 0
        for work in manifest["works"]:
            base = f"statute-consolidated/{work['year']}/{work['number']}"
            documents: list[RawDocument] = []
            try:
                for lang in work.get("languages") or manifest.get("languages", LANGUAGES):
                    if archive is None:
                        documents += self._versions(work, base, lang)
                        continue
                    try:
                        documents += self._package_versions(archive, index, work, base, lang)
                    except (PackageMemberError, zipfile.BadZipFile, zlib.error) as exc:
                        self.fetch_errors.append(f"finlex:{base}/{lang}: {exc}; skipped")
            except FetchError as exc:
                if _REFUSAL.search(str(exc)) or self.offline:
                    raise  # a refusal stops the run; so does a missing cache entry when offline
                self.fetch_errors.append(f"finlex:{base}: {exc}; skipped")
                failed_in_a_row += 1
                if failed_in_a_row >= MAX_CONSECUTIVE_ERRORS:
                    raise FetchError(f"{failed_in_a_row} works failed in a row (last: {exc}); stopping") from exc
                continue
            failed_in_a_row = 0
            yield from documents
