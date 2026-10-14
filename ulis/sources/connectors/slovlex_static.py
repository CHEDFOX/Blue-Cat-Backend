"""Polite fetcher for the static version of Slov-lex (https://static.slov-lex.sk), the Ministry of Justice's
script-free portal of the Slovak Collection of Laws (Zbierka zákonov).

For every act in the manifest it reads the act's history page ``/static/SK/ZZ/<year>/<number>/`` (a table of the
time versions ("časová verzia") with the dates each applies from and to) and then fetches the version pages
``/static/SK/ZZ/<year>/<number>/<YYYYMMDD>.html`` the manifest asks for, oldest first (snapshots are recorded oldest
first, see ``ulis.ingestion.snapshots``). The promulgated version (``vyhlasene_znenie.html``) is never fetched.

Built on :class:`PoliteHttp`: identifying User-Agent, at least 5 s between requests, HTTPS to the two Slov-lex hosts
only, refusals respected, a cache under ``<cache_dir>/static.slov-lex.sk/``. ``https://www.slov-lex.sk/robots.txt``
(``User-agent: *`` / empty ``Disallow``, with sitemaps on the static host) is read and applied to every path; the
static host has no robots.txt of its own.

A manifest ``versions`` policy per act: ``all`` (every time version up to ``as_of``), ``current`` (the one in force on
``as_of``) or ``since: <date>`` (versions in force on or after that date, i.e. including the one that was current on it).
"""

from __future__ import annotations

import re
import time
import urllib.robotparser
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

STATIC_HOST = "static.slov-lex.sk"
ROBOTS_HOST = "www.slov-lex.sk"
BASE_URL = f"https://{STATIC_HOST}"
ACT_ID = re.compile(r"^(19[0-9]{2}|20[0-9]{2})/([1-9][0-9]{0,3})$")
ROW = re.compile(
    r'<tr class="effectivenessHistoryItem" data-iri="[^"]*" data-vyhlasene="(?P<promulgated>[01])" '
    r'data-ucinnostod="(?P<from>[^"]*)" data-ucinnostdo="(?P<to>[^"]*)">'
)

__all__ = ["SlovLexStaticConnector", "history_versions", "select_versions"]


def history_versions(page: str) -> list[tuple[date, date | None]]:
    """The time versions in a history page: (applies from, applies until or None), oldest first."""
    out: list[tuple[date, date | None]] = []
    for m in ROW.finditer(page):
        if m.group("promulgated") == "1" or not m.group("from"):
            continue
        out.append((date.fromisoformat(m.group("from")), date.fromisoformat(m.group("to")) if m.group("to") else None))
    out.sort()
    return out


def select_versions(
    versions: list[tuple[date, date | None]], policy: Any, as_of: date
) -> list[tuple[date, date | None]]:
    known = [v for v in versions if v[0] <= as_of]
    current = [v for v in known if v[1] is None or v[1] >= as_of]
    if policy == "all":
        return known
    if policy == "current":
        return current[-1:]
    if isinstance(policy, dict) and "since" in policy:
        since = policy["since"] if isinstance(policy["since"], date) else date.fromisoformat(str(policy["since"]))
        return [v for v in known if v[1] is None or v[1] >= since]
    raise ManifestError(f"unknown versions policy {policy!r} (use all, current or since: <date>)")


class SlovLexStaticConnector:
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
        if float(manifest.get("crawl_delay_seconds", 5)) < 5:
            raise ManifestError("crawl_delay_seconds must be at least 5")
        try:
            date.fromisoformat(str(manifest.get("as_of")))
        except ValueError as exc:
            raise ManifestError("as_of must be an ISO date") from exc
        laws = manifest.get("laws") or []
        if not laws:
            raise ManifestError("the manifest lists no acts")
        seen: set[str] = set()
        for law in laws:
            act = str(law.get("act", ""))
            if not ACT_ID.match(act) or act in seen:
                raise ManifestError(f"invalid or repeated act id {act!r} (expected <year>/<number>)")
            seen.add(act)
            select_versions([], law.get("versions", "current"), date(2000, 1, 1))  # validates the policy
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        as_of = date.fromisoformat(str(manifest["as_of"]))
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={STATIC_HOST, ROBOTS_HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", 5)),
            offline=self.offline,
            sleep=self._sleep,
        )
        robots_file = self.http.get(f"https://{ROBOTS_HOST}/robots.txt")
        if robots_file is None:
            raise FetchError(f"https://{ROBOTS_HOST}/robots.txt: not found; refusing to fetch without reading it")
        robots = urllib.robotparser.RobotFileParser()
        robots.parse(robots_file[0].decode("utf-8", "replace").splitlines())

        def get(path: str) -> tuple[bytes, dict[str, Any]] | None:
            url = f"{BASE_URL}{path}"
            if not robots.can_fetch(manifest["user_agent"], url) or not robots.can_fetch("*", url):
                raise FetchError(f"{url} is disallowed by robots.txt; not fetched")
            assert self.http is not None
            return self.http.get(url)

        for law in manifest["laws"]:
            act = str(law["act"])
            year, number = act.split("/")
            base = f"/static/SK/ZZ/{year}/{number}/"
            history = get(base)
            if history is None:
                self.fetch_errors.append(f"slovlex:{act}: history page not found (404)")
                continue
            versions = select_versions(
                history_versions(history[0].decode("utf-8", "replace")), law.get("versions", "current"), as_of
            )
            if not versions:
                self.fetch_errors.append(f"slovlex:{act}: no time version selected")
                continue
            for start, _end in versions:
                path = f"{base}{start:%Y%m%d}.html"
                got = get(path)
                if got is None:
                    self.fetch_errors.append(f"slovlex:{act}: {path}: not found (404)")
                    continue
                data, meta = got
                marker = f'data-from="{start.isoformat()}"'.encode()
                if b'id="predpis"' not in data or marker not in data:
                    raise FetchError(
                        f"{BASE_URL}{path}: not the time version starting {start} of {act} "
                        f"({meta.get('content_type')}); not ingested. Remove {self.http.cache_path(BASE_URL + path)} "
                        "after checking the site."
                    )
                yield RawDocument(
                    external_id=f"slovlex:{act}:{start:%Y%m%d}",
                    uri=meta.get("final_url") or f"{BASE_URL}{path}",
                    media_type="text/html",
                    content=data,
                    retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                    metadata={"act": act, "version_from": start.isoformat(), "cached_fetch": meta},
                )
