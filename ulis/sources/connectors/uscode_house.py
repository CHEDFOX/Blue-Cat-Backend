"""Polite fetcher for the United States Code in USLM XML (Office of the Law Revision Counsel).

For every title and appendix in the manifest it downloads one release point's archive from the OLRC's
download page (``https://uscode.house.gov/download.html``)::

    https://uscode.house.gov/download/releasepoints/us/pl/<congress>/<law>/xml_usc<NN>@<congress>-<law>.zip
    https://uscode.house.gov/download/releasepoints/us/pl/<congress>/<law>/xml_usc<NN>a@<congress>-<law>.zip

and yields the archive exactly as received; the USLM parser opens it. (GovInfo's USCODE collection
offers PDF and HTML only, no XML, and ``/bulkdata/USCODE`` does not exist - see
``config/sources/us-uscode-house.yaml``.)

Built on :class:`PoliteHttp`: identifying User-Agent, 5 s between requests (the site's robots.txt
disallows only the Yahoo ``Slurp`` robot and sets no Crawl-delay; it is read and applied to every
URL anyway), HTTPS to this one host only, refusals and 429s respected, and a cache under
``<cache_dir>/uscode.house.gov/`` so runs can be repeated offline. The release point and the date
the OLRC states for it are read from the manifest (``release_point.id`` and ``release_point.date``, the
latter copied from the download page's "current through Public Law 119-121 (10/09/2026)") and passed to the
parser with every document (``metadata["release_point"]``, ``metadata["release_date"]``): the XML itself says
only ``Online@119-121``. Nothing is discovered from the live page (``scripts/build_uscode_manifest.py`` reads
the page, once, to regenerate the manifest: every main title of the Code, 1 to 54 except the reserved 53, the
appendices to titles 5, 11, 18, 28 and 50, and the SHA-256 of each archive).

A title archive that is missing (404) is reported in ``fetch_errors`` and the run goes on; an archive whose
SHA-256 differs from the manifest's pin, or that is not a zip, stops the run.
"""

from __future__ import annotations

import hashlib
import html
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
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


def title_url(release_point: str, number: int, appendix: bool = False) -> str:
    """URL of the XML archive of one main title, or (``appendix=True``) of the appendix to that title
    (``xml_usc05a``), for a release point."""
    m = _RELEASE_POINT.match(release_point)
    if m is None or not 1 <= number <= 54:
        raise ManifestError(f"invalid release point {release_point!r} or title {number!r}")
    congress, law = m.groups()
    name = f"xml_usc{number:02d}{'a' if appendix else ''}@{release_point}.zip"
    return f"{BASE_URL}/download/releasepoints/us/pl/{congress}/{law}/{name}"


def archive_label(number: int, appendix: bool = False) -> str:
    """``usc05``, or ``usc05a`` for the appendix to title 5: the OLRC's own file stem."""
    return f"usc{number:02d}{'a' if appendix else ''}"


@dataclass(frozen=True)
class DownloadPage:
    """What the OLRC's download page states: the release point, its currency and the per-title archives."""

    release_point: str  # "119-121"
    current_through: str  # "Public Law 119-121 (10/09/2026)"
    release_date: date  # the date in current_through, the only date the OLRC states for the release point
    titles: list[dict[str, Any]]  # {"number", "name", "positive_law"}; the reserved title 53 is not listed
    appendices: list[dict[str, Any]] = field(default_factory=list)  # {"number", "name"}: the appendix to title N


# "usctitlechanged": the page prints titles changed since the previous release point in bold.
_TITLE_ITEM = re.compile(
    r'<div class="usctitle(?:changed)?" id="us/usc/t(\d+)"[^>]*>((?:(?!</div>).)*)</div>'
    r'\s*<div class="itemcurrency">([^<]*)</div>',
    re.S,
)
# The appendix to a title is its own row: <div class="usctitleappendix" id="us/usc/t5a">Appendix</div>.
_APPENDIX_ITEM = re.compile(
    r'<div class="usctitleappendix" id="us/usc/t(\d+)a"[^>]*>((?:(?!</div>).)*)</div>'
    r'\s*<div class="itemcurrency">([^<]*)</div>',
    re.S,
)
_THROUGH = re.compile(r"All files are current through\s+(Public Law\s+[\d-]+\s+\((\d{2})/(\d{2})/(\d{4})\))")


