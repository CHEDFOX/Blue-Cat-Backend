"""Parser for German federal law from Gesetze im Internet (gii-norm XML).

Gesetze im Internet (www.gesetze-im-internet.de) is published by the Federal Republic of
Germany (Federal Ministry of Justice and Consumer Protection) and edited by the Federal Office
of Justice; data and technical provision are by juris GmbH. Each law is offered as
``https://www.gesetze-im-internet.de/<abbr>/xml.zip`` holding one XML file (root
``<dokumente>``, DTD ``gii-norm.dtd``). The consolidated texts are **not the official
version**: the authentic text is the promulgation in the Bundesgesetzblatt.

Format notes (what the file provides, and how ULIS uses it):

* the first ``<norm>`` holds the law's metadata: ``jurabk``/``amtabk`` (juris and official
  abbreviations), ``ausfertigung-datum`` (date the original Act was signed), ``fundstelle``
  (promulgation reference), ``langue`` (long title) and ``standangabe`` entries
  (``Stand``: last amending Act; ``Neuf``: re-promulgation; ``Hinweis``; ``Sonst``);
* norms with a ``gliederungseinheit`` are structural headings (Buch, Abschnitt, Titel);
* every other norm is one provision, named by ``enbez`` (``§ 823``, ``Art 1``, ``Präambel``,
  ``(XXXX) §§ 3 bis 6`` for a repealed range). Its ``<P>`` elements are the paragraphs
  (Absätze, printed ``(1)``), ``<DL>`` lists are numbered items (``1.``, ``a)``, ``aa)``), and
  in tax laws ``<SUP class="Rec">`` marks sentence numbers (Satz). These labels become the
  provision structure (``num_path``); a leaf's text starts after its own label;
* ``<Footnotes>`` (official footnotes) and ``<fussnoten>`` (editorial notes, e.g. that the
  Federal Constitutional Court declared a provision void) are kept as notes, never as text;
* **dates:** the file gives no per-provision validity. The site's Hinweise (item 3) state that
  amendments are applied to the text when they enter into force, so the file is the text in
  force when juris built it (``dokumente/@builddate``). Every provision is therefore
  ``attested_at_edition``: in force at the build date, start unknown (``valid_from`` None);
* ``(weggefallen)`` marks repealed provisions (status ``repealed``, empty text);
* a ``Hinweis`` saying an amendment is "textlich nachgewiesen, dokumentarisch noch nicht
  abschließend bearbeitet" means the amendment *is* in the text (Hinweise item 4); any other
  ``Hinweis`` is reported as an outstanding change.

XML safety: the file declares ``<!DOCTYPE dokumente SYSTEM ".../gii-norm.dtd">``. The DTD is
never fetched. That exact bare declaration (no internal subset) is stripped, then the XML is
parsed by defusedxml with DTDs, entities and external references forbidden, so any other
DOCTYPE - in particular one declaring entities - is rejected.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision

PARSER_ID = "gii_norm_xml"
PARSER_VERSION = "1.0.0"
MAX_XML_BYTES = 64 * 1024 * 1024  # the largest federal law is far below this
NOTE_LIMIT = 1200

# The one DOCTYPE form the publisher uses: an external DTD reference and nothing else.
_BARE_DOCTYPE = re.compile(rb'\A(\xef\xbb\xbf)?(<\?xml[^>]*\?>\s*)?<!DOCTYPE\s+dokumente\s+SYSTEM\s+"[^"<>\[\]]*"\s*>')
_ABSATZ = re.compile(r"^\((\d+[a-z]*)\)\s*")
_ITEM_KINDS = (
    (re.compile(r"^(\d+[a-z]*)\.$"), "number", "nr"),
    (re.compile(r"^([a-z])\)$"), "letter", "buchst"),
    (re.compile(r"^(([a-z])\2)\)$"), "double_letter", "buchst"),
    (re.compile(r"^(([a-z])\2\2)\)$"), "triple_letter", "buchst"),
)
WEGGEFALLEN = {"(weggefallen)", "weggefallen"}
SKIPPED_NORMS = ("Inhaltsübersicht", "Anhang EV")
DROP = {"FnR", "Footnotes"}
BLOCKS = {"P", "LA", "DD", "Title", "Ident", "Subtitle", "pre"}

Part = tuple[str, object]  # ("t", text) or ("e", element), in document order


class GiiParseError(ValueError):
    pass


# --------------------------------------------------------------------------- input


def xml_from_download(content: bytes) -> bytes:
    """The XML inside an ``xml.zip`` as downloaded (or the bytes themselves if already XML)."""
    if not content.startswith(b"PK\x03\x04"):
        return content
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise GiiParseError(f"not a valid zip archive: {exc}") from exc
    members = [i for i in archive.infolist() if not i.is_dir() and i.filename.lower().endswith(".xml")]
    if len(members) != 1:
        raise GiiParseError(f"expected exactly one XML file in the archive, found {len(members)}")
    member = members[0]
    if member.file_size > MAX_XML_BYTES:
        raise GiiParseError(f"{member.filename}: declared size {member.file_size} exceeds the limit")
    with archive.open(member) as fh:
        data = fh.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise GiiParseError(f"{member.filename}: decompressed size exceeds the limit")
    return data


def parse_xml(xml: bytes) -> Element:
    """Parse gii-norm XML safely: strip only the publisher's bare DOCTYPE, forbid everything else."""
    m = _BARE_DOCTYPE.match(xml)
    if m:
        xml = (m.group(1) or b"") + (m.group(2) or b"") + xml[m.end() :]
    try:
        return fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception as exc:
        raise GiiParseError(f"not well-formed or unsafe XML: {exc}") from exc


