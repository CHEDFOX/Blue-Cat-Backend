"""Shared retrieval rules for the GovInfo connectors (United States Reports, US Courts opinions).

GovInfo (www.govinfo.gov, U.S. Government Publishing Office) publishes ``robots.txt`` with a sitemap per collection and
no crawl delay, and states that sitemaps "can be used by systems to crawl and harvest content from GovInfo". ULIS uses
only the keyless routes: the collection sitemaps, ``/metadata/pkg/<package>/mods.xml`` and ``/content/pkg/...``.
It never
uses the JSON API (``api.govinfo.gov``), which needs an api.data.gov key. Requests are sequential, at least 5 s apart,
cached, and every URL is checked against the host's ``robots.txt``, read first.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

HOST = "www.govinfo.gov"
BASE_URL = f"https://{HOST}"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
MIN_DELAY = 5.0
PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,80}$")

__all__ = ["BASE_URL", "HOST", "MIN_DELAY", "GovInfoClient", "load_manifest"]


def load_manifest(path: Path, expected_source_id: str) -> dict[str, Any]:
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("source_id") != expected_source_id:
        raise ManifestError("manifest schema or source_id mismatch")
    if str(manifest.get("base_url", "")).rstrip("/") != BASE_URL:
        raise ManifestError(f"base_url must be {BASE_URL}")
    if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
        raise ManifestError("an identifying user_agent with contact details is required")
    if float(manifest.get("crawl_delay_seconds", MIN_DELAY)) < MIN_DELAY:
        raise ManifestError(f"crawl_delay_seconds must be at least {MIN_DELAY:g}")
    return manifest  # type: ignore[no-any-return]


class GovInfoClient:
    """PoliteHttp for www.govinfo.gov, with ``robots.txt`` read first and applied to every URL."""

    def __init__(
        self, manifest: dict[str, Any], cache_dir: Path, *, offline: bool = False, sleep: Any = time.sleep
    ) -> None:
        self.http = PoliteHttp(
            cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", MIN_DELAY)),
            offline=offline,
            sleep=sleep,
        )
        got = self.http.get(ROBOTS_URL, cache_key="robots.txt")
        # No robots.txt (404) would mean no restrictions (RFC 9309); any other failure has already stopped the run.
        self.robots: str | None = got[0].decode("utf-8", "replace") if got is not None else None

    def get(self, url: str, cache_key: str) -> tuple[bytes, dict[str, Any]] | None:
        if self.robots is not None and not robots_allows(self.robots, self.http.user_agent, url):
            raise FetchError(f"{url}: disallowed by {ROBOTS_URL}; not fetched")
        return self.http.get(url, cache_key=cache_key)
