"""Parser for the Oregon Revised Statutes (ORS): one chapter file of https://www.oregonlegislature.gov/bills_laws/ors/.

Input: the bundle (see :mod:`ulis.sources.us_state_bundle`) of ``ors<NNN>.html``. The files are Word-exported
HTML in Windows-1252. Format notes:

* the first lines give ``Chapter 163 — Offenses Against Persons`` and ``2025 EDITION``. Chapters that the
  2025 special session or the 2026 regular session affect carry notices between the two ("New sections of law
  were added by legislative action to this ORS chapter ..."): they are recorded as the document's outstanding
  changes (not linked to a provision) and are not law text. A list of the chapter's sections follows (not parsed).
* a section starts with a paragraph whose first run is bold: the section number and, for a section in force,
  its caption (``163.005 Criminal homicide.``); the text follows in the same paragraph and in the paragraphs
  after it, up to the next section. The source credit is the last bracket of the section
  (``[1971 c.743 §87; 2007 c.867 §4]``) and goes in the note; the editorial ``Note:`` paragraphs that follow
  some sections (and any session-law text printed in them) are not part of the section's text and go in the note.
* a number whose paragraph is only a bracket (``163.010 [Amended by 1963 c.625 §4; repealed by 1971 c.743 §432]``,
  ``[... renumbered 163.109 in 2019]``) is a repealed or renumbered section: recorded as repealed with no text.
* upper-case lines between sections (``HOMICIDE``) are group headings, not law text.
* no start dates: the edition is "2025 EDITION"; the document is dated by its retrieval date and attested as of that
  date (``validity_basis = "attested_at_edition"``). The publisher states that the text in the database is not the
  official text (the printed ORS is); annotations are a separate publication and are not ingested.
"""

from __future__ import annotations

import re
from typing import Any

from ulis.sources.normalized import ParsedDocument
from ulis.sources.parsers.us_state_common import (
    Node,
    SectionDraft,
    StateParseError,
    build_provisions,
    parse_html,
    raw_text,
    state_document,
    ws,
)
from ulis.sources.us_state_bundle import read_bundle

PARSER_ID = "us_or_ors_html"
PARSER_VERSION = "1.0.0"
JURISDICTION = "US-OR"
MODULE = "us_or"
START = re.compile(r"^(\d{1,3}[A-Z]?\.\d{3,4}[A-Z]?)(?![\d.])")
TITLE_LINE = re.compile(r"^Chapter ([0-9]{1,3}[A-Z]?) — (.+)$")
EDITION_LINE = re.compile(r"^(\d{4}) EDITION$", re.IGNORECASE)
CREDIT = re.compile(r"\s*(\[(?:\d{4}|Formerly|Amended|Repealed|Renumbered|Enacted)[^\[\]]*\])\s*$")
GROUP_HEADING = re.compile(r"^[A-Z][A-Z0-9 ,;:'’&()/.-]{3,}$")
NOTE = re.compile(r"^Notes?\b", re.IGNORECASE)


def work_uri(chapter: str) -> str:
    return f"/us-or/ors/{chapter}"


def _decode(data: bytes) -> str:
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _leading_bold(p: Node) -> str | None:
    """Text of the paragraph's first run when that run is bold and the paragraph starts with it."""
    first = p.find(lambda n: n.tag == "b" and bool(ws(raw_text(n))))
    if first is None:
        return None
    whole, bold = ws(raw_text(p)), ws(raw_text(first))
    return bold if whole.startswith(bold) else None


def _items(body: Node) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for p in body.find_all(lambda n: n.tag == "p"):
        text = ws(raw_text(p, br=" "))
        if not text:
            continue
        bold = _leading_bold(p)
        m = START.match(bold) if bold else None
        if bold and m:
            heading = bold[m.end() :].strip() or None
            out.append(("start", (m.group(1), heading, text[len(bold) :].strip())))
        else:
            out.append(("para", text))
    return out


def parse_or_ors(content: bytes) -> list[ParsedDocument]:
    bundle = read_bundle(content, "OR")
    chapter = bundle.key
    body = parse_html(_decode(bundle.body_bytes))
    items = _items(body)
    head = next(
        (m for kind, v in items if kind == "para" and (m := TITLE_LINE.match(v)) and m.group(1) == chapter), None
    )
    if head is None:
        raise StateParseError(f"not the file of ORS chapter {chapter}")
    name = head.group(2)
    edition: int | None = None
    notices: list[str] = []
    seen_title = False
    for kind, v in items:
        if kind == "start":
            break
        if kind == "para" and TITLE_LINE.match(v) and v == head.group(0):
            seen_title = True
        elif seen_title and edition is None:
            m = EDITION_LINE.match(v)
            if m:
                edition = int(m.group(1))
            else:
                notices.append(v)
    context = f"Chapter {chapter} — {name}"

    drafts: list[SectionDraft] = []
    current: SectionDraft | None = None
    in_note = False
    note_lines: list[str] = []

    def close() -> None:
        nonlocal current, in_note, note_lines
        if current is None:
            return
        if note_lines:
            current.notes.append("Source note: " + " ".join(note_lines))
        lines = current.lines
        if lines:
            m = CREDIT.search(lines[-1])
            if m:
                current.notes.insert(0, f"Source credit: {m.group(1)}")
                lines[-1] = lines[-1][: m.start()].rstrip()
                if not lines[-1]:
                    lines.pop()
        if current.heading is None and (not lines or (len(lines) == 1 and lines[0].startswith("["))):
            # "163.010 [Amended by 1963 c.625 §4; repealed by 1971 c.743 §432]" / "[... renumbered 163.109 in 2019]":
            # a repealed or renumbered section; what the source prints in its place is kept in the note.
            if lines:
                current.notes.insert(0, f"Printed in the source: {lines[0]}")
            current.repealed = True
            current.lines = []
        drafts.append(current)
        current, in_note, note_lines = None, False, []

    for i, (kind, v) in enumerate(items):
        if kind == "start":
            close()
            number, heading, rest = v
            current = SectionDraft(
                kind="section", number=number, heading=heading, lines=[rest] if rest else [], context=context
            )
            continue
        if current is None:
            continue
        nxt = items[i + 1] if i + 1 < len(items) else None
        if GROUP_HEADING.match(v) and (nxt is None or nxt[0] == "start") and not in_note:
            close()  # a group heading ("HOMICIDE") between two sections
            continue
        if in_note or NOTE.match(v):
            in_note = True
            note_lines.append(v)
        else:
            current.lines.append(v)
    close()
    provisions = build_provisions(drafts, JURISDICTION)
    doc = state_document(
        bundle,
        jurisdiction=JURISDICTION,
        module=MODULE,
        work_uri=work_uri(chapter),
        work_title=f"ORS chapter {chapter} - {name}",
        title=f"Oregon Revised Statutes, Chapter {chapter} - {name}" + (f" ({edition} Edition)" if edition else ""),
        short_title=f"ORS ch. {chapter}",
        number=chapter,
        ext={"ors_chapter": chapter, **({"edition_year": edition} if edition else {})},
        provisions=provisions,
        edition=f"ors-{edition}-edition" if edition else "ors-online",
        text_origin="official_conversion",
    )
    doc.outstanding_changes = [
        {"description": n, "by_title": "Notice printed by the Legislative Counsel at the head of the chapter"}
        for n in notices
    ]
    return [doc]


__all__ = ["PARSER_ID", "PARSER_VERSION", "parse_or_ors", "work_uri"]
