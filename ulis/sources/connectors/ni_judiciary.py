"""Fetches judgments from the website of the Judiciary of Northern Ireland (https://www.judiciaryni.uk).

``https://www.judiciaryni.uk/judicial-decisions/<slug>`` is the page of one decision (title, neutral citation, date
delivered,
judges and the link to the judgment PDF under ``/files/judiciaryni/``). The decisions to fetch are listed in the
manifest
(``config/manifests/gb-ni-judiciary-judgments.yaml``, built from the site's listing by
``scripts/build_judiciaryni_manifest.py``); for each, the page and then the PDF are fetched and passed on together as
one
JSON bundle, both exactly as received.

Terms: "The material featured on this website is subject to Crown copyright protection and licensed for use under the
Open
Government Licence unless otherwise indicated" (https://www.judiciaryni.uk/crown-copyright, read 2026-10-16); robots.txt
(https://www.judiciaryni.uk/robots.txt) disallows ``/search/``, ``/publications/*type``, ``/publications/*date`` and the
administrative paths, not ``/judicial-decisions/`` or ``/files/``. See ``config/sources/gb-ni-judiciary-
judgments.yaml``.

``PoliteHttp``: identifying User-Agent, at least 5 s between requests, HTTPS to ``www.judiciaryni.uk`` only, back-off on
429/5xx, any other refusal stops the run, cache under ``<cache_dir>/www.judiciaryni.uk/``. A page that is missing (404),
that has no neutral citation (a practice direction or a note, not a judgment), no PDF, or the neutral citation of a
decision
already passed on is not passed on: the first two are listed in ``self.not_judgments``, the others in
``fetch_errors``; the
run goes on.
"""

from __future__ import annotations

import base64
import re
import time
import urllib.parse
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.parsers.ni_judiciary import BUNDLE_SCHEMA, bundle_bytes

HOST = "www.judiciaryni.uk"
BASE_URL = f"https://{HOST}"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
MIN_DELAY = 5.0
MAX_PDF_BYTES = 40_000_000
SLUG = re.compile(r"^\d{4}-ni[a-z]+-\d{1,4}(?:-\d{1,2})?$")
CITATION = re.compile(rb"Neutral Citation No\.?\s*(\[\d{4}\]\s+NI[A-Za-z]+\s+\d{1,4})")
PDF_LINK = re.compile(rb'href="(/files/judiciaryni/[^"?#]+\.pdf)"')

__all__ = ["FetchError", "NiJudiciaryConnector", "decision_url"]


def decision_url(slug: str) -> str:
    if not SLUG.match(slug):
        raise ManifestError(
            f"invalid decision page name {slug!r} (expected <year>-<series>-<number>, e.g. 2026-nica-38)"
        )
    return f"{BASE_URL}/judicial-decisions/{slug}"


class NiJudiciaryConnector:
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
        self.fetch_errors: list[str] = []
        self.not_judgments: list[str] = []
        self.http: PoliteHttp | None = None

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if str(manifest.get("base_url", "")).rstrip("/") != BASE_URL:
            raise ManifestError(f"base_url must be {BASE_URL}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        if float(manifest.get("crawl_delay_seconds", MIN_DELAY)) < MIN_DELAY:
            raise ManifestError(f"crawl_delay_seconds must be at least {MIN_DELAY:g}")
        decisions = manifest.get("decisions") or []
        if not decisions:
            raise ManifestError("the manifest lists no decisions")
        seen: set[str] = set()
        for slug in decisions:
            decision_url(str(slug))  # validates the pattern
            if slug in seen:
                raise ManifestError(f"{slug} is listed twice")
            seen.add(slug)
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", MIN_DELAY)),
            offline=self.offline,
            sleep=self._sleep,
        )
        # No robots.txt (404) means no restrictions (RFC 9309, 2.3.1.3); any other failure stops the run.
        got = self.http.get(ROBOTS_URL, cache_key="robots.txt")
        robots = got[0].decode("utf-8", "replace") if got is not None else ""
        citations: dict[bytes, str] = {}
        for slug in manifest["decisions"]:
            url = decision_url(slug)
            if not robots_allows(robots, self.http.user_agent, url):
                raise FetchError(f"{url}: disallowed by {ROBOTS_URL}; not fetched")
            got = self.http.get(url, cache_key=f"judicial-decisions/{slug}.html")
            if got is None:
                self.fetch_errors.append(f"{url}: not found (404); skipped")
                continue
            page, meta = got
            if "html" not in str(meta.get("content_type") or "").lower():
                self.fetch_errors.append(f"{url}: expected HTML, got {meta.get('content_type')!r}; skipped")
                continue
            found, link = CITATION.search(page), PDF_LINK.search(page)
            if not found or not link:
                self.not_judgments.append(slug)  # a practice direction, a note, or a decision without a PDF
                continue
            citation = re.sub(rb"\s+", b" ", found.group(1))
            if citation in citations:
                shown = citation.decode("utf-8", "replace")
                self.fetch_errors.append(f"{url}: the same decision ({shown}) as {citations[citation]}; skipped")
                continue
            pdf_path = link.group(1).decode()
            pdf_url = BASE_URL + urllib.parse.quote(urllib.parse.unquote(pdf_path), safe="/%")
            if not robots_allows(robots, self.http.user_agent, pdf_url):
                raise FetchError(f"{pdf_url}: disallowed by {ROBOTS_URL}; not fetched")
            pdf = self.http.get(pdf_url, cache_key=f"pdf/{slug}.pdf")
            if pdf is None:
                self.fetch_errors.append(f"{pdf_url}: judgment PDF not found (404); {slug} skipped")
                continue
            data, pdf_meta = pdf
            if not data.startswith(b"%PDF-") or len(data) > MAX_PDF_BYTES:
                self.fetch_errors.append(f"{pdf_url}: not a PDF or larger than {MAX_PDF_BYTES} bytes; {slug} skipped")
                continue
            citations[citation] = slug
            content = bundle_bytes(
                {
                    "schema": BUNDLE_SCHEMA,
                    "decision_page_url": url,
                    "decision_page": page.decode("utf-8", "replace"),
                    "pdf_url": pdf_url,
                    "pdf_base64": base64.b64encode(data).decode("ascii"),
                }
            )
            yield RawDocument(
                external_id=f"ni-judiciary:{slug}",
                uri=url,
                media_type="application/json",
                content=content,
                retrieved_at=datetime.fromisoformat(pdf_meta["retrieved_at"]),
                metadata={"slug": slug, "decision_page": meta, "pdf": pdf_meta},
            )
