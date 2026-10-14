"""Polite fetcher for Likumi.lv (https://likumi.lv), Latvian legislation of the official publisher "Latvijas Vēstnesis".

For every document in the manifest it downloads ``https://likumi.lv/ta/id/<id>`` (the page the site
itself gives as the act's address) and yields the HTML exactly as received; the parser reads it.

Built on :class:`PoliteHttp`: identifying User-Agent, 5 s between requests (robots.txt asks for a
1 s Crawl-delay), HTTPS to this one host only, refusals respected, and a cache under
``<cache_dir>/likumi.lv/ta/id/<id>`` so runs can be repeated offline. Likumi.lv's robots.txt is
unusual: besides asset directories it disallows ``/*/redakcijas-datums/`` and ``&version_date=``
(pages of earlier editions) and a list of individual document ids. The connector therefore reads
robots.txt (one cached request) and refuses any manifest document it disallows; earlier editions are
never requested.
"""

from __future__ import annotations

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

HOST = "likumi.lv"
BASE_URL = f"https://{HOST}"


def robots_disallowed(robots: str) -> list[re.Pattern[str]]:
    """The ``Disallow`` rules of the ``User-agent: *`` group as regular expressions.

    Supports the ``*`` wildcard and the ``$`` end anchor that Likumi.lv's robots.txt uses (Python's
    ``urllib.robotparser`` implements neither). Rules are prefix matches of path plus query.
    """
    rules: list[re.Pattern[str]] = []
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
        elif field == "disallow":
            in_rules = True
            if "*" in agents and value:
                pattern = re.escape(value).replace(r"\*", ".*")
                rules.append(re.compile("^" + (pattern[:-2] + "$" if pattern.endswith(r"\$") else pattern)))
        elif field in ("allow", "crawl-delay"):
            in_rules = True
    return rules


class LikumiLvConnector:
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

    def _manifest(self) -> dict[str, Any]:
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("source_id") != self.expected_source_id:
            raise ManifestError("manifest schema or source_id mismatch")
        if manifest.get("base_url", BASE_URL).rstrip("/") != BASE_URL:
            raise ManifestError(f"base_url must be {BASE_URL}")
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        laws = manifest.get("laws") or []
        if not laws:
            raise ManifestError("the manifest lists no documents")
        for law in laws:
            if not re.fullmatch(r"[1-9][0-9]{0,8}", str(law.get("id", ""))):
                raise ManifestError(f"invalid document id {law.get('id')!r}")
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
        got = self.http.get(f"{BASE_URL}/robots.txt")
        if got is None:
            raise FetchError(f"{BASE_URL}/robots.txt: not found; refusing to fetch without reading it")
        disallowed = robots_disallowed(got[0].decode("utf-8", "replace"))
        for law in manifest["laws"]:
            doc_id = str(law["id"])
            path = f"/ta/id/{doc_id}"
            if any(rule.match(path) for rule in disallowed):
                raise FetchError(f"{BASE_URL}{path} is disallowed by robots.txt; not fetched")
            url = f"{BASE_URL}{path}"
            got = self.http.get(url)
            if got is None:
                raise FetchError(f"{url}: not found (404)")
            data, meta = got
            marker = f'<div id="doc_id">{doc_id}</div>'.encode()
            if b'class="doc-body"' not in data or marker not in data:
                raise FetchError(
                    f"{url}: not a Likumi.lv document page for id {doc_id} ({meta.get('content_type')}); "
                    f"not ingested. Remove {self.http.cache_path(url)} after checking the site."
                )
            yield RawDocument(
                external_id=f"likumi-lv:{doc_id}",
                uri=meta.get("final_url") or url,
                media_type="text/html",
                content=data,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"likumi_id": doc_id, "title": law.get("title"), "cached_fetch": meta},
            )
