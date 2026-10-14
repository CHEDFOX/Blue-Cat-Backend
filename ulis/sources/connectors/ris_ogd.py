"""Fetcher for the Austrian Rechtsinformationssystem (RIS) open-government-data API, v2.6.

The API (https://data.bka.gv.at/ris/api/v2.6/) returns search results as JSON metadata only;
the texts are the document files it links to ("ContentUrl", data type Xml) on ris.bka.gv.at.
The manifest lists exactly what to fetch:

* ``works`` (application BrKons): a Gesetzesnummer, the paragraph/article ranges wanted and
  which versions - those in force on given dates (``fassung_vom``) and/or every version
  (``all_versions``);
* ``decisions`` (applications Vfgh, Vwgh, Justiz): a Geschäftszahl and decision date, and
  optionally the document number of the decision text.

The publisher's OGD terms (https://www.ris.bka.gv.at/UI/Ogd.aspx, "Technische
Rahmenbedingungen") are built in: at most one request every 2 s and no parallel connections
(PoliteHttp, 5 s here, sequential); network access only outside office hours, i.e. 20:00-05:00
Vienna time on weekdays or at weekends (Austrian public holidays, which the terms also allow,
are not computed, so the connector stays closed on them); a hard cap on network requests per
run (``max_network_requests``) so that a run stays a small volume. Cached responses are reused
without any network access, so tests and repeated runs work offline at any time.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

API_HOST = "data.bka.gv.at"
CONTENT_HOSTS = {"ris.bka.gv.at", "www.ris.bka.gv.at"}
JUDIKATUR_APPS = {"Vfgh", "Vwgh", "Justiz"}
_GESNR = re.compile(r"^\d{8}$")
_SECTION_TYPES = {"Paragraph", "Artikel", "Anlage"}
_LOCATOR = re.compile(r"^(?:§|Art\.|Anl\.)\s*\d+[a-z]*$")
_DOC_ID = re.compile(r"^[A-Z]{3}[A-Za-z0-9_]+$")


def _last_sunday(year: int, month: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def vienna_time(now_utc: datetime) -> datetime:
    """Local time in Vienna (CET/CEST under the EU rule: summer time from 01:00 UTC on the last
    Sunday of March to 01:00 UTC on the last Sunday of October). No tz database needed."""
    now_utc = now_utc.astimezone(UTC)
    start = datetime.combine(_last_sunday(now_utc.year, 3), datetime.min.time(), UTC) + timedelta(hours=1)
    end = datetime.combine(_last_sunday(now_utc.year, 10), datetime.min.time(), UTC) + timedelta(hours=1)
    offset = timedelta(hours=2) if start <= now_utc < end else timedelta(hours=1)
    return (now_utc + offset).replace(tzinfo=None)


def ogd_window_open(now_utc: datetime) -> bool:
    """RIS OGD terms: queries only between 20:00 and 05:00 Vienna time, or at weekends."""
    local = vienna_time(now_utc)
    return local.weekday() >= 5 or local.hour >= 20 or local.hour < 5


def _items(value: Any) -> list[Any]:
    """The API's JSON (converted from XML) holds a single child as an object, several as a list."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("#text", ""))
    return "" if value is None else str(value)


