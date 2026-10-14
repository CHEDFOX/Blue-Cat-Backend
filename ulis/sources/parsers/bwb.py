"""Parser for Dutch national legislation from the Basiswettenbestand (BWB), published by KOOP.

The BWB holds the consolidated texts of Dutch regulations. Its open-data repository
(``https://repository.officiele-overheidspublicaties.nl/bwb/<BWB id>/``) offers, per regulation,
a ``manifest.xml`` listing every *toestand* (consolidated version) with its validity period, and
one XML file per toestand (schema ``toestand_2015-2`` / ``2016-1``). Two kinds of document are
parsed:

* a **toestand** (root ``<toestand>``): the whole regulation as valid from the toestand's
  ``inwerkingtreding`` until the next toestand. Every regulation element carries its own
  ``@inwerking`` (the date that version of the element entered into force), ``@bron`` (the
  Staatsblad/Staatscourant publication) and ``@effect``; an article's members (leden) and list
  items have no dates of their own and share the article's. ULIS dates each provision from its
  article's ``@inwerking`` (``valid_from``) and records the toestand as a whole-instrument snapshot
  (``validity_basis = "revision_snapshot"``, ``ulis.ingestion.snapshots``): an unchanged wording
  continues the version recorded from the previous toestand, and a provision absent from a later
  toestand ends on that toestand's date;
* a **manifest** (root ``<work>``): becomes a *change report* for the current toestand. Toestanden
  that start after the date the manifest was generated (``@gegenereerd``) are outstanding
  changes (scheduled consolidations, not law yet, never ingested as text); the start of every
  other toestand is a lifecycle event (``version_change``).

How a toestand is read:

* structural divisions (boek, titeldeel, hoofdstuk, afdeling, paragraaf, divisie) are headings
  (``context_heading``), not provisions. Provisions are articles (``art-<nr>``, e.g. ``art-162``,
  ``art-3:4``), their members (``art-162-lid-1``) and the items of numbered lists
  (``art-1-onderdeel-a``, nested ``...-onderdeel-a-onderdeel-1``), and annexes (``bijlage-2``,
  one provision each). An article without a number (the Grondwet's "Algemene bepaling") is named
  from its heading. Lists whose items are not citable (dashes, unmarked) stay text; so do members
  or items whose numbers repeat within one article (parallel texts such as Awb art. 8:36c), since
  "lid 1" would be ambiguous;
* **text is exact source text.** ``<redactie>`` elements are editorial (KOOP's "Vervallen",
  "Dit lid is nog niet in werking getreden.", "Wijzigt deze wet.", "Per 1 januari 2026: € 550.")
  and go to ``note``, never to ``text``; so do the source publication (``@bron``), the remarks in
  ``opmerkingen-inhoud`` and any retroactive date (``terugwerkend.datum``). Containers keep their
  members' numbers and item labels; a leaf's text starts after its own label;
* **status**: ``@status="vervallen"`` (or an element whose only content is the editorial
  "Vervallen") is repealed, with empty text and ``valid_from`` = the repeal's ``@inwerking``;
  ``@status="nogniet"`` (or "nog niet in werking getreden") is prospective, with no start date;
* **retroactive effect** (``terugwerkend.datum``): the element still starts on its entry into
  force (``@inwerking``); the retroactive date is stated in the note, since the earlier toestanden
  attest a different text for the retroactive period;
* the preamble (``aanhef``), closing formula (``wetsluiting``) and structural editorial text are
  not provisions.

The site states that consolidated texts are not official texts ("geen officiële teksten in de zin
van de Grondwet"); only the Staatsblad, Staatscourant and other official publication bulletins
are. Text origin is therefore ``official_revised`` and never authoritative.

XML safety: parsed with defusedxml, DTDs, entities and external references forbidden. Only the
roots ``toestand`` and ``work`` are accepted.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import date
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.ingestion.snapshots import CHANGE_REPORT, SNAPSHOT_BASIS
from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParsedRef

PARSER_ID = "bwb_xml"
PARSER_VERSION = "1.0.0"
JURISDICTION = "NL"
REPOSITORY = "https://repository.officiele-overheidspublicaties.nl/bwb"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
BWB_ID = re.compile(r"^BWB[RV]\d{7}$")
TOESTAND_LABEL = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d+)$")
OPEN_END = date(9999, 12, 31)
NOTE_LIMIT = 1200

STRUCTURE = {"boek", "deel", "titeldeel", "hoofdstuk", "afdeling", "paragraaf", "sub-paragraaf", "divisie"}
NOT_PROVISIONS = {"kop", "meta-data", "structuurtekst"}
# wetgeving/@soort -> ULIS instrument type (the Grondwet is a "wet" in the BWB).
INSTRUMENT_TYPES = {"wet": "act", "AMvB": "order_in_council", "ministeriele-regeling": "ministerial_regulation"}
STATUS = {"goed": "in_force", "vervallen": "repealed", "nogniet": "prospective"}
REPEALED_NOTE = re.compile(r"^vervallen\.?$", re.IGNORECASE)
NOT_YET_NOTE = re.compile(r"\bnog niet\s*in\s*werking\b", re.IGNORECASE)
# Citable list labels: a. / aa. / A. / 1. / 1°. / 2a. / i. / IV.
ITEM_LABEL = re.compile(r"^(?:[a-z]{1,3}|[A-Z]{1,2}|\d+[a-z]{0,2}°?|[ivxlc]+|[IVXLC]+)\.?$")
_GAP = "\x00"  # where an editorial insertion was removed from a text


class BwbParseError(ValueError):
    pass


# --------------------------------------------------------------------------- input


def parse_xml(content: bytes) -> Element:
    try:
        return fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception as exc:
        raise BwbParseError(f"not well-formed or unsafe XML: {exc}") from exc


def _date(value: str | None) -> date | None:
    value = (value or "").strip()[:10]
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- manifest


@dataclass(frozen=True)
class Toestand:
    """One consolidated version listed in a regulation's ``manifest.xml``."""

    label: str  # e.g. 2023-02-22_0
    start: date  # datum_inwerkingtreding
    end: date | None  # einddatum (last day, inclusive); None when open (9999-12-31)
    visible_from: date | None
    visible_to: date | None
    item: str  # file name, e.g. BWBR0001840_2023-02-22_0.xml
    sha512: str | None
    size: int | None

    def url(self, bwb_id: str) -> str:
        return f"{REPOSITORY}/{bwb_id}/{self.label}/xml/{self.item}"


