"""Polite fetcher for the Laws & Regulations Database of the Republic of China (Taiwan).

全國法規資料庫 (https://law.moj.gov.tw) is maintained by the Ministry of Justice (法務部). For
every law in the manifest the connector downloads the law's full-text page
``https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=<PCode>`` (所有條文) and yields it exactly as
received; the parser reads it.

Why this page and not the open-data API: the site's ``robots.txt`` disallows every path for all
user agents except ``/News/NewsDetail.aspx``, ``/LawClass/LawAll.aspx`` and the sitemap. The
open-data download (``/api/ch/law/json``) is therefore not fetched. Before any law page the
connector reads ``robots.txt`` and refuses a URL that it does not allow (longest-match rule of
RFC 9309, ``Allow`` winning ties).

The manifest may list every law and regulation of the database (``scripts/build_moj_manifest.py`` reads the
sitemap that robots.txt allows: about 11,800 PCodes, one request each), so a page that is missing (404) or is not
a law page, or a server error that outlasts the back-off, is reported in ``fetch_errors`` and the run goes on. A
refusal (401, 403, 429, 451), a URL that robots.txt disallows and ``MAX_CONSECUTIVE_ERRORS`` pages failing in a
row stop the run.

Built on :class:`PoliteHttp`: identifying User-Agent, 5 s between requests (robots.txt sets no
Crawl-delay), HTTPS to this one host only, refusals respected, and a cache under
``<cache_dir>/law.moj.gov.tw/LawClass/LawAll/<PCode>.html`` so runs can be repeated offline.
Responses are cached forever: delete the cached page to pick up a newer consolidation.

TLS: the site's certificate chains to TWCA Global Root CA, a root in the system trust store that
has no Subject Key Identifier extension. Python 3.13 rejects such roots by default
(``VERIFY_X509_STRICT``). The connector keeps full verification (trusted chain, host name) and
only clears that strict-profile flag.
"""

from __future__ import annotations

import re
import ssl
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

HOST = "law.moj.gov.tw"
BASE_URL = f"https://{HOST}"
LAW_PAGE = "/LawClass/LawAll.aspx"
_PCODE = re.compile(r"^[A-Z][0-9]{7}$")
_REFUSAL = re.compile(r"HTTP (?:401|403|429|451)\b")
MAX_CONSECUTIVE_ERRORS = 20

__all__ = ["FetchError", "MojLawsConnector", "law_url", "robots_allows", "tls_context"]


def law_url(pcode: str) -> str:
    return f"{BASE_URL}{LAW_PAGE}?pcode={pcode}"


def tls_context() -> ssl.SSLContext:
    """Default verification (trusted chain and host name) without Python 3.13's strict X.509 profile."""
    context = ssl.create_default_context()
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def _rule_regex(path: str) -> re.Pattern[str]:
    pattern = "".join(".*" if ch == "*" else re.escape(ch) for ch in path.rstrip("$"))
    return re.compile(pattern + ("$" if path.endswith("$") else ""))


def robots_allows(robots_txt: str, user_agent: str, url: str) -> bool:
    """Whether ``robots.txt`` allows ``url`` for ``user_agent`` (RFC 9309: longest match, Allow wins ties).

    The group naming the agent's product token applies, else the ``*`` group; no group allows all.
    """
    token = user_agent.split("/")[0].strip().lower()
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []
    for raw_line in robots_txt.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if rules:  # a new group starts
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
        elif key in ("allow", "disallow") and agents:
            rules.append((key, value))
    if agents:
        groups.append((agents, rules))
    chosen = [r for a, r in groups if token in a] or [r for a, r in groups if "*" in a]
    applicable = [rule for group in chosen for rule in group]
    parts = urllib.parse.urlsplit(url)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    best: tuple[int, bool] | None = None
    for kind, rule_path in applicable:
        if not rule_path:  # "Disallow:" with no path allows everything
            continue
        if _rule_regex(rule_path).match(path):
            candidate = (len(rule_path), kind == "allow")
            if best is None or candidate > best:
                best = candidate
    return best is None or best[1]


class MojLawsConnector:
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
        self.fetch_errors: list[str] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if manifest.get("base_url", BASE_URL).rstrip("/") != BASE_URL:
            raise ManifestError(f"base_url must be {BASE_URL}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        laws = manifest.get("laws") or []
        if not laws:
            raise ManifestError("the manifest lists no laws")
        seen: set[str] = set()
        for law in laws:
            pcode = str(law.get("pcode", ""))
            if not _PCODE.match(pcode):
                raise ManifestError(f"invalid pcode {law.get('pcode')!r}")
            if pcode in seen:
                raise ManifestError(f"{pcode} is listed twice")
            seen.add(pcode)
        return manifest

    def _get(self, url: str, cache_key: str) -> tuple[bytes, dict[str, Any]]:
        assert self.http is not None
        got = self.http.get(url, cache_key=cache_key)
        if got is None:
            raise FetchError(f"{url}: not found (404)")
        return got

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.fetch_errors = []
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", 5)),
            offline=self.offline,
            sleep=self._sleep,
            ssl_context=tls_context(),
        )
        robots, _ = self._get(f"{BASE_URL}/robots.txt", "robots.txt")
        robots_txt = robots.decode("utf-8", errors="replace")
        failed_in_a_row = 0
        for law in manifest["laws"]:
            pcode = str(law["pcode"])
            url = law_url(pcode)
            if not robots_allows(robots_txt, manifest["user_agent"], url):
                raise FetchError(f"{url}: disallowed by {BASE_URL}/robots.txt; not fetched")
            cache_key = f"LawClass/LawAll/{pcode}.html"
            try:
                data, meta = self._get(url, cache_key)
                if b"law-reg-content" not in data or b'id="hlLawName"' not in data:
                    cached = self.http.cache_path(url, cache_key)  # do not keep an error page for later runs
                    cached.unlink(missing_ok=True)
                    cached.with_name(cached.name + ".meta.json").unlink(missing_ok=True)
                    raise FetchError(
                        f"{url}: response is not a law page ({meta.get('content_type')}); not ingested, "
                        "and not kept in the cache"
                    )
            except FetchError as exc:
                if _REFUSAL.search(str(exc)) or self.offline:
                    raise  # a refusal stops the run; so does a missing cache entry when offline
                self.fetch_errors.append(f"moj-law:{pcode}: {exc}; skipped")
                failed_in_a_row += 1
                if failed_in_a_row >= MAX_CONSECUTIVE_ERRORS:
                    raise FetchError(f"{failed_in_a_row} pages failed in a row (last: {exc}); stopping") from exc
                continue
            failed_in_a_row = 0
            yield RawDocument(
                external_id=f"moj-law:{pcode}",
                uri=meta.get("final_url") or url,
                media_type="text/html",
                content=data,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"pcode": pcode, "cached_fetch": meta},
            )
