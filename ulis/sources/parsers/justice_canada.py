"""Parser for the consolidated Acts and regulations of Canada (Justice Laws XML, Department of Justice Canada).

The XML (``github.com/justicecanada/laws-lois-xml``, Open Government Licence - Canada) is the
consolidation published on laws-lois.justice.gc.ca. Since 1 June 2009 these consolidations
are official (Legislation Revision and Consolidation Act, s 31): evidence of the law, but the
enacted text prevails over any inconsistency.

Format notes:

* one file per Act per language (``eng/acts/A-1.xml``, ``fra/lois/A-1.xml``) and one per regulation
  (``eng/regulations/SOR-2001-227.xml``, ``fra/reglements/DORS-2001-227.xml``); English and French are
  equally authoritative. The French file names and instrument numbers write ``DORS`` for ``SOR``,
  ``TR`` for ``SI`` and ``C.R.C., ch.`` for ``C.R.C., c.``; the English form is the language-neutral
  identifier (``/ca/regulation/sor-2001-227``), as on the Justice Laws Website;
* a ``<Statute>`` identifies itself by its consolidated chapter (``Chapter/ConsolidatedNumber``), a
  ``<Regulation>`` by ``InstrumentNumber`` (``SOR/2001-227``, ``SI/2000-100``, ``C.R.C., c. 870``) with its
  ``RegistrationDate``, ``EnablingAuthority`` (the Act it is made under) and the order that made it;
* every element carries ``lims:inforce-start-date``, the date from which that element, as now
  worded, has been in force. A provision's current wording applies from the latest such date
  in its subtree. Earlier wordings are not in the file: dates before it are a history gap,
  not "not yet in force";
* ``<Repealed>`` replaces the text of a repealed provision; a ``<Repealed>`` directly under the root
  marks the whole instrument as repealed (its remaining provisions are repealed too). An instrument
  whose consolidation holds only that notice has nothing to ingest (``ParseSkipped``);
* a regulation's ``<Order>`` (the order in council or other order that made it, or the whole text of a
  statutory instrument such as a proclamation or remission order) is unnumbered and paragraphed
  differently in the two languages; it is indexed as one provision, ``order``;
* ``HistoricalNote`` lists the enactments behind a provision (kept as a note, not law text);
  ``RecentAmendments``, identification ``Note``/``ReaderNote`` and ``Reserved`` markers are editorial;
* the "Amendments not in force" schedule lists amendments not yet applied (outstanding
  changes); the "Related provisions" schedule holds provisions of other Acts and is not
  indexed as part of this instrument.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from urllib.parse import unquote
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParsedRef, ParseSkipped

LIMS = "http://justice.gc.ca/lims"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
PARSER_ID = "justice_canada_xml"
PARSER_VERSION = "1.1.0"
SITE = "https://laws-lois.justice.gc.ca"

ROOTS = {"Statute": "act", "Regulation": "regulation"}
PROVISIONS = {
    "Section": "section",
    "Subsection": "subsection",
    "Paragraph": "paragraph",
    "Subparagraph": "subparagraph",
    "Clause": "clause",
    "Subclause": "subclause",
    "Subsubclause": "subsubclause",
    "Definition": "definition",
    "DefinitionEnOnly": "definition",
    "DefinitionFrOnly": "definition",
}
# Not law text: labels and marginal notes are rendered separately, historical notes are editorial.
NOT_TEXT = {"Label", "MarginalNote", "HistoricalNote", "ScheduleFormHeading", "Footnote", "FootnoteRef"}
OMITTED_SCHEDULES = {"RelatedProvs", "NifProvs"}
LANG3 = {"en": "eng", "fr": "fra"}
ACT_FOLDERS = {"en": "eng/acts", "fr": "fra/lois"}
REGULATION_FOLDERS = {"en": "eng/regulations", "fr": "fra/reglements"}
_ACT_URL = re.compile(r"^https://laws-lois\.justice\.gc\.ca/(?:eng/acts|fra/lois)/([A-Za-z0-9.\-]+)/?(?:index\.html)?$")
_REGULATION_URL = re.compile(
    r"^https://laws-lois\.justice\.gc\.ca/(?:eng/regulations|fra/reglements)/([^/?#]+)/?(?:index\.html)?$"
)


class CanadaParseError(ValueError):
    pass


def _lims(name: str) -> str:
    return f"{{{LIMS}}}{name}"


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9.]+", "-", ascii_text.lower()).strip("-")


def label_key(label: str) -> str:
    """``(1)`` -> ``1``, ``(a)`` / ``a)`` -> ``a``, ``16.31`` -> ``16.31``."""
    return slug(label.strip().strip("()").rstrip(")"))


def _raw(el: Element) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    if tag in ("TableGroup", "table"):
        rows = [" | ".join(_ws(_raw(e)) for e in row.iter("entry")) for row in el.iter("row")]
        return "\n" + "\n".join(r for r in rows if r.strip(" |")) + "\n"
    if tag == "BilingualGroup":
        return " / ".join(_ws(_raw(item)) for item in el)
    parts = [el.text or ""]
    for child in el:
        if child.tag not in NOT_TEXT:
            parts.append(_raw(child))
        parts.append(child.tail or "")
    return "".join(parts)


def inline_text(el: Element) -> str:
    return "\n".join(_ws(line) for line in _raw(el).split("\n") if _ws(line))


def act_uri(consolidated_number: str) -> str:
    return f"/ca/act/{slug(consolidated_number)}"


def regulation_number(number: str) -> str:
    """The instrument number in its English form, which both language versions share.

    ``DORS/2001-227`` -> ``SOR/2001-227``; ``TR/2000-100`` -> ``SI/2000-100``; ``C.R.C., ch. 870`` ->
    ``C.R.C., c. 870``; ``2010, ch. 12, art. 91`` -> ``2010, c. 12, s. 91``; ``C.P. 1945-7421`` -> ``P.C. 1945-7421``.
    Repository file names (``C.R.C.,_ch._870``, ``DORS-2001-227``) normalise the same way. Dashes become hyphen-minus.
    """
    s = _ws(number.replace("–", "-").replace("—", "-").replace("_", " "))
    s = re.sub(r"^DORS(?=[/-])", "SOR", s)
    s = re.sub(r"^TR(?=[/-])", "SI", s)
    s = re.sub(r"^C\.P\.(?=[ -])", "P.C.", s)
    return s.replace(", ch. ", ", c. ").replace(" art. ", " s. ")


def regulation_series(number: str) -> str | None:
    """``SOR`` (Statutory Orders and Regulations), ``SI`` (Statutory Instruments), ``CRC`` (Consolidated Regulations of
    Canada, 1978), or None for the few instruments numbered otherwise (a statute section, an order in council)."""
    canonical = regulation_number(number)
    if canonical.startswith("SOR/"):
        return "SOR"
    if canonical.startswith("SI/"):
        return "SI"
    if canonical.startswith("C.R.C."):
        return "CRC"
    return None


def regulation_uri(number: str) -> str:
    """``SOR/2001-227`` or ``DORS-2001-227`` -> ``/ca/regulation/sor-2001-227``; ``C.R.C., c. 870`` ->
    ``/ca/regulation/crc-c-870``."""
    canonical = regulation_number(number).replace(".", "").lower()
    return "/ca/regulation/" + re.sub(r"[^a-z0-9]+", "-", canonical).strip("-")


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map a Justice Laws Act or regulation URL (either language) to (ULIS work URI, eId)."""
    m = _ACT_URL.match(uri)
    if m:
        return (act_uri(m.group(1)), None)
    m = _REGULATION_URL.match(uri)
    if m:
        return (regulation_uri(unquote(m.group(1))), None)
    return None