@dataclass(frozen=True)
class Listing:
    bwb_id: str
    generated: date
    entry_into_force: date | None
    past: list[Toestand]  # started on or before ``generated``, oldest first
    scheduled: list[Toestand]  # start after ``generated``

    @property
    def current(self) -> Toestand:
        return self.past[-1]


def read_manifest(content: bytes) -> Listing:
    """Read a BWB ``manifest.xml``: the toestanden visible when it was generated, split at that date."""
    root = parse_xml(content)
    if root.tag != "work":
        raise BwbParseError(f"unsupported manifest root {root.tag!r} (expected <work>)")
    return _listing(root)


def _listing(root: Element) -> Listing:
    bwb_id = (root.get("label") or "").strip()
    if not BWB_ID.match(bwb_id):
        raise BwbParseError(f"invalid BWB id {bwb_id!r} in manifest")
    generated = _date(root.get("gegenereerd"))
    if generated is None:
        raise BwbParseError(f"{bwb_id}: manifest has no valid @gegenereerd date")
    toestanden: list[Toestand] = []
    for expr in root.findall("expression"):
        label = expr.get("label") or ""
        meta = expr.find("metadata")
        manifestation = next((m for m in expr.findall("manifestation") if m.get("label") == "xml"), None)
        if meta is None or manifestation is None:
            continue
        items = [i for i in manifestation.findall("item") if i.get("_deleted") != "true"]
        if not TOESTAND_LABEL.match(label) or len(items) != 1:
            continue
        item = items[0].get("label") or ""
        if item != f"{bwb_id}_{label}.xml":
            raise BwbParseError(f"{bwb_id}: unexpected item {item!r} for toestand {label}")
        start = _date(meta.findtext("datum_inwerkingtreding"))
        if start is None:
            raise BwbParseError(f"{bwb_id}: toestand {label} has no start date")
        end = _date(meta.findtext("einddatum"))
        visible_to = _date(meta.findtext("zichtdatum_eind"))
        size = (manifestation.findtext("metadata/size") or "").strip()
        toestanden.append(
            Toestand(
                label=label,
                start=start,
                end=None if end is None or end == OPEN_END else end,
                visible_from=_date(meta.findtext("zichtdatum_start")),
                visible_to=None if visible_to is None or visible_to == OPEN_END else visible_to,
                item=item,
                sha512=(manifestation.findtext("metadata/hashcode") or "").strip().lower() or None,
                size=int(size) if size.isdigit() else None,
            )
        )
    # A toestand no longer visible when the manifest was generated was superseded by a corrected one.
    visible = sorted(
        (t for t in toestanden if t.visible_to is None or t.visible_to >= generated), key=lambda t: (t.start, t.label)
    )
    starts = [t.start for t in visible]
    if len(starts) != len(set(starts)):
        raise BwbParseError(f"{bwb_id}: several visible toestanden start on the same date")
    past = [t for t in visible if t.start <= generated]
    if not past:
        raise BwbParseError(f"{bwb_id}: no toestand in force when the manifest was generated")
    return Listing(
        bwb_id=bwb_id,
        generated=generated,
        entry_into_force=_date(root.findtext("metadata/datum_inwerkingtreding")),
        past=past,
        scheduled=[t for t in visible if t.start > generated],
    )


