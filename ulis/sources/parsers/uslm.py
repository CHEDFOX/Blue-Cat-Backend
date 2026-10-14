"""Parser for United States Legislative Markup (USLM), the US GPO's XML format.

Supported document types and how their time semantics are recorded:

* ``pLaw`` (public law, as enacted): one expression dated on approval. Provisions are
  in force from the approval date under the default rule that an Act takes effect on
  enactment unless it provides otherwise (``validity_basis = enactment_date_presumed``).
* ``statuteCompilation`` (an Act as amended, compiled by the House Office of the Legislative
  Counsel): an unofficial consolidated expression of the same Act, current through the
  stated public law (``validity_basis = attested_at_edition``).
* ``cfrDoc`` (Code of Federal Regulations annual edition volume): one instrument per CFR
  part, attested as in force on the edition's revision date (``attested_at_edition``).
* ``bill`` / ``resolution`` (one legislative stage): proposed text dated by the latest action
  in the preface; never law.
* ``uscDoc`` (one title of the United States Code, as published by the Office of the Law Revision
  Counsel for a release point): one instrument per title, attested as in force at the release point's
  date (``attested_at_edition``). OLRC files use the namespace ``http://xml.house.gov/schemas/uslm/1.0``
  and may be given as the downloaded ``usc<NN>.xml`` zip. Placeholder entries (omitted, transferred,
  renumbered, vacant) carry no law text and are not provisions; repealed ones are recorded as repealed.

Not yet supported (rejected with an explicit error): Federal Register issues (``frDoc``),
Statutes at Large volumes, engrossed amendments.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from datetime import date
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParsedRef

NS = "http://schemas.gpo.gov/xml/uslm"
HOUSE_NS = "http://xml.house.gov/schemas/uslm/1.0"  # the OLRC's United States Code files
DC = "http://purl.org/dc/elements/1.1/"
PARSER_ID = "uslm"
PARSER_VERSION = "1.2.0"
MAX_XML_BYTES = 120 * 1024 * 1024  # largest XML member read from a zip (title 38 is 19 MB)

# The OLRC's XML says only "Online@<congress>-<law>" (current through that Public Law). The date of the
# release point is stated on https://uscode.house.gov/download.html ("Public Law 119-120 (10/07/2026)").
# A release point that is not listed here is rejected rather than dated by a guess.
USC_RELEASE_POINT_DATES: dict[str, date] = {"119-120": date(2026, 10, 7)}

PROVISION_PREFIX = {
    "section": "sec",
    "subsection": "subsec",
    "paragraph": "para",
    "subparagraph": "subpara",
    "clause": "cl",
    "subclause": "subcl",
    "item": "item",
    "subitem": "subitem",
    "subsubitem": "subsubitem",
    "appropriations": "approp",
}
CONTAINERS = {
    "title",
    "subtitle",
    "chapter",
    "subchapter",
    "part",
    "subpart",
    "division",
    "subdivision",
    "level",
    "group",
    "subgroup",
}
# Quoted material is text of the provision that quotes it; its inner structure is never walked.
QUOTED = {"quotedContent", "quotedText"}
# Annotations and layout that are not part of the legal text.
SKIP = {
    "num",
    "heading",
    "subheading",
    "sidenote",
    "page",
    "footnote",
    "editorialNote",
    "explanationNote",
    "source",
    "note",
    "notes",
    "endMarker",
    "img",
    "figure",
    "ear",
    "toc",
    "layout",
}
# The Code's source credit is kept as a note, not as text of the section.
USC_SKIP = SKIP | {"sourceCredit"}
BILL_TYPES = {
    "House Bill": "hr",
    "Senate Bill": "s",
    "House Joint Resolution": "hjres",
    "Senate Joint Resolution": "sjres",
    "House Concurrent Resolution": "hconres",
    "Senate Concurrent Resolution": "sconres",
    "House Simple Resolution": "hres",
    "Senate Simple Resolution": "sres",
}
MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        start=1,
    )
}


class UslmParseError(ValueError):
    pass


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("­", "")).strip()


def _raw_text(el: Element) -> str:
    parts = [el.text or ""]
    for child in el:
        if _local(child.tag) not in SKIP:
            parts.append(_raw_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def inline_text(el: Element) -> str:
    return _ws(_raw_text(el))


def _find_text(el: Element, path: str) -> str | None:
    found = el.find(path, {"u": NS, "dc": DC})
    return inline_text(found) if found is not None else None


def _parse_us_date(text: str) -> date | None:
    m = re.search(r"([A-Z][a-z]+)\.?\s+(\d{1,2}),\s+(\d{4})", text)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))


def _num_value(el: Element) -> tuple[str, str]:
    num = el.find(_q("num"))
    if num is None:
        return "", ""
    value = (num.get("value") or "").strip()
    display = inline_text(num)
    printed = re.sub(r"^(?:SECTION|SEC\.|Sec\.|§+)\s*", "", display).strip(" .()")
    # The printed number wins when it is a more specific form of @value (GPO data has
    # "§ 226.5a" tagged with value="226.5").
    if not value or (printed.startswith(value) and printed != value and re.fullmatch(r"[\w.-]+", printed)):
        value = printed
    return value, display


class _Builder:
    """Walks a USLM subtree and emits provisions with deterministic eIds and texts."""

    def __init__(self, extent: list[str], status: str, valid_from: date | None, usc: bool = False):
        self.extent = extent
        self.status = status
        self.valid_from = valid_from
        # United States Code mode: per-entry ``status`` attributes and source credits are honoured.
        self.usc = usc
        self.skip = USC_SKIP if usc else SKIP
        self.provisions: list[ParsedProvision] = []
        self.eids: Counter[str] = Counter()
        self.approp_counter = 0
        self.unnumbered = 0

    def text_of(self, el: Element) -> str:
        lines: list[str] = []
        for child in el:
            tag = _local(child.tag)
            if tag in self.skip:
                continue
            if tag in PROVISION_PREFIX:
                _, display = _num_value(child)
                heading = child.find(_q("heading"))
                head = inline_text(heading) if heading is not None else ""
                body = self.text_of(child)
                lines.append(_ws(" ".join(p for p in (display, head, body) if p)))
            elif tag in CONTAINERS:
                lines.append(self.text_of(child))
            else:
                text = inline_text(child)
                if text:
                    lines.append(text)
        return "\n".join(line for line in lines if line)

    def refs_of(self, el: Element, source_relation: str | None = None) -> list[ParsedRef]:
        refs: list[ParsedRef] = []
        for child in el:
            tag = _local(child.tag)
            if tag in PROVISION_PREFIX or tag in CONTAINERS:
                continue
            if tag == "source" or (self.usc and tag == "sourceCredit"):
                relation: str | None = source_relation
            else:
                relation = "cites" if tag not in SKIP else None
            if relation is None:
                continue
            for ref in child.iter(_q("ref")):
                href = ref.get("href")
                if href and href.startswith("/us/"):
                    refs.append(ParsedRef(href=href, text=inline_text(ref), relation=relation))
        return refs

    def walk(
        self,
        el: Element,
        parent_eid: str | None,
        num_path: list[str],
        top_kind: str | None,
        top_heading: str | None,
        prefix: str,
    ) -> None:
        for child in el:
            tag = _local(child.tag)
            if tag in CONTAINERS:
                if parent_eid is not None:
                    # A title/chapter/... inside a section is quoted material (e.g. a new title of the
                    # US Code enacted "as follows"): it is text of that section, not part of this Act.
                    continue
                value, _ = _num_value(child)
                child_prefix = f"{prefix}div_{value}__" if tag == "division" and value else prefix
                self.walk(child, parent_eid, num_path, top_kind, top_heading, child_prefix)
                continue
            if tag not in PROVISION_PREFIX or tag in QUOTED:
                continue
            entry_status = (child.get("status") or "") if self.usc else ""
            if entry_status and entry_status != "repealed":
                # omitted, transferred, renumbered, vacant ...: a pointer in the Code, not law text.
                continue
            value, _ = _num_value(child)
            heading_el = child.find(_q("heading"))
            heading = inline_text(heading_el) if heading_el is not None else None
            if tag == "appropriations":
                self.approp_counter += 1
                local_id = f"approp_{self.approp_counter}"
                path = [*num_path]
            elif not value:
                self.unnumbered += 1
                local_id = f"{PROVISION_PREFIX[tag]}_unnumbered_{self.unnumbered}"
                path = [*num_path, ""]
            else:
                local_id = f"{PROVISION_PREFIX[tag]}_{re.sub(r'[^A-Za-z0-9.-]', '_', value)}"
                path = [*num_path, value]
            eid = f"{parent_eid}__{local_id}" if parent_eid else f"{prefix}{local_id}"
            self.eids[eid] += 1
            ambiguous = self.eids[eid] > 1
            if ambiguous:
                eid = f"{eid}_dup{self.eids[eid]}"
            children = [c for c in child if _local(c.tag) in PROVISION_PREFIX]
            own = any(_local(c.tag) not in PROVISION_PREFIX and _local(c.tag) not in self.skip for c in child)
            source = child.find(_q("source"))
            notes = [f"Source note: {inline_text(source)}"] if source is not None else []
            credit = child.find(_q("sourceCredit")) if self.usc else None
            if credit is not None:
                notes.append(f"Source credit: {inline_text(credit)}")
            if self.usc:
                notes.extend(f"Footnote: {f}" for f in _footnotes(child))
            repealed = entry_status == "repealed"
            body = self.text_of(child)
            if repealed:
                # A repealed entry has no text; what the Code prints in its place (the repeal citation) is the note.
                shown = body or heading or ""
                notes.insert(0, "Status in source: repealed." + (f" The Code prints: {shown}" if shown else ""))
            if ambiguous:
                notes.append(
                    "Ambiguous pinpoint: this number repeats at the same level in the source "
                    "(e.g. under separate defined terms); identify the passage by its text."
                )
            note = " ".join(notes) or None
            kind_top = top_kind or tag
            ctx_heading = top_heading if top_kind else heading
            self.provisions.append(
                ParsedProvision(
                    eid=eid,
                    kind=tag,
                    num_path=path,
                    top_kind=kind_top,
                    heading=heading,
                    text="" if repealed else body,
                    parent_eid=parent_eid,
                    sort_order=len(self.provisions),
                    status="repealed" if repealed else self.status,  # type: ignore[arg-type]
                    valid_from=self.valid_from,
                    valid_to=None,
                    extent=self.extent,
                    note=note,
                    refs=self.refs_of(child, source_relation="source_act" if self.usc else "source_rule_document"),
                    is_leaf=not children,
                    has_own_content=own and bool(children),
                    context_heading=ctx_heading,
                )
            )
            self.walk(child, eid, path, kind_top, ctx_heading, prefix)


def _xml_of_usc_zip(content: bytes) -> bytes:
    """The single ``usc<NN>.xml`` of an OLRC per-title download, with a bound on its size."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) != 1 or not re.fullmatch(r"usc\d{2}\.xml", members[0].filename):
                raise UslmParseError(f"expected a single usc<NN>.xml, found {[m.filename for m in members][:5]}")
            if members[0].file_size > MAX_XML_BYTES:
                raise UslmParseError(f"{members[0].filename} is larger than {MAX_XML_BYTES} bytes")
            with archive.open(members[0]) as handle:
                data = handle.read(MAX_XML_BYTES + 1)
    except zipfile.BadZipFile as exc:
        raise UslmParseError(f"not a readable zip archive: {exc}") from exc
    if len(data) > MAX_XML_BYTES:
        raise UslmParseError("XML member is larger than declared")
    return data


