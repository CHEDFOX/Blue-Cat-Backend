"""Parser for compilations and "as made" texts from the Federal Register of Legislation (Australia).

Input: the Register API's response for one document (a compilation, or the text as made) requested with
``Accept: application/json`` and ``$expand=version`` (see the connector). It carries the
EPUB (base64) and the version's dates: ``Start`` (the compilation date) and ``End`` (the day the
next version of the title starts, if any).

Format notes (Office of Parliamentary Counsel compilations; the EPUB is generated from the Word
master and keeps its paragraph styles as XHTML classes):

* ``ActHead1``-``ActHead4`` are Chapter, Part, Division and Subdivision headings (or a Schedule /
  Dictionary heading); ``ActHead5`` is a section (or schedule clause) heading whose number is in
  ``span.CharSectno``;
* ``subsection``, ``paragraph``, ``paragraphsub`` and ``paragraphsub-sub`` hold numbered text: a
  label such as ``(1)``, ``(a)``, ``(i)``, ``(A)`` (or ``1.2`` in the Australian Privacy
  Principles) between two tab spacers. A ``subsection`` without a label is text of the section
  itself; a ``paragraph`` without a label continues the open paragraph; ``subsection2`` continues
  a subsection after its paragraphs;
* ``Definition`` holds a defined term (bold italic) and its meaning;
* in-text notes and examples (``notetext``, ``notepara``, ``noteToPara``), penalties and simplified
  outlines are part of the Act (Acts Interpretation Act 1901 s 13(1)); they stay in the text of
  the provision they belong to, with their own "Note:"/"Penalty:" labels;
* everything before the first heading (cover page, "About this compilation", contents) and from
  ``ENotesHeading1`` on (the endnotes: legislation and amendment history, which are editorial) is
  never put in a provision's text.

Each document is one compilation: the text "as amended and in force" on its compilation date,
until the next version of the Act starts (the version's ``End``). The Register dates the
compilation as a whole, not individual provisions, so every provision gets the compilation's
period and the document is a whole-instrument snapshot (``validity_basis = revision_snapshot``,
see ``ulis.ingestion.snapshots``): an unchanged wording continues across adjacent compilations,
but not across a period that has no compilation.

Not parsed, and counted by :func:`parse_with_report`: text placed directly under a Part or
Division heading (outside any section), and images (formulas), which a provision's note then
mentions. Repealed provisions are omitted from compilations, so they are not recorded as repealed.

**As made** (compilation number 0, register id = title id): the only text of an Act or instrument
that was never amended. The Register dates that version from the making date (an Act: assent; an
instrument: its registration), which is not its commencement (commencement is in the Act's or
instrument's own text and is not extracted), so every provision is dated from that date with
``validity_basis = enactment_date_presumed`` and a note saying so; ``text_origin`` is
``official_conversion`` (the EPUB is the Register's own rendering of the text as made, not the
authorised PDF). In an Act as made the body starts at the assent endorsement (``AssentDt``); the
cover and contents before it, the enacting words, and the second-reading and assent notes after the
last provision (``2ndRd``, ``AssentBk``) are not law text. Unstyled recitals before the first
heading are the preamble.

**Legislative instruments** (title ids ``F2016L00123``, ``F1996B00084``, ``C2004L09583``) use the
same styles when drafted in the current template; compilations prepared by the Office of Legislative
Drafting before 2005 use ``HR`` (regulation heading), ``R1``/``R2`` (regulation, subregulation),
``Rc`` (continuation), ``P1``-``P3`` (paragraphs) and ``Note``, mapped to the current styles.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision

PARSER_ID = "frl_epub"
PARSER_VERSION = "1.1.0"
XHTML = "http://www.w3.org/1999/xhtml"
OPF = "http://www.idpf.org/2007/opf"
CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
CONSTITUTION_ID = "C2004Q00685"
WEB = "https://www.legislation.gov.au"
# The only document type declaration accepted; it is removed before parsing with DTDs forbidden.
_DOCTYPE = re.compile(
    rb'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1\.1//EN" "http://www\.w3\.org/TR/xhtml11/DTD/xhtml11\.dtd">'
)
_MAX_UNZIPPED = 200 * 1024 * 1024
_LABEL = re.compile(r"^(?:\([0-9A-Za-z]{1,12}\.?\)|\d+[A-Z]*\.\d+[A-Z]*)$")
# C...A: an Act; C...Q: the Constitution (one title); C/F...L and F...B: legislative instruments.
_TITLE_ID = re.compile(r"^([CF])\d{4}([ABLQ])\d{5}$")
_SCHEDULE = re.compile(r"^schedule\b\s*(\d+[A-Z]*)?", re.I)
_NUMBERED_HEADING = re.compile(r"^(\d+[A-Z]*(?:\.\d+[A-Z]*)*\.?) (?=\S)")
_MONTHS = {
    m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)
}
IMAGE_NOTE = "Contains a formula or image that is not reproduced in this text; see the source document."
AS_MADE_NOTE = {
    "act": "Text as made, never amended. The Register dates this version from assent ({date}); the Act commences as it "
    "provides (otherwise 28 days after assent, Acts Interpretation Act 1901 s 3A), which is not recorded here.",
    "legislative_instrument": "Text as made, never amended. The Register dates this version from its registration "
    "({date}); the instrument commences as it provides (otherwise the day after registration, Legislation Act 2003 "
    "s 12), which is not recorded here.",
}
# The enacting words of an Act as made (not a provision): "The Parliament of Australia enacts:",
# "BE IT THEREFORE enacted by the Queen, the Senate and the House of Representatives ..., as follows:".
_ENACTING = re.compile(r"^(?:The Parliament of Australia enacts:?|BE IT(?: THEREFORE)? ENACTED\b.*)$", re.I)
# Cover-page, contents and signature styles that are never law text (the body starts after them).
FRONT = {
    "ShortT", "Actno", "ShortTP1", "ActNoP1", "Page1", "AssentDt", "CompiledActNo", "SignCoverPageStart",
    "SignCoverPageEnd", "Title", "CoverStatRule", "CoverMade", "CoverAct", "CoverUpdate", "ContentsStatRule",
    "ContentsHead", "ContentsPage", "TOC",
}  # fmt: skip

# Paragraph styles (XHTML classes) by role.
NUMBERED = {"subsection": 1, "paragraph": 2, "paragraphsub": 3, "paragraphsub-sub": 4, "paragraphsub-sub-sub": 5}
# Older reprints (registered 2004 for 1997-2003) use other names for the same styles, and plain
# h1-h6 elements for the headings (h5 = section heading).
OLD_STYLES = {"Subsection": "subsection", "indenta": "paragraph", "indentii": "paragraphsub"}
# Compilations of regulations and other instruments prepared by the Office of Legislative Drafting
# (registered 2004-2005): regulation heading, regulation / subregulation, continuation, paragraphs, notes.
OLD_STYLES |= {
    "HR": "ActHead5", "R1": "subsection", "R2": "subsection", "Rc": "subsection2", "P1": "paragraph",
    "P2": "paragraphsub", "P3": "paragraphsub-sub", "definition0": "Definition", "Note": "notetext",
}  # fmt: skip
# An editorial pointer to the endnotes in older reprints' headings, e.g. "1 Short title [see Note 1]".
_SEE_NOTE = re.compile(r"\s*\[see Note \d+\]$")
SUB_KIND = {1: "subsection", 2: "paragraph", 3: "subparagraph", 4: "subsubparagraph", 5: "subsubsubparagraph"}
SKIP = {"Header", "TOC1", "TOC2", "TOC3", "TOC4", "TOC5", "TOC6", "TOC7", "TOC8", "TOC9"}
# The endnotes (editorial): "ENotesHeading1" in current compilations, "NotesSection" ("Notes to the
# ... Act"), "NoteHeading" ("Notes to the ... Regulations"), "Table of Acts" and "Table of Amendments" in older
# ones; in an Act as made, the second-reading and assent notes after the last provision. Nothing from here on is
# law text.
_ENDNOTES = re.compile(r"^(?:ENote|EndNote|Notes|NoteHeading|TableOf|TableEnotes|ActNotes|2ndRd|AssentBk)")
# Styles whose role is known; anything else is kept as text of the enclosing provision and counted.
KNOWN = {
    "LongT", "SubsectionHead", "Definition", "subsection2", "notetext", "notepara", "noteToPara", "notemargin",
    "Penalty", "BodyText", "TofSectsSection", "Formula", "Tabletext", "Tablea", "TableHeading", "Tablei", "Tableii",
    "SOText", "SOPara", "SOBullet", "SOHeadItalic", "SOHeadBold", "SOPara1", "SOBullet1", "BoxHeadBold", "BoxText",
    "BoxPara", "BoxList", "",
}  # fmt: skip


class FrlParseError(ValueError):
    pass


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9.]+", "-", ascii_text.lower()).strip("-.")[:60].strip("-.")


def label_key(label: str) -> str:
    """``(1A)`` -> ``1A``, ``(xx.)`` -> ``xx``, ``1.2`` -> ``1.2``, ``51.`` -> ``51``."""
    return re.sub(r"[^0-9A-Za-z.]", "", label.strip().strip("()")).rstrip(".")


def _day(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def written_date(text: str) -> date | None:
    """``12 July 1901`` / ``15 Mar 1995`` -> date; anything else -> None."""
    m = re.fullmatch(r"(\d{1,2}) ([A-Za-z]{3})[A-Za-z]*\.? (\d{4})", _ws(text))
    if not m or m.group(2).lower() not in _MONTHS:
        return None
    try:
        return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    except ValueError:
        return None


def instrument_type_for(title_id: str) -> str:
    """``constitution``, ``act`` or ``legislative_instrument`` from the Register's title id; unsupported ids raise."""
    m = _TITLE_ID.match(title_id)
    if title_id == CONSTITUTION_ID:
        return "constitution"
    if m and m.group(1) == "C" and m.group(2) == "A":
        return "act"
    if m and m.group(2) in ("B", "L"):
        return "legislative_instrument"
    raise FrlParseError(f"unsupported title {title_id!r}")