# --------------------------------------------------------------------------- text


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _close_gaps(text: str) -> str:
    """Remove the marks left where editorial text was taken out, without leaving stray spaces."""
    text = re.sub(rf"\s*{_GAP}+\s*(?=[;:.,)])", "", text)
    return _ws(re.sub(rf"\s*{_GAP}+\s*", " ", text))


@dataclass
class _Collect:
    notes: list[str] = field(default_factory=list)
    refs: list[ParsedRef] = field(default_factory=list)

    def note(self, text: str) -> None:
        if text and text not in self.notes:
            self.notes.append(text)


def _inline(el: Element, out: _Collect | None = None) -> str:
    """Inline text of ``el``: editorial insertions (``redactie``) go to ``out.notes``, metadata is skipped."""
    parts = [el.text or ""]
    for child in el:
        if child.tag == "meta-data":
            pass
        elif child.tag == "redactie":
            if out is not None:
                remark = _ws("".join(child.itertext()))
                before = _close_gaps("".join(parts))
                # An insertion inside a sentence is noted with the words it follows, e.g. the amount it updates.
                if child.get("type") == "extra" and before:
                    anchor = before if len(before) <= 60 else before[-60:].split(" ", 1)[-1]
                    remark = f"Redactioneel, bij “{anchor}”: {remark}"
                out.note(remark)
            parts.append(_GAP)
        else:
            if out is not None and child.tag in ("intref", "extref") and child.get("doc"):
                out.refs.append(
                    ParsedRef(href=child.get("doc", ""), text=_ws("".join(child.itertext())), relation="cites")
                )
            parts.append(_inline(child, out))
        parts.append(child.tail or "")
    return "".join(parts)


def _clean(el: Element, out: _Collect | None = None) -> str:
    return _close_gaps(_inline(el, out))


def _kop(el: Element) -> str:
    """``Hoofdstuk 1 Grondrechten`` / ``Afdeling 1. Algemene bepalingen`` from a structural element's kop."""
    kop = el.find("kop")
    if kop is None:
        return ""
    return _ws(" ".join(_clean(part) for part in kop if part.tag in ("label", "nr", "titel")))


