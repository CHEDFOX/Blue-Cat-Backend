"""Polite fetcher for the Utah Code (Office of Legislative Research and General Counsel), one XML file per title.

https://le.utah.gov/xcode/code.html names the current index file (``versionDefault``), and that index lists every
title with the version string of its current file::

    https://le.utah.gov/xcode/Title76/C76_2026050620260701.xml

The file name changes when the title changes, so the connector reads the index (two small requests) to find the
current name of each title in the manifest. The title XML carries the section texts, the legislature's history
lines and, for sections with a future version, ``effdate`` / ``enddate`` elements. le.utah.gov's robots.txt asks
for ``Crawl-delay: 10`` and disallows a few application paths; the connector waits 10 s and checks every URL.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, ClassVar

from ulis.sources.connectors.us_state_http import FetchError, ManifestError, StateHttpConnector, Target

HOST = "le.utah.gov"
BASE = f"https://{HOST}/xcode"
TITLE = re.compile(r"^[0-9]{1,2}[A-Z]?$")
VERSION_DEFAULT = re.compile(r'var versionDefault\s*=\s*"(C_[0-9]+)"')
INDEX_ENTRY = re.compile(r'href="Title([0-9A-Za-z]+)/([0-9A-Za-z]+)\.html\?v=(C[0-9A-Za-z]+_[0-9]+)"')


def parse_index_version(code_html: str) -> str:
    m = VERSION_DEFAULT.search(code_html)
    if m is None:
        raise FetchError(f"{BASE}/code.html: no current index file (versionDefault) found")
    return m.group(1)


def parse_title_versions(index_html: str) -> dict[str, str]:
    """``{title: version string}`` from the index file, e.g. ``{"76": "C76_2026050620260701"}``."""
    out: dict[str, str] = {}
    for title, again, version in INDEX_ENTRY.findall(index_html):
        if title == again and version.startswith(f"C{title}_"):
            out[title] = version
    return out


def title_url(title: str, version: str) -> str:
    if not TITLE.match(title) or not version.startswith(f"C{title}_"):
        raise ManifestError(f"invalid Utah Code title {title!r} or version {version!r}")
    return f"{BASE}/Title{title}/{version}.xml"


class UtCodeConnector(StateHttpConnector):
    state: ClassVar[str] = "UT"
    external_prefix: ClassVar[str] = "us-ut-code"
    hosts: ClassVar[frozenset[str]] = frozenset({HOST})
    default_delay: ClassVar[float] = 10.0

    def validate_manifest(self, manifest: dict[str, Any]) -> None:
        titles = [str(t) for t in manifest.get("titles") or []]
        if not titles:
            raise ManifestError("the manifest lists no titles")
        if len(set(titles)) != len(titles):
            raise ManifestError("the manifest repeats a title")
        for title in titles:
            if not TITLE.match(title):
                raise ManifestError(f"invalid Utah Code title {title!r}")

    def _page(self, url: str, user_agent: str) -> str:
        got = self.get(url, user_agent)
        if got is None:
            raise FetchError(f"{url}: not found (404)")
        return got[0].decode("utf-8", errors="replace")

    def targets(self, manifest: dict[str, Any]) -> Iterator[Target]:
        agent = manifest["user_agent"]
        index_name = parse_index_version(self._page(f"{BASE}/code.html", agent))
        versions = parse_title_versions(self._page(f"{BASE}/{index_name}.html", agent))
        if not versions:
            raise FetchError(f"{BASE}/{index_name}.html: no titles found in the index")
        for title in manifest["titles"]:
            version = versions.get(str(title))
            if version is None:
                self.fetch_errors.append(f"{self.external_prefix}:{title}: title not listed in the current index")
                continue
            yield Target(str(title), title_url(str(title), version))

    def check(self, target: Target, data: bytes, meta: dict[str, Any]) -> str | None:
        if not data.lstrip().startswith(f'<title number="{target.key}"'.encode()):
            return f"not the XML of title {target.key} ({meta.get('content_type')})"
        return None
