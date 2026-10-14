"""Polite fetcher for the Electronic Code of Federal Regulations (eCFR) XML, one file per CFR title.

The eCFR is the Office of the Federal Register's (OFR) and the Government Publishing Office's (GPO)
continuously updated, *unofficial* editorial compilation of the Code of Federal Regulations. This connector does
**not** call the eCFR website's API: ``https://www.ecfr.gov/robots.txt`` disallows ``/api/versioner/v1/full/`` and
``/api/renderer/v1/content/`` (the full-text endpoints) for every robot. The same compilation is published by the
GPO itself as a bulk-data collection, which is what is read here::

    https://www.govinfo.gov/bulkdata/ECFR/title-<N>/ECFR-title<N>.xml

(``config/sources/us-ecfr.yaml`` records the terms). Only the current text exists there; there is no dated
series. ``Last-Modified`` of each file, the date GPO generated it, is the date the text is attested for.

A title file is split here into *parts* (one ``DIV5`` element of type ``PART`` each), so that the downstream
pipeline works on one regulation (one CFR part) at a time and an unchanged part is recognised on a later run. A
yielded document is a small envelope (root ``ecfr-part``) holding the title's context (volume amendment date,
the enclosing chapter and subchapter headings) and the part's bytes exactly as received. Built on
:class:`PoliteHttp`: identifying User-Agent, 5 s between requests, HTTPS to ``www.govinfo.gov`` only, refusals
and 429s respected (a refusal stops the run), robots.txt read and applied, a cache under
``<cache_dir>/www.govinfo.gov/``.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

LOG = logging.getLogger(__name__)
HOST = "www.govinfo.gov"
BASE_URL = f"https://{HOST}"
ENVELOPE_TAG = "ecfr-part"
_TITLE_TAG = re.compile(rb"<(/?)DIV([1-5])\b([^>]*)>|<AMDDATE>(.*?)</AMDDATE>", re.DOTALL)
_HEAD = re.compile(rb"\s*(<HEAD>.*?</HEAD>)", re.DOTALL)
_ATTR = re.compile(rb'\b(N|TYPE)="([^"<>&]*)"')
_PART_NUMBER = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")


def title_url(number: int) -> str:
    """URL of one title's XML in GPO's bulk-data collection of the eCFR."""
    if not isinstance(number, int) or not 1 <= number <= 50:
        raise ManifestError(f"invalid CFR title number {number!r}")
    return f"{BASE_URL}/bulkdata/ECFR/title-{number}/ECFR-title{number}.xml"


@dataclass
class TitleSplit:
    """The parts of one title file, as envelopes. ``outside_parts`` and ``empty_parts`` are filled while iterating."""

    data: bytes
    title: int
    source_url: str
    last_modified: datetime
    file_sha256: str
    outside_parts: int = 0  # sections or appendices that sit in no part (not ingested)
    empty_parts: int = 0  # parts with no section or appendix, e.g. "[Reserved]" ranges
    part_numbers: list[str] = field(default_factory=list)

    def parts(self) -> Iterator[tuple[str, bytes]]:
        data = self.data
        stack: list[tuple[int, bytes, bytes, bytes]] = []  # (level, N, TYPE, HEAD element)
        amddate = b""
        pos = 0
        inside = 0
        while True:
            m = _TITLE_TAG.search(data, pos)
            if m is None:
                break
            if m.group(4) is not None:  # AMDDATE: it precedes the DIV1 of its volume
                amddate = m.group(4).strip()
                pos = m.end()
                continue
            closing, level, attrs = m.group(1), int(m.group(2)), m.group(3)
            if closing:
                if stack and stack[-1][0] == level:
                    stack.pop()
                pos = m.end()
                continue
            found = dict(_ATTR.findall(attrs))
            if level < 5:
                head = _HEAD.match(data, m.end())
                stack.append((level, found.get(b"N", b""), found.get(b"TYPE", b""), head.group(1) if head else b""))
                pos = m.end()
                continue
            end = data.find(b"</DIV5>", m.end())
            if end < 0:
                raise ManifestError(f"title {self.title}: unterminated DIV5")
            end += len(b"</DIV5>")
            body = data[m.start() : end]
            pos = end
            sections = body.count(b"<DIV8 ") + body.count(b"<DIV9 ")
            inside += sections
            number = found.get(b"N", b"").decode("utf-8", "replace")
            if not sections:
                self.empty_parts += 1
                continue
            if not _PART_NUMBER.match(number):
                raise ManifestError(f"title {self.title}: unexpected part number {number!r}")
            self.part_numbers.append(number)
            yield number, self._envelope(amddate, stack, body)
        self.outside_parts = data.count(b"<DIV8 ") + data.count(b"<DIV9 ") - inside

    def _envelope(self, amddate: bytes, stack: list[tuple[int, bytes, bytes, bytes]], body: bytes) -> bytes:
        attrs = " ".join(
            f"{k}={quoteattr(v)}"
            for k, v in (
                ("title-number", str(self.title)),
                ("source-url", self.source_url),
                ("last-modified", self.last_modified.astimezone(UTC).isoformat()),
                ("file-sha256", self.file_sha256),
            )
        )
        out = [f'<?xml version="1.0" encoding="UTF-8"?>\n<{ENVELOPE_TAG} {attrs}>\n'.encode(), b"<amddate>", amddate]
        out.append(b"</amddate>\n")
        for level, n, kind, head in stack:
            out.append(f'<ancestor level="{level}" n="'.encode() + n + b'" type="' + kind + b'">' + head)
            out.append(b"</ancestor>\n")
        out.append(body)
        out.append(f"\n</{ENVELOPE_TAG}>\n".encode())
        return b"".join(out)


class EcfrConnector:
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
        self.outside_parts: dict[int, int] = {}
        self.empty_parts: dict[int, int] = {}

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if manifest.get("base_url", BASE_URL).rstrip("/") != BASE_URL:
            raise ManifestError(f"base_url must be {BASE_URL}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        titles = manifest.get("titles") or []
        if not titles:
            raise ManifestError("the manifest lists no titles")
        seen: set[int] = set()
        for entry in titles:
            number = entry.get("number")
            if not isinstance(number, int) or number in seen:
                raise ManifestError(f"invalid or repeated title number {number!r}")
            seen.add(number)
            if not entry.get("reserved"):
                title_url(number)  # validates the range
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
        robots = self.http.get(f"{BASE_URL}/robots.txt", cache_key="robots.txt")
        if robots is None:
            raise FetchError(f"{BASE_URL}/robots.txt: not found; refusing to fetch without reading it")
        robots_txt = robots[0].decode("utf-8", errors="replace")
        for entry in manifest["titles"]:
            if entry.get("reserved"):
                continue  # "Title 35: Reserved" has no text and no file
            number = int(entry["number"])
            url = title_url(number)
            if not robots_allows(robots_txt, manifest["user_agent"], url):
                raise FetchError(f"{url}: disallowed by {BASE_URL}/robots.txt; not fetched")
            got = self.http.get(url)  # a refusal (403, 429 ...) raises FetchError and stops the run
            if got is None:
                self.fetch_errors.append(f"title {number}: {url} not found (404); skipped")
                continue
            data, meta = got
            if not data.lstrip().startswith(b"<?xml") or b"<ECFRBRWS" not in data[:50_000]:
                raise FetchError(
                    f"{url}: response is not an eCFR XML file ({meta.get('content_type')}); not ingested. "
                    f"Remove {self.http.cache_path(url)} after checking the site."
                )
            digest = hashlib.sha256(data).hexdigest()
            if entry.get("sha256") and entry["sha256"] != digest:
                raise ManifestError(f"title {number}: sha256 does not match the manifest pin")
            try:
                last_modified = parsedate_to_datetime(str(meta.get("last_modified")))
            except (TypeError, ValueError):
                self.fetch_errors.append(f"title {number}: no usable Last-Modified header; skipped, never dated by a guess")
                continue
            if last_modified.tzinfo is None:
                last_modified = last_modified.replace(tzinfo=UTC)
            retrieved_at = datetime.fromisoformat(meta["retrieved_at"])
            split = TitleSplit(data, number, url, last_modified, digest)
            for part, envelope in split.parts():
                yield RawDocument(
                    external_id=f"ecfr:t{number}:p{part}",
                    uri=f"{url}#part-{part}",
                    media_type="application/xml",
                    content=envelope,
                    retrieved_at=retrieved_at,
                    metadata={"title": number, "part": part, "last_modified": last_modified.isoformat()},
                )
            self.empty_parts[number] = split.empty_parts
            if split.outside_parts:
                self.outside_parts[number] = split.outside_parts
                LOG.warning("eCFR title %s: %s sections/appendices outside any part were not ingested", number, split.outside_parts)
