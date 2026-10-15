"""Polite fetcher for the Federal Register of Legislation (Australia) public API.

The Register (www.legislation.gov.au, Office of Parliamentary Counsel) publishes an OData-style
REST API at ``https://api.prod.legislation.gov.au/v1/`` (no key; documented at
``/swagger/index.html``). Its "Data share and reuse" page allows automated downloading and
commercial reuse under CC BY 4.0, asking only that robots.txt's crawl delay (10 s) be respected.

For every title (``C1901A00002`` ...) in the manifest the connector reads:

1. the title's versions (``/v1/Versions``): start and end dates, register id, compilation
   number, whether it is the latest registered version;
2. the title's EPUB documents (``/v1/Documents``): rectification and volume numbers;
3. for the latest registered compilation (or, with ``history: true``, for every registered
   compilation), the document itself, requested with ``Accept: application/json`` and
   ``$expand=version``. That single response carries the EPUB (base64) **and** the
   version's start and end dates, so each raw document is self-contained evidence.

"As made" versions (compilation number 0) are not fetched: the Register dates them from the
making date, which is not necessarily when the Act commenced. Versions without a registered
compilation (gaps in older histories, future versions not yet compiled) have no document and are
reported in ``skipped``. Multi-volume compilations are skipped as well (not yet supported).

A manifest may list every in-force Act that has a compilation (``scripts/build_frl_manifest.py``, about 780
titles). For the titles that need only their latest compilation, the listings of versions and documents are
requested for 40 titles at a time (two requests per 40 titles instead of two per title), so a title costs one
request, its EPUB. One title that is missing or whose document keeps failing is recorded in ``fetch_errors`` and
the run continues; a refusal (401/403/429/451) or five failures in a row stop it. With ``avoid_hours`` in the
manifest (the Register's help page prefers crawls outside 08:00-20:00 Australian time, UTC+10) network requests
wait for the end of that window.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

API_BASE = "https://api.prod.legislation.gov.au/v1"
WEB_BASE = "https://www.legislation.gov.au"
TITLE_ID = re.compile(r"^C\d{4}[A-Z]\d{5}$")
REGISTER_ID = re.compile(r"^C\d{4}[A-Z]\d{5}$")
VERSION_FIELDS = (
    "titleId,start,retrospectiveStart,end,retrospectiveEnd,compilationNumber,registerId,status,"
    "isLatest,isCurrent,hasUnincorporatedAmendments,name"
)
DOCUMENT_FIELDS = (
    "titleId,start,retrospectiveStart,compilationNumber,registerId,rectificationVersionNumber,"
    "uniqueTypeNumber,volumeNumber,format,type,sizeInBytes"
)
BATCH_SIZE = 40
MAX_FAILURES_IN_A_ROW = 5
REFUSAL = re.compile(r"HTTP (?:401|403|429|451)\b")


def _day(value: str | None) -> str:
    return (value or "")[:10]


def _in_list(values: list[str]) -> str:
    """An OData ``in (...)`` list of string literals (identifiers are validated against TITLE_ID first)."""
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


class FederalRegisterConnector:
    def __init__(
        self,
        manifest_path: Path,
        expected_source_id: str,
        cache_dir: Path,
        *,
        offline: bool = False,
        sleep: Any = time.sleep,
        now: Callable[[], datetime] | None = None,
    ):
        self.manifest_path = manifest_path
        self.expected_source_id = expected_source_id
        self.cache_dir = cache_dir
        self.offline = offline
        self._sleep = sleep
        self._now = now or (lambda: datetime.now(UTC))
        self.http: PoliteHttp | None = None
        # (title id, version start, reason) for every version that has no document fetched.
        self.skipped: list[tuple[str, str, str]] = []
        # One line per title that could not be fetched (missing, or repeated server errors).
        self.fetch_errors: list[str] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        if manifest.get("api_base", API_BASE).rstrip("/") != API_BASE:
            raise ManifestError(f"api_base must be {API_BASE}")
        seen: set[str] = set()
        for work in manifest["works"]:
            if not TITLE_ID.match(str(work.get("title_id", ""))):
                raise ManifestError(f"invalid title id {work.get('title_id')!r}")
            if work["title_id"] in seen:
                raise ManifestError(f"title {work['title_id']} is listed twice")
            seen.add(work["title_id"])
        window = manifest.get("avoid_hours")
        if window is not None and not (
            isinstance(window, dict)
            and {"utc_offset", "start", "end"} <= set(window)
            and all(isinstance(window[k], int) for k in ("utc_offset", "start", "end"))
            and 0 <= window["start"] < window["end"] <= 24
        ):
            raise ManifestError("avoid_hours needs integer utc_offset, start and end (0 <= start < end <= 24)")
        return manifest

    def _http(self, manifest: dict[str, Any]) -> PoliteHttp:
        if self.http is None:
            self.http = PoliteHttp(
                self.cache_dir,
                manifest["user_agent"],
                allowed_hosts={urlsplit(API_BASE).netloc},
                crawl_delay=float(manifest.get("crawl_delay_seconds", 10)),
                offline=self.offline,
                sleep=self._sleep,
            )
        return self.http

    def _wait_off_peak(self, manifest: dict[str, Any]) -> None:
        """Wait for the end of the manifest's ``avoid_hours`` window (local time at ``utc_offset``)."""
        window = manifest.get("avoid_hours")
        while window:
            local = self._now() + timedelta(hours=window["utc_offset"])
            if not window["start"] <= local.hour < window["end"]:
                return
            resume = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=window["end"])
            self._sleep(max(60.0, (resume - local).total_seconds()))

    def _get(
        self, manifest: dict[str, Any], http: PoliteHttp, url: str, cache_key: str
    ) -> tuple[bytes, dict[str, Any]] | None:
        """``http.get`` as JSON; a request that is not already cached waits for the off-peak hours first."""
        if not http.offline:
            cached = http.cache_path(url, cache_key)
            if not (cached.exists() and cached.with_name(cached.name + ".meta.json").exists()):
                self._wait_off_peak(manifest)
        return http.get(url, cache_key=cache_key, headers={"Accept": "application/json"})

    def _listing(self, manifest: dict[str, Any], http: PoliteHttp, url: str, cache_key: str) -> list[dict[str, Any]]:
        """All rows of an OData listing (the API pages at 510 rows).

        The ``@odata.nextLink`` the API returns drops the ``$filter`` of the request (verified 2026-10-15: it keeps only
        ``$select``, ``$orderby`` and ``$skip``), so following it would silently list unrelated titles. The listing is
        continued with ``$skip`` appended to the original URL instead; the link is only the signal that more rows exist.
        """
        rows: list[dict[str, Any]] = []
        page = 0
        while True:
            page_url = url if not rows else f"{url}&$skip={len(rows)}"
            got = self._get(manifest, http, page_url, f"{cache_key}-p{page}.json")
            if got is None:
                raise FetchError(f"{page_url}: not found")
            body = json.loads(got[0])
            rows.extend(body.get("value", []))
            page += 1
            if not body.get("@odata.nextLink") or not body.get("value"):
                return rows
            if page > 50:  # pragma: no cover - defensive
                raise FetchError(f"{url}: too many pages")

    def versions(self, manifest: dict[str, Any], http: PoliteHttp, title_id: str) -> list[dict[str, Any]]:
        url = f"{API_BASE}/Versions?$filter=titleId%20eq%20'{title_id}'&$select={VERSION_FIELDS}&$orderby=start"
        return self._listing(manifest, http, url, f"{title_id}/versions")

    def documents(self, manifest: dict[str, Any], http: PoliteHttp, title_id: str) -> list[dict[str, Any]]:
        url = (
            f"{API_BASE}/Documents?$filter=titleId%20eq%20'{title_id}'%20and%20type%20eq%20'Primary'"
            f"%20and%20format%20eq%20'Epub'&$select={DOCUMENT_FIELDS}&$orderby=start"
        )
        return self._listing(manifest, http, url, f"{title_id}/documents")

    def latest_batch(
        self, manifest: dict[str, Any], http: PoliteHttp, title_ids: list[str]
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
        """Versions and documents of the latest registered compilation of several titles, in two listings."""
        key = "batches/" + "_".join([title_ids[0], title_ids[-1], str(len(title_ids))])
        url = (
            f"{API_BASE}/Versions?$filter=titleId%20in%20{_in_list(title_ids)}%20and%20isLatest%20eq%20true"
            f"&$select={VERSION_FIELDS}&$orderby=titleId,start"
        )
        by_title_versions: dict[str, list[dict[str, Any]]] = {}
        register_ids: list[str] = []
        for v in self._listing(manifest, http, url, f"{key}-versions"):
            by_title_versions.setdefault(v["titleId"], []).append(v)
            if v.get("registerId") and REGISTER_ID.match(str(v["registerId"])):
                register_ids.append(v["registerId"])
        by_title_documents: dict[str, list[dict[str, Any]]] = {}
        if register_ids:
            url = (
                f"{API_BASE}/Documents?$filter=registerId%20in%20{_in_list(register_ids)}%20and%20type%20eq%20'Primary'"
                f"%20and%20format%20eq%20'Epub'&$select={DOCUMENT_FIELDS}&$orderby=titleId,start"
            )
            for d in self._listing(manifest, http, url, f"{key}-documents"):
                by_title_documents.setdefault(d["titleId"], []).append(d)
        return by_title_versions, by_title_documents

    def select(
        self,
        title_id: str,
        versions: list[dict[str, Any]],
        documents: list[dict[str, Any]],
        history: bool,
        since: str | None = None,
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """(version, document) pairs to fetch: registered compilations with one EPUB volume.

        ``since`` (with ``history``) skips versions starting before that date; it exists for small
        test fixtures, since a partial history leaves the earlier periods unindexed.
        """
        docs: dict[str, list[dict[str, Any]]] = {}
        for d in documents:
            docs.setdefault(_day(d.get("start")), []).append(d)
        chosen: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for v in sorted(versions, key=lambda x: str(x.get("start"))):
            start = _day(v.get("start"))
            if not history and not v.get("isLatest"):
                continue
            if history and since and start < since and not v.get("isLatest"):
                continue
            if not v.get("registerId"):
                self.skipped.append((title_id, start, "no registered compilation for this version"))
                continue
            if v.get("compilationNumber") == "0" or v.get("registerId") == title_id:
                self.skipped.append((title_id, start, "as made (not a compilation)"))
                continue
            candidates = docs.get(start, [])
            if not candidates:
                self.skipped.append((title_id, start, "no EPUB document"))
                continue
            if any(d.get("volumeNumber") or d.get("uniqueTypeNumber") for d in candidates):
                self.skipped.append((title_id, start, "multi-volume compilation (not supported)"))
                continue
            # A rectification replaces the earlier registration of the same compilation.
            latest = max(candidates, key=lambda d: int(d.get("rectificationVersionNumber") or 0))
            chosen.append((v, latest))
        return chosen

    def document_url(self, doc: dict[str, Any]) -> str:
        return (
            f"{API_BASE}/documents(titleid='{doc['titleId']}',start={_day(doc['start'])},"
            f"retrospectivestart={_day(doc['retrospectiveStart'])},"
            f"rectificationversionnumber={int(doc.get('rectificationVersionNumber') or 0)},type='Primary',"
            f"uniqueTypeNumber=0,volumeNumber=0,format='Epub')?$expand=version"
        )

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        http = self._http(manifest)
        works = manifest["works"]
        batch = bool(manifest.get("batch_listings", False))
        latest_only = [w["title_id"] for w in works if batch and not w.get("history", False)]
        chunks = {
            t: latest_only[i : i + BATCH_SIZE]
            for i in range(0, len(latest_only), BATCH_SIZE)
            for t in latest_only[i : i + BATCH_SIZE]
        }
        loaded: dict[str, tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]] = {}
        failures_in_a_row = 0
        for work in works:
            title_id = work["title_id"]
            try:
                if title_id not in chunks:
                    versions = self.versions(manifest, http, title_id)
                    documents = self.documents(manifest, http, title_id)
                else:
                    chunk = chunks[title_id]
                    if chunk[0] not in loaded:
                        loaded = {chunk[0]: self.latest_batch(manifest, http, chunk)}
                    versions = loaded[chunk[0]][0].get(title_id, [])
                    documents = loaded[chunk[0]][1].get(title_id, [])
                if not versions:
                    self.fetch_errors.append(f"frl:{title_id}: no registered version listed (is the title in force?)")
                    failures_in_a_row = 0
                    continue
                since = str(work["history_since"]) if work.get("history_since") else None
                for version, doc in self.select(title_id, versions, documents, bool(work.get("history", False)), since):
                    raw = self._document(manifest, http, title_id, version, doc)
                    if raw is not None:
                        yield raw
                failures_in_a_row = 0
            except FetchError as exc:
                if REFUSAL.search(str(exc)) or "offline and not cached" in str(exc):
                    raise  # a refusal is respected, never worked around; an offline miss is a configuration error
                self.fetch_errors.append(f"frl:{title_id}: {exc}")
                failures_in_a_row += 1
                if failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
                    raise FetchError(f"stopping after {failures_in_a_row} failures in a row; last: {exc}") from exc

    def _document(
        self, manifest: dict[str, Any], http: PoliteHttp, title_id: str, version: dict[str, Any], doc: dict[str, Any]
    ) -> RawDocument | None:
        start = _day(doc["start"])
        rect = int(doc.get("rectificationVersionNumber") or 0)
        url = self.document_url(doc)
        got = self._get(manifest, http, url, f"{title_id}/{start}_r{rect}.json")
        if got is None:
            self.skipped.append((title_id, start, "document not found (404)"))
            return None
        data, meta = got
        return RawDocument(
            external_id=f"frl:{title_id}@{start}#r{rect}",
            uri=url,
            media_type="application/json",
            content=data,
            retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
            metadata={
                "title_id": title_id,
                "register_id": version.get("registerId"),
                "compilation_number": version.get("compilationNumber"),
                "start": start,
                "end": _day(version.get("end")) or None,
                "rectification": rect,
                "web_url": f"{WEB_BASE}/{title_id}/{start}/text",
                "cached_fetch": meta,
            },
        )
