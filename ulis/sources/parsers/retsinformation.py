"""Parser for Danish lovbekendtgørelser (LBK H) from Retsinformation, in Lex Dania XML.

A lovbekendtgørelse is an official consolidation ("sammenskrivning af en lov eller
lovbekendtgørelse med senere ændringer", Retsinformation FAQ), made by the responsible ministry
and published in Lovtidende A. Each one is the whole law as consolidated on its date; a law's
consolidations form a snapshot series (``validity_basis = "revision_snapshot"``,
``ulis.ingestion.snapshots``):

* **dates**: the source dates the consolidation, not its provisions. The snapshot date is the
  consolidation's own date (``Meta/DiesSigni``, the date in "LBK nr 433 af 22/04/2014"); it is
  also the currency date. When each consolidated amendment took effect is stated only in prose
  (the amending acts' commencement provisions reproduced at the end), so it is not used;
  ``Meta/StartDate`` is not used either (it is not the Lovtidende publication date for every
  document). Wording may therefore have applied before the snapshot date; amendments made after a
  consolidation are not in its text;
* **later changes**: ``Meta/Change`` lists the amending acts Retsinformation links to the
  document as later changes ("Senere ændringer til forskriften"). For the consolidation marked
  current (``Status = Valid``) they are outstanding changes (instrument-wide: the source does not
  say which provisions they affect). Every listed change is also a lifecycle event (``amendment``);
* **structure**: parts (afsnit) and chapters (kapitel) and paragraph-group headings are context
  headings, not provisions. Provisions are paragraphs (``par-9a`` for "§ 9 a"), their stykker
  when a paragraph has more than one (``par-1-stk-2``), and numbered / lettered items
  (``par-1-stk-2-nr-1``, ``...-litra-a``) when their labels are unique. The commencement
  provisions of amending acts reproduced in the consolidation (``Ikraft``) are provisions too
  (``ikraft-lov-2013-638-par-3``, one per paragraph). The introduction ("Herved bekendtgøres
  ..."), the signature block and annexes (bilag; e.g. the reproduced GDPR, a table of contents)
  are not ingested;
* **text is exact source text**. Footnotes (``Nota``) go to ``note``, never to ``text``. A
  paragraph, stykke or item whose whole text is "(Ophævet)" is repealed (empty text, the marker
  in ``note``).

Text origin is ``official_consolidation`` and never authoritative: Retsinformation's XML is an
"official" format (ELI legal value), the signed PDF in Lovtidende is the definitive publication,
and a consolidation does not replace the enacted acts.

XML safety: defusedxml with DTDs, entities and external references forbidden; only the root
``Dokument`` with document type ``LBK H`` is accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.ingestion.snapshots import SNAPSHOT_BASIS
from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision

PARSER_ID = "retsinformation_lexdania"
PARSER_VERSION = "1.0.0"
JURISDICTION = "DK"
ELI = "https://www.retsinformation.dk/eli"
REPEALED = re.compile(r"^\(Ophævet\)\.?$")
PARAGRAPH = re.compile(r"^§§?\s*(\d+(?:\s*[a-zæøå])?(?:\s*-\s*\d+(?:\s*[a-zæøå])?)?)\s*\.?$")
STK = re.compile(r"^Stk\.\s*(\d+)\s*\.?$")
ITEM = {"Nummer": ("nr", re.compile(r"^(\d+(?:\s*[a-z])?)\)$")), "Litra": ("litra", re.compile(r"^([a-zæøå]{1,2})\)$"))}
AMENDING_ACT = re.compile(r"^Lov nr\. (\d+) af (\d{1,2})\. (\w+) (\d{4})")
STRUCTURE = ("Bog", "Afsnit", "Kapitel", "ParagrafGruppe")


class RetsinformationParseError(ValueError):
    pass


def parse_xml(content: bytes) -> Element:
    try:
        return fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception as exc:
        raise RetsinformationParseError(f"not well-formed or unsafe XML: {exc}") from exc


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "").strip()[:10]) if value and value.strip() else None
    except ValueError:
        return None


def _slug(text: str) -> str:
    text = text.lower().replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    return re.sub(r"[^a-z0-9]+", "", text) or "x"


def _num_slug(number: str) -> str:
    """Paragraph number to eId part: "9 a" -> "9a", "28-30" -> "28-30"."""
    return "-".join(_slug(part) for part in number.split("-"))


# --------------------------------------------------------------------------- text


@dataclass
class _Notes:
    items: list[str] = field(default_factory=list)

    def add(self, text: str) -> None:
        if text and text not in self.items:
            self.items.append(text)


def _note_text(nota: Element) -> str:
    label = _ws(nota.findtext("Explicatus") or "")
    body = " ".join(_ws("".join(e.itertext())) for e in nota.findall("Exitus"))
    return _ws(f"Note {label} {body}")


def _exitus_lines(exitus: Element, notes: _Notes, skip: frozenset[Element]) -> list[str]:
    lines: list[str] = []
    for child in exitus:
        if child.tag == "Linea":
            text = _ws("".join(child.itertext()))
            if text:
                lines.append(text)
        elif child.tag == "Nota":
            notes.add(_note_text(child))
        elif child.tag == "Index":
            for item in child.findall("Indentatio"):
                if item in skip:
                    continue
                lines.extend(_labelled(item, notes))
        elif child.tag == "Table":
            for row in child.iter("Tr"):
                cells = [" ".join(_lines(td, notes)) for td in row.findall("Td")]
                if any(cells):
                    lines.append(" | ".join(cells))
        else:
            lines.extend(_lines(child, notes))
    return lines


def _lines(el: Element, notes: _Notes, skip: frozenset[Element] = frozenset()) -> list[str]:
    """Text of a block (stk, item, cell) without its own label; children keep their labels."""
    lines: list[str] = []
    for child in el:
        if child in skip or child.tag in ("Explicatus", "Rubrica"):
            continue
        if child.tag == "Exitus":
            lines.extend(_exitus_lines(child, notes, skip))
        elif child.tag == "Nota":
            notes.add(_note_text(child))
        elif child.tag == "Stk":
            lines.extend(_labelled(child, notes))
        elif child.tag == "Linea":
            text = _ws("".join(child.itertext()))
            if text:
                lines.append(text)
        else:
            lines.extend(_lines(child, notes))
    return lines


def _labelled(el: Element, notes: _Notes) -> list[str]:
    body = _lines(el, notes)
    label = _ws(el.findtext("Explicatus") or "")
    if body and label:
        return [f"{label} {body[0]}", *body[1:]]
    return body


# --------------------------------------------------------------------------- units


@dataclass
class _Unit:
    kind: str
    eid: str  # relative part, e.g. "stk-2"
    label: str  # num_path entry, e.g. "stk:2"
    text: str
    status: str
    own: bool
    notes: list[str]
    children: list[_Unit] = field(default_factory=list)


def _items(container: Element) -> tuple[list[tuple[str, str, Element]], frozenset[Element]]:
    """Citable numbered / lettered items directly in ``container`` (in its Exitus/Index)."""
    found: list[tuple[str, str, Element]] = []
    for exitus in container.findall("Exitus"):
        for index in exitus.findall("Index"):
            for item in index.findall("Indentatio"):
                spec = ITEM.get(item.get("formaInd") or "")
                match = spec[1].match(_ws(item.findtext("Explicatus") or "")) if spec else None
                if spec is None or match is None:
                    return [], frozenset()
                found.append((spec[0], re.sub(r"\s+", "", match.group(1)), item))
    keys = [(kind, num) for kind, num, _ in found]
    if len(keys) != len(set(keys)):
        return [], frozenset()
    return found, frozenset(item for _, _, item in found)


def _repealed(text: str) -> bool:
    return bool(REPEALED.match(text.strip()))


def _block(kind: str, eid: str, label: str, el: Element) -> _Unit:
    """A stykke or an item, with its split-off items."""
    whole = _Notes()
    text = "\n".join(_lines(el, whole))
    if _repealed(text):
        return _Unit(kind, eid, label, "", "repealed", False, [text.strip(), *whole.items])
    found, split = _items(el)
    children = [_block(k, f"{k}-{n}", f"{k}:{n}", item) for k, n, item in found]
    own_text = "\n".join(_lines(el, _Notes(), split)) if children else ""
    return _Unit(kind, eid, label, text, "in_force", bool(children) and bool(own_text), whole.items, children)


def _paragraph_number(par: Element) -> str:
    printed = _ws(par.findtext("Explicatus") or "")
    match = PARAGRAPH.match(printed)
    if match is None:
        raise RetsinformationParseError(f"unexpected paragraph label {printed!r}")
    return re.sub(r"\s*-\s*", "-", _ws(match.group(1)))


def _paragraph(par: Element, eid: str, kind: str = "paragraph") -> _Unit:
    number = _paragraph_number(par)
    whole = _Notes()
    text = "\n".join(_lines(par, whole))
    if _repealed(text):
        return _Unit(kind, eid, f"p:{number}", "", "repealed", False, [text.strip(), *whole.items])
    stykker = par.findall("Stk")
    children: list[_Unit] = []
    if len(stykker) > 1:
        numbers = []
        for i, stk in enumerate(stykker):
            match = STK.match(_ws(stk.findtext("Explicatus") or ""))
            numbers.append(match.group(1) if match else ("1" if i == 0 and stk.find("Explicatus") is None else ""))
        if all(numbers) and len(set(numbers)) == len(numbers):
            children = [_block("stk", f"stk-{n}", f"stk:{n}", stk) for n, stk in zip(numbers, stykker, strict=True)]
    elif len(stykker) == 1:
        found, _ = _items(stykker[0])
        children = [_block(k, f"{k}-{n}", f"{k}:{n}", item) for k, n, item in found]
        own_text = "\n".join(_lines(stykker[0], _Notes(), frozenset(i for _, _, i in found))) if children else ""
        return _Unit(
            kind, eid, f"p:{number}", text, "in_force", bool(children) and bool(own_text), whole.items, children
        )
    return _Unit(kind, eid, f"p:{number}", text, "in_force", False, whole.items, children)


# --------------------------------------------------------------------------- document


@dataclass(frozen=True)
class Change:
    accn: str
    dated: date | None
    title: str

    @property
    def uri(self) -> str:
        return f"{ELI}/accn/{self.accn}"


def _changes(meta: Element) -> list[Change]:
    by_ref: dict[str, dict[str, str]] = {}
    for el in meta:
        ref = el.get("REFid")
        if el.tag.startswith("Ref_") and ref and ref.startswith("change_"):
            by_ref.setdefault(ref, {})[el.tag] = _ws((el.text or "").replace("\\n", " "))
    return [
        Change(accn=v["Ref_Accn"], dated=_date(v.get("Ref_Af")), title=v.get("Ref_Text", ""))
        for v in by_ref.values()
        if v.get("Ref_Accn")
    ]


def work_uri_for(popular_title: str) -> str:
    return f"/dk/lov/{_slug(popular_title)}"


def parse_lbk(root: Element) -> ParsedDocument:
    meta = root.find("Meta")
    if meta is None:
        raise RetsinformationParseError("no <Meta>")

    def m(tag: str) -> str:
        return _ws(meta.findtext(tag) or "")

    doc_type = m("DocumentType").split("#", 1)[0].strip()
    if doc_type != "LBK H":
        raise RetsinformationParseError(f"unsupported document type {doc_type!r} (only LBK H is parsed)")
    if m("AnnouncedIn") != "Lovtidende A":
        raise RetsinformationParseError(f"unexpected publication medium {m('AnnouncedIn')!r}")
    year, number, popular = m("Year"), m("Number"), m("PopularTitle")
    signed = _date(m("DiesSigni"))
    status = m("Status")
    if not (year.isdigit() and number.isdigit() and popular and signed) or status not in ("Valid", "Historic"):
        raise RetsinformationParseError("incomplete metadata (Year, Number, PopularTitle, DiesSigni, Status)")
    eli = f"{ELI}/lta/{year}/{number}"
    label = f"LBK nr {number} af {signed.strftime('%d/%m/%Y')}"
    body = root.find("DokumentIndhold")
    if body is None:
        raise RetsinformationParseError(f"{label}: no <DokumentIndhold> (no structured content)")

    provisions: list[ParsedProvision] = []
    used: dict[str, int] = {}

    def emit(
        unit: _Unit, parent: str | None, path: list[str], top_kind: str, heading: str | None, context: str | None
    ) -> None:
        eid = f"{parent}-{unit.eid}" if parent else unit.eid
        used[eid] = used.get(eid, 0) + 1
        if used[eid] > 1:
            eid = f"{eid}-{used[eid]}"
        notes = unit.notes
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=unit.kind,
                num_path=[*path, unit.label],
                top_kind=top_kind,
                heading=heading,
                text=unit.text if unit.status != "repealed" else "",
                parent_eid=parent,
                sort_order=len(provisions),
                status=unit.status,  # type: ignore[arg-type]
                valid_from=signed,
                valid_to=None,
                extent=[JURISDICTION],
                note="; ".join(notes) or None,
                refs=[],
                is_leaf=not unit.children,
                has_own_content=unit.own,
                context_heading=context,
            )
        )
        for child in unit.children:
            emit(child, eid, [*path, unit.label], top_kind, None, context)

    def outline_of(el: Element) -> str:
        rubrica = el.find("Rubrica")
        parts = [_ws(el.findtext("Explicatus") or ""), _ws("".join(rubrica.itertext())) if rubrica is not None else ""]
        return " ".join(p for p in parts if p)

    def walk(el: Element, outline: list[str]) -> None:
        for child in el:
            if child.tag in STRUCTURE:
                walk(child, [*outline, outline_of(child)])
            elif child.tag == "Paragraf":
                unit = _paragraph(child, f"par-{_num_slug(_paragraph_number(child))}")
                emit(unit, None, [], "paragraph", None, " – ".join(h for h in outline if h) or None)
            elif child.tag in ("Explicatus", "Rubrica"):
                continue
            else:
                raise RetsinformationParseError(f"{label}: unexpected <{child.tag}> in <{el.tag}>")

    for n, part in enumerate(body, start=1):
        if part.tag == "Bog":
            walk(part, [])
        elif part.tag == "Ikraft":
            notes = _Notes()
            exitus = part.find("Exitus")
            intro = _exitus_lines(exitus, notes, frozenset()) if exitus is not None else []
            act_line = intro[0] if intro else ""
            match = AMENDING_ACT.match(act_line)
            base = f"ikraft-lov-{match.group(4)}-{match.group(1)}" if match else f"ikraft-{n}"
            act = act_line[: match.end()] if match else act_line
            act = act[:1].lower() + act[1:]
            for par in [p for p in part if p.tag in ("Paragraf", "IkraftCentreretParagraf")]:
                unit = _paragraph(par, f"{base}-par-{_num_slug(_paragraph_number(par))}", "commencement_provision")
                unit.children = []  # one provision per reproduced commencement paragraph
                unit.own = False
                unit.notes = [*unit.notes, *notes.items]
                emit(
                    unit,
                    None,
                    [f"act:{act}"],
                    "commencement_provision",
                    act_line or None,
                    "Ikrafttrædelsesbestemmelser",
                )
        elif part.tag in ("Indledning",):
            continue
        else:
            raise RetsinformationParseError(f"{label}: unexpected <{part.tag}> in <DokumentIndhold>")
    if not provisions:
        raise RetsinformationParseError(f"{label}: no provisions found")

    changes = _changes(meta)
    work_uri = work_uri_for(popular)
    outstanding = [
        {
            "affects_eid": None,
            "affected_label": None,
            "by_uri": c.uri,
            "by_title": c.title or c.accn,
            "description": (
                f"Amending act{' of ' + c.dated.isoformat() if c.dated else ''} listed by Retsinformation as a later "
                f'change to {label} ("Senere ændringer til forskriften"); not incorporated in this consolidation. '
                "Whether and when it took effect is stated in the act itself."
            ),
            "effective_date": None,
        }
        for c in changes
    ]
    events = [
        ParsedEvent("version_change", signed, eli, f"{label} ({m('DocumentTitle')}), Lovtidende A", "Meta/DiesSigni")
    ]
    events += [
        ParsedEvent("amendment", c.dated, c.uri, f"{c.title} (later change to {label})", "Meta/Change")
        for c in changes
        if c.dated is not None
    ]
    ext: dict[str, object] = {"popular_title": popular, "document_type": doc_type}
    return ParsedDocument(
        doc_kind="legislation",
        instrument_type="act",
        jurisdiction_id=JURISDICTION,
        work_uri=work_uri,
        expression_uri=f"{work_uri}/dan@lbk-{year}-{number}",
        work_title=popular,
        title=m("DocumentTitle"),
        short_title=popular,
        number=None,
        work_date=None,
        language="da",
        authoritative=False,
        text_origin="official_consolidation",
        consolidation_kind="point_in_time",
        version_date=signed,
        currency_date=signed,
        extent=[JURISDICTION],
        ext_module="dk",
        ext=ext,
        outstanding_changes=outstanding if status == "Valid" else [],
        events=events,
        provisions=provisions,
        validity_basis=SNAPSHOT_BASIS,
    )


def parse_retsinformation(content: bytes) -> list[ParsedDocument]:
    root = parse_xml(content)
    if root.tag != "Dokument":
        raise RetsinformationParseError(f"unsupported root element {root.tag!r} (expected Lex Dania <Dokument>)")
    return [parse_lbk(root)]
