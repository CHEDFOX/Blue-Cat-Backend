"""Parser for Dutch court decisions from the Open Data van de Rechtspraak (``data.rechtspraak.nl``).

The web service returns one XML document per ECLI (root ``<open-rechtspraak>``): the ECLI register
metadata (``rdf:RDF`` with two ``rdf:Description``: the ECLI and the published text), the
``<inhoudsindicatie>`` (headnote) and the decision itself in ``<uitspraak>`` (schema ``rechtspraak-1.0``,
derived from DocBook). Only decisions (``uitspraak``) in Dutch are parsed; advisory opinions
(``conclusie``) and the publisher's English translations are refused.

What a judgment becomes:

* **metadata**: the court (from the OWMS identifier of ``dcterms:creator``, which must agree with the
  court code in the ECLI), the decision date (``dcterms:date``, "Uitspraakdatum"), the case numbers
  (``psi:zaaknummer``, joined with "; " as the docket) and the ECLI as neutral citation. The data has
  no case name; ``dcterms:alternative`` (an alias) is used when present, else the ECLI. Procedure,
  legal area, publications (vindplaatsen), replaced identifiers and dates go to ``ext``.
* **paragraphs** (exact source text; whitespace runs collapsed, ``footnote-ref`` shown as ``[n]``):
  the ``inhoudsindicatie`` as one ``headnote`` paragraph; then, in document order, every ``paragroup``
  with a number (``<nr>``) and every numbered ``section`` as one unit holding its own content (nested
  numbered units are separate); every other paragraph, list or table as its own unit. In documents
  whose numbering is part of the text ("4.1. Hoewel ..." in decisions of the Raad van State and many
  appeals courts) a leading marker such as ``4.1.`` is split off into ``num``. Section titles are
  not paragraph text. Footnotes become ``footnote`` paragraphs. Images are omitted.
* **num** is set only for units in ``considerations`` (the numbered rechtsoverwegingen, e.g.
  ``3.4.1``), so a citation ``r.o. 3.4.1`` never points at a procedural, decision or footnote unit.
  ``part`` is ``headnote``, ``procedure`` (``role="procesverloop"``), ``considerations``
  (``role="overwegingen"`` and numbered units elsewhere), ``decision`` (``role="beslissing"``),
  ``footnote`` or ``text``; in flat documents the headings "Procesverloop", "Overwegingen" and
  "Beslissing" switch the part.
* **references** (citation links, never guessed): the ``dcterms:references`` "Wetsverwijzing"
  identifiers (Juriconnect ``jci1.31:c:BWBR...``) and the ``dcterms:relation`` formal relations (earlier
  or later instance, advisory opinion) are the publisher's markup and attach to the headnote;
  statute articles and ECLIs are also found in the text by strict patterns (``art. 6:162 lid 1 BW``,
  ``art. 3:4 Awb``, ``art. 294.2 Sr``, ``art. 22 UAVG``, ``art. 120 Grondwet``; ``ECLI:NL:...``), marked
  ``pattern``. A pattern needs the article number directly followed by the abbreviation; anything
  else (lists of articles, "oud BW", books of the BW that are not ingested) is left unlinked.

The published text is pseudonymised by the Rechtspraak; ULIS stores it as published and marks it
non-authoritative.

XML safety: parsed with defusedxml (DTDs and entities forbidden).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedJudgmentMeta, ParsedParagraph, ParsedRef

PARSER_VERSION = "1.0.0"

RS = "{http://www.rechtspraak.nl/schema/rechtspraak-1.0}"
DC = "{http://purl.org/dc/terms/}"
RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
RDFS = "{http://www.w3.org/2000/01/rdf-schema#}"
PSI = "{http://psi.rechtspraak.nl/}"
ECLI_NS = "{https://e-justice.europa.eu/ecli}"
BWB_NS = "{bwb-dl}"

OWMS = "http://standaarden.overheid.nl/owms/terms/"
# OWMS identifier of dcterms:creator -> (ULIS court id, ECLI court code)
COURTS: dict[str, tuple[str, str]] = {
    f"{OWMS}Hoge_Raad_der_Nederlanden": ("NL-HR", "HR"),
    f"{OWMS}Raad_van_State": ("NL-ABRVS", "RVS"),
    f"{OWMS}Centrale_Raad_van_Beroep": ("NL-CRVB", "CRVB"),
    f"{OWMS}College_van_Beroep_voor_het_bedrijfsleven": ("NL-CBB", "CBB"),
    f"{OWMS}Gerechtshof_Den_Haag": ("NL-GHDHA", "GHDHA"),
    f"{OWMS}Rechtbank_Den_Haag": ("NL-RBDHA", "RBDHA"),
}

ECLI = re.compile(r"ECLI:NL:([A-Z0-9]{2,12}):(\d{4}):([A-Za-z0-9]{1,25})")
LINEBREAK = "\ue000"  # stands for the <?linebreak?> processing instruction while parsing
SPACE = re.compile(r"[ \t\r\n\xa0\u2009\u202f]+")
NUMBER_MARKER = re.compile(r"^(\d+(?:\.\d+)*)\.(?=[ \t\r\n\xa0])")
TRANSLATION = re.compile(r"\((?:Engels|English|engelse|engelse vertaling|English translation)\b", re.IGNORECASE)
# Standard headings of flat documents (decisions of the Raad van State, CBb, ...): part switches.
HEADINGS = {"procesverloop": "procedure", "overwegingen": "considerations", "beslissing": "decision"}
ROLE_PART = {"procesverloop": "procedure", "overwegingen": "considerations", "beslissing": "decision"}
PREFIX = {
    "headnote": "headnote",
    "procedure": "pv",
    "considerations": "rov",
    "decision": "dec",
    "text": "txt",
    "footnote": "fn",
}
STRUCTURAL_TAGS = {"section", "paragroup"}
TRANSPARENT_TAGS = {"parablock", "uitspraak.info"}
SKIP_TAGS = {"title", "nr", "footnote", "footnote-ref", "mediaobject", "inlinemediaobject", "imageobject"}

# Statute abbreviations that map to one regulation of the nl-bwb source (profile extension nl.abbreviations).
BW_BOOKS = {"1": "BWBR0002656", "6": "BWBR0005289"}
STATUTES = {
    "Awb": "BWBR0005537",
    "Sr": "BWBR0001854",
    "Gw": "BWBR0001840",
    "Grondwet": "BWBR0001840",
    "UAVG": "BWBR0040940",
}
_ART = r"\bart(?:ikel)?\.?[ \xa0]*"
_ITEM = r"(?:[ \xa0]+(?:onder|sub)[ \xa0]+[a-z0-9°]+)*"
COLON_STATUTE = re.compile(
    _ART
    + r"(?P<book>\d{1,2}):(?P<art>\d+[a-z]{0,3})(?:[ \xa0]+lid[ \xa0]+(?P<lid>\d+))?"
    + _ITEM
    + r"[ \xa0]+(?P<abbr>BW|Awb)\b"
)
PLAIN_STATUTE = re.compile(
    _ART
    + r"(?P<art>\d+[a-z]{0,3})(?:\.(?P<lid1>\d+))?(?:[ \xa0]+lid[ \xa0]+(?P<lid>\d+))?"
    + _ITEM
    + r"[ \xa0]+(?P<abbr>Sr|Gw|Grondwet|UAVG)\b"
)


class RechtspraakParseError(ValueError):
    pass


def work_uri_for(ecli: str) -> str:
    return f"/nl/rechtspraak/{ecli}"


# --------------------------------------------------------------------------- text


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _norm(text: str) -> str:
    text = SPACE.sub(" ", text)
    text = re.sub(f" ?{LINEBREAK} ?", "\n", text)
    return text.replace(LINEBREAK, "\n").strip()


@dataclass
class _Footnotes:
    labels: dict[str, str] = field(default_factory=dict)  # footnote id -> label


def _inline(el: ET.Element, notes: _Footnotes) -> str:
    parts = [el.text or ""]
    for child in el:
        tag = _local(child.tag)
        if tag == "footnote-ref":
            label = notes.labels.get(child.get("linkend", ""))
            if label:
                parts.append(f"[{label}]")
        elif tag not in SKIP_TAGS:
            parts.append(_inline(child, notes))
        parts.append(child.tail or "")
    return "".join(parts)


def _roman(n: int) -> str:
    out = ""
    for value, sym in ((10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= value:
            out += sym
            n -= value
    return out


def _marker(numeration: str, n: int) -> str:
    """The label a rendered list shows for item ``n``; empty when the numbering scheme is not known."""
    if numeration == "arabic":
        return f"{n}."
    if numeration in ("loweralpha", "upperalpha") and n <= 26:
        letter = chr(96 + n)
        return f"{letter if numeration == 'loweralpha' else letter.upper()}."
    if numeration in ("lowerroman", "upperroman") and n < 40:
        return f"{_roman(n) if numeration == 'lowerroman' else _roman(n).upper()}."
    return ""


def _list_text(el: ET.Element, notes: _Footnotes) -> str:
    ordered = _local(el.tag) == "orderedlist"
    lines: list[str] = []
    for n, item in enumerate((c for c in el if _local(c.tag) == "listitem"), start=1):
        body = "\n".join(t for t in (_block_text(c, notes) for c in item) if t)
        if body:
            marker = _marker(el.get("numeration", "arabic"), n) if ordered else (el.get("mark") or "-")
            lines.append(f"{marker} {body}".strip())
    return "\n".join(lines)


def _table_text(el: ET.Element, notes: _Footnotes) -> str:
    rows = []
    for row in el.iter():
        if _local(row.tag) == "row":
            cells = [_norm(_inline(c, notes)) for c in row if _local(c.tag) == "entry"]
            if any(cells):
                rows.append(" | ".join(cells))
    return "\n".join(rows)


def _block_text(el: ET.Element, notes: _Footnotes) -> str:
    tag = _local(el.tag)
    if tag in ("orderedlist", "itemizedlist"):
        return _list_text(el, notes)
    if tag in ("table", "informaltable"):
        return _table_text(el, notes)
    if tag == "parablock":
        return "\n".join(t for t in (_block_text(c, notes) for c in el) if t)
    return _norm(_inline(el, notes))


# --------------------------------------------------------------------------- units


@dataclass
class _Unit:
    part: str
    text: str
    label: str | None = None  # number as printed (for the eid)
    num: str | None = None  # citable number: only for units in "considerations"


def _clean_number(text: str) -> str | None:
    label = _norm(text).rstrip(".").strip()
    return label if label and len(label) <= 20 and " " not in label else None


class _Units:
    def __init__(self, notes: _Footnotes):
        self.notes = notes
        self.units: list[_Unit] = []
        self.last_top = 0  # first component of the last number split off a flat paragraph
        self.body_started = False  # numbers in the header (a numbered list of parties) are not considerations

    def add(self, part: str, text: str, label: str | None, heading: bool = False) -> None:
        if not text:
            return
        num = label if part == "considerations" and not heading else None
        self.units.append(_Unit(part, text, label, num))

    def container(self, el: ET.Element, part: str, label: str | None, paragroup: bool = False) -> None:
        """Units of a uitspraak, section or paragroup. A numbered container keeps its own content together."""
        numbered = label is not None
        buffer: list[str] = []
        state = {"part": part, "base": part, "first": True}

        def flush() -> None:
            text = "\n".join(buffer)
            buffer.clear()
            if text:
                use = label if numbered and state["first"] else None
                # A numbered paragroup outside a section with a role is a numbered consideration.
                p = "considerations" if use and paragroup and state["part"] == "text" else str(state["part"])
                self.add(p, text, use)
                state["first"] = False

        for child in el:
            tag = _local(child.tag)
            if tag in STRUCTURAL_TAGS:
                flush()
                self._structural(child, part)
            elif tag in SKIP_TAGS:
                continue
            elif numbered:
                if text := _block_text(child, self.notes):
                    buffer.append(text)
            else:
                self._leaves(child, state)
        flush()

    def _structural(self, el: ET.Element, inherited: str) -> None:
        self.body_started = True
        part = ROLE_PART.get(el.get("role", ""), inherited)
        section = _local(el.tag) == "section"
        nr = el.find(f"{RS}title/{RS}nr") if section else el.find(f"{RS}nr")
        label = _clean_number("".join(nr.itertext())) if nr is not None else None
        self.container(el, part, label, paragroup=not section)

    def _leaves(self, el: ET.Element, state: dict[str, object]) -> None:
        """Unnumbered content: a paragraph, list or table is one unit; a parablock is transparent."""
        tag = _local(el.tag)
        if tag in TRANSPARENT_TAGS:
            # The front matter (uitspraak.info) may hold a table of contents: its lines are not headings.
            inner = {**state, "headings": False} if tag == "uitspraak.info" else state
            for child in el:
                if _local(child.tag) not in SKIP_TAGS:
                    self._leaves(child, inner)
            return
        if tag in STRUCTURAL_TAGS:
            self._structural(el, str(state["base"]))
            return
        text = _block_text(el, self.notes)
        if not text:
            return
        part = str(state["part"])
        label = None
        heading = False
        if tag == "para":
            marker = NUMBER_MARKER.match(text)
            rest = text[marker.end() :].strip() if marker else text
            number = marker.group(1) if marker and rest and len(marker.group(1)) <= 20 else None
            if state.get("headings", True) and rest.lower() in HEADINGS:
                # "Procesverloop", "1. Procesverloop": the body of the decision starts or changes part.
                state["part"] = part = HEADINGS[rest.lower()]
                text, label, heading = rest, number, True
                self.body_started = True
            elif number is not None and self.body_started:
                top = int(number.split(".")[0])
                # Numbering that starts over (an enumeration inside the decision, say) is not a consideration number.
                if top >= self.last_top:
                    self.last_top = top
                    text, label = rest, number
                    part = "considerations" if part == "text" else part
        self.add(part, text, label, heading)


def _merge(units: list[_Unit]) -> list[_Unit]:
    """Join what is one passage of the decision: the procedural history and the operative part (each a run
    of unnumbered paragraphs) and the header (everything before the first numbered or headed unit)."""
    out: list[_Unit] = []
    for unit in units:
        if out and unit.label is None and unit.part == out[-1].part and unit.part in ("procedure", "decision"):
            out[-1].text += "\n" + unit.text
        else:
            out.append(unit)
    first = next((i for i, u in enumerate(out) if u.label is not None or u.part != "text"), None)
    if first is not None and first > 1:
        out = [_Unit("text", "\n".join(u.text for u in out[:first])), *out[first:]]
    return out


# --------------------------------------------------------------------------- references


def _statute_refs(text: str) -> list[ParsedRef]:
    refs: dict[str, ParsedRef] = {}
    for m in COLON_STATUTE.finditer(text):
        if m["abbr"] == "BW":
            bwb = BW_BOOKS.get(m["book"])
            art = m["art"]
        else:
            bwb = STATUTES["Awb"]
            art = f"{m['book']}:{m['art']}"
        if bwb:
            _add(refs, bwb, art, m["lid"], m.group(0))
    for m in PLAIN_STATUTE.finditer(text):
        _add(refs, STATUTES[m["abbr"]], m["art"], m["lid"] or m["lid1"], m.group(0))
    return list(refs.values())


def _add(refs: dict[str, ParsedRef], bwb: str, art: str, lid: str | None, text: str) -> None:
    href = f"jci1.31:c:{bwb}&artikel={art}" + (f"&lid={lid}" if lid else "")
    refs.setdefault(href, ParsedRef(href, _norm(text), "cites", "pattern"))


def _ecli_refs(text: str, own: str) -> list[ParsedRef]:
    refs: dict[str, ParsedRef] = {}
    for m in ECLI.finditer(text):
        ecli = m.group(0)
        if ecli != own:
            refs.setdefault(work_uri_for(ecli), ParsedRef(work_uri_for(ecli), ecli, "cites", "pattern"))
    return list(refs.values())


def _text_refs(text: str, own: str) -> list[ParsedRef]:
    return [*_statute_refs(text), *_ecli_refs(text, own)]


def _metadata_refs(main: ET.Element, own: str) -> list[ParsedRef]:
    refs: list[ParsedRef] = []
    for el in main.findall(f"{DC}references"):
        ident = el.get(f"{BWB_NS}resourceIdentifier")
        if ident and el.get(f"{RDFS}label") == "Wetsverwijzing":
            refs.append(ParsedRef(ident, _norm("".join(el.itertext())), "references"))
    for el in main.findall(f"{DC}relation"):
        ecli = el.get(f"{ECLI_NS}resourceIdentifier")
        if not ecli or not ECLI.fullmatch(ecli) or ecli == own:
            continue
        kind = (el.get(f"{PSI}type") or "").rsplit("/", 1)[-1]
        stage = (el.get(f"{PSI}aanleg") or "").rsplit("/", 1)[-1]
        relation = (
            "opinion"
            if kind == "conclusie"
            else "earlier_instance"
            if stage == "eerdereAanleg"
            else "later_instance"
            if stage == "latereAanleg"
            else "related"
        )
        refs.append(ParsedRef(work_uri_for(ecli), _norm("".join(el.itertext())), relation))
    return refs


# --------------------------------------------------------------------------- document


def _texts(el: ET.Element, tag: str) -> list[str]:
    return [t for t in (_norm("".join(e.itertext())) for e in el.findall(tag)) if t]


def _date(text: str | None, what: str) -> date:
    try:
        return date.fromisoformat((text or "").strip()[:10])
    except ValueError as exc:
        raise RechtspraakParseError(f"missing or invalid {what}: {text!r}") from exc


def parse_rechtspraak(content: bytes) -> list[ParsedDocument]:
    try:
        root = fromstring(content.replace(b"<?linebreak?>", LINEBREAK.encode()), forbid_dtd=True)
    except Exception as exc:  # defusedxml raises several exception types
        raise RechtspraakParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if root.tag != "open-rechtspraak":
        raise RechtspraakParseError("root element is not open-rechtspraak")
    rdf = root.find(f"{RDF}RDF")
    descriptions = rdf.findall(f"{RDF}Description") if rdf is not None else []
    if not descriptions:
        raise RechtspraakParseError("no rdf:Description (ECLI metadata)")
    main = descriptions[0]
    doc_meta = descriptions[1] if len(descriptions) > 1 else None

    ecli = (main.findtext(f"{DC}identifier") or "").strip()
    m = ECLI.fullmatch(ecli)
    if not m:
        raise RechtspraakParseError(f"not a Dutch ECLI: {ecli!r}")
    body = root.find(f"{RS}uitspraak")
    if body is None:
        kind = "an advisory opinion (conclusie)" if root.find(f"{RS}conclusie") is not None else "without text"
        raise RechtspraakParseError(f"{ecli}: not a decision with text ({kind}); not supported")
    if (main.findtext(f"{DC}language") or "").strip() != "nl":
        raise RechtspraakParseError(f"{ecli}: dcterms:language is not nl")
    dockets = _texts(main, f"{PSI}zaaknummer")
    title = _norm("".join(doc_meta.findtext(f"{DC}title") or "")) if doc_meta is not None else ""
    if any(TRANSLATION.search(t) for t in [*dockets, title]):
        raise RechtspraakParseError(f"{ecli}: English translation of a decision; not ingested")
    if not dockets:
        raise RechtspraakParseError(f"{ecli}: no zaaknummer")

    creator = main.find(f"{DC}creator")
    court_uri = creator.get("resourceIdentifier", "") if creator is not None else ""
    court = COURTS.get(court_uri)
    if court is None:
        raise RechtspraakParseError(f"{ecli}: court {court_uri!r} is not mapped to a ULIS court")
    if m.group(1) != court[1]:
        raise RechtspraakParseError(f"{ecli}: the creator does not match the ECLI court code {m.group(1)}")
    decision_date = _date(main.findtext(f"{DC}date"), "dcterms:date")
    if decision_date.year < int(m.group(2)) - 1 or decision_date.year > int(m.group(2)) + 1:
        raise RechtspraakParseError(f"{ecli}: decision date {decision_date} does not fit the ECLI year")
    modified = sorted(
        {
            (e.text or "").strip()[:10]
            for d in descriptions
            for e in d.findall(f"{DC}modified")
            if (e.text or "").strip()
        }
    )
    version_date = _date(modified[-1] if modified else (main.findtext(f"{DC}issued") or ""), "dcterms:modified")

    notes = _Footnotes()
    footnotes = [e for e in body.iter() if _local(e.tag) == "footnote"]
    for fn in footnotes:
        if fn.get("id") and fn.get("label"):
            notes.labels[fn.get("id", "")] = fn.get("label", "")

    walker = _Units(notes)
    walker.container(body, "text", None)
    paragraphs: list[ParsedParagraph] = []
    used: set[str] = set()

    def add(part: str, label: str | None, num: str | None, text: str, refs: list[ParsedRef]) -> None:
        base = f"{PREFIX[part]}-{label}" if label else f"{PREFIX[part]}-u{len(paragraphs) + 1}"
        eid, n = base, 1
        while eid in used:
            n += 1
            eid = f"{base}-{n}"
        used.add(eid)
        paragraphs.append(ParsedParagraph(eid, num, part, text, len(paragraphs), refs))

    headnote_el = root.find(f"{RS}inhoudsindicatie")
    headnote = (
        "\n".join(t for t in (_norm(_inline(p, notes)) for p in headnote_el.iter(f"{RS}para")) if t)
        if headnote_el is not None
        else ""
    )
    meta_refs = _metadata_refs(main, ecli)
    if headnote:
        add("headnote", None, None, headnote, [*meta_refs, *_text_refs(headnote, ecli)])
        meta_refs = []
    for unit in _merge(walker.units):
        add(unit.part, unit.label, unit.num, unit.text, [*meta_refs, *_text_refs(unit.text, ecli)])
        meta_refs = []
    for fn in footnotes:
        text = "\n".join(t for t in (_block_text(c, notes) for c in fn) if t) or _norm(_inline(fn, notes))
        if text:
            add("footnote", fn.get("label") or None, None, text, _text_refs(text, ecli))
    if not any(p.part != "headnote" for p in paragraphs):
        raise RechtspraakParseError(f"{ecli}: the decision has no text")

    case_name = (_texts(main, f"{DC}alternative") or [ecli])[0]
    work_uri = work_uri_for(ecli)
    ext = {
        "ecli": ecli,
        "zaaknummers": dockets,
        "procedure": _texts(main, f"{PSI}procedure"),
        "rechtsgebied": _texts(main, f"{DC}subject"),
        "vindplaatsen": [t for li in main.iter(f"{RDF}li") if (t := _norm("".join(li.itertext())))],
        "replaces": _texts(main, f"{DC}replaces"),
        "issued": (main.findtext(f"{DC}issued") or "").strip() or None,
        "modified": (main.findtext(f"{DC}modified") or "").strip() or None,
        "publisher_title": title or None,
    }
    return [
        ParsedDocument(
            doc_kind="judgment",
            instrument_type="judgment",
            jurisdiction_id="NL",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/nld@{version_date.isoformat()}",
            work_title=case_name,
            title=title or case_name,
            short_title=None,
            number=dockets[0],
            work_date=decision_date,
            language="nl",
            authoritative=False,  # pseudonymised publication; the court's signed judgment is the original
            text_origin="official",
            consolidation_kind="unknown",
            version_date=version_date,
            currency_date=None,
            extent=["NL"],
            ext_module="nl",
            ext=ext,
            judgment=ParsedJudgmentMeta(
                court_id=court[0],
                case_name=case_name,
                neutral_citation=ecli,
                decision_date=decision_date,
                docket="; ".join(dockets),
            ),
            paragraphs=paragraphs,
        )
    ]
