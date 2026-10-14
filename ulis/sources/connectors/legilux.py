"""Fetches Luxembourg consolidated legislation (Akoma Ntoso XML) from Legilux.

The manifest (``config/manifests/lu-legilux.yaml``, built by ``scripts/build_legilux_manifest.py`` from the
Legilux SPARQL endpoint) lists, for each consolidated work (a code, the Constitution, a consolidated law or
regulation), the dates of its consolidations; every consolidation has one XML file in French.

Access rules (see ``config/sources/lu-legilux.yaml``):

* the linked data names files under ``https://data.legilux.public.lu/filestore/``, a path that host's robots.txt
  disallows (together with ``/eli/*``). The Legilux website serves the same files under
  ``https://legilux.public.lu/filestore/`` (its ``api/settings`` ``manifestation_prefix`` is
  ``https://legilux.public.lu``), and that host's robots.txt does not disallow them. Files are fetched only from
  ``legilux.public.lu``, never from the disallowed ``data.legilux.public.lu`` paths, and every URL is checked
  against the robots.txt of ``legilux.public.lu`` before it is requested;
* PoliteHttp: identifying User-Agent, 5 s between requests (no Crawl-delay is published), back-off on 429/5xx,
  any other refusal stops the run, cache under ``var/sources/legilux.public.lu/``;
* consolidations are yielded per work in chronological order, so each later consolidation is compared with the
  versions already recorded (see ``ulis/ingestion/snapshots.py``);
* a file that is missing (404) is recorded in ``fetch_errors`` and the run continues.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
HOST = "legilux.public.lu"
ROBOTS_URL = f"https://{HOST}/robots.txt"
SPARQL_ENDPOINT = "https://data.legilux.public.lu/sparqlendpoint"
ELI_PREFIX = "http://data.legilux.public.lu/eli/etat/leg/"
# A consolidated work: ``code/civil``, ``constitution/1868/10/17/n1/consolide``, ``loi/2018/08/01/a686/consolide``.
_WORK_PATH = re.compile(r"^[a-z]{1,12}(?:/[A-Za-z0-9_.-]+)+$")

__all__ = ["FetchError", "LegiluxConnector", "file_url", "work_eli"]


def work_eli(path: str) -> str:
    return ELI_PREFIX + path


def file_url(path: str, day: date | str) -> str:
    """Public URL of the French XML of the consolidation of work ``path`` applicable on ``day``."""
    stamp = day.strftime("%Y%m%d") if isinstance(day, date) else str(day).replace("-", "")
    slug = path.replace("/", "-")
    return f"https://{HOST}/filestore/eli/etat/leg/{path}/{stamp}/fr/xml/eli-etat-leg-{slug}-{stamp}-fr-xml.xml"


class LegiluxConnector:
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
        self.robots: str | None = None
        # Files that were not found (404): reported as run errors by the pipeline so one missing file among
        # thousands does not abort the run. A refusal (401/403/429/451) still stops it.
        self.fetch_errors: list[str] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        seen: set[str] = set()
        for work in manifest["works"]:
            path = work.get("path", "")
            if not _WORK_PATH.match(path) or ".." in path or path.startswith("adm/"):
                raise ManifestError(f"invalid work path {path!r}")
            if path in seen:
                raise ManifestError(f"{path} is listed twice")
            seen.add(path)
            try:
                dates = [date.fromisoformat(str(d)) for d in work["consolidations"]]
            except ValueError as exc:
                raise ManifestError(f"{path}: invalid consolidation date ({exc})") from exc
            if not dates or dates != sorted(set(dates)):
                raise ManifestError(f"{path}: consolidations must be listed once each, oldest first")
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", 5)),
            offline=self.offline,
            sleep=self._sleep,
        )
        # No robots.txt (404) would mean no restrictions (RFC 9309); any other failure stops the run.
        got = self.http.get(ROBOTS_URL, cache_key="robots.txt")
        self.robots = got[0].decode("utf-8", "replace") if got is not None else None
        for work in manifest["works"]:
            path = work["path"]
            for day in work["consolidations"]:
                url = file_url(path, str(day))
                if self.robots is not None and not robots_allows(self.robots, self.http.user_agent, url):
                    raise FetchError(f"{url}: disallowed by {ROBOTS_URL}; not fetched")
                got = self.http.get(url)
                if got is None:
                    self.fetch_errors.append(f"legilux:{path}@{day}: {url}: not found (404)")
                    continue
                content, meta = got
                if not content.lstrip().startswith(b"<?xml") or "xml" not in str(meta.get("content_type") or ""):
                    self.fetch_errors.append(
                        f"legilux:{path}@{day}: {url}: answer is not XML ({meta.get('content_type')}); not ingested"
                    )
                    continue
                yield RawDocument(
                    external_id=f"legilux:{path}@{day}",
                    uri=meta.get("final_url") or meta["url"],
                    media_type="application/akn+xml",
                    content=content,
                    retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                    metadata={
                        "eli": work_eli(path),
                        "consolidation": str(day),
                        "language": "fr",
                        "cached_fetch": meta,
                    },
                )
