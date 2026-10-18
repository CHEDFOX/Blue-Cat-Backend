"""Parser for EU law fetched from Cellar (the Publications Office repository behind EUR-Lex).

Input is a *bundle* built by :mod:`ulis.sources.connectors.eu_cellar`: the SPARQL answers about
one work (metadata, amending acts and corrigenda, files) and the files of one language
version, each exactly as received. Two text formats are read:

* **Formex 4** (the Publications Office's production format for the Official Journal): acts
  (``ACT``), annexes (``ANNEX``), the Charter (``GENERAL``), the per-issue descriptor (``DOC``),
  either as separate files or as one zip (Official Journal act by act, from October 2023);
* **OJ XHTML** for the consolidated Treaties (TEU, TFEU), which Cellar holds only in that
  format: articles are ``div`` elements whose ``id`` is the Formex identifier (``003``,
  ``003.001``), points are two-column tables;
* **OJ XHTML or legacy EUR-Lex HTML** for the acts (mostly 1953-2003) that Cellar holds without a Formex 4 file:
  one stream of blocks (headings, paragraphs, points, titles of annexes) from which articles, numbered paragraphs,
  points (XHTML only), divisions and annexes are recovered (see the "OJ XHTML and legacy HTML" section).

* **consolidated versions** of an act (``CONS.ACT``, CELEX ``0YYYYTNNNN-YYYYMMDD``), in the same Formex 4: the text
  with the amendments applied, which the Publications Office prepares for documentation. Its ``INFO.CONSLEG`` gives
  the dates the version applies from and to (``END.DATE`` 99999999 = still current). Such a version belongs to the
  instrument of its base act (same work URI), is a whole-instrument snapshot (``validity_basis =
  "revision_snapshot"``) and is not authentic. The amendments are in the text, so the base act's list of changes is
  not applied to it.

Legal status, as recorded on every version:

* since 1 July 2013 only the electronic Official Journal is authentic (Regulation (EU)
  No 216/2013, art 1(2) and art 5); before that, the printed OJ. The Formex/XHTML files are the
  publisher's own renditions of the OJ text, not the signed authentic edition, so OJ texts are
  ``official_conversion``. All language versions are equally authentic (``authoritative``);
* the consolidated Treaties are documentation produced by the publisher, not authentic texts:
  ``official_revised``, not authoritative, and known to be in force only at their publication
  date (``attested_at_edition``).

Dates come from the Publications Office metadata, never from guesses: the date of the document,
entry into force (``EV``), application (``MA``) and partial application (``MA/PART``). A
provision's ``valid_from`` is the act's general date of application, or its entry into force
when no application date is recorded. Partial application dates are kept in the note: which
provisions they concern is stated only in the act's text and is not modelled.

Changes recorded in the metadata (amendments with their modified location and start of
validity, implicit repeals, corrigenda of this language version) are listed as outstanding
changes: the original OJ text is ingested, never the amended one. Where the metadata locates a
replacement, repeal or insertion in a provision, the original wording of that provision (and of
the provisions whose composite text contains it) is ended on the amendment's start of validity,
so it is not presented as current; the later wording is not indexed.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Any
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.ingestion.snapshots import SNAPSHOT_BASIS
from ulis.jurisdictions.modules.eu import DOMAIN_CODES, TREATY_ABBREVIATION, designation
from ulis.sources.connectors.eu_cellar import BUNDLE_FORMAT, LANG3
from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParseSkipped

PARSER_ID = "eu_cellar"
PARSER_VERSION = "1.2.0"
CDM = "http://publications.europa.eu/ontology/cdm#"
ANN = "http://publications.europa.eu/ontology/annotation#"
XHTML_NS = "{http://www.w3.org/1999/xhtml}"
CELLAR_CELEX = "http://publications.europa.eu/resource/celex/"
NO_END = date(9999, 12, 31)
MAX_ZIP_MEMBERS = 200
MAX_ZIP_BYTES = 100 * 1024 * 1024

# English labels of the Publications Office authority tables used in the metadata annotations
# (fd_335 date comments, fd_370 subdivisions, fd_375 roles), as published in Cellar (skos:prefLabel).
CODE_LABELS = {
    "EV": "Entry into force",
    "PE": "Takes effect",
    "DATNOT": "Date notif.",
    "MA": "Application",
    "MA/PART": "Partial application",
    "V": "See",
    "DATPUB": "Date pub.",
    "DATSIG": "Date of signing",
    "ART": "Art",
    "ET": "And",
    "AU+TARD": "At the latest",
    "AR": "Article",
    "PA": "paragraph",
    "ALN": "unnumbered paragraph",
    "PTA": "point",
    "PO": "point",
    "AN": "annex",
    "PRT": "part",
    "TIRE": "indent",
    "TXT": "Text",
    "CHA": "chapter",
    "TIT": "title",
    "SCT": "section",
}
ROLE_LABELS = {
    "R": "Replacement",
    "J": "Addition",
    "A": "Repeal",
    "AP": "Partial repeal",
    "M": "Amendment",
    "C": "Completion",
    "S": "Suspension",
    "DEL": "Deletion",
}
RELATION_LABELS = {
    "resource_legal_amends_resource_legal": "Amended by",
    "resource_legal_repeals_resource_legal": "Repealed by",
    "resource_legal_implicitly_repeals_resource_legal": "Implicitly repealed by",
    "resource_legal_corrects_resource_legal": "Corrigendum",
}
ACT_TYPES = {"REG": "regulation", "DIR": "directive", "DEC": "decision"}
TREATIES = {"teu": "TEU", "tfeu": "TFEU", "char": "CHARTER"}
NUMBERED_LISTS = {"alpha", "ARAB", "roman", "ALPHA", "ROMAN"}
LIST_MARKERS = {"DASH": "—", "NDASH": "–", "BULLET": "•"}


class EuParseError(ValueError):
    pass


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10].replace("/", "-"))  # some annotations use 2015/02/26
    except ValueError:
        return None


def _codes(text: str) -> str:
    """``{DATPUB|uri} +20 {V|uri} {ART|uri} 99`` -> ``Date pub. +20 See Art 99``."""
    return _ws(re.sub(r"\{([^}|]+)(?:\|[^}]*)?\}", lambda m: CODE_LABELS.get(m.group(1), m.group(1)), text))


def _code(text: str | None) -> str | None:
    m = re.match(r"\s*\{([^}|]+)", text or "")
    return m.group(1) if m else None


def _key(label: str) -> str:
    """``(a)`` / ``a)`` -> ``a``; ``1.`` -> ``1``; ``(14a)`` -> ``14a``."""
    return re.sub(r"[^0-9A-Za-z]", "", label).lower()


# --------------------------------------------------------------------------- bundle


@dataclass
class Part:
    role: str
    url: str | None
    data: bytes


def load_bundle(content: bytes) -> tuple[dict[str, Any], list[Part]]:
    try:
        bundle = json.loads(content)
    except ValueError as exc:
        raise EuParseError(f"not a Cellar bundle: {exc}") from exc
    if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE_FORMAT or bundle.get("version") != 1:
        raise EuParseError("not a ULIS Cellar bundle (format/version)")
    parts: list[Part] = []
    for p in bundle.get("parts") or []:
        if p["encoding"] == "omitted":  # an image: listed for provenance, bytes not kept
            parts.append(Part(p["role"], p.get("url"), b""))
            continue
        data = p["content"].encode("utf-8") if p["encoding"] == "utf-8" else base64.b64decode(p["content"])
        if hashlib.sha256(data).hexdigest() != p["sha256"]:
            raise EuParseError(f"bundle part {p.get('url')} does not match its recorded SHA-256")
        parts.append(Part(p["role"], p.get("url"), data))
    return bundle, parts


def _bindings(parts: list[Part], role: str) -> list[dict[str, Any]]:
    part = next((p for p in parts if p.role == role), None)
    if part is None:
        raise EuParseError(f"bundle has no {role} part")
    rows: list[dict[str, Any]] = json.loads(part.data)["results"]["bindings"]
    return [{k: v["value"] for k, v in row.items()} for row in rows]


# --------------------------------------------------------------------------- metadata


@dataclass(frozen=True)
class DateFact:
    value: date
    kind: str | None
    comment: str | None

    def describe(self) -> str:
        return f"{self.value.isoformat()}" + (f" ({_codes(self.comment)})" if self.comment else "")


@dataclass
class WorkMeta:
    celex: str
    eli: str | None
    resource_type: str | None
    in_force: bool | None
    document_date: date | None
    signature_dates: list[date]
    publication_date: date | None
    effect_dates: list[DateFact]
    end_of_validity: list[DateFact]
    transposition: list[DateFact]
    ecli: str | None

    @property
    def entry_into_force(self) -> list[DateFact]:
        """``EV`` (entry into force) and ``PE`` (takes effect: the date a decision addressed to its addressees
        produces its effects, which the Publications Office records instead of an entry into force)."""
        return [d for d in self.effect_dates if d.kind in ("EV", "PE")]

    @property
    def application(self) -> list[DateFact]:
        return [d for d in self.effect_dates if d.kind == "MA" and _code(d.comment) != "MA/PART"]

    @property
    def partial_application(self) -> list[DateFact]:
        return [d for d in self.effect_dates if d.kind == "MA/PART" or _code(d.comment) == "MA/PART"]


def parse_metadata(rows: list[dict[str, Any]], celex: str) -> WorkMeta:
    works = {r["work"] for r in rows}
    if len(works) != 1:
        raise EuParseError(f"{celex}: the metadata describes {len(works)} works, expected one")
    values: dict[str, set[str]] = defaultdict(set)
    notes: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for r in rows:
        prop = r["p"].removeprefix(CDM)
        values[prop].add(r["o"])
        if "ap" in r:
            notes[(prop, r["o"])][r["ap"].removeprefix(ANN)] = r["av"]

    def facts(*props: str) -> list[DateFact]:
        out = set()
        for prop in props:
            for v in values.get(prop, ()):
                d = _date(v)
                if d is not None:
                    n = notes.get((prop, v), {})
                    out.add(DateFact(d, _code(n.get("type_of_date")), n.get("comment_on_date")))
        return sorted(out, key=lambda f: (f.value, f.kind or "", f.comment or ""))

    def one(prop: str) -> str | None:
        vals = sorted(values.get(prop, ()))
        return vals[0] if vals else None

    resource_type = one("work_has_resource-type")
    in_force = one("resource_legal_in-force")
    return WorkMeta(
        celex=celex,
        eli=one("resource_legal_eli"),
        resource_type=resource_type.rsplit("/", 1)[-1] if resource_type else None,
        in_force=None if in_force is None else in_force in ("1", "true"),
        document_date=_date(one("work_date_document")),
        signature_dates=sorted(d for d in (_date(v) for v in values.get("resource_legal_date_signature", ())) if d),
        publication_date=_date(one("date_creation_legacy")),
        effect_dates=facts("resource_legal_date_entry-into-force", "work_date_entry-into-force"),
        end_of_validity=facts("resource_legal_date_end-of-validity"),
        transposition=facts("directive_date_transposition"),
        ecli=one("case-law_ecli"),
    )


@dataclass
class Change:
    celex: str
    relation: str
    document_date: date | None
    titles: dict[str, str] = field(default_factory=dict)
    # One entry per annotation axiom: location, role, start of validity, language list.
    axioms: list[dict[str, str]] = field(default_factory=list)


def parse_changes(rows: list[dict[str, Any]]) -> list[Change]:
    by_act: dict[tuple[str, str], Change] = {}
    axioms: dict[tuple[str, str], dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        if "celex" not in r:
            continue
        rel = r["rel"].removeprefix(CDM)
        key = (r["x"], rel)
        change = by_act.setdefault(key, Change(r["celex"], rel, _date(r.get("date"))))
        if r.get("lang") and r.get("title"):
            change.titles[r["lang"].rsplit("/", 1)[-1]] = _ws(r["title"])
        if r.get("ax") and r.get("ap"):
            axioms[key][r["ax"]][r["ap"].removeprefix(ANN)] = r["av"]
    for key, change in by_act.items():
        change.axioms = [axioms[key][ax] for ax in sorted(axioms[key])]
    return sorted(by_act.values(), key=lambda c: (c.document_date or date.min, c.celex, c.relation))


# --------------------------------------------------------------------------- provisions


@dataclass
class Prov:
    eid: str
    kind: str
    heading: str | None
    text: str
    parent: str | None
    is_leaf: bool
    own_content: bool
    context: str | None
    note: list[str] = field(default_factory=list)


class _Builder:
    def __init__(self) -> None:
        self.provisions: list[Prov] = []
        self.eids: set[str] = set()
        self.footnotes: dict[str, str] = {}

    def eid(self, parent: str | None, segment: str) -> str:
        eid = f"{parent}__{segment}" if parent else segment
        n, candidate = 1, eid
        while candidate in self.eids:  # the same label twice under one parent (rare): number repeats
            n += 1
            candidate = f"{eid}-{n}"
        self.eids.add(candidate)
        return candidate

    def add(self, prov: Prov) -> None:
        self.provisions.append(prov)


# --- Formex ---------------------------------------------------------------------------------

FMX_SKIP = {"NOTE", "TI.ART", "STI.ART", "NO.P", "NO.PARAG"}
# Inside a quoted structure (text inserted into another act) article titles are part of the text.
FMX_SKIP_QUOTED = {"NOTE", "NO.P", "NO.PARAG"}
FMX_BLOCK = {
    "P",
    "ALINEA",
    "LIST",
    "ITEM",
    "PARAG",
    "GR.SEQ",
    "CONTENTS",
    "QUOT.S",
    "DIVISION",
    "ARTICLE",
    "NP",
    "TI",
    "STI",
    "INCL.ELEMENT",
    "IMG.CNT",
}


def _fmx_element(el: Element) -> str:
    """Inline rendering of one element (without its tail)."""
    if el.tag == "NOTE":
        return ""  # footnotes are kept as notes, not as text
    if el.tag in ("QUOT.START", "QUOT.END"):
        code = el.get("CODE") or ""  # the quotation mark's Unicode code point, e.g. 2018
        return chr(int(code, 16)) if re.fullmatch(r"[0-9A-Fa-f]{2,6}", code) else ""
    if el.tag == "TBL":
        return " " + " ".join(fmx_table(el)) + " "
    return fmx_inline(el)


def fmx_inline(el: Element) -> str:
    return (el.text or "") + "".join(_fmx_element(child) + (child.tail or "") for child in el)


def fmx_table(el: Element) -> list[str]:
    rows = []
    for row in el.iter("ROW"):
        cells = [_ws(fmx_inline(cell)) for cell in row.iter("CELL")]
        if any(cells):
            rows.append(" | ".join(cells))
    return rows


def fmx_lines(el: Element, quoted: bool = False) -> list[str]:
    """Text of a block element, one line per block (labels of numbered points kept in front)."""
    skip = FMX_SKIP_QUOTED if quoted else FMX_SKIP
    lines: list[str] = []
    current: list[str] = [el.text or ""]

    def flush() -> None:
        text = _ws("".join(current))
        if text:
            lines.append(text)
        current.clear()

    for child in el:
        tag = child.tag
        inner_quoted = quoted or tag == "QUOT.S"
        if tag in skip:
            pass
        elif tag == "NP":
            flush()
            label = _ws(child.findtext("NO.P") or "")
            inner = fmx_lines(child, inner_quoted)
            if inner and label:
                inner[0] = f"{label} {inner[0]}"
            elif label:
                inner = [label]
            lines.extend(inner)
        elif tag == "PARAG":
            flush()
            label = _ws(child.findtext("NO.PARAG") or "")
            inner = fmx_lines(child, inner_quoted)
            if inner and label:
                inner[0] = f"{label} {inner[0]}"
            lines.extend(inner)
        elif tag == "LIST":
            flush()
            # Unnumbered items are printed with the list's marker (Formex LIST/@TYPE).
            marker = LIST_MARKERS.get(child.get("TYPE") or "")
            for item in child:
                inner = fmx_lines(item, inner_quoted) if item.tag == "ITEM" else []
                if inner and marker and item.find("NP") is None:
                    inner[0] = f"{marker} {inner[0]}"
                lines.extend(inner)
        elif tag in FMX_BLOCK or tag in ("TITLE", "TI.ART", "STI.ART"):
            flush()
            lines.extend(fmx_lines(child, inner_quoted))
        elif tag == "TBL":
            flush()
            lines.extend(fmx_table(child))
        else:
            current.append(_fmx_element(child))
        current.append(child.tail or "")
    flush()
    return lines


def fmx_notes(el: Element) -> list[str]:
    return [_ws(fmx_inline(n)) for n in el.iter("NOTE") if _ws(fmx_inline(n))]


def _numbered_items(lst: Element) -> list[tuple[Element, str]]:
    out = []
    for item in lst.findall("ITEM"):
        np = item.find("NP")
        label = _ws(np.findtext("NO.P") or "") if np is not None else ""
        if np is not None and _key(label):
            out.append((np, label))
    return out


def fmx_points(b: _Builder, container: Element, parent: str, context: str | None, nested: bool) -> list[str]:
    """Create point provisions for the numbered lists directly inside ``container``."""
    created: list[str] = []
    for lst in container.findall("LIST"):
        ltype = lst.get("TYPE")
        if ltype is not None and ltype not in NUMBERED_LISTS:
            continue  # indents (dashes, bullets) stay in their parent's text
        for np, label in _numbered_items(lst):
            roman = (ltype or "").lower() == "roman" or (nested and re.fullmatch(r"[ivxl]+", _key(label)) is not None)
            eid = b.eid(parent, f"{'subpoint' if roman else 'point'}_{_key(label)}")
            prov = Prov(eid, "subpoint" if roman else "point", None, "", parent, True, False, context, fmx_notes(np))
            b.add(prov)
            children = []
            for sub in [np, *np.findall("P")]:
                children += fmx_points(b, sub, eid, context, nested=True)
            prov.text = "\n".join(fmx_lines(np))
            prov.is_leaf = not children
            prov.own_content = bool(children)
            created.append(eid)
    return created


def fmx_subdivisions(b: _Builder, el: Element, parent: str, context: str | None) -> list[str]:
    """Points of ``el`` (an ARTICLE or PARAG): numbered lists in its first subparagraph attach to
    ``el`` itself, those of a later subparagraph to a ``subpara_<n>`` provision."""
    created: list[str] = []
    alineas = el.findall("ALINEA")
    created += fmx_points(b, el, parent, context, nested=False)
    for n, alinea in enumerate(alineas, start=1):
        holders = [alinea, *alinea.findall("P")]
        if not any(_numbered_items(lst) for h in holders for lst in h.findall("LIST")):
            continue
        target = parent
        if n > 1:
            target = b.eid(parent, f"subpara_{n}")
            sub = Prov(target, "subparagraph", None, "\n".join(fmx_lines(alinea)), parent, False, True, context)
            b.add(sub)
            created.append(target)
        for h in holders:
            points = fmx_points(b, h, target, context, nested=False)
            if target == parent:
                created += points
    return created


def fmx_article(b: _Builder, art: Element, context: str | None) -> None:
    ident = art.get("IDENTIFIER") or ""
    number = re.sub(r"^0+(?=\d)", "", ident.split(".")[0]) if ident else ""
    if not number:
        m = re.search(r"(\d+\s*[a-z]?)\s*$", _ws(art.findtext("TI.ART") or ""))
        number = m.group(1).replace(" ", "") if m else ""
    if not number:
        raise EuParseError(f"article without identifier: {_ws(art.findtext('TI.ART') or '')!r}")
    eid = b.eid(None, f"art_{number.lower()}")
    sti = art.find("STI.ART")
    heading = _ws(fmx_inline(sti)) if sti is not None else None
    prov = Prov(eid, "article", heading or None, "\n".join(fmx_lines(art)), None, True, False, context)
    prov.note = [n for n in fmx_notes(art) if n]
    b.add(prov)
    children: list[str] = []
    for parag in art.findall("PARAG"):
        pid = parag.get("IDENTIFIER") or ""
        pnum = re.sub(r"^0+(?=\d)", "", pid.split(".")[-1]) if "." in pid else _key(parag.findtext("NO.PARAG") or "")
        if not pnum:
            raise EuParseError(f"{eid}: paragraph without number")
        peid = b.eid(eid, f"para_{pnum.lower()}")
        pprov = Prov(peid, "paragraph", None, "\n".join(fmx_lines(parag)), eid, True, False, context)
        b.add(pprov)
        children.append(peid)
        sub = fmx_subdivisions(b, parag, peid, context)
        pprov.is_leaf = not sub
        pprov.own_content = bool(sub)
    children += fmx_subdivisions(b, art, eid, context)
    prov.is_leaf = not children
    own = [c for c in art if c.tag not in ("TI.ART", "STI.ART", "PARAG") and fmx_lines(c)]
    prov.own_content = bool(children) and bool(own)


def _division_title(div: Element) -> str:
    title = div.find("TITLE")
    return _ws(" ".join(fmx_lines(title))) if title is not None else ""


def fmx_body(b: _Builder, el: Element, context: list[str]) -> None:
    for child in el:
        if child.tag == "DIVISION":
            fmx_body(b, child, [*context, _division_title(child)])
        elif child.tag == "ARTICLE":
            fmx_article(b, child, " — ".join(c for c in context if c) or None)
        elif child.tag in ("ENACTING.TERMS", "GR.SEQ", "CONTENTS"):
            fmx_body(b, child, context)


def fmx_annex(b: _Builder, root: Element) -> None:
    title = root.find("TITLE")
    title_lines = fmx_lines(title) if title is not None else []
    head = title_lines[0] if title_lines else ""
    m = re.search(r"\b([IVXLC]+[A-Z]?)\s*$", head)
    eid = b.eid(None, f"anx_{m.group(1)}" if m else "anx")
    contents = root.find("CONTENTS")
    text = "\n".join(fmx_lines(contents)) if contents is not None else ""
    heading = " ".join(title_lines[1:]) or None
    notes = fmx_notes(root)
    if contents is not None and contents.find(".//IMG.CNT") is not None:
        notes.append(
            "Parts of this annex are published as images (e.g. forms); their text here is the "
            "publisher's text equivalent of the images (Formex IMG.CNT)."
        )
    b.add(Prov(eid, "annex", heading, text, None, True, False, None, notes))


@dataclass
class FormexDoc:
    title: str
    number: str | None
    year: int | None
    domain_code: str | None
    publication_date: date | None
    # A consolidated version (CONS.ACT): the dates it applies from and to, and the date it was consolidated.
    cons_start: date | None = None
    cons_end: date | None = None
    cons_date: date | None = None


def _xml(data: bytes, name: str) -> Element:
    try:
        return fromstring(data, forbid_dtd=True)  # type: ignore[no-any-return]
    except Exception as exc:
        raise EuParseError(f"{name}: not well-formed or unsafe XML: {exc}") from exc


def formex_files(parts: list[Part]) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for part in parts:
        if not part.role.startswith("item:"):
            continue
        if part.data[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(part.data)) as zf:
                members = [i for i in zf.infolist() if i.filename.endswith(".xml")]
                if len(members) > MAX_ZIP_MEMBERS or sum(i.file_size for i in members) > MAX_ZIP_BYTES:
                    raise EuParseError(f"{part.url}: zip too large")
                for info in sorted(members, key=lambda i: i.filename):
                    files.append((f"{part.url}!{info.filename}", zf.read(info)))
        else:
            files.append((part.url or part.role, part.data))
    return files


def parse_formex(parts: list[Part]) -> tuple[FormexDoc, list[Prov]]:
    b = _Builder()
    mains: list[Element] = []
    annexes: list[Element] = []
    descriptor: Element | None = None
    for name, data in formex_files(parts):
        root = _xml(data, name)
        if root.tag == "DOC":
            descriptor = root
        elif root.tag == "ANNEX":
            annexes.append(root)
        elif root.tag in ("ACT", "GENERAL", "CONS.ACT") and root.find(".//ENACTING.TERMS") is not None:
            mains.append(root)
        elif root.tag in ("ACT", "GENERAL", "TOC", "PUBLICATION"):
            continue  # a table of contents or an issue description
        else:
            raise EuParseError(f"{name}: unsupported Formex root {root.tag!r}")
    if not mains:
        raise EuParseError("no Formex document with enacting terms")
    if any(m.tag == "CONS.ACT" for m in mains) and len(mains) > 1:
        raise EuParseError("more than one main Formex document in a consolidated version")
    main = mains[0]
    consolidated = main.tag == "CONS.ACT"
    doc_root = main.find("CONS.DOC") if consolidated else main
    if doc_root is None:
        raise EuParseError("CONS.ACT without CONS.DOC")
    title_el = doc_root.find("TITLE")
    title = _ws(" ".join(fmx_lines(title_el))) if title_el is not None else ""
    # One Official Journal publication can carry several acts under one CELEX number (a decision and the decision it
    # amends ...): the first is the main act, the others follow it, their title as the context of their provisions.
    for index, act in enumerate(mains):
        terms = act.find(".//ENACTING.TERMS")
        assert terms is not None
        act_title = act.find("TITLE")
        context = [] if index == 0 else [_ws(" ".join(fmx_lines(act_title))) if act_title is not None else ""]
        before = len(b.provisions)
        fmx_body(b, terms, context)
        if len(b.provisions) == before:
            # Enacting terms without a numbered article (older decisions, single-sentence acts): the operative text
            # is one provision, cited by the instrument alone.
            lines = fmx_lines(terms)
            if lines:
                b.add(
                    Prov(
                        b.eid(None, "terms"),
                        "article",
                        None,
                        "\n".join(lines),
                        None,
                        True,
                        False,
                        context[0] if context else None,
                    )
                )
    for annex in annexes:
        fmx_annex(b, annex)
    no_doc = doc_root.find("BIB.INSTANCE/NO.DOC")
    if no_doc is None and consolidated:
        # A consolidated file carries the act's number, year and domain in the family of acts it brings together: the
        # first entry is the act itself (the others are its amendments and corrigenda).
        no_doc = doc_root.find("FAM.COMP/BIB.DATA/BIB.INSTANCE.CONS/NO.DOC")
    info = main.find("INFO.CONSLEG") if consolidated else None
    year = no_doc.findtext("YEAR") if no_doc is not None else None
    pub = None
    if descriptor is not None:
        pub_el = descriptor.find("PUBLICATION.REF/DATE")
        pub = _date(_iso8(pub_el.get("ISO")) if pub_el is not None else None)
    doc = FormexDoc(
        title=title,
        number=_ws(no_doc.findtext("NO.CURRENT") or "") or None if no_doc is not None else None,
        year=int(year) if year and year.isdigit() else None,
        domain_code=_ws(no_doc.findtext("COM") or "") or None if no_doc is not None else None,
        publication_date=pub,
    )
    if info is not None:
        end = _date(_iso8(info.get("END.DATE")))
        doc.cons_start = _date(_iso8(info.get("START.DATE")))
        doc.cons_end = end if end and end != NO_END and end.year < 9999 else None
        doc.cons_date = _date(_iso8(info.get("CONSLEG.DATE")))
    return doc, b.provisions


def _title_domain(doc: FormexDoc) -> str | None:
    """The domain printed before the act number in the title, e.g. ``(EU)`` in ``Regulation (EU)
    2024/1689`` (Formex 6 act-by-act files have no ``NO.DOC/COM``)."""
    if not doc.year or not doc.number:
        return None
    m = re.search(rf"\(([^()]{{2,15}})\)\s+(?:\S+\s+)?{doc.year}/{re.escape(doc.number)}\b", doc.title)
    return DOMAIN_CODES.get(m.group(1).split(",")[0].strip().upper()) if m else None


def _iso8(value: str | None) -> str | None:
    """``20160504`` -> ``2016-05-04``."""
    if value and re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


# --- OJ XHTML (consolidated Treaties) --------------------------------------------------------

_ART_ID = re.compile(r"^\d{3}[a-z]?$")
_PARA_ID = re.compile(r"^\d{3}[a-z]?\.\d{3}[a-z]?$")
_PARA_LABEL = re.compile(r"^\s*(?:\d+[a-z]?\.|\(\d+[a-z]?\))\s+")  # "1. " (en, fr) or "(1) " (de)
_HEADING_LEVELS = (
    ("part", re.compile(r"\b(PART|PARTIE|TEIL)\b", re.I)),
    ("title", re.compile(r"\b(TITLE|TITRE|TITEL)\b", re.I)),
    ("chapter", re.compile(r"\b(CHAPTER|CHAPITRE|KAPITEL)\b", re.I)),
    ("section", re.compile(r"\b(SECTION|ABSCHNITT)\b", re.I)),
)


def _cls(el: Element) -> str:
    return el.get("class") or ""


def _x(tag: str) -> str:
    return XHTML_NS + tag


def _footnote_call(el: Element) -> bool:
    return el.tag == _x("a") and (el.get("href") or "").startswith("#ntr")


def x_text(el: Element) -> str:
    parts = [el.text or ""]
    for child in el:
        if not _footnote_call(child):  # footnote calls ("(2)") are not text; the notes go in ``note``
            parts.append(x_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def x_footnotes(body: Element) -> dict[str, str]:
    """Footnote texts by id (``ntr2-...``); in the OJ XHTML they follow the text they annotate."""
    notes: dict[str, str] = {}
    for p in body.iter(_x("p")):
        if "note" not in {c.removeprefix("oj-") for c in _cls(p).split()}:  # ``oj-note`` from CONVEX 9
            continue
        anchor = next((a for a in p.iter(_x("a")) if (a.get("id") or "").startswith("ntr")), None)
        if anchor is not None:
            text = _ws((anchor.tail or "") + "".join(x_text(c) + (c.tail or "") for c in p if c is not anchor))
            notes[anchor.get("id") or ""] = text
    return notes


def _point_table(table: Element) -> tuple[str, Element] | None:
    """A two-column table whose first cell holds a point label: ``(a)``, ``1)``, ``(iv)``."""
    rows = table.findall(f"{_x('tbody')}/{_x('tr')}") or table.findall(_x("tr"))
    if len(rows) != 1:
        return None
    cells = rows[0].findall(_x("td"))
    if len(cells) != 2:
        return None
    label = _ws(x_text(cells[0]))
    return (label, cells[1]) if _key(label) else None


def x_lines(el: Element) -> list[str]:
    lines: list[str] = []
    for child in el:
        if child.tag == _x("p") and "ti-art" not in _cls(child) and "sti-art" not in _cls(child):
            text = _ws(x_text(child))
            if text:
                lines.append(text)
        elif child.tag == _x("table"):
            point = _point_table(child)
            if point is not None:
                inner = x_lines(point[1])
                lines.append(f"{point[0]} {inner[0]}" if inner else point[0])
                lines.extend(inner[1:])
            else:
                for row in child.iter(_x("tr")):
                    cells = [_ws(x_text(td)) for td in row.findall(_x("td"))]
                    if any(cells):
                        lines.append(" ".join(c for c in cells if c))
        elif child.tag == _x("div"):
            lines.extend(x_lines(child))
    return lines


def x_points(b: _Builder, container: Element, parent: str, context: str | None, nested: bool) -> list[str]:
    created = []
    for table in container.findall(_x("table")):
        point = _point_table(table)
        if point is None:
            continue
        label, cell = point
        roman = nested and re.fullmatch(r"[ivxl]+", _key(label)) is not None
        eid = b.eid(parent, f"{'subpoint' if roman else 'point'}_{_key(label)}")
        prov = Prov(eid, "subpoint" if roman else "point", None, "\n".join(x_lines(cell)), parent, True, False, context)
        b.add(prov)
        sub = x_points(b, cell, eid, context, nested=True)
        prov.is_leaf, prov.own_content = not sub, bool(sub)
        created.append(eid)
    return created


def x_article(b: _Builder, div: Element, context: str | None) -> None:
    number = re.sub(r"^0+(?=\d)", "", div.get("id") or "")
    ti = next((p for p in div.findall(_x("p")) if "ti-art" in _cls(p)), None)
    ti_text = _ws(x_text(ti)) if ti is not None else ""
    # French numbers the first article "Article premier".
    if not re.search(rf"\b{re.escape(number)}\b", ti_text) and not (number == "1" and "premier" in ti_text):
        raise EuParseError(f"article div {div.get('id')!r} does not match its title")
    eid = b.eid(None, f"art_{number}")
    sti = next((p for p in div.findall(_x("p")) if "sti-art" in _cls(p)), None)
    sti_text = _ws(x_text(sti)) if sti is not None else ""
    # In the consolidated Treaties the subtitle is the editorial "(ex Article 2 TEU)" reference.
    note = [sti_text] if sti_text.startswith("(") else []
    heading = None if note else (sti_text or None)
    for a in div.iter(_x("a")):
        footnote = b.footnotes.get((a.get("href") or "#")[1:]) if _footnote_call(a) else None
        if footnote:
            note.append(footnote)
    prov = Prov(eid, "article", heading, "\n".join(x_lines(div)), None, True, False, context, note)
    b.add(prov)
    children: list[str] = []
    for pdiv in div.findall(_x("div")):
        if not _PARA_ID.match(pdiv.get("id") or ""):
            continue
        pnum = re.sub(r"^0+(?=\d)", "", (pdiv.get("id") or "").split(".")[1])
        peid = b.eid(eid, f"para_{pnum}")
        lines = x_lines(pdiv)
        if lines:
            lines[0] = _PARA_LABEL.sub("", lines[0], count=1)
        pprov = Prov(peid, "paragraph", None, "\n".join(lines), eid, True, False, context)
        b.add(pprov)
        children.append(peid)
        sub = x_points(b, pdiv, peid, context, nested=False)
        pprov.is_leaf, pprov.own_content = not sub, bool(sub)
    children += x_points(b, div, eid, context, nested=False)
    prov.is_leaf = not children
    own = [
        p for p in div.findall(_x("p")) if "ti-art" not in _cls(p) and "sti-art" not in _cls(p) and x_text(p).strip()
    ]
    prov.own_content = bool(children) and bool(own)


def parse_xhtml_treaty(parts: list[Part]) -> tuple[str, list[Prov]]:
    items = [p for p in parts if p.role.startswith("item:")]
    if len(items) != 1:
        raise EuParseError(f"expected one XHTML file, got {len(items)}")
    data = items[0].data
    # The OJ XHTML declares an external DTD and uses no entities: drop the declaration (only one
    # without an internal subset) so the document can be parsed with DTDs forbidden.
    data = re.sub(rb"<!DOCTYPE[^>\[]*>", b"", data, count=1)
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise EuParseError("unexpected DTD in XHTML")
    root = _xml(data, items[0].url or "xhtml")
    body = root.find(_x("body"))
    if root.tag != _x("html") or body is None:
        raise EuParseError("not an XHTML document")
    b = _Builder()
    b.footnotes = x_footnotes(body)
    state: dict[str, Any] = {"title": "", "stop": False}
    # Current headings by level (part, title, chapter, section) and the level named last.
    headings: dict[str, str] = {}
    last_level: list[str] = []

    def context() -> str | None:
        for level in ("title", "part"):
            if headings.get(level):
                return headings[level]
        return None

    def walk(el: Element) -> None:
        for child in el:
            if state["stop"]:
                return
            cls = _cls(child)
            if child.tag == _x("p") and "doc-ti" in cls.split():
                if b.provisions:
                    state["stop"] = True  # protocols, annexes and declarations follow the Treaty
                    return
                state["title"] = _ws(f"{state['title']} {x_text(child)}")
            elif child.tag == _x("p") and cls == "ti-section-1":
                text = _ws(x_text(child))
                level = next((name for name, rx in _HEADING_LEVELS if rx.search(text)), "section")
                order = [name for name, _ in _HEADING_LEVELS]
                for lower in order[order.index(level) :]:
                    headings.pop(lower, None)
                headings[level] = text
                last_level[:] = [level]
            elif child.tag == _x("p") and cls == "ti-section-2" and last_level:
                headings[last_level[0]] = _ws(f"{headings.get(last_level[0], '')} {x_text(child)}")
                last_level.clear()
            elif child.tag == _x("div") and _ART_ID.match(child.get("id") or ""):
                x_article(b, child, context())
            elif child.tag == _x("div"):
                walk(child)

    walk(body)
    if not b.provisions:
        raise EuParseError("no articles found in XHTML")
    return state["title"], b.provisions


# --- OJ XHTML and legacy HTML (acts Cellar holds without a Formex 4 file) ---------------------
#
# About 7,100 of the acts in force (mostly 1953-2003) have no Formex 4 file in English. Cellar holds them either as the
# legacy EUR-Lex HTML (HTML 4.01: the text of the Official Journal as flat ``<p>`` paragraphs inside ``<TXT_TE>``, no
# structural markup, footnotes and image placeholders interleaved) or as the OJ XHTML of the CONVEX converter (either
# flat ``p.ti-art``/``p.normal`` siblings, points as two-column tables, or - from version 9 - ``eli-subdivision`` divs
# and ``oj-`` prefixed classes). Both are turned into one stream of blocks (titles, article headings, division headings,
# text paragraphs, points, notes, the final formula) and the same builder recovers the structure: articles from their
# headings ("Article 1", class ``ti-art``), numbered paragraphs from their labels ("1." in sequence), points from the
# XHTML tables, divisions as context, each trailing document (ANNEX I ..., an attached agreement) as one unit. The text
# is the publisher's rendition exactly as held; nothing is reflowed, corrected or completed.

_ART_HEADING = re.compile(r"^(?:Article\s+(\d+)\s*([a-z])?|(Sole)\s+Article)\s*$", re.I)
_ANNEX_HEADING = re.compile(r"^ANNEX(?:\s+([IVXLC]+[A-Za-z]?|\d+[A-Za-z]?))?$")
_DIVISION_HEADING = re.compile(r"^(PART|TITLE|CHAPTER|SECTION|SUB-SECTION)\s+[IVXLC0-9]+[A-Za-z]?\s*$", re.I)
_END_OF_TERMS = re.compile(r"^(Done at\b|For the (Council|Commission|European Parliament|Governing Council)\b)")
_FORMULA = re.compile(r"^HA(?:S|VE)\s+(?:ADOPTED|DECIDED)\b.*:?\s*$")
_PARA_LABEL_ACT = re.compile(r"^(\d+)([a-z])?\s*\.\s+(?=\S)")
# Image and table placeholders of the legacy HTML (the pictures themselves are not held as text).
_PLACEHOLDER = re.compile(
    r"^>(?:PIC FILE|START OF GRAPHIC|END OF GRAPHIC|TABLE|START OF TABLE|END OF TABLE|REFERENCE TO A (?:GRAPHIC|FILM))"
    r"\b[^<>]*>$"
)
_DIVISION_LEVELS = ("part", "title", "chapter", "section", "group")


@dataclass
class Block:
    """One unit of the flattened act: ``kind`` is title, article, subtitle, division, text, point or end (``note`` is a
    remark of the tokenizer about the file as a whole)."""

    kind: str
    text: str = ""
    label: str = ""
    level: int = 1
    points: list[Block] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class _LegacyHtml(HTMLParser):
    """Paragraphs of a legacy EUR-Lex HTML page: the text inside ``div#TexteOnly`` (``<TXT_TE>``), one string per
    ``<p>`` (or table row), whitespace normalised. The ``DC.description`` meta holds the title."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self.description: str | None = None
        self._depth = 0
        self._buf: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "meta" and a.get("name") == "DC.description":
            self.description = _ws(a.get("content") or "") or None
        if tag == "div":
            if self._depth:
                self._depth += 1
            elif a.get("id") == "TexteOnly":
                self._depth, self._buf = 1, []
        elif self._depth and tag in ("p", "tr", "li", "h1", "h2", "h3"):
            self._flush()
            self._buf = []
        elif self._depth and tag in ("br", "td", "th") and self._buf is not None:
            self._buf.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if not self._depth:
            return
        if tag == "div":
            self._depth -= 1
            if not self._depth:
                self._flush()
                self._buf = None
        elif tag in ("p", "tr", "li", "h1", "h2", "h3"):
            self._flush()
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._depth and self._buf is not None:
            self._buf.append(data)

    def _flush(self) -> None:
        if self._buf is not None:
            text = _ws("".join(self._buf))
            if text:
                self.paragraphs.append(text)
        self._buf = None


