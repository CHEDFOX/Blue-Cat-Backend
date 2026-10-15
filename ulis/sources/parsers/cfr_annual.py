"""Parser for the annual editions of the Code of Federal Regulations (GovInfo bulk data, ``CFRDOC`` XML).

Input is one volume of a title as GPO publishes it (``bulkdata/CFR/<year>/title-<n>/CFR-<year>-title<n>-vol<v>.xml``).
It yields one instrument per CFR part, with the same identifier as the eCFR source (``/us/cfr/t12/p220``) so that the
editions and the current eCFR text are versions of one work, and with the same section and paragraph identifiers
(``sec_220.1__para_b__para_1``), so that a pinpoint resolves in every version.

Time. Each edition is the Code as revised on the date printed on its title page ("Revised as of January 1, 2023"),
which is the first day of a quarter: titles 1-16 on January 1, 17-27 on April 1, 28-41 on July 1, 42-50 on October 1.
That date, not the year of the folder, is the version date: a title that was not revised in a year appears again,
unchanged, under the later year (title 1 of the 2025 folder is the edition of January 1, 2023). The text is attested
as in force on that date (``validity_basis = attested_at_edition``); no start date is invented, and a later edition
with a different wording records it from its own date (see docs/temporal-model.md). Editions must therefore be
ingested oldest first, and before the current eCFR files.

Text. A part's sections and appendices are read exactly as the eCFR parser reads them (``ulis.sources.parsers.ecfr``):
the volume's ``SECTION`` and ``APPENDIX`` elements are re-tagged as the eCFR's ``DIV8`` and ``DIV9`` and handed to the
same paragraph-splitting code, so both sources cut a section into the same paragraphs. Source credits, section
authority, editorial notes and footnotes become notes; the part's authority statement is ``ext.cfr_authority``.
The XML is the GPO's data file, not the authenticated print edition (the PDF), and not digitally signed.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any
from xml.etree.ElementTree import Element, SubElement  # noqa: S405 - building elements; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers import ecfr

PARSER_ID = "cfr_annual_xml"
PARSER_VERSION = "1.0.0"
_DATE = re.compile(r"([A-Za-z]+)\.?\s+(\d{1,2}),\s+(\d{4})")
_PART_HEAD = re.compile(r"^PARTS?\s+(\S+?)\s*[—–-]\s*(.*)$", re.S)
_SHORT_EDITION = re.compile(r"\((\d{1,2})-(\d{1,2})-(\d{2}) Edition\)")


class CfrParseError(ValueError):
    pass


def _text(el: Element | None, ctx: ecfr._Ctx) -> str:
    return ecfr._ws(ecfr._flat(el, ctx)) if el is not None else ""


def _revised(root: Element) -> date:
    """The date the edition is revised as of: the title page, else the short form in the running head."""
    ctx = ecfr._Ctx()
    for tag in ("REVISED", "DATE"):
        for el in root.iter(tag):
            found = ecfr._parse_amddate(_text(el, ctx))
            if found is not None and el.tag in ("REVISED", "DATE"):
                return found
    for el in root.iter("LRH"):
        m = _SHORT_EDITION.search(_text(el, ctx))
        if m:
            try:
                return date(
                    2000 + int(m.group(3)) if int(m.group(3)) < 50 else 1900 + int(m.group(3)),
                    int(m.group(1)),
                    int(m.group(2)),
                )
            except ValueError:
                break
    raise CfrParseError("the volume states no revision date")


_LEADING = re.compile(r"^\n( *)")
_TRAILING = re.compile(r"\n *$")


def _depretty(root: Element) -> None:
    """Undo the indentation GPO's pretty-printer added to the file, and only that.

    The volume is indented, one text node per line: ``<P>\\n    (a) \\n    <E>Caption.</E>\\n     Text ...\\n</P>``. The
    printer put a newline and the indentation before and after each text node and kept the node's own spaces beside
    them (the space after "(a)" and the one before "Text" are in the text; the space before a closing parenthesis
    that follows an italic word is not). So the indentation is the smallest run of spaces after a line break among
    the text nodes of an element; it is removed with its line breaks and every other space stays. Text read this way
    is the same as the eCFR's, which is not indented (compared on the parts the two share).
    """
    for el in root.iter():
        nodes: list[str | None] = [el.text, *(child.tail for child in el)]
        # The indentation is the smallest run of spaces after a line break in the element's text nodes, except for a
        # last node that is only a line break: that is the indentation of the closing tag, one level less.
        indents = [
            len(m.group(1))
            for i, n in enumerate(nodes)
            if n and (m := _LEADING.match(n)) and (n[m.end() :].strip() or i < len(nodes) - 1)
        ]
        if not indents:  # nothing but line breaks between the elements: formatting only
            if el.text and el.text.isspace() and "\n" in el.text:
                el.text = None
            for child in el:
                if child.tail and child.tail.isspace() and "\n" in child.tail:
                    child.tail = None
            continue
        indent = min(indents)

        def clean(text: str | None, indent: int = indent) -> str | None:
            if not text:
                return text
            m = _LEADING.match(text)
            if m:
                text = " " * max(0, len(m.group(1)) - indent) + text[m.end() :]
            return _TRAILING.sub("", text)

        el.text = clean(el.text)
        for child in el:
            child.tail = clean(child.tail)


def _normalise_heads(el: Element) -> None:
    """``<HD SOURCE="HD1">`` -> ``<HD1>``, the form the shared appendix code looks for."""
    for child in el.iter():
        if child.tag == "HD" and child.get("SOURCE") in ("HD1", "HD2", "HD3"):
            child.tag = str(child.get("SOURCE"))


def _unit(el: Element, ctx: ecfr._Ctx) -> Element | None:
    """A ``SECTION`` or ``APPENDIX`` of the annual edition as the eCFR's ``DIV8`` / ``DIV9`` element."""
    if el.tag == "SECTION":
        unit = Element("DIV8", {"TYPE": "SECTION", "N": _text(el.find("SECTNO"), ctx)})
        subject = _text(el.find("SUBJECT"), ctx)
        head = SubElement(unit, "HEAD")
        head.text = f"{unit.get('N')} {subject}".strip()
        skip = {"SECTNO", "SUBJECT", "RESERVED"}
    else:
        unit = Element("DIV9", {"TYPE": "APPENDIX"})
        children = list(el)
        first = next((c for c in children if c.tag == "HD"), None)
        if first is None:
            return None
        head = SubElement(unit, "HEAD")
        head.text = _text(first, ctx)
        skip = {"EAR", "RESERVED"}
        children = [c for c in children if c is not first]
        el = Element(el.tag)
        el.extend(children)
    for child in el:
        if child.tag not in skip:
            unit.append(child)
    return unit


