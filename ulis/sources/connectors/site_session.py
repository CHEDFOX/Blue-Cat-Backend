"""Shared plumbing of the connectors that read a publisher's website page by page.

A *manifest* (YAML in ``config/manifests/``) names the documents; the connector fetches exactly those
pages through :class:`PoliteHttp` (identifying User-Agent, at least 5 s between requests, HTTPS to the one
host, cache under ``<cache_dir>/<host>/``, refusals respected) and checks every path against the host's
``robots.txt`` before requesting it. This module holds the pieces they share: manifest validation, the
robots check and the fetch of a single page.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.likumi_lv import robots_disallowed
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

MIN_DELAY = 5.0


class PageNotAvailable(FetchError):
    """The page does not exist (404) or robots.txt disallows it: the entry is skipped, the run goes on.

    Any other refusal (401, 403, 429 ...) is a plain ``FetchError`` and stops the run.
    """


def load_manifest(path: Path, expected_source_id: str, base_url: str, list_key: str) -> dict[str, Any]:
    """Read and validate a page-fetching manifest (see the connectors' modules for the entries)."""
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ManifestError("the manifest is not a mapping")
    if manifest.get("schema_version") != 1 or manifest.get("source_id") != expected_source_id:
        raise ManifestError("manifest schema or source_id mismatch")
    if str(manifest.get("base_url", base_url)).rstrip("/") != base_url:
        raise ManifestError(f"base_url must be {base_url}")
    if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
        raise ManifestError("an identifying user_agent with contact details is required")
    if float(manifest.get("crawl_delay_seconds", MIN_DELAY)) < MIN_DELAY:
        raise ManifestError(f"crawl_delay_seconds must be at least {MIN_DELAY:g}")
    if not manifest.get(list_key):
        raise ManifestError("the manifest lists no documents")
    return manifest


class SiteSession:
    """One polite session on one host: the robots rules are read once, every page is checked against them."""

    def __init__(
        self,
        manifest: dict[str, Any],
        host: str,
        cache_dir: Path,
        *,
        offline: bool = False,
        sleep: Any = time.sleep,
        robots_required: bool = True,
    ):
        self.host = host
        self.base_url = f"https://{host}"
        self.http = PoliteHttp(
            cache_dir,
            manifest["user_agent"],
            allowed_hosts={host},
            crawl_delay=float(manifest.get("crawl_delay_seconds", MIN_DELAY)),
            offline=offline,
            sleep=sleep,
        )
        got = self.http.get(f"{self.base_url}/robots.txt")
        if got is None:
            if robots_required:
                raise FetchError(f"{self.base_url}/robots.txt: not found; refusing to fetch without reading it")
            self.disallowed: list[re.Pattern[str]] = []  # the host publishes no robots.txt: no rules to apply
        else:
            self.disallowed = robots_disallowed(got[0].decode("utf-8", "replace"))

    def get(self, path: str) -> tuple[bytes, dict[str, Any]]:
        """Fetch ``path`` (with its query) from the host; a robots-disallowed path or a 404 raises ``FetchError``."""
        if any(rule.match(path) for rule in self.disallowed):
            raise PageNotAvailable(f"{self.base_url}{path} is disallowed by robots.txt; not fetched")
        url = f"{self.base_url}{path}"
        got = self.http.get(url)
        if got is None:
            raise PageNotAvailable(f"{url}: not found (404)")
        return got
