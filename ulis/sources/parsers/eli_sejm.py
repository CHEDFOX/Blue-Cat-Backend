"""Parser for Polish law from the Sejm's ELI API (the Dziennik Ustaw).

The connector (:mod:`ulis.sources.connectors.eli_sejm`) delivers JSON envelopes. Two kinds are parsed:

* ``consolidated_text``: an announcement (obwieszczenie) of a *tekst jednolity* of an act, with its API
  record and its HTML. The whole act as amended is the annex of the announcement (``section#part_2``);
  the announcement's own text (``part_1``: the list of amending acts) is not ingested. The record's
  ``legalStatusDate`` ("stan prawny") is the date the text is up to date to. The text is therefore a
  whole-act snapshot (``validity_basis = "revision_snapshot"``, see ``ulis.ingestion.snapshots``): every
  provision is dated from the legal-status date, an unchanged wording continues the version recorded from
  the previous text, and a provision absent from a later text ends on that text's date. The source dates no
  individual provision, so a provision may have applied long before the date shown (stated on every result).
* ``change_report``: the API record of the act itself. Its amending acts (``Akty zmieniające``, with their
  entry-into-force dates) and consolidated texts (``Inf. o tekście jednolitym``) later than the newest text
  ingested are attached to that text as outstanding changes, without text; every amending act with a date
  is also a lifecycle event.

How a text is read:

* structural divisions (Księga, Część, Tytuł, Dział, Rozdział, Oddział) are headings
  (``context_heading``), not provisions. Provisions are articles (``art-415``; ``art-22_1`` is art. 22¹,
  ``art-18_3a`` art. 18³ᵃ), their paragraphs (§: ``-par1``), subsections (ust.: ``-ust1``), points (pkt:
  ``-pkt2``, ``-pkt2a``) and letters (lit.: ``-lita``). Numbers come from the HTML ``data-id`` and are checked
  against the printed label;
* **text is exact source text** (whitespace normalised, superscripts written with Unicode superscripts).
  The publisher's footnotes (``gloss-link`` anchors: amendments, Constitutional Tribunal judgments,
  transposed directives) go to ``note``, never to ``text``. The publication addresses printed in the text
  itself ("(Dz. U. z 2023 r. poz. 775)", wrapped by the page in a span of class ``xHidden`` that its style
  sheet does not hide) are part of the text;
* a unit whose whole text is "(uchylony)", "(pominięty)" and the like is repealed (empty text, the
  source's words in ``note``); an article range such as "Art. 109-157." is one provision with the range as
  its number;
* the source's HTML sometimes prints a paragraph without its number ("§ " in art. 263 k.c.); the number is
  then taken from the unit's own structural bookmark (``_a263_p1``) and the note says so;
* a quoted passage (``cite-box``, the oath in art. 35 of the data protection act) is text; the footnotes on the
  act's title (transposed directives) are kept in the act's ``ext.notes``; "Preambuła (uchylona)" is a repealed
  ``preamble`` provision; a division that holds only a statement such as "(zawierający art. 295¹ i art. 295² -
  uchylony)" is a repealed ``division`` provision, and one the source nests inside an article (Rozdział II in
  art. 283 § 2 pkt 9 k.p.) is hoisted out of it.

Not ingested: the announcement's text, footnotes attached to the headings of divisions, links to other acts
(cross-references stay plain text), and any structure the parser does not know (it stops with an error, so a
change in the publisher's markup is noticed, not guessed at). PDF is never read.

The consolidated text is an official announcement published in the Dziennik Ustaw (art. 16 of the Act of
20 July 2000 on the publication of normative acts); the authentic text of the law is the act and its
amending acts as published there. Text origin is therefore ``official_consolidation`` and never
authoritative.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Any

from ulis.ingestion.snapshots import CHANGE_REPORT, SNAPSHOT_BASIS
from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision

PARSER_ID = "eli_sejm_bundle"
PARSER_VERSION = "1.0.0"
JURISDICTION = "PL"
BUNDLE_SCHEMA = "ulis-pl-eli-bundle/1"
ELI = "https://eli.gov.pl/eli"
API = "https://api.sejm.gov.pl/eli"

# Short names and the citation form of the acts ULIS ingests (the abbreviation the Polish legal community
# uses after the pinpoint; an act without one is cited with its name in the genitive, "ustawy o ...").
INSTRUMENTS: dict[str, tuple[str, str]] = {
    "DU/1964/93": ("Kodeks cywilny", "k.c."),
    "DU/1997/553": ("Kodeks karny", "k.k."),
    "DU/2018/1000": ("ustawa o ochronie danych osobowych", "ustawy o ochronie danych osobowych"),
    "DU/1974/141": ("Kodeks pracy", "k.p."),
    "DU/1960/168": ("Kodeks postępowania administracyjnego", "k.p.a."),
    "DU/1964/59": ("Kodeks rodzinny i opiekuńczy", "k.r.o."),
}


class EliParseError(ValueError):
    pass


def generic_cite_as(title: str) -> str | None:
    """The long form of an act without a customary abbreviation: "ustawy z dnia 10 maja 2018 r. o ochronie ..." from the
    title "Ustawa z dnia 10 maja 2018 r. o ochronie ..." (the first word in the genitive, the rest as printed)."""
    m = re.match(r"^Ustawa(\s+z dnia\s+\d{1,2}\s+\w+\s+\d{4}\s*r\..+)$", title.strip())
    return "ustawy " + re.sub(r"\s+", " ", m.group(1)).strip().rstrip(".") if m else None


# --------------------------------------------------------------------------- tiny tolerant DOM

VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "wbr"})


class Node:
    __slots__ = ("attrs", "children", "parent", "tag")

    def __init__(self, tag: str, attrs: dict[str, str], parent: Node | None):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[Node | str] = []

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def elements(self) -> list[Node]:
        return [c for c in self.children if isinstance(c, Node)]

    def find_all(self, pred) -> list[Node]:  # type: ignore[no-untyped-def]
        out: list[Node] = []
        stack: list[Node] = [self]
        while stack:
            node = stack.pop()
            if node is not self and pred(node):
                out.append(node)
            stack.extend(reversed(node.elements()))
        return out


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: v or "" for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.cur.children.append(Node(tag, {k: v or "" for k, v in attrs}, self.cur))

    def handle_endtag(self, tag: str) -> None:
        node = self.cur
        while node is not self.root and node.tag != tag:
            assert node.parent is not None
            node = node.parent
        if node is not self.root:
            assert node.parent is not None
            self.cur = node.parent

    def handle_data(self, data: str) -> None:
        self.cur.children.append(data)


def parse_html(text: str) -> Node:
    builder = _Builder()
    builder.feed(text)
    builder.close()
    return builder.root


# --------------------------------------------------------------------------- text

SUPER_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
SUPER_LETTERS = {
    **dict(zip("abcdefghijklmnoprstuvwxyz", "ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ", strict=True)),
}
UNSUPER = {v: k for k, v in SUPER_LETTERS.items()} | dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789", strict=True))
GLOSS_MARK = re.compile(r"^\s*(\d+)\)\s*$")


def superscript(text: str) -> str:
    """``3a`` -> ``³ᵃ``; text with a character that has no superscript form is kept as it is."""
    out: list[str] = []
    for ch in text:
        if ch.isascii() and ch.isdigit():
            out.append(ch.translate(SUPER_DIGITS))
        elif ch in SUPER_LETTERS:
            out.append(SUPER_LETTERS[ch])
        else:
            return text
    return "".join(out)


def unsuperscript(text: str) -> str:
    return "".join(UNSUPER.get(ch, ch) for ch in text)


def ws(text: str) -> str:
    return re.sub(r"[\s\xa0]+", " ", text).strip()


@dataclass
class Rendered:
    text: str
    notes: list[str] = field(default_factory=list)


def render(node: Node) -> Rendered:
    """The text of an element: ``<br>`` and paragraphs become line breaks, ``<sup>`` a superscript; the
    publisher's footnotes are returned separately as notes."""
    parts: list[str] = []
    notes: list[str] = []

    def walk(el: Node) -> None:
        for child in el.children:
            if isinstance(child, str):
                parts.append(re.sub(r"[\s\xa0]+", " ", child))
                continue
            if child.tag in ("script", "style"):
                continue
            if "gloss-link" in child.classes:
                mark = ws(" ".join(render(s).text for s in child.find_all(lambda n: n.tag == "sup")))
                inner = child.find_all(lambda n: "pro-gloss-inner" in n.classes)
                body = ws(render(inner[0]).text) if inner else ""
                if body:
                    notes.append(f"{mark} {body}".strip())
                continue
            if child.tag == "br":
                parts.append("\n")
            elif child.tag == "sup":
                parts.append(superscript(ws(render(child).text)))
            elif child.tag in ("p", "div", "li", "tr"):
                parts.append("\n")
                walk(child)
                parts.append("\n")
            elif child.tag in ("td", "th"):
                if child.find_all(lambda n: n.tag in ("tr", "table")):
                    walk(child)  # a layout cell around a table
                else:
                    cell = render(child)
                    notes.extend(cell.notes)
                    parts.append(" ".join(cell.text.split("\n")) + " | ")
            else:
                walk(child)

    walk(node)
    lines = [ws(x) for x in "".join(parts).split("\n")]
    # Table cells end with " | "; empty cells and the bars at the ends of a row are layout, not text.
    lines = [re.sub(r"^(?:\|\s*)+|(?:\s*\|)+$", "", re.sub(r"(?:\s*\|){2,}", " |", line)).strip() for line in lines]
    return Rendered("\n".join(line for line in lines if line), notes)