def work_uri_for(title_id: str) -> str:
    return f"/au/{instrument_type_for(title_id)}/{title_id}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map a Register web URL (``https://www.legislation.gov.au/C2004A03712/latest/text``) to a work URI."""
    m = re.match(r"^https://www\.legislation\.gov\.au/([CF]\d{4}[ABLQ]\d{5})(?:/[^?#]*)?$", uri)
    if not m:
        return None
    try:
        return work_uri_for(m.group(1)), None
    except FrlParseError:
        return None


# --------------------------------------------------------------------------- EPUB container


def _epub_pages(epub: bytes) -> list[bytes]:
    """The XHTML pages of an EPUB, in spine order."""
    try:
        z = zipfile.ZipFile(io.BytesIO(epub))
        if sum(i.file_size for i in z.infolist()) > _MAX_UNZIPPED:
            raise FrlParseError("EPUB expands to more than the allowed size")
        if z.read("mimetype").strip() != b"application/epub+zip":
            raise FrlParseError("EPUB mimetype missing")
        container = fromstring(z.read("META-INF/container.xml"), forbid_dtd=True)
        rootfile = next(container.iter(f"{{{CONTAINER}}}rootfile"), None)
        opf_path = rootfile.get("full-path", "") if rootfile is not None else ""
        if not opf_path:
            raise FrlParseError("EPUB container has no rootfile")
        opf = fromstring(z.read(opf_path), forbid_dtd=True)
        base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
        items = {i.get("id"): i.get("href") or "" for i in opf.iter(f"{{{OPF}}}item")}
        pages: list[bytes] = []
        for ref in opf.iter(f"{{{OPF}}}itemref"):
            href = items.get(ref.get("idref"), "")
            if not href.endswith((".html", ".xhtml", ".htm")):
                continue
            if ".." in href.split("/") or href.startswith("/"):
                raise FrlParseError(f"unsafe EPUB path {href!r}")
            pages.append(z.read(base + href))
    except (zipfile.BadZipFile, KeyError) as exc:
        raise FrlParseError(f"not a readable EPUB: {exc}") from exc
    if not pages:
        raise FrlParseError("EPUB has no XHTML pages")
    return pages