# --------------------------------------------------------------------------- text


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _is_sentence_marker(el: object) -> bool:
    return (
        isinstance(el, Element)
        and el.tag == "SUP"
        and el.get("class") == "Rec"
        and _ws("".join(el.itertext())).isdigit()
    )


def _flat(el: Element, skip: frozenset[Element] = frozenset()) -> str:
    """Source text of an element with ``\\n`` at block boundaries. Footnote marks and
    sentence-number superscripts (structure, not text) and the elements in ``skip`` are left out."""
    tag = el.tag if isinstance(el.tag, str) else ""
    if tag in DROP or el in skip or _is_sentence_marker(el):
        return ""
    if tag == "BR":
        return "\n"
    if tag == "DL":
        out, label = [], ""
        for child in el:
            if child.tag == "DT":
                label = _ws(_flat(child))
            elif child.tag == "DD":
                body = _flat(child, skip).strip()
                out.append(f"\n{label} {body}" if label else f"\n{body}")
                label = ""
        return "".join(out) + "\n"
    if tag == "table":
        rows = [" | ".join(_ws(_flat(e, skip)) for e in row.iter("entry")) for row in el.iter("row")]
        return "\n" + "\n".join(r for r in rows if r.strip(" |")) + "\n"
    parts = [el.text or ""]
    for child in el:
        parts.append(_flat(child, skip))
        parts.append(child.tail or "")
    joined = "".join(parts)
    return f"\n{joined}\n" if tag in BLOCKS else joined


def _lines(raw: str) -> str:
    return "\n".join(line for line in (_ws(x) for x in raw.split("\n")) if line)


def _inline(el: Element | None) -> str:
    return _ws(_flat(el)) if el is not None else ""


def _parts(el: Element) -> list[Part]:
    """The inline content of ``el`` in order. Block children (``LA``, ``P``) are opened, so that
    sentence markers and lists inside them are seen at the same level; lists (``DL``) are not."""
    out: list[Part] = [("t", el.text or "")]
    for child in el:
        if child.tag in ("LA", "P"):
            out.extend([("t", "\n"), *_parts(child), ("t", "\n")])
        else:
            out.append(("e", child))
        out.append(("t", child.tail or ""))
    return out


def _seq(blocks: list[Element]) -> list[Part]:
    out: list[Part] = []
    for block in blocks:
        out.extend([("t", "\n"), *_parts(block), ("t", "\n")])
    return out


def _render(parts: list[Part], skip: frozenset[Element] = frozenset(), strip_label: bool = False) -> str:
    text = _lines("".join(_flat(x, skip) if isinstance(x, Element) else str(x) for _, x in parts))
    return _ABSATZ.sub("", text, count=1) if strip_label else text


# --------------------------------------------------------------------------- structure


@dataclass
class _Unit:
    kind: str
    label: str  # num_path label as printed: "(1)", "1.", "a)", "aa)"; "^2" for superscript Satz 2
    eid_part: str
    text: str
    children: list[_Unit] = field(default_factory=list)
    own: bool = False


def _item_kind(label: str) -> tuple[str, str] | None:
    for pattern, kind, prefix in _ITEM_KINDS:
        m = pattern.match(label)
        if m:
            return kind, f"{prefix}-{m.group(1)}"
    return None