# --------------------------------------------------------------------------- units

KIND_OF = {
    "arti": "article",
    "para": "paragraph",
    "pass": "subsection",
    "pint": "point",
    "lett": "letter",
}
LABEL_OF = {"article": "art", "paragraph": "par", "subsection": "ust", "point": "pkt", "letter": "lit"}
CONTAINERS = {"book", "part", "titl", "bran", "chpt", "schp"}
# A unit whose whole text says that it is repealed or left out: "(uchylony)", "(pominięty);", "(utracił moc)".
REPEALED_TEXT = re.compile(
    r"^\(\s*(?:uchylon\w+|pominięt\w+|skreślon\w+|utracił\w*\s+moc|utraciły\s+moc)\s*\)\s*[.;,]*$", re.IGNORECASE
)
REPEALED_NOTE = re.compile(r"\(.*(?:uchylon|pominięt|skreślon|utracił).*\)\s*$", re.IGNORECASE | re.DOTALL)
PREAMBLE_REPEALED = re.compile(r"^Preambuła\s*\(\s*uchylon\w+\s*\)\s*$", re.IGNORECASE)
BOOKMARK_PARAGRAPH = re.compile(r"_a[0-9a-z_]+_p(\d+)$")


@dataclass
class Unit:
    kind: str  # article, paragraph, subsection, point, letter, or a container kind
    key: str
    label: str  # as printed: "§ 1.", "1)", "a)"; empty for articles and containers
    content: list[str | Unit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    heading: str | None = None  # containers
    # Divisions the source nests inside this unit (art. 283 § 2 pkt 9 k.p. holds "Rozdział II"); they are hoisted.
    stray: list[Node] = field(default_factory=list)

    def children(self) -> list[Unit]:
        return [c for c in self.content if isinstance(c, Unit)]

    def own_text(self) -> str:
        return "\n".join(c for c in self.content if isinstance(c, str) and c)

    def text(self) -> str:
        lines = [c if isinstance(c, str) else f"{c.label} {c.text()}".strip() for c in self.content]
        return "\n".join(line for line in lines if line)


def key_of(prefix: str, data_id: str) -> str:
    """``arti_22_1`` -> ``22_1``; ``arti_18_3_a`` -> ``18_3a``; ``pint_2a`` -> ``2a``; ``para_`` -> ``""``."""
    head, _, rest = data_id.partition("_")
    if head != prefix:
        raise EliParseError(f"data-id {data_id!r} does not belong to a {prefix} unit")
    base, *sup = rest.split("_")
    return base + ("_" + "".join(sup) if sup else "")


def _core(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z]", "", unsuperscript(text)).lower()


def _unit_divs(inner: Node) -> list[Node]:
    return [c for c in inner.elements() if c.tag == "div" and "unit" in c.classes]


def render_quotation(box: Node) -> Rendered:
    """A quoted passage (``cite-box``: the oath in art. 35 of the data protection act): the opening mark, the
    quoted text, the closing mark and the trailing punctuation, without the non-breaking spaces the page
    puts around the marks."""
    opening = closing = trailer = ""
    body: list[str] = []
    notes: list[str] = []
    for part in box.elements():
        if "qmark-opn" in part.classes:
            opening = ws(render(part).text)
        elif "qmark-cls" in part.classes:
            closing = ws(render(part).text)
        elif "trailer" in part.classes:
            trailer = ws(render(part).text)
        elif "cite-body" in part.classes:
            for item in part.elements():
                if item.tag != "div" or item.attrs.get("data-template") != "xText":
                    raise EliParseError(f"unsupported content <{item.tag}> in a quotation")
                r = render(item)
                body.append(r.text)
                notes.extend(r.notes)
        else:
            raise EliParseError(f"unsupported part of a quotation: {part.attrs.get('class')!r}")
    return Rendered(f"{opening}{' '.join(body)}{closing}{trailer}", notes)


def build_unit(div: Node) -> Unit:
    classes = div.classes
    kind_code = next((c[5:] for c in classes if c.startswith("unit_")), "")
    if kind_code not in KIND_OF and kind_code not in CONTAINERS:
        raise EliParseError(f"unknown unit kind {kind_code!r} (id {div.attrs.get('id')!r})")
    head = next((c for c in div.elements() if c.tag == "h3"), None)
    inner = next((c for c in div.elements() if c.tag == "div" and "unit-inner" in c.classes), None)
    if head is None or inner is None:
        raise EliParseError(f"unit {div.attrs.get('id')!r} has no heading or body")
    h = render(head)
    if kind_code in CONTAINERS:
        unit = Unit(kind_code, "", "", heading=" - ".join(h.text.split("\n")) or None)
    else:
        kind = KIND_OF[kind_code]
        data_id = div.attrs.get("data-id", "")
        key = key_of(kind_code, data_id)
        label = h.text.replace("\n", " ")
        if not key and kind == "paragraph":
            m = BOOKMARK_PARAGRAPH.search(div.attrs.get("data-bookmark", ""))
            if m is None:
                raise EliParseError(f"paragraph {div.attrs.get('id')!r} has no number")
            key = m.group(1)
            h.notes.append(
                f"The source prints this paragraph without its number; numbered {key} after its structural "
                f"bookmark {div.attrs['data-bookmark']}."
            )
        elif kind == "article":
            printed = re.sub(r"^Art\.\s*", "", label)
            if _core(printed) != _core(key):
                raise EliParseError(f"article label {label!r} does not match its id {data_id!r}")
        elif key and label and _core(label) != _core(key):
            raise EliParseError(f"{kind} label {label!r} does not match its id {data_id!r}")
        if not key:
            raise EliParseError(f"{kind} {div.attrs.get('id')!r} has no number")
        unit = Unit(kind, key, "" if kind == "article" else label)
    unit.notes.extend(h.notes)
    for child in inner.children:
        if isinstance(child, str):
            if ws(child):
                raise EliParseError(f"unexpected text directly in unit {div.attrs.get('id')!r}: {ws(child)[:60]!r}")
            continue
        if child.tag == "div" and "unit" in child.classes:
            if kind_code in CONTAINERS:  # the units inside a division are walked by the caller
                continue
            if "unit_none" in child.classes:
                # An unnumbered line the source labels ("A." / "Banki państwowe", a stray sub-heading inside art. 13b):
                # text of this unit, label and text as printed.
                sub_head = next((c for c in child.elements() if c.tag == "h3"), None)
                sub_inner = next((c for c in child.elements() if c.tag == "div" and "unit-inner" in c.classes), None)
                if sub_head is None or sub_inner is None:
                    raise EliParseError(f"unit {child.attrs.get('id')!r} has no heading or body")
                if _unit_divs(sub_inner):  # it holds units of its own: a division, hoisted like the others
                    unit.stray.append(child)
                    continue
                r = render(sub_inner)
                line = f"{render(sub_head).text} {r.text}".strip()
                unit.content.append(line)
                unit.notes.extend(r.notes)
                continue
            if any(c.startswith("unit_") and c[5:] in CONTAINERS for c in child.classes):
                unit.stray.append(child)
                continue
            member = build_unit(child)
            unit.content.append(member)
            unit.stray.extend(member.stray)
        elif child.tag == "div" and child.attrs.get("data-template") == "xText":
            r = render(child)
            if r.text:
                unit.content.append(r.text)
            unit.notes.extend(r.notes)
        elif child.tag == "div" and "cite-box" in child.classes:
            r = render_quotation(child)
            unit.content.append(r.text)
            unit.notes.extend(r.notes)
        elif child.tag == "ul" and child.attrs.get("data-template") == "xEnum":
            # An unnumbered list ("- text" items, "tiret"): each item is a line of the unit's own text.
            for item in child.elements():
                r = render(item)
                lines = r.text.split("\n")
                if len(lines) > 1 and lines[0] in ("-", "\u2013", "\u2014"):
                    lines = [f"{lines[0]} {lines[1]}", *lines[2:]]
                unit.content.extend(line for line in lines if line)
                unit.notes.extend(r.notes)
        elif child.tag in ("tr", "table"):
            # A table the source places in the unit: its rows as lines, cells separated by " | ".
            r = render(child)
            if r.text:
                unit.content.append(r.text)
            unit.notes.extend(r.notes)
        elif child.tag == "div" and "eqtn" in child.classes:
            # A formula: the TeX the source gives (\( ... \)), kept as written.
            r = render(child)
            if r.text:
                unit.content.append(r.text)
        else:
            raise EliParseError(
                f"unexpected <{child.tag} class={child.attrs.get('class')!r}> in unit {div.attrs.get('id')!r}"
            )
    return unit


def note_text(notes: list[str]) -> str | None:
    seen: list[str] = []
    for n in notes:
        if n and n not in seen:
            seen.append(n)
    return " ".join(seen) or None


# --------------------------------------------------------------------------- the document

MONTHS = {
    "stycznia": 1,
    "lutego": 2,
    "marca": 3,
    "kwietnia": 4,
    "maja": 5,
    "czerwca": 6,
    "lipca": 7,
    "sierpnia": 8,
    "września": 9,
    "października": 10,
    "listopada": 11,
    "grudnia": 12,
}


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def work_uri_for(act_id: str) -> str:
    """``DU/1964/93`` -> ``/pl/du/1964/93``."""
    m = re.fullmatch(r"DU/((?:19|20)\d\d)/([1-9]\d{0,4})", act_id)
    if m is None:
        raise EliParseError(f"not a Dziennik Ustaw address: {act_id!r}")
    return f"/pl/du/{m.group(1)}/{m.group(2)}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map an ELI address of an act in the Dziennik Ustaw to (ULIS work URI, eId or None).

    ``https://eli.gov.pl/eli/DU/1964/93/ogl`` and ``https://api.sejm.gov.pl/eli/acts/DU/1964/93/text.html``
    -> ``/pl/du/1964/93``; ``.../text.html/artykul=415`` -> eId ``art-415``.
    """
    m = re.match(
        rf"^(?:{re.escape(ELI)}|{re.escape(API)}/acts)/DU/((?:19|20)\d\d)/([1-9]\d{{0,4}})(?:/[^?#]*?)?(?:/artykul(?:=|%3D)([0-9a-z]+))?/?$",
        uri.strip(),
    )
    if m is None:
        return None
    return f"/pl/du/{m.group(1)}/{m.group(2)}", (f"art-{m.group(3)}" if m.group(3) else None)


def _address(act_id: str) -> str:
    year, pos = act_id.split("/")[1:]
    return f"Dz.U. {year} poz. {pos}"


def _bundle(content: bytes) -> dict[str, Any]:
    try:
        bundle = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise EliParseError(f"not an ELI bundle (JSON): {exc}") from exc
    if not isinstance(bundle, dict) or bundle.get("schema") != BUNDLE_SCHEMA:
        raise EliParseError(
            f"not an ELI bundle (schema {bundle.get('schema') if isinstance(bundle, dict) else None!r})"
        )
    return bundle


def _annex(root: Node) -> Node:
    """The section holding the consolidated text: the one headed "Załącznik - Tekst jednolity ..."."""
    found = []
    for section in root.find_all(lambda n: n.tag == "section" and n.attrs.get("id", "").startswith("part_")):
        for h2 in section.find_all(lambda n: n.tag == "h2" and "part" in n.classes):
            heading = ws(render(h2).text)
            if heading.lower().startswith("załącznik") and "tekst jednolity" in heading.lower():
                found.append(section)
    if len(found) != 1:
        raise EliParseError(f"expected exactly one annex with the consolidated text, found {len(found)}")
    return found[0]


def _block(annex: Node) -> Node:
    block = annex.find_all(lambda n: n.tag == "div" and "block" in n.classes)
    if len(block) != 1:
        raise EliParseError(f"the annex should have one text block, found {len(block)}")
    return block[0]


def parse_consolidated_text(bundle: dict[str, Any]) -> ParsedDocument:
    record = bundle["tj_record"]
    act = bundle["act"]
    act_id = bundle["act_id"]
    tj_id = bundle["tj_id"]
    status_date = _date(record.get("legalStatusDate"))
    if status_date is None:
        raise EliParseError(f"{tj_id}: no legal-status date")
    short, cite_as = INSTRUMENTS.get(act_id, (act["title"].rstrip("."), generic_cite_as(act["title"])))
    root = parse_html(bundle["text_html"])
    annex = _annex(root)

    headings: list[tuple[int, str]] = []
    rank = {"book": 1, "part": 2, "titl": 3, "bran": 4, "chpt": 5, "schp": 6}
    provisions: list[ParsedProvision] = []
    used: dict[str, int] = {}
    work = work_uri_for(act_id)

    def unique(eid: str) -> str:
        n = used.get(eid, 0) + 1
        used[eid] = n
        return eid if n == 1 else f"{eid}-{n}"

    def add(u: Unit, parent_eid: str | None, path: list[str], eid_prefix: str, context: str | None) -> None:
        eid = unique(eid_prefix)
        children = u.children()
        own = u.own_text()
        text = u.text()
        notes = list(u.notes)
        if not children and not text and u.kind != "article":
            raise EliParseError(f"{u.kind} {u.key!r} of {eid} has no text")
        # An article with neither text nor members (art. 266-280 k.p. "not included", footnote only) is left out.
        repealed = not children and (not text or bool(REPEALED_TEXT.match(text)))
        if repealed:
            shown = text
            text = ""
            if shown:
                notes.insert(0, shown)
            notes.append(
                "Repeal date not stated by the source: the consolidated text with legal status "
                f"{status_date.isoformat()} shows the provision as repealed or left out."
            )
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=u.kind,
                num_path=path,
                top_kind="article",
                heading=None,
                text=text,
                parent_eid=parent_eid,
                sort_order=len(provisions),
                status="repealed" if repealed else "in_force",
                valid_from=status_date,
                valid_to=None,
                extent=[JURISDICTION],
                note=note_text(notes),
                refs=[],
                is_leaf=not children,
                has_own_content=bool(own) and bool(children),
                context_heading=context,
            )
        )
        suffix = {"paragraph": "par", "subsection": "ust", "point": "pkt", "letter": "lit"}
        for child in children:
            if child.kind == "article" or child.kind in CONTAINERS:
                raise EliParseError(f"{u.kind} {u.key!r} contains a {child.kind}")
            tag = suffix[child.kind]
            add(child, eid, [*path, f"{LABEL_OF[child.kind]}:{child.key}"], f"{eid}-{tag}{child.key}", context)

    def walk(divs: list[Node]) -> None:
        for div in divs:
            if "unit_none" in div.classes:
                # A division the source labels but does not name ("A." / "Plany naprawy" inside a chapter): the lowest
                # heading level; the units inside it are walked, and the heading ends with it.
                sub_head = next((c for c in div.elements() if c.tag == "h3"), None)
                sub_inner = next((c for c in div.elements() if c.tag == "div" and "unit-inner" in c.classes), None)
                if sub_head is None or sub_inner is None:
                    raise EliParseError(f"unit {div.attrs.get('id')!r} has no heading or body")
                subtitle = " ".join(
                    render(c).text
                    for c in sub_inner.elements()
                    if c.tag == "div" and "unit" not in c.classes and c.attrs.get("data-template") == "xText"
                )
                while headings and headings[-1][0] >= 9:
                    headings.pop()
                headings.append((9, f"{render(sub_head).text} {subtitle}".strip()))
                walk(_unit_divs(sub_inner))
                headings.pop()
                continue
            u = build_unit(div)
            if u.kind in CONTAINERS:
                while headings and headings[-1][0] >= rank[u.kind]:
                    headings.pop()
                if u.heading:
                    headings.append((rank[u.kind], u.heading))
                own = u.own_text()
                inner = next(c for c in div.elements() if c.tag == "div" and "unit-inner" in c.classes)
                if own and not REPEALED_NOTE.match(own):
                    # A second line of the heading ("Dział III - Odpowiedzialność dyscyplinarna." / "Zawieszenie
                    # oraz rozwiązanie stosunku pracy z ekspertem"): part of the heading, not of a provision.
                    if len(own) > 200:
                        raise EliParseError(f"division {u.heading!r} has text of its own: {own[:60]!r}")
                    had_heading = bool(u.heading)
                    u.heading = " - ".join(x for x in (u.heading, *own.split("\n")) if x)
                    if had_heading:
                        headings[-1] = (rank[u.kind], u.heading)
                    else:
                        headings.append((rank[u.kind], u.heading))
                    own = ""
                if own:
                    # A division with a statement of its own such as "(zawierający art. 295¹ i art. 295² - uchylony)".
                    # The HTML id holds the division's whole path (``bran_DWUNASTY-chpt_III``), which keeps it stable.
                    eid = unique("div-" + re.sub(r"[^0-9A-Za-zÀ-ſ_-]+", "-", div.attrs.get("id", "")))
                    provisions.append(
                        ParsedProvision(
                            eid=eid,
                            kind="division",
                            num_path=[f"div:{(u.heading or eid)}"],
                            top_kind="division",
                            heading=u.heading,
                            text="",
                            parent_eid=None,
                            sort_order=len(provisions),
                            status="repealed",
                            valid_from=status_date,
                            valid_to=None,
                            extent=[JURISDICTION],
                            note=note_text([own, *u.notes]),
                            refs=[],
                            is_leaf=True,
                            has_own_content=False,
                            context_heading=" › ".join(h for _, h in headings[:-1]) or None,
                        )
                    )
                walk(_unit_divs(inner))
            elif u.kind == "article":
                context = " › ".join(h for _, h in headings) or None
                add(u, None, [f"art:{u.key}"], f"art-{u.key}", context)
                walk(u.stray)
            else:
                raise EliParseError(f"unexpected top-level {u.kind} {u.key!r}")

    block = _block(annex)
    # Footnotes on the act's title (transposed directives, the regulation the act serves) are the act's own notes.
    act_notes: list[str] = []
    for title_node in annex.find_all(lambda n: n.tag == "h2" and "part" in n.classes):
        act_notes.extend(render(title_node).notes)
    for item in block.elements():
        if "unit" in item.classes:
            continue
        if item.tag != "div":
            raise EliParseError(f"unexpected <{item.tag}> in the text block of {tj_id}")
        r = render(item)
        act_notes.extend(r.notes)
        if r.text:  # "Preambuła (uchylona)"
            gone = bool(PREAMBLE_REPEALED.match(r.text))
            provisions.append(
                ParsedProvision(
                    eid="preamble",
                    kind="preamble",
                    num_path=["preamble"],
                    top_kind="preamble",
                    heading=None,
                    text="" if gone else r.text,
                    parent_eid=None,
                    sort_order=len(provisions),
                    status="repealed" if gone else "in_force",
                    valid_from=status_date,
                    valid_to=None,
                    extent=[JURISDICTION],
                    note=note_text(
                        [r.text if gone else "", *r.notes]
                        + (
                            [
                                "Repeal date not stated by the source: the consolidated text with legal status "
                                f"{status_date.isoformat()} shows the preamble as repealed."
                            ]
                            if gone
                            else []
                        )
                    ),
                    refs=[],
                    is_leaf=True,
                    has_own_content=False,
                    context_heading=None,
                )
            )
    walk(_unit_divs(block))
    if not any(p.kind == "article" for p in provisions):
        raise EliParseError(f"{tj_id}: no articles found")

    title = act["title"]
    work_date = _date(act.get("announcementDate"))
    in_force = _date(act.get("entryIntoForce"))
    published = _date(record.get("promulgation"))
    events = [
        *([ParsedEvent("enactment", work_date, None, None, "act/announcementDate")] if work_date else []),
        *([ParsedEvent("entry_into_force", in_force, None, None, "act/entryIntoForce")] if in_force else []),
        ParsedEvent(
            "version_change",
            status_date,
            None,
            f"Tekst jednolity {_address(tj_id)}: stan prawny na dzień {status_date.isoformat()}",
            "record/legalStatusDate",
        ),
        *(
            [
                ParsedEvent(
                    "consolidation_published",
                    published,
                    None,
                    f"Tekst jednolity {_address(tj_id)} ogłoszony w Dzienniku Ustaw",
                    "record/promulgation",
                )
            ]
            if published
            else []
        ),
    ]
    ext: dict[str, Any] = {
        "dz_u": act["displayAddress"],
        "eli": f"{ELI}/{act_id}/ogl",
        "short_name": short,
        **({"cite_as": cite_as} if cite_as else {}),
        **({"notes": act_notes} if act_notes else {}),
    }
    return ParsedDocument(
        doc_kind="legislation",
        instrument_type="act",
        jurisdiction_id=JURISDICTION,
        work_uri=work,
        expression_uri=f"{work}/pol@{status_date.isoformat()}",
        work_title=title,
        title=f"{title.rstrip('.')} (tekst jednolity: {record.get('displayAddress') or _address(tj_id)})",
        short_title=short,
        number=act["displayAddress"],
        work_date=work_date,
        language="pl",
        # The announcement is official, but the authentic text of the law is the act and its amendments.
        authoritative=False,
        text_origin="official_consolidation",
        consolidation_kind="point_in_time",
        version_date=status_date,
        currency_date=status_date,
        extent=[JURISDICTION],
        ext_module="pl",
        ext=ext,
        outstanding_changes=[],
        events=events,
        provisions=provisions,
        validity_basis=SNAPSHOT_BASIS,
    )


def _ref_ids(record: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [r for r in (record.get("references") or {}).get(name, []) if isinstance(r, dict) and r.get("id")]


def _sort_key(act_id: str) -> tuple[int, int]:
    year, pos = act_id.split("/")[1:]
    return int(year), int(pos)


def parse_change_report(bundle: dict[str, Any]) -> ParsedDocument:
    record = bundle["act_record"]
    act_id = bundle["act_id"]
    attach = bundle["attach_to"]
    status_date = _date(attach["legal_status_date"])
    if status_date is None or record.get("publisher") != "DU":
        raise EliParseError("a change report needs the legal-status date of the text it attaches to")
    tj_id = attach["tj_id"]
    work = work_uri_for(act_id)
    outstanding: list[dict[str, Any]] = []
    events: list[ParsedEvent] = []
    for ref in sorted(_ref_ids(record, "Akty zmieniające"), key=lambda r: _sort_key(r["id"])):
        amend_id, when = ref["id"], _date(ref.get("date"))
        address = _address(amend_id)
        url = f"{ELI}/{amend_id}/ogl"
        if when is not None:
            events.append(ParsedEvent("amendment", when, url, address, f"references/Akty zmieniające/{amend_id}"))
        if when is not None and when <= status_date:
            continue  # in force on the text's legal-status date: part of that text
        if when is None and _sort_key(amend_id) < _sort_key(tj_id):
            continue  # published before the text and no date to say otherwise
        outstanding.append(
            {
                "affects_eid": None,
                "affected_label": None,
                "by_uri": url,
                "by_title": address,
                "description": (
                    f"Amending act {address}"
                    + (f", in force from {when.isoformat()}" if when else " (entry into force date not stated)")
                    + f", is later than the legal status of this text ({status_date.isoformat()}); "
                    "its effect on this provision is not applied here and the amending act is not ingested."
                ),
                **({"effective_date": when.isoformat()} if when else {}),
            }
        )
    for ref in sorted(_ref_ids(record, "Inf. o tekście jednolitym"), key=lambda r: _sort_key(r["id"])):
        if _sort_key(ref["id"]) <= _sort_key(tj_id):
            continue
        address = _address(ref["id"])
        outstanding.append(
            {
                "affects_eid": None,
                "affected_label": None,
                "by_uri": f"{ELI}/{ref['id']}/tj",
                "by_title": f"Tekst jednolity {address}",
                "description": (
                    f"A later consolidated text of this act was announced in the Dziennik Ustaw ({address}); "
                    "it is not ingested."
                ),
            }
        )
    short, cite_as = INSTRUMENTS.get(
        act_id, (str(record.get("title", act_id)).rstrip("."), generic_cite_as(str(record.get("title", ""))))
    )
    return ParsedDocument(
        doc_kind="legislation",
        instrument_type="act",
        jurisdiction_id=JURISDICTION,
        work_uri=work,
        expression_uri=f"{work}/pol@{status_date.isoformat()}",
        work_title=str(record.get("title", act_id)),
        title=str(record.get("title", act_id)),
        short_title=short,
        number=record.get("displayAddress"),
        work_date=_date(record.get("announcementDate")),
        language="pl",
        authoritative=False,
        text_origin="official_consolidation",
        consolidation_kind=CHANGE_REPORT,
        version_date=status_date,
        # The report does not extend what the consolidated text attests: it is up to date to its legal status.
        currency_date=status_date,
        extent=[JURISDICTION],
        ext_module="pl",
        ext={
            "dz_u": str(record.get("displayAddress")),
            "eli": f"{ELI}/{act_id}/ogl",
            "short_name": short,
            **({"cite_as": cite_as} if cite_as else {}),
        },
        outstanding_changes=outstanding,
        events=events,
        provisions=[],
        validity_basis=SNAPSHOT_BASIS,
    )


def parse_eli_sejm(content: bytes) -> list[ParsedDocument]:
    bundle = _bundle(content)
    kind = bundle.get("kind")
    if kind == "consolidated_text":
        return [parse_consolidated_text(bundle)]
    if kind == "change_report":
        return [parse_change_report(bundle)]
    raise EliParseError(f"unsupported bundle kind {kind!r}")
