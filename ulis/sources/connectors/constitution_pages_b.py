"""Polite fetcher for constitution documents published by parliaments, presidents, courts and other official bodies.

One source descriptor covers ONE publisher (one organisation, one host or a few hosts it names in the manifest);
every publisher has its own robots.txt and terms, which the person adding the source reads and quotes in the source
YAML before any document is listed. The connector then fetches exactly the pages the manifest lists and nothing else.

Rules built in:

* identifying User-Agent with contact details, at least 5 s between requests to a host (the robots ``Crawl-delay`` if
  larger), back-off on 429/5xx, cache under ``<cache_dir>/<host>/`` (all of that is :class:`PoliteHttp`);
* HTTPS only, only to the hosts the manifest names;
* the host's ``robots.txt`` is read first (once per host) and every path is checked against the rules that apply to
  this crawler (a group naming ``ulis-research-bot``, else ``User-agent: *``; ``Allow``/``Disallow`` by longest match);
  a robots.txt that refuses the request (401/403) or disallows the whole site stops that host: the connector never
  fetches a document of it and never works around the refusal;
* a document that is missing (404) or disallowed is reported in ``fetch_errors`` and the run goes on; any other
  refusal stops the run.

Manifest (``config/manifests/<source id>.yaml``)::

    schema_version: 1
    source_id: xx-publisher
    user_agent: "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
    crawl_delay_seconds: 5
    hosts: [www.example.org]
    documents:
      - id: xx-constitution-en            # unique, becomes the external id
        country: XX                       # ISO 3166-1 alpha-2 (or XK for Kosovo)
        language: en                      # BCP 47 language of the text
        url: https://www.example.org/constitution.pdf
        format: pdf                       # html | pdf | text
        title: Constitution of Xland      # the document's own title
        status_note: "..."                # optional: the publisher's words on the status of the text
        parser_hints: {...}               # optional, see ulis/sources/parsers/constitution_pages_b.py
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from datetime import datetime
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.parsers.constitution_pages_b import HINT_KEYS, bundle_bytes

AGENT_TOKEN = "ulis-research-bot"  # noqa: S105 - the crawler's name in robots.txt, not a secret
MIN_DELAY = 5.0
FORMATS = {"html": "text/html", "pdf": "application/pdf", "text": "text/plain"}
DOC_KEYS = frozenset({"id", "country", "language", "url", "format", "title", "status_note", "parser_hints", "note"})
DOC_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,80}")
COUNTRY = re.compile(r"[A-Z]{2}")
LANGUAGE = re.compile(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})*")


class RobotsRefusal(FetchError):
    """The host's robots.txt refuses this crawler (or could not be obtained): no document of the host is fetched."""


class RobotsPolicy:
    """The rules of a robots.txt that apply to this crawler (RFC 9309 style: longest match wins, ``Allow`` on a tie)."""

    def __init__(self, text: str, agent_token: str = AGENT_TOKEN):
        specific: list[tuple[bool, str]] = []
        generic: list[tuple[bool, str]] = []
        specific_delay: float | None = None
        generic_delay: float | None = None
        agents: list[str] = []
        in_rules = False
        found_specific = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            field, _, value = line.partition(":")
            field, value = field.strip().lower(), value.strip()
            if field == "user-agent":
                if in_rules:
                    agents, in_rules = [], False
                agents.append(value.lower())
                continue
            if field not in ("allow", "disallow", "crawl-delay"):
                continue
            in_rules = True
            is_specific = any(a != "*" and a in agent_token for a in agents)
            is_generic = "*" in agents
            found_specific = found_specific or is_specific
            if field == "crawl-delay":
                try:
                    delay = float(value)
                except ValueError:
                    continue
                if is_specific:
                    specific_delay = delay
                elif is_generic:
                    generic_delay = delay
            elif value:
                if is_specific:
                    specific.append((field == "allow", value))
                elif is_generic:
                    generic.append((field == "allow", value))
        rules = specific if found_specific else generic
        self.crawl_delay = specific_delay if found_specific else generic_delay
        self._rules = [(allow, len(pattern), self._compile(pattern)) for allow, pattern in rules]

    @staticmethod
    def _compile(pattern: str) -> re.Pattern[str]:
        body = re.escape(pattern).replace(r"\*", ".*")
        return re.compile("^" + (body[:-2] + "$" if body.endswith(r"\$") else body))

    def allowed(self, path: str) -> bool:
        best_len, best_allow = -1, True
        for allow, length, rule in self._rules:
            if rule.match(path) and (length > best_len or (length == best_len and allow)):
                best_len, best_allow = length, allow
        return best_allow

    @property
    def closes_site(self) -> bool:
        return not self.allowed("/")


def _is_robots_file(data: bytes) -> bool:
    """A 200 answer that is an HTML page (a "soft 404") is not a robots.txt."""
    head = data[:400].lstrip().lower()
    return not (head.startswith(b"<!doctype html") or head.startswith(b"<html"))


