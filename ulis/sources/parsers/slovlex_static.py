"""Parser for time versions ("časová verzia") of Slovak acts from the static version of Slov-lex.

Input: one HTML page ``https://static.slov-lex.sk/static/SK/ZZ/<year>/<number>/<YYYYMMDD>.html``, the text of an act
(Zbierka zákonov) as in force from that day until the next amendment, as the Ministry of Justice publishes it. The
page carries its own structured markup: ``<div class="paragraf Skupina " id="paragraf-79">`` with ``odsek``,
``pismeno`` and ``bod`` children, the Constitution's ``ustavnyclanok``, amending acts' ``clanok``, and the
containers ``cast``, ``hlava``, ``diel``, ``oddiel`` and ``skupinaParagrafov``; ``<div class="ucinnost_header"
data-from=... data-to=...>`` states the period the version applies to, and the footnotes are in ``<div
id="poznamky">``.

* Every ``clanok``, ``ustavnyclanok``, ``paragraf``, ``odsek``, ``pismeno`` and ``bod`` is a provision; its ``eid`` is
  the page's own element id (``paragraf-79.odsek-3.pismeno-a``), identical in every version. An ``odsek`` without a
  number is the text of its parent. Containers give headings (``context_heading``) only. An annex (``priloha``) is
  one provision with its text flattened. Quoted text inside ``text2``/``citat`` blocks (amending instructions) stays in
  the text of the provision it belongs to.
* The page is one whole-instrument snapshot: every provision gets the period of the version (``data-from`` to the day
  after ``data-to``) and the document is ``validity_basis = revision_snapshot`` (see ``ulis.ingestion.snapshots``).
* ``<sup>`` becomes superscript digits (as Likumi.lv does) and ``<br>`` a line break; runs of white space, including
  non-breaking spaces, become one space. Footnote markers stay in the text; the footnote itself (``poznamky``) is the
  provision's ``note``. Links to other acts (``../../../../SK/ZZ/2011/351/#paragraf-57.odsek-2``) and within the act
  are ``cites`` references.
* The page says its content is informative ("Obsah zobrazeného právneho predpisu má informatívny charakter"); the
  legally binding text is the promulgated PDF. So the document is an official consolidation, not authoritative.
* Not parsed: the history table's text beyond the amending acts of this version, tables of contents, signatures and
  the promulgated version (``vyhlasene_znenie.html``), which the connector does not fetch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from html.parser import HTMLParser

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParsedRef

PARSER_ID = "slovlex_static_html"
PARSER_VERSION = "1.0.0"
JURISDICTION = "SK"
VOID = {"br", "img", "hr", "input", "meta", "link", "col", "area", "base", "wbr"}
SUPER = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
MONTHS = {
    "januára": 1,
    "februára": 2,
    "marca": 3,
    "apríla": 4,
    "mája": 5,
    "júna": 6,
    "júla": 7,
    "augusta": 8,
    "septembra": 9,
    "októbra": 10,
    "novembra": 11,
    "decembra": 12,
}
# class of a provision element -> (kind, num_path kind)
PROVISIONS = {
    "clanok": ("article", "art"),
    "ustavnyclanok": ("article", "art"),
    "paragraf": ("section", "sec"),
    "odsek": ("subsection", "sub"),
    "pismeno": ("paragraph", "pt"),
    "bod": ("point", "item"),
}
CONTAINERS = ("cast", "hlava", "diel", "oddiel", "pododdiel", "skupinaParagrafov")
INSTRUMENT_TYPES = (
    ("ÚSTAVNÝ ZÁKON", "constitutional_act"),
    ("ÚSTAVA", "constitution"),
    ("ZÁKON", "act"),
    ("NARIADENIE VLÁDY", "regulation"),
    ("VYHLÁŠKA", "regulation"),
    ("NARIADENIE", "regulation"),
    ("OPATRENIE", "regulation"),
)
REPEALED = re.compile(r"^\s*[(\[]?\s*(?:zrušen[ýáé]|vypusten[ýáé]|vynechan[ýáé])\s*[)\]]?\.?\s*$", re.I)
LINK = re.compile(r"(?:^|/)SK/ZZ/([0-9]{4})/([0-9]+)/(?:[0-9]{8}\.html)?(?:#(.+))?$")


class SlovLexParseError(ValueError):
    pass


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    children: list[Node | str] = field(default_factory=list)

    @property
    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()

    def find_all(self, pred):  # type: ignore[no-untyped-def]
        for c in self.children:
            if isinstance(c, Node):
                if pred(c):
                    yield c
                yield from c.find_all(pred)


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack[-1].children.append(Node(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def parse_html(content: bytes) -> Node:
    builder = _Builder()
    builder.feed(content.decode("utf-8"))
    builder.close()
    return builder.root


def _first(node: Node, pred) -> Node | None:  # type: ignore[no-untyped-def]
    return next(node.find_all(pred), None)


def _tokens(node: Node, out: list[str], refs: list[Node], footnotes: list[str], in_sup: bool = False) -> None:
    for c in node.children:
        if isinstance(c, str):
            text = re.sub(r"[ \t\r\n\xa0]+", " ", c)  # source line wrapping is not a line break
            out.append(text.translate(SUPER) if in_sup else text)
            continue
        if c.tag == "br":
            out.append("\n")
        elif c.tag == "sup":
            _tokens(c, out, refs, footnotes, True)
        elif c.tag == "a":
            href = c.attrs.get("href", "")
            if href.startswith("#poznamky.poznamka-"):
                footnotes.append(href.removeprefix("#poznamky."))
            elif "citacnyOdkaz" in c.attrs.get("class", ""):
                refs.append(c)
            _tokens(c, out, refs, footnotes, in_sup)
        elif c.tag == "div":
            out.append("\n")
            _tokens(c, out, refs, footnotes, in_sup)
            out.append("\n" if not any(x == "Oznacenie" or x.endswith("Oznacenie") for x in c.classes) else " ")
        else:
            _tokens(c, out, refs, footnotes, in_sup)


def _clean(parts: list[str]) -> str:
    text = "".join(parts)
    lines = [re.sub(r"[ \t\r\n\xa0]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _flat(node: Node) -> tuple[str, list[Node], list[str]]:
    out: list[str] = []
    refs: list[Node] = []
    footnotes: list[str] = []
    _tokens(node, out, refs, footnotes)
    return _clean(out), refs, footnotes


def _child_class(node: Node, name: str) -> list[Node]:
    return [c for c in node.children if isinstance(c, Node) and name in c.classes]


def _label(node: Node, cls: str) -> str:
    marks = _child_class(node, f"{cls}Oznacenie")
    return _flat(marks[0])[0] if marks else ""


def _bare(cls: str, raw: str) -> str:
    raw = raw.strip()
    if cls in ("clanok", "ustavnyclanok"):
        return re.sub(r"^Čl\.\s*", "", raw).strip()
    if cls == "paragraf":
        return re.sub(r"^§\s*", "", raw).strip().rstrip(".")
    return raw.strip().strip("()").rstrip(".)").strip()


def _ref(a: Node, own_work: str, own_eid: str | None) -> ParsedRef | None:
    href = a.attrs.get("href", "")
    text = _flat(a)[0]
    if href.startswith("#"):
        target = href[1:]
        if target.startswith("poznamky."):
            return None
        return ParsedRef(f"{own_work}/~{target}", text, "cites")
    m = LINK.search(href)
    if not m:
        return None
    work = f"/sk/zz/{m.group(1)}/{int(m.group(2))}"
    return ParsedRef(f"{work}/~{m.group(3)}" if m.group(3) else work, text, "cites")


def _date(value: str) -> date | None:
    return date.fromisoformat(value) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or "") else None


def _adopted(text: str) -> date | None:
    m = re.search(r"z\s+(\d{1,2})\.\s*(\w+)\s+(\d{4})", text.replace("\xa0", " "))
    if not m or m.group(2) not in MONTHS:
        return None
    return date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))


def parse_slovlex_static(content: bytes) -> list[ParsedDocument]:
    root = parse_html(content)
    h1 = _first(root, lambda n: n.tag == "h1")
    header = _first(root, lambda n: "ucinnost_header" in n.classes)
    predpis = _first(root, lambda n: n.attrs.get("id") == "predpis")
    if h1 is None or header is None or predpis is None:
        raise SlovLexParseError("not a Slov-lex act page (no h1, ucinnost_header or predpis)")
    m = re.fullmatch(r"(\d+)/(\d{4}) (Z\. z\.|Zb\.)", _flat(h1)[0].replace("\xa0", " ").strip())
    if not m:
        raise SlovLexParseError(f"unexpected act designation {_flat(h1)[0]!r}")
    number, year, collection = m.group(1), int(m.group(2)), m.group(3)
    start, end = _date(header.attrs.get("data-from", "")), _date(header.attrs.get("data-to", ""))
    if start is None:
        raise SlovLexParseError(f"{number}/{year}: no effective date (the promulgated version is not parsed)")
    if end is not None and end < start:
        raise SlovLexParseError(f"{number}/{year}: validity ends before it starts")
    valid_to = end + timedelta(days=1) if end else None
    work_uri = f"/sk/zz/{year}/{int(number)}"

    def text_of(cls: str) -> str:
        n = _first(predpis, lambda x: x.attrs.get("id") == f"predpis.{cls}")
        return _flat(n)[0] if n is not None else ""

    kind_text = text_of("typ")
    instrument_type = next((t for k, t in INSTRUMENT_TYPES if kind_text.upper().startswith(k)), None)
    if instrument_type is None:
        raise SlovLexParseError(f"{number}/{year}: unsupported instrument type {kind_text!r}")
    subtitle = text_of("podnadpis") or text_of("nadpis")
    if subtitle.isupper():
        subtitle = subtitle.capitalize()
    title = f"{kind_text.capitalize()} {subtitle}".strip()
    if not title or not kind_text:
        raise SlovLexParseError(f"{number}/{year}: no title")

    footnote_texts: dict[str, str] = {}
    footnote_refs: dict[str, list[ParsedRef]] = {}
    notes_block = _first(root, lambda n: n.attrs.get("id") == "poznamky")
    if notes_block is not None:
        for fn in notes_block.find_all(
            lambda n: "poznamka" in n.classes and n.attrs.get("id", "").startswith("poznamky.")
        ):
            label, body = _label(fn, "poznamka"), _child_class(fn, "text")
            key = fn.attrs["id"].removeprefix("poznamky.")
            text, anchors, _ = _flat(body[0]) if body else ("", [], [])
            footnote_texts[key] = f"{label} {text}".strip()
            footnote_refs[key] = [r for r in (_ref(a, work_uri, None) for a in anchors) if r is not None]

    provisions: list[ParsedProvision] = []
    used: dict[str, int] = {}

    def own_content(node: Node) -> tuple[str, list[ParsedRef], list[str]]:
        parts: list[str] = []
        refs: list[ParsedRef] = []
        notes: list[str] = []
        for c in node.children:
            if isinstance(c, Node) and ("text" in c.classes or "text2" in c.classes):
                text, anchors, fns = _flat(c)
                if text:
                    parts.append(text)
                refs += [r for r in (_ref(a, work_uri, None) for a in anchors) if r is not None]
                notes += fns
        return "\n".join(parts), refs, notes

    def has_paragraf(node: Node) -> bool:
        return any(
            isinstance(c, Node)
            and "text" not in c.classes
            and "text2" not in c.classes
            and ("paragraf" in c.classes or has_paragraf(c))
            for c in node.children
        )

    note_keys: dict[int, list[str]] = {}

    def add(node: Node, cls: str, parent_index: int | None, path: list[str], context: str | None) -> None:
        kind, path_kind = PROVISIONS[cls]
        label = _bare(cls, _label(node, cls))
        text, refs, fns = own_content(node)
        if (
            cls in ("odsek", "bod", "pismeno") and not label and parent_index is not None
        ):  # unnumbered: its parent's text
            parent = provisions[parent_index]
            parent.text = "\n".join(t for t in (parent.text, text) if t)
            parent.has_own_content = bool(parent.text)
            parent.refs += refs
            note_keys.setdefault(parent_index, []).extend(fns)
            walk(node, parent_index, path, context)
            return
        if cls == "clanok" and has_paragraf(node):  # the article that holds the act itself: not part of citations
            walk(node, parent_index, path, context)
            return
        raw_id = node.attrs.get("id") or f"{cls}-{len(provisions)}"
        used[raw_id] = used.get(raw_id, 0) + 1
        eid = raw_id if used[raw_id] == 1 else f"{raw_id}-{used[raw_id]}"
        heading_nodes = [
            c for c in node.children if isinstance(c, Node) and any(x.endswith("Nadpis") for x in c.classes)
        ]
        heading = _flat(heading_nodes[0])[0] if heading_nodes else None
        here = [*path, f"{path_kind}:{label}"]
        index = len(provisions)
        repealed = bool(REPEALED.match(text))
        if parent_index is not None:
            provisions[parent_index].is_leaf = False
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=kind,
                num_path=here,
                top_kind="section",
                heading=heading,
                text="" if repealed else text,
                parent_eid=provisions[parent_index].eid if parent_index is not None else None,
                sort_order=index,
                status="repealed" if repealed else "in_force",
                valid_from=start,
                valid_to=valid_to,
                extent=[JURISDICTION],
                note=None,
                refs=refs,
                is_leaf=True,
                has_own_content=bool(text) and not repealed,
                context_heading=heading or context,
            )
        )
        note_keys[index] = list(fns)
        if repealed:
            provisions[index].note = text
        walk(node, index, here, heading or context)

    def walk(node: Node, parent_index: int | None, path: list[str], context: str | None) -> None:
        for c in node.children:
            if not isinstance(c, Node):
                continue
            cls = next((x for x in c.classes if x in PROVISIONS), None)
            if cls is not None:
                add(c, cls, parent_index, path, context)
            elif any(x in CONTAINERS for x in c.classes):
                nadpis = [
                    n
                    for n in c.children
                    if isinstance(n, Node) and any(x.endswith(("Nadpis", "Oznacenie")) for x in n.classes)
                ]
                heading = " ".join(t for t in (_flat(n)[0] for n in nadpis) if t) or context
                walk(c, parent_index, path, heading)
            elif "priloha" in c.classes and parent_index is None:
                label = _flat(c.children[0])[0] if c.children and isinstance(c.children[0], Node) else ""
                body = _flat(c)[0]
                eid = c.attrs.get("id") or f"priloha-{len(provisions)}"
                provisions.append(
                    ParsedProvision(
                        eid=eid,
                        kind="annex",
                        num_path=[f"annex:{eid.removeprefix('predpis.priloha-')}"],
                        top_kind="annex",
                        heading=label or None,
                        text=body,
                        parent_eid=None,
                        sort_order=len(provisions),
                        status="in_force",
                        valid_from=start,
                        valid_to=valid_to,
                        extent=[JURISDICTION],
                        note=None,
                        refs=[],
                        is_leaf=True,
                        has_own_content=bool(body),
                        context_heading=label or None,
                    )
                )

    intro = _first(predpis, lambda n: n.attrs.get("id") == "predpis.text")
    if intro is not None and "PREAMBULA" in _flat(intro)[0]:
        preamble = _flat(intro)[0]
        provisions.append(
            ParsedProvision(
                eid="preamble",
                kind="preamble",
                num_path=["preamble:preambula"],
                top_kind="preamble",
                heading="Preambula",
                text=preamble,
                parent_eid=None,
                sort_order=0,
                status="in_force",
                valid_from=start,
                valid_to=valid_to,
                extent=[JURISDICTION],
                note=None,
                refs=[],
                is_leaf=True,
                has_own_content=True,
                context_heading="Preambula",
            )
        )
    walk(predpis, None, [], None)
    for index, keys in note_keys.items():
        seen: list[str] = []
        for key in keys:
            if key in footnote_texts and footnote_texts[key] not in seen:
                seen.append(footnote_texts[key])
                known = {r.href for r in provisions[index].refs}
                provisions[index].refs += [
                    r for r in footnote_refs[key] if r.href not in known
                ]  # the footnote cites them
        if seen:
            provisions[index].note = "\n".join(seen)
    if not provisions:
        raise SlovLexParseError(f"{number}/{year}: no provisions found")

    amended_by = ""
    row = _first(root, lambda n: n.tag == "tr" and n.attrs.get("data-ucinnostod") == start.isoformat())
    if row is not None:
        cells = [c for c in row.children if isinstance(c, Node) and c.tag == "td"]
        amended_by = _flat(cells[-1])[0] if cells else ""
    events = [
        ParsedEvent(
            "version_change",
            start,
            None,
            f"Time version in force from {start}" + (f"; amending: {amended_by}" if amended_by else ""),
            "ucinnost_header",
        )
    ]
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=instrument_type,
            jurisdiction_id=JURISDICTION,
            work_uri=work_uri,
            expression_uri=f"{work_uri}/slk@{start.isoformat()}",
            work_title=title,
            title=title,
            short_title=None,
            number=f"{number}/{year} {collection}",
            work_date=_adopted(text_of("datum")),
            language="sk",
            authoritative=False,
            text_origin="official_consolidation",
            consolidation_kind="point_in_time",
            version_date=start,
            currency_date=None,
            extent=[JURISDICTION],
            ext_module="sk",
            ext={"year": year, "number": number, "collection": collection},
            events=events,
            provisions=provisions,
            validity_basis="revision_snapshot",
        )
    ]


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """``/sk/zz/<year>/<number>[/~<eid>]`` -> (work URI, eId); other URIs are not Slovak."""
    m = re.match(r"^(/sk/zz/\d{4}/\d+)(?:/~(.+))?$", uri.strip())
    return (m.group(1), m.group(2)) if m else None