def _to_gpo_namespace(root: Element) -> None:
    """Re-tag an OLRC (``xml.house.gov``) tree into the GPO USLM namespace the rest of the parser uses."""
    old = f"{{{HOUSE_NS}}}"
    for el in root.iter():
        if isinstance(el.tag, str) and el.tag.startswith(old):
            el.tag = f"{{{NS}}}" + el.tag[len(old) :]


def parse_uslm(content: bytes) -> list[ParsedDocument]:
    if content.startswith(b"PK\x03\x04"):
        content = _xml_of_usc_zip(content)
    try:
        root = fromstring(content, forbid_dtd=True)
    except Exception as exc:
        raise UslmParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if isinstance(root.tag, str) and root.tag.startswith(f"{{{HOUSE_NS}}}"):
        _to_gpo_namespace(root)
    if not isinstance(root.tag, str) or not root.tag.startswith(f"{{{NS}}}"):
        raise UslmParseError("root element is not in the USLM namespace")
    kind = _local(root.tag)
    if kind == "uscDoc":
        return [_usc_title(root)]
    if kind == "pLaw":
        return [_public_law(root)]
    if kind == "statuteCompilation":
        return [_compilation(root)]
    if kind == "cfrDoc":
        return _cfr(root)
    if kind in ("bill", "resolution"):
        return [_bill(root, kind)]
    raise UslmParseError(f"USLM document type {kind!r} is not supported yet")


