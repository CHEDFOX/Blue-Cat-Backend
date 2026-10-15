"""Parser for the Federal Register (GovInfo bulk data, one daily issue as ``FEDREG`` XML): the final rules.

One instrument per final rule (``RULE`` element; interim and direct final rules and corrections are ``RULE`` too).
Proposed rules (``PRORULE``), notices (``NOTICE``), presidential documents and the issue's contents and reader aids are
not law and are not ingested: a notice or a proposal says nothing about what the law is, and a presidential document
is not a regulation. They can be added later as their own instrument types.

Identity. A rule is identified, as USLM does for documents of the Federal Register, by volume and first page
(``/us/fr/91/65077``; a second rule that begins on the same page gets ``-2``), and cited the same way:
``91 Fed. Reg. 65,077 (Oct. 14, 2026)``. The first page is the one the issue's own page marks (``PRTPAGE``) show where
the rule begins; the Federal Register document number (``2026-20956``, from the ``[FR Doc. 2026-20956 Filed ...]`` line)
is kept in ``ext.fr_number`` and as the instrument's number.

Time. The version date is the date of the issue. A rule's effective date is stated in prose in its DATES paragraph
and is often later than the issue; it is not extracted, so provisions carry no start date (``valid_from`` is None, the
``EFFECTIVE_DATE_UNKNOWN`` limitation applies) and the DATES paragraph is a provision of its own, in the rule's words.

Text. The preamble (summary, dates, addresses, contact, and the "Supplementary Information" cut at its headings) is the
agency's explanation, not regulatory language: each of its provisions says so in its note. The regulatory text (the
amendatory instructions and new text, ``REGTEXT``) is kept apart as provisions of kind ``regulatory_text``. Text is read
as the eCFR parser reads it (``ulis.sources.parsers.ecfr``): paragraphs one per line, tables by row, footnote marks
dropped and footnotes in the note, graphics not reproduced (a note says so). Nothing is summarised or reworded.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParseSkipped
from ulis.sources.parsers import ecfr

PARSER_ID = "federal_register_xml"
PARSER_VERSION = "1.0.0"
BLOCK = 25_000  # characters after which a block of preamble or regulatory text is cut at the next line
_FRDOC = re.compile(r"FR\s+Doc\.?\s*([A-Za-z0-9]+-[A-Za-z0-9]+)")
_ISSUE_DATE = re.compile(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})")
PREAMBLE_NOTE = "Preamble of the final rule (the agency's explanation); it is not regulatory text."
REGTEXT_NOTE = (
    "Amendatory instructions and new text of the final rule as published; "
    "the current text of the regulation is the eCFR."
)
GRAPHICS_NOTE = "The source has graphics or formulas here that are not reproduced in the text."


class FederalRegisterParseError(ValueError):
    pass


def _issue_date(text: str) -> date:
    m = _ISSUE_DATE.search(text)
    parsed = ecfr._parse_amddate(f"{m.group(1)} {m.group(2)}, {m.group(3)}") if m else None
    if parsed is None:
        raise FederalRegisterParseError(f"issue date not found in {text!r}")
    return parsed


def _text(el: Element | None, ctx: ecfr._Ctx) -> str:
    return ecfr._ws(ecfr._flat(el, ctx)) if el is not None else ""


def _lines_of(children: list[Element], ctx: ecfr._Ctx) -> list[str]:
    out: list[str] = []
    for child in children:
        out.extend(ecfr._lines(child, ctx))
    return out


def _footnotes(el: Element, ctx: ecfr._Ctx) -> list[str]:
    return [f"Footnote: {t}" for f in el.iter("FTNT") if (t := ecfr._ws(ecfr._flat(f, ctx)))]


def _blocks(items: list[tuple[str | None, list[str]]]) -> list[tuple[str | None, list[str]]]:
    """Cut (heading, lines) groups that are longer than BLOCK characters between lines."""
    out: list[tuple[str | None, list[str]]] = []
    for heading, lines in items:
        current: list[str] = []
        size = 0
        for line in lines:
            if current and size + len(line) > BLOCK:
                out.append((heading, current))
                current, size = [], 0
            current.append(line)
            size += len(line) + 1
        if current:
            out.append((heading, current))
    return out


class _Rule:
    def __init__(self) -> None:
        self.provisions: list[ParsedProvision] = []
        self.counts: dict[str, int] = {}

    def add(self, base: str, kind: str, heading: str | None, lines: list[str], note: str | None) -> None:
        text = "\n".join(lines).strip()
        if not text:
            return
        self.counts[base] = self.counts.get(base, 0) + 1
        eid = base if self.counts[base] == 1 else f"{base}_{self.counts[base]}"
        self.provisions.append(
            ParsedProvision(
                eid=eid,
                kind=kind,
                num_path=[],
                top_kind=kind,
                heading=heading,
                text=text,
                parent_eid=None,
                sort_order=len(self.provisions),
                status="in_force",
                valid_from=None,
                valid_to=None,
                extent=["US"],
                note=note,
                refs=[],
                is_leaf=True,
                has_own_content=False,
                context_heading=heading,
            )
        )


def _field(el: Element | None, ctx: ecfr._Ctx) -> list[str]:
    """The lines of a preamble field (``SUM``, ``EFFDATE`` ...) without its label ("SUMMARY:")."""
    if el is None:
        return []
    return _lines_of([c for c in el if not (c.tag == "HD" and c.get("SOURCE") == "HED")], ctx)


def _regtext(rule: _Rule, el: Element | None, ctx: ecfr._Ctx, lead: list[str] | None = None) -> None:
    """One ``REGTEXT`` (the amendments to one CFR part); ``lead`` is the headings that precede it in the rule
    ("§ 39.13 [Amended]")."""
    title = (el.get("TITLE") or "").strip() if el is not None else ""
    part = (el.get("PART") or "").strip() if el is not None else ""
    heading = f"Regulatory text: {title} CFR part {part}" if title and part else "Regulatory text"
    groups: list[tuple[str | None, list[str]]] = [(heading, list(lead or []))]
    for child in el if el is not None else []:
        if child.tag == "SECTION":
            sectno = _text(child.find("SECTNO"), ctx)
            subject = _text(child.find("SUBJECT"), ctx)
            lines = [f"{sectno} {subject}".strip()] if sectno or subject else []
            lines += _lines_of([c for c in child if c.tag not in ("SECTNO", "SUBJECT", "RESERVED")], ctx)
            groups[-1][1].extend(x for x in lines if x)
        else:
            groups[-1][1].extend(ecfr._lines(child, ctx))
    for head, lines in _blocks(groups):
        rule.add("regtext", "regulatory_text", head, lines, REGTEXT_NOTE)


def _first_pages(root: Element) -> dict[int, int | None]:
    """Page on which each ``RULE`` begins, by the page marks in document order."""
    page: int | None = None
    out: dict[int, int | None] = {}
    for el in root.iter():
        if el.tag == "PRTPAGE":
            value = (el.get("P") or "").strip()
            page = int(value) if value.isdigit() else page
        elif el.tag == "RULE":
            preamble = el.find("PREAMB")
            first = preamble[0] if preamble is not None and len(preamble) else None
            if first is not None and first.tag == "PRTPAGE" and (first.get("P") or "").isdigit():
                out[id(el)] = int(first.get("P") or "")
            else:
                out[id(el)] = page
    return out


def parse_issue(content: bytes) -> list[ParsedDocument]:
    try:
        root = fromstring(content, forbid_dtd=True)
    except Exception as exc:
        raise FederalRegisterParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if root.tag != "FEDREG":
        raise FederalRegisterParseError("root element is not a Federal Register issue (FEDREG)")
    ctx0 = ecfr._Ctx()
    volume = ecfr._ws(ecfr._flat(root.find("VOL"), ctx0)) if root.find("VOL") is not None else ""
    published = _issue_date(ecfr._flat(root.find("DATE"), ctx0)) if root.find("DATE") is not None else None
    if not volume.isdigit() or published is None:
        raise FederalRegisterParseError("the issue states no volume or date")
    pages = _first_pages(root)
    docs: list[ParsedDocument] = []
    used: dict[str, int] = {}
    numbers: set[str] = set()
    for el in root.iter("RULE"):
        doc = _rule(el, int(volume), published, pages.get(id(el)))
        if doc is None or doc.number in numbers:
            continue
        numbers.add(str(doc.number))
        used[doc.work_uri] = used.get(doc.work_uri, 0) + 1
        if used[doc.work_uri] > 1:  # a second rule beginning on the same page
            first = doc.work_uri
            doc.work_uri = f"{first}-{used[first]}"
            doc.expression_uri = doc.work_uri + doc.expression_uri[len(first) :]
        docs.append(doc)
    if not docs:
        raise ParseSkipped(f"Federal Register {published.isoformat()} has no final rule")
    return docs


def _rule(el: Element, volume: int, published: date, page: int | None) -> ParsedDocument | None:
    ctx = ecfr._Ctx()
    frdoc = el.find("FRDOC")
    m = _FRDOC.search(ecfr._flat(frdoc, ctx)) if frdoc is not None else None
    preamble = el.find("PREAMB")
    if m is None or preamble is None or page is None:
        return None  # not identifiable: never guessed
    number = m.group(1)
    subject = _text(preamble.find("SUBJECT"), ctx)
    agency = ecfr._display_case(_text(preamble.find("AGENCY"), ctx)) or None
    subagency = ecfr._display_case(_text(preamble.find("SUBAGY"), ctx)) or None
    action = " ".join(_field(preamble.find("ACT"), ctx)) or None
    rin = _text(preamble.find("RIN"), ctx) or None
    affected = [ecfr._ws(ecfr._flat(c, ctx)) for c in preamble.findall("CFR")]

    rule = _Rule()
    for tag, base, heading in (
        ("SUM", "summary", "Summary"),
        ("EFFDATE", "dates", "Dates"),
        ("ADD", "addresses", "Addresses"),
        ("FURINF", "contact", "For further information contact"),
    ):
        lines = _field(preamble.find(tag), ctx)
        for head, chunk in _blocks([(heading, lines)]):
            rule.add(base, "preamble", head, chunk, PREAMBLE_NOTE)
    # headed text inside the preamble itself (some rules put sub-headed paragraphs between ADDRESSES and the contact)
    extra: list[tuple[str | None, list[str]]] = []
    for child in preamble:
        if child.tag == "HD":
            extra.append((ecfr._ws(ecfr._flat(child, ctx)), []))
        elif child.tag == "P" and extra:
            extra[-1][1].extend(ecfr._lines(child, ctx))
    for head, chunk in _blocks(extra):
        rule.add("preamble_text", "preamble", head, chunk, PREAMBLE_NOTE)

    for supl in el.findall("SUPLINF"):
        groups: list[tuple[str | None, list[str]]] = [("Supplementary information", [])]
        in_rule_text = False  # after the first REGTEXT: headings belong to the next amendment, not to the preamble
        lead: list[str] = []
        for child in supl:
            if child.tag == "REGTEXT":
                if not in_rule_text:
                    for head, chunk in _blocks(groups):
                        rule.add("supplementary_information", "preamble", head, chunk, PREAMBLE_NOTE)
                    groups = [(None, [])]
                in_rule_text = True
                _regtext(rule, child, ctx, lead)
                lead = []
            elif child.tag == "FTNT":
                continue  # footnotes are collected once, below
            elif child.tag == "SIG":
                if lead:
                    _regtext(rule, None, ctx, lead)
                    lead = []
                for head, chunk in _blocks(groups):
                    rule.add("supplementary_information", "preamble", head, chunk, PREAMBLE_NOTE)
                groups = [(None, [])]
                rule.add("signature", "preamble", "Signature", ecfr._lines(child, ctx), PREAMBLE_NOTE)
            elif in_rule_text:
                lead.extend(ecfr._lines(child, ctx))
            elif child.tag == "HD":
                head = ecfr._ws(ecfr._flat(child, ctx))
                if child.get("SOURCE") == "HED":
                    continue  # the label "SUPPLEMENTARY INFORMATION:"
                if child.get("SOURCE") == "HD1":  # a new block at each top-level heading; lower headings stay in it
                    groups.append((head, [head]))
                else:
                    groups[-1][1].append(head)
            else:
                groups[-1][1].extend(ecfr._lines(child, ctx))
        if lead:
            _regtext(rule, None, ctx, lead)
        for head, chunk in _blocks(groups):
            rule.add("supplementary_information", "preamble", head, chunk, PREAMBLE_NOTE)
    for child in el:  # regulatory text placed after the preamble, not inside it
        if child.tag == "REGTEXT":
            _regtext(rule, child, ctx)
        elif child.tag == "SIG":
            rule.add("signature", "preamble", "Signature", ecfr._lines(child, ctx), PREAMBLE_NOTE)
    for head, chunk in _blocks([("Footnotes", _footnotes(el, ctx))]):
        rule.add("footnotes", "preamble", head, chunk, PREAMBLE_NOTE)
    if not rule.provisions:
        return None
    if ctx.graphics:
        rule.provisions[0].note = f"{rule.provisions[0].note} {GRAPHICS_NOTE}".strip()
    work_uri = f"/us/fr/{volume}/{page}"
    label = f"{volume} FR {page}"
    kind = (action or "Final rule").rstrip(".")
    title = f"{subject or 'Final rule'} ({agency or 'agency not stated'}; {kind}, {label}, {published.isoformat()})"
    ext: dict[str, Any] = {
        "fr_volume": volume,
        "fr_page": page,
        "fr_number": number,
        "fr_action": action,
        "agency": agency,
        "sub_agency": subagency,
        "rin": rin,
        "cfr_affected": affected or None,
    }
    return ParsedDocument(
        doc_kind="legislation",
        instrument_type="regulation",
        jurisdiction_id="US",
        work_uri=work_uri,
        expression_uri=f"{work_uri}/en@{published.isoformat()}",
        work_title=subject or f"Final rule {number}",
        title=title,
        short_title=None,
        number=number,
        work_date=published,
        language="en",
        authoritative=False,
        text_origin="official_conversion",
        consolidation_kind="federal_register_rule",
        version_date=published,
        currency_date=None,
        extent=["US"],
        ext_module="us",
        ext={k: v for k, v in ext.items() if v is not None},
        events=[ParsedEvent("publication", published, None, f"Published in the Federal Register, {label}", "FRDOC")],
        provisions=rule.provisions,
        validity_basis="source_periods",
    )
