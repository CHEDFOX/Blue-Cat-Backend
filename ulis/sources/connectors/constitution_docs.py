"""Polite fetcher for constitutions published as a web page or a PDF file, one manifest per publisher.

Every publisher (a parliament, a head of state, a constitutional court, an electoral commission, a government
printer) has its own site, so each gets its own source descriptor and manifest (``config/manifests/<source>.yaml``);
the connector and the parser (``ulis.sources.parsers.constitution_text``) are shared.

For every document in the manifest the connector

1. reads the host's ``robots.txt`` once (a missing file, 404, allows everything; a page that is HTML instead of a
   robots file is treated as missing; any other refusal or error stops that host - nothing from it is fetched), honours
   its ``Crawl-delay`` when it is longer than the manifest's, and refuses a URL that ``robots.txt`` disallows for the
   ULIS user agent (longest-match rule of RFC 9309);
2. downloads the URL with :class:`PoliteHttp` (identifying User-Agent, at least 5 s between requests, HTTPS to the
   hosts the manifest names only, TLS verified, cache under ``<cache_dir>/<host>/constitutions/<slug>.<ext>``);
3. checks that it got what the manifest says (a PDF starts with ``%PDF-``; an HTML page was not redirected to another
   page or host) and yields a *bundle*: the exact bytes received (base64) together with the manifest entry and the
   retrieval time, which the parser reads. A document that cannot be fetched or checked is appended to
   ``fetch_errors`` and the run goes on; a refusal (401/403/451) of the document stops the run for that host.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import http.client
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
from ulis.sources.parsers.constitution_text import BUNDLE_SCHEMA, bundle_bytes

__all__ = ["ConstitutionDocsConnector", "crawl_delay"]

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
JURISDICTION = re.compile(r"^[A-Z]{2,3}$")
LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")
HOST = re.compile(r"^[a-z0-9.-]+$")
# manifest keys copied to the bundle; ``url`` and ``format`` are handled apart
DOCUMENT_KEYS = (
    "slug",
    "jurisdiction",
    "language",
    "title",
    "citation_name",
    "adopted_on",
    "edition_date",
    "edition_label",
    "events",
    "authoritative",
    "text_origin",
    "publisher",
    "structure",
    "note",
)


def crawl_delay(robots_txt: str, user_agent: str) -> float | None:
    """The ``Crawl-delay`` that applies to the agent's product token or, failing that, to ``*`` (seconds)."""
    token = user_agent.split("/")[0].strip().lower()
    groups: list[tuple[list[str], float | None]] = []
    agents: list[str] = []
    delay: float | None = None
    started = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if started:
                groups.append((agents, delay))
                agents, delay, started = [], None, False
            agents.append(value.lower())
        elif key == "crawl-delay" and agents:
            started = True
            try:
                delay = float(value)
            except ValueError:
                delay = None
        elif agents:
            started = True
    if agents:
        groups.append((agents, delay))
    for wanted in (token, "*"):
        for names, found in groups:
            if wanted in names and found is not None:
                return found
    return None