def html_blocks(data: bytes, name: str) -> tuple[str | None, list[Block]]:
    """Blocks of a legacy HTML page. Structure is recovered from the paragraphs' text alone: article headings
    ("Article 1"), division headings ("TITLE I" and the heading lines that follow), annex headings ("ANNEX II"), the
    final formula ("Done at ..."). Image placeholders (``>PIC FILE= ...>``) are dropped and reported."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EuParseError(f"{name}: legacy HTML is not UTF-8: {exc}") from exc
    parser = _LegacyHtml()
    parser.feed(text)
    parser.close()
    if not parser.paragraphs:
        raise EuParseError(f"{name}: no text found in the legacy HTML (div#TexteOnly)")
    blocks: list[Block] = []
    division_open = False
    placeholders = 0
    for para in parser.paragraphs:
        if _PLACEHOLDER.match(para):
            placeholders += 1
            continue
        art = _ART_HEADING.match(para)
        if art:
            number = "1" if art.group(3) else art.group(1) + (art.group(2) or "")
            blocks.append(Block("article", para, number.lower()))
            division_open = False
        elif _DIVISION_HEADING.match(para):
            blocks.append(Block("division", para, level=1))
            division_open = True
        elif _ANNEX_HEADING.match(para):
            blocks.append(Block("title", para))
            division_open = False
        elif _END_OF_TERMS.match(para):
            blocks.append(Block("end", para))
            division_open = False
        elif division_open:
            blocks.append(Block("division", para, level=2))
        else:
            blocks.append(Block("text", para))
    if placeholders:
        blocks.insert(
            0,
            Block(
                "note",
                f"{placeholders} image or table placeholder(s) of the HTML rendition (e.g. '>PIC FILE= ...>') are not "
                "text and were left out; the pictures themselves are not held in this format.",
            ),
        )
    return parser.description, blocks


def _classes(el: Element) -> set[str]:
    """CSS classes without the ``oj-`` prefix that CONVEX 9 adds (``oj-ti-art`` and ``ti-art`` are the same)."""
    return {c.removeprefix("oj-") for c in _cls(el).split()}


def _footnote_ids(el: Element) -> list[str]:
    return [(a.get("href") or "#")[1:] for a in el.iter(_x("a")) if _footnote_call(a)]


def _x_point(table: Element, footnotes: dict[str, str]) -> Block | None:
    point = _point_table(table)
    if point is None:
        return None
    label, cell = point
    block = Block("point", "", label)
    block.notes = [footnotes[i] for i in _footnote_ids(cell) if i in footnotes]
    for line in _x_cell_blocks(cell, footnotes):
        if line.kind == "point":
            block.points.append(line)
        elif line.text:
            block.text = f"{block.text}\n{line.text}" if block.text else line.text
    return block


def _x_cell_text(cell: Element) -> str:
    """Text of a table cell: its paragraphs one after the other (nested tables flattened)."""
    paragraphs = [_ws(x_text(p)) for p in cell.iter(_x("p"))]
    return " ".join(t for t in paragraphs if t) if paragraphs else _ws(x_text(cell))


def _x_table_lines(table: Element) -> list[str]:
    """Rows of an ordinary table (a tariff schedule, a list of codes ...), one line per row, cells separated by
    ``|`` as for Formex tables; nested tables are flattened into their cell."""
    lines: list[str] = []
    for row in table.iter(_x("tr")):
        cells = [_x_cell_text(td) for td in row if td.tag in (_x("td"), _x("th"))]
        if not any(cells):
            continue
        if len(cells) == 2 and re.fullmatch(r"[—–•-]?", cells[0]):
            lines.append(" ".join(c for c in cells if c))  # an indent: the marker and its text
        else:
            lines.append(" | ".join(cells))
    return lines


def _x_enumeration(div: Element) -> str | None:
    """CONVEX 9 prints "1. Text" as a ``div.enumeration-spacing`` with two inline paragraphs: one line."""
    if "enumeration-spacing" not in _classes(div):
        return None
    parts = [_ws(x_text(child)) for child in div if child.tag == _x("p")]
    return " ".join(t for t in parts if t) or None


def _x_cell_blocks(el: Element, footnotes: dict[str, str]) -> list[Block]:
    """Text and nested points inside a point cell (or any container), in document order."""
    out: list[Block] = []
    for child in el:
        if child.tag == _x("p"):
            text = _ws(x_text(child))
            if text:
                out.append(Block("text", text))
        elif child.tag == _x("table"):
            point = _x_point(child, footnotes)
            if point is not None:
                out.append(point)
            else:
                out.extend(Block("text", t) for t in _x_table_lines(child))
        elif child.tag in (_x("div"), _x("figure")):
            line = _x_enumeration(child)
            if line:
                out.append(Block("text", line))
            else:
                out.extend(_x_cell_blocks(child, footnotes))
    return out


def xhtml_blocks(data: bytes, name: str) -> list[Block]:
    """Blocks of an OJ XHTML file (CONVEX 6 to 9): classes name the parts, tables carry the points."""
    data = re.sub(rb"<!DOCTYPE[^>\[]*>", b"", data, count=1)
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise EuParseError(f"{name}: unexpected DTD in XHTML")
    root = _xml(data, name)
    body = root.find(_x("body"))
    if root.tag != _x("html") or body is None:
        raise EuParseError(f"{name}: not an XHTML document")
    footnotes = x_footnotes(body)
    blocks: list[Block] = []

    def walk(el: Element, quoted: bool) -> None:
        for child in el:
            classes = _classes(child)
            inner_quoted = quoted or "quotation" in classes
            if child.tag == _x("p"):
                text = _ws(x_text(child))
                notes = [footnotes[i] for i in _footnote_ids(child) if i in footnotes]
                if (
                    not text
                    or classes & {"note", "signatory", "doc-end", "separator"}
                    or any(c.startswith("hd-") for c in classes)
                ):
                    continue
                if inner_quoted:
                    kind = "text"
                elif "doc-ti" in classes:
                    kind = "title"
                elif "ti-art" in classes:
                    kind = "article"
                elif "sti-art" in classes:
                    kind = "subtitle"
                elif classes & {"ti-section-1", "ti-section-2", "ti-grseq-1", "ti-grseq-2"}:
                    kind = "division"
                else:
                    kind = "end" if _END_OF_TERMS.match(text) and "final" in _classes(el) else "text"
                block = Block(kind, text, level=2 if "ti-section-2" in classes else 1)
                if kind == "article":
                    m = _ART_HEADING.match(text)
                    if not m:
                        block = Block("text", text)
                    else:
                        block.label = ("1" if m.group(3) else m.group(1) + (m.group(2) or "")).lower()
                block.notes = notes
                blocks.append(block)
            elif child.tag == _x("table"):
                point = None if inner_quoted else _x_point(child, footnotes)
                if point is not None:
                    blocks.append(point)
                else:
                    blocks.extend(Block("text", t) for t in _x_table_lines(child))
            elif child.tag in (_x("div"), _x("figure"), _x("span")):
                line = _x_enumeration(child)
                if line:
                    block = Block("text", line)
                    block.notes = [footnotes[i] for i in _footnote_ids(child) if i in footnotes]
                    blocks.append(block)
                else:
                    walk(child, inner_quoted)

    walk(body, False)
    if not blocks:
        raise EuParseError(f"{name}: no text found in the XHTML")
    return blocks


def _looks_like_heading(text: str) -> bool:
    """In the legacy HTML an article's heading ("Scope", "Definitions") is a short paragraph right after "Article N",
    without final punctuation and not a numbered paragraph or point."""
    return len(text) <= 100 and not re.search(r"[.;:,]$", text) and not re.match(r"^[\d(—\-]", text)


@dataclass
class TextActDoc:
    title: str
    notes: list[str] = field(default_factory=list)


class _ActBuilder:
    """Turns the block stream of one act into provisions (see :class:`Prov`)."""

    def __init__(self, text_headings: bool = False) -> None:
        self.b = _Builder()
        self.text_headings = text_headings  # article headings are unmarked paragraphs (legacy HTML)
        self.phase = "preamble"  # preamble -> terms -> signature -> annex
        self.title_lines: list[str] = []
        self.headings: dict[str, str] = {}
        self.last_level: str | None = None
        self.article: Prov | None = None
        self.art_lines: list[str] = []  # the article's complete text
        self.own_lines = 0  # lines of the article outside its paragraphs
        self.paragraph: Prov | None = None
        self.para_lines: list[str] = []
        self.para_children: list[str] = []
        self.art_children: list[str] = []
        self.unit: Prov | None = None  # an annex or an attached document
        self.unit_lines: list[str] = []
        self.unit_heading: list[str] = []
        self.terms_lines: list[str] = []  # enacting terms without numbered articles
        self.formula_seen = False
        self.previous_kind = ""
        self.attachments = 0

    # -- helpers

    def context(self) -> str | None:
        return " — ".join(self.headings[level] for level in _DIVISION_LEVELS if self.headings.get(level)) or None

    def _close_paragraph(self) -> None:
        if self.paragraph is not None:
            self.paragraph.text = "\n".join(self.para_lines)
            self.paragraph.is_leaf = not self.para_children
            self.paragraph.own_content = bool(self.para_children)
        self.paragraph, self.para_lines, self.para_children = None, [], []

    def _close_article(self) -> None:
        self._close_paragraph()
        if self.article is not None:
            self.article.text = "\n".join(self.art_lines)
            self.article.is_leaf = not self.art_children
            self.article.own_content = bool(self.art_children) and self.own_lines > 0
        self.article, self.art_lines, self.art_children, self.own_lines = None, [], [], 0

    def _close_unit(self) -> None:
        if self.unit is not None:
            self.unit.text = "\n".join(self.unit_lines)
            self.unit.heading = " ".join(self.unit_heading) or None
        self.unit, self.unit_lines, self.unit_heading = None, [], []

    def _start_unit(self, title: str) -> None:
        self._close_article()
        self._close_unit()
        self.phase = "annex"
        m = _ANNEX_HEADING.match(title)
        if m:
            eid = self.b.eid(None, f"anx_{m.group(1).upper()}" if m.group(1) else "anx")
            self.unit = Prov(eid, "annex", None, "", None, True, False, None)
        else:
            self.attachments += 1
            eid = self.b.eid(None, f"att_{self.attachments}")
            self.unit = Prov(eid, "attachment", None, "", None, True, False, None)
            self.unit_heading.append(title)
        self.b.add(self.unit)

    def _add_point(self, block: Block, parent: Prov, children: list[str], nested: bool) -> list[str]:
        """A point (XHTML table) under ``parent``: one provision, its subpoints below it; returns its text lines."""
        key = _key(block.label)
        roman = nested and re.fullmatch(r"[ivxl]+", key) is not None
        eid = self.b.eid(parent.eid, f"{'subpoint' if roman else 'point'}_{key}")
        kind = "subpoint" if roman else "point"
        prov = Prov(eid, kind, None, "", parent.eid, True, False, parent.context, list(block.notes))
        self.b.add(prov)
        children.append(eid)
        lines = block.text.split("\n") if block.text else []
        sub: list[str] = []
        for inner in block.points:
            lines += self._add_point(inner, prov, sub, nested=True)
        prov.text = "\n".join(lines)
        prov.is_leaf, prov.own_content = not sub, bool(sub)
        if lines:
            lines[0] = f"{block.label} {lines[0]}"
        else:
            lines = [block.label]
        return lines

    def _text_lines(self, block: Block) -> list[str]:
        """The lines a block contributes to the text of an annex or attachment (points flattened)."""
        if block.kind != "point":
            return [block.text] if block.text else []
        lines = block.text.split("\n") if block.text else []
        for inner in block.points:
            lines += self._text_lines(inner)
        if lines:
            lines[0] = f"{block.label} {lines[0]}"
        return lines or [block.label]

    # -- the state machine

    def feed(self, block: Block) -> None:
        kind = block.kind
        if kind == "title":
            if self.phase == "preamble":
                self.title_lines.append(block.text)
            elif self.previous_kind == "title" and self.unit is not None and not _ANNEX_HEADING.match(block.text):
                self.unit_heading.append(block.text)  # a title of several lines
            else:
                self._start_unit(block.text)  # "ANNEX Ia" right after "ANNEX I" is a unit of its own
            self.previous_kind = kind
            return
        self.previous_kind = kind
        if self.phase == "annex":
            assert self.unit is not None
            self.unit_lines += self._text_lines(block)
            self.unit.note += block.notes
            return
        if kind == "text" and self.phase in ("preamble", "terms", "signature") and _ANNEX_HEADING.match(block.text):
            self._start_unit(block.text)  # legacy HTML: the annex heading is an ordinary paragraph
            return
        if kind == "article":
            self._close_article()
            self.phase = "terms"
            eid = self.b.eid(None, f"art_{block.label}")
            self.article = Prov(eid, "article", None, "", None, True, False, self.context(), list(block.notes))
            self.b.add(self.article)
            return
        if kind == "division":
            if self.phase in ("preamble", "terms"):
                self._close_article()
                if block.level == 2 and self.last_level:
                    self.headings[self.last_level] = _ws(f"{self.headings.get(self.last_level, '')} {block.text}")
                    return
                m = _DIVISION_HEADING.match(block.text)
                level = m.group(1).lower().replace("sub-section", "section") if m else "group"
                for lower in _DIVISION_LEVELS[_DIVISION_LEVELS.index(level) :]:
                    self.headings.pop(lower, None)
                self.headings[level] = block.text
                self.last_level = level
            return
        if kind == "end":
            if self.phase == "terms":
                self._close_article()
                self.phase = "signature"
            elif self.phase == "preamble" and self.formula_seen:
                self.phase = "signature"
            return
        if (
            kind == "text"
            and self.text_headings
            and self.article is not None
            and self.article.heading is None
            and not self.art_lines
            and _looks_like_heading(block.text)
        ):
            kind = "subtitle"  # legacy HTML: "Article 1" / "Purpose" / "The purpose of this Directive is ..."
        if kind == "subtitle":
            if self.article is not None and self.article.heading is None and not self.art_lines:
                self.article.heading = block.text
                self.article.note += block.notes
            elif self.article is not None:
                self.art_lines.append(block.text)
                self.own_lines += 1
            return
        if self.phase == "preamble":
            if kind == "text" and _FORMULA.match(block.text):
                self.formula_seen = True
            elif self.formula_seen:
                self.terms_lines += self._text_lines(block)
            return
        if self.phase == "signature" or self.article is None:
            return
        # Text or a point inside the current article.
        if kind == "point":
            parent = self.paragraph or self.article
            children = self.para_children if self.paragraph is not None else self.art_children
            lines = self._add_point(block, parent, children, nested=False)
            (self.para_lines if self.paragraph is not None else self.art_lines).append("\n".join(lines))
            if self.paragraph is not None:
                self.art_lines.append("\n".join(lines))
            return
        text = block.text
        m = _PARA_LABEL_ACT.match(text)
        if m:
            label = m.group(1) + (m.group(2) or "")
            expected = len([c for c in self.art_children if c.split("__")[-1].startswith("para_")]) + 1
            previous = self.paragraph.eid.split("para_")[-1] if self.paragraph is not None else ""
            sequential = int(m.group(1)) == expected and not m.group(2)
            lettered = bool(m.group(2)) and previous and re.sub(r"[a-z]$", "", previous) == m.group(1)
            if sequential or lettered:
                self._close_paragraph()
                peid = self.b.eid(self.article.eid, f"para_{label}")
                self.paragraph = Prov(peid, "paragraph", None, "", self.article.eid, True, False, self.article.context)
                self.paragraph.note = list(block.notes)
                self.b.add(self.paragraph)
                self.art_children.append(peid)
                self.para_lines.append(text[m.end() :])
                self.art_lines.append(text)
                return
        if self.paragraph is not None:
            self.para_lines.append(text)
            self.paragraph.note += block.notes
        else:
            self.own_lines += 1
            self.article.note += block.notes
        self.art_lines.append(text)

    def finish(self) -> tuple[TextActDoc, list[Prov]]:
        self._close_article()
        self._close_unit()
        # A title with nothing under it ("ANNEX I" over "ANNEX Ia", "ANNEX Ib" ...) is not a unit.
        self.b.provisions = [p for p in self.b.provisions if p.kind not in ("annex", "attachment") or p.text]
        if not any(p.eid.startswith("art_") for p in self.b.provisions) and self.terms_lines:
            text = "\n".join(self.terms_lines)
            terms = Prov(self.b.eid(None, "terms"), "article", None, text, None, True, False, None)
            self.b.provisions.insert(0, terms)
        if not self.b.provisions:
            raise EuParseError("no articles, enacting terms or annexes found in the text")
        return TextActDoc(_ws(" ".join(self.title_lines))), self.b.provisions


def parse_text_act(parts: list[Part], fmt: str) -> tuple[TextActDoc, list[Prov]]:
    """Provisions of an act held as OJ XHTML or legacy HTML (``fmt``); several files follow each other."""
    items = [p for p in parts if p.role == f"item:{fmt}" and p.data]
    if not items:
        raise EuParseError(f"no {fmt} file in the bundle")
    builder = _ActBuilder(text_headings=fmt == "html")
    notes: list[str] = []
    description: str | None = None
    for item in items:
        name = item.url or item.role
        if fmt == "html":
            description, blocks = html_blocks(item.data, name)
        else:
            blocks = xhtml_blocks(item.data, name)
        for block in blocks:
            if block.kind == "note":  # what the tokenizer left out of the text (placeholders of the legacy HTML)
                notes.append(block.text)
                continue
            builder.feed(block)
    doc, provs = builder.finish()
    if not doc.title and description:
        doc.title = description
    doc.notes = notes
    return doc, provs


def _domain_from_title(title: str) -> str | None:
    """The domain printed in the act number: ``Regulation (EEC) No 2007/75``, ``64/300/EEC: Council Decision``,
    ``Directive 2002/59/EC`` (EEC, EEC, EC)."""
    m = re.search(r"\(([A-Za-z]{2,7})(?:,\s*[A-Za-z]{2,7})*\)\s*(?:No\s*)?\d+/\d+", title)
    if not m:
        m = re.search(r"\b\d{2,4}/\d+/([A-Za-z]{2,7})\b", title)
    return DOMAIN_CODES.get(m.group(1).upper()) if m else None


# --------------------------------------------------------------------------- changes -> versions


# Latin suffixes of inserted articles in the metadata: "71 BI" is Article 71a, "71 TR" Article 71b...
LATIN_SUFFIXES = {"BI": "a", "TR": "b", "QT": "c", "QQ": "d"}


def _article_number(value: str) -> str:
    m = re.fullmatch(r"(\d+)\s+([A-Z]{2})", value.strip())
    if m and m.group(2) in LATIN_SUFFIXES:
        return m.group(1) + LATIN_SUFFIXES[m.group(2)]
    return _key(value)


def _location_path(location: str) -> list[str]:
    """``{AR} 6 {PA} 1 {PTA} (b) PT (i)`` -> ``[art_6, para_1, point_b, subpoint_i]`` (as far as indexed)."""
    path: list[str] = []
    for code, value in re.findall(r"\{([^}|]+)(?:\|[^}]*)?\}\s*([^{]*)", location):
        value = value.strip()
        if code == "AR" and value:
            path = [f"art_{_article_number(value)}"]
        elif code == "AN":
            path = [f"anx_{value.strip().upper()}" if value else "anx"]
            break  # annexes are indexed as a whole
        elif code == "PA" and value and path:
            path.append(f"para_{_key(value)}")
        elif code == "ALN" and path:
            if _key(value).isdigit() and _key(value) != "1":
                path.append(f"subpara_{_key(value)}")
            elif not _key(value).isdigit():
                break  # a new or whole unnumbered paragraph: the parent changes
        elif code in ("PTA", "PO") and value and path:
            first, *rest = re.split(r"\s+PT\s+", value)
            path.append(f"point_{_key(first)}")
            path.extend(f"subpoint_{_key(r)}" for r in rest)
        else:
            break  # chapter, part, indent, text: no finer location is indexed
    return path


def _resolve_path(path: list[str], eids: set[str]) -> list[str]:
    """In an article without numbered paragraphs the metadata counts its unnumbered paragraphs
    (``{AR} 113 {PA} 3`` is the third one): use ``subpara_3`` when there is no ``para_3``."""
    out: list[str] = []
    for seg in path:
        candidate = "__".join([*out, seg])
        if seg.startswith("para_") and candidate not in eids:
            alternative = "__".join([*out, "sub" + seg])
            if alternative in eids:
                seg = "sub" + seg
        out.append(seg)
    return out


def _readable(location: str) -> str:
    return _codes(location)


@dataclass
class VersionEffects:
    outstanding: list[dict[str, Any]] = field(default_factory=list)
    ends: dict[str, date] = field(default_factory=dict)
    notes: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    events: list[ParsedEvent] = field(default_factory=list)


def apply_changes(changes: list[Change], language: str, eids: set[str]) -> VersionEffects:
    lang3 = LANG3[language]
    lang2 = language.upper()
    fx = VersionEffects()

    def ancestors(eid: str) -> list[str]:
        segs = eid.split("__")
        return ["__".join(segs[:i]) for i in range(1, len(segs))]

    for change in changes:
        by_uri = CELLAR_CELEX + change.celex
        by_title = change.titles.get(lang3) or change.celex
        label = RELATION_LABELS.get(change.relation, change.relation)
        if change.relation == "resource_legal_corrects_resource_legal":
            listed = [a.get("language_list") for a in change.axioms if a.get("language_list")]
            languages = {code.strip() for value in listed for code in (value or "").split(",")}
            if listed and lang2 not in languages:
                continue
            which = f"language versions {', '.join(sorted(languages))}" if listed else "languages not stated"
            fx.outstanding.append(
                {
                    "affects_eid": None,
                    "affected_label": None,
                    "by_uri": by_uri,
                    "by_title": f"Corrigendum {change.celex}",
                    "description": f"Corrigendum of {change.document_date or 'unknown date'} ({which}); "
                    "the corrections are not applied to this text.",
                }
            )
            continue
        located = [a for a in change.axioms if a.get("reference_to_modified_location")]
        starts = sorted(d for d in (_date(a.get("start_of_validity")) for a in change.axioms) if d)
        if starts:
            fx.events.append(
                ParsedEvent(
                    "amendment" if "amends" in change.relation else "repeal",
                    starts[0],
                    by_uri,
                    f"{label} {by_title}",
                    "sparql:changes",
                )
            )
        if not located:
            start = starts[0] if starts else None
            fx.outstanding.append(
                {
                    "affects_eid": None,
                    "affected_label": None,
                    "by_uri": by_uri,
                    "by_title": by_title,
                    "description": f"{label} {change.celex}"
                    + (f", with effect from {start}" if start else "")
                    + "; location not recorded; not applied to this text.",
                }
            )
            continue
        for axiom in located:
            location = axiom["reference_to_modified_location"]
            role = _code(axiom.get("role2")) or ""
            start = _date(axiom.get("start_of_validity"))
            path = _resolve_path(_location_path(location), eids)
            prefix = next(("__".join(path[:i]) for i in range(len(path), 0, -1) if "__".join(path[:i]) in eids), None)
            exact = prefix is not None and prefix == "__".join(path)
            what = f"{ROLE_LABELS.get(role, role or 'Change')} of {_readable(location)}"
            fx.outstanding.append(
                {
                    "affects_eid": prefix,
                    "affected_label": _readable(location),
                    "by_uri": by_uri,
                    "by_title": by_title,
                    "description": f"{what} by {change.celex}"
                    + (f" with effect from {start}" if start else "")
                    + " (Publications Office metadata); not applied to this text.",
                }
            )
            if prefix is None or start is None:
                continue
            ended = [prefix, *ancestors(prefix)]
            if exact and role not in ("J", "C"):
                ended += [e for e in eids if e.startswith(prefix + "__")]
            for eid in ended:
                if eid not in fx.ends or start < fx.ends[eid]:
                    fx.ends[eid] = start
                fx.notes[eid].append(
                    f"{what} by {change.celex} with effect from {start} (Publications Office metadata). "
                    "This wording is not current from that date; the later wording is not indexed."
                )
    return fx


# --------------------------------------------------------------------------- document


_ELI = re.compile(r"^https?://data\.europa\.eu/eli/([a-z_]+)/([0-9a-z_]+)(?:/(\d+))?(?:/oj)?/?$")


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map an ELI of an EU act to its ULIS work URI.

    ``http://data.europa.eu/eli/reg/2016/679/oj`` -> (``/eu/reg/2016/679``, None)
    """
    m = _ELI.match(uri)
    return ("/eu/" + "/".join(x for x in m.groups() if x), None) if m else None


