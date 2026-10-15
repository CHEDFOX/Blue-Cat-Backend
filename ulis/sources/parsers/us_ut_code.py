"""Parser for the Utah Code (Office of Legislative Research and General Counsel): the XML of one title.

Input: the bundle (see :mod:`ulis.sources.us_state_bundle`) of ``Title<N>/C<N>_<version>.xml`` from
https://le.utah.gov/xcode/. Format notes:

* ``<title number="76"><effdate>7/1/2026</effdate><catchline>...</catchline><chapter number="76-5">`` then
  ``<part number="76-5-2"><section number="76-5-203">`` (a chapter may have sections directly). A section has
  ``<histories>`` (the legislature's history lines: "Amended by Chapter 356, 2026 General Session"),
  ``<catchline>`` (its caption), its own text and nested ``<subsection number="76-5-203(1)(a)">``; the printed
  label of a subsection (``(a)``) is the last parenthesised group of its ``number`` attribute and is not in the
  element text, so the parser writes it in front of the text, as Utah's own pages do. Cross references are
  ``<xref refnumber="76-18-506">`` and become citation links.
* **Dates.** A section that has a version taking effect later is printed twice: the current text with
  ``<enddate type="SC">1/1/2027</enddate>`` and the new text with ``<effdate>1/1/2027</effdate>``. These are
  stated by the source: the current version gets ``valid_to``, the later one ``valid_from`` (a point-in-time
  history of the future, not of the past). Every other section is the current text with no stated start
  (``validity_basis = "attested_at_edition"``, dated by retrieval date); the title's own ``effdate`` is kept as
  ``compiled_effective`` in the extension data.
* ``<contingent type="CE"/>`` marks a section whose effectiveness depends on a condition; the parser says so in the
  note. Tables are flattened to lines of cells; a ``<char set=.. char=../>`` (a special character the XML encodes
  by code) is written as ``[char set/char]`` because its meaning is not stated.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedRef
from ulis.sources.parsers.us_state_common import (
    SectionDraft,
    StateParseError,
    build_provisions,
    lines_of,
    state_document,
    ws,
)
from ulis.sources.us_state_bundle import read_bundle

PARSER_ID = "us_ut_code_xml"
PARSER_VERSION = "1.0.0"
JURISDICTION = "US-UT"
MODULE = "us_ut"
MAX_XML_BYTES = 80 * 1024 * 1024
NOT_TEXT = frozenset({"histories", "catchline", "effdate", "enddate", "contingent", "sinfo"})
LABEL = re.compile(r"(\([^()]+\))$")
REFNUMBER = re.compile(r"^([0-9]+[A-Za-z]?)(?:-([0-9]+[A-Za-z]*)(?:-([0-9]+(?:\.[0-9]+)*[A-Za-z]*))?)?(?:\(.*)?$")


def work_uri(title: str) -> str:
    return f"/us-ut/code/{title}"


def section_eid(number: str) -> str:
    return f"sec-{number}"


def _date(text: str | None) -> date:
    try:
        return datetime.strptime((text or "").strip(), "%m/%d/%Y").date()
    except ValueError as exc:
        raise StateParseError(f"unreadable date {text!r}") from exc


def _xref(el: Element) -> ParsedRef | None:
    m = REFNUMBER.match((el.get("refnumber") or "").strip())
    if m is None:
        return None
    title, _chapter, section = m.groups()
    work = work_uri(title)
    # depth: 1 chapter, 2 part, 3 section, 4 subsection (a title is depth 0): only 3 and deeper name a section.
    raw_depth = (el.get("depth") or "").strip()
    depth = int(raw_depth) if raw_depth.isdigit() else (3 if section else 0)  # the source has "null" and stray values
    href = f"{work}/~{section_eid('-'.join(m.group(i) for i in (1, 2, 3)))}" if section and depth >= 3 else work
    return ParsedRef(href=href, text=ws("".join(el.itertext())), relation="cites")


def _piece(child: Element, refs: list[ParsedRef]) -> str:
    """The text one child element contributes (its tail excluded)."""
    tag = child.tag
    if tag == "subsection" or tag in NOT_TEXT:
        return ""
    if tag == "tab":
        return " "
    if tag == "eol":
        return "\n"
    if tag == "char":
        return f"[char {child.get('set')}/{child.get('char')}]"
    if tag in ("table", "tbl"):
        rows = [" ".join(ws("".join(c.itertext())) for c in row.findall("cell")) for row in child.iter("row")]
        return "\n" + "\n".join(rows) + "\n"
    if tag == "xref":
        ref = _xref(child)
        if ref is not None:
            refs.append(ref)
        return "".join(child.itertext())
    if tag == "right":
        return " " + "".join(child.itertext())
    return _inline(child, refs)


def _inline(el: Element, refs: list[ParsedRef]) -> str:
    """Text of an element without its subsections: tabs become spaces, ``<eol/>`` line breaks."""
    parts: list[str] = [el.text or ""]
    for child in el:
        parts.append(_piece(child, refs))
        parts.append(child.tail or "")
    return "".join(parts)


def _label(el: Element) -> str:
    m = LABEL.search(el.get("number") or "")
    return m.group(1) if m else ""


def _emit(el: Element, carry: str, out: list[str], refs: list[ParsedRef]) -> None:
    """Lines of an element in document order; labels of subsections without text of their own carry on."""
    state = {"carry": carry}

    def flush(buf: str) -> None:
        text = lines_of(buf)
        if not text:
            return
        lines = text.split("\n")
        if state["carry"]:
            lines[0] = f"{state['carry']} {lines[0]}"
            state["carry"] = ""
        out.extend(lines)

    buf = el.text or ""
    for child in el:
        if child.tag == "subsection":
            flush(buf)
            _emit(child, state["carry"] + _label(child), out, refs)
            state["carry"] = ""
            buf = child.tail or ""
        else:
            buf += _piece(child, refs) + (child.tail or "")
    flush(buf)


def _version_notes(section: Element) -> list[str]:
    notes: list[str] = []
    history = [ws("".join(h.itertext())) for h in section.findall("./histories/history")]
    if history:
        notes.append("Source history: " + "; ".join(h for h in history if h))
    if section.find("contingent") is not None:
        notes.append(
            "Contingent effectiveness: the source marks this section as taking effect only on a condition "
            f"(type {section.find('contingent').get('type')})."  # type: ignore[union-attr]
        )
    return notes


def parse_ut_code(content: bytes) -> list[ParsedDocument]:
    bundle = read_bundle(content, "UT")
    title = bundle.key
    if len(bundle.body_bytes) > MAX_XML_BYTES:
        raise StateParseError(f"title {title}: XML is larger than {MAX_XML_BYTES} bytes")
    try:
        root = fromstring(bundle.body_bytes, forbid_dtd=True)
    except Exception as exc:  # defusedxml raises its own exception types besides ParseError
        raise StateParseError(f"title {title}: not readable XML: {exc}") from exc
    if root.tag != "title" or root.get("number") != title:
        raise StateParseError(f"not the XML of Utah Code title {title}")
    name = ws(root.findtext("catchline") or "")
    effective = root.findtext("effdate")
    drafts: list[SectionDraft] = []

    def walk(el: Element, context: list[str]) -> None:
        for child in el:
            if child.tag in ("chapter", "part"):
                label = (
                    f"{child.tag.capitalize()} {child.get('number')}. {ws(child.findtext('catchline') or '')}".strip()
                )
                walk(child, [*context, label])
            elif child.tag == "section":
                number = child.get("number") or ""
                if not number.lower().startswith(f"{title.lower()}-"):  # the XML writes "26b-2-902" in title 26B
                    raise StateParseError(f"section {number!r} is not in title {title}")
                refs: list[ParsedRef] = []
                lines: list[str] = []
                _emit(child, "", lines, refs)
                start, end = child.findtext("effdate"), child.findtext("enddate")
                notes = _version_notes(child)
                if start:
                    notes.append(f"Version in effect from {_date(start).isoformat()} (stated by the source).")
                if end:
                    notes.append(f"Version ends {_date(end).isoformat()} (stated by the source).")
                drafts.append(
                    SectionDraft(
                        kind="section",
                        number=number,
                        heading=ws(child.findtext("catchline") or "") or None,
                        lines=lines,
                        notes=notes,
                        refs=list({r.href: r for r in refs}.values()),
                        context="; ".join(context) or None,
                        valid_from=_date(start) if start else None,
                        valid_to=_date(end) if end else None,
                    )
                )

    walk(root, [])
    ext: dict[str, str] = {"title": title, "title_name": name}
    if effective:
        ext["compiled_effective"] = _date(effective).isoformat()
    return [
        state_document(
            bundle,
            jurisdiction=JURISDICTION,
            module=MODULE,
            work_uri=work_uri(title),
            work_title=f"Utah Code, Title {title} - {name}",
            title=f"Utah Code, Title {title} - {name}",
            short_title=f"Utah Code title {title}",
            number=title,
            ext=ext,
            provisions=build_provisions(drafts, JURISDICTION),
            edition="code-online",
        )
    ]


__all__ = ["PARSER_ID", "PARSER_VERSION", "parse_ut_code", "work_uri"]
