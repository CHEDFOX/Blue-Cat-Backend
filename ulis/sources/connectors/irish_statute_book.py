"""Polite fetcher for the electronic Irish Statute Book (www.irishstatutebook.ie), Acts as enacted.

For every Act in the manifest it downloads the XML rendition that the site's open-data page
documents (``https://www.irishstatutebook.ie/eli/<year>/act/<number>/enacted/en/xml``: "The XML
source for Acts from 1922 to date is available using the ELI format of xml") and yields it exactly as
received. The DTD the XML may name (``legislation.dtd``) is never fetched.

Built on :class:`PoliteHttp`: identifying User-Agent, 5 s between requests (the site has no
robots.txt), HTTPS to this one host only, refusals respected, and a cache under
``<cache_dir>/www.irishstatutebook.ie/eli/<year>/act/<number>/enacted/en/xml`` so runs can be repeated
offline.
"""

from __future__ import annotations

import hashlib
import time
import urllib.parse
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

HOST = "www.irishstatutebook.ie"
BASE_URL = f"https://{HOST}"


def act_url(year: int, number: int) -> str:
    return f"{BASE_URL}/eli/{year}/act/{number}/enacted/en/xml"


class IrishStatuteBookConnector:
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
        if manifest.get("base_url", BASE_URL).rstrip("/") != BASE_URL:
            raise ManifestError(f"base_url must be {BASE_URL}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        acts = manifest.get("acts") or []
        if not acts:
            raise ManifestError("the manifest lists no acts")
        for act in acts:
            year, number = act.get("year"), act.get("number")
            if not (isinstance(year, int) and 1922 <= year <= 2100 and isinstance(number, int) and 1 <= number <= 999):
                raise ManifestError(f"invalid act reference {year!r}/{number!r}")
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
        for act in manifest["acts"]:
            year, number = act["year"], act["number"]
            url = act_url(year, number)
            got = self.http.get(url)
            if got is None:
                raise FetchError(f"{url}: not found (404)")
            data, meta = got
            final = meta.get("final_url") or url
            if urllib.parse.urlsplit(final).netloc != HOST:
                raise FetchError(f"{url}: redirected away from {HOST} to {final}; not ingested")
            head = data[:600].lstrip().lower()
            if not (head.startswith(b"<?xml") or head.startswith(b"<!doctype act") or head.startswith(b"<act")):
                raise FetchError(
                    f"{url}: response is not Act XML ({meta.get('content_type')}); not ingested. "
                    f"Remove {self.http.cache_path(url)} after checking the site."
                )
            pinned = act.get("sha256")
            if pinned and hashlib.sha256(data).hexdigest() != pinned:
                raise ManifestError(f"{year}/{number}: sha256 does not match the manifest pin")
            yield RawDocument(
                external_id=f"irishstatutebook:act/{year}/{number}",
                uri=final,
                media_type="application/xml",
                content=data,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"year": year, "number": number, "cached_fetch": meta},
            )
