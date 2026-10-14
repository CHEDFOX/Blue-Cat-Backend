"""Fetches United States courts of appeals opinions from GovInfo (collection USCOURTS), one opinion at a time.

The manifest (``config/manifests/us-govinfo-uscourts.yaml``) lists court-year sitemaps. For each one the connector
requests ``/sitemap/USCOURTS/USCOURTS_<court>_<year>_sitemap.xml`` (the packages of that court and year, listed by
GovInfo for crawlers), then for each package its metadata ``/metadata/pkg/<package>/mods.xml`` and, for each opinion
(granule) the MODS lists, the PDF ``/content/pkg/<package>/pdf/<granule>.pdf``. Metadata and PDF are yielded together,
exactly as received, as one JSON bundle (``ulis-us-govinfo-uscourts/1``, PDF base64-encoded); the parser reads the
PDF's text layer.

Access rules (see ``config/sources/us-govinfo-uscourts.yaml``): ``robots.txt`` is read first and every URL is
checked against it; sequential requests at least 5 s apart; identifying User-Agent; refusals stop the run; a missing or
malformed package is skipped and reported (``fetch_errors``); cache under ``<cache_dir>/www.govinfo.gov/`` so a run can
be repeated or resumed offline. The JSON API (api key) is not used. ``limit_per_sitemap`` (optional) takes only the
first packages of each sitemap, for a slice.
"""

from __future__ import annotations

import base64
import re
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from defusedxml.ElementTree import fromstring

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.govinfo import BASE_URL, GovInfoClient, load_manifest
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.parsers.govinfo_uscourts import BUNDLE_SCHEMA, COURTS, bundle_bytes

MODS = "{http://www.loc.gov/mods/v3}"
PACKAGE = re.compile(r"^USCOURTS-(?P<court>[A-Za-z0-9]+)-[A-Za-z0-9]{2}-[A-Za-z0-9._-]{1,20}$")
DETAILS = re.compile(r"<loc>https://www\.govinfo\.gov/app/details/(USCOURTS-[^</\s]+)</loc>")

__all__ = ["FetchError", "UsCourtsGovInfoConnector", "mods_url", "pdf_url", "sitemap_url"]


def sitemap_url(court: str, year: int) -> str:
    return f"{BASE_URL}/sitemap/USCOURTS/USCOURTS_{court}_{year}_sitemap.xml"


def mods_url(package: str) -> str:
    return f"{BASE_URL}/metadata/pkg/{package}/mods.xml"


def pdf_url(package: str, granule: str) -> str:
    return f"{BASE_URL}/content/pkg/{package}/pdf/{granule}.pdf"


class UsCourtsGovInfoConnector:
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
        # Packages skipped on purpose (404, not the package asked for, no PDF): reported as run errors by the
        # pipeline so one bad entry among thousands does not abort the source. A refusal still stops the run.
        self.fetch_errors: list[str] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = load_manifest(self.manifest_path, self.expected_source_id)
        sitemaps = manifest.get("sitemaps") or []
        if not sitemaps:
            raise ManifestError("the manifest lists no sitemaps")
        seen: set[tuple[str, int]] = set()
        for entry in sitemaps:
            court, year = entry.get("court"), entry.get("year")
            if court not in COURTS or not isinstance(year, int) or not 1950 <= year <= 2100 or (court, year) in seen:
                raise ManifestError(f"invalid or repeated court/year {court!r}/{year!r}")
            seen.add((court, year))
        limit = manifest.get("limit_per_sitemap")
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            raise ManifestError("limit_per_sitemap must be a positive integer")
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        client = GovInfoClient(manifest, self.cache_dir, offline=self.offline, sleep=self._sleep)
        limit = manifest.get("limit_per_sitemap")
        for entry in manifest["sitemaps"]:
            court, year = entry["court"], entry["year"]
            url = sitemap_url(court, year)
            got = client.get(url, f"sitemap/USCOURTS_{court}_{year}_sitemap.xml")
            if got is None:
                self.fetch_errors.append(f"govinfo:{court}-{year}: {url}: not found (404)")
                continue
            packages = []
            for package in DETAILS.findall(got[0].decode("utf-8", "replace")):
                m = PACKAGE.match(package)
                if m and m.group("court") == court:
                    packages.append(package)
            if not packages:
                self.fetch_errors.append(f"govinfo:{court}-{year}: {url}: lists no package of {court}")
                continue
            for package in packages[:limit]:
                yield from self._package(client, package)

    def _package(self, client: GovInfoClient, package: str) -> Iterator[RawDocument]:
        got = client.get(mods_url(package), f"metadata/{package}.mods.xml")
        if got is None:
            self.fetch_errors.append(f"govinfo:{package}: {mods_url(package)}: not found (404)")
            return
        mods, mods_meta = got
        granules, problem = self._granules(package, mods, mods_meta)
        if problem:
            self.fetch_errors.append(f"govinfo:{package}: {problem}")
            return
        for granule in granules:
            url = pdf_url(package, granule)
            got = client.get(url, f"content/{package}/{granule}.pdf")
            if got is None:
                self.fetch_errors.append(f"govinfo:{granule}: {url}: not found (404)")
                continue
            pdf, pdf_meta = got
            if not pdf.startswith(b"%PDF-"):
                self.fetch_errors.append(
                    f"govinfo:{granule}: {url}: not a PDF file ({pdf_meta.get('content_type')}); not ingested"
                )
                continue
            yield RawDocument(
                external_id=f"govinfo:{granule}",
                uri=url,
                media_type="application/json",
                content=bundle_bytes(
                    {
                        "schema": BUNDLE_SCHEMA,
                        "package": package,
                        "granule": granule,
                        "mods_url": mods_url(package),
                        "mods": mods.decode("utf-8"),
                        "pdf_url": url,
                        "pdf_base64": base64.b64encode(pdf).decode("ascii"),
                    }
                ),
                retrieved_at=datetime.fromisoformat(pdf_meta["retrieved_at"]),
                metadata={"package": package, "granule": granule, "mods_fetch": mods_meta, "pdf_fetch": pdf_meta},
            )

    @staticmethod
    def _granules(package: str, mods: bytes, meta: dict[str, Any]) -> tuple[list[str], str | None]:
        """The opinions (granules) a package's MODS lists, if it is the MODS of exactly this package."""
        if "xml" not in str(meta.get("content_type") or "").lower():
            return [], f"{mods_url(package)}: expected XML, got {meta.get('content_type')!r}; not ingested"
        try:
            root = fromstring(mods, forbid_dtd=True)
        except Exception as exc:  # defusedxml raises several exception types
            return [], f"{mods_url(package)}: not well-formed XML ({exc}); not ingested"
        package_ids = [
            e.findtext(f"{MODS}accessId")
            for e in root.findall(f"{MODS}extension")
            if e.findtext(f"{MODS}docClass") == "USCOURTS"
        ]
        if root.tag != f"{MODS}mods" or package not in package_ids:
            return [], f"{mods_url(package)}: the response is not the package MODS of {package}; not ingested"
        granules = []
        for item in root.findall(f"{MODS}relatedItem[@type='constituent']"):
            granule = item.findtext(f"{MODS}extension/{MODS}accessId") or ""
            links = [
                (u.text or "").strip() for u in item.iter(f"{MODS}url") if u.get("displayLabel") == "PDF rendition"
            ]
            if re.fullmatch(re.escape(package) + r"-\d{1,3}", granule) and pdf_url(package, granule) in links:
                granules.append(granule)
        if not granules:
            return [], f"{mods_url(package)}: the package lists no PDF opinion; not ingested"
        return granules, None