def _xhtml_body(page: bytes) -> Element:
    page = _DOCTYPE.sub(b"", page, count=1)
    try:
        root = fromstring(page, forbid_dtd=True)
    except Exception as exc:
        raise FrlParseError(f"not well-formed or unsafe XHTML: {exc}") from exc
    body = root.find(f"{{{XHTML}}}body")
    if root.tag != f"{{{XHTML}}}html" or body is None:
        raise FrlParseError("not an XHTML page")
    return body


def _blocks(el: Element) -> list[Element]:
    out: list[Element] = []
    for child in el:
        if _local(child.tag) == "div":
            out.extend(_blocks(child))
        else:
            out.append(child)
    return out


# --------------------------------------------------------------------------- text of one block


def _is_spacer(el: Element) -> bool:
    return "inline-block" in (el.get("style") or "") and not _ws("".join(el.itertext()))


def _segments(el: Element, images: list[str]) -> list[str | None]:
    """Text pieces of a block; ``None`` marks a tab spacer."""
    out: list[str | None] = [el.text or ""]
    for child in el:
        tag = _local(child.tag)
        if tag == "span" and _is_spacer(child):
            out.append(None)
        elif tag == "img":
            images.append(child.get("alt") or "")
        elif tag == "br":
            out.append(" ")
        else:
            out.extend(_segments(child, images))
        out.append(child.tail or "")
    return out


def _render(segments: list[str | None]) -> str:
    return _ws("".join(" " if s is None else s for s in segments))


def _split_label(segments: list[str | None]) -> tuple[str | None, str]:
    """``[tab](1)[tab]Text`` -> (``(1)``, ``Text``); an unnumbered block -> (None, its text)."""
    first_text = next((i for i, s in enumerate(segments) if s and _ws(s)), None)
    if first_text is None:
        return None, ""
    spacer = next((i for i in range(first_text, len(segments)) if segments[i] is None), None)
    if spacer is None:
        return None, _render(segments)
    label = _render(segments[first_text:spacer])
    if not _LABEL.match(label):
        return None, _render(segments)
    return label, _render(segments[spacer + 1 :])


