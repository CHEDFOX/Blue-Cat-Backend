"""Polite fetcher for Lilex (https://www.gesetze.li), the consolidated law of the Principality of Liechtenstein.

Lilex is run by the Rechtsdienst der Regierung. Every consolidated law has a page ``/konso/<LGBl-Nr>``
(for example ``/konso/1921.015``) with a list of its versions - "Fassungen", each with the dates it applied
- and, in an iframe, the text of the selected version at ``/konso/html/<id>?version=<n>``.

For every law in the manifest the connector reads the law page (one request), picks the versions the
manifest asks for (``current``: the version in force now; ``all``: every version Lilex lists, oldest first)
and fetches each version's text (one request each). It yields one raw document per version: a small JSON
*bundle* holding the text exactly as received (``html``) together with the version number and the dates
printed in the version list, because the text itself does not state the dates of the version.

Built on :class:`PoliteHttp`: identifying User-Agent, 5 s between requests, HTTPS to ``www.gesetze.li`` only,
cache under ``<cache_dir>/www.gesetze.li/``, refusals respected. The host publishes no ``robots.txt`` (404); the
connector still checks every path against it should one appear. A law that is missing or whose page is not a
Lilex law page is reported in ``fetch_errors`` and the run goes on; a refusal stops it.

Manifest (``config/manifests/li-lilex.yaml``)::

    schema_version: 1
    source_id: li-lilex
    base_url: https://www.gesetze.li
    user_agent: "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
    crawl_delay_seconds: 5
    default_versions: current        # or: all
    laws:
      - lgbl: "1921.015"             # LGBl-Nr as printed (year.number)
        title: ...                   # optional, documentation only
        versions: all                # optional override of default_versions
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.site_session import PageNotAvailable, SiteSession, load_manifest
from ulis.sources.parsers.legal_html import HtmlParseError, parse_html

HOST = "www.gesetze.li"
BASE_URL = f"https://{HOST}"
BUNDLE_SCHEMA = "ulis-li-lilex-bundle/1"
LGBL = re.compile(r"[12][0-9]{3}\.[0-9]{1,3}")
VERSION_MODES = ("current", "all")
OPTION = re.compile(r'<option(?P<sel>\s+selected="selected")?\s+value="(?P<n>[0-9]+)"\s*>(?P<label>[^<]+)</option>')
LABEL = re.compile(r"(?P<a>\d{2}\.\d{2}\.\d{4})(?:\s*-\s*(?P<b>\d{2}\.\d{2}\.\d{4}))?")


def bundle_bytes(bundle: dict[str, Any]) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def law_page_info(page: bytes, lgbl: str) -> tuple[str, list[tuple[int, str]]]:
    """From a law page: the internal id (``1921015000``) and its versions as ``(number, printed dates)``, oldest first.

    Raises ``HtmlParseError`` when the page is not the page of this law.
    """
    text = page.decode("utf-8", "replace")
    root = parse_html(page)
    chrono = root.find(
        lambda n: n.tag == "a" and n.attrs.get("href", "").startswith("/chrono/") and "target" in n.attrs
    )
    chrono_id = (chrono.attrs["href"].rsplit("/", 1)[-1]) if chrono is not None else ""
    if not re.fullmatch(r"[0-9]{9,10}", chrono_id):
        raise HtmlParseError(f"{lgbl}: not a Lilex law page (no LGBl link)")
    year, _, number = lgbl.partition(".")
    if chrono_id != f"{year}{int(number):03d}000":
        raise HtmlParseError(f"{lgbl}: the page belongs to {chrono_id}, not to this law")
    versions = [(int(m["n"]), m["label"].strip()) for m in OPTION.finditer(text)]
    if not versions:
        raise HtmlParseError(f"{lgbl}: no version list on the law page")
    versions.sort()
    return chrono_id, versions


class LilexConnector:
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
        self.session: SiteSession | None = None
        self.fetch_errors: list[str] = []

    @property
    def http(self):  # type: ignore[no-untyped-def]
        return self.session.http if self.session else None

    def _manifest(self) -> dict[str, Any]:
        manifest = load_manifest(self.manifest_path, self.expected_source_id, BASE_URL, "laws")
        if manifest.get("default_versions", "current") not in VERSION_MODES:
            raise ManifestError(f"default_versions must be one of {VERSION_MODES}")
        seen: set[str] = set()
        for law in manifest["laws"]:
            lgbl = str(law.get("lgbl", ""))
            if not LGBL.fullmatch(lgbl):
                raise ManifestError(f"invalid LGBl-Nr {law.get('lgbl')!r}")
            if lgbl in seen:
                raise ManifestError(f"LGBl-Nr {lgbl} is listed twice")
            seen.add(lgbl)
            if law.get("versions", "current") not in VERSION_MODES:
                raise ManifestError(f"{lgbl}: versions must be one of {VERSION_MODES}")
        return manifest

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self._manifest()
        self.fetch_errors = []
        self.session = SiteSession(
            manifest, HOST, self.cache_dir, offline=self.offline, sleep=self._sleep, robots_required=False
        )
        default = manifest.get("default_versions", "current")
        for law in manifest["laws"]:
            lgbl = str(law["lgbl"])
            try:
                page, page_meta = self.session.get(f"/konso/{lgbl}")
                chrono_id, versions = law_page_info(page, lgbl)
                wanted = versions if law.get("versions", default) == "all" else versions[-1:]
                for number, label in wanted:
                    path = f"/konso/html/{chrono_id}?version={number}"
                    data, meta = self.session.get(path)
                    if b'class="art"' not in data and b'class="abs"' not in data and b'class="htit1"' not in data:
                        raise HtmlParseError(f"{lgbl} version {number}: not a Lilex law text")
                    yield RawDocument(
                        external_id=f"li-lilex:{lgbl}@v{number}",
                        uri=f"{BASE_URL}{path}",
                        media_type="application/json",
                        content=bundle_bytes(
                            {
                                "schema": BUNDLE_SCHEMA,
                                "lgbl": lgbl,
                                "chrono_id": chrono_id,
                                "version": number,
                                "dates": label,
                                "law_page_url": f"{BASE_URL}/konso/{lgbl}",
                                "version_url": f"{BASE_URL}{path}",
                                "html": data.decode("utf-8"),
                            }
                        ),
                        retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
                        metadata={
                            "lgbl": lgbl,
                            "version": number,
                            "dates": label,
                            "title": law.get("title"),
                            "cached_fetch": meta,
                            "law_page_fetch": page_meta,
                        },
                    )
            except (PageNotAvailable, HtmlParseError, UnicodeDecodeError) as exc:
                self.fetch_errors.append(f"li-lilex:{lgbl}: {exc}")