def _latest_start(el: Element) -> date | None:
    """Latest in-force start date in a subtree, ignoring editorial notes."""
    found: list[date] = []

    def visit(e: Element) -> None:
        if e.tag in ("HistoricalNote", "Footnote"):
            return
        for attr in ("inforce-start-date", "lastAmendedDate"):
            d = _date(e.get(_lims(attr)))
            if d:
                found.append(d)
        for c in e:
            visit(c)

    visit(el)
    return max(found, default=None)


def _source_date(el: Element | None) -> date | None:
    """``<Date><YYYY>2001</YYYY><MM>6</MM><DD>14</DD></Date>`` -> 2001-06-14 (None when incomplete)."""
    if el is None:
        return None
    parts = [_ws(el.findtext(tag) or "") for tag in ("YYYY", "MM", "DD")]
    if not all(p.isdigit() for p in parts):
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _statute_identity(root: Element, ident: Element, language: str) -> tuple[str, str, dict[str, object]]:
    """(consolidated number, work URI, ext) of a consolidated Act."""
    number = _ws(ident.findtext("Chapter/ConsolidatedNumber") or "")
    if not number:
        raise CanadaParseError("missing consolidated number")
    annual = ident.find("Chapter/AnnualStatuteId")
    annual_year = _ws(annual.findtext("YYYY") or "") if annual is not None else ""
    annual_number = annual.find("AnnualStatuteNumber") if annual is not None else None
    annual_chapter = _ws(_raw(annual_number)) if annual_number is not None else ""
    # Acts in the Revised Statutes of Canada, 1985 are cited "RSC 1985, c A-1". The consolidation
    # does not say so directly, but its historical notes cite the RSC chapter.
    rsc_prefix = f"R.S., 1985, c. {number}," if language == "en" else f"L.R. (1985), ch. {number},"
    statute: dict[str, object]
    if annual is not None and annual_year.isdigit() and annual_chapter:
        # e.g. Divorce Act: RSC 1985, c 3 (2nd Supp); PIPEDA: SC 2000, c 5
        series = "RSC" if annual.get("revised-statute") == "yes" else "SC"
        statute = {"statute_series": series, "statute_year": int(annual_year), "statute_chapter": annual_chapter}
    elif any(_ws(_raw(i)).startswith(rsc_prefix) for i in root.iter("HistoricalNoteSubItem")):
        statute = {"statute_series": "RSC", "statute_year": 1985, "statute_chapter": number}
    else:
        statute = {}
    return number, act_uri(number), {"consolidated_number": number, **statute}


