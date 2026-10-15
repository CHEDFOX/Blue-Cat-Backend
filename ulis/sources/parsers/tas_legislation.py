"""Parser for Tasmanian Acts (Tasmanian Legislation Online, XML of an Act's current version).

The XML (``https://www.legislation.tas.gov.au/view/xml/inforce/current/act-YYYY-NNN``, Office of
Parliamentary Counsel Tasmania, CC BY 4.0) uses the Tasmanian ``act.dtd``. The DTD is not loaded: the
DOCTYPE line is removed before parsing and the document is parsed with ``defusedxml`` (DTDs forbidden).

Format notes:

* one file per Act; the root ``ACT`` states the version's ``FIRST.VALID.DATE`` (the date from which this
  version applies; ``END.VALID.DATE`` 9999-11-02 is the open-end sentinel). Individual provisions are not
  dated, so the version is a snapshot (``validity_basis = revision_snapshot``); earlier versions are not in
  the file and dates before it are a history gap, not "not yet in force";
* numbers are attributes (``SECNO``, ``SUBSECNO``, ``PARANO`` ...), the printed form comes from ``NOFORMAT``
  (``BR...``: in brackets); ``PART``, ``DIVISION`` and ``C.CHAPTER`` are headings only and become the
  context of the sections inside them;
* a schedule enacted as a Code (the Criminal Code is Schedule 1 to the Criminal Code Act 1924) has its
  sections as ``code_section`` provisions, cited ``Criminal Code (Tas) s 158``;
* a provision with ``OMITTED="YESOMIT"`` is repealed or omitted: status ``repealed``, empty text, the
  ``HOWAFFECTED`` note in ``note``; ``HOWAFFECTED`` (how a provision was amended) is never law text;
* ``CHARGE`` (the wording of the charge for an offence in a Code) is kept as a line of its own, exactly as
  published; appendices (forms, tables of a Code) are not indexed.
"""

from __future__ import annotations

import re
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParsedRef
from ulis.sources.parsers.subnational_common import iso_date, padded_number, safe_id, ws

PARSER_ID = "tas_act_xml"
PARSER_VERSION = "1.0.0"
SITE = "https://www.legislation.tas.gov.au"
JURISDICTION = "AU-TAS"

# element -> (kind, attribute holding its number)
UNITS = {
    "SECTION": ("section", "SECNO"),
    "S.CLAUSE": ("section", "CLAUSENO"),
    "SUBSECTION": ("subsection", "SUBSECNO"),
    "S.SUBCLAUSE": ("subsection", "SUBCLAUSENO"),
    "PARAGRAPH": ("paragraph", "PARANO"),
    "SUBPARAGRAPH": ("subparagraph", "SUBPARANO"),
    "SUBSUBPARAGRAPH": ("subsubparagraph", "SUBSUBPARANO"),
    "DEFINITION": ("definition", ""),
}
HEADING_CONTAINERS = {"PART": "PARTNO", "DIVISION": "DIVNO", "C.CHAPTER": "CHAPNO"}
# Content that is not the provision's own law text.
NOT_TEXT = {"HEADNOTE", "HEADING", "HOWAFFECTED", "SECTREF", "CREST", "APPENDIX", "GRAPHIC", "FORM"}
DOCTYPE = re.compile(rb"<!DOCTYPE[^>\[]*(?:\[[^\]]*\])?\s*>", re.S)

Item = tuple[str, "str | Element", Element]


class TasParseError(ValueError):
    pass


def work_uri(year: str, number: str) -> str:
    return f"/au/tas/act/{year}-{padded_number(number)}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map a Tasmanian Legislation Act URL to (ULIS work URI, eId)."""
    m = re.match(
        r"^https://www\.legislation\.tas\.gov\.au/view/(?:whole/)?(?:html|xml|pdf)/inforce/current/act-(\d{4})-(\d{3}[A-Za-z]?)$",
        uri,
    )
    return (work_uri(m.group(1), m.group(2)), None) if m else None


def _raw(el: Element) -> str:
    """Text of an element; an empty ``DATE`` element carries its date in attributes (shown as ``1 July 2000``)."""
    if el.tag == "DATE" and not "".join(el.itertext()).strip():
        day, month, year = el.get("DAY"), el.get("MONTH"), el.get("YEAR")
        if day and month and year and day.isdigit():
            return f"{int(day)} {month.capitalize()} {year}" + (el.tail or "")
        return el.tail or ""
    parts = [el.text or ""]
    for child in el:
        parts.append(_raw(child))
    parts.append(el.tail or "")
    return "".join(parts)


