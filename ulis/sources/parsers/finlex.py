"""Parser for consolidated Finnish statutes (Finlex open data, Akoma Ntoso).

Finlex (Ministry of Justice of Finland) publishes each consolidated statute ("ajantasainen
lainsäädäntö" / "uppdaterad lagstiftning", FRBR subtype ``statute-consolidated``) as Akoma
Ntoso XML, one document per language (Finnish ``fin``, Swedish ``swe``) and per *temporal
version* (``fin@20260380`` = as amended by statute 380/2026). The consolidation is prepared by
Finlex's editors and is not authentic: the source marks the work ``FRBRauthoritative =
false``. The authentic text is the statute as published in the Statute Book of Finland, in
Finnish and Swedish, which are equally authentic.

Format notes, all taken from the documents:

* An element whose wording comes from an amending statute carries
  ``finlex:originalVersion="@20111112"`` (statute 1112/2011) and a label (``4.11.2011/1112``).
  Unmarked elements keep the wording of the nearest marked ancestor, or of the statute as
  enacted. ``finlex:amendedBy`` gives each amending statute's entry-into-force date and
  ``finlex:inForce`` the statute's own, so a wording applies from the entry into force of the
  statute that gave it (``validity_basis = source_periods``). A statute without a recorded
  date leaves the start unknown (``None``), never guessed.
* A wording enacted but not yet in force is printed together with the wording that still
  applies, after an authorial note ("Aiempi sanamuoto kuuluu:" / "Den tidigare formen
  lyder:"). Both are recorded, each from its own date. A container (section, subsection) is
  recorded as it stands on the version date and at each later change in its subtree.
* Repealed provisions are printed as a note ("4 momentti on kumottu L:lla 22.5.2026/380." /
  "4 mom. har upphävts genom L 22.5.2026/380."): status ``repealed``, empty text, the note
  kept as ``note``, from the entry into force of the repealing statute it links to.
* Amendment markers in the text (``(4.11.2011/1112)``) and authorial notes are editorial:
  kept in notes, never in ``text``. The entry-into-force provisions of amending statutes and
  the list of preparatory works are not provisions of this statute and are not indexed.

Identifiers: the source eIds are not reliable across language versions (in one language an
introductory clause may be tagged as a separate subsection, a closing clause as an unnumbered
paragraph, and eId numbers do not always follow the printed numbers). ULIS therefore builds
its own language-neutral eIds in the same style from the printed numbers (``chp_2a``,
``sec_8a``, ``para_3a``, ``subpara_b``) and, for subsections, which are not numbered in print,
from their position after normalising those encodings. Chapters and parts are structure, not
provisions, as in the UK and Canadian parsers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParsedRef

AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
FINLEX = "http://data.finlex.fi/schema/finlex"
PARSER_ID = "finlex_akn"
PARSER_VERSION = "1.0.0"
LANGUAGES = {"fin": "fi", "swe": "sv"}
INSTRUMENT_TYPES = {"type-statute.act": "act", "type-statute.decree": "decree"}

PROVISIONS = ("section", "subsection", "paragraph", "subparagraph")
STRUCTURE = ("part", "chapter")
TOKENS = {
    "part": "part",
    "chapter": "chp",
    "section": "sec",
    "subsection": "subsec",
    "paragraph": "para",
    "subparagraph": "subpara",
}
NOT_TEXT = {"num", "heading", "subheading", "crossHeading"}
_WORK = re.compile(r"^/akn/fi/act/statute-consolidated/(\d{4})/(\d{1,5}(?:-\d{3})?)$")
_CONSOLIDATED_REF = re.compile(r"^/akn/fi/act/statute-consolidated/(\d{4})/(\d{1,5}(?:-\d{3})?)(?:#([\w.-]+))?$")
_REPEAL = re.compile(
    r"^\d+(?: ?[a-z])?(?: §| momentti| mom\.| kohta| punkten| luku| kap\.)?(?:,[^,]*,)? "
    r"(?:on kumottu|har upphävts)\b"
)
_SECTION_REPEAL = re.compile(r"^\d+(?: ?[a-z])? §")
_EARLIER_WORDING = re.compile(r"Aiempi sanamuoto|tidigare formen", re.I)
_INSERTED = re.compile(r"\blisätty\b|\bhar tillfogats\b", re.I)  # "added", in an authorial note
_TRAILING = re.compile(r"\s+([.,;:])")
_MONTHS = {
    m: i + 1
    for months in (
        "tammikuuta helmikuuta maaliskuuta huhtikuuta toukokuuta kesäkuuta heinäkuuta elokuuta syyskuuta "
        "lokakuuta marraskuuta joulukuuta",
        "januari februari mars april maj juni juli augusti september oktober november december",
    )
    for i, m in enumerate(months.split())
}
_ENTRY_INTO_FORCE = re.compile(
    r"^(?:Tämä laki tulee voimaan (\d{1,2}) päivänä (\w+) (\d{4})"
    r"|Denna lag träder i kraft den (\d{1,2}) (\w+) (\d{4}))\.",
    re.I,
)


class FinlexParseError(ValueError):
    pass


def _a(tag: str) -> str:
    return f"{{{AKN}}}{tag}"


def _f(tag: str) -> str:
    return f"{{{FINLEX}}}{tag}"


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def version_id(year: int | str, number: str) -> str:
    """Temporal version named after statute ``number/year``: 380/2026 -> ``20260380``."""
    return f"{year}{int(number.split('-')[0]):04d}"


def work_uri(year: int | str, number: str) -> str:
    return f"/fi/act/{year}/{number}"


def _compact(label: str) -> str:
    """``8 a`` -> ``8a``; ``I`` -> ``i``."""
    return re.sub(r"[^0-9a-z]", "", label.lower())


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map a Finlex reference to a consolidated statute to (ULIS work URI, eId).

    ``/akn/fi/act/statute-consolidated/1994/750#sec_8a`` -> (``/fi/act/1994/750``, ``sec_8a``).
    Finlex fragments are the publisher's eIds; one that matches no ULIS eId resolves to the work.
    """
    m = _CONSOLIDATED_REF.match(uri)
    if not m:
        return None
    fragment = re.sub(r"v\d{8}(?=(?:_\d+)?(?:__|$))", "", m.group(3)) if m.group(3) else None
    return work_uri(m.group(1), m.group(2)), fragment


