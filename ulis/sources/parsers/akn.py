"""Parser for a documented subset of Akoma Ntoso 3.0 (OASIS LegalDocML).

Supported: ``act`` (incl. constitutions, codes, treaties via ``@name``), ``bill`` and
``judgment`` documents; FRBR identification; ``lifecycle`` events; ``temporalData``
periods on provisions; ``ref`` elements; and ULIS metadata in the ``urn:ulis:akn-ext:1``
namespace.  Anything outside the subset is ignored rather than guessed at.

Text extraction is deterministic so that integrity checks can re-derive every stored
text from the raw bytes: inline text is whitespace-normalised; each block element
becomes one line; child provisions are prefixed with their number.
"""

from __future__ import annotations

import json
import re
from datetime import date
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.languages import to_bcp47
from ulis.sources.normalized import (
    ParsedDocument,
    ParsedEvent,
    ParsedJudgmentMeta,
    ParsedParagraph,
    ParsedProvision,
    ParsedRef,
    ParsedTreatyParty,
)

AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
ULIS_NS = "urn:ulis:akn-ext:1"
PARSER_ID = "akn3"
PARSER_VERSION = "1.0.0"

PROVISION_TAGS = {
    "article",
    "section",
    "subsection",
    "paragraph",
    "subparagraph",
    "clause",
    "subclause",
    "point",
    "rule",
}
CONTAINER_TAGS = {"part", "chapter", "title", "division", "book", "subpart", "subchapter", "hcontainer"}
SKIP_IN_TEXT = {"num", "heading", "subheading", "authorialNote"}
EVENT_NAMES = {
    "entryIntoForce": "entry_into_force",
}


class AknParseError(ValueError):
    pass


def _a(tag: str) -> str:
    return f"{{{AKN_NS}}}{tag}"


def _u(tag: str) -> str:
    return f"{{{ULIS_NS}}}{tag}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def inline_text(el: Element) -> str:
    return _ws("".join(el.itertext()))


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _required(el: Element | None, what: str) -> Element:
    if el is None:
        raise AknParseError(f"missing {what}")
    return el


def _attr(el: Element, name: str, what: str) -> str:
    value = el.get(name)
    if not value:
        raise AknParseError(f"missing @{name} on {what}")
    return value


class _Context:
    def __init__(self, meta: Element):
        self.events: dict[str, tuple[date, str, str | None]] = {}
        self.refs: dict[str, str] = {}
        refs_el = meta.find(_a("references"))
        if refs_el is not None:
            for ref in refs_el:
                if ref.get("eId") and ref.get("href"):
                    self.refs[ref.get("eId", "")] = ref.get("href", "")
        lifecycle = meta.find(_a("lifecycle"))
        if lifecycle is not None:
            for ev in lifecycle.findall(_a("eventRef")):
                eid = _attr(ev, "eId", "eventRef")
                kind = (ev.get("refersTo") or "").lstrip("#") or ev.get("type", "generation")
                source = (ev.get("source") or "").lstrip("#")
                affecting = None if source in ("", "original") else self.refs.get(source)
                self.events[eid] = (date.fromisoformat(_attr(ev, "date", "eventRef")), kind, affecting)
        self.periods: dict[str, tuple[date | None, date | None]] = {}
        temporal = meta.find(_a("temporalData"))
        if temporal is not None:
            for group in temporal.findall(_a("temporalGroup")):
                interval = _required(group.find(_a("timeInterval")), "timeInterval")
                start = interval.get("start")
                end = interval.get("end")
                self.periods[_attr(group, "eId", "temporalGroup")] = (
                    self.events[start.lstrip("#")][0] if start else None,
                    self.events[end.lstrip("#")][0] if end else None,
                )

    def period(self, ref: str | None) -> tuple[date | None, date | None]:
        if not ref:
            return (None, None)
        key = ref.lstrip("#")
        if key not in self.periods:
            raise AknParseError(f"unknown period {ref}")
        return self.periods[key]


def _text_of(el: Element) -> str:
    """Exact text of a provision including its child provisions."""
    lines: list[str] = []
    for child in el:
        tag = _local(child.tag)
        if tag in SKIP_IN_TEXT:
            continue
        if tag in PROVISION_TAGS:
            num = child.find(_a("num"))
            body = _text_of(child)
            prefix = inline_text(num) if num is not None else ""
            lines.append(f"{prefix} {body}".strip() if prefix else body)
        elif tag in ("content", "intro", "wrapUp", "wrap"):
            lines.extend(inline_text(block) for block in child)
        else:
            lines.append(inline_text(child))
    return "\n".join(line for line in lines if line)


def _own_refs(el: Element) -> list[ParsedRef]:
    refs: list[ParsedRef] = []
    for child in el:
        if _local(child.tag) in PROVISION_TAGS:
            continue
        for ref in child.iter(_a("ref")):
            refs.append(
                ParsedRef(
                    href=_attr(ref, "href", "ref"),
                    text=inline_text(ref),
                    relation=ref.get(_u("relation"), "cites"),
                )
            )
    return refs