def _regulation_identity(ident: Element) -> tuple[str, str, dict[str, object]]:
    """(instrument number in its English form, work URI, ext) of a consolidated regulation."""
    printed = _ws(ident.findtext("InstrumentNumber") or "")
    if not printed:
        raise CanadaParseError("missing instrument number")
    number = regulation_number(printed)
    ext: dict[str, object] = {"instrument_number": number}
    series = regulation_series(number)
    if series:
        ext["instrument_series"] = series
    registered = _source_date(ident.find("RegistrationDate/Date"))
    if registered:
        ext["registration_date"] = registered.isoformat()
    enabling = [
        _ws(x.get("link") or "")
        for x in ident.findall("EnablingAuthority/XRefExternal")
        if x.get("reference-type") == "act" and _ws(x.get("link") or "")
    ]
    if enabling:
        ext["enabling_acts"] = enabling
    order = ident.find("RegulationMakerOrder")
    if order is not None:
        order_number = _ws(order.findtext("OrderNumber") or "")
        if order_number:
            ext["order_number"] = order_number
        made = _source_date(order.find("Date"))
        if made:
            ext["order_date"] = made.isoformat()
    return number, regulation_uri(number), ext


def parse_justice_canada(content: bytes) -> list[ParsedDocument]:
    try:
        root = fromstring(content, forbid_dtd=True)
    except Exception as exc:
        raise CanadaParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if root.tag not in ROOTS:
        raise CanadaParseError(
            f"unsupported root element {root.tag!r} (only consolidated Acts and regulations are supported)"
        )
    instrument_type = ROOTS[root.tag]
    language = root.get(XML_LANG) or ""
    if language not in LANG3:
        raise CanadaParseError(f"unsupported language {language!r}")
    ident = root.find("Identification")
    if ident is None:
        raise CanadaParseError("missing Identification")
    body = root.find("Body")
    order = root.find("Order")
    whole_repeal = root.find("Repealed")
    repeal_note = _ws(_raw(whole_repeal)) if whole_repeal is not None else ""
    version_date = _date(root.get(_lims("pit-date")))
    if version_date is None:
        raise CanadaParseError("missing lims:pit-date")
    current_date = _date(root.get(_lims("current-date")))
    short_el = ident.find("ShortTitle")
    long_el = ident.find("LongTitle")
    title = inline_text(short_el) if short_el is not None else ""
    if not title and long_el is not None:
        title = inline_text(long_el)
    if instrument_type == "act":
        number, work_uri, ext = _statute_identity(root, ident, language)
        registered = None
    else:
        number, work_uri, ext = _regulation_identity(ident)
        registered = _source_date(ident.find("RegistrationDate/Date"))
    instrument_not_in_force = root.get("in-force") == "no"
    lang3 = LANG3[language]

    provisions: list[ParsedProvision] = []
    used: dict[str, int] = {}

    def own_notes(el: Element) -> str | None:
        hist = el.find("HistoricalNote")
        if hist is None:
            return None
        items = [_ws(_raw(i)) for i in hist.iter("HistoricalNoteSubItem")]
        text = "; ".join(i for i in items if i)
        return text[:400] or None

    def external_ref(ref: Element) -> ParsedRef | None:
        link = _ws(ref.get("link") or "")
        kind = ref.get("reference-type")
        if not link or kind not in ("act", "regulation"):
            return None
        if kind == "regulation":
            if regulation_uri(link) == work_uri:  # the order "makes the annexed Regulations": not a citation
                return None
            return ParsedRef(f"{SITE}/{REGULATION_FOLDERS[language]}/{link}/", _ws(_raw(ref)), "cites")
        return ParsedRef(f"{SITE}/{ACT_FOLDERS[language]}/{link}/", _ws(_raw(ref)), "cites")

    def own_refs(el: Element) -> list[ParsedRef]:
        out: list[ParsedRef] = []
        for child in el:
            if child.tag in PROVISIONS or child.tag in NOT_TEXT:
                continue
            for ref in child.iter("XRefExternal"):
                parsed = external_ref(ref)
                if parsed:
                    out.append(parsed)
        return out

    def printed_label(el: Element) -> str:
        lab = el.find("Label")
        return _ws(_raw(lab)) if lab is not None else ""

    def label_of(el: Element) -> str:
        """Language-neutral label for ``num_path`` (both language versions share the provision row):
        ``4``, ``(1)``, ``(a)`` (printed ``a)`` in French), ``def:business day|jour ouvrable``."""
        if el.tag == "Schedule":
            heading = el.find("ScheduleFormHeading/Label")
            words = _ws(heading.text or "").split(maxsplit=1) if heading is not None else []
            return words[1] if len(words) == 2 else ""
        if PROVISIONS.get(el.tag) == "definition":
            terms = []
            for tag in ("DefinedTermEn", "DefinedTermFr"):
                term = next(el.iter(tag), None)
                terms.append(_ws(_raw(term)) if term is not None else "")
            return f"def:{terms[0]}|{terms[1]}" if any(terms) else ""
        label = printed_label(el)
        if el.tag == "Section":
            return label
        core = label.strip().strip("()").strip()
        return f"({core})" if core else ""

    def eid_of(el: Element, parent_eid: str | None) -> str:
        kind = PROVISIONS.get(el.tag, "schedule")
        if kind == "definition":
            en = next(el.iter("DefinedTermEn"), None)
            fr = next(el.iter("DefinedTermFr"), None)
            key = slug(_raw(en)) if en is not None else f"fr-{slug(_raw(fr))}" if fr is not None else ""
            local = f"def-{key or len(provisions)}"
        elif kind == "schedule":
            local = "schedule" + (f"-{label_key(label_of(el))}" if label_of(el) else "")
        elif kind == "section":
            local = f"section-{label_key(label_of(el))}"
        else:
            local = label_key(label_of(el)) or f"{kind}{len(provisions)}"
        eid = f"{parent_eid}-{local}" if parent_eid else local
        # The same term can be defined twice in one provision (for different purposes); number
        # repeats in document order, which both language versions share.
        n = used.get(eid, 0) + 1
        used[eid] = n
        return eid if n == 1 else f"{eid}-{n}"

    def text_of(el: Element) -> str:
        lines: list[str] = []
        for child in el:
            if child.tag in NOT_TEXT:
                continue
            if child.tag in PROVISIONS:
                label = printed_label(child) if PROVISIONS[child.tag] != "definition" else ""
                body_text = text_of(child)
                lines.append(f"{label} {body_text}".strip() if label else body_text)
            else:
                text = inline_text(child)
                if text:
                    lines.append(text)
        return "\n".join(line for line in lines if line)

    def repeal_only(el: Element) -> bool:
        """A definition whose ``<Text>`` holds nothing but the defined term(s) and the repeal notice."""
        t = el.find("Text")
        if t is None or t.find("Repealed") is None or _ws(t.text or ""):
            return False
        return all(c.tag in ("DefinedTermEn", "DefinedTermFr", "Repealed") and not _ws(c.tail or "") for c in t)

    def status_of(el: Element, text: str, own_repeal: Element | None) -> tuple[str, date | None, str | None]:
        """(status, valid_from, note) of a provision: a repealed provision keeps only its repeal notice as the note."""
        if instrument_not_in_force or el.get("in-force") == "no":
            return "prospective", None, own_notes(el)
        if own_repeal is not None and (_ws(text) == _ws(_raw(own_repeal)) or repeal_only(el)):
            return "repealed", _latest_start(el), _ws(_raw(own_repeal))
        if whole_repeal is not None:
            return "repealed", _latest_start(el), repeal_note
        return "in_force", _latest_start(el), own_notes(el)

    def add(
        el: Element, parent_eid: str | None, num_path: list[str], top_kind: str | None, context: str | None
    ) -> None:
        kind = PROVISIONS.get(el.tag, "schedule")
        eid = eid_of(el, parent_eid)
        label = label_of(el)
        path = [*num_path, label]
        marginal = el.find("MarginalNote")
        heading = inline_text(marginal) if marginal is not None else None
        if kind == "schedule":
            title_el = el.find("ScheduleFormHeading/TitleText")
            heading = inline_text(title_el) if title_el is not None else heading
        text = text_of(el)
        repeal = el.find("Text/Repealed")
        if repeal is None and kind == "schedule":
            repeal = el.find("Repealed")
        status, start, note = status_of(el, text, repeal)
        children = [c for c in el if c.tag in PROVISIONS]
        own = any(c.tag not in PROVISIONS and c.tag not in NOT_TEXT and inline_text(c) for c in el)
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=kind,
                num_path=path,
                top_kind=top_kind or kind,
                heading=heading,
                text="" if status == "repealed" else text,
                parent_eid=parent_eid,
                sort_order=len(provisions),
                status=status,  # type: ignore[arg-type]
                valid_from=start,
                valid_to=None,
                extent=["CA"],
                note=note or None,
                refs=own_refs(el),
                is_leaf=not children,
                has_own_content=own and bool(children),
                context_heading=context,
            )
        )
        for child in children:
            add(child, eid, path, top_kind or kind, context)

    if order is not None:
        # The making order (or the whole text of an order in council): one unnumbered provision, ``order``.
        text = text_of(order)
        status, start, note = status_of(order, text, None)
        provisions.append(
            ParsedProvision(
                eid="order",
                kind="order",
                num_path=[],
                top_kind="order",
                heading=None,
                text="" if status == "repealed" else text,
                parent_eid=None,
                sort_order=0,
                status=status,  # type: ignore[arg-type]
                valid_from=start,
                valid_to=None,
                extent=["CA"],
                note=note or None,
                refs=own_refs(order),
                is_leaf=True,
                has_own_content=False,
                context_heading=None,
            )
        )
        used["order"] = 1
    heading: str | None = None
    if body is not None:
        for el in body:
            if el.tag == "Heading":
                title_text = el.find("TitleText")
                heading = inline_text(title_text if title_text is not None else el)
            elif el.tag in PROVISIONS:
                add(el, None, [], None, heading)
            elif el.tag == "Schedule" and el.get("id") not in OMITTED_SCHEDULES:
                add(el, None, [], None, None)
    for sched in root.findall("Schedule"):
        if sched.get("id") in OMITTED_SCHEDULES:
            continue
        add(sched, None, [], None, None)
    if not provisions:
        if whole_repeal is not None:
            raise ParseSkipped(f"repealed; the consolidation holds only the repeal notice {repeal_note}")
        raise CanadaParseError("no provisions found")
    # The Act a regulation is made under (EnablingAuthority), as a citation link from its first provision.
    if instrument_type == "regulation":
        made_under = [
            ParsedRef(f"{SITE}/{ACT_FOLDERS[language]}/{x.get('link')}/", _ws(_raw(x)), "made_under")
            for x in ident.findall("EnablingAuthority/XRefExternal")
            if x.get("reference-type") == "act" and _ws(x.get("link") or "")
        ]
        provisions[0].refs = [*made_under, *provisions[0].refs]

    outstanding: list[dict[str, object]] = []
    for sched in root.findall("Schedule"):
        if sched.get("id") != "NifProvs":
            continue
        for block in sched.iter("RelatedOrNotInForce"):
            head = block.find("Heading/TitleText")
            label = _ws(_raw(head)).lstrip("—– ") if head is not None else ""
            parts = [text_of(c) if c.tag in PROVISIONS else inline_text(c) for c in block if c.tag != "Heading"]
            outstanding.append(
                {
                    "affects_eid": None,
                    "affected_label": None,
                    "by_uri": None,
                    "by_title": label or None,
                    "description": (_ws(" ".join(parts)) or label)[:400],
                }
            )

    events = [ParsedEvent("version_change", version_date, None, None, f"{root.tag}/@lims:pit-date")]
    repeal_date = _date(whole_repeal.get(_lims("inforce-start-date"))) if whole_repeal is not None else None
    if repeal_date:
        events.append(ParsedEvent("repeal", repeal_date, None, repeal_note or None, f"{root.tag}/Repealed"))

    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=instrument_type,
            jurisdiction_id="CA",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/{lang3}@{version_date.isoformat()}",
            work_title=title,
            title=title,
            short_title=title,
            number=number,
            # Acts: the enactment date is not in the consolidation. Regulations: the registration date.
            work_date=registered,
            language=language,
            authoritative=True,
            text_origin="official_consolidation",
            consolidation_kind="point_in_time",
            version_date=version_date,
            currency_date=current_date,
            extent=["CA"],
            ext_module="ca",
            ext=ext,
            outstanding_changes=outstanding,
            events=events,
            provisions=provisions,
            validity_basis="source_periods",
        )
    ]