def _list_units(dls: list[Element]) -> tuple[list[_Unit], frozenset[Element]]:
    """Items of the citable lists among ``dls``, and the lists that were split into items.

    A list whose labels are not citable (roman numerals, dashes, blank) stays text. Several lists
    under one parent are split only if they continue one another (same kind of label, no label
    repeated, as in Art. 73 Abs. 1 GG); otherwise they belong to different, unmarked sentences and
    a citation without the sentence number would be ambiguous, so none is split.
    """
    pairs: list[tuple[str, str, str, Element]] = []
    split: list[Element] = []
    for dl in dls:
        items, label = [], None
        for child in dl:
            if child.tag == "DT":
                label = _ws(_flat(child))
            elif child.tag == "DD" and label is not None:
                items.append((label, child))
                label = None
        kinds = [_item_kind(lab) for lab, _ in items]
        if items and all(kinds):
            pairs.extend((lab, k[0], k[1], dd) for (lab, dd), k in zip(items, kinds, strict=True) if k)
            split.append(dl)
    labels = [p[0] for p in pairs]
    if len(labels) != len(set(labels)) or len({p[1] for p in pairs}) > 1:
        return [], frozenset()
    units = [_unit(kind, label, eid_part, _parts(dd)) for label, kind, eid_part, dd in pairs]
    return units, frozenset(split)


def _sentences(parts: list[Part], strip_label: bool) -> list[tuple[int, list[Part]]] | None:
    """Split content at its sentence-number superscripts; None if it has none (or they are odd).

    Sentence 1 often has no marker of its own: text before a first marker "2" is sentence 1.
    """
    found = [(i, x) for i, (_, x) in enumerate(parts) if isinstance(x, Element) and _is_sentence_marker(x)]
    if not found:
        return None
    markers = [i for i, _ in found]
    numbers = [int(_ws("".join(x.itertext()))) for _, x in found]
    out: list[tuple[int, list[Part]]] = []
    if _render(parts[: markers[0]], strip_label=strip_label):
        if numbers[0] != 2:
            return None
        out.append((1, parts[: markers[0]]))
    elif numbers[0] != 1:
        return None
    if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        return None
    bounds = [*markers, len(parts)]
    for n, start, end in zip(numbers, bounds, bounds[1:], strict=False):
        out.append((n, parts[start + 1 : end]))
    return out


def _dls(parts: list[Part]) -> list[Element]:
    return [x for _, x in parts if isinstance(x, Element) and x.tag == "DL"]


def _unit(kind: str, label: str, eid_part: str, parts: list[Part], strip_label: bool = False) -> _Unit:
    """A unit and its children: its numbered sentences if the source numbers them, else its list items."""
    text = _render(parts, strip_label=strip_label)
    sentences = _sentences(parts, strip_label)
    if sentences is not None:
        children = [
            _unit("sentence", f"^{n}", f"satz-{n}", chunk, strip_label=strip_label and n == 1) for n, chunk in sentences
        ]
        return _Unit(kind, label, eid_part, text, children, own=False)
    items, split = _list_units(_dls(parts))
    own = not items or bool(_render(parts, split, strip_label))
    return _Unit(kind, label, eid_part, text, items, own)


def _norm_unit(kind: str, label: str, eid: str, content: Element | None) -> _Unit:
    """A norm (§, Art, ...) with its paragraphs (Absätze ``(1)``, ``(2)``), or its items/sentences."""
    blocks = list(content) if content is not None else []
    groups: list[tuple[str | None, list[Element]]] = []
    for block in blocks:
        m = _ABSATZ.match(_render(_seq([block]))) if block.tag == "P" else None
        if m:
            groups.append((m.group(1), [block]))
        elif groups:
            groups[-1][1].append(block)  # continuation of the paragraph (or of the lead text)
        else:
            groups.append((None, [block]))
    numbered = [(lab, members) for lab, members in groups if lab is not None]
    if not numbered:
        return _unit(kind, label, eid, _seq(blocks))
    lead = groups[0][1] if groups[0][0] is None else []
    children = [
        _unit("subsection", f"({lab})", f"abs-{lab}", _seq(members), strip_label=True) for lab, members in numbered
    ]
    return _Unit(kind, label, eid, _render(_seq(blocks)), children, own=bool(lead and _render(_seq(lead))))


# --------------------------------------------------------------------------- identifiers


def _slug(text: str) -> str:
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"})
    return re.sub(r"[^a-z0-9]+", "-", text.translate(table).lower()).strip("-")


