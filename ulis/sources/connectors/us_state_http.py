"""Shared base of the connectors for United States state codes published as plain web files.

Each state connector (``us_wa_rcw``, ``us_tx_statutes``, ``us_or_ors``, ``us_ut_code``) lists in its
manifest exactly what to fetch, reads the robots.txt of every host it contacts and refuses any URL
those rules disallow, waits at least 5 s (more when robots.txt asks for it) between requests, sends
the identifying User-Agent, talks HTTPS to the manifest's hosts only, caches every response under
``<cache_dir>/<host>/`` and yields it wrapped in the shared envelope of :mod:`ulis.sources.us_state_bundle`
(exact bytes plus URL and retrieval time).

Robustness rules (see docs/jurisdictions/us-states.md): a document that is missing (404) or is not what the
manifest says it is goes into ``fetch_errors`` and the run continues; a refusal (401/403/429 after back-off,
a disallowing robots.txt, an unreadable robots.txt) stops the run, and nothing is fetched around it.
"""

from __future__ import annotations

import hashlib
import time
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.infoleg_ar import robots_crawl_delay
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.us_state_bundle import make_bundle

__all__ = ["FetchError", "ManifestError", "StateHttpConnector", "Target"]


@dataclass(frozen=True)
class Target:
    key: str  # identifies the document inside its source ("9A.32", "PE", "163", "76")
    url: str
    # A candidate may legitimately not exist (the publisher's table of contents gives ranges, not a list):
    # a 404 is then recorded in ``not_found`` instead of ``fetch_errors``.
    candidate: bool = False
    sha256: str | None = None  # optional pin from the manifest


class StateHttpConnector:
    state: ClassVar[str]  # postal code, e.g. "WA"
    external_prefix: ClassVar[str]  # prefix of RawDocument.external_id, e.g. "us-wa-rcw"
    hosts: ClassVar[frozenset[str]]
    default_delay: ClassVar[float] = 5.0

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
        self.robots: dict[str, str] = {}
        # Skipped documents (404, or not the expected kind of file): reported as run errors by the pipeline.
        self.fetch_errors: list[str] = []
        # Candidate documents that do not exist (not errors).
        self.not_found: list[str] = []

    # ------------------------------------------------------------------ hooks
    def validate_manifest(self, manifest: dict[str, Any]) -> None:
        """Raise ``ManifestError`` for entries the state's URL scheme cannot represent."""
        raise NotImplementedError

    def targets(self, manifest: dict[str, Any]) -> Iterator[Target]:
        raise NotImplementedError

    def check(self, target: Target, data: bytes, meta: dict[str, Any]) -> str | None:
        """None when ``data`` is the expected kind of file, else the reason it is not."""
        raise NotImplementedError

    # ------------------------------------------------------------------ machinery
    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        self.validate_manifest(manifest)
        return manifest

    def _read_robots(self, host: str, user_agent: str) -> None:
        assert self.http is not None
        url = f"https://{host}/robots.txt"
        got = self.http.get(url, cache_key="robots.txt")
        if got is None:  # 404: the host publishes no rules
            self.robots[host] = ""
            return
        text = got[0].decode("utf-8", errors="replace")
        # A single-page-application host answers every path with its HTML shell: that is not a robots file.
        self.robots[host] = "" if text.lstrip().lower().startswith(("<!doctype", "<html")) else text

    def _allowed(self, url: str, user_agent: str) -> bool:
        host = urllib.parse.urlsplit(url).netloc
        if host not in self.robots:
            self._read_robots(host, user_agent)
            delay = robots_crawl_delay(self.robots[host])
            assert self.http is not None
            if delay:
                self.http.crawl_delay = max(self.http.crawl_delay, delay)
        return robots_allows(self.robots[host], user_agent, url)

    def get(self, url: str, user_agent: str, cache_key: str | None = None) -> tuple[bytes, dict[str, Any]] | None:
        """One polite, robots-checked fetch (None for 404)."""
        assert self.http is not None
        if not self._allowed(url, user_agent):
            raise FetchError(f"{url}: disallowed by robots.txt; not fetched")
        return self.http.get(url, cache_key=cache_key)

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        agent = manifest["user_agent"]
        self.http = PoliteHttp(
            self.cache_dir,
            agent,
            allowed_hosts=set(self.hosts),
            crawl_delay=max(float(manifest.get("crawl_delay_seconds", self.default_delay)), self.default_delay),
            offline=self.offline,
            sleep=self._sleep,
        )
        for host in sorted(self.hosts):
            self._allowed(f"https://{host}/", agent)  # reads robots.txt of every host before anything else
        for target in self.targets(manifest):
            got = self.get(target.url, agent)
            if got is None:
                message = f"{self.external_prefix}:{target.key}: {target.url}: not found (404)"
                (self.not_found if target.candidate else self.fetch_errors).append(message)
                continue
            data, meta = got
            problem = self.check(target, data, meta)
            if problem is not None:
                self.fetch_errors.append(
                    f"{self.external_prefix}:{target.key}: {target.url}: {problem}; not ingested. "
                    f"Remove {self.http.cache_path(target.url)} after checking the site."
                )
                continue
            if target.sha256 and hashlib.sha256(data).hexdigest() != target.sha256:
                raise ManifestError(f"{target.key}: sha256 does not match the manifest pin")
            yield RawDocument(
                external_id=f"{self.external_prefix}:{target.key}",
                uri=meta.get("final_url") or target.url,
                media_type="application/json",
                content=make_bundle(self.state, target.key, data, meta),
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"key": target.key, "cached_fetch": meta},
            )