class RisOgdConnector:
    def __init__(
        self,
        manifest_path: Path,
        expected_source_id: str,
        cache_dir: Path,
        *,
        offline: bool = False,
        sleep: Any = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.manifest_path = manifest_path
        self.expected_source_id = expected_source_id
        self.cache_dir = cache_dir / "ris"
        self.offline = offline
        self._sleep = sleep
        self._clock = clock
        self.http: PoliteHttp | None = None
        self.skipped: list[str] = []
        self._require_window = True
        self._max_requests = 0

    # ------------------------------------------------------------------ manifest
    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        api = urllib.parse.urlsplit(manifest.get("api_base", ""))
        if api.scheme != "https" or api.netloc != API_HOST:
            raise ManifestError(f"api_base must be https://{API_HOST}/...")
        if float(manifest.get("crawl_delay_seconds", 0)) < 2:
            raise ManifestError("crawl_delay_seconds must be at least 2 (RIS OGD terms: 0.5 requests per second)")
        if int(manifest.get("max_network_requests", 0)) <= 0:
            raise ManifestError("max_network_requests must be set")
        for work in manifest.get("works", []):
            if not _GESNR.match(str(work.get("gesetzesnummer", ""))):
                raise ManifestError(f"invalid Gesetzesnummer {work.get('gesetzesnummer')!r}")
            for section in work.get("sections", []):
                if section.get("typ") not in _SECTION_TYPES:
                    raise ManifestError(f"invalid section type {section.get('typ')!r}")
            if not work.get("fassung_vom") and not work.get("all_versions"):
                raise ManifestError(f"{work['gesetzesnummer']}: give fassung_vom dates or all_versions")
        for decision in manifest.get("decisions", []):
            if decision.get("applikation") not in JUDIKATUR_APPS:
                raise ManifestError(f"unsupported application {decision.get('applikation')!r}")
            if not decision.get("geschaeftszahl") or not decision.get("entscheidungsdatum"):
                raise ManifestError("a decision needs geschaeftszahl and entscheidungsdatum")
        return manifest

    # ------------------------------------------------------------------ network
    def _get(self, url: str) -> tuple[bytes, dict[str, Any]] | None:
        assert self.http is not None
        cache_file = self.http.cache_path(url)
        cached = cache_file.exists() and cache_file.with_name(cache_file.name + ".meta.json").exists()
        if not cached and not self.offline:
            if self._require_window and not ogd_window_open(self._clock()):
                raise FetchError(
                    f"{url}: outside the RIS OGD access window (20:00-05:00 Vienna time or weekends); "
                    "not fetched. Re-run in the window; cached responses are kept."
                )
            if self.http.requests_made >= self._max_requests:
                raise FetchError(f"budget of {self._max_requests} network requests exhausted; not fetched: {url}")
        return self.http.get(url)

    def _search(self, api_base: str, area: str, params: dict[str, str]) -> Iterator[tuple[dict[str, Any], str]]:
        """Yield (OgdDocumentReference, search URL) for every result page of one query."""
        page = 1
        while True:
            query = {**params, "DokumenteProSeite": "OneHundred", "Seitennummer": str(page)}
            url = f"{api_base}/{area}?{urllib.parse.urlencode(query)}"
            got = self._get(url)
            if got is None:
                raise FetchError(f"{url}: not found")
            data = json.loads(got[0].decode("utf-8-sig"))
            result = data.get("OgdSearchResult", {})
            if "Error" in result:
                raise FetchError(f"{url}: API error {result['Error']}")
            docs = result.get("OgdDocumentResults", {})
            hits = docs.get("Hits", {})
            total = int(_text(hits) or 0)
            for ref in _items(docs.get("OgdDocumentReference")):
                yield ref, url
            if page * 100 >= total:
                return
            page += 1

    @staticmethod
    def _xml_url(ref: dict[str, Any]) -> str | None:
        for content in _items(ref.get("Data", {}).get("Dokumentliste", {}).get("ContentReference")):
            if content.get("ContentType") != "MainDocument":
                continue
            for u in _items(content.get("Urls", {}).get("ContentUrl")):
                if u.get("DataType") == "Xml":
                    return str(u.get("Url"))
        return None

    def _document(self, ref: dict[str, Any], search_url: str, extra: dict[str, Any]) -> RawDocument | None:
        meta = ref.get("Data", {}).get("Metadaten", {})
        doc_id = str(meta.get("Technisch", {}).get("ID", ""))
        url = self._xml_url(ref)
        if not _DOC_ID.match(doc_id) or not url:
            self.skipped.append(f"{doc_id or '?'}: no XML document")
            return None
        if urllib.parse.urlsplit(url).netloc not in CONTENT_HOSTS:
            raise FetchError(f"{doc_id}: unexpected content host in {url}")
        got = self._get(url)
        if got is None:
            self.skipped.append(f"{doc_id}: XML not found ({url})")
            return None
        data, fetch_meta = got
        return RawDocument(
            external_id=f"ris:{doc_id}",
            uri=fetch_meta.get("final_url") or url,
            media_type="application/xml",
            content=data,
            retrieved_at=datetime.fromisoformat(fetch_meta["retrieved_at"]),
            metadata={"document_number": doc_id, "search_url": search_url, "cached_fetch": fetch_meta, **extra},
        )

    # ------------------------------------------------------------------ fetch
    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self._require_window = bool(manifest.get("off_hours_only", True))
        self._max_requests = int(manifest["max_network_requests"])
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={API_HOST, *CONTENT_HOSTS},
            crawl_delay=float(manifest["crawl_delay_seconds"]),
            offline=self.offline,
            sleep=self._sleep,
        )
        api_base = manifest["api_base"].rstrip("/")
        for work in manifest.get("works", []):
            yield from self._fetch_work(api_base, work)
        for decision in manifest.get("decisions", []):
            yield from self._fetch_decision(api_base, decision)

    def _fetch_work(self, api_base: str, work: dict[str, Any]) -> Iterator[RawDocument]:
        gesnr = str(work["gesetzesnummer"])
        queries: list[dict[str, str]] = []
        for section in work.get("sections") or [{}]:
            base = {"Applikation": "BrKons", "Gesetzesnummer": gesnr}
            if section:
                base["Abschnitt.Typ"] = section["typ"]
                for key in ("von", "bis"):
                    if section.get(key) is not None:
                        base[f"Abschnitt.{key.capitalize()}"] = str(section[key])
            for when in work.get("fassung_vom") or []:
                queries.append({**base, "Fassung.FassungVom": str(when)})
            if work.get("all_versions"):
                # Every version whose Inkrafttretensdatum lies in this (all-embracing) interval.
                every = {"Fassung.VonInkrafttretensdatum": "1800-01-01", "Fassung.BisInkrafttretensdatum": "2999-12-31"}
                queries.append({**base, **every})
        refs: dict[str, tuple[dict[str, Any], str]] = {}
        for params in queries:
            for ref, url in self._search(api_base, "Bundesrecht", params):
                meta = ref.get("Data", {}).get("Metadaten", {})
                doc_id = str(meta.get("Technisch", {}).get("ID", ""))
                brkons = meta.get("Bundesrecht", {}).get("BrKons", {})
                locator = str(brkons.get("ArtikelParagraphAnlage", "")).strip()
                if str(brkons.get("Gesetzesnummer", "")) != gesnr:
                    raise FetchError(f"{doc_id}: belongs to Gesetzesnummer {brkons.get('Gesetzesnummer')}, not {gesnr}")
                if not _LOCATOR.match(locator) or brkons.get("Uebergangsrecht"):
                    self.skipped.append(f"{doc_id}: {locator!r} is not a plain paragraph/article/annex")
                    continue
                refs.setdefault(doc_id, (ref, url))

        def order(item: tuple[str, tuple[dict[str, Any], str]]) -> tuple[str, str]:
            brkons = item[1][0].get("Data", {}).get("Metadaten", {}).get("Bundesrecht", {}).get("BrKons", {})
            # Newest versions first, so that the work is created from its current title.
            return (str(brkons.get("Inkrafttretensdatum") or ""), item[0])

        for _doc_id, (ref, url) in sorted(refs.items(), key=order, reverse=True):
            brkons = ref.get("Data", {}).get("Metadaten", {}).get("Bundesrecht", {}).get("BrKons", {})
            raw = self._document(
                ref,
                url,
                {
                    "gesetzesnummer": gesnr,
                    "locator": brkons.get("ArtikelParagraphAnlage"),
                    "inkrafttretensdatum": brkons.get("Inkrafttretensdatum"),
                    "ausserkrafttretensdatum": brkons.get("Ausserkrafttretensdatum"),
                },
            )
            if raw is not None:
                yield raw

    def _fetch_decision(self, api_base: str, decision: dict[str, Any]) -> Iterator[RawDocument]:
        when = str(decision["entscheidungsdatum"])
        params = {
            "Applikation": decision["applikation"],
            "Geschaeftszahl": str(decision["geschaeftszahl"]),
            "EntscheidungsdatumVon": when,
            "EntscheidungsdatumBis": when,
            "Dokumenttyp.SucheInEntscheidungstexten": "true",
        }
        wanted = decision.get("document_number")
        found = False
        for ref, url in self._search(api_base, "Judikatur", params):
            meta = ref.get("Data", {}).get("Metadaten", {})
            doc_id = str(meta.get("Technisch", {}).get("ID", ""))
            if meta.get("Judikatur", {}).get("Dokumenttyp") != "Text" or (wanted and doc_id != wanted):
                continue
            raw = self._document(ref, url, {"applikation": decision["applikation"]})
            if raw is not None:
                found = True
                yield raw
        if not found:
            label = f"{decision['applikation']} {decision['geschaeftszahl']} ({when})"
            self.skipped.append(f"{label}: no decision text found")
