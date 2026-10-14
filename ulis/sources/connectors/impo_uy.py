"""Polite fetcher for IMPO, Uruguayan national legislation (https://www.impo.com.uy).

IMPO publishes "toda la información pública disponible en sus bases de datos" as open data (Licencia de
Datos Abiertos - Uruguay): any public norm is available in JSON by adding ``?json=true`` to its address
(https://www.impo.com.uy/datos-abiertos/). For every norm in the manifest this connector downloads
``https://www.impo.com.uy/bases/<path>?json=true``, for example ``bases/leyes/18331-2008?json=true``, and yields
one *bundle* per norm (JSON, ``ulis-impo-bundle/1``): the answer exactly as received (the server sends
ISO-8859-1; the bytes are carried as a Latin-1 string, which is lossless) with its URL and retrieval time. The page
states no date of update, so the retrieval date is what lets the parser attest the text.

Built on :class:`PoliteHttp`: identifying User-Agent, a delay between requests (the larger of the manifest's and
robots.txt's ``Crawl-delay``, which is 10 s), HTTPS to this one host only, refusals respected, cache under
``<cache_dir>/www.impo.com.uy/bases/<path>.json``. robots.txt is read first (one cached request) and every path is
checked against its ``Disallow`` rules. IMPO's search interface (``/bases`` itself, which sends anonymous visitors
to a login page under the disallowed ``/cgi-bin/``) is never used: the manifest names the norms. A norm that
answers with anything but a JSON norm (404, a redirect elsewhere, a login page) is skipped and reported in
``fetch_errors``; a refusal (401/403/429) stops the run.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.infoleg_ar import robots_crawl_delay
from ulis.sources.connectors.likumi_lv import robots_disallowed
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.parsers.impo_uy import BUNDLE_FORMAT, PATH_PATTERN

HOST = "www.impo.com.uy"
BASE_URL = f"https://{HOST}"
_PATH = re.compile(PATH_PATTERN)
REFUSALS = {401, 403, 429, 451}  # a refusal stops the run; any other failure skips the document
MAX_FAILURES_IN_A_ROW = 5


class ImpoUyConnector:
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
        # Norms that were skipped (404, not a JSON norm, a redirect elsewhere): reported as run errors by the
        # pipeline so one bad entry does not abort the source. A refusal (401/403/429) still stops the run.
        self.fetch_errors: list[str] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if manifest.get("base_url", BASE_URL).rstrip("/") != BASE_URL:
            raise ManifestError(f"base_url must be {BASE_URL}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        norms = manifest.get("norms") or []
        if not norms:
            raise ManifestError("the manifest lists no documents")
        seen: set[str] = set()
        for norm in norms:
            path = str(norm.get("path", ""))
            if not _PATH.fullmatch(path):
                raise ManifestError(f"invalid document path {path!r} (expected <kind>/<number>-<year>)")
            if path in seen:
                raise ManifestError(f"document {path} is listed twice")
            seen.add(path)
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", 10)),
            offline=self.offline,
            sleep=self._sleep,
        )
        got = self.http.get(f"{BASE_URL}/robots.txt")
        if got is None:
            raise FetchError(f"{BASE_URL}/robots.txt: not found; refusing to fetch without reading it")
        robots = got[0].decode("utf-8", "replace")
        disallowed = robots_disallowed(robots)
        self.http.crawl_delay = max(self.http.crawl_delay, robots_crawl_delay(robots) or 0.0)
        failures_in_a_row = 0
        for norm in manifest["norms"]:
            path = norm["path"]
            request_path = f"/bases/{path}?json=true"
            if any(rule.match(request_path) for rule in disallowed):
                self.fetch_errors.append(
                    f"impo-uy:{path}: {BASE_URL}{request_path} is disallowed by robots.txt; not fetched"
                )
                continue
            url = f"{BASE_URL}{request_path}"
            try:
                fetched = self.http.get(url, cache_key=f"bases/{path}.json")
            except FetchError as exc:
                code = re.search(r"HTTP (\d{3})", str(exc))
                if self.offline or (code and int(code.group(1)) in REFUSALS):
                    raise
                # IMPO answers an unknown address with HTTP 500 (after PoliteHttp's retries), not 404.
                self.fetch_errors.append(f"impo-uy:{path}: {exc}")
                failures_in_a_row += 1
                if failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
                    raise FetchError(
                        f"{failures_in_a_row} documents in a row could not be fetched (last: {path}); stopping the run"
                    ) from exc
                continue
            failures_in_a_row = 0
            if fetched is None:
                self.fetch_errors.append(f"impo-uy:{path}: {url}: not found (404)")
                continue
            data, meta = fetched
            final = urlsplit(meta.get("final_url") or url)
            if final.netloc != HOST or final.path != f"/bases/{path}":
                self.fetch_errors.append(
                    f"impo-uy:{path}: {url} answered from {final.geturl()}, not the norm; not ingested. "
                    f"Remove {self.http.cache_path(url, f'bases/{path}.json')} after checking the site."
                )
                continue
            body = data.decode("latin-1")  # lossless: every byte is one code point; the parser decodes the text
            try:
                parsed = json.loads(
                    body, strict=False
                )  # IMPO leaves bare control characters (a CR before <br>) in strings
            except ValueError:
                parsed = None
            if not isinstance(parsed, dict) or not isinstance(parsed.get("articulos"), list):
                self.fetch_errors.append(
                    f"impo-uy:{path}: {url}: the answer is not a JSON norm ({meta.get('content_type')}); not ingested. "
                    f"Remove {self.http.cache_path(url, f'bases/{path}.json')} after checking the site."
                )
                continue
            bundle = {
                "format": BUNDLE_FORMAT,
                "path": path,
                "url": meta.get("final_url") or url,
                "retrieved_at": meta["retrieved_at"],
                "content_type": meta.get("content_type"),
                "body": body,
            }
            yield RawDocument(
                external_id=f"impo-uy:{path}",
                uri=f"{BASE_URL}/bases/{path}",
                media_type="application/json",
                content=json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"impo_path": path, "title": norm.get("title")},
            )