def _main(root: Element) -> Element:
    main = root.find(_q("main"))
    if main is None:
        raise UslmParseError("missing main")
    return main


def _public_law(root: Element) -> ParsedDocument:
    meta = root.find(_q("meta"))
    if meta is None:
        raise UslmParseError("missing meta")
    congress = _find_text(meta, "u:congress")
    citable = [inline_text(c) for c in meta.findall(_q("citableAs"))]
    pl = next((c for c in citable if c.startswith("Public Law")), None)
    m = re.match(r"Public Law (\d+)[–-](\d+)", pl or "")
    if not m or not congress:
        raise UslmParseError("public law number not found")
    number = m.group(2)
    approved = None
    for action in root.iter(_q("actionDescription")):
        approved = _parse_us_date(inline_text(action))
        if approved:
            break
    if approved is None:
        raise UslmParseError("approval date not found")
    main = _main(root)
    short = main.find(f".//{_q('shortTitle')}")
    official = _find_text(main, "u:longTitle/u:officialTitle") or ""
    work_uri = f"/us/pl/{congress}/{number}"
    builder = _Builder(["US"], "in_force", approved)
    builder.walk(main, None, [], None, None, "")
    title = inline_text(short).strip('“”"') if short is not None else f"Public Law {congress}-{number}"
    return ParsedDocument(
        doc_kind="legislation",
        instrument_type="act",
        jurisdiction_id="US",
        work_uri=work_uri,
        expression_uri=f"{work_uri}/en@{approved.isoformat()}",
        work_title=title,
        title=f"{title} (Public Law {congress}-{number}, as enacted)",
        short_title=title if short is not None else None,
        number=f"{congress}-{number}",
        work_date=approved,
        language="en",
        authoritative=False,
        text_origin="official_conversion",
        consolidation_kind="as_enacted",
        version_date=approved,
        currency_date=None,
        extent=["US"],
        ext_module="us",
        ext={
            "congress": int(congress),
            "number": number,
            "statutes_at_large": [c for c in citable if "Stat." in c],
            "official_title": official or None,
            "public_private": _find_text(meta, "u:publicPrivate"),
        },
        events=[ParsedEvent("enactment", approved, None, "Approved by the President", "main/action")],
        provisions=builder.provisions,
        validity_basis="enactment_date_presumed",
    )