def _raw(el: Element, *, editorial: bool) -> str:
    """Text of an element. Editorial references (``#entryIntoForce_...`` amendment markers) and
    authorial notes are left out unless ``editorial`` is set."""
    parts = [el.text or ""]
    for child in el:
        tag = _local(child.tag)
        skip = tag in NOT_TEXT or tag in PROVISIONS or (tag == "hcontainer" and child.get("name") == "noteAuthorial")
        if tag == "ref" and (child.get("href") or "").startswith("#") and not editorial:
            skip = True
        if not skip:
            parts.append(_raw(child, editorial=editorial))
        parts.append(child.tail or "")
    return "".join(parts)


def _clean(text: str) -> str:
    return _TRAILING.sub(r"\1", _ws(text))


def _lines(block: Element | None, *, editorial: bool = False) -> list[str]:
    if block is None:
        return []
    paragraphs = [p for p in block if _local(p.tag) == "p"]
    if not paragraphs:
        text = _clean(_raw(block, editorial=editorial))
        return [text] if text else []
    return [t for t in (_clean(_raw(p, editorial=editorial)) for p in paragraphs) if t]


@dataclass
class _Node:
    el: Element
    kind: str
    num: str  # printed number without the unit word (``8 a``); "" for subsections
    printed: bool = True  # whether ``num`` is printed in the source (else taken from the eId)
    heading: str | None = None
    before: list[str] = field(default_factory=list)  # own text before child provisions
    after: list[str] = field(default_factory=list)  # own text after them (closing clause)
    children: list[_Node] = field(default_factory=list)
    own_marker: tuple[str, str | None] | None = None  # (version id, label) on the element itself
    # Markers that date only part of the element's own wording (an amended heading, a merged clause).
    extra_markers: list[tuple[str, str | None]] = field(default_factory=list)
    heading_marker: tuple[str, str | None] | None = None
    notes: list[str] = field(default_factory=list)
    refs: list[ParsedRef] = field(default_factory=list)
    status: str = "in_force"
    repealed_by: str | None = None  # version id of the repealing statute (from the repeal note's link)
    alternative: bool = False  # the earlier wording of the preceding sibling
    spill: list[list[str]] = field(default_factory=list)  # unnumbered sentences that are new subsections
    earlier_wording_follows: bool = False
    context: str | None = None
    # Assigned after normalisation:
    key: str = ""
    eid: str = ""
    parent: _Node | None = None
    marker: tuple[str, str | None] | None = None  # effective (own or inherited)
    start: date | None = None
    start_known: bool = False
    end: date | None = None


