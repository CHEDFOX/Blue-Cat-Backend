"""Fetches consolidated Spanish State legislation from the BOE open-data API.

One request per norm: ``GET https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/{id}``
with ``Accept: application/xml`` returns the metadata, the legal analysis and the consolidated text
with every version of every block, so the full version history costs one request.

Reuse is governed by the AEBOE reuse conditions (https://www.boe.es/informacion/aviso_legal/
index.php#reutilizacion). robots.txt sets no Crawl-delay and does not disallow ``/datosabiertos/``;
``PoliteHttp`` identifies ULIS, waits 5 s between requests, respects refusals and caches every
response under ``<cache>/www.boe.es/legislacion-consolidada/``.
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

API = "https://www.boe.es/datosabiertos/api/legislacion-consolidada"
HOST = "www.boe.es"
_ID = re.compile(r"^BOE-[A-Z]-\d{4}-\d{1,6}$")


class BoeConsolidadaConnector:
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
        self.requests_made = 0

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if manifest.get("api_base", API) != API:
            raise ManifestError(f"api_base must be {API}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        for doc in manifest["documents"]:
            if not _ID.match(str(doc.get("id", ""))):
                raise ManifestError(f"invalid BOE identifier {doc.get('id')!r}")
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=max(5.0, float(manifest.get("crawl_delay_seconds", 5))),
            offline=self.offline,
            sleep=self._sleep,
        )
        try:
            for doc in manifest["documents"]:
                boe_id = doc["id"]
                url = f"{API}/id/{boe_id}"
                got = http.get(
                    url, cache_key=f"legislacion-consolidada/{boe_id}.xml", headers={"Accept": "application/xml"}
                )
                if got is None:
                    raise FetchError(f"{boe_id}: not found (404)")
                content, meta = got
                yield RawDocument(
                    external_id=f"boe-consolidada:{boe_id}",
                    uri=meta.get("final_url") or url,
                    media_type="application/xml",
                    content=content,
                    retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                    metadata={"boe_id": boe_id, "title": doc.get("title"), "cached_fetch": meta},
                )
        finally:
            self.requests_made += http.requests_made
