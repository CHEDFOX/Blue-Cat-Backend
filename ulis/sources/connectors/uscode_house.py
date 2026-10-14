"""Polite fetcher for the United States Code in USLM XML (Office of the Law Revision Counsel).

For every title in the manifest it downloads one release point's per-title archive from the OLRC's
download page (``https://uscode.house.gov/download.html``)::

    https://uscode.house.gov/download/releasepoints/us/pl/<congress>/<law>/xml_usc<NN>@<congress>-<law>.zip

and yields the archive exactly as received; the USLM parser opens it. (GovInfo's USCODE collection
offers PDF and HTML only, no XML, and ``/bulkdata/USCODE`` does not exist - see
``config/sources/us-uscode-house.yaml``.)

Built on :class:`PoliteHttp`: identifying User-Agent, 5 s between requests (the site's robots.txt
disallows only the Yahoo ``Slurp`` robot and sets no Crawl-delay; it is read and applied to every
URL anyway), HTTPS to this one host only, refusals and 429s respected, and a cache under
``<cache_dir>/uscode.house.gov/`` so runs can be repeated offline. The release point and the date
the OLRC states for it are read from the manifest; nothing is discovered from the live page.
"""

from __future__ import annotations

import hashlib
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

HOST = "uscode.house.gov"
BASE_URL = f"https://{HOST}"
_RELEASE_POINT = re.compile(r"^(\d{1,3})-(\d{1,4})$")


def title_url(release_point: str, number: int) -> str:
    """URL of the XML archive of one main title (not an appendix) for a release point."""
    m = _RELEASE_POINT.match(release_point)
    if m is None or not 1 <= number <= 54:
        raise ManifestError(f"invalid release point {release_point!r} or title {number!r}")
    congress, law = m.groups()
    return f"{BASE_URL}/download/releasepoints/us/pl/{congress}/{law}/xml_usc{number:02d}@{release_point}.zip"


class UscodeHouseConnector:
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

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if manifest.get("base_url", BASE_URL).rstrip("/") != BASE_URL:
            raise ManifestError(f"base_url must be {BASE_URL}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        release = manifest.get("release_point") or {}
        if not _RELEASE_POINT.match(str(release.get("id", ""))):
            raise ManifestError("release_point.id must look like '119-120' (Congress-Public Law number)")
        titles = manifest.get("titles") or []
        if not titles:
            raise ManifestError("the manifest lists no titles")
        seen: set[int] = set()
        for entry in titles:
            number = entry.get("number")
            if not isinstance(number, int) or number in seen:
                raise ManifestError(f"invalid or repeated title number {number!r}")
            seen.add(number)
            title_url(str(release["id"]), number)  # validates the range
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        release = str(manifest["release_point"]["id"])
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", 5)),
            offline=self.offline,
            sleep=self._sleep,
        )
        robots = self.http.get(f"{BASE_URL}/robots.txt", cache_key="robots.txt")
        if robots is None:
            raise FetchError(f"{BASE_URL}/robots.txt: not found; refusing to fetch without reading it")
        robots_txt = robots[0].decode("utf-8", errors="replace")
        for entry in manifest["titles"]:
            number = int(entry["number"])
            url = title_url(release, number)
            if not robots_allows(robots_txt, manifest["user_agent"], url):
                raise FetchError(f"{url}: disallowed by {BASE_URL}/robots.txt; not fetched")
            got = self.http.get(url)
            if got is None:
                raise FetchError(f"{url}: not found (404); the release point may have moved to the prior archive")
            data, meta = got
            if not data.startswith(b"PK\x03\x04"):
                raise FetchError(
                    f"{url}: response is not a zip archive ({meta.get('content_type')}); not ingested. "
                    f"Remove {self.http.cache_path(url)} after checking the site."
                )
            pinned = entry.get("sha256")
            if pinned and hashlib.sha256(data).hexdigest() != pinned:
                raise ManifestError(f"title {number}: sha256 does not match the manifest pin")
            yield RawDocument(
                external_id=f"uscode-house:usc{number:02d}@{release}",
                uri=meta.get("final_url") or url,
                media_type="application/zip",
                content=data,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"title": number, "release_point": release, "cached_fetch": meta},
            )
