"""Fetches UK Supreme Court judgments from the Court's own website (HTML pages).

``https://www.supremecourt.uk/cases/judgments/uksc-<year>-<number>`` shows a judgment in full. The cases to
fetch are listed in the manifest (``config/manifests/gb-uksc-judgments.yaml``, taken from the Court's sitemap);
each page is yielded exactly as received. Terms: the Court's website content is Crown copyright re-usable under
the Open Government Licence v3.0 (https://www.supremecourt.uk/about/terms-and-conditions); see
``config/sources/gb-uksc-judgments.yaml``. This is the Court's own site and not the National Archives' Find Case
Law service, whose licence for computational analysis is a separate matter (``gb-find-case-law``).

``PoliteHttp``: identifying User-Agent, at least 5 s between requests, HTTPS to ``www.supremecourt.uk`` only,
back-off on 429/5xx, any other refusal stops the run, cache under ``<cache_dir>/www.supremecourt.uk/``. The host's
``robots.txt`` (it disallows only ``/contact-us/book-room-hearing``) is read first and every URL is checked
against it. A page that is missing (404), is not the page of the listed case, or is not UTF-8 HTML is not passed
on: it is reported in ``fetch_errors`` and the run goes on.
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
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

HOST = "www.supremecourt.uk"
BASE_URL = f"https://{HOST}"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
MIN_DELAY = 5.0
CASE_ID = re.compile(r"^UKSC/(\d{4})/(\d{4})$")

__all__ = ["FetchError", "UkscConnector", "judgment_url"]


def judgment_url(case_id: str) -> str:
    m = CASE_ID.match(case_id)
    if not m:
        raise ManifestError(f"invalid case id {case_id!r} (expected UKSC/yyyy/nnnn)")
    return f"{BASE_URL}/cases/judgments/uksc-{m.group(1)}-{m.group(2)}"


class UkscConnector:
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
        cases = manifest.get("cases") or []
        if not cases:
            raise ManifestError("the manifest lists no cases")
        seen: set[str] = set()
        for case_id in cases:
            judgment_url(str(case_id))  # validates the pattern
            if case_id in seen:
                raise ManifestError(f"{case_id} is listed twice")
            seen.add(case_id)
        return manifest

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
        robots = got[0].decode("utf-8", "replace") if got is not None else ""
        for case_id in manifest["cases"]:
            url = judgment_url(case_id)
            if not robots_allows(robots, self.http.user_agent, url):
                raise FetchError(f"{url}: disallowed by {ROBOTS_URL}; not fetched")
            key = "judgments/" + url.rsplit("/", 1)[1] + ".html"
            got = self.http.get(url, cache_key=key)
            if got is None:
                self.fetch_errors.append(f"{url}: not found (404); skipped")
                continue
            content, meta = got
            if "html" not in str(meta.get("content_type") or "").lower():
                self.fetch_errors.append(f"{url}: expected HTML, got {meta.get('content_type')!r}; skipped")
                continue
            if 'id="case-id"' not in content.decode("utf-8", "replace") or case_id.encode() not in content:
                self.fetch_errors.append(f"{url}: the page is not the judgment page of {case_id}; skipped")
                continue
            yield RawDocument(
                external_id=f"uksc:{case_id}",
                uri=url,
                media_type="text/html",
                content=content,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"case_id": case_id, "cached_fetch": meta},
            )