def norm_identity(enbez: str) -> tuple[str, str, str]:
    """(kind, eId, num_path label) of a norm's ``enbez``: ``§ 823`` -> (section, para-823, § 823)."""
    label = re.sub(r"^\(X+\)\s*", "", _ws(enbez))
    if m := re.fullmatch(r"§\s*(\d+[a-z]*)", label):
        return "section", f"para-{m.group(1)}", f"§ {m.group(1)}"
    if m := re.fullmatch(r"§§\s*(.+)", label):
        return "section", f"paras-{_slug(m.group(1))}", f"§§ {m.group(1)}"
    if m := re.fullmatch(r"Art\.?\s*(\d+[a-z]*)", label):
        return "article", f"art-{m.group(1)}", f"Art {m.group(1)}"
    if m := re.fullmatch(r"Art\.?\s+(.+)", label):
        return "article", f"arts-{_slug(m.group(1))}", f"Art {m.group(1)}"
    if label.startswith("Anlage"):
        return "annex", _slug(label), label
    if label == "Präambel":
        return "preamble", "praeambel", label
    if label == "Eingangsformel":
        return "enacting_formula", "eingangsformel", label
    return "other", _slug(label) or "norm", label


def gii_slug(jurabk: str) -> str:
    """The site's path for a law: its juris abbreviation, lower-cased, other characters as ``_``
    (``BDSG 2018`` -> ``bdsg_2018``). Checked against the downloaded laws in the tests."""
    return re.sub(r"[^a-z0-9-]", "_", jurabk.lower())