def load_manifest(path: Path, expected_source_id: str) -> dict[str, Any]:
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ManifestError("the manifest is not a mapping")
    if manifest.get("schema_version") != 1 or manifest.get("source_id") != expected_source_id:
        raise ManifestError("manifest schema or source_id mismatch")
    if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
        raise ManifestError("an identifying user_agent with contact details is required")
    if float(manifest.get("crawl_delay_seconds", MIN_DELAY)) < MIN_DELAY:
        raise ManifestError(f"crawl_delay_seconds must be at least {MIN_DELAY:g}")
    hosts = manifest.get("hosts")
    if not isinstance(hosts, list) or not hosts or not all(isinstance(h, str) and h for h in hosts):
        raise ManifestError("the manifest must name the hosts it fetches from")
    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ManifestError("the manifest lists no documents")
    seen: set[str] = set()
    for doc in documents:
        doc_id = str(doc.get("id", ""))
        if not DOC_ID.fullmatch(doc_id):
            raise ManifestError(f"invalid document id {doc.get('id')!r}")
        if doc_id in seen:
            raise ManifestError(f"{doc_id} is listed twice")
        seen.add(doc_id)
        if not COUNTRY.fullmatch(str(doc.get("country", ""))):
            raise ManifestError(f"{doc_id}: country must be a two-letter code")
        if not LANGUAGE.fullmatch(str(doc.get("language", ""))):
            raise ManifestError(f"{doc_id}: invalid language {doc.get('language')!r}")
        if doc.get("format") not in FORMATS:
            raise ManifestError(f"{doc_id}: format must be one of {sorted(FORMATS)}")
        parts = urlsplit(str(doc.get("url", "")))
        if parts.scheme != "https" or parts.netloc not in hosts:
            raise ManifestError(f"{doc_id}: the url must be https on one of {hosts}")
        if not doc.get("title"):
            raise ManifestError(f"{doc_id}: the document's own title is required")
        unknown = set(doc) - DOC_KEYS
        if unknown:
            raise ManifestError(f"{doc_id}: unknown keys {sorted(unknown)}")
        bad_hints = set(doc.get("parser_hints") or {}) - HINT_KEYS
        if bad_hints:
            raise ManifestError(f"{doc_id}: unknown parser hints {sorted(bad_hints)}")
    return manifest


class ConstitutionPagesBConnector:
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
        self.requests_made = 0
        self._sessions: dict[str, tuple[PoliteHttp, RobotsPolicy]] = {}

    def _session(self, manifest: dict[str, Any], host: str) -> tuple[PoliteHttp, RobotsPolicy]:
        if host in self._sessions:
            return self._sessions[host]
        client = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={host},
            crawl_delay=float(manifest.get("crawl_delay_seconds", MIN_DELAY)),
            offline=self.offline,
            sleep=self._sleep,
        )
        url = f"https://{host}/robots.txt"
        try:
            got = client.get(url)
        except (FetchError, OSError, HTTPException) as exc:
            raise RobotsRefusal(f"{url}: {exc}; the host's rules could not be read, so nothing is fetched") from exc
        if got is None or not _is_robots_file(got[0]):
            policy = RobotsPolicy("")  # no robots.txt (404 or not a robots file): no rules to apply
        else:
            policy = RobotsPolicy(got[0].decode("utf-8", "replace"))
        if policy.closes_site:
            raise RobotsRefusal(f"https://{host}/robots.txt disallows this crawler; nothing is fetched from {host}")
        if policy.crawl_delay and policy.crawl_delay > client.crawl_delay:
            client.crawl_delay = policy.crawl_delay
        self._sessions[host] = (client, policy)
        return self._sessions[host]

    def fetch(self) -> Iterator[RawDocument]:
        manifest = load_manifest(self.manifest_path, self.expected_source_id)
        self.fetch_errors = []
        self._sessions = {}
        for doc in manifest["documents"]:
            doc_id = str(doc["id"])
            parts = urlsplit(str(doc["url"]))
            path = parts.path + (f"?{parts.query}" if parts.query else "")
            try:
                client, policy = self._session(manifest, parts.netloc)
            except RobotsRefusal as exc:
                self.fetch_errors.append(f"{doc_id}: {exc}")
                continue
            if not policy.allowed(path):
                self.fetch_errors.append(f"{doc_id}: {doc['url']} is disallowed by robots.txt; not fetched")
                continue
            try:
                got = client.get(str(doc["url"]), cache_key=f"doc/{doc_id}")
            except (OSError, HTTPException) as exc:  # a dropped connection: this document only
                self.fetch_errors.append(f"{doc_id}: {doc['url']}: {exc}")
                continue
            self.requests_made = sum(h.requests_made for h, _ in self._sessions.values())
            if got is None:
                self.fetch_errors.append(f"{doc_id}: {doc['url']}: not found (404)")
                continue
            data, meta = got
            expected = doc["format"]
            if expected == "pdf" and not data.startswith(b"%PDF-"):
                self.fetch_errors.append(f"{doc_id}: {doc['url']}: not a PDF file")
                continue
            if expected != "pdf" and not data.strip():
                self.fetch_errors.append(f"{doc_id}: {doc['url']}: empty response")
                continue
            retrieved = datetime.fromisoformat(meta["retrieved_at"])
            yield RawDocument(
                external_id=doc_id,
                uri=str(doc["url"]),
                media_type="application/json",
                content=bundle_bytes(
                    document_id=doc_id,
                    country=str(doc["country"]),
                    language=str(doc["language"]),
                    title=str(doc["title"]),
                    url=str(doc["url"]),
                    fmt=expected,
                    retrieved_on=retrieved.date().isoformat(),
                    content_type=meta.get("content_type"),
                    data=data,
                    status_note=doc.get("status_note"),
                    hints=doc.get("parser_hints") or {},
                ),
                retrieved_at=retrieved,
                metadata={
                    "format": expected,
                    "country": doc["country"],
                    "language": doc["language"],
                    "cached_fetch": meta,
                },
            )