def _ulis_text(meta: Element, tag: str) -> str | None:
    el = meta.find(_u(tag))
    return el.text.strip() if el is not None and el.text else None


def parse_akn(content: bytes) -> ParsedDocument:
    try:
        root = fromstring(content, forbid_dtd=True)
    except Exception as exc:  # defusedxml raises several exception types
        raise AknParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if root.tag != _a("akomaNtoso"):
        raise AknParseError("root element is not akomaNtoso")
    doc = next(iter(root), None)
    if doc is None:
        raise AknParseError("empty akomaNtoso document")
    doc_tag = _local(doc.tag)
    if doc_tag not in ("act", "bill", "judgment"):
        raise AknParseError(f"unsupported document type {doc_tag}")

    meta = _required(doc.find(_a("meta")), "meta")
    ident = _required(meta.find(_a("identification")), "identification")
    work = _required(ident.find(_a("FRBRWork")), "FRBRWork")
    expr = _required(ident.find(_a("FRBRExpression")), "FRBRExpression")
    ulis_meta = _required(meta.find(f"{_a('proprietary')}/{_u('meta')}"), "proprietary ulis:meta")

    work_uri = _attr(_required(work.find(_a("FRBRuri")), "FRBRWork/FRBRuri"), "value", "FRBRuri")
    expression_uri = _attr(_required(expr.find(_a("FRBRuri")), "FRBRExpression/FRBRuri"), "value", "FRBRuri")
    if not expression_uri.startswith(work_uri + "/"):
        raise AknParseError("expression URI is not under the work URI")
    work_date_el = work.find(_a("FRBRdate"))
    work_date = _date(work_date_el.get("date")) if work_date_el is not None else None
    expr_date = date.fromisoformat(
        _attr(_required(expr.find(_a("FRBRdate")), "FRBRExpression/FRBRdate"), "date", "FRBRdate")
    )
    language = to_bcp47(_attr(_required(expr.find(_a("FRBRlanguage")), "FRBRlanguage"), "language", "FRBRlanguage"))
    auth_el = expr.find(_a("FRBRauthoritative"))
    authoritative = auth_el is not None and auth_el.get("value") == "true"
    name_el = work.find(_a("FRBRname"))
    number_el = work.find(_a("FRBRnumber"))
    work_title = name_el.get("value", "") if name_el is not None else ""
    doc_title_el = doc.find(f".//{_a('docTitle')}")
    title = inline_text(doc_title_el) if doc_title_el is not None else work_title

    jurisdiction_id = _ulis_text(ulis_meta, "jurisdiction")
    if not jurisdiction_id:
        raise AknParseError("missing ulis:jurisdiction")
    extent_text = _ulis_text(ulis_meta, "extent")
    extent = extent_text.split() if extent_text else []
    consolidation = ulis_meta.find(_u("consolidation"))
    ext_el = ulis_meta.find(_u("ext"))

    ctx = _Context(meta)
    events = [
        ParsedEvent(
            event_type=EVENT_NAMES.get(kind, kind),
            date=when,
            affecting_uri=affecting,
            description=None,
            locator=f"lifecycle/eventRef[@eId='{eid}']",
        )
        for eid, (when, kind, affecting) in ctx.events.items()
    ]

    parsed = ParsedDocument(
        doc_kind="judgment" if doc_tag == "judgment" else "legislation",
        instrument_type=_ulis_text(ulis_meta, "instrumentType")
        or ("judgment" if doc_tag == "judgment" else doc.get("name", doc_tag)),
        jurisdiction_id=jurisdiction_id,
        work_uri=work_uri,
        expression_uri=expression_uri,
        work_title=work_title,
        title=title,
        short_title=_ulis_text(ulis_meta, "shortTitle"),
        number=number_el.get("value") if number_el is not None else None,
        work_date=work_date,
        language=language,
        authoritative=authoritative,
        text_origin=_ulis_text(ulis_meta, "textOrigin") or "official",
        consolidation_kind=consolidation.get("kind", "unknown") if consolidation is not None else "unknown",
        version_date=expr_date,
        currency_date=_date(consolidation.get("currencyDate")) if consolidation is not None else None,
        extent=extent,
        ext_module=ext_el.get("module") if ext_el is not None else None,
        ext=json.loads(ext_el.text) if ext_el is not None and ext_el.text else {},
        events=events,
        stage=_ulis_text(ulis_meta, "billStage"),
    )
    parsed.outstanding_changes = [
        {
            "affects_eid": (oc.get("affects") or "").lstrip("#") or None,
            "by_uri": oc.get("by"),
            "by_title": oc.get("byTitle"),
            "description": inline_text(oc),
        }
        for oc in ulis_meta.findall(_u("outstandingChange"))
    ]
    parties = ulis_meta.find(_u("treatyParties"))
    if parties is not None:
        parsed.treaty_parties = [
            ParsedTreatyParty(
                party=_attr(p, "jurisdiction", "ulis:party"),
                signed_on=_date(p.get("signed")),
                consent_type=p.get("consentType"),
                consented_on=_date(p.get("consented")),
                entry_into_force_on=_date(p.get("entryIntoForce")),
                withdrawn_on=_date(p.get("withdrawn")),
                reservations=p.get("reservations"),
            )
            for p in parties.findall(_u("party"))
        ]

    if doc_tag == "judgment":
        decision = _ulis_text(ulis_meta, "decisionDate")
        court = _ulis_text(ulis_meta, "court")
        if not decision or not court:
            raise AknParseError("judgment requires ulis:court and ulis:decisionDate")
        parsed.judgment = ParsedJudgmentMeta(
            court_id=court,
            case_name=_ulis_text(ulis_meta, "caseName") or work_title,
            neutral_citation=_ulis_text(ulis_meta, "neutralCitation"),
            decision_date=date.fromisoformat(decision),
            docket=_ulis_text(ulis_meta, "docket"),
        )
        parsed.paragraphs = _parse_paragraphs(_required(doc.find(_a("judgmentBody")), "judgmentBody"))
    else:
        body = _required(doc.find(_a("body")), "body")
        is_bill = doc_tag == "bill" or parsed.instrument_type == "bill"
        parsed.provisions = _parse_provisions(body, ctx, extent, is_bill, parsed.work_date)
    return parsed