def _stated_entry_into_force(block: Element | None) -> date | None:
    """The date in an amending statute's standard commencement sentence ("Tämä laki tulee voimaan 1
    päivänä syyskuuta 1983." / "Denna lag träder i kraft den 1 september 1983."), only when the
    provision states a single commencement and nothing else about it."""
    lines = _lines(block, editorial=True) if block is not None else []
    text = " ".join(lines)
    if not lines or len(re.findall(r"tulee voimaan|träder i kraft", text)) != 1:
        return None
    m = _ENTRY_INTO_FORCE.match(lines[0])
    if not m:
        return None
    day, month, year = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(4), m.group(5), m.group(6))
    if month.lower() not in _MONTHS:
        return None
    try:
        return date(int(year), _MONTHS[month.lower()], int(day))
    except ValueError:
        return None


def _marker(el: Element) -> tuple[str, str | None] | None:
    ov = (el.get(_f("originalVersion")) or "").lstrip("@")
    return (ov, el.get(_f("originalVersionLabel"))) if re.fullmatch(r"\d{8}", ov) else None


def _printed_num(el: Element, kind: str) -> str:
    num = el.find(_a("num"))
    printed = _ws("".join(num.itertext())) if num is not None else ""
    if kind in STRUCTURE:
        return re.sub(r"\s*(luku|kap\.?|osa|avdelningen)$", "", printed, flags=re.I).strip()
    if kind == "section":
        return printed.rstrip("§").strip()
    return printed.rstrip(")").strip()