def _table_lines(table: Element, images: list[str]) -> list[str]:
    rows: list[str] = []
    for tr in table.iter(f"{{{XHTML}}}tr"):
        cells = []
        for cell in tr:
            if _local(cell.tag) in ("td", "th"):
                parts = [_render(_segments(p, images)) for p in cell if _local(p.tag) in ("p", "div", "span")]
                cells.append(" ".join(p for p in parts if p) or _render(_segments(cell, images)))
        if any(cells):
            rows.append(" | ".join(cells))
    return rows


def _term(block: Element) -> str | None:
    """The defined term: bold italic in current Acts, in quotation marks in the Constitution."""
    for span in block.iter(f"{{{XHTML}}}span"):
        style = span.get("style") or ""
        if "bold" in style and "italic" in style:
            term = _ws("".join(span.itertext()))
            if term:
                return term
    m = re.match(r"^[“\"]([^”\"]+)[”\"]", _ws("".join(block.itertext())))
    return m.group(1) if m else None


# --------------------------------------------------------------------------- provision tree


@dataclass
class _Node:
    eid: str
    kind: str
    top_kind: str
    num_path: list[str]
    label: str | None  # printed label, shown before the text in the parent's composite text
    level: float
    heading: str | None = None
    parent: _Node | None = None
    items: list[str | _Node] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    context: str | None = None

    def text(self) -> str:
        lines: list[str] = []
        for item in self.items:
            if isinstance(item, _Node):
                body = item.text()
                lines.append(f"{item.label} {body}".strip() if item.label else body)
            elif item:
                lines.append(item)
        return "\n".join(line for line in lines if line)


@dataclass
class ParseReport:
    """What the parser saw but did not record as provision text (for documentation and tests)."""

    blocks_outside_provisions: Counter[str] = field(default_factory=Counter)
    images: int = 0
    unlabelled_paragraphs: int = 0
    unstyled_paragraphs: int = 0
    # Paragraph styles with no specific role in this parser (kept as text of the enclosing provision).
    other_styles: Counter[str] = field(default_factory=Counter)