class ConstitutionDocsConnector:
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
        self._robots: dict[str, str | None] = {}

    # ------------------------------------------------------------------ manifest

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        hosts = manifest.get("hosts") or []
        if not hosts or any(not HOST.match(str(h)) for h in hosts):
            raise ManifestError("the manifest must list the hosts it fetches from (lower-case host names)")
        documents = manifest.get("documents") or []
        if not documents:
            raise ManifestError("the manifest lists no documents")
        seen: set[str] = set()
        for doc in documents:
            slug = str(doc.get("slug", ""))
            if not SLUG.match(slug) or slug in seen:
                raise ManifestError(f"invalid or duplicate slug {slug!r}")
            seen.add(slug)
            if not JURISDICTION.match(str(doc.get("jurisdiction", ""))):
                raise ManifestError(f"{slug}: invalid jurisdiction {doc.get('jurisdiction')!r}")
            if not LANGUAGE.match(str(doc.get("language", ""))):
                raise ManifestError(f"{slug}: invalid language {doc.get('language')!r}")
            if doc.get("format") not in ("pdf", "html"):
                raise ManifestError(f"{slug}: format must be pdf or html")
            if not doc.get("title") or not doc.get("citation_name"):
                raise ManifestError(f"{slug}: title and citation_name are required")
            parts = urllib.parse.urlsplit(str(doc.get("url", "")))
            if parts.scheme != "https" or parts.netloc not in hosts:
                raise ManifestError(f"{slug}: url must be https on one of {hosts}")
            doc_parts = doc.get("parts")
            if doc_parts is not None:
                if doc["format"] != "html" or not isinstance(doc_parts, list) or not doc_parts:
                    raise ManifestError(f"{slug}: parts must be a non-empty list and only for html documents")
                for part in doc_parts:
                    pu = urllib.parse.urlsplit(str(part.get("url", "")) if isinstance(part, dict) else "")
                    if pu.scheme != "https" or pu.netloc not in hosts:
                        raise ManifestError(f"{slug}: every part needs an https url on one of {hosts}")
            for key in ("adopted_on", "edition_date"):
                if doc.get(key) is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(doc[key])):
                    raise ManifestError(f"{slug}: {key} must be an ISO date")
        return manifest

    # ------------------------------------------------------------------ fetching

    def _host_rules(self, host: str, manifest: dict[str, Any]) -> str | None:
        """The text of the host's robots.txt, or None when there is none. Raises FetchError when the host refuses."""
        assert self.http is not None
        if host in self._robots:
            return self._robots[host]
        got = self.http.get(f"https://{host}/robots.txt", cache_key="robots.txt")
        text: str | None = None
        if got is not None:
            data, meta = got
            ctype = (meta.get("content_type") or "").lower()
            body = data.decode("utf-8", errors="replace")
            # a page that is not a robots file (HTML) means the site has no robots.txt
            is_page = "html" in ctype or body.lstrip()[:15].lower().startswith(("<!doctype", "<html"))
            text = None if is_page else body
        self._robots[host] = text
        return text

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        hosts = {str(h) for h in manifest["hosts"]}
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts=hosts,
            crawl_delay=float(manifest.get("crawl_delay_seconds", 5)),
            offline=self.offline,
            sleep=self._sleep,
        )
        base_delay = float(manifest.get("crawl_delay_seconds", 5))
        stopped: set[str] = set()
        for doc in manifest["documents"]:
            slug, url = str(doc["slug"]), str(doc["url"])
            host = urllib.parse.urlsplit(url).netloc
            if host in stopped:
                self.fetch_errors.append(f"{slug}: not fetched, {host} refused an earlier request")
                continue
            try:
                robots = self._host_rules(host, manifest)
                if robots is not None:
                    wanted = crawl_delay(robots, manifest["user_agent"])
                    self.http.crawl_delay = max(base_delay, wanted or 0.0)
                else:
                    self.http.crawl_delay = base_delay
                ext = "pdf" if doc["format"] == "pdf" else "html"
                wanted_parts = doc.get("parts") or [{"url": url}]
                fetched: list[tuple[bytes, dict[str, Any], str | None]] = []
                problem = None
                for number, part in enumerate(wanted_parts, start=1):
                    part_url = str(part["url"])
                    if robots is not None and not robots_allows(robots, manifest["user_agent"], part_url):
                        problem = f"{part_url} is disallowed by https://{host}/robots.txt; not fetched"
                        break
                    key = f"constitutions/{slug}.{ext}"
                    if len(wanted_parts) > 1:
                        key = f"constitutions/{slug}-{number}.{ext}"
                    got = self.http.get(part_url, cache_key=key)
                    if got is None:
                        problem = f"{part_url} not found (404)"
                        break
                    data, meta = got
                    if data[:2] == b"\x1f\x8b":  # a server that sends gzip although none was asked for
                        with contextlib.suppress(OSError, EOFError):
                            data = gzip.decompress(data)
                    problem = self._check(doc, part_url, data, meta)
                    if problem:
                        break
                    fetched.append((data, meta, part.get("heading")))
            except FetchError as exc:
                self.fetch_errors.append(f"{slug}: {exc}")
                if re.search(r"HTTP (401|403|451)\b", str(exc)):
                    stopped.add(host)
                continue
            except (OSError, http.client.HTTPException) as exc:  # e.g. a host that closes the connection unanswered
                self.fetch_errors.append(f"{slug}: {url}: {type(exc).__name__}: {exc}")
                continue
            if problem:
                self.fetch_errors.append(f"{slug}: {problem}")
                continue
            data, meta, _ = fetched[0]
            bundle: dict[str, Any] = {
                "schema": BUNDLE_SCHEMA,
                "document": {k: doc[k] for k in DOCUMENT_KEYS if k in doc},
                "url": url,
                "final_url": meta.get("final_url") or url,
                "media": doc["format"],
                "retrieved_at": max(m["retrieved_at"] for _, m, _ in fetched),
                "body_b64": base64.b64encode(data).decode("ascii"),
            }
            if doc.get("parts"):
                bundle["parts"] = [
                    {
                        "url": str(p["url"]),
                        "final_url": m.get("final_url") or str(p["url"]),
                        "heading": h,
                        "body_b64": base64.b64encode(d).decode("ascii"),
                    }
                    for p, (d, m, h) in zip(doc["parts"], fetched, strict=True)
                ]
            yield RawDocument(
                external_id=f"constitution:{slug}",
                uri=url if doc.get("parts") else (meta.get("final_url") or url),
                media_type="application/json",
                content=bundle_bytes(bundle),
                retrieved_at=datetime.fromisoformat(str(bundle["retrieved_at"])),
                metadata={"slug": slug, "jurisdiction": doc["jurisdiction"], "format": doc["format"]},
            )

    @staticmethod
    def _check(doc: dict[str, Any], url: str, data: bytes, meta: dict[str, Any]) -> str | None:
        final = urllib.parse.urlsplit(meta.get("final_url") or url)
        wanted = urllib.parse.urlsplit(url)
        if final.netloc != wanted.netloc:
            return f"redirected to another host ({meta.get('final_url')}); not ingested"
        if doc["format"] == "pdf":
            if not data.startswith(b"%PDF-"):
                return f"not a PDF ({meta.get('content_type')}, final URL {meta.get('final_url')}); not ingested"
        else:
            if data.startswith(b"%PDF-"):
                return "a PDF, but the manifest says html; not ingested"
            same = final.path.rstrip("/") == wanted.path.rstrip("/")
            if not same and not doc.get("allow_redirect"):
                return f"redirected to {meta.get('final_url')}; not ingested (set allow_redirect after checking)"
        return None
