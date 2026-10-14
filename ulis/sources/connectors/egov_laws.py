"""Polite fetcher for e-Gov 法令検索 (Digital Agency), 法令API Version 2.

For every law in the manifest it fetches the revision list (``/law_revisions/{law_id}``) and
the text of the revisions to ingest (``/law_data/{law_revision_id}``), as XML:

* ``history: false`` - only the revision currently in force (``CurrentEnforced``);
* ``history: true`` - every enforced revision since the data baseline (1 April 2017), one per
  enforcement date: when several amendments take effect on the same day, e-Gov lists one
  revision per amendment and only the last of them (the first listed) is the law of that day;
  ``history_dates`` restricts the earlier revisions fetched (small test fixtures);
* revisions not yet in force are never fetched as text: the revision list, yielded after the
  texts, reports them as outstanding changes.

Requests use ``PoliteHttp`` (identifying User-Agent, a delay between requests, back-off,
refusals respected, HTTPS to laws.e-gov.go.jp only, local cache, offline mode). The site has
no robots.txt, so the default 5-second delay applies. Responses are cached forever: delete
``<cache>/laws.e-gov.go.jp/api2/law_revisions`` to pick up new revisions.
"""

from __future__ import annotations

import re
import time
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from defusedxml.ElementTree import fromstring

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

BASE_URL = "https://laws.e-gov.go.jp/api/2"
_LAW_ID = re.compile(r"^[0-9]{3}[0-9A-Z]+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
IN_FORCE = ("CurrentEnforced", "PreviousEnforced")

__all__ = ["EgovLawsConnector", "FetchError", "Revision", "revisions_to_fetch"]


@dataclass(frozen=True)
class Revision:
    law_revision_id: str
    status: str
    enforcement_date: str


def revisions_to_fetch(listing: bytes, history: bool, history_dates: set[str] | None = None) -> list[Revision]:
    """Enforced revisions to fetch, oldest first: one per enforcement date (the law of that day)."""
    try:
        root = fromstring(listing, forbid_dtd=True)
    except Exception as exc:
        raise FetchError(f"revision list is not well-formed or unsafe XML: {exc}") from exc
    if root.tag != "law_revisions_response":
        raise FetchError(f"unexpected revision list root {root.tag!r}")
    listed = [
        Revision(
            (r.findtext("law_revision_id") or "").strip(),
            (r.findtext("current_revision_status") or "").strip(),
            (r.findtext("amendment_enforcement_date") or "").strip(),
        )
        for r in root.iter("revision")
    ]
    by_date: dict[str, Revision] = {}
    for rev in listed:  # newest first: the first revision listed for a date is the last one applied
        if rev.status not in IN_FORCE or not _DATE.match(rev.enforcement_date):
            continue
        current = by_date.get(rev.enforcement_date)
        if current is None or (rev.status == "CurrentEnforced" and current.status != "CurrentEnforced"):
            by_date[rev.enforcement_date] = rev
    chosen = sorted(by_date.values(), key=lambda r: r.enforcement_date)
    if not any(r.status == "CurrentEnforced" for r in chosen):
        raise FetchError("the revision list has no revision currently in force")
    if not history:
        return [r for r in chosen if r.status == "CurrentEnforced"]
    if history_dates is not None:
        return [r for r in chosen if r.status == "CurrentEnforced" or r.enforcement_date in history_dates]
    return chosen


class EgovLawsConnector:
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

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        if manifest.get("base_url", BASE_URL).rstrip("/") != BASE_URL:
            raise ManifestError(f"base_url must be {BASE_URL}")
        for law in manifest.get("laws") or []:
            if not _LAW_ID.match(str(law.get("law_id", ""))):
                raise ManifestError(f"invalid law_id {law.get('law_id')!r}")
        return manifest

    def _get(self, url: str, cache_key: str) -> tuple[bytes, dict[str, Any]]:
        assert self.http is not None
        got = self.http.get(url, cache_key=cache_key)
        if got is None:
            raise FetchError(f"{url}: not found")
        return got

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        host = urllib.parse.urlsplit(BASE_URL).netloc
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={host},
            crawl_delay=float(manifest.get("crawl_delay_seconds", 5)),
            offline=self.offline,
            sleep=self._sleep,
        )
        for law in manifest["laws"]:
            law_id = str(law["law_id"])
            list_url = f"{BASE_URL}/law_revisions/{law_id}?response_format=xml"
            listing, list_meta = self._get(list_url, f"api2/law_revisions/{law_id}.xml")
            wanted = law.get("history_dates")
            for rev in revisions_to_fetch(
                listing, bool(law.get("history", False)), {str(d) for d in wanted} if wanted is not None else None
            ):
                url = f"{BASE_URL}/law_data/{rev.law_revision_id}?response_format=xml"
                data, meta = self._get(url, f"api2/law_data/{rev.law_revision_id}.xml")
                yield self._raw(f"e-gov:{rev.law_revision_id}", data, meta, law_id, rev)
            # After the texts: the list reports amendments not yet in force for the current revision.
            yield self._raw(f"e-gov:{law_id}:law_revisions", listing, list_meta, law_id, None)

    @staticmethod
    def _raw(external_id: str, data: bytes, meta: dict[str, Any], law_id: str, rev: Revision | None) -> RawDocument:
        metadata: dict[str, Any] = {"law_id": law_id, "cached_fetch": meta}
        if rev is not None:
            metadata.update(law_revision_id=rev.law_revision_id, enforcement_date=rev.enforcement_date)
        return RawDocument(
            external_id=external_id,
            uri=meta.get("final_url") or meta["url"],
            media_type="application/xml",
            content=data,
            retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
            metadata=metadata,
        )
