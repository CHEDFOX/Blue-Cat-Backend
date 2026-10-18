"""Fetches consolidated Spanish State legislation from the BOE open-data API.

One request per norm: ``GET https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/{id}``
with ``Accept: application/xml`` returns the metadata, the legal analysis and the consolidated text
with every version of every block, so the full version history costs one request. The manifest is
generated from the same API's list endpoint (``scripts/build_boe_manifest.py``: every State norm).

Reuse is governed by the AEBOE reuse conditions (https://www.boe.es/informacion/aviso_legal/
index.php#reutilizacion). robots.txt sets no Crawl-delay and does not disallow ``/datosabiertos/``;
``PoliteHttp`` identifies ULIS, waits the manifest's delay between request starts (at least 1 s, and never
less than a Crawl-delay robots.txt may set), uses one connection, respects refusals and caches every
response under ``<cache>/www.boe.es/legislacion-consolidada/``.

Robustness for a manifest of thousands of norms: robots.txt is read first, and a norm whose API path one
of its ``Disallow`` rules names (it carries about 12,000 per-document rules for other paths) is skipped; a
norm the API does not know (404) is skipped and reported in ``fetch_errors`` and the run continues; a
refusal (403, 429 ...) raises and stops the run.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.likumi_lv import robots_disallowed
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import PoliteHttp, manifest_crawl_delay

API = "https://www.boe.es/datosabiertos/api/legislacion-consolidada"
HOST = "www.boe.es"
_ID = re.compile(r"^BOE-[A-Z]-\d{4}-\d{1,6}$")
_ID_IN_RULE = re.compile(r"BOE-[A-Z]-\d{4}-\d{1,6}")


def robots_rules_by_id(robots: str) -> tuple[dict[str, list[re.Pattern[str]]], list[re.Pattern[str]]]:
    """The ``Disallow`` rules for all agents, split into rules that name a BOE identifier and the rest."""
    by_id: dict[str, list[re.Pattern[str]]] = defaultdict(list)
    generic: list[re.Pattern[str]] = []
    for rule in robots_disallowed(robots):
        ids = _ID_IN_RULE.findall(rule.pattern.replace("\\", ""))
        if ids:
            for boe_id in ids:
                by_id[boe_id].append(rule)
        else:
            generic.append(rule)
    return by_id, generic


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
        self.fetch_errors: list[str] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if manifest.get("api_base", API) != API:
            raise ManifestError(f"api_base must be {API}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        manifest_crawl_delay(manifest)
        seen: set[str] = set()
        for doc in manifest["documents"]:
            boe_id = str(doc.get("id", ""))
            if not _ID.match(boe_id) or boe_id in seen:
                raise ManifestError(f"invalid or repeated BOE identifier {doc.get('id')!r}")
            seen.add(boe_id)
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=manifest_crawl_delay(manifest),
            offline=self.offline,
            sleep=self._sleep,
            honour_robots_delay=True,
        )
        try:
            # Read afresh on every online run (offline: the cached copy): its Disallow rules and any Crawl-delay.
            robots = http.get(f"https://{HOST}/robots.txt", refresh=True)
            by_id, generic = robots_rules_by_id(robots[0].decode("utf-8", "replace")) if robots else ({}, [])
            for doc in manifest["documents"]:
                boe_id = doc["id"]
                url = f"{API}/id/{boe_id}"
                path = url.removeprefix(f"https://{HOST}")
                if any(rule.match(path) for rule in (*by_id.get(boe_id, ()), *generic)):
                    self.fetch_errors.append(f"{boe_id}: {path} is disallowed by robots.txt; not fetched")
                    continue
                got = http.get(
                    url, cache_key=f"legislacion-consolidada/{boe_id}.xml", headers={"Accept": "application/xml"}
                )
                if got is None:
                    self.fetch_errors.append(f"{boe_id}: not found (404); skipped")
                    continue
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
