"""Polite fetcher for consolidated Swedish statutes from the Riksdag's open data.

Each statute in Svensk författningssamling has a document id ``sfs-<year>-<number>``
(documented at https://www.riksdagen.se/sv/dokument-och-lagar/riksdagens-oppna-data/anvandarstod/sa-fungerar-dokument-id/);
``https://data.riksdagen.se/dokument/sfs-1974-152.json`` returns the document as JSON with its
metadata, the consolidated text and an HTML rendering. The connector fetches exactly the
statutes the manifest lists, one request each, and yields the JSON exactly as received.

Rules built in through :class:`PoliteHttp`: an identifying User-Agent, HTTPS only to
``data.riksdagen.se``, a pause between requests (data.riksdagen.se publishes no robots.txt, and
www.riksdagen.se/robots.txt sets no delay for other agents, so 5 s), back-off on 429/5xx, any
refusal (401/403/451) stops the run, and every response is cached with its URL and retrieval time
so nothing is fetched twice and runs can be repeated offline.
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

BASE = "https://data.riksdagen.se/dokument"
HOST = "data.riksdagen.se"
_SFS = re.compile(r"^(\d{4}):(\d{1,4})$")

__all__ = ["FetchError", "RiksdagenSfsConnector", "document_id"]


def document_id(sfs: str) -> str:
    """``1974:152`` -> ``sfs-1974-152``."""
    m = _SFS.match(sfs.strip())
    if not m:
        raise ManifestError(f"invalid SFS number {sfs!r} (expected YYYY:N)")
    return f"sfs-{m.group(1)}-{int(m.group(2))}"


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

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        if manifest.get("base_url", BASE) != BASE:
            raise ManifestError(f"base_url must be {BASE}")
        for work in manifest.get("works") or []:
            document_id(str(work.get("sfs", "")))
        return manifest

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
            doc_id = document_id(str(work["sfs"]))
            url = f"{BASE}/{doc_id}.json"
            got = self.http.get(url, headers={"Accept": "application/json"})
            if got is None:
                raise FetchError(f"{url}: not found")
            content, meta = got
            yield RawDocument(
                external_id=f"riksdagen:{doc_id}",
                uri=url,
                media_type="application/json",
                content=content,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"sfs": str(work["sfs"]), "document_id": doc_id, "fetched_url": meta["url"]},
            )