def _table(el: Element, out: _Collect | None) -> list[str]:
    rows = []
    for row in el.iter("row"):
        cells = [_clean(entry, out) for entry in row.iter("entry")]
        if any(cells):
            rows.append(" | ".join(cells))
    return rows


def _lines(el: Element, out: _Collect | None, skip: frozenset[Element] = frozenset()) -> list[str]:
    """The text of a block (article, member, item, annex) as lines, with members' numbers and items'
    labels; editorial insertions go to ``out``. Elements in ``skip`` (split-off children) are left out."""
    lines: list[str] = []
    for child in el:
        if child in skip or child.tag in ("kop", "lidnr", "li.nr", "meta-data"):
            continue
        if child.tag == "lid":
            body = _lines(child, out)
            if body:
                number = _ws(child.findtext("lidnr") or "")
                lines.append(f"{number} {body[0]}".strip())
                lines.extend(body[1:])
        elif child.tag == "lijst":
            for li in child.findall("li"):
                if li in skip:
                    continue
                body = _lines(li, out)
                label = _ws(li.findtext("li.nr") or "")
                if body:
                    lines.append(f"{label} {body[0]}".strip())
                    lines.extend(body[1:])
        elif child.tag in ("table", "tabel"):
            lines.extend(_table(child, out))
        elif child.tag in ("artikel", "divisie") or child.tag in STRUCTURE:
            heading = _kop(child)
            if heading:
                lines.append(heading)
            lines.extend(_lines(child, out))
        else:  # al, tussenkop and any other inline block
            text = _clean(child, out)
            if text:
                lines.append(text)
    return lines


def _editorial_only(el: Element) -> str | None:
    """The editorial text of a block whose whole content is a ``redactie`` replacement (e.g. "Vervallen")."""
    out = _Collect()
    if _lines(el, out):
        return None
    texts = [_ws("".join(r.itertext())) for r in el.iter("redactie") if r.get("type") == "vervanging"]
    return "; ".join(t for t in texts if t) or None


# --------------------------------------------------------------------------- units


@dataclass
class _Unit:
    kind: str
    eid: str
    label: str
    text: str
    status: str
    own: bool
    notes: list[str]  # notes on this unit's text (which includes its children's)
    refs: list[ParsedRef]
    children: list[_Unit] = field(default_factory=list)
    shared: list[str] = field(default_factory=list)  # notes that also apply to its children


def _slug(text: str) -> str:
    text = text.replace("°", "")
    return re.sub(r"[^A-Za-z0-9:.]+", "-", text).strip("-.").lower() or "x"


def _item_units(container: Element, status: str) -> tuple[list[_Unit], frozenset[Element]]:
    """Items of the citable lists directly under ``container``; none when a label repeats (ambiguous)."""
    items: list[tuple[str, Element]] = []
    for lijst in container.findall("lijst"):
        entries = [(_ws(li.findtext("li.nr") or ""), li) for li in lijst.findall("li")]
        if lijst.get("type") == "ongemarkeerd" or not entries or not all(ITEM_LABEL.match(lab) for lab, _ in entries):
            continue
        items.extend(entries)
    labels = [lab.rstrip(".") for lab, _ in items]
    if len(labels) != len(set(labels)):
        return [], frozenset()
    units = [_block("item", f"onderdeel-{_slug(lab)}", lab, li, status) for lab, li in items]
    return units, frozenset(li for _, li in items)


def _block(kind: str, eid: str, label: str, el: Element, status: str) -> _Unit:
    """A member or list item: its own text, notes and references, and its split-off items."""
    editorial = _editorial_only(el)
    if editorial is not None:
        if REPEALED_NOTE.match(editorial):
            status = "repealed"
        elif NOT_YET_NOTE.search(editorial):
            status = "prospective"
        return _Unit(kind, eid, label, "", status, False, [editorial], [])
    children, split = _item_units(el, status)
    whole = _Collect()
    text = "\n".join(_lines(el, whole))
    own = _Collect()
    own_text = "\n".join(_lines(el, own, split))
    refs = own.refs if children else whole.refs
    own_content = bool(children) and bool(own_text)
    return _Unit(kind, eid, label, text, status, own_content, whole.notes, refs, children, own.notes)


