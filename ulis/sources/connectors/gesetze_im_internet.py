"""Polite fetcher for Gesetze im Internet (www.gesetze-im-internet.de), German federal law.

For every law in the manifest it downloads ``https://www.gesetze-im-internet.de/<abbr>/xml.zip``
(the site's own XML download, offered "für die Nutzer ..., die die ... Gesetze und
Rechtsverordnungen über automatisierte Verfahren weiterverarbeiten möchten") and yields the
archive exactly as received; the parser opens it. The DTD the XML refers to is never fetched.

Built on :class:`PoliteHttp`: identifying User-Agent, 5 s between requests (robots.txt sets no
Crawl-delay and disallows nothing), HTTPS to this one host only, refusals respected, and a cache
under ``<cache_dir>/www.gesetze-im-internet.de/<abbr>/xml.zip`` so runs can be repeated offline.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import PoliteHttp

HOST = "www.gesetze-im-internet.de"
BASE_URL = f"https://{HOST}"
_SLUG = re.compile(r"^[a-z0-9_][a-z0-9_\-]*$")  # 35 real laws start with an underscore (_ag, _anlg ...)


class GesetzeImInternetConnector:
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
        # Laws that were skipped (404, or an answer that is not a zip): reported as run errors by the pipeline so
        # one bad entry among thousands does not abort the whole source. A refusal (403/429) still stops the run.
        self.fetch_errors: list[str] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if manifest.get("base_url", BASE_URL).rstrip("/") != BASE_URL:
            raise ManifestError(f"base_url must be {BASE_URL}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        laws = manifest.get("laws") or []
        if not laws:
            raise ManifestError("the manifest lists no laws")
        for law in laws:
            if not _SLUG.match(str(law.get("abbr", ""))):
                raise ManifestError(f"invalid law path {law.get('abbr')!r}")
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
        for law in manifest["laws"]:
            abbr = law["abbr"]
            url = f"{BASE_URL}/{abbr}/xml.zip"
            got = self.http.get(url)
            if got is None:
                self.fetch_errors.append(f"gesetze-im-internet:{abbr}: {url}: not found (404)")
                continue
            data, meta = got
            if not data.startswith(b"PK\x03\x04"):
                self.fetch_errors.append(
                    f"gesetze-im-internet:{abbr}: {url}: response is not a zip archive ({meta.get('content_type')}); "
                    f"not ingested. Remove {self.http.cache_path(url)} after checking the site."
                )
                continue
            pinned = law.get("sha256")
            if pinned and hashlib.sha256(data).hexdigest() != pinned:
                raise ManifestError(f"{abbr}: sha256 does not match the manifest pin")
            yield RawDocument(
                external_id=f"gesetze-im-internet:{abbr}",
                uri=meta.get("final_url") or url,
                media_type="application/zip",
                content=data,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"abbr": abbr, "cached_fetch": meta},
            )