def _compilation(root: Element) -> ParsedDocument:
    meta = root.find(_q("meta"))
    preface = root.find(_q("preface"))
    if meta is None or preface is None:
        raise UslmParseError("missing meta or preface")
    congress = _find_text(meta, "u:congress")
    number = _find_text(meta, "u:docNumber")
    approved_text = _find_text(meta, "u:approvedDate")
    edition = preface.find(_q("editionNote"))
    current_date = None
    current_through = None
    if edition is not None:
        d = edition.find(_q("date"))
        current_date = date.fromisoformat(d.get("date", "")) if d is not None and d.get("date") else None
        current_through = _find_text(edition, "u:currentThroughPublicLaw")
    if not congress or not number or not approved_text:
        raise UslmParseError("compilation lacks congress, number or approval date")
    approved = date.fromisoformat(approved_text)
    currency = current_date or approved
    title = (_find_text(meta, "u:containsShortTitle") or _find_text(meta, "dc:title") or "").strip()
    work_uri = f"/us/pl/{congress}/{number}"
    builder = _Builder(["US"], "in_force", None)
    builder.walk(_main(root), None, [], None, None, "")
    return ParsedDocument(
        doc_kind="legislation",
        instrument_type="act",
        jurisdiction_id="US",
        work_uri=work_uri,
        expression_uri=f"{work_uri}/en@{currency.isoformat()}/compilation",
        work_title=title,
        title=f"{title} (compilation as amended through {current_through or 'enactment'})",
        short_title=title,
        number=f"{congress}-{number}",
        work_date=approved,
        language="en",
        authoritative=False,
        text_origin="unofficial_compilation",
        consolidation_kind="compilation",
        version_date=currency,
        currency_date=currency,
        extent=["US"],
        ext_module="us",
        ext={"congress": int(congress), "number": number, "compiled_through": current_through},
        events=[ParsedEvent("enactment", approved, None, None, "meta/approvedDate")],
        provisions=builder.provisions,
        validity_basis="attested_at_edition",
    )