def _article_number(art: Element) -> str | None:
    nr = _ws(art.findtext("kop/nr") or "")
    if nr:
        return nr.rstrip(".")
    for label in (art.get("label") or "", _ws(art.findtext("kop/label") or "")):
        m = re.fullmatch(r"(?:Artikel|Art\.)\s+(\S+)", label.strip())
        if m:
            return m.group(1).rstrip(".")
    return None


def _source_notes(el: Element) -> list[str]:
    """Publication, effect, retroactivity and editorial remarks of an element (from its own metadata)."""
    notes: list[str] = []
    if el.get("bron"):
        effect = (el.get("effect") or "").replace("-", " ")
        notes.append(f"Bron: {el.get('bron')}" + (f" ({effect})" if effect else ""))
    meta = el.find("meta-data")
    if meta is not None:
        for retro in meta.iter("terugwerkend.datum"):
            when = retro.get("isodatum") or _ws(retro.text or "")
            scope = (retro.get("datum-scope") or "").replace("-", " ")
            notes.append(f"Terugwerkende kracht {scope} {when}".replace("  ", " ").strip())
        for remark in meta.iter("opmerkingen-inhoud"):
            text = _ws(" ".join(_clean(al) for al in remark.iter("al")))
            if text:
                notes.append(text)
    return notes


def _article(art: Element, toestand_start: date) -> tuple[_Unit, date | None]:
    raw_status = art.get("status") or "goed"
    if raw_status not in STATUS:
        raise BwbParseError(f"{art.get('bwb-ng-variabel-deel')}: unknown status {raw_status!r}")
    status = STATUS[raw_status]
    nr = _article_number(art)
    titel = art.find("kop/titel")
    heading = _clean(titel) if titel is not None else ""
    if nr:
        kind, eid, label = "article", f"art-{_slug(nr)}", nr
    else:
        kind, eid, label = (
            "general_provision",
            f"art-{_slug(heading or art.get('bwb-ng-variabel-deel') or 'x')}",
            heading,
        )
    source = _source_notes(art)
    start = _date(art.get("inwerking"))
    if status == "prospective":
        start = None
    elif start is None:  # not seen in the BWB files read so far; the toestand attests it from its start
        start = toestand_start
        source.append(f"Geen inwerkingtredingsdatum vermeld; opgenomen vanaf de toestand van {toestand_start}")
    editorial = _editorial_only(art)
    if editorial is not None:
        if REPEALED_NOTE.match(editorial):
            status = "repealed"
        elif NOT_YET_NOTE.search(editorial):
            status, start = "prospective", None
        return _Unit(kind, eid, label, "", status, False, [editorial, *source], []), start
    if status == "repealed":
        whole = _Collect()
        _lines(art, whole)
        return _Unit(kind, eid, label, "", status, False, [*whole.notes, *source], []), start

    leden = art.findall("lid")
    numbers = [_ws(lid.findtext("lidnr") or "").rstrip(".") for lid in leden]
    children: list[_Unit] = []
    split: frozenset[Element] = frozenset()
    if leden and all(numbers) and len(numbers) == len(set(numbers)):
        children = [
            _block("subsection", f"lid-{_slug(n)}", f"lid {n}", lid, status)
            for n, lid in zip(numbers, leden, strict=True)
        ]
        split = frozenset(leden)
    elif not leden:
        children, split = _item_units(art, status)
    whole = _Collect()
    text = "\n".join(_lines(art, whole))
    own = _Collect()
    own_text = "\n".join(_lines(art, own, split))
    unit = _Unit(
        kind,
        eid,
        label,
        text,
        status,
        bool(children) and bool(own_text),
        [*whole.notes, *source],
        own.refs if children else whole.refs,
        children,
        [*own.notes, *source],
    )
    return unit, start


