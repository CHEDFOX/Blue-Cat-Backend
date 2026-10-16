"""Polite fetcher for Danish lovbekendtgørelser from Retsinformation (Civilstyrelsen).

Retsinformation publishes every document under a European Legislation Identifier (ELI) and offers
it as Lex Dania XML at ``https://www.retsinformation.dk/eli/<pubMedia>/<year>/<number>/xml``
(documented in the FAQ "Hvordan får jeg adgang til data ..." and the ELI documentation). The
manifest lists, per law, the consolidations (lovbekendtgørelser) to fetch, oldest first; the
connector fetches exactly those and yields them in that order (snapshots are recorded oldest
first, see ``ulis.ingestion.snapshots``). Nothing is discovered or inferred at run time: the committed manifest
is generated from the publisher's ELI sitemap and ELI metadata by ``scripts/build_dk_manifest.py`` (one current
consolidation for each law in force, the full series for two pilot Acts). A consolidation that is not served (404) or
has no Lex Dania XML (the website's HTML shell) is reported in ``fetch_errors`` and the run goes on; a refusal stops it.

Rules built in through :class:`PoliteHttp`: an identifying User-Agent, HTTPS only to
``www.retsinformation.dk``, a pause between requests (robots.txt states no crawl delay; the
harvest API allows one call per 10 s, and ULIS keeps that pace for documents too), back-off on
429/5xx, any refusal stops the run, every response cached so nothing is fetched twice. The
harvest API (``api.retsinformation.dk``, open 03:00-23:45, a change feed) is not used.
"""

from __future__ import annotations

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

BASE = "https://www.retsinformation.dk/eli"
HOST = "www.retsinformation.dk"
# ELI of a document published in Lovtidende A: lta/<year>/<number>
ELI_PATH = re.compile(r"^lta/(\d{4})/(\d{1,5})$")

__all__ = ["FetchError", "RetsinformationConnector", "xml_url"]


def xml_url(eli: str) -> str:
    return f"{BASE}/{eli}/xml"


class RetsinformationConnector:
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
        self.fetch_errors: list[str] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        if manifest.get("base_url", BASE) != BASE:
            raise ManifestError(f"base_url must be {BASE}")
        if float(manifest.get("crawl_delay_seconds", 10)) < 10:
            raise ManifestError("crawl_delay_seconds must be at least 10 (the publisher's one call per 10 s)")
        seen: set[str] = set()
        for work in manifest["works"]:
            elis = work.get("consolidations") or []
            if not work.get("name") or not elis or not all(ELI_PATH.match(str(e)) for e in elis):
                raise ManifestError(f"invalid work {work!r}")
            for eli in elis:
                if eli in seen:
                    raise ManifestError(f"{eli} is listed twice")
                seen.add(eli)
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.fetch_errors = []
        http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", 10)),
            offline=self.offline,
            sleep=self._sleep,
        )
        for work in manifest["works"]:
            for eli in work["consolidations"]:  # oldest first, as listed
                url = xml_url(eli)
                got = http.get(url, cache_key=f"eli/{eli}/xml", headers={"Accept": "application/xml"})
                if got is None:
                    self.fetch_errors.append(f"retsinformation:{eli}: {work['name']}: {url} not found (404); skipped")
                    continue
                data, meta = got
                # Unknown paths are answered with the website's HTML shell (status 200): never pass it on.
                if b"<Dokument" not in data[:1000]:
                    self.fetch_errors.append(f"retsinformation:{eli}: {url}: not a Lex Dania XML document; skipped")
                    continue
                yield RawDocument(
                    external_id=f"retsinformation:{eli}",
                    uri=url,
                    media_type="application/xml",
                    content=data,
                    retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                    metadata={"work": work["name"], "eli": f"{BASE}/{eli}", "fetched_url": meta["url"]},
                )
