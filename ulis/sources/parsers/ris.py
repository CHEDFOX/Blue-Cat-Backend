"""Parsers for documents of the Austrian Rechtsinformationssystem des Bundes (RIS).

RIS serves every document as a ``risdok`` XML file (namespace ``http://www.bka.gv.at``). The
file holds the text and, as labelled ``erltext`` paragraphs (attribute ``ct``), the document's
metadata, so a single file is self-describing.

Bundesrecht konsolidiert (federal law, consolidated):

* one document per paragraph (§), article (Art.) or annex (Anl.) **and per version**
  ("Fassung"): ``ikra`` is the Inkrafttretensdatum, ``akra`` the Außerkrafttretensdatum, the
  last day the version applied (inclusive; ULIS stores the exclusive day after). A version
  whose end precedes its start never applied and is not recorded;
* the text is a sequence of ``absatz`` elements (an Absatz starts with "(1)", the first one
  with the § / Art. symbol in ``gldsym``) and ``liste`` elements (Ziffern "1.", litera "a)",
  sublitera "aa)", closing words in ``schlussteil``). Subdivisions inherit the period of their
  paragraph: RIS dates versions per paragraph/article only;
* editorial annotations "(Anm.: ...)" and "Beachte" notes are notes, never law text; a
  paragraph whose text is only an annotation that it was repealed is ``repealed``;
* consolidated texts are informational only (the authentic text is the Bundesgesetzblatt):
  ``text_origin = official_revised``, ``authoritative = False``.

Not verified against real data: the ``ct`` name of the Außerkrafttretensdatum field (``akra`` is
assumed; the only document seen so far is a current version without one). Decisions (Judikatur)
are not parsed yet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision

NS = "http://www.bka.gv.at"
PARSER_VERSION = "1.0.0"
SITE = "https://www.ris.bka.gv.at"

# RIS "Typ" of a federal norm -> ULIS instrument type.
INSTRUMENT_TYPES = {"BVG": "constitution", "BG": "act", "V": "regulation"}
TOP_KINDS = {"§": "section", "Art.": "article", "Anl.": "annex"}
EID_PREFIX = {"section": "par", "article": "art", "annex": "anl"}
# Kinds of subdivision, with the num_path tag and eId key the jurisdiction module renders.
SUB_KINDS = {"abs": "subsection", "z": "item", "lit": "letter", "sublit": "subletter", "item": "point"}
# Legacy list elements and the depth they stand for (aufzaehlung carries an explicit @ebene).
LIST_DEPTH = {"ziffernliste": 1, "literaliste": 2, "subliteraliste": 3, "strichliste": 4, "betragliste": 1}
SCHLUSS_DEPTH = {"Abs": 0, "Ziff": 1, "Lit": 2, "e1": 1, "e4": 4, "e5": 5}
SKIP_INLINE = {"kzinhalt", "fzinhalt", "feld", "fnz", "fnzeichen", "fntext", "binary", "layoutdaten"}
_ABS = re.compile(r"^\((\d+[a-z]*)\)\s*")
_ANM = re.compile(r"^\(?\s*Anm\.?\s*:", re.I)
_REPEAL = re.compile(r"aufgehoben|entf[äa]ll|außer Kraft|ausser Kraft", re.I)
_LOCATOR = re.compile(r"^(§|Art\.|Anl\.)\s*(\d+)([a-z]*)$")
_DMY = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")


class RisParseError(ValueError):
    pass


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _raw(el: Element) -> str:
    parts = [el.text or ""]
    for child in el:
        name = _local(child.tag)
        if name not in SKIP_INLINE:
            parts.append(" " if name in ("tab", "br") else _raw(child))
        parts.append(child.tail or "")
    return "".join(parts)


def inline_text(el: Element) -> str:
    return _ws(_raw(el))


def ris_date(value: str | None) -> date | None:
    """``01.01.2020`` (RIS display form) or ``2020-01-01`` -> date; anything else -> None."""
    value = (value or "").strip()
    m = _DMY.match(value)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def _root(content: bytes) -> Element:
    try:
        root = fromstring(content, forbid_dtd=True)
    except Exception as exc:
        raise RisParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if root.tag != _q("risdok"):
        raise RisParseError(f"unsupported root element {root.tag!r} (expected a RIS risdok document)")
    return root


@dataclass
class _Section:
    """One labelled part of a RIS document: a metadata field or the text."""

    label: str
    elements: list[Element] = field(default_factory=list)


def _sections(root: Element) -> tuple[dict[str, str], list[_Section]]:
    """Split the document into ``ct``-keyed metadata values and titled sections (in order).

    A metadata field is an ``ueberschrift typ="titel"`` followed by ``absatz`` elements with a
    ``ct`` naming the field; text sections are introduced the same way ("Text", "Spruch", ...).
    """
    nutz = root.find(_q("nutzdaten"))
    if nutz is None:
        raise RisParseError("missing nutzdaten")
    meta: dict[str, str] = {}
    sections: list[_Section] = []
    current: _Section | None = None
    for abschnitt in nutz.findall(_q("abschnitt")):
        for el in abschnitt:
            name = _local(el.tag)
            if name in ("kzinhalt", "fzinhalt"):
                continue
            if name == "ueberschrift" and el.get("typ") == "titel":
                current = _Section(inline_text(el))
                sections.append(current)
                continue
            ct = el.get("ct")
            if name == "absatz" and el.get("typ") == "erltext" and ct and ct != "text":
                value = inline_text(el)
                meta[ct] = f"{meta[ct]} {value}".strip() if ct in meta else value
            if current is not None:
                current.elements.append(el)
    return meta, sections


# ---------------------------------------------------------------------------- Bundesrecht


@dataclass
class _Node:
    tag: str  # abs | z | lit | sublit | item | top
    key: str  # language-neutral number ("3", "a", "aa")
    label: str  # as printed: "(3)", "1.", "a)"
    depth: int
    own: list[str] = field(default_factory=list)
    tail: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)

    def text(self) -> str:
        lines = [*self.own]
        for child in self.children:
            body = child.text()
            lines.append(f"{child.label} {body}".strip() if body else child.label)
        lines.extend(self.tail)
        return "\n".join(line for line in lines if line)


def _symbol_kind(symbol: str) -> tuple[str, str]:
    """``1.`` -> (z, 1); ``a)`` -> (lit, a); ``aa)`` -> (sublit, aa); dashes -> (dash, ""); else (item, symbol)."""
    s = symbol.strip()
    if re.fullmatch(r"\d+[a-z]*\.", s):
        return "z", s[:-1]
    if re.fullmatch(r"[a-z]\)", s):
        return "lit", s[0]
    if re.fullmatch(r"([a-z])\1\)", s):
        return "sublit", s[:-1]
    if s in ("-", "–", "—", "•", ""):
        return "dash", ""
    return "item", s


def _list_items(liste: Element) -> list[tuple[str, int, Element]]:
    """Flatten a ``liste`` into (kind, depth, element) in document order; kind is listelem/schluss."""
    out: list[tuple[str, int, Element]] = []

    def walk(el: Element, depth: int) -> None:
        for child in el:
            name = _local(child.tag)
            if name == "listelem":
                out.append(("listelem", depth, child))
            elif name == "aufzaehlung" or name in LIST_DEPTH:
                d = int(child.get("ebene") or 0) if name == "aufzaehlung" else LIST_DEPTH[name]
                walk(child, d or depth + 1)
            elif name == "schlussteil":
                out.append(("schluss", int(float(child.get("ebene") or 0)), child))
            elif name == "schluss":
                out.append(("schluss", SCHLUSS_DEPTH.get(child.get("typ") or "", 0), child))
            elif name == "erlliste":
                walk(child, depth + 1)

    walk(liste, 0)
    return out


def _table_text(el: Element) -> str:
    rows = []
    for tr in el.iter(_q("tr")):
        cells = [inline_text(td) for td in tr if _local(td.tag) in ("td", "th")]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _build_tree(elements: list[Element], top: _Node) -> tuple[list[str], list[str]]:
    """Build the subdivision tree of one paragraph/article; return (headings, group headings)."""
    headings: list[str] = []
    groups: list[str] = []
    current: _Node = top  # the Absatz (or the paragraph itself) that list items attach to
    stack: list[_Node] = []  # open list items under ``current``

    def target_for(depth: int) -> _Node:
        while stack and stack[-1].depth >= depth:
            stack.pop()
        return stack[-1] if stack else current

    def add_line(node: _Node, text: str, as_tail: bool) -> None:
        if _ANM.match(text):
            node.notes.append(text)
        elif as_tail or node.children:
            node.tail.append(text)
        else:
            node.own.append(text)

    for el in elements:
        name = _local(el.tag)
        if name == "ueberschrift":
            text = inline_text(el)
            if not text:
                continue
            if el.get("typ") in ("para", "art", "anlage"):
                headings.append(text)
            else:
                groups.append(text)
        elif name == "absatz":
            if el.get("typ") in ("kz", "fz"):
                continue
            gld = el.find(_q("gldsym"))
            text = inline_text(el)
            if gld is not None:
                symbol = inline_text(gld)
                text = _ws(text[len(symbol) :]) if text.startswith(symbol) else text
            if not text:
                continue
            m = _ABS.match(text)
            if m:
                node = _Node("abs", m.group(1), f"({m.group(1)})", 0)
                top.children.append(node)
                current, stack = node, []
                rest = text[m.end() :]
                if rest:
                    add_line(node, rest, False)
            else:
                add_line(stack[-1] if stack else current, text, bool(stack))
        elif name == "liste":
            for kind, depth, item in _list_items(el):
                if kind == "schluss":
                    owner = target_for(depth + 1)
                    text = inline_text(item)
                    if text:
                        owner.tail.append(text) if not _ANM.match(text) else owner.notes.append(text)
                    continue
                sym_el = item.find(_q("symbol"))
                symbol = inline_text(sym_el) if sym_el is not None else ""
                text = inline_text(item)
                if symbol and text.startswith(symbol):
                    text = _ws(text[len(symbol) :])
                tag, key = _symbol_kind(symbol)
                parent = target_for(depth)
                if tag == "dash":
                    add_line(parent, f"{symbol} {text}".strip(), False)
                    continue
                node = _Node(tag, key, symbol, depth)
                if text:
                    add_line(node, text, False)
                parent.children.append(node)
                stack.append(node)
        elif name == "table":
            text = _table_text(el)
            if text:
                add_line(stack[-1] if stack else current, text, bool(stack))
        elif name in ("unterschrift", "pre", "textbox", "beschr"):
            text = inline_text(el)
            if text:
                add_line(stack[-1] if stack else current, text, bool(stack))
    return headings, groups


def sort_key(top_kind: str, number: str) -> int:
    """Document order across separately fetched paragraphs: § 20a after § 20, before § 21."""
    m = re.fullmatch(r"(\d+)([a-z]*)", number)
    if not m:
        return 0
    letters = m.group(2)
    suffix = (ord(letters[0]) - 96) if letters else 0
    base = (int(m.group(1)) * 27 + suffix) * 1000
    return base + (50_000_000 if top_kind == "annex" else 0)


def work_uri(instrument_type: str, gesetzesnummer: str) -> str:
    return f"/at/{instrument_type}/{gesetzesnummer}"


def parse_ris_bundesrecht(content: bytes) -> list[ParsedDocument]:
    root = _root(content)
    meta, sections = _sections(root)
    doknr = meta.get("doknr", "")
    if not re.fullmatch(r"NOR\d+", doknr):
        raise RisParseError(f"not a Bundesrecht konsolidiert document (Dokumentnummer {doknr!r})")
    gesnr = meta.get("gesnr", "")
    if not re.fullmatch(r"\d{8}", gesnr):
        raise RisParseError(f"{doknr}: missing or invalid Gesetzesnummer {gesnr!r}")
    ris_type = meta.get("typ", "")
    if ris_type not in INSTRUMENT_TYPES:
        raise RisParseError(f"{doknr}: unsupported norm type {ris_type!r}")
    instrument_type = INSTRUMENT_TYPES[ris_type]
    locator = meta.get("artikel_anlage", "")
    m = _LOCATOR.match(locator)
    if not m:
        raise RisParseError(f"{doknr}: unsupported locator {locator!r} (only plain §, Art. and Anl.)")
    top_kind = TOP_KINDS[m.group(1)]
    number = f"{m.group(2)}{m.group(3)}"
    valid_from = ris_date(meta.get("ikra"))
    if valid_from is None:
        raise RisParseError(f"{doknr}: missing Inkrafttretensdatum")
    last_day = ris_date(meta.get("akra"))
    valid_to = last_day + timedelta(days=1) if last_day else None
    title = meta.get("kurztitel", "")
    if not title:
        raise RisParseError(f"{doknr}: missing Kurztitel")
    abbreviation = meta.get("abkuerzung") or None

    text_section = next((s for s in sections if s.label == "Text"), None)
    if text_section is None:
        raise RisParseError(f"{doknr}: no Text section")
    top = _Node("top", number, locator, -1)
    headings, groups = _build_tree(text_section.elements, top)

    work = work_uri(instrument_type, gesnr)
    top_eid = f"{EID_PREFIX[top_kind]}-{number}"
    provisions: list[ParsedProvision] = []
    base_order = sort_key(top_kind, number)
    never_applied = valid_to is not None and valid_to <= valid_from
    doc_notes = [f"Beachte: {meta['beachte']}"] if meta.get("beachte") else []

    def add(node: _Node, eid: str, path: list[str], parent: str | None) -> None:
        text = node.text()
        repealed = not text and any(_REPEAL.search(n) for n in node.notes)
        notes = node.notes + (doc_notes if parent is None else [])
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=top_kind if parent is None else SUB_KINDS[node.tag],
                num_path=path,
                top_kind=top_kind,
                heading=" ".join(headings) or None if parent is None else None,
                text="" if repealed else text,
                parent_eid=parent,
                sort_order=base_order + len(provisions),
                status="repealed" if repealed else "in_force",
                valid_from=valid_from,
                valid_to=valid_to,
                extent=["AT"],
                note=" | ".join(notes)[:1000] or None,
                refs=[],
                is_leaf=not node.children,
                has_own_content=bool(node.children) and bool(node.own or node.tail),
                context_heading=" ".join(groups) or None,
                record_version=not never_applied,
            )
        )
        used: dict[str, int] = {}
        for child in node.children:
            key = re.sub(r"[^a-z0-9]+", "", child.key.lower()) or "x"
            local = f"{child.tag}-{key}"
            used[local] = used.get(local, 0) + 1
            if used[local] > 1:  # a repeated label (rare drafting slip): number repeats in document order
                local = f"{local}-{used[local]}"
            add(child, f"{eid}-{local}", [*path, f"{child.tag}:{child.key}"], eid)

    add(top, top_eid, [number], None)
    kundmachung = meta.get("kundmachungsorgan")
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=instrument_type,
            jurisdiction_id="AT",
            work_uri=work,
            expression_uri=f"{work}/deu@{valid_from.isoformat()}/{doknr.lower()}",
            work_title=title,
            title=title,
            short_title=abbreviation or title,
            number=gesnr,
            work_date=None,  # the enactment date is not in the paragraph documents
            language="de",
            authoritative=False,
            text_origin="official_revised",
            consolidation_kind="point_in_time",
            version_date=valid_from,
            currency_date=None,  # RIS documents state no currency date (retrieval time is recorded)
            extent=["AT"],
            ext_module="at",
            ext={"gesetzesnummer": gesnr, "ris_type": ris_type},
            events=[
                ParsedEvent(
                    "version_change",
                    valid_from,
                    None,
                    f"{locator} in der Fassung ab {valid_from.isoformat()}"
                    + (f" ({kundmachung})" if kundmachung else ""),
                    f"{doknr}/ikra",
                )
            ],
            provisions=provisions,
            validity_basis="source_periods",
        )
    ]
