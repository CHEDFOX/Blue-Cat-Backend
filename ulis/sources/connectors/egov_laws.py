"""Polite fetcher for e-Gov 法令検索 (Digital Agency): the bulk download and 法令API Version 2.

Two modes (manifest ``mode``):

``bulk`` (the committed manifest; the route for a full load). Digital Agency asked API users not to loop over every
law fetching texts one after another, and the API's own spec points to the XML bulk download for downloading law
data. The connector downloads ``all_xml.zip`` once (``bulk.url``, every law in force or to come, one 法令標準XML file
per revision plus the list ``all_law_list.csv``; 283 MB), checks it against the size and SHA-256 the manifest pins,
and reads the API's list of laws in force once (``/laws?repeal_status=None``, 10 pages of 1,000, cached per archive):
the list gives, for each law, the ``law_info`` and ``current_revision_info`` that a ``law_data`` response carries
and the bare files lack. For every manifest law it then yields, without further requests:

* the archive member of the revision the list names as in force (exact bytes; metadata ``egov_bulk: law`` with the
  list's two records), which the parser turns into the document ``law_data`` gives for that revision;
* the law's lines of ``all_law_list.csv`` (the header and its rows, exact bytes; ``egov_bulk: law_list``): the change
  report. Rows marked 未施行 (not yet in force) become outstanding changes; their texts, which are in the archive
  too, are never yielded.

A law the archive and the list disagree on (the archive older or newer than the list, a law repealed since the
manifest was made) is reported in ``fetch_errors`` and skipped. The archive holds only the revision in force and
the revisions to come, so laws with ``history: true`` are fetched from the API as below.

``api`` (default when ``mode`` is absent; single laws and laws with history). For every law it fetches the revision
list (``/law_revisions/{law_id}``) and the text of the revisions to ingest (``/law_data/{law_revision_id}``), as XML:

* ``history: false`` - only the revision currently in force (``CurrentEnforced``);
* ``history: true`` - every enforced revision since the data baseline (1 April 2017), one per
  enforcement date: when several amendments take effect on the same day, e-Gov lists one
  revision per amendment and only the last of them (the first listed) is the law of that day;
  ``history_dates`` restricts the earlier revisions fetched (small test fixtures);
* revisions not yet in force are never fetched as text: the revision list, yielded after the
  texts, reports them as outstanding changes.

Requests use ``PoliteHttp`` (identifying User-Agent, a delay between requests, back-off,
refusals respected, HTTPS to laws.e-gov.go.jp only, one connection, local cache, offline mode). The site has
no robots.txt (the path answers with the web app's HTML page; it is read before the first request of a run, and a
Crawl-delay there would win). The delay stays at no less than :data:`MIN_DELAY` (5 s): Digital Agency asked API
users not to loop over every law fetching texts one after another and warned that excessive or bot access may be
restricted (https://www.digital.go.jp/policies/legal-practice/public-test, read 2026-10-18), so ULIS does not go
faster than it already did without asking first. Responses are cached forever: delete
``<cache>/laws.e-gov.go.jp/api2/law_revisions`` to pick up new revisions. In bulk mode a new archive is picked up by
deleting ``<cache>/laws.e-gov.go.jp/bulkdownload/all_xml.zip`` and moving the pin (``bulk.bytes``, ``bulk.sha256``)
to the archive the run then reports; the list is read again for each archive.

The manifest may list every law in force (``scripts/build_egov_manifest.py``: about 9,000 laws, two requests
each), so one law that cannot be fetched (404, a revision list without a revision in force, a server error that
outlasts the back-off) is reported in ``fetch_errors`` and the run goes on. A refusal (401, 403, 429, 451)
stops the run, and so do ``MAX_CONSECUTIVE_ERRORS`` laws failing in a row.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from defusedxml.ElementTree import fromstring

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp, manifest_crawl_delay
from ulis.sources.parsers.egov_law import BulkListRow, EgovParseError, bulk_list_rows

BASE_URL = "https://laws.e-gov.go.jp/api/2"
MODES = ("api", "bulk")
# The publisher's archive of every law (XML only), named in the API spec (lawapi-v2.yaml, XML一括ダウンロードについて).
BULK_URL = "https://laws.e-gov.go.jp/bulkdownload?file_section=1&only_xml_flag=true"
BULK_CACHE_KEY = "bulkdownload/all_xml.zip"
BULK_LIST = "all_law_list.csv"
LIST_PAGE = 1000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# The publisher's own wish (see the module docstring), stricter than the project-wide minimum of 1 s.
MIN_DELAY = 5.0
_LAW_ID = re.compile(r"^[0-9]{3}[0-9A-Z]+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
IN_FORCE = ("CurrentEnforced", "PreviousEnforced")
_REFUSAL = re.compile(r"HTTP (?:401|403|429|451)\b")
MAX_CONSECUTIVE_ERRORS = 20

__all__ = ["BulkArchive", "BulkMismatch", "EgovLawsConnector", "FetchError", "Revision", "revisions_to_fetch"]


class BulkMismatch(FetchError):
    """The bulk archive and the API list do not agree on a law (reported; the run goes on)."""


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


@dataclass(frozen=True)
class Listed:
    """One law of the API list of laws in force: its law_info and current_revision_info (element texts)."""

    law_info: dict[str, str]
    revision_info: dict[str, str]
    url: str
    retrieved_at: str


def _fields(el: Any) -> dict[str, str]:
    return {child.tag: "".join(child.itertext()) for child in el} if el is not None else {}


def _list_page(data: bytes) -> Any:
    """One page of ``/laws?repeal_status=None`` (its root element)."""
    try:
        root = fromstring(data, forbid_dtd=True)
    except Exception as exc:
        raise FetchError(f"law list is not well-formed or unsafe XML: {exc}") from exc
    if root.tag != "laws_response":
        raise FetchError(f"unexpected law list root {root.tag!r}")
    return root


def list_laws(pages: list[tuple[Any, dict[str, Any]]]) -> dict[str, Listed]:
    """law_id -> its entry, from the pages (root element, fetch meta) of ``/laws?repeal_status=None``."""
    listed: dict[str, Listed] = {}
    for root, meta in pages:
        for law in root.findall("laws/law"):
            info = _fields(law.find("law_info"))
            if info.get("law_id"):
                listed[info["law_id"]] = Listed(
                    info, _fields(law.find("current_revision_info")), meta["url"], meta["retrieved_at"]
                )
    return listed


class BulkArchive:
    """``all_xml.zip`` as cached: its members by revision id and the lines of ``all_law_list.csv`` by law."""

    def __init__(self, path: Path, meta: dict[str, Any], *, url: str, sha256: str, size: int):
        self.path = path
        self.meta = meta
        self.url = url
        self.sha256 = sha256
        self.size = size
        self.zip = zipfile.ZipFile(path)
        names = self.zip.namelist()
        if BULK_LIST not in names:
            raise FetchError(f"{url}: the archive has no {BULK_LIST}")
        self.members = {name for name in names if name.endswith(".xml")}
        raw = self.zip.read(BULK_LIST)
        header, *lines = raw.split(b"\n")
        self.header = header
        self.lines: dict[str, list[bytes]] = defaultdict(list)
        self.rows: dict[str, list[BulkListRow]] = defaultdict(list)
        try:
            for line in lines:
                if not line:
                    continue
                (row,) = bulk_list_rows(header + b"\n" + line + b"\n")  # one physical line per row (checked)
                row.revision_id  # noqa: B018 - validates the 本文URL
                self.lines[row.law_id].append(line)
                self.rows[row.law_id].append(row)
        except (EgovParseError, ValueError) as exc:
            raise FetchError(f"{url}: unexpected {BULK_LIST}: {exc}") from exc
        self.retrieved_at = datetime.fromisoformat(meta["retrieved_at"])

    def provenance(self, member: str) -> dict[str, Any]:
        return {"url": self.url, "bytes": self.size, "sha256": self.sha256, "member": member}

    def law(self, law_id: str, listed: Listed | None) -> list[RawDocument]:
        """The text of the revision in force and the change report of one law."""
        rows = self.rows.get(law_id)
        where = f"the bulk archive (SHA-256 {self.sha256[:12]}…)"
        if not rows:
            raise BulkMismatch(f"not in {where}: repealed, or not in the archive yet")
        if listed is None or not listed.revision_info:
            raise BulkMismatch("not in the API list of laws in force (repeal_status=None): repealed since?")
        current = listed.revision_info
        status = current.get("current_revision_status", "")
        if status not in IN_FORCE:
            raise BulkMismatch(f"the API list gives no revision in force ({status or 'none'})")
        revision_id = current.get("law_revision_id", "")
        in_force = [r for r in rows if not r.pending]
        row = next((r for r in rows if r.revision_id == revision_id), None)
        if row is None or row.pending or any(r.enforcement_date > row.enforcement_date for r in in_force):
            raise BulkMismatch(
                f"{where} and the API list disagree on the revision in force (list: {revision_id}; archive: "
                f"{', '.join(r.revision_id for r in in_force) or 'none'}): read both again, or use mode: api"
            )
        member = f"{revision_id}/{revision_id}.xml"
        if member not in self.members:
            raise BulkMismatch(f"{where} lists {revision_id} but has no file {member}")
        common: dict[str, Any] = {
            "law_id": law_id,
            "law_info": listed.law_info,
            "revision_info": current,
            "api_list": {"url": listed.url, "retrieved_at": listed.retrieved_at},
            "cached_fetch": self.meta,
        }
        text = RawDocument(
            external_id=f"e-gov:{revision_id}",
            uri=f"{self.url}#{member}",
            media_type="application/xml",
            content=self.zip.read(member),
            retrieved_at=self.retrieved_at,
            metadata={
                **common,
                "egov_bulk": "law",
                "law_revision_id": revision_id,
                "enforcement_date": current.get("amendment_enforcement_date"),
                "archive": self.provenance(member),
            },
        )
        report = RawDocument(
            external_id=f"e-gov:{law_id}:law_list",
            uri=f"{self.url}#{BULK_LIST}",
            media_type="text/csv",
            content=b"".join(line + b"\n" for line in [self.header, *self.lines[law_id]]),
            retrieved_at=self.retrieved_at,
            metadata={**common, "egov_bulk": "law_list", "archive": self.provenance(BULK_LIST)},
        )
        return [text, report]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


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
        self.fetch_errors: list[str] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        if manifest.get("base_url", BASE_URL).rstrip("/") != BASE_URL:
            raise ManifestError(f"base_url must be {BASE_URL}")
        seen: set[str] = set()
        for law in manifest.get("laws") or []:
            law_id = str(law.get("law_id", ""))
            if not _LAW_ID.match(law_id):
                raise ManifestError(f"invalid law_id {law.get('law_id')!r}")
            if law_id in seen:
                raise ManifestError(f"{law_id} is listed twice")
            seen.add(law_id)
        manifest_crawl_delay(manifest, minimum=MIN_DELAY)
        mode = manifest.get("mode", "api")
        if mode not in MODES:
            raise ManifestError(f"mode must be one of {', '.join(MODES)} (got {mode!r})")
        if mode == "bulk":
            bulk = manifest.get("bulk")
            if not isinstance(bulk, dict) or bulk.get("url") != BULK_URL:
                raise ManifestError(f"mode: bulk needs bulk.url {BULK_URL}")
            pinned = (bulk.get("bytes"), bulk.get("sha256"))
            if pinned != (None, None) and not (
                isinstance(pinned[0], int) and isinstance(pinned[1], str) and _SHA256.match(pinned[1])
            ):
                raise ManifestError(
                    "bulk.bytes and bulk.sha256 pin the archive: both set (an integer, 64 hex) or both null"
                )
        return manifest

    def _get(self, url: str, cache_key: str) -> tuple[bytes, dict[str, Any]]:
        assert self.http is not None
        got = self.http.get(url, cache_key=cache_key)
        if got is None:
            raise FetchError(f"{url}: not found")
        return got

    def _law(self, law: dict[str, Any]) -> list[RawDocument]:
        """All documents of one law: the texts of its revisions, oldest first, then the revision list.
        Nothing is yielded unless every request for the law succeeded, so a failed law leaves no partial history."""
        law_id = str(law["law_id"])
        list_url = f"{BASE_URL}/law_revisions/{law_id}?response_format=xml"
        listing, list_meta = self._get(list_url, f"api2/law_revisions/{law_id}.xml")
        wanted = law.get("history_dates")
        documents = []
        for rev in revisions_to_fetch(
            listing, bool(law.get("history", False)), {str(d) for d in wanted} if wanted is not None else None
        ):
            url = f"{BASE_URL}/law_data/{rev.law_revision_id}?response_format=xml"
            data, meta = self._get(url, f"api2/law_data/{rev.law_revision_id}.xml")
            documents.append(self._raw(f"e-gov:{rev.law_revision_id}", data, meta, law_id, rev))
        # After the texts: the list reports amendments not yet in force for the current revision.
        documents.append(self._raw(f"e-gov:{law_id}:law_revisions", listing, list_meta, law_id, None))
        return documents

    def _archive(self, bulk: dict[str, Any]) -> BulkArchive:
        """The bulk archive: downloaded once (one request) into the cache, then checked against the pin."""
        assert self.http is not None
        path = self.http.cache_path(BULK_URL, BULK_CACHE_KEY)
        meta_path = path.with_name(path.name + ".meta.json")
        if not (path.exists() and meta_path.exists()):
            self._get(BULK_URL, BULK_CACHE_KEY)  # PoliteHttp stores the file and its meta; the bytes are dropped
        size, sha256 = path.stat().st_size, _file_sha256(path)
        if bulk.get("sha256") is not None and (size, sha256) != (bulk.get("bytes"), bulk.get("sha256")):
            raise FetchError(
                f"{BULK_URL}: the archive in {path} ({size} bytes, SHA-256 {sha256}) is not the one the manifest pins "
                f"({bulk.get('bytes')} bytes, SHA-256 {bulk.get('sha256')}). e-Gov serves only its latest archive: "
                "review it, then move bulk.bytes and bulk.sha256 to it (or set both to null to take the latest)"
            )
        try:
            return BulkArchive(path, json.loads(meta_path.read_text()), url=BULK_URL, sha256=sha256, size=size)
        except zipfile.BadZipFile as exc:
            raise FetchError(f"{BULK_URL}: {path} is not a zip archive: {exc}") from exc

    def _list(self, archive_sha256: str) -> dict[str, Listed]:
        """The API list of laws in force, read once per archive (cached under its SHA-256)."""
        pages: list[tuple[Any, dict[str, Any]]] = []
        offset = 0
        while True:
            url = f"{BASE_URL}/laws?repeal_status=None&limit={LIST_PAGE}&offset={offset}&response_format=xml"
            data, meta = self._get(url, f"api2/laws/bulk-{archive_sha256[:16]}/offset{offset:06d}.xml")
            root = _list_page(data)
            pages.append((root, meta))
            count = len(root.findall("laws/law"))
            offset += count
            if count == 0 or offset >= int(root.findtext("total_count") or 0):
                return list_laws(pages)

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.fetch_errors = []
        host = urllib.parse.urlsplit(BASE_URL).netloc
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={host},
            crawl_delay=manifest_crawl_delay(manifest, minimum=MIN_DELAY),
            offline=self.offline,
            sleep=self._sleep,
            honour_robots_delay=True,
        )
        archive: BulkArchive | None = None
        listed: dict[str, Listed] = {}
        if manifest.get("mode", "api") == "bulk":
            archive = self._archive(manifest["bulk"])
            listed = self._list(archive.sha256)
        failed_in_a_row = 0
        for law in manifest["laws"]:
            try:
                if archive is not None and not law.get("history", False):
                    documents = archive.law(str(law["law_id"]), listed.get(str(law["law_id"])))
                else:  # the API: single laws, and the earlier revisions the archive does not hold
                    documents = self._law(law)
            except FetchError as exc:
                if not isinstance(exc, BulkMismatch) and (_REFUSAL.search(str(exc)) or self.offline):
                    raise  # a refusal stops the run; so does a missing cache entry when offline
                self.fetch_errors.append(f"e-gov:{law['law_id']}: {exc}; skipped")
                failed_in_a_row += 1
                if failed_in_a_row >= MAX_CONSECUTIVE_ERRORS:
                    raise FetchError(f"{failed_in_a_row} laws failed in a row (last: {exc}); stopping") from exc
                continue
            failed_in_a_row = 0
            yield from documents

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