def _build(el: Element, kind: str, context: str | None) -> _Node:
    num = _printed_num(el, kind) if kind != "subsection" else ""
    node = _Node(el=el, kind=kind, num=num, printed=bool(num), context=context)
    node.own_marker = _marker(el)
    heading_el = el.find(_a("heading"))
    if heading_el is not None:
        node.heading = _clean(_raw(heading_el, editorial=False)) or None
        node.heading_marker = _marker(heading_el)
        if node.heading_marker:
            node.extra_markers.append(node.heading_marker)
    if kind == "chapter":
        num_el = el.find(_a("num"))
        printed = _ws("".join(num_el.itertext())) if num_el is not None else ""
        node.context = _ws(f"{printed} {node.heading or ''}") or None
    pending_alternative = False
    numbered_seen = False
    for child in el:
        tag = _local(child.tag)
        if tag in ("content", "intro"):
            node.before.extend(_lines(child))
        elif tag == "wrapUp":
            node.after.extend(_lines(child))
        elif tag == "hcontainer" and child.get("name") == "noteAuthorial":
            text = " ".join(_lines(child.find(_a("content")), editorial=True))
            if node.children and text:
                node.children[-1].notes.append(text)
            pending_alternative = bool(_EARLIER_WORDING.search(text))
            continue
        elif tag in PROVISIONS or tag in STRUCTURE:
            leaf_paragraph = tag == "paragraph" and not any(_local(c.tag) in PROVISIONS for c in child)
            clause = _lines(child.find(_a("content")))
            printed = _printed_num(child, tag) if tag == "paragraph" else ""
            last = node.children[-1] if node.children else None
            if (
                leaf_paragraph
                and clause
                and (not printed or (last is not None and printed == last.num and clause[0][:1].islower()))
            ):
                # Some language versions tag the introductory clause, the closing clause ("on
                # tuomittava ...") or a following sentence of a subsection as a "paragraph" without a
                # number (or repeating the previous number). They are not list items.
                if not numbered_seen:
                    node.before.extend(clause)
                elif clause[0][:1].islower():
                    node.after.extend(clause)
                else:
                    node.spill.append(clause)
                continue
            sub = _build(child, tag, node.context)
            previous = node.children[-1] if node.children else None
            sub.alternative = previous is not None and (pending_alternative or previous.earlier_wording_follows)
            node.children.append(sub)
            numbered_seen = numbered_seen or tag == "paragraph"
        pending_alternative = False
    for tag in ("content", "intro", "wrapUp"):
        block = el.find(_a(tag))
        for ref in block.iter(_a("ref")) if block is not None else ():
            href = ref.get("href") or ""
            if _CONSOLIDATED_REF.match(href):
                node.refs.append(ParsedRef(href=href, text=_ws("".join(ref.itertext())), relation="cites"))
    if kind in PROVISIONS and not node.children:
        note = " ".join(_lines(el.find(_a("content")), editorial=True))
        if note and len(node.before) == 1 and _REPEAL.match(note):
            node.status = "repealed"
            node.notes.append(note)
            node.before = []
            link = next(
                (r.get("href", "") for r in el.iter(_a("ref")) if r.get("href", "").startswith("#entryIntoForce_")), ""
            )
            node.repealed_by = link.removeprefix("#entryIntoForce_") or None
            node.earlier_wording_follows = bool(_EARLIER_WORDING.search(note))
    return node


def _normalise(node: _Node) -> None:
    """Undo encodings that differ between language versions of the same text."""
    for child in node.children:
        _normalise(child)
    kids = node.children
    # 0. Sentences after a subsection's list that start a new subsection.
    i = 0
    while i < len(kids):
        for n, lines in enumerate(kids[i].spill, start=1):
            kids.insert(
                i + n,
                _Node(
                    el=kids[i].el,
                    kind="subsection",
                    num="",
                    before=lines,
                    own_marker=kids[i].own_marker,
                    extra_markers=list(kids[i].extra_markers),
                ),
            )
        kids[i].spill = []
        i += 1
    # 1. An introductory clause tagged as its own subsection, followed by a subsection that is only a
    #    list of numbered paragraphs, is that subsection's introduction.
    i = 0
    while i + 1 < len(kids):
        a, b = kids[i], kids[i + 1]
        if (
            a.kind == b.kind == "subsection"
            and not a.children
            and a.before
            and a.status == "in_force"
            and not b.before
            and b.children
            and b.children[0].kind == "paragraph"
            and not b.alternative
        ):
            b.before = a.before + b.before
            b.extra_markers += ([a.own_marker] if a.own_marker else []) + a.extra_markers
            b.notes = a.notes + b.notes
            b.refs = a.refs + b.refs
            del kids[i]
            continue
        i += 1
    # 2. A list item tagged as its own subsection ("3) hän ei ole voinut ...") belongs to the list.
    i = 1
    while i < len(kids):
        a, b = kids[i - 1], kids[i]
        item = re.match(r"^(\d+(?: ?[a-z])?)\) (.+)$", b.before[0]) if len(b.before) == 1 else None
        if (
            item
            and a.kind == b.kind == "subsection"
            and a.children
            and a.children[-1].kind == "paragraph"
            and not a.after
            and not b.children
            and b.status == "in_force"
        ):
            a.children.append(
                _Node(
                    el=b.el,
                    kind="paragraph",
                    num=item.group(1),
                    before=[item.group(2)],
                    own_marker=b.own_marker,
                    extra_markers=b.extra_markers,
                )
            )
            del kids[i]
            continue
        i += 1
    # 3. A closing clause tagged as its own subsection (it continues the sentence, so it starts in
    #    lower case) belongs to the preceding subsection's list.
    i = 1
    while i < len(kids):
        a, b = kids[i - 1], kids[i]
        if (
            a.kind == b.kind == "subsection"
            and a.children
            and not a.after
            and not b.children
            and b.before
            and b.before[0][:1].islower()
            and b.status == "in_force"
            and not b.alternative
        ):
            a.after = b.before
            a.extra_markers += ([b.own_marker] if b.own_marker else []) + b.extra_markers
            a.refs = a.refs + b.refs
            del kids[i]
            continue
        i += 1
    # 4. A section whose only subsection is a repeal note for the whole section is repealed.
    if (
        node.kind == "section"
        and len(kids) == 1
        and kids[0].status == "repealed"
        and _SECTION_REPEAL.match(kids[0].notes[-1] if kids[0].notes else "")
    ):
        node.status, node.notes, node.repealed_by = "repealed", kids[0].notes, kids[0].repealed_by
        node.extra_markers += ([kids[0].own_marker] if kids[0].own_marker else []) + kids[0].extra_markers
        node.children = []


