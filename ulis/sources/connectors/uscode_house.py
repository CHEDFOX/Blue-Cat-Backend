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
the OLRC states for it are read from the manifest; nothing is discovered from the live page
(``scripts/build_uscode_manifest.py`` reads the page, once, to regenerate the manifest: every main title of
the Code, 1 to 54 except the reserved 53, with the SHA-256 of each archive).

A title archive that is missing (404) is reported in ``fetch_errors`` and the run goes on; an archive whose
SHA-256 differs from the manifest's pin, or that is not a zip, stops the run.
"""

from __future__ import annotations

import hashlib
import html
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
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


@dataclass(frozen=True)
class DownloadPage:
    """What the OLRC's download page states: the release point, its currency and the per-title archives."""

    release_point: str  # "119-120"
    current_through: str  # "Public Law 119-120 (10/07/2026)"
    titles: list[dict[str, Any]]  # {"number", "name", "positive_law"}; appendices and reserved titles are not listed


# "usctitlechanged": the page prints titles changed since the previous release point in bold.
_TITLE_ITEM = re.compile(
    r'<div class="usctitle(?:changed)?" id="us/usc/t(\d+)"[^>]*>((?:(?!</div>).)*)</div>'
    r'\s*<div class="itemcurrency">([^<]*)</div>',
    re.S,
)
_THROUGH = re.compile(r"All files are current through\s+(Public Law\s+[\d-]+\s+\(\d{2}/\d{2}/\d{4}\))")


def parse_download_page(page: str) -> DownloadPage:
    """Read https://uscode.house.gov/download.html (kept as the OLRC prints it; nothing is guessed).

    Each main title is a ``usctitle`` block ``Title N - Name`` with a trailing ``*`` footnote link for a title
    "enacted as positive law" (the page's own legend). A block counts only when its row offers the title's XML
    archive for the page's release point, so reserved titles (53) and appendices are never listed.
    """
    through = _THROUGH.search(page)
    if through is None:
        raise ManifestError("the download page does not state the release point's currency")
    release = re.search(r"xml_usc\d{2}@(\d{1,3}-\d{1,4})\.zip", page)
    if release is None or not _RELEASE_POINT.match(release.group(1)):
        raise ManifestError("the download page links no per-title XML archive")
    legend = "has been enacted as positive law" in page
    titles: list[dict[str, Any]] = []
    for item in _TITLE_ITEM.finditer(page):
        number = int(item.group(1))
        if f"xml_usc{number:02d}@{release.group(1)}.zip" not in page or item.group(3).strip() != release.group(1):
            continue
        label = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", item.group(2)))).strip()
        named = re.fullmatch(r"Title (\d+) - (.+?)(?: \*)?", label)
        if named is None or int(named.group(1)) != number:
            raise ManifestError(f"unexpected title label {label!r} for title {number}")
        titles.append({"number": number, "name": named.group(2), "positive_law": legend and label.endswith("*")})
    if not titles:
        raise ManifestError("the download page lists no title")
    return DownloadPage(release.group(1), re.sub(r"\s+", " ", through.group(1)), titles)


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
        self.fetch_errors: list[str] = []

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
            if "positive_law" in entry and not isinstance(entry["positive_law"], bool):
                raise ManifestError(f"title {number}: positive_law must be true or false")
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.fetch_errors = []
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
                # One missing archive must not abort the other titles; a refusal (401/403/429) still stops the run.
                self.fetch_errors.append(
                    f"{url}: not found (404); the release point may have moved to the prior archive"
                )
                continue
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
