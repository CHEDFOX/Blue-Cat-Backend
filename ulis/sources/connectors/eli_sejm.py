"""Polite fetcher for the Sejm's ELI API (https://api.sejm.gov.pl/eli), the machine interface to the Polish
Dziennik Ustaw (Journal of Laws).

For every instrument in the manifest it yields

* one *consolidated text* bundle per listed announcement (obwieszczenie o ogłoszeniu tekstu jednolitego):
  a JSON envelope holding, verbatim, the announcement's metadata record
  (``GET /acts/DU/<year>/<pos>``) and its HTML (``GET /acts/DU/<year>/<pos>/text.html``), oldest first;
* one *change report* envelope holding, verbatim, the metadata record of the act itself, which lists the
  amending acts and the consolidated texts published for it; the parser reports those later than the newest
  text ingested as outstanding changes (see :mod:`ulis.sources.parsers.eli_sejm`).

Built on :class:`PoliteHttp`: identifying User-Agent, at least 5 s between requests, HTTPS to the API host
only, refusals respected, a cache under ``<cache_dir>/api.sejm.gov.pl/eli/acts/...`` so runs can be repeated
offline. ``api.sejm.gov.pl`` has no robots.txt (404, which means no restrictions); if one appears the connector
reads it and refuses a URL it disallows. PDF texts are never requested: an announcement whose record says
``textHTML: false`` is refused, so the manifest lists only texts the API offers as HTML.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

HOST = "api.sejm.gov.pl"
BASE_URL = f"https://{HOST}/eli"
ROBOTS_URL = f"https://{HOST}/robots.txt"
ACT_ID = re.compile(r"^DU/(?P<year>(?:19|20)\d\d)/(?P<pos>[1-9]\d{0,4})$")
BUNDLE_SCHEMA = "ulis-pl-eli-bundle/1"
MIN_DELAY = 5.0
REFUSAL = re.compile(r"HTTP (401|403|407|429|451)\b")

__all__ = ["BASE_URL", "BUNDLE_SCHEMA", "EliSejmConnector", "act_sort_key", "text_url", "record_url"]


def record_url(act_id: str) -> str:
    return f"{BASE_URL}/acts/{act_id}"


def text_url(act_id: str) -> str:
    return f"{BASE_URL}/acts/{act_id}/text.html"


def act_sort_key(act_id: str) -> tuple[int, int]:
    """``DU/2024/1061`` -> ``(2024, 1061)``: the order of publication within the Dziennik Ustaw."""
    m = ACT_ID.match(act_id)
    if m is None:
        raise ValueError(f"not a Dziennik Ustaw address: {act_id!r}")
    return int(m["year"]), int(m["pos"])


def bundle_bytes(bundle: dict[str, Any]) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class EliSejmConnector:
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
        self.robots: str | None = None
        # Instruments skipped under ``on_instrument_error: skip`` (reported as run errors by the pipeline).
        self.fetch_errors: list[str] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if str(manifest.get("base_url", "")).rstrip("/") != BASE_URL:
            raise ManifestError(f"base_url must be {BASE_URL}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        if float(manifest.get("crawl_delay_seconds", MIN_DELAY)) < MIN_DELAY:
            raise ManifestError(f"crawl_delay_seconds must be at least {MIN_DELAY:g}")
        if manifest.get("on_instrument_error", "stop") not in ("stop", "skip"):
            raise ManifestError("on_instrument_error must be 'stop' or 'skip'")
        instruments = manifest.get("instruments") or []
        if not instruments:
            raise ManifestError("the manifest lists no instruments")
        seen: set[str] = set()
        for inst in instruments:
            for act_id in [inst.get("act"), *(inst.get("consolidated") or [])]:
                if not isinstance(act_id, str) or not ACT_ID.match(act_id):
                    raise ManifestError(f"invalid Dziennik Ustaw address {act_id!r} (expected DU/<year>/<position>)")
            if not inst.get("consolidated"):
                raise ManifestError(f"instrument {inst.get('key')!r} lists no consolidated text")
            for act_id in inst["consolidated"]:
                if act_id in seen:
                    raise ManifestError(f"{act_id} is listed twice")
                seen.add(act_id)
            keys = [act_sort_key(a) for a in inst["consolidated"]]
            if keys != sorted(keys) or len(set(keys)) != len(keys):
                raise ManifestError(f"instrument {inst.get('key')!r}: consolidated texts must be listed oldest first")
        return manifest

    def _get(self, url: str, cache_key: str, accept: str, content_type: str) -> tuple[bytes, dict[str, Any]]:
        assert self.http is not None
        if self.robots is not None and not robots_allows(self.robots, self.http.user_agent, url):
            raise FetchError(f"{url}: disallowed by {ROBOTS_URL}; not fetched")
        got = self.http.get(url, cache_key=cache_key, headers={"Accept": accept})
        if got is None:
            raise FetchError(f"{url}: not found (404)")
        data, meta = got
        if not str(meta.get("content_type") or "").lower().startswith(content_type):
            raise FetchError(f"{url}: expected {content_type}, got {meta.get('content_type')!r}; not ingested")
        return data, meta

    def _record(self, act_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        url = record_url(act_id)
        data, meta = self._get(url, f"eli/acts/{act_id}.json", "application/json", "application/json")
        try:
            record = json.loads(data.decode("utf-8"))
        except ValueError as exc:
            raise FetchError(f"{url}: not JSON ({exc})") from exc
        year, pos = act_sort_key(act_id)
        if not (
            isinstance(record, dict)
            and record.get("publisher") == "DU"
            and record.get("year") == year
            and record.get("pos") == pos
        ):
            raise FetchError(f"{url}: the record is not that of {act_id}")
        return record, meta

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", MIN_DELAY)),
            offline=self.offline,
            sleep=self._sleep,
        )
        # No robots.txt (404) means no restrictions (RFC 9309, 2.3.1.3); any other failure stops the run.
        got = self.http.get(ROBOTS_URL, cache_key="robots.txt")
        self.robots = got[0].decode("utf-8", "replace") if got is not None else None

        self.fetch_errors = []
        skip = manifest.get("on_instrument_error", "stop") == "skip"
        for inst in manifest["instruments"]:
            try:
                documents = self._instrument(inst)
            except FetchError as exc:
                # One act that is gone, has no HTML text or does not match its record must not end a run of
                # hundreds; a refusal (401/403/429/451) always does. Nothing of that instrument is yielded.
                if not skip or REFUSAL.search(str(exc)):
                    raise
                self.fetch_errors.append(f"pl-eli:{inst['act']}: {exc}")
                continue
            yield from documents

    def _instrument(self, inst: dict[str, Any]) -> list[RawDocument]:
        """The documents of one instrument (its consolidated texts, then its change report), or a FetchError."""
        out: list[RawDocument] = []
        act_id = inst["act"]
        act_record, act_meta = self._record(act_id)
        # The stable facts about the act itself that a consolidated text needs (its name and dates).
        act_summary = {
            k: act_record.get(k) for k in ("title", "displayAddress", "announcementDate", "entryIntoForce", "type")
        }
        last_status: tuple[date, str] | None = None
        for tj_id in inst["consolidated"]:
            record, record_meta = self._record(tj_id)
            refs = record.get("references") or {}
            if act_id not in [r.get("id") for r in refs.get("Tekst jednolity dla aktu") or []]:
                raise FetchError(f"{tj_id} is not recorded as a consolidated text of {act_id}")
            if record.get("type") != "Obwieszczenie":
                raise FetchError(f"{tj_id}: type {record.get('type')!r}, expected an Obwieszczenie")
            if record.get("textHTML") is not True:
                raise FetchError(f"{tj_id}: the API offers no HTML text (textHTML false); PDF is not parsed")
            try:
                status_date = date.fromisoformat(str(record.get("legalStatusDate")))
            except ValueError as exc:
                raise FetchError(f"{tj_id}: no legal-status date (legalStatusDate) in the record") from exc
            if last_status is not None and status_date <= last_status[0]:
                raise FetchError(
                    f"{tj_id}: legal-status date {status_date} is not after {last_status[0]} of {last_status[1]}"
                )
            last_status = (status_date, tj_id)
            text, text_meta = self._get(text_url(tj_id), f"eli/acts/{tj_id}.text.html", "text/html", "text/html")
            try:
                html = text.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise FetchError(f"{text_url(tj_id)}: not UTF-8 ({exc})") from exc
            if not html.lstrip().lower().startswith("<!doctype html") or 'class="unit ' not in html:
                raise FetchError(f"{text_url(tj_id)}: not an ELI act text; not ingested")
            bundle = {
                "schema": BUNDLE_SCHEMA,
                "kind": "consolidated_text",
                "act_id": act_id,
                "act": act_summary,
                "tj_id": tj_id,
                "tj_record": record,
                "text_html": html,
                "urls": {"record": record_url(tj_id), "text": text_url(tj_id)},
            }
            out.append(
                RawDocument(
                    external_id=f"pl-eli:{tj_id}",
                    uri=text_url(tj_id),
                    media_type="application/json",
                    content=bundle_bytes(bundle),
                    retrieved_at=datetime.fromisoformat(text_meta["retrieved_at"]),
                    metadata={
                        "instrument": inst["key"],
                        "act": act_id,
                        "consolidated_text": tj_id,
                        "legal_status_date": status_date.isoformat(),
                        "cached_fetch": {"record": record_meta, "text": text_meta},
                    },
                )
            )
        assert last_status is not None
        report = {
            "schema": BUNDLE_SCHEMA,
            "kind": "change_report",
            "act_id": act_id,
            "attach_to": {"tj_id": last_status[1], "legal_status_date": last_status[0].isoformat()},
            "act_record": act_record,
            "urls": {"record": record_url(act_id)},
        }
        out.append(
            RawDocument(
                external_id=f"pl-eli:{act_id}:references",
                uri=record_url(act_id),
                media_type="application/json",
                content=bundle_bytes(report),
                retrieved_at=datetime.fromisoformat(act_meta["retrieved_at"]),
                metadata={
                    "instrument": inst["key"],
                    "act": act_id,
                    "attached_to": last_status[1],
                    "cached_fetch": {"record": act_meta},
                },
            )
        )
        return out
