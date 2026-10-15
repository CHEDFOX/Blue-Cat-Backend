"""Polite fetcher for Narodne novine (https://narodne-novine.nn.hr), the official gazette of Croatia.

The publisher invites automated use ("Data access" page, https://narodne-novine.nn.hr/data_access.aspx: access is
open to everyone, anonymously and free of charge, to any crawler; a yearly list of all regulations is offered as
CSV for "professional use"; scripts and bots are asked to make at most three requests per second). Every act of the
official part has a stable ELI address, ``/eli/sluzbeni/<year>/<edition>/<number>``, which answers with the act's page
(redirecting to ``/clanci/sluzbeni/<year>_<month>_<edition>_<number>.html``).

For every act in the manifest the connector fetches that address (one request, 5 s apart) and yields the page exactly as
received. Built on :class:`PoliteHttp` (identifying User-Agent, HTTPS to ``narodne-novine.nn.hr`` only, cache under
``<cache_dir>/narodne-novine.nn.hr/``, refusals respected). robots.txt is read first and applied twice: to the
ELI path requested and, because it names individual article pages (``/clanci/sluzbeni/2017_06_57_1277.html``, about two
hundred of them, removed from public view), to the act's year/edition/number and to the address the server redirects to.
An act that is missing, blocked or not an act page is reported in ``fetch_errors`` and the run goes on; a refusal
(401/403/429) stops it.

Manifest (``config/manifests/hr-narodne-novine.yaml``)::

    schema_version: 1
    source_id: hr-narodne-novine
    base_url: https://narodne-novine.nn.hr
    user_agent: "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
    crawl_delay_seconds: 5
    acts:
      - {eli: "2023/30/510", title: "Zakon o službenoj iskaznici", kind: zakon}
"""

from __future__ import annotations

import re
import time
import urllib.parse
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.site_session import PageNotAvailable, SiteSession, load_manifest

HOST = "narodne-novine.nn.hr"
BASE_URL = f"https://{HOST}"
ELI = re.compile(r"(?:19[89][0-9]|20[0-9]{2})/[1-9][0-9]{0,3}/[1-9][0-9]{0,5}")
ARTICLE_PAGE = re.compile(r"^/clanci/(?:sluzbeni|oglasi)(?:/full)?/(\d{4})_(\d{2})_(\d+)_(\d+)\.html$")


def blocked_acts(robots: str) -> set[tuple[int, int, int]]:
    """(year, edition, number) of the acts whose article pages robots.txt disallows for every robot."""
    out: set[tuple[int, int, int]] = set()
    agents: list[str] = []
    in_rules = False
    for raw in robots.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(value)
        elif field == "disallow":
            in_rules = True
            if "*" in agents and (m := ARTICLE_PAGE.match(value)):
                out.add((int(m.group(1)), int(m.group(3)), int(m.group(4))))
        elif field in ("allow", "crawl-delay"):
            in_rules = True
    return out


class NarodneNovineConnector:
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
        self.session: SiteSession | None = None
        self.fetch_errors: list[str] = []

    @property
    def http(self):  # type: ignore[no-untyped-def]
        return self.session.http if self.session else None

    def _manifest(self) -> dict[str, Any]:
        manifest = load_manifest(self.manifest_path, self.expected_source_id, BASE_URL, "acts")
        seen: set[str] = set()
        for act in manifest["acts"]:
            eli = str(act.get("eli", ""))
            if not ELI.fullmatch(eli):
                raise ManifestError(f"invalid ELI path {act.get('eli')!r} (expected <year>/<edition>/<number>)")
            if eli in seen:
                raise ManifestError(f"{eli} is listed twice")
            seen.add(eli)
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.fetch_errors = []
        self.session = SiteSession(manifest, HOST, self.cache_dir, offline=self.offline, sleep=self._sleep)
        robots = self.session.http.get(f"{BASE_URL}/robots.txt")
        blocked = blocked_acts(robots[0].decode("utf-8", "replace")) if robots else set()
        for act in manifest["acts"]:
            eli = str(act["eli"])
            year, edition, number = (int(x) for x in eli.split("/"))
            path = f"/eli/sluzbeni/{eli}"
            try:
                if (year, edition, number) in blocked:
                    raise PageNotAvailable(f"{BASE_URL}{path}: robots.txt disallows this act's page; not fetched")
                data, meta = self.session.get(path)
                final = urllib.parse.urlsplit(meta.get("final_url") or "").path
                if final and final != path and any(rule.match(final) for rule in self.session.disallowed):
                    # the redirect target is disallowed: drop the page rather than keep a disallowed fetch
                    self.session.http.cache_path(f"{BASE_URL}{path}").unlink(missing_ok=True)
                    raise PageNotAvailable(f"{BASE_URL}{final} is disallowed by robots.txt; not kept")
                marker = f"/clanci/sluzbeni/full/{year}_".encode()
                if (
                    b'class="sl-content"' not in data
                    or marker not in data
                    or f"_{edition}_{number}.html".encode() not in data
                ):
                    raise PageNotAvailable(f"{BASE_URL}{path}: not the page of act NN {edition}/{year} no. {number}")
            except PageNotAvailable as exc:
                self.fetch_errors.append(f"hr-nn:{eli}: {exc}")
                continue
            yield RawDocument(
                external_id=f"hr-nn:{eli}",
                uri=f"{BASE_URL}{path}",
                media_type="text/html",
                content=data,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={
                    "eli": f"sluzbeni/{eli}",
                    "title": act.get("title"),
                    "kind": act.get("kind"),
                    "cached_fetch": meta,
                },
            )
