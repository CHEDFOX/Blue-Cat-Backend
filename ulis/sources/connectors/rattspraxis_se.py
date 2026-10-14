"""Fetches Swedish case law reports from Domstolsverket's open data API ("Sök rättspraxis", PUH API).

``https://rattspraxis.etjanst.domstol.se/api/v1/publiceringar`` lists publications (the OpenAPI file is at
``/openapi/puh-openapi.yaml``; the dataset is listed in Sveriges dataportal with licence CC0 1.0). For each court
in the manifest the connector pages through ``GET /api/v1/publiceringar?domstolkod=<code>&publiceringsformer=REFERAT``
(``sortorder=avgorandedatum``, oldest decision first with ``asc=true``, ``page`` starting at 0,
``pagesize`` at most 100) until a page is short, and yields every
publication as its own document: the JSON object exactly as the API returned it, re-serialised canonically (sorted
keys, UTF-8, no added whitespace). The page response itself is kept byte for byte in the cache.

Access rules (see ``config/sources/se-rattspraxis.yaml``): the API publishes no ``robots.txt`` (404, which means no
restrictions) and no rate limit; ULIS sends one request at a time, at least 5 s apart (``PoliteHttp``: identifying
User-Agent, back-off on 429/5xx, any other refusal stops the run, HTTPS to this one host only, cache under
``<cache_dir>/rattspraxis.etjanst.domstol.se/``). Every URL is checked against robots.txt when one exists.

The API gives no total, so a short page ends a court's list. Paging is by date, ties are ordered by the service;
a publication seen twice is skipped and reported in ``fetch_errors`` (it means the list changed during the run).
A page that is not a JSON list, or a publication of another court or form than asked for, is not passed on.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.parsers.rattspraxis_se import COURTS

HOST = "rattspraxis.etjanst.domstol.se"
BASE_URL = f"https://{HOST}"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
API = f"{BASE_URL}/api/v1/publiceringar"
MIN_DELAY = 5.0
MAX_PAGE_SIZE = 100
MAX_PAGES = 2000
FORMS = ("REFERAT",)
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

__all__ = ["FetchError", "RattspraxisConnector", "page_url", "publication_bytes"]


def page_url(court: str, page: int, size: int, *, ascending: bool, form: str = "REFERAT") -> str:
    # The API ignores `asc` unless `sortorder` is given (it then orders newest first); the decision date is explicit.
    asc = "true" if ascending else "false"
    return (
        f"{API}?domstolkod={court}&publiceringsformer={form}&sortorder=avgorandedatum&asc={asc}"
        f"&pagesize={size}&page={page}"
    )


def publication_bytes(item: dict[str, Any]) -> bytes:
    return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class RattspraxisConnector:
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
        self.http: PoliteHttp | None = None
        self._robots: str | None = None

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
        size = manifest.get("page_size", 50)
        if not isinstance(size, int) or not 1 <= size <= MAX_PAGE_SIZE:
            raise ManifestError(f"page_size must be an integer from 1 to {MAX_PAGE_SIZE}")
        if manifest.get("forms") != list(FORMS):
            raise ManifestError(f"forms must be {list(FORMS)}")
        courts = manifest.get("courts") or []
        if not courts or len(set(courts)) != len(courts):
            raise ManifestError("courts must be a non-empty list without repeats")
        for code in courts:
            if code not in COURTS:
                raise ManifestError(f"court code {code!r} is not one the parser maps to a ULIS court")
        limit = manifest.get("max_items_per_court")
        if limit is not None and not (isinstance(limit, int) and limit >= 1):
            raise ManifestError("max_items_per_court must be a positive integer or null")
        if not isinstance(manifest.get("ascending", True), bool):
            raise ManifestError("ascending must be true or false")
        return manifest

    def _get(self, url: str, key: str) -> tuple[bytes, dict[str, Any]]:
        assert self.http is not None
        if self._robots is not None and not robots_allows(self._robots, self.http.user_agent, url):
            raise FetchError(f"{url}: disallowed by {ROBOTS_URL}; not fetched")
        got = self.http.get(url, cache_key=key)
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
        self._robots = got[0].decode("utf-8", "replace") if got is not None else None
        size = int(manifest.get("page_size", 50))
        ascending = bool(manifest.get("ascending", True))
        limit = manifest.get("max_items_per_court")
        seen: set[str] = set()
        for court in manifest["courts"]:
            count = 0
            for page in range(MAX_PAGES):
                url = page_url(court, page, size, ascending=ascending)
                content, meta = self._get(
                    url, f"referat/{court}/date-{'asc' if ascending else 'desc'}-{size}-{page:05d}.json"
                )
                try:
                    items = json.loads(content)
                except ValueError as exc:
                    raise FetchError(f"{url}: not JSON ({exc}); stopped") from exc
                if not isinstance(items, list):
                    raise FetchError(f"{url}: expected a JSON list; stopped")
                retrieved_at = datetime.fromisoformat(meta["retrieved_at"])
                for item in items:
                    pid = str(item.get("id", "")) if isinstance(item, dict) else ""
                    ok = (
                        UUID.match(pid)
                        and item.get("publiceringsform") == "REFERAT"
                        and (item.get("domstol") or {}).get("domstolKod") == court
                    )
                    if not ok:
                        self.fetch_errors.append(f"{url}: an entry is not a {court} report ({pid or 'no id'}); skipped")
                        continue
                    if pid in seen:
                        self.fetch_errors.append(f"{url}: {pid} seen twice (the list changed during the run); skipped")
                        continue
                    seen.add(pid)
                    count += 1
                    yield RawDocument(
                        external_id=f"rattspraxis:{pid}",
                        uri=f"{API}/{pid}",
                        media_type="application/json",
                        content=publication_bytes(item),
                        retrieved_at=retrieved_at,
                        metadata={"court": court, "page": page, "page_url": url, "cached_fetch": meta},
                    )
                    if limit is not None and count >= int(limit):
                        break
                if len(items) < size or (limit is not None and count >= int(limit)):
                    break
            else:
                raise FetchError(f"{court}: more than {MAX_PAGES} pages; stopped")
