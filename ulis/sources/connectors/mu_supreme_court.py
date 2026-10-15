"""Fetches decisions of the courts of Mauritius from the Supreme Court website's Judgment Search.

For each year in the manifest (``config/manifests/mu-supreme-court-judgments.yaml``) it reads the pages of
``https://supremecourt.govmu.org/judgment-search?from_year=Y&to_year=Y&page=N`` (10 decisions per page, newest
first) until a page has no rows, and for every row downloads the PDF the row links to
(``/system/files/judgment/<node id>/<file>.pdf``). The text layer of the PDF is extracted with ``pdftotext``
(poppler-utils) and delivered, with the listing row and the PDF's URL, SHA-256 and size, as one JSON bundle
(see :mod:`ulis.sources.parsers.mu_judgments`). The PDF itself stays in the download cache
(``<cache_dir>/supremecourt.govmu.org/pdf/``) and is not stored again.

Access rules (see ``config/sources/mu-supreme-court-judgments.yaml``): the site's terms allow reproduction
of its judgments free of charge in any format or medium if accurate, not misleading, with the source named and
the copyright acknowledged; ``robots.txt`` is the standard Drupal file (it disallows ``/search/`` and the like,
no crawl delay) and every URL is checked against it. ULIS makes sequential requests at least 5 s apart
(PoliteHttp: identifying User-Agent, back-off on 429/5xx, any other refusal stops the run, HTTPS to this one
host only, cache under ``var/sources/``). Per-document problems (a missing PDF, a response that is not a PDF) are
collected in ``fetch_errors`` and the run goes on; a refusal (401/403/429/451) stops it.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import subprocess  # noqa: S404 - poppler's pdftotext, fixed argv, no shell
import tempfile
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
from ulis.sources.parsers.mu_judgments import BUNDLE_SCHEMA

HOST = "supremecourt.govmu.org"
BASE_URL = f"https://{HOST}"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
MIN_DELAY = 5.0
MAX_PDF_BYTES = 60_000_000
MAX_CONSECUTIVE_FAILURES = 25
PDF_PATH = re.compile(r"^/system/files/judgment/(\d+)/[^/]+\.pdf$", re.IGNORECASE)
REFUSAL = re.compile(r"HTTP (401|403|429|451)\b")

__all__ = ["FetchError", "MuSupremeCourtConnector", "listing_url", "parse_listing"]


def listing_url(year: int, page: int) -> str:
    return f"{BASE_URL}/judgment-search?from_year={year}&to_year={year}&page={page}"


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_listing(page: str) -> tuple[list[dict[str, str]], int]:
    """(rows, last page number) of one Judgment Search page; rows hold the table's cells as printed."""
    body = re.search(r"(?s)<tbody>(.*?)</tbody>", page)
    rows: list[dict[str, str]] = []
    for tr in re.findall(r"(?s)<tr\b.*?</tr>", body.group(1)) if body else []:
        link = re.search(r'href="/view_document/(\d+)/\d+\?file=([^"&]+)', tr)
        title = re.search(r'(?s)<span class="document-title">(.*?)</span>', tr)
        by = re.search(r'(?s)<span class="delivered-by">(.*?)</span>', tr)
        number = re.search(r"(?s)<li>(.*?)</li>", tr)
        delivered = re.search(r'(?s)views-field-field-delivered-on[^>]*>\s*<a[^>]*>(.*?)</a>', tr)
        summary = re.search(r'(?s)<td[^>]*views-field-field-summary[^>]*>(.*?)</td>', tr)
        if not (link and title and delivered):
            raise FetchError("a row of the Judgment Search table has no link, title or date; the markup changed?")
        pdf = urllib.parse.unquote(link.group(2)).split("#", 1)[0]
        parts = urllib.parse.urlsplit(pdf)
        match = PDF_PATH.match(urllib.parse.unquote(parts.path))
        if parts.scheme != "https" or parts.netloc != HOST or not match or match.group(1) != link.group(1):
            raise FetchError(f"node {link.group(1)}: unexpected PDF address {pdf!r}")
        rows.append(
            {
                "node_id": link.group(1),
                "title": _text(title.group(1)),
                "delivered_by": _text(by.group(1)) if by else "",
                "document_number": _text(number.group(1)) if number else "",
                "delivered_on": _text(delivered.group(1)),
                "summary": _text(summary.group(1)) if summary else "",
                "pdf_url": f"{BASE_URL}{urllib.parse.quote(urllib.parse.unquote(parts.path))}",
            }
        )
    pages = [int(n) for n in re.findall(r"[?&]page=(\d+)", page)]
    return rows, max(pages, default=0)


def pdftotext_version() -> str:
    exe = shutil.which("pdftotext")
    if exe is None:
        raise FetchError("pdftotext (poppler-utils) is required to read the judgments' PDFs and is not installed")
    done = subprocess.run([exe, "-v"], capture_output=True, text=True, timeout=30, check=False)  # noqa: S603
    return (done.stderr or done.stdout).splitlines()[0].strip() if (done.stderr or done.stdout) else "pdftotext"