def _units(el: Element) -> list[Element]:
    out: list[Element] = []
    for child in el:
        if child.tag in ("SECTION", "APPENDIX"):
            out.append(child)
        elif child.tag in ("SUBPART", "SUBJGRP"):
            out.extend(_units(child))
    return out


_FRONT_BACK = {"FMTR", "BMTR"}
_Context = tuple[str | None, str | None]  # chapter number and agency


def _title_number(root: Element, ctx: ecfr._Ctx) -> int:
    """The title number: the title page, else the title heading ("Title 1-General Provisions") of the text."""
    for path in ("FMTR/TITLEPG/TITLENUM", "TITLE/CFRTITLE/TITLEHD/HD", "TITLE/CFRTITLE/HD", "CFRTITLE/HD"):
        m = re.match(r"\s*Title\s+(\d+)\b", _text(root.find(path), ctx))
        if m:
            return int(m.group(1))
    raise CfrParseError("the volume states no title number")


def _parts_in_order(el: Element, chapter: _Context) -> list[tuple[Element, _Context]]:
    """Every PART of the text with the chapter (number, agency) it sits in; the front and back matter are not text."""
    out: list[tuple[Element, _Context]] = []
    for child in el:
        if child.tag in _FRONT_BACK:
            continue
        if child.tag == "PART":
            out.append((child, chapter))
        elif child.tag == "CHAPTER":
            out.extend(_parts_in_order(child, _chapter(child)))
        else:
            out.extend(_parts_in_order(child, chapter))
    return out


