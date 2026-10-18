"""Polite, cached HTTPS fetching shared by connectors that download from publishers' websites.

Rules built in (see docs/onboarding-a-jurisdiction.md):

* an identifying User-Agent with contact details is required;
* a delay between network requests, measured from the start of one request to the start of the next. The
  delay is ``max(the publisher's robots.txt Crawl-delay for the host, the manifest's crawl_delay_seconds)``;
  a manifest may go below the old 5 s default only down to :data:`MIN_CRAWL_DELAY` (1 s), and only where the
  publisher states no Crawl-delay or rate of its own (``manifest_crawl_delay`` refuses anything shorter;
  ``honour_robots_delay=True`` reads the host's robots.txt before the first network request of a run and
  applies its Crawl-delay as a floor);
* one connection: requests are sequential, never parallel;
* back-off on 429 and 5xx, honouring ``Retry-After``; 404 is "not found", any other refusal
  (401/403/451...) stops the run - ULIS never works around a refusal;
* HTTPS only, and only to the hosts the manifest names;
* every response is cached with its URL and retrieval time, so nothing is fetched twice and
  runs can be repeated offline.
"""

from __future__ import annotations

import hashlib
import http.client
import http.cookiejar
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ulis.sources.connectors.local_manifest import ManifestError

MIN_CRAWL_DELAY = 1.0
"""Project-wide minimum pause between two requests to one publisher (seconds, start to start). A manifest may ask
for a longer delay; the publisher's robots.txt ``Crawl-delay``, when it gives one, always wins over a shorter
manifest value; a published rate limit is written into the manifest (and, where it binds, into the connector)."""

DEFAULT_CRAWL_DELAY = 5.0
"""The delay a connector uses when its manifest sets none."""


class FetchError(RuntimeError):
    pass


