"""Parser for Queensland Acts (Queensland Legislation website, XML of an Act's current reprint).

The XML (``https://www.legislation.qld.gov.au/view/xml/inforce/current/act-YYYY-NNN``, Office of the
Queensland Parliamentary Counsel, CC BY 4.0) uses the OQPC "QuILLS" DTD. The DTD is an external file and
is not loaded: the DOCTYPE line is removed before parsing and the document is parsed with ``defusedxml``
(DTDs forbidden).

Format notes:

* one file per Act; the root ``act`` states the reprint's first valid date (``first.valid.date``): the
  date from which this reprint applies. It does NOT date individual provisions, so the whole reprint is a
  snapshot (``validity_basis = revision_snapshot``) dated from that date; earlier reprints are not in the
  file and dates before it are a history gap, not "not yet in force";
* ``clause`` is an Act's section (``subclause``: subsection, ``li``: paragraph and further levels,
  ``definition``: a defined term); ``part``, ``division`` and ``subdivision`` (and, in a Code, ``codechapter``
  ...) are headings only and become the context of the sections inside them;
* a schedule enacted as a Code (the Criminal Code is Schedule 1 to the Criminal Code Act 1899) has its
  sections as ``code_section`` provisions, cited ``Criminal Code (Qld) s 302``;
* repealed or expired provisions keep their number with an empty ``repealed`` element: status
  ``repealed``, empty text, the history note in ``note``;
* ``historynote`` (legislative history) and ``note`` (drafting notes) are kept as notes, never as law text;
* the cover's "amendments not yet in force" list becomes outstanding changes; the text of a draft
  Commonwealth Bill reproduced inside a schedule (``indoc``) is not Queensland law and is not indexed.
"""

from __future__ import annotations

import re
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParsedRef
from ulis.sources.parsers.subnational_common import iso_date as _date
from ulis.sources.parsers.subnational_common import padded_number, safe_id
from ulis.sources.parsers.subnational_common import ws as _ws

PARSER_ID = "qld_quills_xml"
PARSER_VERSION = "1.0.0"
SITE = "https://www.legislation.qld.gov.au"
JURISDICTION = "AU-QLD"

UNITS = {"clause": "section", "subclause": "subsection", "li": "paragraph", "definition": "definition"}
HEADING_CONTAINERS = {
    "part",
    "division",
    "subdivision",
    "clausegroup",
    "codechapter",
    "codechapterdivision",
    "codechaptersubdivision",
}
NOT_TEXT = {"no", "heading", "sourceref", "coverdata", "indoc", "front", "repealed"}
DOCTYPE = re.compile(rb"<!DOCTYPE[^>\[]*(?:\[[^\]]*\])?\s*>", re.S)

# One item of a unit's content in document order: (kind, text or child unit, source element).
# kinds: "t" a line of text, "u" a child unit, "n" a note.
Item = tuple[str, "str | Element", Element]


class QldParseError(ValueError):
    pass


def work_uri(year: str, number: str) -> str:
    return f"/au/qld/act/{year}-{padded_number(number)}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map a Queensland Legislation Act URL to (ULIS work URI, eId)."""
    m = re.match(
        r"^https://www\.legislation\.qld\.gov\.au(?::443)?/view/(?:whole/)?(?:html|xml|pdf)/inforce/"
        r"(?:current|\d{4}-\d{2}-\d{2})/act-(\d{4})-(\d{3})$",
        uri,
    )
    return (work_uri(m.group(1), m.group(2)), None) if m else None


def inline(el: Element) -> str:
    return _ws("".join(el.itertext()))


def _table_text(el: Element) -> str:
    rows = [" | ".join(inline(e) for e in row.iter("entry")) for row in el.iter("row")]
    return "\n".join(r for r in rows if r.strip(" |"))