def inline(el: Element) -> str:
    # _raw includes the element's tail (needed for children); drop the root's own tail
    saved, el.tail = el.tail, None
    try:
        return ws(_raw(el))
    finally:
        el.tail = saved


def _table_text(el: Element) -> str:
    rows = [" | ".join(inline(e) for e in row.iter("ENTRY")) for row in el.iter("ROW")]
    return "\n".join(r for r in rows if r.strip(" |"))


def _stream(el: Element) -> list[Item]:
    out: list[Item] = []
    for c in el:
        tag = c.tag
        if tag in NOT_TEXT or tag in HEADING_CONTAINERS:
            continue
        if tag == "TEXT":
            line = inline(c)
            if line:
                out.append(("t", line, c))
        elif tag in UNITS:
            out.append(("u", c, c))
        elif tag == "PENALTY":
            out.extend(_stream(c))
        elif tag == "TABLE":
            out.append(("t", _table_text(c), c))
        else:  # including CHARGE, the wording of the charge for an offence in a Code, kept as its own line
            line = inline(c)
            if line:
                out.append(("t", line, c))
    return out


def _label(el: Element) -> str:
    """The printed number: bracketed when the source's ``NOFORMAT`` says so (``(1)``, ``(a)``, ``(i)``)."""
    attr = UNITS.get(el.tag, ("", ""))[1]
    value = ws(el.get(attr) or "") if attr else ""
    if not value:
        return ""
    return f"({value})" if (el.get("NOFORMAT") or "").startswith("BR") else value


def _term(el: Element) -> str:
    term = el.find("DEFINEDTERM")
    return inline(term) if term is not None else ""


def _text_of(el: Element) -> str:
    lines: list[str] = []
    for kind, value, _ in _stream(el):
        if kind == "t":
            lines.append(str(value))
        elif kind == "u":
            assert isinstance(value, Element)
            label = _label(value)
            body = _text_of(value)
            lines.append(f"{label} {body}".strip() if label else body)
    return "\n".join(line for line in lines if line)


def _eid_source(el: Element) -> str:
    return safe_id(re.sub(r"/EN$", "", (el.get("ID") or "")).replace("/", "-"))