def release_date_of(current_through: str) -> date:
    """The date in "Public Law 119-121 (10/09/2026)" (the OLRC prints it as month/day/year)."""
    m = re.search(r"\((\d{2})/(\d{2})/(\d{4})\)", current_through)
    if m is None:
        raise ManifestError(f"no (MM/DD/YYYY) date in {current_through!r}")
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError as exc:
        raise ManifestError(f"not a date: {current_through!r}") from exc


def parse_download_page(page: str) -> DownloadPage:
    """Read https://uscode.house.gov/download.html (kept as the OLRC prints it; nothing is guessed).

    Each main title is a ``usctitle`` block ``Title N - Name`` with a trailing ``*`` footnote link for a title
    "enacted as positive law" (the page's own legend). A block counts only when its row offers the title's XML
    archive for the page's release point, so the reserved title (53) is never listed. The appendix to a title is
    a ``usctitleappendix`` block with the archive ``xml_usc<NN>a``; the page's legend says of it that "any
    Appendix to this title has not been enacted as part of the title".
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
    appendices: list[dict[str, Any]] = []
    for item in _APPENDIX_ITEM.finditer(page):
        number = int(item.group(1))
        if f"xml_usc{number:02d}a@{release.group(1)}.zip" not in page or item.group(3).strip() != release.group(1):
            continue
        label = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", item.group(2)))).strip()
        if label != "Appendix" or number not in {t["number"] for t in titles}:
            raise ManifestError(f"unexpected appendix row {label!r} for title {number}")
        appendices.append({"number": number, "name": f"Appendix to Title {number}"})
    current_through = re.sub(r"\s+", " ", through.group(1))
    return DownloadPage(release.group(1), current_through, release_date_of(current_through), titles, appendices)


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
        if "date" in release:
            # The date the OLRC states for the release point ("current through Public Law 119-121 (10/09/2026)"),
            # copied from the download page by scripts/build_uscode_manifest.py; the parser dates every text by it.
            given = release["date"]
            if isinstance(given, datetime):
                raise ManifestError("release_point.date must be a date (YYYY-MM-DD), not a timestamp")
            try:
                released = given if isinstance(given, date) else date.fromisoformat(str(given))
            except ValueError as exc:
                raise ManifestError(f"release_point.date {given!r} is not a date (YYYY-MM-DD)") from exc
            if release.get("current_through") and release_date_of(str(release["current_through"])) != released:
                raise ManifestError("release_point.date does not agree with the date in release_point.current_through")
        titles = manifest.get("titles") or []
        if not titles:
            raise ManifestError("the manifest lists no titles")
        appendices = manifest.get("appendices") or []
        if not isinstance(appendices, list):
            raise ManifestError("appendices must be a list of {number, sha256} entries")
        seen: set[tuple[int, bool]] = set()
        for entry, appendix in [(t, False) for t in titles] + [(a, True) for a in appendices]:
            number = entry.get("number")
            if not isinstance(number, int) or (number, appendix) in seen:
                raise ManifestError(f"invalid or repeated {'appendix' if appendix else 'title'} number {number!r}")
            seen.add((number, appendix))
            title_url(str(release["id"]), number, appendix)  # validates the range
            if "positive_law" in entry and not isinstance(entry["positive_law"], bool):
                raise ManifestError(f"title {number}: positive_law must be true or false")
        return manifest

    @staticmethod
    def release_metadata(manifest: dict[str, Any]) -> dict[str, Any]:
        """What the manifest states about the release point, for the parser: the id, the date (ISO) and the
        OLRC's wording. Only facts the manifest holds are passed; the parser refuses a release point it cannot date."""
        release = manifest["release_point"]
        out: dict[str, Any] = {"release_point": str(release["id"])}
        if release.get("date"):
            given = release["date"]
            out["release_date"] = (given if isinstance(given, date) else date.fromisoformat(str(given))).isoformat()
        if release.get("current_through"):
            out["current_through"] = str(release["current_through"])
        return out

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
        stated = self.release_metadata(manifest)
        archives = [(t, False) for t in manifest["titles"]] + [(a, True) for a in manifest.get("appendices") or []]
        for entry, appendix in archives:
            number = int(entry["number"])
            url = title_url(release, number, appendix)
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
                raise ManifestError(f"{archive_label(number, appendix)}: sha256 does not match the manifest pin")
            yield RawDocument(
                external_id=f"uscode-house:{archive_label(number, appendix)}@{release}",
                uri=meta.get("final_url") or url,
                media_type="application/zip",
                content=data,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"title": number, "appendix": appendix, **stated, "cached_fetch": meta},
            )
