"""Parser for the Akoma Ntoso served by legislation.gov.uk (The National Archives).

legislation.gov.uk's AKN differs from the plain OASIS subset in ``akn.py``:

* provision validity and territorial extent are attached through ``<restrictions>``
  (``refersTo`` a ``temporalGroup`` or a ``TLCLocation``), inherited down the tree;
* repealed / prospective status is declared in ``<uk:status>`` metadata (and repealed text is
  printed as a row of dots);
* regulations, articles and rules of statutory instruments are ``hcontainer`` elements;
* editorial commentaries (amendment, commencement and extent notes) are ``<note>`` elements
  referenced by ``noteRef``;
* outstanding changes the editors have not yet applied are listed as ``ukm:UnappliedEffect``.

Each document is one point-in-time version of a whole instrument; the connector fetches one
per date on which the instrument changed, which yields its full version history.
"""

from __future__ import annotations

import re
from datetime import date
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParsedRef, ParseSkipped

AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
UK = "https://www.legislation.gov.uk/namespaces/UK-AKN"
UKM = "http://www.legislation.gov.uk/namespaces/metadata"
DC = "http://purl.org/dc/elements/1.1/"
DCT = "http://purl.org/dc/terms/"
PARSER_ID = "legislation_gov_uk_akn"
PARSER_VERSION = "1.1.0"

# legislation.gov.uk type -> (ULIS jurisdiction of the enacting body, instrument type)
TYPES = {
    "ukpga": ("GB", "act"),
    "ukla": ("GB", "act"),
    "asp": ("GB-SCT", "act"),
    "anaw": ("GB-WLS", "act"),
    "asc": ("GB-WLS", "act"),
    "mwa": ("GB-WLS", "measure"),
    "nia": ("GB-NIR", "act"),
    "uksi": ("GB", "statutory_instrument"),
    "ssi": ("GB-SCT", "statutory_instrument"),
    "wsi": ("GB-WLS", "statutory_instrument"),
    "nisr": ("GB-NIR", "statutory_rule"),
}
PROVISION_TAGS = {"section", "subsection", "paragraph", "subparagraph", "level", "article", "rule", "clause", "point"}
NAMED_PROVISIONS = {"regulation", "article", "rule"}  # hcontainer/@name used by statutory instruments
SKIP = {"num", "heading", "subheading", "authorialNote", "noteRef", "del"}
DOTS = re.compile(r"^[.\s…]*$")
# ``/ukpga/1998/42`` and, for Acts before 1963, the regnal-year form ``/ukpga/Eliz2/10-11/58`` (the site's canonical
# identifier). Calendar year + chapter alone is ambiguous for a few of them (Burial Act 1859 and Clerk of the Council
# Act 1859 are both "1859 c. 1"), so the regnal form is the work identity for those Acts.
SESSION = r"(?:\d{4}|[A-Za-z0-9]+/\d+(?:-\d+)*)"


class UkParseError(ValueError):
    pass


def _a(tag: str) -> str:
    return f"{{{AKN}}}{tag}"


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _raw(el: Element) -> str:
    parts = [el.text or ""]
    for child in el:
        if _local(child.tag) not in SKIP:
            parts.append(_raw(child))
        parts.append(child.tail or "")
    return "".join(parts)


def inline_text(el: Element) -> str:
    return _ws(_raw(el))


def _is_provision(el: Element) -> bool:
    tag = _local(el.tag)
    return tag in PROVISION_TAGS or (tag == "hcontainer" and el.get("name") in NAMED_PROVISIONS)


def _kind(el: Element) -> str:
    tag = _local(el.tag)
    return el.get("name", tag) if tag == "hcontainer" else tag


