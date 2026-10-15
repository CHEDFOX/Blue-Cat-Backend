"""Parser for Estonian consolidated texts (terviktekstid) from Riigi Teataja, in the ``oigusakt`` XML.

Riigi Teataja (Justiits- ja Digiministeerium) publishes a consolidated text for every version of an
act, with its validity period. Each consolidated text is the whole act as it stood during that
period; an act's consolidated texts form a snapshot series (``validity_basis = "revision_snapshot"``,
``ulis.ingestion.snapshots``):

* **dates**: ``metaandmed/kehtivus`` gives ``kehtivuseAlgus`` (first day) and ``kehtivuseLopp``
  (last day, inclusive; absent while open). The calendar date as written is used (the XML often
  carries a time-zone offset such as ``2019-01-15+02:00``); ``valid_to`` is the day after
  ``kehtivuseLopp`` (half-open intervals). The source gives no validity dates for single
  provisions: it marks, per provision, the amending act and the date its change took effect
  (``muutmismarge``), which is kept as the provision's ``note`` and as lifecycle events;
* **currency**: the XML does not say to which date a consolidated text is up to date (only its
  validity period), so ``currency_date`` is not set and every open-ended version carries the
  "currency unknown" limitation;
* **structure**: parts (osa), chapters (peatükk), divisions (jagu), subdivisions (jaotis, alljaotis)
  are context headings, not provisions. Provisions are paragraphs (``para-5b1`` for "§ 5¹"),
  their subsections (lõige, ``para-5-lg-2``) and numbered items (punkt, alapunkt: ``...-p-3``,
  ``...-p-3-alap-a``). A paragraph without subsections carries its own text;
* **text is exact source text**: ``sisuTekst`` children are concatenated in document order
  (``tavatekst`` and the display text of a reference ``viide``); superscripts are written with
  Unicode superscripts ("§ 5¹", "m²"); line breaks inside a text are kept; amendment marks go to
  ``note``, never to ``text``, and so are the notes of corrections of errors (``veaparandus``). A
  provision that the source reproduces only as "Kehtetu - " plus the repealing act is repealed
  (empty text, the marker in ``note``);
* annexes (lisa) and the introductory formula are not ingested. Cross-references are not stored.

Text origin and authenticity: Riigi Teataja Act § 3(1) calls the consolidated texts it publishes
"ametlikud terviktekstid" (official consolidated texts), made on the basis of the originally
published act (algtekst) and with its validity data added (§ 3(2)); § 10(4) treats a consolidated
text that does not match its algtekst as an error to be corrected. The text origin is therefore
``official_consolidation`` and the text is not marked authoritative (the algtekst is the enacted
text); see ``config/sources/ee-riigi-teataja.yaml``.

XML safety: defusedxml with DTDs, entities and external references forbidden; only the root
``oigusakt`` with text type ``terviktekst`` or ``algtekst-terviktekst`` is accepted.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.ingestion.snapshots import SNAPSHOT_BASIS
from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParseSkipped

PARSER_ID = "riigi_teataja_xml"
PARSER_VERSION = "1.0.0"
JURISDICTION = "EE"
BASE = "https://www.riigiteataja.ee"
TEXT_TYPES = ("terviktekst", "algtekst-terviktekst")
DOCUMENT_TYPES = {"seadus": "act", "määrus": "regulation"}
CONTAINERS = ("osa", "peatykk", "jagu", "jaotis", "alljaotis")
SUPERSCRIPT = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")
REPEALED = re.compile(r"^\s*\[?\s*Kehtetu", re.IGNORECASE)  # Kehtetu, Kehtetud, Kehtetuks


class RiigiTeatajaParseError(ValueError):
    pass


def parse_xml(content: bytes) -> Element:
    try:
        return fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception as exc:
        raise RiigiTeatajaParseError(f"not well-formed or unsafe XML: {exc}") from exc


def _name(el: Element) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def _children(el: Element, *names: str) -> list[Element]:
    return [c for c in el if _name(c) in names]


def _child(el: Element, name: str) -> Element | None:
    for c in el:
        if _name(c) == name:
            return c
    return None


def _text(el: Element | None, name: str) -> str:
    c = _child(el, name) if el is not None else None
    return re.sub(r"\s+", " ", "".join(c.itertext())).strip() if c is not None else ""


def _date(value: str | None) -> date | None:
    value = (value or "").strip()[:10]
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _dmy(d: date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


# --------------------------------------------------------------------------- text


def _superscript(text: str) -> str:
    return text.translate(SUPERSCRIPT) if re.fullmatch(r"[0-9+-]+", text.strip()) else text


def _inline(el: Element) -> str:
    """Text of an element in document order: references by their display text, superscripts as such."""
    parts: list[str] = [el.text or ""]
    for c in el:
        n = _name(c)
        if n == "reavahetus":
            parts.append("\n")
        elif n == "sup":
            parts.append(_superscript("".join(c.itertext())))
        elif n == "viide":
            shown = _child(c, "kuvatavTekst")
            parts.append("".join(shown.itertext()) if shown is not None else "")
        elif n in ("muutmismarge", "veaparandus"):
            pass
        else:
            parts.append(_inline(c))
        parts.append(c.tail or "")
    return "".join(parts)


def _clean(text: str) -> str:
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.replace("\xa0", " ").split("\n")]
    return "\n".join(line for line in lines if line)


def _content(el: Element) -> str:
    """The text of a ``sisuTekst`` (children in order; amendment marks excluded)."""
    sisu = _child(el, "sisuTekst")
    if sisu is None:
        return ""
    return _clean(_inline(sisu))


def _label(el: Element, name: str) -> str:
    """Number of a unit (``paragrahvNr`` ...) with its superscript index (``ylaIndeks``)."""
    nr = _child(el, name)
    if nr is None:
        return ""
    base = re.sub(r"\s+", " ", "".join(nr.itertext())).strip()
    index = (nr.get("ylaIndeks") or "").strip()
    return base + (_superscript(index) if index else "")


def _id_label(el: Element, kind: str) -> str:
    """Number from the element's ``id`` (``para1lg5b1`` -> ``5¹``) when the source gives no number element."""
    m = re.search(rf"{kind}(\d+)(?:b(\d+))?$", el.get("id") or "")
    return (m.group(1) + (_superscript(m.group(2)) if m.group(2) else "")) if m else ""