def _cfr(root: Element) -> list[ParsedDocument]:
    meta = root.find(_q("meta"))
    if meta is None:
        raise UslmParseError("missing meta")
    title_no = _find_text(meta, "u:docNumber")
    revised = _find_text(meta, "u:docReleasePoint")
    if not title_no or not revised:
        raise UslmParseError("CFR title number or revision date missing")
    edition = date.fromisoformat(revised)
    docs: list[ParsedDocument] = []
    agency = None
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "chapter":
            agency = _find_text(el, "u:heading") or agency
        if tag != "part" or not (el.get("identifier") or "").startswith("/us/cfr/"):
            continue
        part_no, _ = _num_value(el)
        heading = (_find_text(el, "u:heading") or "").strip()
        work_uri = el.get("identifier", "")
        builder = _Builder(["US"], "in_force", None)
        builder.walk(el, None, [], None, None, "")
        if not builder.provisions:
            continue
        name = f"{title_no} CFR Part {part_no}"
        docs.append(
            ParsedDocument(
                doc_kind="legislation",
                instrument_type="regulation",
                jurisdiction_id="US",
                work_uri=work_uri,
                expression_uri=f"{work_uri}/en@{edition.isoformat()}",
                work_title=f"{name}: {heading.title()}",
                title=f"{name}: {heading.title()} (CFR edition revised as of {edition.isoformat()})",
                short_title=name,
                number=part_no,
                work_date=None,
                language="en",
                authoritative=False,
                text_origin="official_conversion",
                consolidation_kind="cfr_annual_edition",
                version_date=edition,
                currency_date=edition,
                extent=["US"],
                ext_module="us",
                ext={"cfr_title": int(title_no), "cfr_part": part_no, "agency": agency},
                provisions=builder.provisions,
                validity_basis="attested_at_edition",
            )
        )
    if not docs:
        raise UslmParseError("no CFR parts found")
    return docs


_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to"}


def _title_case(heading: str) -> str:
    """'CRIMES AND CRIMINAL PROCEDURE' -> 'Crimes and Criminal Procedure' (a display label, not law text)."""
    words = heading.lower().split()
    return " ".join(w if i and w in _SMALL_WORDS else w[:1].upper() + w[1:] for i, w in enumerate(words))


def _drop_footnote_markers(root: Element) -> None:
    """Remove footnote reference marks (``<ref class="footnoteRef">1</ref>``) so they do not become part of
    numbers, headings or text (``§ 3598.`` + mark ``1`` would read ``3598.1``). The footnote bodies stay
    available as notes of the provision (see :func:`_footnotes`)."""
    for parent in list(root.iter()):
        previous: Element | None = None
        for child in list(parent):
            if _local(child.tag) == "ref" and "footnoteRef" in (child.get("class") or "").split():
                if child.tail:
                    if previous is None:
                        parent.text = (parent.text or "") + child.tail
                    else:
                        previous.tail = (previous.tail or "") + child.tail
                parent.remove(child)
            else:
                previous = child


def _footnotes(el: Element) -> list[str]:
    """Texts of the footnotes attached to this provision itself (not to the provisions inside it)."""
    found: list[str] = []
    for child in el:
        tag = _local(child.tag)
        if tag in PROVISION_PREFIX or tag in CONTAINERS:
            continue
        if tag == "note" and child.get("type") == "footnote":
            found.append(inline_text(child))
        else:
            found.extend(_footnotes(child))
    return [f for f in found if f]


