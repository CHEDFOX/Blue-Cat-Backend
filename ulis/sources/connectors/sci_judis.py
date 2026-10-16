"""Fetches Supreme Court of India judgments (JUDIS PDFs) from the Court's API host.

``https://api.sci.gov.in/jonew/judis/<id>.pdf`` serves a judgment as a PDF by the Court's own document number. The Court
publishes no list of the numbers on a host that lets the bot in (the search on its website is behind a CAPTCHA and the
website refuses the bot, HTTP 403 on 2026-10-16), so the manifest (``config/manifests/in-sci-judis.yaml``) lists number
RANGES, and the connector asks for every number in them. A number with no judgment answers HTTP 200 with an empty body
(many numbers in the range: that is the host's way of saying "none", not an error); the connector passes on only
non-empty PDFs, each exactly as received (a four-byte length prefix the server adds before ``%PDF`` is part of the
stored bytes; the parser drops it).

Terms: see ``config/sources/in-sci-judis.yaml``. ``PoliteHttp``: identifying User-Agent, at least 5 s between requests,
HTTPS to ``api.sci.gov.in`` only, back-off on 429/5xx, any other refusal (401/403/451 ...) stops the run, cache under
``<cache_dir>/api.sci.gov.in/``. The host's ``robots.txt`` (an empty file on 2026-10-16: nothing disallowed) is read
first and every URL is checked against it. Anything that answers 200 but is not a PDF, and any single number that
fails for another reason than a refusal (a 5xx after the retries, a dropped connection), is reported in ``fetch_errors``
and the run goes on; 20 failures in a row, a refusal (401/403/407/429/451) or an offline cache miss stop it.
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

HOST = "api.sci.gov.in"
BASE_URL = f"https://{HOST}"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
MIN_DELAY = 5.0
MAX_ID = 1_000_000
# A refusal stops the run; any other failure of one number (a 5xx that outlasts the retries, a dropped connection) is
# reported and the run goes on, unless this many numbers in a row fail (the host is down).
REFUSAL = re.compile(r"HTTP (?:401|403|407|429|451)\b")
MAX_CONSECUTIVE_FAILURES = 20

__all__ = ["FetchError", "SciJudisConnector", "judgment_url"]


def judgment_url(judis_id: int) -> str:
    if not 1 <= judis_id <= MAX_ID:
        raise ManifestError(f"invalid JUDIS number {judis_id!r}")
    return f"{BASE_URL}/jonew/judis/{judis_id}.pdf"


class SciJudisConnector:
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
        self.numbers_asked = 0
        self.numbers_empty = 0

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
        ranges = manifest.get("id_ranges") or []
        if not ranges:
            raise ManifestError("the manifest lists no number ranges")
        last_end = 0
        for item in ranges:
            if not (isinstance(item, list) and len(item) == 2 and all(isinstance(n, int) for n in item)):
                raise ManifestError(f"a range must be [first, last] with whole numbers, not {item!r}")
            first, last = item
            judgment_url(first)
            judgment_url(last)
            if first > last or first <= last_end:
                raise ManifestError(f"ranges must be ascending and must not overlap or repeat: {item!r}")
            last_end = last
        return manifest

    def numbers(self) -> Iterator[int]:
        for first, last in self._manifest()["id_ranges"]:
            yield from range(first, last + 1)

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
        # No robots.txt (404) means no restrictions (RFC 9309, 2.3.1.3); an empty file allows everything.
        got = self.http.get(ROBOTS_URL, cache_key="robots.txt")
        robots = got[0].decode("utf-8", "replace") if got is not None else ""
        failures = 0
        for judis_id in self.numbers():
            url = judgment_url(judis_id)
            if not robots_allows(robots, self.http.user_agent, url):
                raise FetchError(f"{url}: disallowed by {ROBOTS_URL}; not fetched")
            self.numbers_asked += 1
            try:
                got = self.http.get(url, cache_key=f"judis/{judis_id}.pdf")
            except FetchError as exc:
                failures += 1
                if self.offline or REFUSAL.search(str(exc)) or failures >= MAX_CONSECUTIVE_FAILURES:
                    raise
                self.fetch_errors.append(f"{exc}; skipped")
                continue
            failures = 0
            if got is None:
                self.numbers_empty += 1  # 404: no judgment under this number
                continue
            content, meta = got
            if not content:
                self.numbers_empty += 1  # HTTP 200 with an empty body: no judgment under this number
                continue
            if b"%PDF-" not in content[:64]:
                self.fetch_errors.append(
                    f"{url}: not a PDF ({meta.get('content_type')!r}, {len(content)} bytes); skipped"
                )
                continue
            yield RawDocument(
                external_id=f"sci-judis:{judis_id}",
                uri=url,
                media_type="application/pdf",
                content=content,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"judis_id": judis_id, "cached_fetch": meta},
            )