def _slug(label: str) -> str:
    """Language-neutral id part of a label: ``5¹`` -> ``5b1``, ``2`` -> ``2``."""
    out: list[str] = []
    for ch in label.lower():
        sup = ch.translate(str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789"))
        if sup != ch:
            if not out or out[-1] != "b":
                out.append("b")
            out.append(sup)
        elif ch.isalnum():
            out.append(ch)
        else:
            out.append("-")
    return re.sub(r"-+", "-", "".join(out)).strip("-")


# --------------------------------------------------------------------------- amendment marks


def _ref(marge: Element) -> str:
    """Publication reference of a ``muutmismarge`` (its ``avaldamismarge``)."""
    ref = _child(marge, "avaldamismarge")
    return _fmt_ref(ref) if ref is not None else ""


def _fmt_ref(ref: Element) -> str:
    part, published = _text(ref, "RTosa"), _date(_text(ref, "avaldamineKuupaev"))
    year, number, article = _text(ref, "RTaasta"), _text(ref, "RTnr"), _text(ref, "RTartikkel")
    if published is not None:
        return ", ".join(p for p in (part, _dmy(published), article) if p)
    return f"{part} {year}, " + ", ".join(p for p in (number, article) if p) if year else ""


def _mark(marge: Element) -> str:
    """``Kehtetu - RT I 2006, 28, 211, jõust. 01.07.2006`` from a ``muutmismarge``."""
    lead = _text(marge, "tavatekst")
    ref = _ref(marge)
    came = _date(_text(marge, "joustumine"))
    text = f"{lead} {ref}".strip() if lead.endswith("-") else ", ".join(p for p in (lead, ref) if p)
    return text + (f", jõust. {_dmy(came)}" if came else "")


def _marks(el: Element) -> list[Element]:
    out = _children(el, "muutmismarge")
    sisu = _child(el, "sisuTekst")
    if sisu is not None:
        out += _children(sisu, "muutmismarge")
    return out


def _errata(el: Element) -> list[str]:
    """The notes of corrections of errors (``veaparandus``: "Veaparandus - ... seaduse § 10 lg 3 alusel"), which sit
    beside the unit's ``sisuTekst`` or inside it, after its text (they are not part of the text)."""
    sisu = _child(el, "sisuTekst")
    found = _children(el, "veaparandus") + (_children(sisu, "veaparandus") if sisu is not None else [])
    return [t for t in (_text(e, "veaparandusTekst") for e in found) if t]


# --------------------------------------------------------------------------- units


class _Unit:
    def __init__(self, kind: str, path_kind: str, label: str, eid: str, el: Element):
        self.kind, self.path_kind, self.label, self.eid, self.el = kind, path_kind, label, eid, el
        self.text = _content(el)
        marks = [_mark(m) for m in _marks(el)]
        self.notes = [m for m in marks if m] + _errata(el)
        self.repealed = False
        self.children: list[_Unit] = []


ITEM_KINDS = (("pt", "point"), ("subpt", "subpoint"), ("subsubpt", "subsubpoint"))


def _unit(el: Element, kind: str, path_kind: str, label: str, eid: str, depth: int = 0) -> _Unit:
    unit = _Unit(kind, path_kind, label, eid, el)
    used: dict[str, int] = {}

    def child_eid(prefix: str, lab: str) -> str:
        base = f"{eid}-{prefix}-{_slug(lab)}"
        used[base] = used.get(base, 0) + 1
        return base if used[base] == 1 else f"{base}-{used[base]}"

    sub_ids = [c.get("id") for c in el if _name(c) == "loige"]
    ids_unique = len(set(sub_ids)) == len(sub_ids)  # some versions repeat one id for every unnumbered lõige
    ordinal = 0
    for c in el:
        n = _name(c)
        if n == "loige":
            ordinal += 1
            lab = _label(c, "loigeNr") or (
                _id_label(c, "lg") if ids_unique else str(ordinal) if len(sub_ids) > 1 else ""
            )
            if not lab:  # an unnumbered lõige is the paragraph's own text
                inner = _unit(c, kind, path_kind, label, eid, 0)
                unit.text = "\n".join(t for t in (unit.text, inner.text) if t)
                unit.notes += inner.notes
                unit.children += inner.children
                continue
            unit.children.append(_unit(c, "subsection", "sub", lab, child_eid("lg", lab), 0))
        elif n == "alampunkt":
            lab = _label(c, "alampunktNr") or _id_label(c, "p")
            path_kind, kind_name = ITEM_KINDS[min(depth, 2)]
            prefix = ("p", "alap", "alalap")[min(depth, 2)]
            unit.children.append(_unit(c, kind_name, path_kind, lab, child_eid(prefix, lab), depth + 1))
        elif n in (
            "loigeNr",
            "alampunktNr",
            "paragrahvNr",
            "kuvatavNr",
            "paragrahvPealkiri",
            "sisuTekst",
            "muutmismarge",
            "veaparandus",
        ):
            continue
        else:
            raise RiigiTeatajaParseError(f"{eid}: unexpected <{n}> in <{_name(el)}>")
    flagged = any(c.get("kehtiv") == "0" for c in el if _name(c).endswith("Nr")) or el.get("kehtiv") == "0"
    marked = any(REPEALED.match(_text(m, "tavatekst")) for m in _marks(el))
    if not unit.text and not unit.children and (marked or flagged):
        unit.repealed = True
    return unit


# --------------------------------------------------------------------------- document


def _publication(ref: Element | None) -> str | None:
    return (_fmt_ref(ref) or None) if ref is not None else None


def parse_act(root: Element) -> ParsedDocument:
    meta = _child(root, "metaandmed")
    if meta is None:
        raise RiigiTeatajaParseError("no <metaandmed>")
    text_type = _text(meta, "tekstiliik")
    if _text(meta, "seisund") == "EELNÕU":  # a draft (the archive holds the publisher's schema examples)
        raise ParseSkipped("a draft (eelnõu), not a published act")
    if text_type not in TEXT_TYPES:
        raise RiigiTeatajaParseError(f"unsupported text type {text_type!r} (only consolidated texts are parsed)")
    document_type = _text(meta, "dokumentLiik")
    instrument_type = DOCUMENT_TYPES.get(document_type)
    if instrument_type is None:
        raise ParseSkipped(f"document type {document_type!r}: only acts (seadus) and regulations (määrus) are ingested")
    if _text(meta, "marksona") == "põhiseadus":  # the Constitution is typed "seadus"; its keyword says what it is
        instrument_type = "constitution"
    global_id, group_id = _text(meta, "globaalID"), _text(meta, "terviktekstiGrupiID")
    validity = _child(meta, "kehtivus")
    start = _date(_text(validity, "kehtivuseAlgus"))
    end = _date(_text(validity, "kehtivuseLopp"))
    names = _child(root, "aktinimi")
    title = _text(_child(names, "nimi") if names is not None else None, "pealkiri")
    if not (global_id.isdigit() and group_id.isdigit() and start and title):
        raise RiigiTeatajaParseError("incomplete metadata (globaalID, terviktekstiGrupiID, kehtivuseAlgus, pealkiri)")
    if end is not None and end < start:
        raise RiigiTeatajaParseError(f"{global_id}: validity ends before it starts")
    valid_to = end + timedelta(days=1) if end else None
    abbreviation = _text(meta, "lyhend") or None
    sisu = _child(root, "sisu")
    if sisu is None:
        raise RiigiTeatajaParseError(f"{global_id}: no <sisu> (no structured content)")
    if len(sisu) == 0:
        # An act that has been repealed is published as an empty consolidated text with the repeal as its mark
        # ("Kehtetu - RT I, ..., jõust. ..."): nothing to ingest, and the text is not in force.
        raise ParseSkipped(f"{global_id}: the consolidated text of {title!r} is empty (repealed)")
    if any(_name(c) == "sisuTekst" and any(_name(e) == "HTMLKonteiner" for e in c.iter()) for c in sisu):
        # Texts migrated from the old register hold their whole text as one HTML fragment (CDATA), without
        # paragraphs or subsections: not ingested (many of them are only the "Kehtetu - ..." mark of a repealed act).
        raise ParseSkipped(f"{global_id}: the text of {title!r} is one unstructured HTML fragment; not ingested")

    provisions: list[ParsedProvision] = []
    used: dict[str, int] = {}

    def emit(unit: _Unit, parent: str | None, path: list[str], heading: str | None, context: str | None) -> None:
        eid = unit.eid
        status = "repealed" if unit.repealed else "in_force"
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=unit.kind,
                num_path=[*path, f"{unit.path_kind}:{unit.label}"],
                top_kind="preamble" if unit.kind == "preamble" else "section",
                heading=heading,
                text="" if unit.repealed else unit.text,
                parent_eid=parent,
                sort_order=len(provisions),
                status=status,  # type: ignore[arg-type]
                valid_from=start,
                valid_to=valid_to,
                extent=[JURISDICTION],
                note="; ".join(unit.notes) or None,
                refs=[],
                is_leaf=not unit.children,
                has_own_content=bool(unit.text),
                context_heading=context,
            )
        )
        for child in unit.children:
            emit(child, eid, [*path, f"{unit.path_kind}:{unit.label}"], None, context)

    def walk(el: Element, outline: list[str]) -> None:
        for c in el:
            n = _name(c)
            if n == "sisuTekst" and el is sisu:  # preamble (e.g. of the Constitution)
                unit = _Unit("preamble", "preamble", "preambul", "preamble", el)
                unit.notes = [t for t in (_mark(m) for m in _marks(el)) if t]
                emit(unit, None, [], "Preambul", "Preambul")
            elif n == "preambul" and el is sisu:
                # The preamble of a regulation ("Määrus kehtestatakse ... alusel"): its enabling provision.
                unit = _Unit("preamble", "preamble", "preambul", "preamble", c)
                # tavatekst and the display text of a reference (viide) are siblings here: joined without the
                # whitespace the XML puts between them
                pieces: list[str] = []
                for t in c:
                    if _name(t) == "reavahetus":
                        pieces.append("\n")
                    elif _name(t) == "viide":
                        display = _child(t, "kuvatavTekst")
                        pieces.append("".join(display.itertext()) if display is not None else "")
                    else:
                        pieces.append(_inline(t))
                unit.text = _clean("".join(pieces))
                if unit.text:
                    emit(unit, None, [], "Preambul", "Preambul")
            elif n in CONTAINERS:
                shown = _text(c, "kuvatavNr")
                heading = " ".join(p for p in (shown, _text(c, f"{n}Pealkiri")) if p)
                walk(c, [*outline, heading])
            elif n == "paragrahv":
                label = _label(c, "paragrahvNr")
                eid = f"para-{_slug(label)}"
                used[eid] = used.get(eid, 0) + 1
                if used[eid] > 1:
                    eid = f"{eid}-{used[eid]}"
                unit = _unit(c, "section", "sec", label, eid)
                title_text = _text(c, "paragrahvPealkiri") or None
                emit(unit, None, [], title_text, title_text)
            elif n in ("muutmismarge", "veaparandus", "kuvatavNr") or n.endswith(("Nr", "Pealkiri")):
                continue
            else:
                raise RiigiTeatajaParseError(f"{global_id}: unexpected <{n}> in <{_name(el)}>")

    walk(sisu, [])
    if not provisions:
        raise RiigiTeatajaParseError(f"{global_id}: no provisions found")

    adopted = _child(meta, "vastuvoetud")
    work_date = _date(_text(adopted, "aktikuupaev")) if adopted is not None else None
    original = _publication(_child(adopted, "avaldamismarge")) if adopted is not None else None
    own_ref = _child(meta, "avaldamismarge")
    publication = _publication(own_ref)
    work_uri = f"/ee/{instrument_type}/{group_id}"
    act_page = f"{BASE}/akt/{global_id}"
    events = [ParsedEvent("version_change", start, act_page, f"Consolidated text valid from {start}", "metaandmed")]
    for marge in _children(root, "muutmismarge"):
        came = _date(_text(marge, "joustumine"))
        ref = _child(marge, "avaldamismarge")
        act_id = _text(ref, "aktViide") if ref is not None else ""
        if came and act_id:
            events.append(
                ParsedEvent("amendment", came, f"{BASE}/akt/{act_id}", _mark(marge) or "Amendment", "muutmismarge")
            )
    ext: dict[str, object] = {"group_id": group_id, "global_id": global_id, "document_type": document_type}
    for key, value in (
        ("abbreviation", abbreviation),
        ("issuer", _text(meta, "valjaandja") or None),
        ("original_publication", original),
        ("publication", publication),
    ):
        if value:
            ext[key] = value
    return ParsedDocument(
        doc_kind="legislation",
        instrument_type=instrument_type,
        jurisdiction_id=JURISDICTION,
        work_uri=work_uri,
        expression_uri=f"{work_uri}/est@{global_id}",
        work_title=title,
        title=title,
        short_title=abbreviation,
        number=None,
        work_date=work_date,
        language="et",
        authoritative=False,
        text_origin="official_consolidation",
        consolidation_kind="point_in_time",
        version_date=start,
        currency_date=None,
        extent=[JURISDICTION],
        ext_module="ee",
        ext=ext,
        events=events,
        provisions=provisions,
        validity_basis=SNAPSHOT_BASIS,
    )


def parse_riigi_teataja(content: bytes) -> list[ParsedDocument]:
    root = parse_xml(content)
    if _name(root) != "oigusakt":
        raise RiigiTeatajaParseError(f"unsupported root element {root.tag!r} (expected <oigusakt>)")
    return [parse_act(root)]


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """``/ee/<type>/<group id>[/~<eid>]`` -> (work URI, eId); other URIs are not Estonian."""
    m = re.match(r"^(/ee/(?:act|constitution)/\d+)(?:/~(.+))?$", uri.strip())
    return (m.group(1), m.group(2)) if m else None
