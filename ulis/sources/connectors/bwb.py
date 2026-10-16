"""Fetches Dutch national legislation from the Basiswettenbestand (BWB) open-data repository.

For each regulation in the manifest (``config/manifests/nl-bwb.yaml``) it downloads the
regulation's ``manifest.xml`` from ``https://repository.officiele-overheidspublicaties.nl/bwb/<BWB
id>/``, then the XML of the toestanden (consolidated versions) it needs, oldest first:

* ``history: true``: every toestand that had started when the manifest was generated (the
  variants visible at that date: a toestand corrected later for retroactive effect is replaced
  by its corrected variant, ``_1``);
* ``history: false``: only the current toestand.

Toestanden that start later (scheduled consolidations) are never fetched. Each toestand is
yielded exactly as received; the manifest itself is yielded last, as a change report for the
current toestand (``ulis.sources.parsers.bwb``).

The committed manifest lists every ``BWBR`` regulation of the publisher's sitemap (about 43,000;
``scripts/build_bwb_manifest.py``), the six pilot regulations first. A regulation that has lapsed (the
current toestand of ``history: false`` has an end date before the manifest was generated) is skipped
after its manifest has been read and counted in ``lapsed``; one of another kind than a wet, an AMvB or a ministeriele
regeling (``wetgeving/@soort``: beleidsregel, reglement, ...) is skipped after its toestand has been read and listed in
``unsupported``; one whose manifest or toestand is missing
(404) or unusable is reported in ``fetch_errors`` and the run goes on; any other refusal stops it.

Access rules (see ``config/sources/nl-bwb.yaml``): the repository publishes no robots.txt (404);
the SRU search service on ``zoekservice.overheid.nl`` is **not** used, because that host's
robots.txt disallows all robots, and ``wetten.overheid.nl`` (the website) is not crawled (only its sitemap is read,
once, by ``scripts/build_bwb_manifest.py``, to make the manifest).
PoliteHttp: identifying User-Agent, 5 s between requests, back-off on 429/5xx, any other refusal
stops the run, HTTPS to the repository host only, cache under ``var/sources/`` so runs can be
repeated offline. The manifest's SHA-512 hash of each file is compared with the download and the
result recorded (KOOP regenerated older files without updating their hash, so a mismatch is
recorded, not fatal).
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
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.parsers.bwb import BWB_ID, INSTRUMENT_TYPES, REPOSITORY, BwbParseError, read_manifest

HOST = "repository.officiele-overheidspublicaties.nl"
_SOORT = re.compile(rb"<wetgeving\b[^>]*?\bsoort=\"([^\"]*)\"")

__all__ = ["BwbConnector", "FetchError"]


class _Skipped(FetchError):
    """One regulation cannot be used (missing or unreadable file); the run goes on with the next."""


class BwbConnector:
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
        self.lapsed: list[str] = []
        self.unsupported: list[str] = []

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if str(manifest.get("base_url", REPOSITORY)).rstrip("/") != REPOSITORY:
            raise ManifestError(f"base_url must be {REPOSITORY}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        regulations = manifest.get("regulations") or []
        if not regulations:
            raise ManifestError("the manifest lists no regulations")
        ids = [str(r.get("bwb_id", "")) for r in regulations]
        for bwb_id in ids:
            if not BWB_ID.match(bwb_id):
                raise ManifestError(f"invalid BWB id {bwb_id!r}")
        if len(ids) != len(set(ids)):
            raise ManifestError("a regulation is listed twice")
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.fetch_errors = []
        self.lapsed = []
        self.unsupported = []
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", 5)),
            offline=self.offline,
            sleep=self._sleep,
        )
        for regulation in manifest["regulations"]:
            try:
                yield from self._regulation(regulation)
            except _Skipped as exc:
                self.fetch_errors.append(f"bwb:{regulation['bwb_id']}: {exc}; skipped")

    def _get(self, url: str) -> tuple[bytes, dict[str, Any]]:
        assert self.http is not None
        got = self.http.get(url)
        if got is None:
            raise _Skipped(f"{url}: not found (404)")
        return got

    def _regulation(self, regulation: dict[str, Any]) -> Iterator[RawDocument]:
        bwb_id = regulation["bwb_id"]
        listing_url = f"{REPOSITORY}/{bwb_id}/manifest.xml"
        listing_bytes, listing_meta = self._get(listing_url)
        try:
            listing = read_manifest(listing_bytes)
        except BwbParseError as exc:
            raise _Skipped(f"{listing_url}: {exc}") from exc
        if listing.bwb_id != bwb_id:
            raise _Skipped(f"{listing_url}: manifest is for {listing.bwb_id}")
        if not regulation.get("history", False):
            last = listing.current
            if last.end is not None and last.end < listing.generated:
                self.lapsed.append(bwb_id)  # no toestand in force any more: not part of the current law
                return
        wanted = listing.past if regulation.get("history", False) else [listing.current]
        for toestand in wanted:
            url = toestand.url(bwb_id)
            content, meta = self._get(url)
            if not re.match(rb"\A(\xef\xbb\xbf)?<\?xml[^>]*\?>\s*<toestand[\s>]", content):
                raise _Skipped(
                    f"{url}: response is not a BWB toestand ({meta.get('content_type')}); not ingested. "
                    f"Remove {self.http.cache_path(url) if self.http else url} after checking the repository."
                )
            kind = _SOORT.search(content)
            if kind is not None and kind.group(1).decode("utf-8", "replace") not in INSTRUMENT_TYPES:
                # A beleidsregel, reglement ...: not an Act, AMvB or ministerial regulation. Nothing of it is stored.
                self.unsupported.append(f"{bwb_id}: {kind.group(1).decode('utf-8', 'replace')}")
                return
            sha512 = hashlib.sha512(content).hexdigest()
            yield RawDocument(
                external_id=f"bwb:{bwb_id}@{toestand.label}",
                uri=meta.get("final_url") or url,
                media_type="application/xml",
                content=content,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={
                    "bwb_id": bwb_id,
                    "toestand": toestand.label,
                    "geldig_van": toestand.start.isoformat(),
                    "geldig_tot_en_met": toestand.end.isoformat() if toestand.end else None,
                    "manifest_sha512": toestand.sha512,
                    "manifest_sha512_matches": toestand.sha512 == sha512 if toestand.sha512 else None,
                    "manifest_generated": listing.generated.isoformat(),
                    "cached_fetch": meta,
                },
            )
        yield RawDocument(
            external_id=f"bwb:{bwb_id}:manifest",
            uri=listing_meta.get("final_url") or listing_url,
            media_type="application/xml",
            content=listing_bytes,
            retrieved_at=datetime.fromisoformat(listing_meta["retrieved_at"]),
            metadata={
                "bwb_id": bwb_id,
                "manifest_generated": listing.generated.isoformat(),
                "current_toestand": listing.current.label,
                "scheduled_toestanden": [t.label for t in listing.scheduled],
                "cached_fetch": listing_meta,
            },
        )