def extract_text(pdf: bytes) -> str:
    """The PDF's text layer as ``pdftotext -enc UTF-8`` writes it (pages separated by form feeds)."""
    exe = shutil.which("pdftotext")
    if exe is None:
        raise FetchError("pdftotext (poppler-utils) is required to read the judgments' PDFs and is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "decision.pdf"
        path.write_bytes(pdf)
        done = subprocess.run(  # noqa: S603 - fixed argv, a file in a private temporary directory
            [exe, "-enc", "UTF-8", str(path), "-"], capture_output=True, timeout=180, check=False
        )
    if done.returncode != 0:
        raise FetchError(f"pdftotext failed: {done.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return done.stdout.decode("utf-8")


class MuSupremeCourtConnector:
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
        # Decisions skipped on purpose (reported as run errors by the pipeline): one bad entry among
        # thousands must not abort the run. A refusal still stops it.
        self.fetch_errors: list[str] = []

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
        years = manifest.get("years") or []
        if not years or not all(isinstance(y, int) and 1990 <= y <= 2100 for y in years) or len(set(years)) != len(years):
            raise ManifestError("years must be a list of distinct years (integers, 1990-2100)")
        limit = manifest.get("max_documents")
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            raise ManifestError("max_documents must be a positive integer or null")
        return manifest

    def _get(self, url: str, cache_key: str) -> tuple[bytes, dict[str, Any]] | None:
        assert self.http is not None
        if self.robots is not None and not robots_allows(self.robots, self.http.user_agent, url):
            raise FetchError(f"{url}: disallowed by {ROBOTS_URL}; not fetched")
        return self.http.get(url, cache_key=cache_key)

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        extractor = pdftotext_version()
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", MIN_DELAY)),
            offline=self.offline,
            sleep=self._sleep,
        )
        # No robots.txt (404) would mean no restrictions (RFC 9309); any other failure stops the run.
        got = self.http.get(ROBOTS_URL, cache_key="robots.txt")
        self.robots = got[0].decode("utf-8", "replace") if got is not None else None
        limit = manifest.get("max_documents")
        seen: set[str] = set()
        delivered = 0
        failures = 0
        for year in manifest["years"]:
            page = 0
            last = 0
            while page <= last:
                url = listing_url(year, page)
                got = self._get(url, f"listing/{year}-{page:04d}.html")
                if got is None:
                    self.fetch_errors.append(f"mu-supreme-court:{year} page {page}: {url}: not found (404)")
                    break
                rows, last_seen = parse_listing(got[0].decode("utf-8", "replace"))
                last = max(last, last_seen)
                if not rows:
                    break
                for row in rows:
                    if row["node_id"] in seen:
                        continue  # new decisions push older ones onto the next page while a run is going on
                    seen.add(row["node_id"])
                    doc = self._decision(row)
                    if doc is None:
                        failures += 1
                        if failures >= MAX_CONSECUTIVE_FAILURES:
                            raise FetchError(f"{failures} decisions in a row could not be fetched; stopping the run")
                        continue
                    failures = 0
                    yield RawDocument(
                        external_id=f"mu-supreme-court:{row['node_id']}",
                        uri=row["pdf_url"],
                        media_type="application/json",
                        content=doc[0],
                        retrieved_at=doc[1],
                        metadata={"node_id": row["node_id"], "listing_url": url, "extractor": extractor},
                    )
                    delivered += 1
                    if limit is not None and delivered >= limit:
                        return
                page += 1

    def _decision(self, row: dict[str, str]) -> tuple[bytes, datetime] | None:
        name = f"mu-supreme-court:{row['node_id']}"
        try:
            got = self._get(row["pdf_url"], f"pdf/{row['node_id']}.pdf")
        except FetchError as exc:
            if REFUSAL.search(str(exc)) or "disallowed by" in str(exc) or "offline and not cached" in str(exc):
                raise
            self.fetch_errors.append(f"{name}: {exc}")
            return None
        if got is None:
            self.fetch_errors.append(f"{name}: {row['pdf_url']}: not found (404)")
            return None
        data, meta = got
        if not data.startswith(b"%PDF") or len(data) > MAX_PDF_BYTES:
            self.fetch_errors.append(
                f"{name}: {row['pdf_url']}: the response is not a PDF of a sane size ({meta.get('content_type')}, "
                f"{len(data)} bytes); not ingested"
            )
            return None
        try:
            text = extract_text(data)
        except (FetchError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
            self.fetch_errors.append(f"{name}: {row['pdf_url']}: text could not be extracted ({exc}); not ingested")
            return None
        bundle = {
            "schema": BUNDLE_SCHEMA,
            "listing": {k: row[k] for k in ("node_id", "title", "delivered_by", "document_number", "delivered_on", "summary")},
            "pdf": {"url": row["pdf_url"], "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)},
            "text": text,
        }
        content = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return content, datetime.fromisoformat(meta["retrieved_at"])
