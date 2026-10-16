"""Fetches UK Supreme Court judgments that the Court publishes as PDF (2009 to 2020) from the Court's own website.

``https://supremecourt.uk/cases/uksc-<year>-<number>`` is the Court's page for one case: case id, title, judgment date,
neutral citation, the justices and a link to the judgment PDF
(``https://supremecourt.uk/uploads/uksc_<year>_<number>_judgment_<hash>.pdf``).
The cases to fetch are listed in the manifest (``config/manifests/gb-uksc-judgments-pdf.yaml``, built from the Court's
sitemap by
``scripts/build_uksc_pdf_manifest.py``, without the judgments the HTML source ``gb-uksc-judgments`` already holds).
For each case the
page and then the PDF are fetched and passed on together as one JSON bundle, both exactly as received.

Terms: the Court's website content is Crown copyright re-usable under the Open Government Licence v3.0
(https://www.supremecourt.uk/about/terms-and-conditions); see ``config/sources/gb-uksc-judgments-pdf.yaml``. This is
the Court's own
site and not the National Archives' Find Case Law service.

``PoliteHttp``: identifying User-Agent, at least 5 s between requests, HTTPS to ``supremecourt.uk`` only, back-off on
429/5xx, any other
refusal stops the run, cache under ``<cache_dir>/supremecourt.uk/``. The host's ``robots.txt`` is read first and every
URL is checked
against it. A case page that is missing (404), is not the page of the listed case, or has the neutral citation of a
case already passed
on (joined appeals have one page per case number) is not passed on and is reported in ``fetch_errors``; a case without
a judgment yet
(no neutral citation or no PDF link) is skipped silently and listed in ``self.not_yet_judged``.
"""

from __future__ import annotations

import base64
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
from ulis.sources.parsers.uksc_pdf import BUNDLE_SCHEMA, bundle_bytes

HOST = "supremecourt.uk"
BASE_URL = f"https://{HOST}"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
MIN_DELAY = 5.0
MAX_PDF_BYTES = 40_000_000
CASE_ID = re.compile(r"^(UKSC|JCPC)/(\d{4})/(\d{4})$")
CITATION = re.compile(rb'<p[^>]*\bid="neutral-citation-number"[^>]*>\s*(\[\d{4}\] (?:UKSC|UKPC) \d{1,3})\s*</p>')

__all__ = ["FetchError", "UkscPdfConnector", "case_url"]


def case_url(case_id: str, *, host: str = HOST, prefix: str = "UKSC") -> str:
    m = CASE_ID.match(case_id)
    if not m or m.group(1) != prefix:
        raise ManifestError(f"invalid case id {case_id!r} (expected {prefix}/yyyy/nnnn)")
    return f"https://{host}/cases/{prefix.lower()}-{m.group(2)}-{m.group(3)}"


class UkscPdfConnector:
    # The Judicial Committee of the Privy Council (jcpc.uk) is the same site design: see connectors/jcpc.py.
    host = HOST
    prefix = "UKSC"

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
        self.not_yet_judged: list[str] = []
        self.http: PoliteHttp | None = None

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if str(manifest.get("base_url", "")).rstrip("/") != f"https://{self.host}":
            raise ManifestError(f"base_url must be https://{self.host}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        if float(manifest.get("crawl_delay_seconds", MIN_DELAY)) < MIN_DELAY:
            raise ManifestError(f"crawl_delay_seconds must be at least {MIN_DELAY:g}")
        cases = manifest.get("cases") or []
        if not cases:
            raise ManifestError("the manifest lists no cases")
        seen: set[str] = set()
        for case_id in cases:
            case_url(str(case_id), host=self.host, prefix=self.prefix)  # validates the pattern
            if case_id in seen:
                raise ManifestError(f"{case_id} is listed twice")
            seen.add(case_id)
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={self.host},
            crawl_delay=float(manifest.get("crawl_delay_seconds", MIN_DELAY)),
            offline=self.offline,
            sleep=self._sleep,
        )
        # No robots.txt (404) means no restrictions (RFC 9309, 2.3.1.3); any other failure stops the run.
        robots_url = f"https://{self.host}/robots.txt"
        got = self.http.get(robots_url, cache_key="robots.txt")
        robots = got[0].decode("utf-8", "replace") if got is not None else ""
        pdf_link = re.compile(
            rb'\bid="judgment-pdf"[^>]*\bhref="(https://'
            + re.escape(self.host.encode())
            + rb'/uploads/[A-Za-z0-9_.-]+\.pdf)"'
        )
        citations: dict[bytes, str] = {}
        for case_id in manifest["cases"]:
            url = case_url(case_id, host=self.host, prefix=self.prefix)
            if not robots_allows(robots, self.http.user_agent, url):
                raise FetchError(f"{url}: disallowed by {robots_url}; not fetched")
            got = self.http.get(url, cache_key="cases/" + url.rsplit("/", 1)[1] + ".html")
            if got is None:
                self.fetch_errors.append(f"{url}: not found (404); skipped")
                continue
            page, meta = got
            if "html" not in str(meta.get("content_type") or "").lower() or case_id.encode() not in page:
                self.fetch_errors.append(f"{url}: not the case page of {case_id}; skipped")
                continue
            found, link = CITATION.search(page), pdf_link.search(page)
            if not found or not link:
                self.not_yet_judged.append(case_id)  # an appeal without a judgment (yet)
                continue
            citation = found.group(1)
            if citation in citations:
                shown = citation.decode("utf-8", "replace")
                self.fetch_errors.append(f"{url}: the same judgment ({shown}) as {citations[citation]}; skipped")
                continue
            pdf_url = link.group(1).decode()
            if not robots_allows(robots, self.http.user_agent, pdf_url):
                raise FetchError(f"{pdf_url}: disallowed by {robots_url}; not fetched")
            pdf = self.http.get(pdf_url, cache_key="uploads/" + pdf_url.rsplit("/", 1)[1])
            if pdf is None:
                self.fetch_errors.append(f"{pdf_url}: judgment PDF not found (404); {case_id} skipped")
                continue
            data, pdf_meta = pdf
            if not data.startswith(b"%PDF-") or len(data) > MAX_PDF_BYTES:
                self.fetch_errors.append(
                    f"{pdf_url}: not a PDF or larger than {MAX_PDF_BYTES} bytes; {case_id} skipped"
                )
                continue
            citations[citation] = case_id
            content = bundle_bytes(
                {
                    "schema": BUNDLE_SCHEMA,
                    "case_page_url": url,
                    "case_page": page.decode("utf-8", "replace"),
                    "pdf_url": pdf_url,
                    "pdf_base64": base64.b64encode(data).decode("ascii"),
                }
            )
            yield RawDocument(
                external_id=f"{self.prefix.lower()}-pdf:{case_id}",
                uri=pdf_url,
                media_type="application/json",
                content=content,
                retrieved_at=datetime.fromisoformat(pdf_meta["retrieved_at"]),
                metadata={"case_id": case_id, "case_page": meta, "pdf": pdf_meta},
            )