def _chapter(chapter: Element) -> _Context:
    ctx = ecfr._Ctx()
    head_el = chapter.find("TOC/TOCHD/HD")
    head = _text(head_el if head_el is not None else chapter.find("HD"), ctx)
    named = re.match(r"^CHAPTER\s+(\S+)\s*[—–-]\s*(.*)$", head, flags=re.S | re.I)
    if not named:
        return None, None
    agency = ecfr._display_case(re.sub(r"\s*\(continued\)\s*$", "", named.group(2), flags=re.I).strip())
    return named.group(1), agency


def parse_cfr_volume(content: bytes) -> list[ParsedDocument]:
    try:
        root = fromstring(content, forbid_dtd=True)
    except Exception as exc:
        raise CfrParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if root.tag != "CFRDOC":
        raise CfrParseError("root element is not a CFR volume (CFRDOC)")
    _depretty(root)
    _spread_fractions(root)
    ctx = ecfr._Ctx()
    title_no = _title_number(root, ctx)
    revised = _revised(root)
    amended = ecfr._parse_amddate(_text(root.find("AMDDATE"), ctx))
    stamp = hashlib.sha256(content).hexdigest()[:8]
    _normalise_heads(root)

    docs: list[ParsedDocument] = []
    seen: set[str] = set()
    for part, (chapter_no, agency) in _parts_in_order(root, (None, None)):
        doc = _part(part, ctx, title_no, revised, amended, stamp, chapter_no, agency)
        if doc is None or doc.work_uri in seen:
            continue
        seen.add(doc.work_uri)
        docs.append(doc)
    if not docs:
        raise ParseSkipped(f"{title_no} CFR (revised {revised.isoformat()}) has no part with section or appendix text")
    return docs


def _spread_fractions(root: Element) -> None:
    """A stacked fraction (``<FR>1/2</FR>``) after a whole number reads as "1 1/2", not "11/2"."""
    for parent in root.iter():
        for i, child in enumerate(parent):
            if child.tag != "FR":
                continue
            before = parent.text if i == 0 else parent[i - 1].tail
            if before and before[-1].isdigit():
                if i == 0:
                    parent.text = before + " "
                else:
                    parent[i - 1].tail = before + " "


def _part(
    part: Element,
    ctx: ecfr._Ctx,
    title_no: int,
    revised: date,
    amended: date | None,
    stamp: str,
    chapter_no: str | None,
    agency: str | None,
) -> ParsedDocument | None:
    head = _text(part.find("HD"), ctx)
    m = _PART_HEAD.match(head)
    if not m:
        return None  # a part that is only a reserved heading
    part_no, heading = m.group(1), m.group(2).strip()
    builder = ecfr._PartBuilder()
    for el in _units(part):
        unit = _unit(el, ctx)
        if unit is not None:
            builder.add_unit(unit)
    if not builder.provisions:
        return None
    authority = re.sub(r"^Authority:\s*", "", _text(part.find("AUTH"), ctx)) or None
    work_uri = f"/us/cfr/t{title_no}/p{part_no}"
    name = f"{title_no} CFR Part {part_no}"
    label = ecfr._display_case(heading)
    ext: dict[str, Any] = {
        "cfr_title": title_no,
        "cfr_part": part_no,
        "agency": agency,
        "cfr_chapter": chapter_no,
        "cfr_authority": authority,
        "volume_amended_on": amended.isoformat() if amended else None,
    }
    return ParsedDocument(
        doc_kind="legislation",
        instrument_type="regulation",
        jurisdiction_id="US",
        work_uri=work_uri,
        expression_uri=f"{work_uri}/en@{revised.isoformat()}/cfr-{stamp}",
        work_title=f"{name}: {label}",
        title=f"{name}: {label} (CFR annual edition revised as of {revised.isoformat()})",
        short_title=name,
        number=part_no,
        work_date=None,
        language="en",
        authoritative=False,
        text_origin="official_conversion",
        consolidation_kind="cfr_annual_edition",
        version_date=revised,
        currency_date=revised,
        extent=["US"],
        ext_module="us",
        ext={k: v for k, v in ext.items() if v is not None},
        provisions=builder.provisions,
        validity_basis="attested_at_edition",
    )