class _Builder:
    def __init__(self, title_id: str, as_made: bool = False):
        self.is_constitution = title_id == CONSTITUTION_ID
        self.as_made = as_made
        self.nodes: list[_Node] = []
        self.used: dict[str, int] = {}
        self.stack: list[_Node] = []
        self.report = ParseReport()
        # Where section headings belong: "act", "covering" (the Constitution Act's own clauses),
        # "constitution", "schedule" or "code" (a schedule enacted as a code, e.g. the Criminal Code).
        self.scope = "covering" if self.is_constitution else "act"
        self.schedule_no = ""
        self.schedule_heading: str | None = None
        self.schedule_node: _Node | None = None
        self.schedule_structured = False
        self.schedule_notes: list[str] = []
        self.dictionary = False
        self.code_title: str | None = None
        self.context: str | None = None
        self.pending_heading: str | None = None
        self.note_target: _Node | None = None
        self.preamble: _Node | None = None
        self.in_preamble = False

    # -- identifiers
    def new(
        self,
        local: str,
        kind: str,
        top_kind: str,
        num_path: list[str],
        label: str | None,
        level: float,
        parent: _Node | None,
        heading: str | None = None,
    ) -> _Node:
        eid = f"{parent.eid}-{local}" if parent else local
        # The same label can occur twice in one provision (e.g. two lists of paragraphs (a), (b) in
        # a section of the Constitution); repeats are numbered in document order.
        n = self.used.get(eid, 0) + 1
        self.used[eid] = n
        eid = eid if n == 1 else f"{eid}-{n}"
        node = _Node(eid, kind, top_kind, num_path, label, level, heading, parent, context=self.context)
        if parent is not None:
            parent.items.append(node)
        self.nodes.append(node)
        return node

    # -- headings
    def close_all(self) -> None:
        self.stack.clear()
        self.note_target = None
        self.pending_heading = None
        self.in_preamble = False

    def top_kind(self) -> str:
        if self.scope == "covering":
            return "covering_clause"
        if self.scope == "code":
            return "code_dictionary" if self.dictionary else "code_section"
        if self.dictionary:
            return "dictionary"
        return "schedule" if self.scope == "schedule" else "section"

    def heading1(self, text: str) -> None:
        self.close_all()
        self.context = text
        if text.strip().rstrip(".").lower() == "dictionary":
            self.dictionary = True
            return
        m = _SCHEDULE.match(text)
        if not m:
            self._schedule_structure()
            return
        self.dictionary = False
        if text == self.schedule_heading:  # repeated at the start of each volume
            return
        self._unattached_schedule_notes()
        self.schedule_heading = text
        self.schedule_no = m.group(1) or ""
        self.schedule_node = None
        self.schedule_structured = False
        code = re.match(r"^Schedule—The (.+ Code)$", text)
        if code:
            self.scope, self.code_title = "code", code.group(1)
        else:
            self.scope = "schedule"

    def heading(self, text: str) -> None:
        """Chapter/Part/Division/Subdivision heading: context only, not a provision."""
        self.close_all()
        self.context = text
        self._schedule_structure()

    def _schedule_structure(self) -> None:
        if self.scope == "schedule":
            self.schedule_structured = True
            self._unattached_schedule_notes()

    def _unattached_schedule_notes(self) -> None:
        if self.schedule_notes:
            self.report.blocks_outside_provisions["notemargin"] += len(self.schedule_notes)
            self.schedule_notes = []

    def section(self, number: str | None, heading: str) -> None:
        self.close_all()
        if self.is_constitution and number is None and heading.rstrip(".").strip() == "The Constitution":
            self.scope = "constitution"
        self.dictionary = False
        self._schedule_structure()
        path: list[str] = []
        kind = "section"
        if self.scope == "schedule":  # schedule-1-clause-2, schedule-clause-2 (unnumbered schedule)
            prefix = f"schedule-{label_key(self.schedule_no)}-" if self.schedule_no else "schedule-"
            path, kind = [self.schedule_no], "clause"
        elif self.scope == "covering":
            prefix = "covering-clause-"
        else:
            prefix = "section-"
        if number:
            printed = number[:-1] if number.endswith(".") else number  # "51." -> "51"
            local = f"{prefix}{'clause-' if kind == 'clause' else ''}{label_key(printed)}"
            path = [*path, printed]
        else:  # an unnumbered heading, e.g. "Overview of the Australian Privacy Principles"
            local, kind = f"{prefix}heading-{slug(heading)}", "heading"
            path = [*path, f"heading:{heading}"]
        self.stack = [self.new(local, kind, self.top_kind(), path, None, 0, None, heading)]

    # -- attachment points
    def innermost(self, max_level: float) -> _Node | None:
        for node in reversed(self.stack):
            if node.level <= max_level:
                return node
        return None

    def schedule_text_node(self) -> _Node | None:
        """Text directly under a schedule heading with no clauses is the schedule's own provision."""
        if self.scope != "schedule" or self.dictionary or self.stack or self.schedule_structured:
            return None
        if self.schedule_node is None:
            local = f"schedule-{label_key(self.schedule_no)}" if self.schedule_no else "schedule"
            node = self.new(local, "schedule", "schedule", [self.schedule_no], None, 0, None, self.schedule_heading)
            node.notes.extend(self.schedule_notes)
            self.schedule_notes = []
            self.schedule_node = node
        return self.schedule_node

    def text_target(self) -> _Node | None:
        """Where subsection-level text (notes, penalties, continuation text, tables) belongs."""
        return self.innermost(1.5) or self.schedule_text_node()

    def add_text(self, node: _Node, text: str, images: list[str]) -> None:
        if text:
            node.items.append(text)
        if images:
            self.report.images += len(images)
            if IMAGE_NOTE not in node.notes:
                node.notes.append(IMAGE_NOTE)

    # -- blocks
    def block(self, block: Element, cls: str, segments: list[str | None], text: str, images: list[str]) -> None:
        if self.in_preamble:
            # Recitals and enacting words between the long title and the first heading. In a compilation,
            # unstyled paragraphs there are template placeholders ("Do not delete : Chapter placeholder"); in
            # an Act as made they are the enacting words (not a provision) or unstyled recitals (the preamble).
            if not cls and (not self.as_made or text.startswith("Do not delete")):
                self.report.blocks_outside_provisions["unstyled"] += 1
                return
            if not cls and _ENACTING.match(text):
                self.report.blocks_outside_provisions["enacting_words"] += 1
                return
            if self.preamble is None:
                self.preamble = self.new("preamble", "preamble", "preamble", [], None, 0, None)
            self.add_text(self.preamble, text, images)
            return
        if cls == "SubsectionHead":
            self.pending_heading = text
            return
        if cls in NUMBERED:
            self.numbered(NUMBERED[cls], cls, segments, text, images)
            return
        if cls == "Definition":
            self.definition(block, text, images)
            return
        target: _Node | None
        if cls == "noteToPara":
            target = self.stack[-1] if self.stack else None
        elif cls == "notepara":
            target = self.note_target
        elif cls == "notemargin":
            target = self.innermost(0)
            if target is None and self.scope == "schedule" and not self.stack and not self.schedule_structured:
                if self.schedule_node is not None:
                    self.schedule_node.notes.append(text)
                else:
                    self.schedule_notes.append(text)  # kept if the schedule turns out to have own text
                return
        else:
            target = self.text_target()
            if not cls and self.stack:
                self.report.unstyled_paragraphs += 1
        if target is None:
            self.report.blocks_outside_provisions[cls or "unstyled"] += 1
            return
        if cls in ("notetext", "noteToPara", "notemargin", "Penalty"):
            self.note_target = target
        self.add_text(target, text, images)

    def numbered(self, level: int, cls: str, segments: list[str | None], text: str, images: list[str]) -> None:
        label, body = _split_label(segments)
        if label is None:
            # Unnumbered: a "subsection" is text of the section itself; any other numbered style
            # continues the open provision at that indentation (e.g. text after subparagraphs).
            if level > 1:
                self.report.unlabelled_paragraphs += 1
            target = self.innermost(0 if level == 1 else level) or self.schedule_text_node()
            if target is None:
                self.report.blocks_outside_provisions[cls] += 1
                return
            self.stack = [n for n in self.stack if n.level <= target.level]
            self.add_text(target, text, images)
            self.pending_heading = None
            self.note_target = target
            return
        while self.stack and self.stack[-1].level >= level:
            self.stack.pop()
        parent = self.stack[-1] if self.stack else self.schedule_text_node()
        if parent is None:
            self.report.blocks_outside_provisions[cls] += 1
            return
        kind = "subclause" if level == 1 and parent.kind == "clause" else SUB_KIND[level]
        node = self.new(label_key(label), kind, parent.top_kind, [*parent.num_path, label], label, level, parent)
        if level == 1:
            node.heading = self.pending_heading
        self.pending_heading = None
        self.add_text(node, body, images)
        self.stack.append(node)
        self.note_target = node

    def definition(self, block: Element, text: str, images: list[str]) -> None:
        while self.stack and (self.stack[-1].level > 1 or self.stack[-1].kind == "definition"):
            self.stack.pop()
        term = _term(block)
        local = f"def-{slug(term)}" if term else "def"
        parent = self.stack[-1] if self.stack else None
        if parent is None:
            if not self.dictionary:
                self.report.blocks_outside_provisions["Definition"] += 1
                return
            node = self.new(f"dictionary-{local}", "definition", self.top_kind(), [f"def:{term or ''}"], None, 1, None)
        else:
            level = 1.5 if parent.level >= 1 else 1
            path = [*parent.num_path, f"def:{term or ''}"]
            node = self.new(local, "definition", parent.top_kind, path, None, level, parent)
        self.add_text(node, text, images)
        self.stack.append(node)
        self.note_target = node


