"""Polite fetcher for the Revised Code of Washington (RCW), one chapter page at a time.

The Washington State Legislature publishes the RCW on https://app.leg.wa.gov/rcw/. A chapter's full text
(every section with its source credit) is one page::

    https://app.leg.wa.gov/rcw/default.aspx?cite=9A.32&full=true

The manifest lists the chapters (taken from the Office of the Code Reviser's 2026 RCW Archive directory
https://lawfilesext.leg.wa.gov/law/RCWArchive/2026/, which names every chapter). app.leg.wa.gov publishes no
robots.txt (it answers 404); the connector still asks for it before anything else and applies what it finds.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, ClassVar

from ulis.sources.connectors.us_state_http import ManifestError, StateHttpConnector, Target

HOST = "app.leg.wa.gov"
CHAPTER = re.compile(r"^[0-9]{1,2}[A-Z]{0,2}\.[0-9]{1,3}[A-Z]{0,2}$")


def chapter_url(chapter: str) -> str:
    if not CHAPTER.match(chapter):
        raise ManifestError(f"invalid RCW chapter {chapter!r}")
    return f"https://{HOST}/rcw/default.aspx?cite={chapter}&full=true"


class WaRcwConnector(StateHttpConnector):
    state: ClassVar[str] = "WA"
    external_prefix: ClassVar[str] = "us-wa-rcw"
    hosts: ClassVar[frozenset[str]] = frozenset({HOST})

    def validate_manifest(self, manifest: dict[str, Any]) -> None:
        chapters = manifest.get("chapters") or []
        if not chapters:
            raise ManifestError("the manifest lists no chapters")
        if len(set(chapters)) != len(chapters):
            raise ManifestError("the manifest repeats a chapter")
        for chapter in chapters:
            chapter_url(str(chapter))

    def targets(self, manifest: dict[str, Any]) -> Iterator[Target]:
        for chapter in manifest["chapters"]:
            yield Target(str(chapter), chapter_url(str(chapter)))

    def check(self, target: Target, data: bytes, meta: dict[str, Any]) -> str | None:
        if b"id='contentWrapper'" not in data or f"Chapter {target.key} RCW".encode() not in data:
            return f"not the page of chapter {target.key} ({meta.get('content_type')})"
        return None