def _usc_title(root: Element) -> ParsedDocument:
    meta = root.find(_q("meta"))
    if meta is None:
        raise UslmParseError("missing meta")
    _drop_footnote_markers(root)
    if _find_text(meta, "dc:type") != "USCTitle":
        raise UslmParseError(f"not a main title of the United States Code (dc:type {_find_text(meta, 'dc:type')!r})")
    number = _find_text(meta, "u:docNumber")
    m = re.fullmatch(r"Online@(\d+-\d+)", _find_text(meta, "u:docPublicationName") or "")
    identifier = root.get("identifier") or ""
    if not number or not number.isdigit() or not m or identifier != f"/us/usc/t{number}":
        raise UslmParseError("United States Code title number, release point or identifier not found")
    release_point = m.group(1)
    released = USC_RELEASE_POINT_DATES.get(release_point)
    if released is None:
        raise UslmParseError(
            f"release point {release_point} has no recorded date; take it from https://uscode.house.gov/download.html "
            "and add it to USC_RELEASE_POINT_DATES"
        )
    positive = {"yes": True, "no": False}.get((_find_text(meta, "u:property[@role='is-positive-law']") or "").lower())
    main = _main(root)
    heading = _find_text(main, "u:title/u:heading")
    if not heading:
        raise UslmParseError("title heading not found")
    name = _title_case(heading)
    through = f"Public Law {release_point}"
    status = {
        True: "positive-law title",
        False: "not enacted as positive law - prima facie evidence only, 1 U.S.C. 204",
        None: "positive-law status not stated",
    }[positive]
    builder = _Builder(["US"], "in_force", None, usc=True)
    builder.walk(main, None, [], None, None, "")
    if not builder.provisions:
        raise UslmParseError("no sections found")
    return ParsedDocument(
        doc_kind="legislation",
        instrument_type="act",
        jurisdiction_id="US",
        work_uri=identifier,
        expression_uri=f"{identifier}/en@{released.isoformat()}/release-point-{release_point}",
        work_title=f"{name} (Title {number}, United States Code)",
        title=f"{name} (Title {number}, United States Code; {status}; release point {release_point})",
        short_title=f"{number} U.S.C.",
        number=number,
        work_date=None,
        language="en",
        authoritative=False,
        text_origin="official_consolidation",
        consolidation_kind="us_code_release_point",
        version_date=released,
        currency_date=released,
        extent=["US"],
        ext_module="us",
        ext={
            "usc_title": int(number),
            "positive_law": positive,
            "release_point": release_point,
            "compiled_through": through,
        },
        provisions=builder.provisions,
        validity_basis="attested_at_edition",
    )