def _work_uri(meta: WorkMeta) -> str:
    resolved = resolve_uri(meta.eli or "")
    return resolved[0] if resolved else "/eu/celex/" + re.sub(r"[^0-9A-Za-z]", "_", meta.celex)


def _treaty(meta: WorkMeta) -> str | None:
    m = re.match(r"^https?://data\.europa\.eu/eli/treaty/([a-z]+)_\d{4}/", meta.eli or "")
    return TREATIES.get(m.group(1)) if m else None


def parse_eu_cellar(content: bytes) -> list[ParsedDocument]:
    bundle, parts = load_bundle(content)
    celex, language, fmt = bundle.get("celex"), bundle.get("language"), bundle.get("content_format")
    if not isinstance(celex, str) or language not in LANG3 or fmt not in ("fmx4", "xhtml", "html"):
        raise EuParseError("bundle lacks celex, language or format")
    lang3 = LANG3[language]
    base_celex = bundle.get("base_celex")
    if base_celex is not None and (not isinstance(base_celex, str) or fmt != "fmx4"):
        raise EuParseError(f"{celex}: a consolidated version is read from Formex 4 with its base act's CELEX number")
    consolidated = base_celex is not None
    own_meta = parse_metadata(_bindings(parts, "sparql:metadata"), celex)
    # A consolidated version describes itself with the base act's facts: type, ELI, dates of entry into force.
    meta = parse_metadata(_bindings(parts, "sparql:metadata:base"), str(base_celex)) if consolidated else own_meta
    changes = [] if consolidated else parse_changes(_bindings(parts, "sparql:changes"))
    items = _bindings(parts, f"sparql:items:{fmt}")
    expression_title = next(
        (_ws(r["title"]) for r in items if r.get("lang", "").endswith("/" + lang3.upper()) and r.get("title")), None
    )
    if not any(p.role.startswith("item:") for p in parts):
        raise EuParseError(f"{celex}: no {fmt} file for language {language}")

    treaty = _treaty(meta)
    act_type = ACT_TYPES.get((meta.resource_type or "").split("_")[0])
    if treaty == "CHARTER":
        instrument_type = "charter"
    elif treaty in ("TEU", "TFEU"):
        instrument_type = "founding_treaty"
    elif act_type:
        instrument_type = act_type
    else:
        raise EuParseError(f"{celex}: unsupported resource type {meta.resource_type!r}")

    ext: dict[str, Any] = {"celex": celex, "eli": own_meta.eli, "resource_type": own_meta.resource_type}
    if consolidated:
        ext.update(base_celex=base_celex, resource_type=meta.resource_type)
    if fmt == "fmx4":
        fdoc, provs = parse_formex(parts)
        if consolidated:  # the consolidated file does not repeat the act's number and year: the base ELI does
            eli = _ELI.match(meta.eli or "")
            if eli and eli.group(2).isdigit() and eli.group(3):
                fdoc.year, fdoc.number = int(eli.group(2)), eli.group(3)
            elif re.fullmatch(r"3\d{4}[A-Z]\d{4}", str(base_celex)):
                fdoc.year, fdoc.number = int(str(base_celex)[1:5]), str(int(str(base_celex)[6:]))
        title = expression_title or fdoc.title
        short_title: str | None = None
        number: str | None = None
        if act_type:
            domain = DOMAIN_CODES.get((fdoc.domain_code or "").upper()) or _title_domain(fdoc)
            ext.update(act_type=act_type, year=fdoc.year, number=fdoc.number, domain=domain)
            short_title = designation(act_type, fdoc.year, fdoc.number, domain, language)
            if fdoc.year and fdoc.number:
                number = (
                    f"{fdoc.year}/{fdoc.number}"
                    if fdoc.year >= 2015 or act_type != "regulation"
                    else (f"{fdoc.number}/{fdoc.year}")
                )
        if treaty == "CHARTER":
            ext["treaty"] = treaty
            # The Formex title is set in capitals (and lower-cased in the source); the metadata
            # title is the properly cased title of this language version.
            short_title = expression_title or fdoc.title or None
        publication = fdoc.publication_date or meta.publication_date
    elif treaty in ("TEU", "TFEU"):
        xtitle, provs = parse_xhtml_treaty(parts)
        title = expression_title or xtitle
        ext["treaty"] = treaty
        short_title = TREATY_ABBREVIATION[treaty].get(language)
        number = None
        publication = meta.document_date
    else:
        # An act Cellar holds without a Formex 4 file: the OJ XHTML or the legacy EUR-Lex HTML of its text.
        if not act_type or consolidated:
            raise EuParseError(f"{celex}: {fmt} is read for acts as adopted only (regulations, directives, decisions)")
        tdoc, provs = parse_text_act(parts, fmt)
        title = expression_title or tdoc.title
        year: int | None = None
        number = None
        act_number = re.fullmatch(r"3(\d{4})[A-Z](\d{4})", celex)
        if act_number:
            year, number = int(act_number.group(1)), str(int(act_number.group(2)))
        domain = _domain_from_title(title) or _domain_from_title(tdoc.title)
        ext.update(act_type=act_type, year=year, number=number, domain=domain)
        short_title = designation(act_type, year, number, domain, language)
        if year and number:
            number = f"{year}/{number}" if year >= 2015 or act_type != "regulation" else f"{number}/{year}"
        publication = meta.publication_date
        rendition = "legacy EUR-Lex HTML" if fmt == "html" else "OJ XHTML"
        format_notes = [
            f"Text from the Publications Office's {rendition} rendition of the Official Journal, the only text format "
            "Cellar holds for this act (no Formex 4 file). The structure (articles, numbered paragraphs"
            f"{', points' if fmt == 'xhtml' else ''}, annexes) is recognised from the headings and labels of that "
            "rendition; the wording is as held, including any footnotes printed within the text.",
            *tdoc.notes,
        ]
        for prov in provs:
            prov.note = [*format_notes, *prov.note]
    if not provs:
        raise ParseSkipped(f"{celex}: the Formex file holds no operative text to ingest")

    # ---- dates
    events: list[ParsedEvent] = []
    note_lines: list[str] = []
    if meta.signature_dates:
        events.append(
            ParsedEvent("signature", meta.signature_dates[0], None, None, "sparql:resource_legal_date_signature")
        )
    if meta.document_date:
        events.append(ParsedEvent("document", meta.document_date, None, None, "sparql:work_date_document"))
    for event_type, facts, locator in (
        ("entry_into_force", meta.entry_into_force, "sparql:EV"),
        ("application", meta.application, "sparql:MA"),
        ("partial_application", meta.partial_application, "sparql:MA/PART"),
        ("transposition_deadline", meta.transposition, "sparql:directive_date_transposition"),
    ):
        for fact in facts:
            events.append(ParsedEvent(event_type, fact.value, None, _codes(fact.comment or "") or None, locator))

    currency: date | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    version_date: date | None
    if consolidated:
        # A consolidated version: the whole act as it applies from START.DATE (to END.DATE), documentation only.
        start = fdoc.cons_start
        if start is None:
            raise EuParseError(f"{celex}: no START.DATE in the consolidated version")
        validity_basis, text_origin, authoritative, consolidation = (
            SNAPSHOT_BASIS,
            "official_revised",
            False,
            "point_in_time",
        )
        valid_from, valid_to, currency = start, fdoc.cons_end, fdoc.cons_date
        span = f"from {start.isoformat()}" + (f" to {valid_to.isoformat()}" if valid_to else "")
        note_lines.append(
            f"Consolidated version applying {span}, prepared by the Publications Office for documentation; only the "
            "acts as published in the Official Journal are authentic. The version gives no date for individual "
            "provisions."
        )
        version_date = start
    elif instrument_type == "founding_treaty":
        # A consolidated version attests the text on its publication date only.
        validity_basis, text_origin, authoritative, consolidation = (
            "attested_at_edition",
            "official_revised",
            False,
            "consolidated",
        )
        currency = publication
        note_lines.append(
            f"Consolidated version published on {publication} by the Publications Office for documentation; "
            "only the Treaties as signed and ratified are authentic. When this wording took effect is not recorded."
        )
        version_date = publication or meta.document_date
    else:
        validity_basis, text_origin, authoritative = "source_periods", "official_conversion", True
        consolidation = "as_published" if instrument_type == "charter" else "as_adopted"
        general = meta.application
        ev = meta.entry_into_force
        if len(general) == 1:
            valid_from = general[0].value
        elif not general and len({d.value for d in ev}) == 1:
            valid_from = ev[0].value
        dates = []
        if ev:
            dates.append("entry into force " + "; ".join(d.describe() for d in ev))
        if general:
            dates.append("application " + "; ".join(d.describe() for d in general))
        if meta.partial_application:
            dates.append(
                "partial application "
                + "; ".join(d.describe() for d in meta.partial_application)
                + " - the provisions concerned are stated in the act and are not modelled"
            )
        if meta.transposition:
            dates.append("transposition " + "; ".join(d.describe() for d in meta.transposition))
        if dates:
            note_lines.append("Publications Office metadata: " + "; ".join(dates) + ".")
        if valid_from is None:
            note_lines.append("No single date of entry into force or application is recorded for this act.")
        ends = [d for d in meta.end_of_validity if d.value != NO_END and _code(d.comment) != "FIN/VAL/PART"]
        if ends:
            valid_to = min(d.value for d in ends)
            note_lines.append(f"End of validity {valid_to} (Publications Office metadata).")
        if instrument_type == "directive":
            note_lines.append(
                "Directive: binding on the Member States to which it is addressed as to the result to be achieved, "
                "leaving the choice of form and methods to national authorities (Article 288 TFEU); national "
                "transposing measures are not indexed."
            )
        version_date = publication or meta.document_date
    if version_date is None:
        raise EuParseError(f"{celex}: no publication date")

    eids = {p.eid for p in provs}
    fx = VersionEffects() if consolidated else apply_changes(changes, language, eids)
    events.extend(fx.events)

    provisions: list[ParsedProvision] = []
    for order, p in enumerate(provs):
        end = min((d for d in (valid_to, fx.ends.get(p.eid)) if d), default=None)
        record = not (end is not None and valid_from is not None and end <= valid_from)
        notes = [*p.note, *note_lines, *dict.fromkeys(fx.notes.get(p.eid, []))]
        top = p.eid.split("__")[0]
        provisions.append(
            ParsedProvision(
                eid=p.eid,
                kind=p.kind,
                num_path=p.eid.split("__"),
                top_kind="annex" if top.startswith(("anx", "att")) else "article",
                heading=p.heading,
                text=p.text,
                parent_eid=p.parent,
                sort_order=order,
                status="in_force",
                valid_from=valid_from,
                valid_to=end if record else None,
                extent=["EU"],
                note="\n".join(notes) or None,
                refs=[],
                is_leaf=p.is_leaf,
                has_own_content=p.own_content,
                context_heading=p.context,
                record_version=record,
            )
        )

    work_uri = _work_uri(meta)
    work_date = (
        meta.signature_dates[0]
        if instrument_type in ("founding_treaty", "charter") and meta.signature_dates
        else (meta.document_date)
    )
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=instrument_type,
            jurisdiction_id="EU",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/{lang3.lower()}@{version_date.isoformat()}",
            work_title=title,
            title=title,
            short_title=short_title,
            number=number,
            work_date=work_date,
            language=language,
            authoritative=authoritative,
            text_origin=text_origin,
            consolidation_kind=consolidation,
            version_date=version_date,
            currency_date=currency,
            extent=["EU"],
            ext_module="eu",
            ext={k: v for k, v in ext.items() if v is not None},
            outstanding_changes=fx.outstanding,
            events=events,
            provisions=provisions,
            validity_basis=validity_basis,
        )
    ]
