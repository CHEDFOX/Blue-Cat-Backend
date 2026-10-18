"""Polite fetcher for consolidated Swedish statutes from the Riksdag's open data.

Each statute in Svensk författningssamling has a document id ``sfs-<year>-<number>``
(documented at https://www.riksdagen.se/sv/dokument-och-lagar/riksdagens-oppna-data/anvandarstod/sa-fungerar-dokument-id/):
the SFS number as the Riksdag prints it with the colon replaced by a hyphen, so a statute whose number carries a
balk or page suffix has an id with a space, ``sfs-1736-0123 2`` (Handelsbalk) or ``sfs-1907-36 s. 22``, which the
request URL percent-encodes. ``https://data.riksdagen.se/dokument/sfs-1974-152.json`` returns the document as JSON
with its metadata, the consolidated text and an HTML rendering. The connector fetches exactly the statutes the
manifest lists, one request each, and yields the JSON exactly as received. The committed manifest lists every
consolidated statute in force of the Riksdag's own document list (about 5,350; see ``scripts/build_sfs_manifest.py``),
so one statute that is no longer served (404) is reported in ``fetch_errors`` and the run goes on; any other
refusal stops it.

Rules built in through :class:`PoliteHttp`: an identifying User-Agent, HTTPS only to
``data.riksdagen.se``, one connection, the manifest's delay between request starts (at least 1 s;
data.riksdagen.se publishes no robots.txt - it is checked before the first request of a run, and a
Crawl-delay there would win - and www.riksdagen.se/robots.txt sets "crawl-delay:1" only for named
crawlers, which the 1 s minimum already meets), back-off on 429/5xx, any
refusal (401/403/451) stops the run, and every response is cached with its URL and retrieval time
so nothing is fetched twice and runs can be repeated offline.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp, manifest_crawl_delay
from ulis.sources.parsers.riksdagen_sfs import SfsNumber, parse_sfs_number

BASE = "https://data.riksdagen.se/dokument"
HOST = "data.riksdagen.se"

__all__ = ["FetchError", "RiksdagenSfsConnector", "document_id", "sfs_number"]


def sfs_number(sfs: str) -> SfsNumber:
    """The SFS number a manifest entry names, or :class:`ManifestError` when it is not one."""
    number = parse_sfs_number(sfs)
    if number is None:
        raise ManifestError(
            f"invalid SFS number {sfs!r} (expected YYYY:N, with the balk or page suffix a few old statutes carry)"
        )
    return number


def document_id(sfs: str) -> str:
    """``1974:152`` -> ``sfs-1974-152``: the Riksdag's id is the number as printed with the colon replaced, so the
    serial keeps its leading zero (``1810:0926`` -> ``sfs-1810-0926``) and a suffix keeps its spacing
    (``1736:0123 2`` -> ``sfs-1736-0123 2``, ``1907:36 s. 22`` -> ``sfs-1907-36 s. 22``)."""
    sfs_number(sfs)
    return "sfs-" + " ".join(sfs.split()).replace(":", "-", 1)


class RiksdagenSfsConnector:
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
        if manifest.get("base_url", BASE) != BASE:
            raise ManifestError(f"base_url must be {BASE}")
        seen: set[str] = set()
        for work in manifest.get("works") or []:
            # Two spellings of one number (the list has "1904:48 s.1" and "1904:48s1") would fetch one statute twice.
            canonical = sfs_number(str(work.get("sfs", ""))).canonical
            if canonical in seen:
                raise ManifestError(f"{work.get('sfs')} is listed twice")
            seen.add(canonical)
        manifest_crawl_delay(manifest)
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.fetch_errors = []
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=manifest_crawl_delay(manifest),
            offline=self.offline,
            sleep=self._sleep,
            honour_robots_delay=True,
        )
        for work in manifest["works"]:
            doc_id = document_id(str(work["sfs"]))
            url = f"{BASE}/{quote(doc_id)}.json"  # a suffixed id has a space: sfs-1736-0123%202
            got = self.http.get(url, headers={"Accept": "application/json"})
            if got is None:
                self.fetch_errors.append(f"riksdagen:{doc_id}: {url}: not found (404); skipped")
                continue
            content, meta = got
            yield RawDocument(
                external_id=f"riksdagen:{doc_id}",
                uri=url,
                media_type="application/json",
                content=content,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"sfs": str(work["sfs"]), "document_id": doc_id, "fetched_url": meta["url"]},
            )
