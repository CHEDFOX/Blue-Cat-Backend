"""Polite fetcher for the Oregon Revised Statutes (ORS), one chapter file at a time.

The Oregon Legislature publishes each ORS chapter as one HTML file::

    https://www.oregonlegislature.gov/bills_laws/ors/ors163.html        (chapter 163; ``ors285A.html`` for 285A)

The publisher gives no list of the chapter files: its Table of Titles and Chapters
(https://www.oregonlegislature.gov/bills_laws/BillsLawsEDL/ORS_TitlesChapters.pdf) gives the chapter ranges of
each title. The manifest therefore lists every chapter number in those ranges as a *candidate*; a candidate
that does not exist (404) is recorded in ``not_found`` and is not an error. robots.txt of the host only names
one Microsoft search robot (an empty Disallow); the connector applies it to every URL regardless.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, ClassVar

from ulis.sources.connectors.us_state_http import ManifestError, StateHttpConnector, Target

HOST = "www.oregonlegislature.gov"
CHAPTER = re.compile(r"^[0-9]{1,3}[A-Z]?$")


def chapter_url(chapter: str) -> str:
    if not CHAPTER.match(chapter):
        raise ManifestError(f"invalid ORS chapter {chapter!r}")
    digits = re.match(r"^[0-9]+", chapter)
    assert digits is not None
    return f"https://{HOST}/bills_laws/ors/ors{digits.group(0).zfill(3)}{chapter[len(digits.group(0)) :]}.html"


class OrOrsConnector(StateHttpConnector):
    state: ClassVar[str] = "OR"
    external_prefix: ClassVar[str] = "us-or-ors"
    hosts: ClassVar[frozenset[str]] = frozenset({HOST})

    def validate_manifest(self, manifest: dict[str, Any]) -> None:
        chapters = [str(c) for c in manifest.get("chapters") or []]
        if not chapters:
            raise ManifestError("the manifest lists no chapters")
        if len(set(chapters)) != len(chapters):
            raise ManifestError("the manifest repeats a chapter")
        for chapter in chapters:
            chapter_url(chapter)

    def targets(self, manifest: dict[str, Any]) -> Iterator[Target]:
        for chapter in manifest["chapters"]:
            yield Target(str(chapter), chapter_url(str(chapter)), candidate=True)

    def check(self, target: Target, data: bytes, meta: dict[str, Any]) -> str | None:
        # The file is Word-exported HTML: "Chapter" and the number may be separated by a line break.
        if b"Oregon Revised Statutes" not in data or not re.search(rb"Chapter\s+" + target.key.encode() + rb"\b", data):
            return f"not the file of ORS chapter {target.key} ({meta.get('content_type')})"
        return None
