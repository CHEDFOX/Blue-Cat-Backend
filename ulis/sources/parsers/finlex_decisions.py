"""Parser for decisions of the Data Protection Ombudsman in Finlex open data (Akoma Ntoso ``judgment``).

Finlex publishes the decisions of the Data Protection Ombudsman (tietosuojavaltuutettu) as Akoma
Ntoso ``judgment`` documents (``/akn/fi/judgment/data-protection-ombudsman-decision/{year}/{number}``),
one XML per language expression (``fin@``, ``swe@``). Metadata: the diary number (``FRBRalias
diaryNumber``), the date of the decision (``FRBRdate dateIssued``), the date Finlex produced the
expression (``dateProduced``), the document title (``docTitle``), keywords and the legal basis
(``finlex:legalBasis``, a concept such as "EU:n yleisen tietosuoja-asetuksen mukainen päätös").

The body is an unnumbered sequence of headings (``tblock/heading``), paragraphs (``p``) and list items
(``li``). Each becomes a paragraph, in document order, exact text with whitespace collapsed; footnote
references (``sup``) appear as ``[n]``. Paragraph numbers do not exist in the source, so ``num`` is empty
and citations are to the decision as a whole. The Ombudsman is not a court (profile entry ``FI-TSV``,
kind ``administrative_body``); its decisions are never labelled authoritative precedent.

Only ``data-protection-ombudsman-decision`` is supported. The decisions of the Chancellor of Justice and
the Parliamentary Ombudsman, which Finlex serves in the same shape, hold their text in an attached PDF
(``componentRef``) and are refused rather than stored without it.

Links: a statute named with its Statute Book number in brackets, "(1050/2018)", is linked (extraction method
``pattern``) to the work ``/fi/act/{year}/{number}``; nothing else is linked.
"""

from __future__ import annotations

import re
from datetime import date
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedJudgmentMeta, ParsedParagraph, ParsedRef, ParseSkipped

PARSER_VERSION = "1.0.0"
AKN = "{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}"
FINLEX = "{http://data.finlex.fi/schema/finlex}"
SUBTYPE = "data-protection-ombudsman-decision"
COURT_ID = "FI-TSV"
LANGUAGES = {"fin": "fi", "swe": "sv"}
_URI = re.compile(rf"^/akn/fi/judgment/{SUBTYPE}/(\d{{4}})/(\d{{1,6}})$")
BREAK = "\x1f"
SPACE = re.compile(r"[ \t\r\n\xa0]+")
# A statute is identified in Finnish and Swedish legal text by its number in the Statute Book, in brackets:
# "tietosuojalaki (1050/2018) 38 §". That identifier is strict (number, slash, year), so it is linked at the level
# of the work; whether the Act is ingested is decided when citations are resolved (otherwise the link stays open).
_STATUTE = re.compile(r"(?<![\d/])\((\d{1,4})/(1[89]\d{2}|20\d{2})\)")
PREFIX = {"headnote": "t", "introduction": "i", "heading": "h", "text": "p", "item": "li"}