def work_uri_for(slug: str) -> str:
    return f"/de/bund/{slug}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map a Gesetze im Internet URL to (ULIS work URI, eId).

    ``https://www.gesetze-im-internet.de/bgb/__823.html`` -> (``/de/bund/bgb``, ``para-823``);
    ``.../gg/art_1.html`` -> (``/de/bund/gg``, ``art-1``).
    """
    m = re.match(r"^https?://www\.gesetze-im-internet\.de/([a-z0-9_\-]+)/([^?#]*)$", uri)
    if not m or m.group(1) in ("dtd", "aktuell"):
        return None
    work, page = work_uri_for(m.group(1)), m.group(2)
    if p := re.fullmatch(r"__(\d+[a-z]*)\.html", page):
        return work, f"para-{p.group(1)}"
    if p := re.fullmatch(r"art_(\d+[a-z]*)\.html", page):
        return work, f"art-{p.group(1)}"
    return work, None


# --------------------------------------------------------------------------- document


def _date(value: str | None) -> date | None:
    value = (value or "").strip()
    try:
        if re.fullmatch(r"\d{8}(\d{6})?", value):
            return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def _note(norm: Element) -> str | None:
    """Official footnotes and editorial notes of a norm, shortened to ``NOTE_LIMIT``."""
    texts = [_inline(fn) for fn in norm.iter("Footnote")]
    editorial = norm.find("textdaten/fussnoten")
    if editorial is not None:
        texts.append(_inline(editorial))
    note = "; ".join(t for t in texts if t)
    if len(note) > NOTE_LIMIT:
        note = note[:NOTE_LIMIT].rstrip() + " […]"
    return note or None


def _instrument_type(jurabk: str, title: str) -> str:
    if jurabk == "GG":
        return "constitution"
    if re.search(r"\bVerordnung\b", title) and not title.startswith("Gesetz"):
        return "ordinance"
    return "act"


def _join(*parts: str | None) -> str | None:
    return "; ".join(p for p in parts if p) or None


def parse_gesetze_im_internet(content: bytes) -> list[ParsedDocument]:
    root = parse_xml(xml_from_download(content))
    if root.tag != "dokumente":
        raise GiiParseError(f"unsupported root element {root.tag!r} (expected gii-norm <dokumente>)")
    norms = root.findall("norm")
    head = norms[0].find("metadaten") if norms else None
    if head is None:
        raise GiiParseError("missing head norm metadata")
    build = _date(root.get("builddate"))
    if build is None:
        raise GiiParseError("missing or invalid dokumente/@builddate")
    jurabk = _ws(head.findtext("jurabk") or "")
    if not jurabk:
        raise GiiParseError("missing jurabk")
    amtabk = _ws(head.findtext("amtabk") or "") or None
    title = _inline(head.find("langue")) or _inline(head.find("kurzue")) or jurabk
    enacted = _date(head.findtext("ausfertigung-datum"))
    fundstelle = head.find("fundstelle")
    reference = (
        _ws(f"{fundstelle.findtext('periodikum') or ''} {fundstelle.findtext('zitstelle') or ''}")
        if fundstelle is not None
        else ""
    )
    stand: list[dict[str, str]] = []
    outstanding: list[dict[str, object]] = []
    for entry in head.findall("standangabe"):
        typ, comment = _ws(entry.findtext("standtyp") or ""), _inline(entry.find("standkommentar"))
        if not comment:
            continue
        stand.append({"typ": typ, "kommentar": comment})
        if typ == "Hinweis" and "textlich nachgewiesen" not in comment:
            outstanding.append(
                {
                    "affects_eid": None,
                    "affected_label": None,
                    "by_uri": None,
                    "by_title": comment,
                    "description": comment,
                }
            )
    work_uri = work_uri_for(gii_slug(jurabk))
    provisions: list[ParsedProvision] = []
    used: dict[str, int] = {}
    outline: dict[int, str] = {}

    def unique(eid: str) -> str:
        n = used.get(eid, 0) + 1
        used[eid] = n
        return eid if n == 1 else f"{eid}-{n}"

    def add(
        unit: _Unit,
        parent_eid: str | None,
        path: list[str],
        top_kind: str,
        heading: str | None,
        note: str | None,
        context: str | None,
        repealed: bool,
    ) -> None:
        eid = unique(f"{parent_eid}-{unit.eid_part}" if parent_eid else unit.eid_part)
        children = [] if repealed else unit.children
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=unit.kind,
                num_path=[*path, unit.label],
                top_kind=top_kind,
                heading=heading,
                text="" if repealed else unit.text,
                parent_eid=parent_eid,
                sort_order=len(provisions),
                status="repealed" if repealed else "in_force",
                valid_from=None,
                valid_to=None,
                extent=["DE"],
                note=_join("(weggefallen)" if repealed else None, note),
                refs=[],
                is_leaf=not children,
                has_own_content=unit.own and bool(children),
                context_heading=context,
            )
        )
        for child in children:
            add(child, eid, [*path, unit.label], top_kind, None, note, context, child.text in WEGGEFALLEN)

    for norm in norms[1:]:
        meta = norm.find("metadaten")
        if meta is None:
            continue
        outline_el = meta.find("gliederungseinheit")
        if outline_el is not None:
            level = max(1, len(_ws(outline_el.findtext("gliederungskennzahl") or "")) // 3)
            for deeper in [k for k in outline if k >= level]:
                del outline[deeper]
            outline[level] = _ws(
                f"{_inline(outline_el.find('gliederungsbez'))} {_inline(outline_el.find('gliederungstitel'))}"
            )
            continue
        enbez = _ws(meta.findtext("enbez") or "")
        if not enbez or enbez.startswith(SKIPPED_NORMS):
            continue
        kind, eid, label = norm_identity(enbez)
        heading = _inline(meta.find("titel")) or None
        unit = _norm_unit(kind, label, eid, norm.find("textdaten/text/Content"))
        note = _note(norm)
        # Repealed: "(weggefallen)" as heading or text, or no text and an editorial note saying so
        # (e.g. HGB "§§ 153 bis 160": "Weggefallen aufgrund Neuregelung ...").
        repealed = (
            heading in WEGGEFALLEN
            or unit.text in WEGGEFALLEN
            or (not unit.text and "weggefallen" in (note or "").lower())
        )
        context = " – ".join(outline[k] for k in sorted(outline)) or None
        add(unit, None, [], kind, None if heading in WEGGEFALLEN else heading, note, context, repealed)
    if not provisions:
        raise GiiParseError("no provisions found")

    events = [ParsedEvent("version_change", build, None, "Build date of the XML file", "dokumente/@builddate")]
    if enacted:
        description = f"Ausfertigung ({reference})" if reference else "Ausfertigung"
        events.insert(0, ParsedEvent("enactment", enacted, None, description, "norm[1]/metadaten/ausfertigung-datum"))
    ext: dict[str, object] = {"jurabk": jurabk, "doknr": root.get("doknr") or "", "standangaben": stand}
    if amtabk:
        ext["amtabk"] = amtabk
    if reference:
        ext["fundstelle"] = reference
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=_instrument_type(jurabk, title),
            jurisdiction_id="DE",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/deu@{build.isoformat()}",
            work_title=title,
            title=title,
            short_title=amtabk or jurabk,
            number=None,
            work_date=enacted,
            language="de",
            authoritative=False,  # a consolidation, not the promulgated (authentic) text
            text_origin="official_revised",
            consolidation_kind="current_consolidation",
            version_date=build,
            currency_date=build,
            extent=["DE"],
            ext_module="de",
            ext=ext,
            outstanding_changes=outstanding,
            events=events,
            provisions=provisions,
            validity_basis="attested_at_edition",
        )
    ]
