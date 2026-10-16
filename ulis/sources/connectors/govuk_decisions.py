"""Fetches tribunal decisions from GOV.UK's Content API (Employment Appeal Tribunal, Upper Tribunal AAC).

``https://www.gov.uk/api/content/<path>`` returns the JSON of a GOV.UK page; for a tribunal decision it holds the
title, the
decision date, the PDF attachment and the PDF's extracted text (``details.metadata.hidden_indexable_content``). The
decisions to
fetch are listed in the manifest (``config/manifests/gb-govuk-tribunal-decisions.yaml``, built from the Search API by
``scripts/build_govuk_decisions_manifest.py``); each response is passed on exactly as received.

Terms: GOV.UK content is Crown copyright published under the Open Government Licence v3.0 and made available "through
feeds for
other websites and applications to use" (https://www.gov.uk/help/terms-conditions, read 2026-10-16); robots.txt
(https://www.gov.uk/robots.txt) disallows only ``/*/print$`` and ``/search/all*``. See ``config/sources/gb-govuk-
tribunal-decisions.yaml``.

``PoliteHttp``: identifying User-Agent, at least 5 s between requests, HTTPS to ``www.gov.uk`` only, back-off on
429/5xx, any
other refusal stops the run, cache under ``<cache_dir>/www.gov.uk/``. A decision that is missing (404), is not JSON,
or is not a
tribunal decision of the listed collection is reported in ``fetch_errors`` and skipped; the run goes on.
"""

from __future__ import annotations

import json
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

HOST = "www.gov.uk"
BASE_URL = f"https://{HOST}"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
MIN_DELAY = 5.0
# finder path -> GOV.UK document type
COLLECTIONS = {
    "employment-appeal-tribunal-decisions": "employment_appeal_tribunal_decision",
    "administrative-appeals-tribunal-decisions": "utaac_decision",
}
PATH = re.compile(r"^(" + "|".join(COLLECTIONS) + r")/([a-z0-9][a-z0-9-]{2,400})$")

__all__ = ["COLLECTIONS", "FetchError", "GovUkDecisionsConnector", "content_url"]


def content_url(path: str) -> str:
    if not PATH.match(path):
        raise ManifestError(f"invalid decision path {path!r} (expected <collection>/<page-name>)")
    return f"{BASE_URL}/api/content/{path}"


class GovUkDecisionsConnector:
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
        for path in decisions:
            content_url(str(path))  # validates the pattern
            if path in seen:
                raise ManifestError(f"{path} is listed twice")
            seen.add(path)
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
        for path in manifest["decisions"]:
            url = content_url(path)
            if not robots_allows(robots, self.http.user_agent, url):
                raise FetchError(f"{url}: disallowed by {ROBOTS_URL}; not fetched")
            got = self.http.get(url, cache_key=f"api/content/{path}.json")
            if got is None:
                self.fetch_errors.append(f"{url}: not found (404); skipped")
                continue
            content, meta = got
            try:
                page = json.loads(content)
            except ValueError:
                self.fetch_errors.append(f"{url}: not JSON; skipped")
                continue
            collection = path.split("/", 1)[0]
            if page.get("base_path") != f"/{path}" or page.get("document_type") != COLLECTIONS[collection]:
                self.fetch_errors.append(f"{url}: not the {COLLECTIONS[collection]} page {path}; skipped")
                continue
            yield RawDocument(
                external_id=f"govuk:{path}",
                uri=f"{BASE_URL}/{path}",
                media_type="application/json",
                content=content,
                retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                metadata={"path": path, "cached_fetch": meta},
            )