def _assign_ids(node: _Node) -> None:
    used: dict[str, int] = {}
    position = 0
    previous: _Node | None = None
    for child in node.children:
        child.parent = node
        if child.alternative and previous is not None:
            key = previous.key
            child.num = child.num or previous.num
        else:
            if child.kind == "subsection":
                position += 1
                label = str(position)
                child.num = label
            else:
                if not child.num:  # not printed: fall back to the source eId's number
                    eid_num = re.sub(r"v\d{8}(?=(?:_\d+)?$)", "", (child.el.get("eId") or "").rsplit("__", 1)[-1])
                    child.num = re.sub(r"^[a-z]+_(\d+)([a-z]?)(?:_\d+)?$", r"\1 \2", eid_num).strip()
                label = _compact(child.num)
            key = f"{TOKENS[child.kind]}_{label}"
            used[key] = used.get(key, 0) + 1
            if used[key] > 1:  # the same number twice without an "earlier wording" note: keep both
                key = f"{key}_{used[key]}"
        child.key = key
        child.eid = f"{node.eid}__{key}" if node.eid else key
        previous = child
        _assign_ids(child)


def parse_finlex(content: bytes) -> list[ParsedDocument]:
    try:
        root = fromstring(content, forbid_dtd=True)
    except Exception as exc:
        raise FinlexParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if root.tag != _a("akomaNtoso"):
        raise FinlexParseError(f"unsupported root element {root.tag!r}")
    act = root.find(_a("act"))
    if act is None:
        raise FinlexParseError("no <act> (only statutes are supported)")
    meta = act.find(_a("meta"))
    ident = meta.find(_a("identification")) if meta is not None else None
    prop = meta.find(_a("proprietary")) if meta is not None else None
    body = act.find(_a("body"))
    if meta is None or ident is None or prop is None or body is None:
        raise FinlexParseError("missing identification, proprietary metadata or body")
    work = ident.find(_a("FRBRWork"))
    expr = ident.find(_a("FRBRExpression"))
    if work is None or expr is None:
        raise FinlexParseError("missing FRBRWork or FRBRExpression")

    def value(parent: Element, tag: str, attr: str = "value") -> str:
        el = parent.find(_a(tag))
        return (el.get(attr) or "") if el is not None else ""

    def frbr_date(parent: Element, name: str) -> date | None:
        return next((_date(d.get("date")) for d in parent.findall(_a("FRBRdate")) if d.get("name") == name), None)

    subtype = value(work, "FRBRsubtype")
    if subtype != "statute-consolidated":
        raise FinlexParseError(
            f"unsupported FRBR subtype {subtype!r}: only consolidated statutes (statute-consolidated) are supported"
        )
    m = _WORK.match(value(work, "FRBRuri"))
    if not m:
        raise FinlexParseError(f"unsupported work URI {value(work, 'FRBRuri')!r}")
    year, number = int(m.group(1)), m.group(2)
    lang3 = value(expr, "FRBRlanguage", "language")
    if lang3 not in LANGUAGES:
        raise FinlexParseError(f"unsupported language {lang3!r}")
    version = value(expr, "FRBRversionNumber")
    if not re.fullmatch(r"\d{8}", version):
        raise FinlexParseError(f"missing or invalid FRBRversionNumber {version!r}")
    in_force_flag = prop.find(_f("isInForce"))
    if in_force_flag is not None and in_force_flag.get("value") == "false":
        raise FinlexParseError("the statute is no longer in force (repealed statutes are not supported)")
    act_start_el = prop.find(f"{_f('inForce')}/{_f('dateEntryIntoForce')}")
    act_start = _date(act_start_el.get("date")) if act_start_el is not None else None

    # entry into force of each amending statute, keyed by temporal version id
    amendments: dict[str, tuple[date | None, str, str, str | None]] = {}
    for ref_el in prop.iter(_f("statuteReference")):
        link = ref_el.find(_f("ref"))
        href = (link.get("href") or "") if link is not None else ""
        am = re.fullmatch(r"/akn/fi/act/statute/(\d{4})/(\d{1,4})", href)
        if link is None or am is None:
            continue
        dates = {d for d in (_date(e.get("date")) for e in ref_el.iter(_f("dateEntryIntoForce"))) if d}
        note = ref_el.findtext(_f("noteEditorial"))
        # A statute brought into force in stages or by decree has no single date here: unknown.
        amendments[version_id(am.group(1), am.group(2))] = (
            next(iter(dates)) if len(dates) == 1 else None,
            _ws(link.text or ""),
            href,
            _ws(note) if note else None,
        )
    original_id = version_id(year, number)
    # Older amending statutes may lack the date in the metadata; their own commencement provision
    # (printed after the consolidated text) states it.
    read_from_text: set[str] = set()
    for hc in body.iter(_a("hcontainer")):
        vid = (hc.get("eId") or "").removeprefix("entryIntoForce_")
        if hc.get("name") == "entryIntoForce" and vid in amendments and amendments[vid][0] is None:
            stated = _stated_entry_into_force(hc.find(_a("content")))
            if stated is not None:
                amendments[vid] = (stated, *amendments[vid][1:])
                read_from_text.add(vid)

    def start_of(vid: str | None) -> tuple[date | None, bool]:
        if vid is None or vid == original_id:
            return act_start, act_start is not None
        found = amendments.get(vid)
        return (found[0], True) if found is not None and found[0] is not None else (None, False)

    consolidated = frbr_date(expr, "dateConsolidated")
    version_date = start_of(version)[0] or consolidated
    if version_date is None:
        raise FinlexParseError("no version date: neither an entry-into-force date nor a consolidation date")
    observed: date = consolidated or version_date

    preface = act.find(_a("preface"))
    title_el = preface.find(f".//{_a('docTitle')}") if preface is not None else None
    number_el = preface.find(f".//{_a('docNumber')}") if preface is not None else None
    title = _ws("".join(title_el.itertext())) if title_el is not None else ""
    statute_number = _ws(number_el.text or "") if number_el is not None else ""
    if not title or not statute_number:
        raise FinlexParseError("missing docTitle or docNumber")
    type_el = prop.find(_f("typeStatute"))
    type_ref = (type_el.get("refersTo") or "").lstrip("#") if type_el is not None else ""
    concept = next((c.get("href", "") for c in meta.iter(_a("TLCConcept")) if c.get("eId") == type_ref), "")
    instrument_type = INSTRUMENT_TYPES.get(concept.rsplit("/", 1)[-1], "")
    if not instrument_type:
        raise FinlexParseError(f"unsupported statute type {concept!r}")
    wrapper = next(
        (h for h in body if _local(h.tag) == "hcontainer" and h.get("name") == "statuteProvisionsWrapper"), None
    )
    if wrapper is None:
        raise FinlexParseError("missing statuteProvisionsWrapper")

    # ------------------------------------------------------------------ tree, identifiers, dates
    top = _build(wrapper, "body", None)
    _normalise(top)
    _assign_ids(top)

    def assign_dates(node: _Node, inherited: tuple[str, str | None] | None, inherited_end: date | None) -> None:
        groups: dict[str, list[_Node]] = {}
        for child in node.children:
            own = child.own_marker
            child.marker = own or inherited
            child.start, child.start_known = start_of(child.marker[0] if child.marker else None)
            for extra in child.extra_markers:  # e.g. a heading amended later than the section
                extra_start, extra_known = start_of(extra[0])
                if not extra_known:
                    child.start, child.start_known = None, False
                elif child.start is not None and extra_start is not None and extra_start > child.start:
                    child.start = extra_start
            if child.status == "repealed" and child.repealed_by:
                child.start, child.start_known = start_of(child.repealed_by)
            if child.marker and child.status != "repealed" and own:
                label = child.marker[1] or child.marker[0]
                remark = (
                    f" Its entry into force is read from the commencement provision of {label}, not the metadata."
                    if child.marker[0] in read_from_text
                    else ""
                )
                child.notes.insert(0, f"Wording from {label}.{remark}")
            elif child.heading_marker and child.status != "repealed":
                child.notes.insert(0, f"Heading from {child.heading_marker[1] or child.heading_marker[0]}.")
            groups.setdefault(child.key, []).append(child)
        for alternatives in groups.values():
            starts = sorted(c.start for c in alternatives if c.start is not None)
            for c in alternatives:
                later = [d for d in starts if c.start is not None and d > c.start]
                ends = [d for d in (later[0] if later else None, inherited_end) if d is not None]
                c.end = min(ends) if ends else None
        for child in node.children:
            assign_dates(child, child.marker, child.end)

    assign_dates(top, None, None)

    def applies(node: _Node, t: date) -> bool:
        return (node.start is None or node.start <= t) and (node.end is None or t < node.end)

    def subtree_starts(node: _Node) -> set[date]:
        out = {node.start} if node.start is not None else set()
        for c in node.children:
            out |= subtree_starts(c)
        return out

    def blockers(node: _Node) -> set[date]:
        out: set[date] = set()
        for c in node.children:
            replaces = any(
                o is not c and o.key == c.key and (o.start is None or (c.start is not None and o.start < c.start))
                for o in node.children
            )
            inserted = any(_INSERTED.search(n) for n in c.notes)
            if c.start is not None and c.start > observed and not replaces and not inserted:
                out.add(c.start)
            out |= blockers(c)
        return out

    def composite(node: _Node, t: date) -> tuple[list[str], date | None]:
        """Lines of ``node`` as it stands on ``t`` and the date that text took effect (None if unknown)."""
        start, known = node.start, node.start_known
        lines = list(node.before)
        done: set[str] = set()
        for child in node.children:
            if child.key in done:
                continue
            done.add(child.key)
            chosen = [c for c in node.children if c.key == child.key and applies(c, t)]
            if not chosen:
                continue
            pick = max(chosen, key=lambda c: c.start or date.min)
            c_lines, c_start = composite(pick, t)
            if c_start is None:
                known = False
            elif start is None or c_start > start:
                start = c_start
            if pick.status == "repealed" or not c_lines:
                continue
            if pick.kind in ("paragraph", "subparagraph") and pick.num and pick.printed:
                c_lines = [f"{pick.num}) {c_lines[0]}", *c_lines[1:]]
            lines.extend(c_lines)
        lines.extend(node.after)
        return lines, (start if known else None)

    prefix = {"chapter": "ch", "section": "s", "subsection": "ss", "paragraph": "p", "subparagraph": "sp"}

    def path_of(node: _Node) -> list[str]:
        out: list[str] = []
        n: _Node | None = node
        while n is not None:
            if n.kind in prefix:
                out.append(f"{prefix[n.kind]}:{n.num}")
            n = n.parent
        return list(reversed(out))

    provisions: list[ParsedProvision] = []
    order: dict[str, int] = {}
    chapters_of: dict[str, set[str]] = {}

    def emit(node: _Node) -> None:
        if node.kind in STRUCTURE or node.kind == "body":
            for c in node.children:
                emit(c)
            return
        if node.kind == "section":
            chapter = node.parent.num if node.parent is not None and node.parent.kind == "chapter" else ""
            chapters_of.setdefault(node.num, set()).add(chapter)
        order.setdefault(node.eid, len(order))
        leaf = not node.children
        versions: list[tuple[date | None, list[str], date | None]] = []
        if leaf:
            versions.append((node.start if node.start_known else None, node.before + node.after, node.end))
        else:
            # The document shows the law as consolidated on ``observed``: the wordings in force then
            # and later ones. A later wording printed without the wording it replaces (and not marked
            # as an insertion) leaves the container's text unknown until it takes effect. Earlier
            # states of a container come from earlier temporal versions.
            first = max(d for d in (observed, node.start, *blockers(node)) if d is not None)
            for t in sorted({first} | {d for d in subtree_starts(node) if d > first}):
                if node.end is not None and t >= node.end:
                    continue
                lines, start = composite(node, t)
                if not any(v[0] == start and v[1] == lines for v in versions):
                    versions.append((start, lines, None))
            if versions and node.end is not None:
                versions[-1] = (versions[-1][0], versions[-1][1], node.end)
        parent_eid = node.parent.eid if node.parent is not None and node.parent.kind in PROVISIONS else None
        for start, lines, end in versions:
            provisions.append(
                ParsedProvision(
                    eid=node.eid,
                    kind=node.kind,
                    num_path=path_of(node),
                    top_kind="section",
                    heading=node.heading,
                    text="" if node.status == "repealed" else "\n".join(lines),
                    parent_eid=parent_eid,
                    sort_order=order[node.eid],
                    status=node.status,  # type: ignore[arg-type]
                    valid_from=start,
                    valid_to=end,
                    extent=["FI"],
                    note=" ".join(node.notes)[:600] or None,
                    refs=node.refs,
                    is_leaf=leaf,
                    has_own_content=bool(node.before or node.after) and not leaf,
                    context_heading=node.context,
                )
            )
        for c in node.children:
            emit(c)

    emit(top)
    if not provisions:
        raise FinlexParseError("no provisions found")

    events = []
    if act_start is not None:
        events.append(ParsedEvent("entry_into_force", act_start, None, None, "proprietary/finlex:inForce"))
    for when, label, href, note in sorted(amendments.values(), key=lambda a: (a[0] or date.min, a[2])):
        if when is not None:
            description = f"{label}: {note}" if note else label
            events.append(ParsedEvent("amendment", when, href, description, f"finlex:amendedBy[{label}]"))
    events.append(ParsedEvent("version_change", version_date, None, None, f"FRBRversionNumber[{version}]"))

    uri = work_uri(year, number)
    eli = next((a.get("value") for a in work.findall(_a("FRBRalias")) if a.get("name") == "eli"), None)
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=instrument_type,
            jurisdiction_id="FI",
            work_uri=uri,
            expression_uri=f"{uri}/{lang3}@{version}",
            work_title=title,
            title=title,
            short_title=title,
            number=statute_number,
            work_date=frbr_date(work, "dateIssued"),
            language=LANGUAGES[lang3],
            authoritative=value(work, "FRBRauthoritative") == "true",
            # Consolidated by Finlex's editors; the authentic text is the statute as published.
            text_origin="official_revised",
            consolidation_kind="point_in_time",
            version_date=version_date,
            currency_date=consolidated,
            extent=["FI"],
            ext_module="fi",
            ext={
                "statute_number": statute_number,
                "year": year,
                "number": number,
                "sections_per_chapter": any(len(chapters) > 1 for chapters in chapters_of.values()),
                **({"eli": eli} if eli else {}),
            },
            events=events,
            provisions=provisions,
            validity_basis="source_periods",
        )
    ]