def _stream(el: Element) -> list[Item]:
    """The content of a unit in document order (headings, numbers and sub-headings excluded)."""
    out: list[Item] = []
    for c in el:
        tag = c.tag
        if tag in NOT_TEXT or tag in HEADING_CONTAINERS:
            continue
        if tag in ("historynote", "note"):
            note = inline(c)
            if note:
                out.append(("n", note, c))
        elif tag in ("block", "list", "deflist"):
            out.extend(_stream(c))
        elif tag == "txt":
            line = inline(c)
            if line:
                out.append(("t", line, c))
        elif tag == "penalty":
            for txt in c.iter("txt"):
                line = inline(txt)
                if line:
                    out.append(("t", line, txt))
        elif tag in UNITS:
            out.append(("u", c, c))
        elif tag == "table":
            out.append(("t", _table_text(c), c))
        else:
            line = inline(c)
            if line:
                out.append(("t", line, c))
    return out


def _label(el: Element) -> str:
    no = el.find("no")
    return inline(no) if no is not None else ""


def _term(el: Element) -> str:
    term = next(el.iter("defterm"), None)
    return inline(term) if term is not None else ""


def _text_of(el: Element) -> str:
    lines: list[str] = []
    for kind, value, _ in _stream(el):
        if kind == "t":
            lines.append(str(value))
        elif kind == "u":
            assert isinstance(value, Element)
            if value.get("status") in ("repealed", "expired") or value.find("repealed") is not None:
                continue  # a repealed unit is its own provision (no text); the parent shows only what is in force
            label = _label(value) if value.tag != "definition" else ""
            body = _text_of(value)
            lines.append(f"{label} {body}".strip() if label else body)
    return "\n".join(line for line in lines if line)


