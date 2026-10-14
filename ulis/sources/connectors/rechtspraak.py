"""Fetches Dutch court decisions from the Open Data van de Rechtspraak web service.

For each ECLI in the manifest (``config/manifests/nl-rechtspraak.yaml``) it requests
``https://data.rechtspraak.nl/uitspraken/content?id=<ECLI>``, which returns the decision (metadata,
inhoudsindicatie and text) as one XML document, and yields it exactly as received.

Access rules (see ``config/sources/nl-rechtspraak.yaml``): the publisher describes the service as free
and public, asks users not to hammer the servers and to avoid concurrent requests, and allows at most
10 requests per second; ULIS makes one request at a time, at least 5 s apart (PoliteHttp: identifying
User-Agent, back-off on 429/5xx, any other refusal stops the run, HTTPS to this one host only, cache
under ``<cache_dir>/data.rechtspraak.nl/content/`` so runs can be repeated offline). ``robots.txt`` of
the host is read first (it publishes none: 404, which means no restrictions) and every URL is checked
against it. The ECLI index (``/uitspraken/zoeken``) was used by hand to choose the manifest's ECLIs;
the connector itself does not search.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from defusedxml.ElementTree import fromstring

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

HOST = "data.rechtspraak.nl"
BASE_URL = f"https://{HOST}"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
MIN_DELAY = 5.0
ECLI = re.compile(r"^ECLI:NL:[A-Z0-9]{2,12}:\d{4}:[A-Za-z0-9]{1,25}$")
DC = "{http://purl.org/dc/terms/}"

__all__ = ["FetchError", "RechtspraakConnector", "content_url"]


def content_url(ecli: str) -> str:
    return f"{BASE_URL}/uitspraken/content?id={ecli}"


def cache_key(ecli: str) -> str:
    return f"content/{ecli.replace(':', '_')}.xml"


class RechtspraakConnector:
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
        self.robots: str | None = None

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if str(manifest.get("base_url", "")).rstrip("/") != BASE_URL:
            raise ManifestError(f"base_url must be {BASE_URL}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        if float(manifest.get("crawl_delay_seconds", MIN_DELAY)) < MIN_DELAY:
            raise ManifestError(f"crawl_delay_seconds must be at least {MIN_DELAY:g}")
        decisions = manifest.get("decisions") or []
        if not decisions:
            raise ManifestError("the manifest lists no decisions")
        seen: set[str] = set()
        for decision in decisions:
            ecli = str(decision.get("ecli", ""))
            if not ECLI.match(ecli):
                raise ManifestError(f"invalid ECLI {ecli!r}")
            if ecli in seen:
                raise ManifestError(f"{ecli} is listed twice")
            seen.add(ecli)
        return manifest

    def _get(self, ecli: str) -> tuple[bytes, dict[str, Any]]:
        assert self.http is not None
        url = content_url(ecli)
        if self.robots is not None and not robots_allows(self.robots, self.http.user_agent, url):
            raise FetchError(f"{url}: disallowed by {ROBOTS_URL}; not fetched")
        got = self.http.get(url, cache_key=cache_key(ecli))
        if got is None:
            raise FetchError(f"{url}: not found (404)")
        return got

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", MIN_DELAY)),
            offline=self.offline,
            sleep=self._sleep,
        )
        # No robots.txt (404) means no restrictions (RFC 9309, 2.3.1.3); any other failure stops the run.
        got = self.http.get(ROBOTS_URL, cache_key="robots.txt")
        self.robots = got[0].decode("utf-8", "replace") if got is not None else None
        for decision in manifest["decisions"]:
            ecli = decision["ecli"]
            content, meta = self._get(ecli)
            self._check(ecli, content, meta)
            yield RawDocument(
                external_id=f"rechtspraak:{ecli}",
                uri=content_url(ecli),
                media_type="application/xml",
                content=content,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"ecli": ecli, "cached_fetch": meta},
            )

    @staticmethod
    def _check(ecli: str, content: bytes, meta: dict[str, Any]) -> None:
        """The response must be the open-data document of exactly this ECLI; anything else is not ingested."""
        url = content_url(ecli)
        if "xml" not in str(meta.get("content_type") or "").lower():
            raise FetchError(f"{url}: expected XML, got {meta.get('content_type')!r}; not ingested")
        try:
            root = fromstring(content, forbid_dtd=True)
        except Exception as exc:  # defusedxml raises several exception types
            raise FetchError(f"{url}: not well-formed XML ({exc}); not ingested") from exc
        identifier = root.find(f".//{DC}identifier")
        if root.tag != "open-rechtspraak" or identifier is None or (identifier.text or "").strip() != ecli:
            raise FetchError(f"{url}: the response is not the open-data document of {ecli}; not ingested")