def _annex(bijlage: Element) -> tuple[_Unit, date | None, str | None]:
    raw_status = bijlage.get("status") or "goed"
    if raw_status not in STATUS:
        raise BwbParseError(f"{bijlage.get('bwb-ng-variabel-deel')}: unknown status {raw_status!r}")
    status = STATUS[raw_status]
    nr = _ws(bijlage.findtext("kop/nr") or "") or re.sub(r"^Bijlage\s*", "", bijlage.get("label") or "").strip()
    label = f"Bijlage {nr}".strip()
    titel = bijlage.find("kop/titel")
    heading = _clean(titel) if titel is not None else None
    whole = _Collect()
    text = "" if status == "repealed" else "\n".join(_lines(bijlage, whole))
    start = None if status == "prospective" else _date(bijlage.get("inwerking"))
    unit = _Unit(
        "annex",
        f"bijlage-{_slug(nr or 'x')}",
        label,
        text,
        status,
        False,
        [*whole.notes, *_source_notes(bijlage)],
        whole.refs,
    )
    return unit, start, heading


# --------------------------------------------------------------------------- identifiers


def work_uri_for(bwb_id: str) -> str:
    return f"/nl/bwb/{bwb_id}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map a Juriconnect reference (as in BWB ``intref``/``extref``) to (ULIS work URI, eId).

    ``jci1.3:c:BWBR0001840&hoofdstuk=7&artikel=132a&z=2023-02-22&g=2023-02-22`` ->
    (``/nl/bwb/BWBR0001840``, ``art-132a``); ``...&artikel=4%3A21&lid=2`` -> ``art-4:21-lid-2``.
    """
    uri = re.sub(r"^https?://wetten\.overheid\.nl/", "", uri.strip())
    m = re.match(r"^(?:jci)?\d\.\d+:c:(BWB[RV]\d{7})(?:&(.*))?$", uri)
    if not m:
        return None
    work = work_uri_for(m.group(1))
    params = dict(urllib.parse.parse_qsl(m.group(2) or "", keep_blank_values=True))
    artikel = params.get("artikel", "").strip()
    if not artikel:
        return work, None
    eid = f"art-{_slug(artikel)}"
    if params.get("lid", "").strip():
        eid += f"-lid-{_slug(params['lid'])}"
    return work, eid


# --------------------------------------------------------------------------- documents


def _instrument_type(soort: str, citeertitel: str) -> str:
    if soort == "wet" and citeertitel == "Grondwet":
        return "constitution"
    if soort not in INSTRUMENT_TYPES:
        raise BwbParseError(f"unsupported regulation type (wetgeving/@soort) {soort!r}")
    return INSTRUMENT_TYPES[soort]


def _ext(bwb_id: str, citeertitel: str | None, soort: str | None) -> dict[str, object]:
    ext: dict[str, object] = {"bwb_id": bwb_id, "jci": f"jci1.3:c:{bwb_id}"}
    if citeertitel:
        ext["citeertitel"] = citeertitel
        book = re.fullmatch(r"Burgerlijk Wetboek Boek (\d+[a-z]?)", citeertitel)
        if book:
            ext["boek"] = book.group(1)
    if soort:
        ext["soort"] = soort
    return ext


def _join(notes: list[str]) -> str | None:
    note = "; ".join(dict.fromkeys(n for n in notes if n))
    if len(note) > NOTE_LIMIT:
        note = note[:NOTE_LIMIT].rstrip() + " […]"
    return note or None


def parse_toestand(root: Element) -> ParsedDocument:
    bwb_id = (root.get("bwb-id") or "").strip()
    if not BWB_ID.match(bwb_id):
        raise BwbParseError(f"invalid toestand/@bwb-id {bwb_id!r}")
    start = _date(root.get("inwerkingtreding"))
    if start is None:
        raise BwbParseError(f"{bwb_id}: missing or invalid toestand/@inwerkingtreding")
    m = re.search(r"/(\d{4}-\d{2}-\d{2})/(\d+)$", root.get("bwb-ng-vast-deel") or "")
    label = f"{m.group(1)}_{m.group(2)}" if m else f"{start.isoformat()}_0"
    if m and m.group(1) != start.isoformat():
        raise BwbParseError(f"{bwb_id}: bwb-ng-vast-deel date differs from @inwerkingtreding")
    wetgeving = root.find("wetgeving")
    if wetgeving is None:
        raise BwbParseError(f"{bwb_id}: no <wetgeving>")
    if (wetgeving.get(XML_LANG) or "nl") != "nl":
        raise BwbParseError(f"{bwb_id}: unexpected language {wetgeving.get(XML_LANG)!r}")
    soort = (wetgeving.get("soort") or "").strip()
    cite_el = wetgeving.find("citeertitel")
    citeertitel = _clean(cite_el) if cite_el is not None else ""
    intitule_el = wetgeving.find("intitule")
    intitule = _clean(intitule_el) if intitule_el is not None else ""
    title = intitule or citeertitel or bwb_id
    instrument_type = _instrument_type(soort, citeertitel)
    work_date = _date(intitule_el.get("inwerking")) if intitule_el is not None else None
    work_uri = work_uri_for(bwb_id)

    provisions: list[ParsedProvision] = []
    used: dict[str, int] = {}

    def unique(eid: str) -> str:
        n = used.get(eid, 0) + 1
        used[eid] = n
        return eid if n == 1 else f"{eid}-{n}"

    def emit(
        unit: _Unit,
        parent: str | None,
        path: list[str],
        top_kind: str,
        heading: str | None,
        valid_from: date | None,
        inherited: list[str],
        context: str | None,
    ) -> None:
        eid = unique(f"{parent}-{unit.eid}" if parent else unit.eid)
        status = unit.status
        start = None if status == "prospective" else valid_from
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=unit.kind,
                num_path=[*path, unit.label],
                top_kind=top_kind,
                heading=heading,
                text="" if status == "repealed" else unit.text,
                parent_eid=parent,
                sort_order=len(provisions),
                status=status,  # type: ignore[arg-type]
                valid_from=start,
                valid_to=None,
                extent=[JURISDICTION],
                note=_join([*unit.notes, *inherited]),
                refs=unit.refs,
                is_leaf=not unit.children,
                has_own_content=unit.own,
                context_heading=context,
            )
        )
        for child in unit.children:
            # Members and items share their article's publication and editorial notes.
            emit(child, eid, [*path, unit.label], top_kind, None, start, [*inherited, *unit.shared], context)

    def walk(el: Element, outline: list[str]) -> None:
        for child in el:
            if child.tag in STRUCTURE:
                walk(child, [*outline, _kop(child)])
            elif child.tag == "artikel":
                unit, valid_from = _article(child, start)
                heading_el = child.find("kop/titel")
                heading = _clean(heading_el) if heading_el is not None and unit.kind == "article" else None
                context = " – ".join(h for h in outline if h) or None
                emit(unit, None, [], unit.kind, heading or None, valid_from, [], context)
            elif child.tag in NOT_PROVISIONS:
                continue
            else:
                raise BwbParseError(f"{bwb_id}: unexpected element <{child.tag}> in <{el.tag}>")

    besluit = wetgeving.find("wet-besluit")
    if besluit is None:
        raise BwbParseError(f"{bwb_id}: no <wet-besluit>")
    for part in besluit:
        if part.tag == "wettekst":
            walk(part, [])
        elif part.tag == "bijlage":
            unit, valid_from, heading = _annex(part)
            emit(unit, None, [], "annex", heading, valid_from, [], None)
        elif part.tag in ("aanhef", "wetsluiting", "meta-data"):
            continue  # preamble and closing formula are not provisions
        else:
            raise BwbParseError(f"{bwb_id}: unexpected element <{part.tag}> in <wet-besluit>")
    if not provisions:
        raise BwbParseError(f"{bwb_id}: no provisions found")

    # The start of every toestand is recorded from the manifest (change report), which lists them all.
    events: list[ParsedEvent] = []
    if work_date is not None:
        bron = intitule_el.get("bron") if intitule_el is not None else None
        description = f"Inwerkingtreding regeling ({bron})" if bron else "Inwerkingtreding regeling"
        events.append(ParsedEvent("entry_into_force", work_date, None, description, "wetgeving/intitule/@inwerking"))
    return ParsedDocument(
        doc_kind="legislation",
        instrument_type=instrument_type,
        jurisdiction_id=JURISDICTION,
        work_uri=work_uri,
        expression_uri=f"{work_uri}/nld@{label}",
        work_title=title,
        title=title,
        short_title=citeertitel or None,
        number=None,
        work_date=work_date,
        language="nl",
        authoritative=False,  # a consolidation; the Staatsblad / Staatscourant publications are authentic
        text_origin="official_revised",
        consolidation_kind="point_in_time",
        version_date=start,
        currency_date=start,
        extent=[JURISDICTION],
        ext_module="nl",
        ext=_ext(bwb_id, citeertitel, soort),
        events=events,
        provisions=provisions,
        validity_basis=SNAPSHOT_BASIS,
    )


def parse_manifest(root: Element) -> ParsedDocument:
    """A manifest as a change report on the current toestand (see the module docstring)."""
    listing = _listing(root)
    current = listing.current
    work_uri = work_uri_for(listing.bwb_id)
    outstanding: list[dict[str, object]] = []
    for t in listing.scheduled:
        period = f"from {t.start.isoformat()}" + (f" to {t.end.isoformat()}" if t.end else "")
        outstanding.append(
            {
                "affects_eid": None,
                "affected_label": None,
                "by_uri": t.url(listing.bwb_id),
                "by_title": f"BWB toestand {t.label}",
                "description": f"Consolidated version scheduled {period} (listed in the manifest generated "
                f"{listing.generated.isoformat()}); not applied to this text and not ingested: it is not law "
                "before that date and can still change.",
                "effective_date": t.start.isoformat(),
            }
        )
    events = [
        ParsedEvent(
            "version_change",
            t.start,
            None,
            f"BWB toestand {t.label}: geldig vanaf {t.start.isoformat()}"
            + (f" tot en met {t.end.isoformat()}" if t.end else ""),
            f"work/expression[@label='{t.label}']",
        )
        for t in listing.past
    ]
    # The manifest's own datum_inwerkingtreding is not used: for the Wetboek van Strafrecht it differs
    # from the date the toestand gives (intitule/@inwerking), which ULIS records.
    return ParsedDocument(
        doc_kind="legislation",
        instrument_type="act",  # not recorded by a change report (the toestand gives the type)
        jurisdiction_id=JURISDICTION,
        work_uri=work_uri,
        expression_uri=f"{work_uri}/nld@{current.label}",
        work_title=listing.bwb_id,
        title=listing.bwb_id,
        short_title=None,
        number=None,
        work_date=None,
        language="nl",
        authoritative=False,
        text_origin="official_revised",
        consolidation_kind=CHANGE_REPORT,
        version_date=current.start,
        currency_date=listing.generated,  # the listing attests the current toestand as current on this date
        extent=[JURISDICTION],
        ext_module="nl",
        ext=_ext(listing.bwb_id, None, None),
        outstanding_changes=outstanding,
        events=events,
        provisions=[],
        validity_basis=SNAPSHOT_BASIS,
    )


def parse_bwb(content: bytes) -> list[ParsedDocument]:
    root = parse_xml(content)
    if root.tag == "toestand":
        return [parse_toestand(root)]
    if root.tag == "work":
        return [parse_manifest(root)]
    raise BwbParseError(f"unsupported root element {root.tag!r} (expected BWB <toestand> or manifest <work>)")