def _parse_body(pages: list[Element], b: _Builder) -> None:
    # An Act as made repeats its long title on the cover and on page 1 before the assent endorsement
    # ("[Assented to 2 December 2024]"); the body starts there, and the long title is recorded once.
    starts_at_assent = b.as_made and any(
        (block.get("class") or "") == "AssentDt" for body in pages for block in _blocks(body)
    )
    long_title: str | None = None
    for body in pages:
        inside = False
        for block in _blocks(body):
            tag = _local(block.tag)
            cls = block.get("class") or ""
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and not cls:
                cls = f"ActHead{tag[1]}"
            cls = OLD_STYLES.get(cls, cls)
            if inside and _ENDNOTES.match(cls):
                break
            if not inside and starts_at_assent:
                if cls in ("LongT", "Page1"):
                    long_title = _render(_segments(block, []))
                if cls != "AssentDt":
                    continue
                inside = True
                if long_title:
                    b.close_all()
                    b.new("long-title", "long_title", "long_title", [], None, 0, None).items.append(long_title)
                    b.in_preamble = True
                continue
            if not inside:
                if cls != "LongT" and not cls.startswith("ActHead"):
                    continue
                inside = True
            if cls in SKIP or cls in FRONT:
                continue
            images: list[str] = []
            if tag == "table":
                lines = _table_lines(block, images)
                target = b.text_target()
                if target is None:
                    b.report.blocks_outside_provisions["table"] += 1
                else:
                    for line in lines:
                        b.add_text(target, line, [])
                    b.add_text(target, "", images)
                continue
            if tag not in ("p", "h1", "h2", "h3", "h4", "h5", "h6"):
                continue
            segments = _segments(block, images)
            text = _render(segments)
            if cls == "LongT":
                b.close_all()
                node = b.new("long-title", "long_title", "long_title", [], None, 0, None)
                node.items.append(text)
                b.in_preamble = True
                continue
            if cls.startswith("ActHead"):
                level = cls.removeprefix("ActHead")
                if level == "5":
                    number_el = next(
                        (s for s in block.iter(f"{{{XHTML}}}span") if s.get("class") == "CharSectno"), None
                    )
                    number = _ws("".join(number_el.itertext())) if number_el is not None else ""
                    if not number:  # a few headings carry the number without the CharSectno style
                        m = _NUMBERED_HEADING.match(text)
                        number = m.group(1) if m else ""
                    heading = text[len(number) :].strip() if number and text.startswith(number) else text
                    heading = _SEE_NOTE.sub("", heading)
                    b.section(number or None, heading)
                elif level == "1":
                    b.heading1(text)
                else:
                    b.heading(text)
                continue
            if text or images:
                if cls not in KNOWN and cls not in NUMBERED:
                    b.report.other_styles[cls] += 1
                b.block(block, cls, segments, text, images)
    b._unattached_schedule_notes()