def parse_tas_legislation(content: bytes) -> list[ParsedDocument]:
    try:
        root = fromstring(DOCTYPE.sub(b"", content, count=1), forbid_dtd=True)
    except Exception as exc:
        raise TasParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if root.tag != "ACT":
        raise TasParseError(f"unsupported root element {root.tag!r} (only Acts are supported)")
    title = ws(root.get("TITLE") or "")
    year, number = root.get("YEAR") or "", root.get("ACTNO") or ""
    if not title or not year.isdigit() or not re.fullmatch(r"\d+[A-Za-z]?", number):
        raise TasParseError("missing title, year or number")
    version_date = iso_date(root.get("FIRST.VALID.DATE"))
    if version_date is None:
        raise TasParseError("missing FIRST.VALID.DATE")
    uri = work_uri(year, number)
    plain_number = padded_number(number).lstrip("0")

    provisions: list[ParsedProvision] = []
    used: dict[str, int] = {}
    code_titles: list[str] = []

    def eid_of(el: Element, parent_eid: str | None, kind: str) -> str:
        base = _eid_source(el) or f"{parent_eid or 'x'}-{kind}{len(provisions)}"
        n = used.get(base, 0) + 1
        used[base] = n
        return base if n == 1 else f"{base}-{n}"

    def refs_of(el: Element) -> list[ParsedRef]:
        out: list[ParsedRef] = []
        for kind, _, source in _stream(el):
            if kind != "t":
                continue
            for ref in source.iter("ACTREF"):
                y, n = ref.get("YEAR") or "", ref.get("ACTNO") or ""
                if ref.get("JURISDICTION") == "TAS" and y.isdigit() and re.fullmatch(r"\d+[A-Za-z]?", n):
                    out.append(
                        ParsedRef(f"{SITE}/view/html/inforce/current/act-{y}-{padded_number(n)}", inline(ref), "cites")
                    )
        return out

    def add(el: Element, parent_eid: str | None, num_path: list[str], top_kind: str, context: str | None) -> None:
        schedule = el.tag == "SCHEDULE"
        kind = "schedule" if schedule else UNITS[el.tag][0]
        eid = eid_of(el, parent_eid, kind)
        if el.tag == "DEFINITION":
            label = f"def:{_term(el)}"
        elif schedule:
            label = ws(el.get("SCHEDULENO") or "")
        else:
            label = _label(el)
        path = [*num_path, label]
        head_el = el.find("HEADNOTE") if not schedule else el.find("HEADING")
        heading = inline(head_el) if head_el is not None and inline(head_el) else None
        items = _stream(el)
        children = [v for k, v, _ in items if k == "u"]
        repealed = el.get("OMITTED") == "YESOMIT"
        notes = [ws(h.findtext("TEXT") or "") for h in el.findall("HOWAFFECTED")]
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=kind,
                num_path=path,
                top_kind=top_kind,
                heading=heading,
                text="" if repealed else _text_of(el),
                parent_eid=parent_eid,
                sort_order=len(provisions),
                status="repealed" if repealed else "in_force",
                valid_from=version_date,
                valid_to=None,
                extent=[JURISDICTION],
                note=ws(" ".join(notes))[:2000] or None,
                refs=[] if repealed else refs_of(el),
                is_leaf=not children or repealed,
                has_own_content=any(k == "t" for k, _, _ in items) and bool(children) and not repealed,
                context_heading=context,
            )
        )
        if not repealed:
            for child in children:
                assert isinstance(child, Element)
                add(child, eid, path, top_kind, context)

    def walk(
        container: Element, context: str | None, top_kind: str, prefix: list[str], direct_sections: bool = True
    ) -> None:
        """Descend through headings (part, division, chapter) to the sections they contain."""
        for child in container:
            if child.tag in ("SECTION", "S.CLAUSE") and direct_sections:
                add(child, None, prefix, top_kind, context)
            elif child.tag in HEADING_CONTAINERS:
                head = child.find("HEADING")
                number_attr = HEADING_CONTAINERS[child.tag]
                word = {"PART": "Part", "DIVISION": "Division", "C.CHAPTER": "Chapter"}[child.tag]
                here = ws(f"{word} {child.get(number_attr) or ''} {inline(head) if head is not None else ''}")
                walk(child, f"{context} — {here}" if context else here, top_kind, prefix)

    body = root.find("BODY")
    if body is None:
        raise TasParseError("missing BODY")
    walk(body, None, "section", [])

    tail = root.find("TAIL")
    for sched in tail if tail is not None else []:
        if sched.tag != "SCHEDULE":
            continue
        head_el = sched.find("HEADING")
        sched_title = inline(head_el) if head_el is not None else ""
        if next(sched.iter("C.CHAPTER"), None) is not None:  # a schedule enacted as a Code
            # The schedule may carry no heading; the Code is then named after its Act ("Criminal Code Act 1924").
            code_titles.append(re.sub(r"^The\s+", "", sched_title) or re.sub(r"\s+Act\s+\d{4}$", "", title))
            walk(sched, sched_title or None, "code_section", [])
        else:
            add(sched, None, [], "schedule", None)
            walk(sched, None, "schedule", [ws(sched.get("SCHEDULENO") or "")], direct_sections=False)

    if not provisions:
        raise TasParseError("no provisions found")

    ext: dict[str, object] = {
        "state": "Tas",
        "legislation_id": root.get("ID") or f"act-{year}-{padded_number(number)}",
        "act_number": f"No. {plain_number} of {year}",
    }
    if code_titles:
        ext["code_title"] = code_titles[0]
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type="act",
            jurisdiction_id=JURISDICTION,
            work_uri=uri,
            expression_uri=f"{uri}/en@{version_date.isoformat()}",
            work_title=title,
            title=title,
            short_title=title,
            number=f"{plain_number} of {year}",
            work_date=iso_date(root.get("ASSENT.DATE")),
            language="en",
            authoritative=False,
            text_origin="official_consolidation",
            consolidation_kind="point_in_time",
            version_date=version_date,
            currency_date=None,
            extent=[JURISDICTION],
            ext_module="au_state",
            ext=ext,
            outstanding_changes=[],
            events=[ParsedEvent("version_change", version_date, None, None, "ACT/@FIRST.VALID.DATE")],
            provisions=provisions,
            validity_basis="revision_snapshot",
        )
    ]