def extent_ids(show_as: str) -> list[str]:
    """Map a legislation.gov.uk extent label (e.g. "E+W", "E+W+S+N.I.") to ULIS jurisdictions."""
    parts = {p.strip() for p in show_as.split("+") if p.strip()}
    if {"E", "W", "S", "N.I."} <= parts:
        return ["GB"]
    out: list[str] = []
    if {"E", "W"} <= parts:
        out.append("GB-EAW")
        parts -= {"E", "W"}
    mapping = {"E": "GB-ENG", "W": "GB-WLS", "S": "GB-SCT", "N.I.": "GB-NIR"}
    out.extend(mapping[p] for p in sorted(parts) if p in mapping)
    return out


def affected_eid(label: str) -> str | None:
    """Best-effort mapping of an affected-provisions label ("s. 3(2)", "Sch. 1 para. 4") to an eId."""
    label = label.strip()
    m = re.fullmatch(r"s\. (\d+[A-Z]*)((?:\(\w+\))*)", label)
    if m:
        subs = re.findall(r"\((\w+)\)", m.group(2))
        return "-".join(["section", m.group(1), *subs])
    m = re.fullmatch(r"Sch\. (\d+[A-Z]*)(?: para\. (\d+[A-Z]*))?", label)
    if m:
        return f"schedule-{m.group(1)}" + (f"-paragraph-{m.group(2)}" if m.group(2) else "")
    m = re.fullmatch(r"(?:reg|art|rule)\. (\d+[A-Z]*)((?:\(\w+\))*)", label)
    if m:
        kind = {"reg": "regulation", "art": "article", "rule": "rule"}[label.split(".")[0]]
        return "-".join([kind, m.group(1), *re.findall(r"\((\w+)\)", m.group(2))])
    return None


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map a legislation.gov.uk URI to (ULIS work URI, eId).

    ``http://www.legislation.gov.uk/id/ukpga/1998/42/section/4/5`` -> (``/gb/ukpga/1998/42``, ``section-4-5``)
    """
    m = re.match(rf"^https?://www\.legislation\.gov\.uk/(?:id/)?([a-z]+)/({SESSION})/(\d+)(?:/([^?#]*))?$", uri)
    if not m or m.group(1) not in TYPES:
        return None
    kind, year, number, rest = m.groups()
    work = f"/gb/{kind}/{year}/{number}"
    rest = (rest or "").strip("/")
    if not rest or re.match(r"^\d{4}-\d{2}-\d{2}$", rest) or rest in ("contents", "enacted", "made"):
        return work, None
    return work, rest.replace("/", "-")


def parse_legislation_gov_uk(content: bytes) -> list[ParsedDocument]:
    try:
        root = fromstring(content, forbid_dtd=True)
    except Exception as exc:
        raise UkParseError(f"not well-formed or unsafe XML: {exc}") from exc
    doc = next(iter(root), None)
    if root.tag != _a("akomaNtoso") or doc is None:
        raise UkParseError("not an Akoma Ntoso document")
    meta = doc.find(_a("meta"))
    if meta is None:
        raise UkParseError("missing meta")
    prop = meta.find(_a("proprietary"))
    if prop is None:
        raise UkParseError("missing legislation.gov.uk proprietary metadata")
    identifier = (prop.findtext(f"{{{DC}}}identifier") or "").strip()
    m = re.match(rf"^https?://www\.legislation\.gov\.uk/([a-z]+)/({SESSION})/(\d+)(?:/|$)", identifier)
    if not m or m.group(1) not in TYPES:
        raise UkParseError(f"unsupported legislation.gov.uk identifier {identifier!r}")
    leg_type, year, number = m.groups()  # ``year`` is "1998" or, before 1963, "Eliz2/10-11"
    jurisdiction, instrument_type = TYPES[leg_type]
    work_uri = f"/gb/{leg_type}/{year}/{number}"

    expr_date_el = meta.find(f"{_a('identification')}/{_a('FRBRExpression')}/{_a('FRBRdate')}")
    if expr_date_el is None or not expr_date_el.get("date"):
        raise UkParseError("missing expression date")
    version_date = date.fromisoformat(expr_date_el.get("date", "")[:10])
    modified = prop.findtext(f"{{{DC}}}modified")
    title = _ws(prop.findtext(f"{{{DC}}}title") or "")
    # Element truthiness depends on children, so compare with None explicitly.
    primary = prop.find(f"{{{UKM}}}PrimaryMetadata")
    if primary is None:
        primary = prop.find(f"{{{UKM}}}SecondaryMetadata")
    enacted_el = None
    status_el = None
    if primary is not None:
        enacted_el = primary.find(f"{{{UKM}}}EnactmentDate")
        if enacted_el is None:
            enacted_el = primary.find(f"{{{UKM}}}Made")
        status_el = primary.find(f"{{{UKM}}}DocumentClassification/{{{UKM}}}DocumentStatus")
    work_date = (
        date.fromisoformat(enacted_el.get("Date", "")) if enacted_el is not None and enacted_el.get("Date") else None
    )
    revised = status_el is not None and status_el.get("Value") == "revised"
    calendar_year = year if year.isdigit() else None
    year_el = primary.find(f"{{{UKM}}}Year") if primary is not None else None
    if year_el is not None and (year_el.get("Value") or "").isdigit():
        calendar_year = year_el.get("Value")
    if calendar_year is None and work_date is not None:
        calendar_year = str(work_date.year)
    if calendar_year is None:
        raise UkParseError("missing year")
    frbr_name = meta.find(f"{_a('identification')}/{_a('FRBRWork')}/{_a('FRBRname')}")

    # --- events and periods
    events: dict[str, date] = {}
    revision_dates: list[date] = []
    lifecycle = meta.find(_a("lifecycle"))
    if lifecycle is not None:
        for ev in lifecycle.findall(_a("eventRef")):
            if ev.get("eId") and ev.get("date"):
                events[ev.get("eId", "")] = date.fromisoformat(ev.get("date", "")[:10])
                if ev.get("refersTo") != "#enactment":
                    revision_dates.append(events[ev.get("eId", "")])
    # A period with an end but no start ("period-to-2007-01-15") covers the text from the start of
    # the revised data: the first lifecycle date after enactment (the 1991-02-01 basedate for older
    # Acts, enactment for later ones).
    history_base = min(revision_dates, default=None)
    periods: dict[str, tuple[date | None, date | None]] = {}
    temporal = meta.find(_a("temporalData"))
    if temporal is not None:
        for group in temporal.findall(_a("temporalGroup")):
            interval = group.find(_a("timeInterval"))
            if interval is None:
                continue
            start = (interval.get("start") or "").lstrip("#")
            end = (interval.get("end") or "").lstrip("#")
            periods[group.get("eId", "")] = (events.get(start), events.get(end))
    locations: dict[str, list[str]] = {}
    refs_el = meta.find(_a("references"))
    if refs_el is not None:
        for loc in refs_el.findall(_a("TLCLocation")):
            locations[loc.get("eId", "")] = extent_ids(loc.get("showAs", ""))
    period_of: dict[str, str] = {}
    extent_of: dict[str, list[str]] = {}
    default_period: str | None = None
    default_extent: list[str] = []
    restrictions = next(meta.iter(_a("restrictions")), None)  # inside <analysis>
    if restrictions is not None:
        for r in restrictions.findall(_a("restriction")):
            target = (r.get("href") or "").lstrip("#")
            ref = (r.get("refersTo") or "").lstrip("#")
            if ref in periods:
                if target:
                    period_of[target] = ref
                else:
                    default_period = ref
            elif ref in locations:
                if target:
                    extent_of[target] = locations[ref]
                else:
                    default_extent = locations[ref]
    status_of: dict[str, str] = {}
    for st in prop.iter(f"{{{UK}}}status"):
        status_of[(st.get("href") or "").lstrip("#")] = (st.get("refersTo") or "").lstrip("#").removeprefix("status-")
    for st in meta.iter(f"{{{UK}}}status"):
        status_of[(st.get("href") or "").lstrip("#")] = (st.get("refersTo") or "").lstrip("#").removeprefix("status-")

    # --- commentaries
    notes: dict[str, str] = {}
    notes_el = meta.find(_a("notes"))
    if notes_el is not None:
        for note in notes_el.findall(_a("note")):
            text = inline_text(note)
            if note.get("eId") and text:
                notes[note.get("eId", "")] = text[:400]

    # --- outstanding changes
    outstanding: list[dict[str, object]] = []
    for eff in prop.iter(f"{{{UKM}}}UnappliedEffect"):
        affecting_title = eff.findtext(f"{{{UKM}}}AffectingTitle") or ""
        outstanding.append(
            {
                "affects_eid": affected_eid(eff.get("AffectedProvisions", "")),
                "affected_label": eff.get("AffectedProvisions"),
                "by_uri": eff.get("AffectingURI"),
                "by_title": _ws(affecting_title) or eff.get("AffectingURI"),
                "description": _ws(f"{eff.get('Type', '')} {eff.get('AffectingProvisions', '')}"),
            }
        )

    body = doc.find(_a("body"))
    if body is None:
        raise UkParseError("missing body")
    provisions: list[ParsedProvision] = []

    def own_notes(el: Element) -> list[str]:
        out: list[str] = []
        for child in el:
            if _is_provision(child):
                continue
            for ref in [child, *child.iter()]:
                if _local(ref.tag) == "noteRef":
                    text = notes.get((ref.get("href") or "").lstrip("#"))
                    if text and text not in out:
                        out.append(text)
        return out

    def own_refs(el: Element) -> list[ParsedRef]:
        out: list[ParsedRef] = []
        for child in el:
            if _is_provision(child):
                continue
            for ref in child.iter(_a("ref")):
                href = ref.get("href") or ""
                if "legislation.gov.uk" in href:
                    out.append(ParsedRef(href=href, text=inline_text(ref), relation="cites"))
        return out

    def text_of(el: Element) -> str:
        lines: list[str] = []
        for child in el:
            tag = _local(child.tag)
            if tag in SKIP:
                continue
            if _is_provision(child):
                if never_applied(child):
                    continue
                num = child.find(_a("num"))
                body_text = text_of(child)
                if DOTS.match(body_text):  # repealed: printed as dots in some versions, omitted in others
                    body_text = ""
                prefix = inline_text(num) if num is not None else ""
                lines.append(f"{prefix} {body_text}".strip() if prefix else body_text)
            elif tag in ("content", "intro", "wrapUp"):
                lines.extend(t for t in (inline_text(block) for block in child) if t)
            else:
                text = inline_text(child) if tag != "hcontainer" else text_of(child)
                if text:
                    lines.append(text)
        return "\n".join(line for line in lines if line)

    def never_applied(el: Element) -> bool:
        """An alternative wording whose own period is empty (e.g. replaced on the day it was due
        to commence) never applied."""
        start, end = periods.get(period_of.get(el.get("eId") or "", ""), (None, None))
        return start is not None and end is not None and end <= start

    def walk(
        el: Element,
        parent_eid: str | None,
        num_path: list[str],
        top_kind: str | None,
        period: str | None,
        extent: list[str],
        status: str | None,
    ) -> None:
        for child in el:
            eid = child.get("eId") or ""
            c_period = period_of.get(eid, period)
            c_extent = extent_of.get(eid, extent)
            c_status = status_of.get(eid, status)
            if not _is_provision(child):
                if _local(child.tag) in ("hcontainer", "part", "chapter", "title", "division"):
                    walk(child, parent_eid, num_path, top_kind, c_period, c_extent, c_status)
                continue
            if not eid or never_applied(child):
                continue
            num_el = child.find(_a("num"))
            num = inline_text(num_el).rstrip(".") if num_el is not None else ""
            heading_el = child.find(_a("heading"))
            heading = inline_text(heading_el) if heading_el is not None else None
            path = [*num_path, num]
            kind = _kind(child)
            text = text_of(child)
            start, end = periods.get(c_period, (None, None)) if c_period else (None, None)
            if start is None and end is not None:
                start = history_base
            if c_status == "prospective":
                state, start, end = "prospective", None, None
            elif c_status == "repealed" or DOTS.match(text):
                state = "repealed"
                text = ""
            else:
                state = "in_force"
            children = [c for c in child if _is_provision(c)]
            own = any(_local(c.tag) in ("content", "intro", "wrapUp") for c in child)
            note_texts = own_notes(child)
            provisions.append(
                ParsedProvision(
                    eid=eid,
                    kind=kind,
                    num_path=path,
                    top_kind=top_kind or kind,
                    heading=heading,
                    text=text,
                    parent_eid=parent_eid,
                    sort_order=len(provisions),
                    status=state,  # type: ignore[arg-type]
                    valid_from=start,
                    valid_to=end,
                    extent=c_extent,
                    note=" | ".join(note_texts[:5]) or None,
                    refs=own_refs(child),
                    is_leaf=not children,
                    has_own_content=own and bool(children),
                )
            )
            walk(child, eid, path, top_kind or kind, c_period, c_extent, c_status)

    walk(body, None, [], None, default_period, default_extent, None)
    if not provisions:
        # Well formed, but nothing to store (for example an Act repealed in full whose text has been removed).
        raise ParseSkipped(f"{identifier}: no provisions in the {version_date.isoformat()} version")
    # A container's text includes its children, so its wording is current only from the latest
    # start, and until the earliest end, found anywhere in its subtree.
    by_eid = {p.eid: p for p in provisions}
    for prov in reversed(provisions):
        parent = by_eid.get(prov.parent_eid or "")
        if parent is None or parent.status == "prospective" or prov.status == "prospective":
            continue
        if prov.valid_from and (parent.valid_from is None or prov.valid_from > parent.valid_from):
            parent.valid_from = prov.valid_from
        if prov.valid_to and (parent.valid_to is None or prov.valid_to < parent.valid_to):
            parent.valid_to = prov.valid_to
    for prov in provisions:
        if prov.valid_from and prov.valid_to and prov.valid_to <= prov.valid_from:
            prov.record_version = False  # children's wordings never applied together
        elif prov.valid_from and prov.valid_from > version_date and prov.status != "prospective":
            # A wording dated after this point in time is a forecast: it can still change before
            # it takes effect (and often does). The point-in-time version for that date records
            # what actually applied; here it is only reported as a scheduled change.
            prov.record_version = False
            parent = by_eid.get(prov.parent_eid or "")
            if parent is None or parent.record_version:
                outstanding.append(
                    {
                        "affects_eid": prov.eid,
                        "affected_label": None,
                        "by_uri": None,
                        "by_title": None,
                        "description": f"Scheduled change on {prov.valid_from.isoformat()} (as published at "
                        f"{version_date.isoformat()}); the new wording is not shown before that date.",
                    }
                )

    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=instrument_type,
            jurisdiction_id=jurisdiction,
            work_uri=work_uri,
            expression_uri=f"{work_uri}/eng@{version_date.isoformat()}",
            work_title=title,
            title=f"{title} (as at {version_date.isoformat()})" if revised else title,
            short_title=title,
            number=number,
            work_date=work_date,
            language="en",
            authoritative=False,
            text_origin="official_revised" if revised else "official_conversion",
            consolidation_kind="point_in_time" if revised else "as_made",
            version_date=version_date,
            currency_date=date.fromisoformat(modified[:10]) if modified else None,
            extent=default_extent,
            ext_module="gb",
            ext={
                "leg_type": leg_type,
                "year": int(calendar_year),
                "number": number,
                "chapter": frbr_name.get("value") if frbr_name is not None else None,
            },
            outstanding_changes=outstanding,
            events=[
                ParsedEvent("version_change", d, None, None, f"lifecycle/eventRef[@date='{d}']")
                for d in sorted(set(events.values()))
            ],
            provisions=provisions,
            validity_basis="source_periods",
        )
    ]