def manifest_crawl_delay(
    manifest: dict[str, Any], default: float = DEFAULT_CRAWL_DELAY, minimum: float = MIN_CRAWL_DELAY
) -> float:
    """The manifest's ``crawl_delay_seconds`` (``default`` when absent), refused below ``minimum``.

    ``minimum`` is the project-wide :data:`MIN_CRAWL_DELAY` unless a connector knows a stricter publisher rule (for
    example a published "one call per 10 s"); it can never be lower than :data:`MIN_CRAWL_DELAY`.
    """
    minimum = max(minimum, MIN_CRAWL_DELAY)
    raw = manifest.get("crawl_delay_seconds", default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ManifestError(f"crawl_delay_seconds must be a number of seconds, got {raw!r}") from None
    if not value >= minimum:  # also refuses NaN
        raise ManifestError(f"crawl_delay_seconds must be at least {minimum:g} (got {value:g})")
    return value


def robots_crawl_delay(robots_txt: str, user_agent: str) -> float | None:
    """The ``Crawl-delay`` (seconds) of the robots.txt group for the agent's product token or, failing that, ``*``.

    Groups follow RFC 9309: consecutive ``User-agent`` lines share the rules that follow them. A group that names
    the agent's token (``ULIS-research-bot``) wins over ``*``; a group for other agents (``Claudebot``...) is ignored.
    None when the applicable group sets no (valid) Crawl-delay.
    """
    token = user_agent.split("/")[0].strip().lower()
    groups: list[tuple[list[str], float | None]] = []
    agents: list[str] = []
    delay: float | None = None
    in_rules = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if in_rules:
                groups.append((agents, delay))
                agents, delay, in_rules = [], None, False
            agents.append(value.lower())
        elif agents:
            in_rules = True
            if key == "crawl-delay":
                try:
                    found = float(value)
                except ValueError:
                    continue
                if found >= 0 and found == found:  # not negative, not NaN
                    delay = found if delay is None else max(delay, found)
    if agents:
        groups.append((agents, delay))
    for wanted in (token, "*"):
        matched = [found for names, found in groups if wanted in names]
        if matched:
            values = [d for d in matched if d is not None]
            return max(values) if values else None
    return None


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
        ssl_context: ssl.SSLContext | None = None,
        cookies: bool = False,
        honour_robots_delay: bool = False,
    ):
        if not user_agent or "(" not in user_agent:
            raise ValueError("an identifying user_agent with contact details is required")
        if ssl_context is not None and (ssl_context.verify_mode != ssl.CERT_REQUIRED or not ssl_context.check_hostname):
            raise ValueError("an ssl_context must verify certificates and host names")
        self.cache_dir = cache_dir
        self.user_agent = user_agent
        self.allowed_hosts = allowed_hosts
        self.crawl_delay = crawl_delay
        self.offline = offline
        self._sleep = sleep
        self.timeout = timeout
        # None: Python's default verification. A connector may pass its own context (still
        # verifying the chain and host name), e.g. for a publisher whose trusted root CA predates
        # the strict X.509 checks Python 3.13 enables by default.
        self.ssl_context = ssl_context
        # Off by default. A connector for a site whose downloads need the session its own pages
        # start (Normattiva) turns it on; cookies are kept for the lifetime of this object only.
        self.cookie_jar: http.cookiejar.CookieJar | None = http.cookiejar.CookieJar() if cookies else None
        self._last_request = 0.0
        self.requests_made = 0
        # On: before the first network request to a host in this object's lifetime, robots.txt of that host is read
        # afresh and its Crawl-delay (for ULIS's token, else ``*``) becomes the floor for every request to the host.
        self.honour_robots_delay = honour_robots_delay
        self.robots_delays: dict[str, float | None] = {}

    def delay_for(self, host: str) -> float:
        """The pause before a request to ``host``: the configured delay, or the host's robots Crawl-delay if longer."""
        return max(self.crawl_delay, self.robots_delays.get(host) or 0.0)

    def load_robots_delay(self, host: str) -> float | None:
        """Read ``https://<host>/robots.txt`` (once per run, not from the cache) and record its Crawl-delay.

        A 404 means no robots.txt and so no Crawl-delay; a refusal (401/403/451...) raises like any other request.
        """
        if host in self.robots_delays:
            return self.robots_delays[host]
        self.robots_delays[host] = None  # set first: the robots request itself must not ask for robots.txt again
        got = self.get(f"https://{host}/robots.txt", refresh=True)
        text = got[0].decode("utf-8", errors="replace") if got else ""
        self.robots_delays[host] = robots_crawl_delay(text, self.user_agent)
        return self.robots_delays[host]

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
        self,
        url: str,
        cache_key: str | None = None,
        headers: dict[str, str] | None = None,
        *,
        refresh: bool = False,
    ) -> tuple[bytes, dict[str, Any]] | None:
        """Return (content, meta) or None for 404. ``meta`` has url, final_url, status, retrieved_at.

        ``refresh`` fetches again even when the URL is cached (the new response replaces the cached one).
        """
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.netloc not in self.allowed_hosts:
            raise FetchError(f"refusing {url}: only https to {sorted(self.allowed_hosts)}")
        cache_file = self.cache_path(url, cache_key)
        meta_file = cache_file.with_name(cache_file.name + ".meta.json")
        if cache_file.exists() and meta_file.exists() and not (refresh and not self.offline):
            meta = json.loads(meta_file.read_text())
            return (cache_file.read_bytes(), meta) if meta.get("status") != 404 else None
        if self.offline:
            raise FetchError(f"offline and not cached: {url}")
        if self.honour_robots_delay and parts.path != "/robots.txt":
            self.load_robots_delay(parts.netloc)
        delay = self.delay_for(parts.netloc)
        for attempt in range(5):
            wait = self._last_request + delay - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last_request = time.monotonic()
            self.requests_made += 1
            request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, **(headers or {})})  # noqa: S310
            if self.cookie_jar is not None:
                self.cookie_jar.add_cookie_header(request)
            # Only pass a context when one is configured, so the default call is unchanged.
            extra: dict[str, Any] = {"context": self.ssl_context} if self.ssl_context is not None else {}
            try:
                with urllib.request.urlopen(request, timeout=self.timeout, **extra) as response:  # noqa: S310 - https only
                    if headers and any(k.lower() == "range" for k in headers) and response.status != 206:
                        # A server that ignores Range would send the whole (possibly huge) file.
                        raise FetchError(f"{url}: the range request was not honoured (HTTP {response.status})")
                    data = response.read()
                    if self.cookie_jar is not None:
                        self.cookie_jar.extract_cookies(response, request)
                    meta = {
                        "url": url,
                        "final_url": response.geturl(),
                        "status": response.status,
                        "content_type": response.headers.get("Content-Type"),
                        "last_modified": response.headers.get("Last-Modified"),
                        "retrieved_at": datetime.now(UTC).isoformat(),
                    }
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    self._store(cache_file, meta_file, b"", {"url": url, "status": 404})
                    if self.honour_robots_delay and parts.path == "/robots.txt":
                        self.robots_delays[parts.netloc] = None  # no robots.txt: no Crawl-delay
                    return None
                if exc.code in (429, 500, 502, 503, 504) and attempt < 4:
                    retry_after = exc.headers.get("Retry-After")
                    pause = float(retry_after) if retry_after and retry_after.isdigit() else delay * 2**attempt
                    self._sleep(pause)
                    continue
                raise FetchError(f"{url}: HTTP {exc.code} (not retried: a refusal is respected)") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
                if attempt < 4:
                    self._sleep(delay * 2**attempt)
                    continue
                raise FetchError(f"{url}: {exc}") from exc
        else:  # pragma: no cover - loop always breaks or raises
            raise FetchError(url)
        self._store(cache_file, meta_file, data, meta)
        if self.honour_robots_delay and parts.path == "/robots.txt":
            # A robots.txt read from the network (not from the cache) sets the host's floor for this run.
            self.robots_delays[parts.netloc] = robots_crawl_delay(
                data.decode("utf-8", errors="replace"), self.user_agent
            )
        return data, meta

    @staticmethod
    def _store(cache_file: Path, meta_file: Path, data: bytes, meta: dict[str, Any]) -> None:
        cache_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        cache_file.write_bytes(data)
        meta_file.write_text(json.dumps(meta))
