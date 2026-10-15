"""Polite fetchers for sub-national legislation websites (Canadian provinces, Australian states).

One manifest-driven connector family. A manifest (``config/manifests/<source id>.yaml``) names a host
and lists the documents to fetch; a subclass says where one document lives on that host and how to
recognise it. Documents are yielded exactly as received.

Built on :class:`PoliteHttp`: identifying User-Agent with contact details, at least 5 s between
requests (more when the site's robots.txt asks for a longer ``Crawl-delay``), HTTPS to the one host
named in the manifest, a cache under ``<cache_dir>/<host>/...`` so runs can be repeated offline.

Robots rules are enforced here, because ``PoliteHttp`` does not parse robots.txt:

* the host's ``/robots.txt`` is read first (one cached request). A missing file (404, or an HTML error
  page served in its place) means no restrictions; any refusal (401/403/429...) stops the run, as
  ``PoliteHttp`` always does;
* every document path is checked against the ``Disallow`` rules of the ``User-agent: *`` group and a
  disallowed path is refused, not fetched. A site whose robots.txt disallows everything therefore
  yields nothing (those sources stay ``planned``; see ``config/sources``).

Robustness: a document that is not found (404) or that does not look like the expected document is recorded in
``fetch_errors`` and the run goes on. A page that looks like a bot challenge or an access-denied page stops the run at
once; three unrecognisable documents in a row stop it too: the site is then challenging the client and ULIS never
works around a challenge.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.likumi_lv import robots_disallowed
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

MIN_DELAY = 5.0
MAX_CONSECUTIVE_UNRECOGNISED = 3
# Words that only appear on a bot-challenge or access-denied page: the first one stops the run (a refusal is respected).
CHALLENGE_MARKERS = (
    b"captcha",
    b"perfdrive",
    b"__uzdbm",
    b"challenge-platform",
    b"cf-chl",
    b"verify you are human",
    b"are you a robot",
    b"incapsula",
    b"access denied",
    b"request unsuccessful",
)


def looks_like_challenge(data: bytes) -> bool:
    head = data[:20000].lower()
    return any(marker in head for marker in CHALLENGE_MARKERS)


def robots_crawl_delay(robots: str) -> float | None:
    """The ``Crawl-delay`` of the ``User-agent: *`` group, if any."""
    agents: list[str] = []
    in_rules = False
    for raw in robots.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(value)
        elif field == "crawl-delay":
            in_rules = True
            if "*" in agents:
                try:
                    return float(value)
                except ValueError:
                    return None
        elif field in ("allow", "disallow"):
            in_rules = True
    return None


def looks_like_robots(data: bytes) -> bool:
    """Whether a response is a robots.txt (and not, say, an HTML error page served in its place)."""
    text = data[:4096].decode("utf-8", "replace").lower()
    return "user-agent" in text and "<html" not in text


class SubnationalConnector:
    """Base class: subclasses set the class attributes and implement :meth:`path` and :meth:`recognise`."""

    HOSTS: ClassVar[frozenset[str]]
    ID_RE: ClassVar[re.Pattern[str]]
    MEDIA_TYPE: ClassVar[str]
    EXTERNAL_PREFIX: ClassVar[str]

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

    # -- subclass hooks -------------------------------------------------------------------
    def path(self, doc_id: str) -> str:
        raise NotImplementedError

    def recognise(self, doc_id: str, data: bytes) -> bool:
        raise NotImplementedError

    # -- manifest -------------------------------------------------------------------------
    def manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if manifest.get("host") not in self.HOSTS:
            raise ManifestError(f"host must be one of {sorted(self.HOSTS)}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        docs = manifest.get("documents") or []
        if not docs:
            raise ManifestError("the manifest lists no documents")
        seen: set[str] = set()
        for doc in docs:
            doc_id = str(doc.get("id", ""))
            if not self.ID_RE.fullmatch(doc_id):
                raise ManifestError(f"invalid document id {doc_id!r}")
            if doc_id in seen:
                raise ManifestError(f"duplicate document id {doc_id!r}")
            seen.add(doc_id)
        return manifest

    # -- fetching -------------------------------------------------------------------------
    def fetch(self) -> Iterator[RawDocument]:
        manifest = self.manifest()
        host = manifest["host"]
        base = f"https://{host}"
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={host},
            crawl_delay=max(float(manifest.get("crawl_delay_seconds", MIN_DELAY)), MIN_DELAY),
            offline=self.offline,
            sleep=self._sleep,
        )
        rules: list[re.Pattern[str]] = []
        got = self.http.get(f"{base}/robots.txt")  # a refusal raises FetchError and stops the run
        if got is not None and looks_like_robots(got[0]):
            robots = got[0].decode("utf-8", "replace")
            rules = robots_disallowed(robots)
            delay = robots_crawl_delay(robots)
            if delay is not None:
                self.http.crawl_delay = max(self.http.crawl_delay, delay)
        unrecognised = 0
        for doc in manifest["documents"]:
            doc_id = str(doc["id"])
            path = self.path(doc_id)
            if any(rule.match(path) for rule in rules):
                raise FetchError(f"{base}{path} is disallowed by robots.txt; not fetched")
            url = f"{base}{path}"
            got = self.http.get(url)
            if got is None:
                self.fetch_errors.append(f"{self.EXTERNAL_PREFIX}:{doc_id}: {url}: not found (404)")
                continue
            data, meta = got
            if not self.recognise(doc_id, data):
                if looks_like_challenge(data):
                    raise FetchError(
                        f"{url}: the site answered with a bot-challenge or access-denied page. Stopping at once "
                        "(ULIS never works around a challenge); try again later or ask the publisher."
                    )
                unrecognised += 1
                self.fetch_errors.append(
                    f"{self.EXTERNAL_PREFIX}:{doc_id}: {url}: not the expected document "
                    f"({meta.get('content_type')}, {len(data)} bytes); not ingested. "
                    f"Remove {self.http.cache_path(url)} after checking the site."
                )
                if unrecognised >= MAX_CONSECUTIVE_UNRECOGNISED:
                    raise FetchError(
                        f"{host}: {unrecognised} documents in a row were not the expected documents; the site may be "
                        "challenging automated clients. Stopping (ULIS never works around a challenge)."
                    )
                continue
            unrecognised = 0
            yield RawDocument(
                external_id=f"{self.EXTERNAL_PREFIX}:{doc_id}",
                uri=meta.get("final_url") or url,
                media_type=self.MEDIA_TYPE,
                content=data,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"document_id": doc_id, "title": doc.get("title"), "cached_fetch": meta},
            )


def _head(data: bytes, n: int = 6000) -> str:
    return data[:n].decode("utf-8", "replace")


class QueenslandLegislationConnector(SubnationalConnector):
    """Queensland Legislation (Office of the Queensland Parliamentary Counsel): the XML of an Act's current reprint.

    ``https://www.legislation.qld.gov.au/view/xml/inforce/current/<act-YYYY-NNN>`` (the link the Act's own
    page gives under "XML"). ``robots.txt`` disallows ``/view/pdf/``, ``/view/xml/compare`` and
    ``/view/html/compare`` only.
    """

    HOSTS = frozenset({"www.legislation.qld.gov.au"})
    ID_RE = re.compile(r"act-[0-9]{4}-[0-9]{3}")
    MEDIA_TYPE = "application/xml"
    EXTERNAL_PREFIX = "qld-legislation"

    def path(self, doc_id: str) -> str:
        return f"/view/xml/inforce/current/{doc_id}"

    def recognise(self, doc_id: str, data: bytes) -> bool:
        head = _head(data)
        return "<act " in head and f'id="{doc_id}"' in head and "QuILLS" in head


class TasmanianLegislationConnector(SubnationalConnector):
    """Tasmanian Legislation Online (Office of Parliamentary Counsel): the XML of an Act's current version.

    ``https://www.legislation.tas.gov.au/view/xml/inforce/current/<act-YYYY-NNN>``. ``robots.txt``
    disallows ``/admin/`` only.
    """

    HOSTS = frozenset({"www.legislation.tas.gov.au"})
    ID_RE = re.compile(r"act-[0-9]{4}-[0-9]{3}[A-Za-z]?")
    MEDIA_TYPE = "application/xml"
    EXTERNAL_PREFIX = "tas-legislation"

    def path(self, doc_id: str) -> str:
        return f"/view/xml/inforce/current/{doc_id}"

    def recognise(self, doc_id: str, data: bytes) -> bool:
        head = _head(data)
        return "<ACT " in head and f'ID="{doc_id}"' in head


class OntarioELawsConnector(SubnationalConnector):
    """Ontario e-Laws (https://www.ontario.ca/laws): the HTML page of a statute's current consolidation.

    ``https://www.ontario.ca/laws/statute/<id>`` (for example ``90c43``). robots.txt (Crawl-delay 1 s,
    ULIS keeps 5 s) does not disallow ``/laws/``. The site sits behind a bot manager that can answer with
    a challenge page; such a page is not a statute and the run stops after three of them in a row.
    """

    HOSTS = frozenset({"www.ontario.ca"})
    ID_RE = re.compile(r"[0-9]{2}[a-z][0-9]{2}[a-z]?")
    MEDIA_TYPE = "text/html"
    EXTERNAL_PREFIX = "ontario-e-laws"

    def path(self, doc_id: str) -> str:
        return f"/laws/statute/{doc_id}"

    def recognise(self, doc_id: str, data: bytes) -> bool:
        text = data.decode("utf-8", "replace")
        return 'class="shorttitle' in text and "ConsolidationPeriod" in text


class ManitobaLawsConnector(SubnationalConnector):
    """Manitoba Laws (https://web2.gov.mb.ca/laws): the HTML page of a C.C.S.M. Act's current consolidation.

    ``https://web2.gov.mb.ca/laws/statutes/ccsm/<id>.php?lang=en`` (for example ``h175``). The host has no
    robots.txt (404 answered by an HTML error page), so no restriction applies.
    """

    HOSTS = frozenset({"web2.gov.mb.ca"})
    ID_RE = re.compile(r"[a-z][0-9]{1,3}[a-z]?")
    MEDIA_TYPE = "text/html"
    EXTERNAL_PREFIX = "manitoba-laws"

    def path(self, doc_id: str) -> str:
        return f"/laws/statutes/ccsm/{doc_id}.php?lang=en"

    def recognise(self, doc_id: str, data: bytes) -> bool:
        text = data.decode("utf-8", "replace")
        return 'class="secnum"' in text or 'class="secheading"' in text
