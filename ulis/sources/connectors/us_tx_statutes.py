"""Polite fetcher for the Texas statutes (Texas Legislative Council), one zip of HTML chapter files per code.

The "Download Statutes" page of https://statutes.capitol.texas.gov offers, for each code, a zip of its chapters in
HTML (listed in the site's own ``assets/StatuteCodeDownloads.json``); the files are served from
``https://tcss.legis.texas.gov/resources/Zips/<CODE>.htm.zip``. Only that file host is contacted (the website
itself is a single-page application, not needed). Neither host publishes a robots.txt (404 or the application's
HTML shell); the connector asks for it anyway and applies what it finds.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, ClassVar

from ulis.sources.connectors.us_state_http import ManifestError, StateHttpConnector, Target

HOST = "tcss.legis.texas.gov"
CODE = re.compile(r"^[A-Z][A-Z0-9]$")


def zip_url(code: str) -> str:
    if not CODE.match(code):
        raise ManifestError(f"invalid Texas code abbreviation {code!r}")
    return f"https://{HOST}/resources/Zips/{code}.htm.zip"


class TxStatutesConnector(StateHttpConnector):
    state: ClassVar[str] = "TX"
    external_prefix: ClassVar[str] = "us-tx-statutes"
    hosts: ClassVar[frozenset[str]] = frozenset({HOST})

    def validate_manifest(self, manifest: dict[str, Any]) -> None:
        codes = manifest.get("codes") or []
        if not codes:
            raise ManifestError("the manifest lists no codes")
        seen: set[str] = set()
        for entry in codes:
            code = str(entry.get("code", ""))
            zip_url(code)
            if code in seen or not entry.get("name"):
                raise ManifestError(f"repeated code or missing name: {code!r}")
            seen.add(code)

    def targets(self, manifest: dict[str, Any]) -> Iterator[Target]:
        for entry in manifest["codes"]:
            yield Target(str(entry["code"]), zip_url(str(entry["code"])), sha256=entry.get("sha256"))

    def check(self, target: Target, data: bytes, meta: dict[str, Any]) -> str | None:
        if not data.startswith(b"PK\x03\x04"):
            return f"response is not a zip archive ({meta.get('content_type')})"
        return None
