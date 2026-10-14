"""Polite, cached HTTPS fetching shared by connectors that download from publishers' websites.

Rules built in (see docs/onboarding-a-jurisdiction.md):

* an identifying User-Agent with contact details is required;
* a delay between network requests (the publisher's robots ``Crawl-delay``, else 5 s);
* back-off on 429 and 5xx, honouring ``Retry-After``; 404 is "not found", any other refusal
  (401/403/451...) stops the run - ULIS never works around a refusal;
* HTTPS only, and only to the hosts the manifest names;
* every response is cached with its URL and retrieval time, so nothing is fetched twice and
  runs can be repeated offline.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class FetchError(RuntimeError):
    pass


class PoliteHttp:
    def __init__(
        self,
        cache_dir: Path,
        user_agent: str,
        *,
        allowed_hosts: set[str],
        crawl_delay: float = 5.0,
        offline: bool = False,
        sleep: Any = time.sleep,
        timeout: float = 120.0,
    ):
        if not user_agent or "(" not in user_agent:
            raise ValueError("an identifying user_agent with contact details is required")
        self.cache_dir = cache_dir
        self.user_agent = user_agent
        self.allowed_hosts = allowed_hosts
        self.crawl_delay = crawl_delay
        self.offline = offline
        self._sleep = sleep
        self.timeout = timeout
        self._last_request = 0.0
        self.requests_made = 0

    def cache_path(self, url: str, cache_key: str | None = None) -> Path:
        """Cache file for a URL: ``<cache_dir>/<host>/<cache_key or path[?sha of query]>``."""
        parts = urllib.parse.urlsplit(url)
        key = cache_key or parts.path.strip("/") or "index"
        if parts.query and not cache_key:
            key += "@" + hashlib.sha256(parts.query.encode()).hexdigest()[:16]
        key = re.sub(r"[^A-Za-z0-9._/@=-]", "_", key)
        path = (self.cache_dir / parts.netloc / key).resolve()
        if self.cache_dir.resolve() not in path.parents:
            raise FetchError(f"cache path escapes the cache directory: {key}")
        return path

    def get(
        self, url: str, cache_key: str | None = None, headers: dict[str, str] | None = None
    ) -> tuple[bytes, dict[str, Any]] | None:
        """Return (content, meta) or None for 404. ``meta`` has url, final_url, status, retrieved_at."""
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.netloc not in self.allowed_hosts:
            raise FetchError(f"refusing {url}: only https to {sorted(self.allowed_hosts)}")
        cache_file = self.cache_path(url, cache_key)
        meta_file = cache_file.with_name(cache_file.name + ".meta.json")
        if cache_file.exists() and meta_file.exists():
            meta = json.loads(meta_file.read_text())
            return (cache_file.read_bytes(), meta) if meta.get("status") != 404 else None
        if self.offline:
            raise FetchError(f"offline and not cached: {url}")
        for attempt in range(5):
            wait = self._last_request + self.crawl_delay - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last_request = time.monotonic()
            self.requests_made += 1
            request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, **(headers or {})})  # noqa: S310
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - https only
                    data = response.read()
                    meta = {
                        "url": url,
                        "final_url": response.geturl(),
                        "status": response.status,
                        "content_type": response.headers.get("Content-Type"),
                        "retrieved_at": datetime.now(UTC).isoformat(),
                    }
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    self._store(cache_file, meta_file, b"", {"url": url, "status": 404})
                    return None
                if exc.code in (429, 500, 502, 503, 504) and attempt < 4:
                    retry_after = exc.headers.get("Retry-After")
                    pause = (
                        float(retry_after) if retry_after and retry_after.isdigit() else self.crawl_delay * 2**attempt
                    )
                    self._sleep(pause)
                    continue
                raise FetchError(f"{url}: HTTP {exc.code} (not retried: a refusal is respected)") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < 4:
                    self._sleep(self.crawl_delay * 2**attempt)
                    continue
                raise FetchError(f"{url}: {exc}") from exc
        else:  # pragma: no cover - loop always breaks or raises
            raise FetchError(url)
        self._store(cache_file, meta_file, data, meta)
        return data, meta

    @staticmethod
    def _store(cache_file: Path, meta_file: Path, data: bytes, meta: dict[str, Any]) -> None:
        cache_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        cache_file.write_bytes(data)
        meta_file.write_text(json.dumps(meta))
