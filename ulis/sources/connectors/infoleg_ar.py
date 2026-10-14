"""Polite fetcher for InfoLEG, Argentine national legislation ("Leyes Argentinas", https://www.argentina.gob.ar/normativa).

For every norm in the manifest it downloads two pages of ``www.argentina.gob.ar``, both linked from the
site itself:

1. the norm's summary page ``/normativa/nacional/norma-<id>`` (number, organ, dates of sanction and
   publication, Boletín Oficial number and page), whose links show which texts exist;
2. the text page it links to: ``/normativa/nacional/norma-<id>/actualizacion`` (the "texto actualizado")
   or ``/normativa/nacional/norma-<id>/texto`` (the "texto original"), as the manifest asks (``text:``).

It yields one *bundle* per norm (JSON, format ``ulis-infoleg-bundle/1``): both pages exactly as received
(decoded UTF-8), with their URLs and retrieval times. The pages state no date of update, so the bundle is
what lets the parser attest the text at its retrieval date. The bundle is the stored raw document.

Built on :class:`PoliteHttp`: identifying User-Agent, a delay between requests (the larger of the manifest's
and robots.txt's ``Crawl-delay``, which is 10 s), HTTPS to this one host only, refusals respected, cache under
``<cache_dir>/www.argentina.gob.ar/normativa/nacional/norma-<id>/``. robots.txt is read first (one cached
request) and every path is checked against its ``Disallow`` rules (``*?tca=*``, ``/*.pdf$``, directories, and
``/sites/default/files/infoleg/``, where InfoLEG keeps its file downloads: never requested).
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
from ulis.sources.connectors.likumi_lv import robots_disallowed
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.parsers.infoleg_ar import BUNDLE_FORMAT

HOST = "www.argentina.gob.ar"
BASE_URL = f"https://{HOST}"
TEXT_PATHS = {"actualizado": "actualizacion", "original": "texto"}


def robots_crawl_delay(robots: str) -> float | None:
    """``Crawl-delay`` of the ``User-agent: *`` group, if any."""
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


class InfolegArConnector:
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
            if law.get("text") not in TEXT_PATHS:
                raise ManifestError(f"document {law.get('id')}: text must be one of {sorted(TEXT_PATHS)}")
        return manifest

    def _get(self, path: str, cache_key: str, disallowed: list[re.Pattern[str]]) -> tuple[str, dict[str, Any]]:
        if any(rule.match(path) for rule in disallowed):
            raise FetchError(f"{BASE_URL}{path} is disallowed by robots.txt; not fetched")
        assert self.http is not None
        url = f"{BASE_URL}{path}"
        got = self.http.get(url, cache_key=cache_key)
        if got is None:
            raise FetchError(f"{url}: not found (404)")
        data, meta = got
        try:
            return data.decode("utf-8"), meta
        except UnicodeDecodeError as exc:
            raise FetchError(f"{url}: the page is not UTF-8") from exc

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.http = PoliteHttp(
            self.cache_dir,
            manifest["user_agent"],
            allowed_hosts={HOST},
            crawl_delay=float(manifest.get("crawl_delay_seconds", 10)),
            offline=self.offline,
            sleep=self._sleep,
        )
        got = self.http.get(f"{BASE_URL}/robots.txt")
        if got is None:
            raise FetchError(f"{BASE_URL}/robots.txt: not found; refusing to fetch without reading it")
        robots = got[0].decode("utf-8", "replace")
        disallowed = robots_disallowed(robots)
        self.http.crawl_delay = max(self.http.crawl_delay, robots_crawl_delay(robots) or 0.0)
        for law in manifest["laws"]:
            norm_id = int(law["id"])
            kind = law["text"]
            base = f"/normativa/nacional/norma-{norm_id}"
            summary_key = f"normativa/nacional/norma-{norm_id}/resumen.html"
            summary, summary_meta = self._get(base, summary_key, disallowed)
            if f'href="{BASE_URL}{base}"' not in summary or '<dl class="normativa">' not in summary:
                raise FetchError(
                    f"{BASE_URL}{base}: not an InfoLEG summary page for norm {norm_id}; not ingested. "
                    f"Remove {self.http.cache_path(BASE_URL + base, summary_key)} after checking the site."
                )
            text_path = f"{base}/{TEXT_PATHS[kind]}"
            if f'href="{text_path}"' not in summary:
                raise FetchError(
                    f"{BASE_URL}{base}: the page does not link to {text_path} (no {kind} text offered); "
                    "set the manifest's text: for this norm accordingly"
                )
            text_key = f"normativa/nacional/norma-{norm_id}/{TEXT_PATHS[kind]}.html"
            text, text_meta = self._get(text_path, text_key, disallowed)
            if f'class="infoleg-norma-body infoleg-mode-{kind}"' not in text:
                raise FetchError(f"{BASE_URL}{text_path}: no {kind} text found in the page; not ingested")
            bundle = {
                "format": BUNDLE_FORMAT,
                "infoleg_id": norm_id,
                "text_kind": kind,
                "pages": {
                    "summary": _page(summary, summary_meta),
                    "text": _page(text, text_meta),
                },
            }
            yield RawDocument(
                external_id=f"infoleg-ar:{norm_id}:{kind}",
                uri=f"{BASE_URL}{text_path}",
                media_type="application/json",
                content=json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                retrieved_at=datetime.fromisoformat(text_meta["retrieved_at"]),
                metadata={"infoleg_id": norm_id, "text_kind": kind, "title": law.get("title")},
            )


def _page(html: str, meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": meta.get("final_url") or meta["url"],
        "retrieved_at": meta["retrieved_at"],
        "content_type": meta.get("content_type"),
        "html": html,
    }