def parse_qld_legislation(content: bytes) -> list[ParsedDocument]:
    try:
        root = fromstring(DOCTYPE.sub(b"", content, count=1), forbid_dtd=True)
    except Exception as exc:
        raise QldParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if root.tag != "act":
        raise QldParseError(f"unsupported root element {root.tag!r} (only Acts are supported)")
    title = _ws(root.get("title") or "")
    year, number = root.get("year.assent") or "", root.get("no") or ""
    if not title or not year.isdigit() or not re.fullmatch(r"\d+[A-Za-z]?", number):
        raise QldParseError("missing title, year or number")
    version_date = _date(root.get("first.valid.date"))
    if version_date is None:
        raise QldParseError("missing first.valid.date")
    uri = work_uri(year, number)
    plain_number = padded_number(number).lstrip("0")

    provisions: list[ParsedProvision] = []
    used: dict[str, int] = {}
    code_titles: list[str] = []

    def eid_of(el: Element, parent_eid: str | None, kind: str) -> str:
        base = safe_id(el.get("id") or "") or f"{parent_eid or 'x'}-{kind}{len(provisions)}"
        n = used.get(base, 0) + 1
        used[base] = n
        return base if n == 1 else f"{base}--{n}"

    def refs_of(el: Element) -> list[ParsedRef]:
        out: list[ParsedRef] = []
        for kind, _, source in _stream(el):
            if kind != "t":
                continue
            for ref in source.iter("legref"):
                target = ref.get("target.doc.id") or ""
                m = re.fullmatch(r"act-(\d{4})-(\d{3})", target)
                if not m or ref.get("check") == "invalid" or ref.get("valid.date") == "as.made":
                    continue
                href = f"{SITE}/view/html/inforce/current/{target}"
                if ref.get("refid"):
                    href = f"{work_uri(m.group(1), m.group(2))}/~{safe_id(ref.get('refid') or '')}"
                out.append(ParsedRef(href, inline(ref), "cites"))
        return out

    def add(el: Element, parent_eid: str | None, num_path: list[str], top_kind: str, context: str | None) -> None:
        schedule = el.tag == "schedule"
        kind = "schedule" if schedule else UNITS[el.tag]
        eid = eid_of(el, parent_eid, kind)
        if el.tag == "definition":
            label = f"def:{_term(el)}"
        elif schedule:
            label = _ws(re.sub(r"^Schedule\b", "", _label(el), flags=re.I))
        else:
            label = _label(el)
        path = [*num_path, label]
        heading_el = el.find("heading")
        heading = inline(heading_el) if heading_el is not None and inline(heading_el) else None
        items = _stream(el)
        notes = [str(v) for k, v, _ in items if k == "n"]
        status = el.get("status")
        repealed = status in ("repealed", "expired") or el.find("repealed") is not None
        children = [v for k, v, _ in items if k == "u"]
        if repealed and status == "expired":
            notes.insert(0, "Expired.")
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
                note=_ws(" ".join(notes))[:2000] or None,
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
        container: Element, context: str | None, top_kind: str, prefix: list[str], direct_clauses: bool = True
    ) -> None:
        """Descend through headings (part, division, chapter...) to the clauses they contain."""
        for child in container:
            if child.tag == "clause" and direct_clauses:
                add(child, None, prefix, top_kind, context)
            elif child.tag in HEADING_CONTAINERS:
                head = child.find("heading")
                here = _ws(f"{_label(child)} {inline(head) if head is not None else ''}")
                walk(child, f"{context} — {here}" if context and here else here or context, top_kind, prefix)

    body = next(root.iter("body"), None)
    if body is None:
        raise QldParseError("missing body")

    preamble = next(root.iter("preamble"), None)
    if preamble is not None and _text_of(preamble):
        provisions.append(
            ParsedProvision(
                eid="preamble",
                kind="preamble",
                num_path=[],
                top_kind="preamble",
                heading=None,
                text=_text_of(preamble),
                parent_eid=None,
                sort_order=0,
                status="in_force",
                valid_from=version_date,
                valid_to=None,
                extent=[JURISDICTION],
                note=None,
                refs=[],
                is_leaf=True,
                has_own_content=False,
            )
        )
    walk(body, None, "section", [])

    schedules = root.find("schedules")
    for sched in schedules if schedules is not None else []:
        if sched.tag != "schedule":
            continue
        head_el = sched.find("heading")
        sched_title = inline(head_el) if head_el is not None else ""
        if next(sched.iter("codechapter"), None) is not None:  # a schedule enacted as a Code
            code_titles.append(re.sub(r"^The\s+", "", sched_title))
            walk(sched, sched_title or None, "code_section", [])
        else:
            add(sched, None, [], "schedule", None)
            sched_no = _ws(re.sub(r"^Schedule\b", "", _label(sched), flags=re.I))
            walk(sched, None, "schedule", [sched_no], direct_clauses=False)  # its own clauses are children

    if not provisions:
        raise QldParseError("no provisions found")

    outstanding: list[dict[str, object]] = []
    status_record = next(root.iter("statusrecord"), None)
    if status_record is not None:
        for ref in status_record.iter("legref"):
            label = inline(ref)
            outstanding.append(
                {
                    "affects_eid": None,
                    "affected_label": None,
                    "by_uri": None,
                    "by_title": label or None,
                    "description": f"Enacted but not yet commenced: {label}",
                }
            )

    ext: dict[str, object] = {
        "state": "Qld",
        "legislation_id": root.get("id") or f"act-{year}-{padded_number(number)}",
        "act_number": f"No. {plain_number} of {year}",
    }
    if code_titles:
        ext["code_title"] = code_titles[0]
    short = next(root.iter("shorttitle"), None)
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type="act",
            jurisdiction_id=JURISDICTION,
            work_uri=uri,
            expression_uri=f"{uri}/en@{version_date.isoformat()}",
            work_title=title,
            title=title,
            short_title=inline(short) if short is not None else title,
            number=f"{plain_number} of {year}",
            work_date=_date(root.get("assent.date")),
            language="en",
            authoritative=False,
            text_origin="official_consolidation",
            consolidation_kind="point_in_time",
            version_date=version_date,
            currency_date=None,
            extent=[JURISDICTION],
            ext_module="au_state",
            ext=ext,
            outstanding_changes=outstanding,
            events=[ParsedEvent("version_change", version_date, None, None, "act/@first.valid.date")],
            provisions=provisions,
            validity_basis="revision_snapshot",
        )
    ]