def _assent_date(pages: list[Element], name: str, title_id: str) -> date | None:
    """Assent date of the principal Act: its row in Endnote 3 (legislation history)."""
    names = {name, f"{name} ({title_id})"}
    for body in pages:
        seen = False
        for el in body.iter():
            cls = el.get("class") or ""
            if cls == "TableOfActsHead" or (
                cls == "ENotesHeading2" and "Legislation history" in "".join(el.itertext())
            ):
                seen = True
            elif seen and (cls.startswith("ENotesHeading") or cls == "TableOfAmendHead"):
                return None
            elif seen and _local(el.tag) == "tr":
                cells = [_ws("".join(td.itertext())) for td in el if _local(td.tag) in ("td", "th")]
                if len(cells) >= 3 and cells[0] in names:
                    return written_date(cells[2])
    return None


def _cover_value(pages: list[Element], pattern: str, cls: str | None = None) -> str | None:
    """The first match of ``pattern`` in a paragraph of the first page (of style ``cls``, when given)."""
    for el in pages[0].iter(f"{{{XHTML}}}p"):
        if cls is not None and (el.get("class") or "") != cls:
            continue
        m = re.match(pattern, _ws("".join(el.itertext())))
        if m:
            return m.group(1)
    return None


def _assent_endorsements(pages: list[Element]) -> list[date]:
    """Dates in the assent endorsement(s) of an Act as made: "[Assented to 2 December 2024]",
    "[Queen's Assent, 19 October 1973]" (also "[Reserved for Her Majesty's pleasure, 14 September 1973]")."""
    out: list[date] = []
    for body in pages:
        for el in body.iter(f"{{{XHTML}}}p"):
            if (el.get("class") or "") == "AssentDt":
                for m in re.finditer(r"\d{1,2} [A-Z][a-z]+ \d{4}", _ws("".join(el.itertext()))):
                    d = written_date(m.group(0))
                    if d is not None:
                        out.append(d)
    return out


