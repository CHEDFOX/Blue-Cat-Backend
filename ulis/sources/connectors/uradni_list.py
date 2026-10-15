"""Polite fetcher for Uradni list RS (https://www.uradni-list.si), the official gazette of Slovenia.

The gazette's own legal notice (https://www.uradni-list.si/podjetje/pravna-obvestila, general terms 2.1) restricts
the use
of the site's materials to personal, non-commercial viewing and then says: "Omejitve uporabe gradiv ne veljajo za
Uradni
list Republike Slovenije in v njem objavljene vsebine" - the restrictions do not apply to the Official Gazette of the
Republic of Slovenia and the content published in it. Its robots.txt disallows only ``/_pdf/*/*a/``.

For every act in the manifest the connector fetches ``/glasilo-uradni-list-rs/vsebina/<year>-<part>-<number>`` (the
address
without the title slug, which the site accepts) and yields the page exactly as received. Built on :class:`PoliteHttp`
(identifying User-Agent, 5 s between requests, HTTPS to ``www.uradni-list.si`` only, cache under
``<cache_dir>/www.uradni-list.si/``, refusals respected). An act that is missing, disallowed or not an act page is
reported in
``fetch_errors`` and the run goes on; a refusal stops it.

Manifest (``config/manifests/si-uradni-list.yaml``)::

    schema_version: 1
    source_id: si-uradni-list
    base_url: https://www.uradni-list.si
    user_agent: "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
    crawl_delay_seconds: 5
    acts:
      - {id: "2025-01-3978", title: "Zakon o varstvu javnega reda in miru (ZJRM-2)", issue: "112/2025"}
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.site_session import PageNotAvailable, SiteSession, load_manifest

HOST = "www.uradni-list.si"
BASE_URL = f"https://{HOST}"
ACT_ID = re.compile(r"(?:19[89][0-9]|20[0-9]{2})-[0-9]{2}-[0-9]{1,6}")  # the number may be zero-padded: 2006-01-0970


class UradniListConnector:
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
        self.session: SiteSession | None = None
        self.fetch_errors: list[str] = []

    @property
    def http(self):  # type: ignore[no-untyped-def]
        return self.session.http if self.session else None

    def _manifest(self) -> dict[str, Any]:
        manifest = load_manifest(self.manifest_path, self.expected_source_id, BASE_URL, "acts")
        seen: set[str] = set()
        for act in manifest["acts"]:
            act_id = str(act.get("id", ""))
            if not ACT_ID.fullmatch(act_id):
                raise ManifestError(f"invalid act id {act.get('id')!r} (expected <year>-<part>-<number>)")
            if act_id in seen:
                raise ManifestError(f"{act_id} is listed twice")
            seen.add(act_id)
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.fetch_errors = []
        self.session = SiteSession(manifest, HOST, self.cache_dir, offline=self.offline, sleep=self._sleep)
        for act in manifest["acts"]:
            act_id = str(act["id"])
            path = f"/glasilo-uradni-list-rs/vsebina/{act_id}"
            try:
                data, meta = self.session.get(path)
                if b'class="content-segments"' not in data or f"/vsebina/{act_id}".encode() not in data:
                    raise PageNotAvailable(f"{BASE_URL}{path}: not the page of act {act_id}")
            except PageNotAvailable as exc:
                self.fetch_errors.append(f"si-ul:{act_id}: {exc}")
                continue
            yield RawDocument(
                external_id=f"si-ul:{act_id}",
                uri=f"{BASE_URL}{path}",
                media_type="text/html",
                content=data,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"ul_id": act_id, "title": act.get("title"), "issue": act.get("issue"), "cached_fetch": meta},
            )
