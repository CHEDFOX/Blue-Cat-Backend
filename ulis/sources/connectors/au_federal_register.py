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
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from datetime import datetime
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
VERSION_FIELDS = (
    "titleId,start,retrospectiveStart,end,retrospectiveEnd,compilationNumber,registerId,status,"
    "isLatest,isCurrent,hasUnincorporatedAmendments,name"
)
DOCUMENT_FIELDS = (
    "titleId,start,retrospectiveStart,compilationNumber,registerId,rectificationVersionNumber,"
    "uniqueTypeNumber,volumeNumber,format,type,sizeInBytes"
)


def _day(value: str | None) -> str:
    return (value or "")[:10]


class FederalRegisterConnector:
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
        self.http: PoliteHttp | None = None
        # (title id, version start, reason) for every version that has no document fetched.
        self.skipped: list[tuple[str, str, str]] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        if manifest.get("api_base", API_BASE).rstrip("/") != API_BASE:
            raise ManifestError(f"api_base must be {API_BASE}")
        for work in manifest["works"]:
            if not TITLE_ID.match(str(work.get("title_id", ""))):
                raise ManifestError(f"invalid title id {work.get('title_id')!r}")
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

    def _listing(self, http: PoliteHttp, url: str, cache_key: str) -> list[dict[str, Any]]:
        """All rows of an OData listing, following ``@odata.nextLink`` (the API pages at 510 rows)."""
        rows: list[dict[str, Any]] = []
        page = 0
        next_url: str | None = url
        while next_url:
            got = http.get(next_url, cache_key=f"{cache_key}-p{page}.json", headers={"Accept": "application/json"})
            if got is None:
                raise FetchError(f"{next_url}: not found")
            body = json.loads(got[0])
            rows.extend(body.get("value", []))
            next_url = body.get("@odata.nextLink")
            page += 1
            if page > 50:  # pragma: no cover - defensive
                raise FetchError(f"{url}: too many pages")
        return rows

    def versions(self, http: PoliteHttp, title_id: str) -> list[dict[str, Any]]:
        url = f"{API_BASE}/Versions?$filter=titleId%20eq%20'{title_id}'&$select={VERSION_FIELDS}&$orderby=start"
        return self._listing(http, url, f"{title_id}/versions")

    def documents(self, http: PoliteHttp, title_id: str) -> list[dict[str, Any]]:
        url = (
            f"{API_BASE}/Documents?$filter=titleId%20eq%20'{title_id}'%20and%20type%20eq%20'Primary'"
            f"%20and%20format%20eq%20'Epub'&$select={DOCUMENT_FIELDS}&$orderby=start"
        )
        return self._listing(http, url, f"{title_id}/documents")

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
        for work in manifest["works"]:
            title_id = work["title_id"]
            versions = self.versions(http, title_id)
            if not versions:
                raise FetchError(f"{title_id}: no versions listed")
            documents = self.documents(http, title_id)
            since = str(work["history_since"]) if work.get("history_since") else None
            for version, doc in self.select(title_id, versions, documents, bool(work.get("history", False)), since):
                start = _day(doc["start"])
                rect = int(doc.get("rectificationVersionNumber") or 0)
                url = self.document_url(doc)
                got = http.get(
                    url, cache_key=f"{title_id}/{start}_r{rect}.json", headers={"Accept": "application/json"}
                )
                if got is None:
                    self.skipped.append((title_id, start, "document not found (404)"))
                    continue
                data, meta = got
                yield RawDocument(
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