def _parse_paragraphs(body: Element) -> list[ParsedParagraph]:
    out: list[ParsedParagraph] = []
    for part in body:
        for para in part.iter(_a("paragraph")):
            num_el = para.find(_a("num"))
            num = inline_text(num_el).strip("[]() .") if num_el is not None else None
            out.append(
                ParsedParagraph(
                    eid=_attr(para, "eId", "paragraph"),
                    num=num,
                    part=_local(part.tag),
                    text=_text_of(para),
                    sort_order=len(out),
                    refs=_own_refs(para),
                )
            )
    return out


def _parse_provisions(
    body: Element, ctx: _Context, doc_extent: list[str], is_bill: bool, work_date: date | None
) -> list[ParsedProvision]:
    out: list[ParsedProvision] = []

    def walk(
        el: Element,
        parent_eid: str | None,
        num_path: list[str],
        top_kind: str | None,
        top_heading: str | None,
        period_ref: str | None,
        status: str | None,
        extent: list[str],
        note: str | None,
    ) -> None:
        for child in el:
            tag = _local(child.tag)
            if tag not in PROVISION_TAGS and tag not in CONTAINER_TAGS:
                continue
            c_period = child.get("period") or period_ref
            c_status = child.get(_u("status")) or status
            c_extent_attr = child.get(_u("extent"))
            c_extent = c_extent_attr.split() if c_extent_attr else extent
            c_note = child.get(_u("note")) or note
            if tag in CONTAINER_TAGS:
                walk(child, parent_eid, num_path, top_kind, top_heading, c_period, c_status, c_extent, c_note)
                continue
            eid = _attr(child, "eId", tag)
            num_el = child.find(_a("num"))
            heading_el = child.find(_a("heading"))
            heading = inline_text(heading_el) if heading_el is not None else None
            path = [*num_path, inline_text(num_el) if num_el is not None else ""]
            kind_top = top_kind or tag
            ctx_heading = top_heading if top_kind else heading
            children = [c for c in child if _local(c.tag) in PROVISION_TAGS]
            own_content = any(_local(c.tag) in ("content", "intro", "wrapUp") for c in child)

            if is_bill:
                final_status, start, end = "proposed", work_date, None
            elif c_status == "prospective":
                final_status, start, end = "prospective", None, None
            elif c_status == "repealed":
                final_status = "repealed"
                start, end = ctx.period(c_period)
            else:
                final_status = "in_force"
                start, end = ctx.period(c_period)

            out.append(
                ParsedProvision(
                    eid=eid,
                    kind=tag,
                    num_path=path,
                    top_kind=kind_top,
                    heading=heading,
                    text="" if final_status == "repealed" else _text_of(child),
                    parent_eid=parent_eid,
                    sort_order=len(out),
                    status=final_status,  # type: ignore[arg-type]
                    valid_from=start,
                    valid_to=end,
                    extent=c_extent,
                    note=c_note,
                    refs=_own_refs(child),
                    is_leaf=not children,
                    has_own_content=own_content and bool(children),
                    context_heading=ctx_heading,
                )
            )
            walk(child, eid, path, kind_top, ctx_heading, c_period, c_status, c_extent, c_note)

    walk(body, None, [], None, None, body.get("period"), body.get(_u("status")), doc_extent, None)

    # A container's text includes its children's text, so the container's current wording
    # only starts when its most recently changed in-force descendant started.
    by_eid = {p.eid: p for p in out}
    for prov in reversed(out):  # children before parents
        parent = by_eid.get(prov.parent_eid) if prov.parent_eid else None
        if (
            parent is not None
            and parent.status == "in_force"
            and prov.status == "in_force"
            and prov.valid_from is not None
            and (parent.valid_from is None or prov.valid_from > parent.valid_from)
        ):
            parent.valid_from = prov.valid_from
    return out