def _bill(root: Element, kind: str) -> ParsedDocument:
    meta = root.find(_q("meta"))
    preface = root.find(_q("preface"))
    if meta is None or preface is None:
        raise UslmParseError("missing meta or preface")
    congress = _find_text(meta, "u:congress")
    number = _find_text(meta, "u:docNumber")
    doc_type = _find_text(meta, "dc:type") or ""
    bill_type = BILL_TYPES.get(doc_type)
    stage_text = _find_text(meta, "u:docStage") or ""
    citable = [inline_text(c) for c in meta.findall(_q("citableAs"))]
    slug = (_find_text(preface, "u:slugLine") or "").lstrip("•· ").split()
    stage = None
    if citable and len(citable[0].split()) == 4:
        stage = citable[0].split()[-1].lower()
    elif len(slug) == 3:
        stage = slug[-1].lower()
    if not congress or not number or not bill_type or not stage:
        raise UslmParseError("bill congress, type, number or stage not found")

    def dated(scope: Element) -> list[date]:
        return [
            date.fromisoformat(d.get("date", ""))
            for d in scope.iter(_q("date"))
            if re.match(r"^\d{4}-\d{2}-\d{2}$", d.get("date", ""))
        ]

    dates = dated(preface)
    date_basis = "latest_preface_action"
    if not dates:
        # Some stages (e.g. "Received in Senate") carry no dated preface action; the latest dated
        # attestation elsewhere in the document is a lower bound for the version date.
        dates = dated(root)
        date_basis = "latest_dated_action_in_document_lower_bound"
    if not dates:
        raise UslmParseError(f"no dated action for {doc_type} {number} ({stage_text}); version date unknown")
    when = max(dates)
    work_uri = f"/us/bill/{congress}/{bill_type}/{number}"
    builder = _Builder(["US"], "proposed", when)
    builder.walk(_main(root), None, [], None, None, "")
    official = _find_text(preface, "dc:title") or _find_text(meta, "dc:title") or ""
    instrument_type = "bill" if bill_type in ("hr", "s", "hjres", "sjres") else "resolution"
    label = f"{doc_type} {number}"
    return ParsedDocument(
        doc_kind="legislation",
        instrument_type=instrument_type,
        jurisdiction_id="US",
        work_uri=work_uri,
        expression_uri=f"{work_uri}/{stage}/en@{when.isoformat()}",
        work_title=f"{label} ({congress}th Congress): {official}",
        title=f"{label}, {stage_text} ({stage.upper()})",
        short_title=None,
        number=number,
        work_date=min(dates),
        language="en",
        authoritative=False,
        text_origin="official_conversion",
        consolidation_kind=f"bill:{stage}",
        version_date=when,
        currency_date=None,
        extent=["US"],
        ext_module="us",
        ext={"congress": int(congress), "number": number, "bill_type": bill_type, "version_date_basis": date_basis},
        events=[ParsedEvent(f"bill_stage:{stage}", when, None, stage_text, "preface/action")],
        provisions=builder.provisions,
        stage=stage_text,
        validity_basis="proposal_stage",
    )


_ACT_LEVELS = ["sec", "subsec", "para", "subpara", "cl", "subcl", "item", "subitem", "subsubitem"]


def _eid(levels: list[str], parts: list[str]) -> str:
    return "__".join(f"{levels[min(i, len(levels) - 1)]}_{p}" for i, p in enumerate(parts))


def resolve_identifier(uri: str) -> tuple[str, str | None] | None:
    """Map a USLM identifier to (work URI, eId) in ULIS terms, or None if not a known form.

    ``/us/cfr/t12/s220.1/b/1`` -> (``/us/cfr/t12/p220``, ``sec_220.1__para_b__para_1``);
    ``/us/pl/115/6/s2/a`` -> (``/us/pl/115/6``, ``sec_2__subsec_a``);
    ``/us/bill/116/hr/1000/s2`` -> (``/us/bill/116/hr/1000``, ``sec_2``);
    ``/us/usc/t18/s1030/a/2`` -> (``/us/usc/t18``, ``sec_1030``): a Code reference below the section resolves to the
    section (the levels between a section and its parts vary, so its subdivisions are not guessed);
    ``/us/usc/t1/ch1`` -> (``/us/usc/t1``, None).
    """
    m = re.match(r"^(/us/usc/t\d+)(?:/s(\d[0-9A-Za-z._-]*))?(?:/[0-9A-Za-z._-]+)*/?$", uri)
    if m:
        work, section = m.groups()
        return work, f"sec_{re.sub(r'[^A-Za-z0-9.-]', '_', section)}" if section else None
    m = re.match(r"^/us/cfr/t(\d+)/s(\d+)\.([0-9A-Za-z-]+)((?:/[0-9A-Za-z.-]+)*)/?$", uri)
    if m:
        title, part, rest, tail = m.groups()
        parts = [f"{part}.{rest}", *[t for t in tail.split("/") if t]]
        return f"/us/cfr/t{title}/p{part}", "__".join([f"sec_{parts[0]}", *[f"para_{p}" for p in parts[1:]]])
    m = re.match(r"^(/us/(?:pl/\d+/\d+|bill/\d+/[a-z]+/\d+))(?:/s([0-9A-Za-z.-]+)((?:/[0-9A-Za-z.-]+)*))?/?$", uri)
    if m:
        work, section, tail = m.groups()
        if not section:
            return work, None
        return work, _eid(_ACT_LEVELS, [section, *[t for t in (tail or "").split("/") if t]])
    return None