class FinlexDecisionParseError(ValueError):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _raw(el: Element) -> str:
    parts = [el.text or ""]
    for child in el:
        tag = _local(child.tag)
        if tag == "br":
            parts.append(BREAK)
        elif tag == "sup":
            inner = SPACE.sub(" ", "".join(child.itertext())).strip()
            parts.append(f"[{inner}]" if inner else "")
        else:
            parts.append(_raw(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _text(el: Element) -> str:
    text = SPACE.sub(" ", _raw(el))
    text = re.sub(f" ?{BREAK} ?", BREAK, text).strip(f" {BREAK}")
    return text.replace(BREAK, "\n")


def _statute_refs(text: str) -> list[ParsedRef]:
    refs: dict[str, ParsedRef] = {}
    for m in _STATUTE.finditer(text):
        href = f"/fi/act/{m.group(2)}/{m.group(1)}"
        refs.setdefault(href, ParsedRef(href, m.group(0), "references", extraction_method="pattern"))
    return list(refs.values())


def _date(value: str | None, what: str) -> date:
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError as exc:
        raise FinlexDecisionParseError(f"{what}: not an ISO date: {value!r}") from exc


def _units(el: Element, out: list[tuple[str, str]]) -> None:
    """The text units below a container, in document order: (part, text)."""
    for child in el:
        tag = _local(child.tag)
        if tag == "p":
            if text := _text(child):
                out.append(("introduction" if _local(el.tag) == "introduction" else "text", text))
        elif tag == "heading":
            if text := _text(child):
                out.append(("heading", text))
        elif tag in ("ol", "ul"):
            for item in child:
                if _local(item.tag) == "li" and (text := _text(item)):
                    out.append(("item", text))
        elif tag in ("tblock", "block", "decision", "introduction", "hcontainer", "blockContainer", "container"):
            _units(child, out)
        elif tag == "componentRef":
            raise FinlexDecisionParseError("the text is in an attached document (componentRef); not supported")
        elif text := _text(child):  # anything else keeps its text rather than dropping it silently
            out.append(("text", text))


def parse_finlex_decision(content: bytes) -> list[ParsedDocument]:
    try:
        root = fromstring(content, forbid_dtd=True)
    except Exception as exc:  # defusedxml raises several exception types
        raise FinlexDecisionParseError(f"not well-formed or unsafe XML: {exc}") from exc
    judgment = root.find(f"{AKN}judgment")
    if root.tag != f"{AKN}akomaNtoso" or judgment is None:
        raise FinlexDecisionParseError("not an Akoma Ntoso judgment")
    work = judgment.find(f"{AKN}meta/{AKN}identification/{AKN}FRBRWork")
    expression = judgment.find(f"{AKN}meta/{AKN}identification/{AKN}FRBRExpression")
    manifestation = judgment.find(f"{AKN}meta/{AKN}identification/{AKN}FRBRManifestation")
    if work is None or expression is None or manifestation is None:
        raise FinlexDecisionParseError("FRBR Work, Expression or Manifestation missing")
    subtype = work.find(f"{AKN}FRBRsubtype")
    found = subtype.get("value") if subtype is not None else None
    if found != SUBTYPE:
        raise FinlexDecisionParseError(f"unsupported decision type {found!r}")
    uri_el = work.find(f"{AKN}FRBRuri")
    work_uri = (uri_el.get("value") if uri_el is not None else "") or ""
    m = _URI.match(work_uri)
    if not m:
        raise FinlexDecisionParseError(f"unexpected work URI {work_uri!r}")
    year, number = int(m.group(1)), m.group(2)

    language_el = expression.find(f"{AKN}FRBRlanguage")
    language = LANGUAGES.get(language_el.get("language", "") if language_el is not None else "")
    if language is None:
        raise FinlexDecisionParseError("expression language is not fin or swe")
    diary = next((a.get("value") for a in work.findall(f"{AKN}FRBRalias") if a.get("name") == "diaryNumber"), None)
    issued = next((d.get("date") for d in work.findall(f"{AKN}FRBRdate") if d.get("name") == "dateIssued"), None)
    produced = next(
        (d.get("date") for d in manifestation.findall(f"{AKN}FRBRdate") if d.get("name") == "dateProduced"), None
    )
    decision_date = _date(issued, "dateIssued")
    version_date = _date(produced, "dateProduced")

    title_el = judgment.find(f"{AKN}header//{AKN}docTitle")
    title = _text(title_el) if title_el is not None else ""
    if not title:
        raise FinlexDecisionParseError("no docTitle")

    body = judgment.find(f"{AKN}judgmentBody")
    units: list[tuple[str, str]] = []
    if body is not None:
        _units(body, units)
    if not units:
        raise ParseSkipped(f"{work_uri}: the decision has no text")

    paragraphs: list[ParsedParagraph] = [ParsedParagraph("t-1", None, "headnote", title, 0, [])]
    counters: dict[str, int] = {}
    for part, text in units:
        counters[part] = counters.get(part, 0) + 1
        paragraphs.append(
            ParsedParagraph(f"{PREFIX[part]}-{counters[part]}", None, part, text, len(paragraphs), _statute_refs(text))
        )

    concepts = {
        c.get("eId", ""): (c.get("showAs") or "").strip() for c in judgment.iter(f"{AKN}TLCConcept") if c.get("eId")
    }
    basis_el = judgment.find(f"{AKN}meta/{AKN}proprietary/{FINLEX}legalBasis")
    legal_basis = concepts.get((basis_el.get("refersTo") or "").lstrip("#")) if basis_el is not None else None
    ext = {
        "subtype": SUBTYPE,
        "year": year,
        "number": number,
        "diary_number": diary,
        "legal_basis": legal_basis or None,
        "keywords": [k.get("showAs", "").strip() for k in judgment.iter(f"{AKN}keyword") if k.get("showAs")],
        "date_produced": version_date.isoformat(),
    }
    return [
        ParsedDocument(
            doc_kind="judgment",
            instrument_type="judgment",
            jurisdiction_id="FI",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/{'fin' if language == 'fi' else 'swe'}@{version_date.isoformat()}",
            work_title=title,
            title=title,
            short_title=None,
            number=diary,
            work_date=decision_date,
            language=language,
            authoritative=False,  # Finlex's publication of the Ombudsman's decision
            text_origin="official",
            consolidation_kind="unknown",
            version_date=version_date,
            currency_date=None,
            extent=["FI"],
            ext_module="fi",
            ext=ext,
            judgment=ParsedJudgmentMeta(
                court_id=COURT_ID,
                case_name=title,
                neutral_citation=None,
                decision_date=decision_date,
                docket=diary,
            ),
            paragraphs=paragraphs,
        )
    ]
