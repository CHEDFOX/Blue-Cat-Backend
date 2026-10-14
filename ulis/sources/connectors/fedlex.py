"""Fetches Swiss federal law (SR consolidations, Akoma Ntoso XML) from Fedlex.

The manifest (``config/manifests/ch-fedlex.yaml``, built by ``scripts/build_fedlex_manifest.py``
from the Fedlex SPARQL endpoint) lists, for each work, the consolidations to fetch with their
date of applicability and the XML file of each language version.

Access rules (see ``config/sources/ch-fedlex.yaml``):

* the linked data names files under ``https://fedlex.data.admin.ch/filestore/``, a path that
  host's robots.txt disallows. The Fedlex website maps that prefix to
  ``https://www.fedlex.admin.ch/filestore/`` (its ``api/assets/app-config.json``
  ``publicMapping``), whose robots.txt allows everything except print views. Files are fetched
  only from that public mapping, never from the disallowed path;
* PoliteHttp: identifying User-Agent, 5 s between requests (no Crawl-delay is published),
  back-off on 429/5xx, any other refusal stops the run, cache under ``var/sources/``;
* consolidations are yielded per work in chronological order, so each later consolidation is
  compared with the versions already recorded.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
SPARQL_ENDPOINT = "https://fedlex.data.admin.ch/sparqlendpoint"
DATA_FILESTORE = "https://fedlex.data.admin.ch/filestore/"
PUBLIC_FILESTORE = "https://www.fedlex.admin.ch/filestore/"
LANGUAGES = ("de", "fr", "it")
_ELI = re.compile(r"^https://fedlex\.data\.admin\.ch/eli/cc/[0-9A-Za-z]+/[0-9a-z_]+$")
_FILE = re.compile(r"^https://fedlex\.data\.admin\.ch/filestore/fedlex\.data\.admin\.ch/eli/cc/[0-9A-Za-z/_.-]+\.xml$")

__all__ = ["FetchError", "FedlexConnector", "public_url"]


def public_url(data_url: str) -> str:
    """Map a linked-data file URL to the website's public filestore URL (same file)."""
    if not _FILE.match(data_url):
        raise ManifestError(f"unexpected Fedlex file URL {data_url!r}")
    return PUBLIC_FILESTORE + data_url.removeprefix(DATA_FILESTORE)


class FedlexConnector:
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
        if not manifest.get("user_agent") or "(" not in manifest["user_agent"]:
            raise ManifestError("an identifying user_agent with contact details is required")
        for work in manifest["works"]:
            if not _ELI.match(work.get("eli", "")):
                raise ManifestError(f"invalid work {work.get('eli')!r}")
            dates = [date.fromisoformat(str(c["date"])) for c in work["consolidations"]]
            if not dates or dates != sorted(set(dates)):
                raise ManifestError(f"{work['eli']}: consolidations must be listed once each, oldest first")
            for cons in work["consolidations"]:
                if set(cons["files"]) != set(manifest.get("languages", LANGUAGES)):
                    raise ManifestError(f"{work['eli']}@{cons['date']}: a file per language is required")
                for url in cons["files"].values():
                    public_url(url)  # validates
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={"www.fedlex.admin.ch"},
            crawl_delay=float(manifest.get("crawl_delay_seconds", 5)),
            offline=self.offline,
            sleep=self._sleep,
        )
        for work in manifest["works"]:
            for cons in work["consolidations"]:
                for lang in manifest.get("languages", LANGUAGES):
                    data_url = cons["files"][lang]
                    got = self.http.get(public_url(data_url))
                    if got is None:
                        raise FetchError(f"{data_url}: not found")
                    content, meta = got
                    yield RawDocument(
                        external_id=f"fedlex:{work['eli'].removeprefix('https://fedlex.data.admin.ch/eli/')}"
                        f"@{cons['date']}:{lang}",
                        uri=meta.get("final_url") or meta["url"],
                        media_type="application/akn+xml",
                        content=content,
                        retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                        metadata={
                            "sr": work.get("sr"),
                            "eli": work["eli"],
                            "consolidation": str(cons["date"]),
                            "consolidation_end": str(cons["end"]) if cons.get("end") else None,
                            "language": lang,
                            "linked_data_file": data_url,
                            "scheduled_consolidations": [str(d) for d in work.get("scheduled", [])],
                            "cached_fetch": meta,
                        },
                    )
