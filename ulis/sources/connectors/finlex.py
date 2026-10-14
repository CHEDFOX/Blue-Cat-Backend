"""Polite fetcher for consolidated Finnish statutes from the Finlex open data API.

The API (``https://opendata.finlex.fi/finlex/avoindata/v1``, documented at
https://www.finlex.fi/en/open-data/integration-quick-guide) serves each consolidated statute
(``statute-consolidated``) as Akoma Ntoso XML, one document per language and *temporal
version*. A temporal version is named after the amending statute it incorporates:
``fin@20260380`` is the Finnish text as amended by statute 380/2026, ``fin@20181050`` the
text as originally enacted (statute 1050/2018) and ``fin@latest`` the newest version.

For every work in the manifest the connector fetches the latest version in each listed
language and, if ``history`` is set, every earlier temporal version: the versions named
after the amending statutes the latest version lists (``finlex:amendedBy``) and after the
statute itself. A version the API does not hold (404) is skipped and reported in the run
metadata; nothing is inferred in its place.

Rules from the API documentation are built in through :class:`PoliteHttp`: a User-Agent
header on every call (required by the API), ``Accept-Encoding: gzip`` (recommended), HTTPS
only to ``opendata.finlex.fi``, a pause between requests (the API publishes no robots.txt,
so 5 s), back-off on 429 (the API's rate-limit response) and 5xx, any refusal stops the run,
and every response is cached so nothing is fetched twice. Responses arrive gzip-encoded; the
connector yields the decoded XML exactly as served.
"""

from __future__ import annotations

import gzip
import re
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

API = "https://opendata.finlex.fi/finlex/avoindata/v1"
HOST = "opendata.finlex.fi"
LANGUAGES = ("fin", "swe")
_NUMBER = re.compile(r"^\d{1,5}(?:-\d{3})?$")
_VERSION = re.compile(rb"<FRBRversionNumber value=\"(\d{8})\"")
_AMENDED_BY = re.compile(rb"<finlex:amendedBy>(.*?)</finlex:amendedBy>", re.S)
_AMENDING = re.compile(rb"<finlex:ref href=\"/akn/fi/act/statute/(\d{4})/(\d{1,4})\">")
_AMENDMENT = re.compile(
    rb"<finlex:statuteReference>\s*<finlex:ref href=\"/akn/fi/act/statute/(\d{4})/(\d{1,4})\">[^<]*</finlex:ref>"
    rb"(?:\s*<finlex:inForce>\s*<finlex:dateEntryIntoForce date=\"(\d{4}-\d{2}-\d{2})\")?"
)

__all__ = ["FetchError", "FinlexConnector", "history_version_ids", "version_dates", "version_id"]


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


class FinlexConnector:
    def __init__(
        self,
        manifest_path: Path,
        expected_source_id: str,
        cache_dir: Path,
        *,
        offline: bool = False,
        sleep: Any = time.sleep,
    ):
        self.manifest_path = manifest_path
        self.expected_source_id = expected_source_id
        self.cache_dir = cache_dir
        self.offline = offline
        self._sleep = sleep
        self.missing_versions: list[str] = []
        self.http: PoliteHttp | None = None

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
        for work in manifest["works"]:
            if not isinstance(work.get("year"), int) or not _NUMBER.match(str(work.get("number", ""))):
                raise ManifestError(f"invalid work {work!r}")
        return manifest

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

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", 5)),
            offline=self.offline,
            sleep=self._sleep,
        )
        for work in manifest["works"]:
            base = f"statute-consolidated/{work['year']}/{work['number']}"
            for lang in manifest.get("languages", LANGUAGES):
                latest = self._get(f"akn/fi/act/{base}/{lang}@latest")
                if latest is None:
                    raise FetchError(f"{base} ({lang}): not found")
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
                versions.sort(
                    key=lambda r: (dates.get(r.metadata["temporal_version"], ""), r.metadata["temporal_version"])
                )
                yield from versions