def _outstanding(version: dict[str, Any], title_id: str, end: date | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if version.get("HasUnincorporatedAmendments"):
        out.append(
            {
                "affects_eid": None,
                "affected_label": None,
                "by_uri": None,
                "by_title": "Unincorporated amendments",
                "description": "The Register records amendments to this title that are not incorporated in this "
                "compilation.",
            }
        )
    if version.get("IsLatest") and end is not None:
        out.append(
            {
                "affects_eid": None,
                "affected_label": None,
                "by_uri": f"{WEB}/{title_id}/latest/versions",
                "by_title": f"Version from {end.isoformat()}",
                "description": f"When this document was retrieved the Register listed a later version of this title "
                f"starting {end.isoformat()} with no compilation registered for it; its text is not included.",
            }
        )
    return out


def parse_with_report(content: bytes) -> tuple[list[ParsedDocument], ParseReport]:
    try:
        data = json.loads(content)
    except (ValueError, UnicodeDecodeError) as exc:
        raise FrlParseError(f"not a Register API JSON document: {exc}") from exc
    if not isinstance(data, dict) or "Bytes" not in data or "TitleId" not in data:
        raise FrlParseError("not a Register API document response (TitleId/Bytes missing)")
    if data.get("Extension") != ".epub" or data.get("MimeType") != "application/epub+zip":
        raise FrlParseError("only EPUB documents are supported")
    version = data.get("Version")
    if not isinstance(version, dict):
        raise FrlParseError("version metadata missing (request the document with $expand=version)")
    title_id = str(data["TitleId"])
    instrument_type = instrument_type_for(title_id)  # raises for unsupported titles
    start = _day(data.get("Start"))
    if start is None or _day(version.get("Start")) != start or version.get("TitleId") != title_id:
        raise FrlParseError("missing or inconsistent version metadata")
    end = _day(version.get("End"))
    if end is not None and end <= start:
        raise FrlParseError(f"version ends ({end}) before it starts ({start})")
    as_made = str(data.get("CompilationNumber") or "") == "0" or data.get("RegisterId") == title_id
    if data.get("VolumeNumber") or data.get("UniqueTypeNumber"):
        raise FrlParseError("multi-volume documents are not supported")
    try:
        epub = base64.b64decode(data["Bytes"], validate=True)
    except (binascii.Error, TypeError) as exc:
        raise FrlParseError(f"document bytes are not valid base64: {exc}") from exc
    pages = [_xhtml_body(p) for p in _epub_pages(epub)]
    stated = _cover_value(pages, r"^Compilation date: (.+)$")
    if stated is not None and written_date(stated) not in (None, start):
        raise FrlParseError(f"compilation date {stated!r} differs from the version start {start}")
    assented = _assent_endorsements(pages) if as_made else []
    if assented and start not in assented:
        raise FrlParseError(f"assent date(s) {assented} differ from the version start {start}")
    name = _ws(str(version.get("Name") or ""))
    if not name:
        raise FrlParseError("version has no title")

    builder = _Builder(title_id, as_made)
    _parse_body(pages, builder)
    if not any(n.kind in ("section", "clause") for n in builder.nodes):
        raise FrlParseError("no sections found")
    as_made_note = AS_MADE_NOTE[instrument_type].format(date=start.isoformat()) if as_made else None
    provisions: list[ParsedProvision] = []
    for node in builder.nodes:
        children = [i for i in node.items if isinstance(i, _Node)]
        own = any(isinstance(i, str) and i for i in node.items)
        provisions.append(
            ParsedProvision(
                eid=node.eid,
                kind=node.kind,
                num_path=node.num_path,
                top_kind=node.top_kind,
                heading=node.heading,
                text=node.text(),
                parent_eid=node.parent.eid if node.parent else None,
                sort_order=len(provisions),
                status="in_force",
                valid_from=start,
                valid_to=end,
                extent=["AU"],
                note=" | ".join([n for n in (as_made_note, *node.notes) if n])[:400] or None,
                refs=[],
                is_leaf=not children,
                has_own_content=own and bool(children),
                context_heading=node.context,
            )
        )

    act_no = next(
        (
            _ws("".join(el.itertext()))
            for el in pages[0].iter(f"{{{XHTML}}}p")
            if el.get("class") in ("CompiledActNo", "Actno") and instrument_type == "act"
        ),
        "",
    )
    retro = _day(data.get("RetrospectiveStart"))
    if not as_made:
        work_date = _assent_date(pages, name, title_id)
        events = [ParsedEvent("version_change", start, None, None, "Version/Start")]
    elif instrument_type == "act":
        # The Register dates an Act as made from its assent.
        work_date = start
        events = [ParsedEvent("enactment", start, None, "Assent", "Version/Start")]
    else:
        # An instrument as made is dated from its registration; the cover states when it was made ("Dated ...").
        dated = _cover_value(pages, r"^Dated:? (\d{1,2} [A-Z][a-z]+ \d{4})\b")
        work_date = written_date(dated) if dated else None
        events = [ParsedEvent("publication", start, None, "Registered", "Version/Start")]
        if work_date is not None:
            events.insert(0, ParsedEvent("enactment", work_date, None, "Made", "cover: Dated"))
    if retro is not None and retro != start:
        events.append(ParsedEvent("retrospective_start", retro, None, None, "Version/RetrospectiveStart"))
    collection = {"constitution": "Constitution", "act": "Act", "legislative_instrument": "LegislativeInstrument"}
    work_uri = work_uri_for(title_id)
    doc = ParsedDocument(
        doc_kind="legislation",
        instrument_type=instrument_type,
        jurisdiction_id="AU",
        work_uri=work_uri,
        expression_uri=f"{work_uri}/eng@{start.isoformat()}",
        work_title=name,
        title=name,
        short_title=name,
        number=act_no or None,
        work_date=work_date,
        language="en",
        authoritative=True,
        text_origin="official_conversion" if as_made else "official_consolidation",
        consolidation_kind="as_made" if as_made else "point_in_time",
        version_date=start,
        # A text as made states no currency; the compilation date is the currency of a compilation.
        currency_date=None if as_made else start,
        extent=["AU"],
        ext_module="au",
        ext={
            "title_id": title_id,
            "collection": collection[instrument_type],
            "act_number": act_no or None,
            "code_title": builder.code_title,
        },
        outstanding_changes=_outstanding(version, title_id, end),
        events=events,
        provisions=provisions,
        validity_basis="enactment_date_presumed" if as_made else "revision_snapshot",
    )
    return [doc], builder.report


def parse_federal_register(content: bytes) -> list[ParsedDocument]:
    return parse_with_report(content)[0]
