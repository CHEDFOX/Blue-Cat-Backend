"""Parser for Latvian legislation from Likumi.lv (HTML pages of the official publisher "Latvijas Vēstnesis").

Likumi.lv (https://likumi.lv) is run by the state limited company "Latvijas Vēstnesis", the official
publisher. Its systematised (consolidated) texts are **informative**: "Sistematizēti tiesību akti ir
informatīvi. Pretrunu gadījumā vadās pēc oficiālās publikācijas." The authentic text is the
publication in the official gazette Latvijas Vēstnesis (vestnesis.lv). Texts are therefore
``authoritative: false`` and ``official_revised``.

Input: one document page, ``https://likumi.lv/ta/id/<id>`` (the page the publisher links as the
act's own address). Format notes (what the page provides, and how ULIS uses it):

* ``<div class="doc-body">`` holds the text as a flat list of ``<div class="TVnnn">`` blocks:
  ``TV206`` enacting formula, ``TV207`` title, ``TV208`` preamble (or the body of ``TV403``),
  ``TV212`` structural heading (part, division, chapter, ...; the level is read from the heading
  words, the class is the same for all), ``TV213`` an article (``data-pfx="p"``, ``data-num`` its
  number, ``38_1`` for 38.¹) or a transitional provision (``data-pfx="pn"``), ``TV214`` the heading
  "Pārejas noteikumi", ``TV403`` the heading "Informatīva atsauce uz Eiropas Savienības direktīvām",
  ``TV215``-``TV217`` adoption statement and signatures;
* inside an article the first ``<p>`` carries the printed number (and, for most acts, the heading:
  ``<B>117.pants. Slepkavība pastiprinošos apstākļos</B>``); the rest are lines: ``(1)`` starts a
  part (daļa), ``<p class="limenis2">`` items ``1)`` are points (punkts), ``limenis3`` items ``a)``
  sub-points (apakšpunkts). Unnumbered paragraphs stay text of their container: ULIS does not number
  what the source does not number;
* ``<p class="labojumu_pamats">`` and ``<span class="fontsize2">`` are the publisher's amendment and
  deletion annotations ("(Ar grozījumiem, kas izdarīti ar ... likumu, kas stājas spēkā ...)",
  "(Izslēgts ar ... likumu)"). They are kept as notes, never as text; a unit that consists of nothing
  else than a deletion annotation is ``repealed`` (empty text, the annotation as note);
* ``<sup>`` is rendered as superscript digits (``38.¹``), because plain digits would change the
  number; whitespace is collapsed;
* **dates:** the page embeds the list of editions ("redakcijas") with their validity, and says which
  one it shows (``id="ver_date"``). The shown edition is the whole act as it stood from its start
  date; the pages of earlier editions are disallowed by robots.txt and are not fetched. The document
  is therefore a whole-act snapshot (``validity_basis = "revision_snapshot"``): every provision is
  dated from the edition's start, ends where the source states that the edition ends, and later
  editions ("Nākotnes", enacted but not yet in force) are reported as outstanding changes, without
  text. The page states no per-provision dates and no "up to date" date (``currency_date`` None).

Not ingested: the enacting formula, adoption statement and signatures, the amendment notes' links,
tables and annexes (the documents ingested have none; an unknown block class stops the parse).
"""

from __future__ import annotations

import html as htmllib
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from html.parser import HTMLParser

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParsedRef, ParseSkipped

PARSER_ID = "likumi_lv_html"
PARSER_VERSION = "1.0.0"
SITE = "https://likumi.lv"
JURISDICTION = "LV"
NOTE_LIMIT = 2000

# The Satversme is the constitution; the other documents are acts (likums). Short names and genitive
# forms (needed for the citation "Krimināllikuma 117. pants") cannot be derived from a title in
# general, so they are given for the documents ULIS ingests; other titles ending in "likums" get the
# regular genitive.
CONSTITUTION_IDS = {57980}
SHORT_NAMES = {
    57980: ("Satversme", "Satversmes"),
    88966: ("Krimināllikums", "Krimināllikuma"),
    26019: ("Darba likums", "Darba likuma"),
    300099: ("Fizisko personu datu apstrādes likums", "Fizisko personu datu apstrādes likuma"),
    55567: ("Administratīvā procesa likums", "Administratīvā procesa likuma"),
    90224: ("Civillikums", "Civillikuma"),
    90223: ("Civillikums", "Civillikuma"),
    90222: ("Civillikums", "Civillikuma"),
    90221: ("Civillikums", "Civillikuma"),
    90220: ("Civillikums", "Civillikuma"),
}


class LikumiParseError(ValueError):
    pass


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

    def find(self, pred) -> Node | None:  # type: ignore[no-untyped-def]
        stack: list[Node] = [self]
        while stack:
            node = stack.pop()
            if node is not self and pred(node):
                return node
            stack.extend(reversed(node.elements()))
        return None

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
        if tag == "p" and self.cur.tag == "p":  # an unclosed paragraph ends where the next begins
            assert self.cur.parent is not None
            self.cur = self.cur.parent
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


def parse_html(content: bytes) -> Node:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LikumiParseError(f"not UTF-8: {exc}") from exc
    builder = _Builder()
    builder.feed(text)
    builder.close()
    return builder.root


# --------------------------------------------------------------------------- text

SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
DESUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
SUP = "⁰¹²³⁴⁵⁶⁷⁸⁹"
REPEAL = re.compile(r"izslēgt\w*|zaudējis spēku|zaudējusi spēku|zaudējuši spēku", re.IGNORECASE)
SKIP_CLASSES = {"info-icon-wrapper", "panta-doc-npk", "court", "notify-msg"}


def ws(text: str) -> str:
    return re.sub(r"[\s\xa0]+", " ", text).strip()


def lines_of(text: str) -> str:
    return "\n".join(line for line in (ws(x) for x in text.split("\n")) if line)


@dataclass
class Rendered:
    text: str
    notes: list[str] = field(default_factory=list)
    refs: list[tuple[str, str]] = field(default_factory=list)


def render(node: Node, skip: Node | None = None) -> Rendered:
    """The text of an element: ``<br>`` becomes a line break, ``<sup>`` superscript digits; the
    publisher's annotations are returned separately as notes."""
    parts: list[str] = []
    notes: list[str] = []
    refs: list[tuple[str, str]] = []

    def walk(el: Node) -> None:
        for child in el.children:
            if isinstance(child, str):
                parts.append(child)
                continue
            if child is skip or child.tag in ("script", "style"):
                continue
            if child.classes & SKIP_CLASSES:
                continue
            if child.tag == "br":
                parts.append("\n")
            elif child.tag == "sup":
                inner = ws(render(child).text)
                parts.append(inner.translate(SUPERSCRIPT) if inner.isdigit() else inner)
            elif (child.tag == "span" and "fontsize2" in child.classes) or "labojumu_pamats" in child.classes:
                note = ws(render(child).text)
                if note:
                    notes.append(note)
            elif child.tag == "a":
                href = child.attrs.get("href", "")
                link = render(child)
                if href:
                    refs.append((href, ws(link.text)))
                parts.append(link.text)
            else:
                walk(child)

    walk(node)
    return Rendered(lines_of("".join(parts)), notes, refs)


# --------------------------------------------------------------------------- references


def work_uri(likumi_id: int | str) -> str:
    return f"/lv/likumi/{int(likumi_id)}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map a Likumi.lv document URL to (ULIS work URI, eId).

    ``https://likumi.lv/ta/id/88966#p117`` -> (``/lv/likumi/88966``, ``art-117``);
    ``https://likumi.lv/ta/id/88966-kriminallikums`` -> (``/lv/likumi/88966``, None).
    """
    m = re.match(r"^https://likumi\.lv/ta/id/(\d+)(?:-[a-z0-9-]*)?(?:/redakcijas-datums/[\d/]*)?(?:#(.+))?$", uri)
    if not m:
        return None
    work, frag = work_uri(m.group(1)), m.group(2)
    if frag and (p := re.fullmatch(r"p(\d+(?:_\d+)?)", frag)):
        return work, f"art-{p.group(1)}"
    if frag and (p := re.fullmatch(r"pn(\d+(?:_\d+)?)", frag)):
        return work, f"pn-{p.group(1)}"
    return work, None


def ref_href(href: str, doc_id: int) -> str | None:
    """A Likumi.lv link as an absolute URL that ``resolve_uri`` understands; None for other links."""
    if href.startswith("#"):
        frag = href[1:]
        return f"{SITE}/ta/id/{doc_id}#{frag}" if re.fullmatch(r"pn?\d+(?:_\d+)?", frag) else None
    m = re.match(r"^(?:https://likumi\.lv)?/ta/id/(\d+)(?:-[a-z0-9-]*)?(#pn?\d+(?:_\d+)?)?$", href)
    if m:
        return f"{SITE}/ta/id/{m.group(1)}{m.group(2) or ''}"
    return None


# --------------------------------------------------------------------------- units

DALA = re.compile(rf"^\(([0-9]+[{SUP}]*)\)\s*")
POINT = re.compile(rf"^([0-9]+(?:\.?[{SUP}]+)?)\)\s*")
# A sub-point of a Cabinet regulation: "2.1.", "12.1.3." (a number with at least one more part, then a point).
DOTTED = re.compile(rf"^([0-9]+[{SUP}]*(?:\.[0-9]+[{SUP}]*)+)\.\s*")
SUBPOINT = re.compile(rf"^([a-zāčēģīķļņšūž]{{1,2}}[{SUP}]*)\)\s*")
NUMBER_LABEL = re.compile(rf"^([0-9]+(?:\.?[{SUP}]+)?)\.?\s*")
ARTICLE_HEAD = re.compile(rf"^([0-9]+(?:\.?[{SUP}]+)?)\.?\s*pants\.?\s*(.*)$", re.DOTALL)
BARE_NUMBER = re.compile(rf"^([0-9]+(?:\.?[{SUP}]+)?)\.?$")


def key_of(label: str) -> str:
    """``(1)`` -> ``1``, ``7.¹)`` -> ``7_1``, ``a)`` -> ``a``, ``38.¹`` -> ``38_1``."""
    s = label.strip().strip("()").rstrip(")").rstrip(".")
    return re.sub(rf"\.?([{SUP}]+)", lambda m: "_" + m.group(1).translate(DESUPERSCRIPT), s)


def dotted_key(label: str) -> str:
    """``2.1`` -> ``2-1``; ``12.1.3`` -> ``12-1-3``; ``2.1¹`` -> ``2-1_1`` (a superscript stays an inserted number)."""
    return "-".join(key_of(part) for part in label.split("."))


@dataclass
class Unit:
    kind: str
    label: str  # as printed: "(1)", "1)", "a)"; empty for top-level units
    key: str
    heading: str | None = None
    content: list[str | Unit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    refs: list[tuple[str, str]] = field(default_factory=list)

    def children(self) -> list[Unit]:
        return [c for c in self.content if isinstance(c, Unit)]

    def own_text(self) -> str:
        return "\n".join(c for c in self.content if isinstance(c, str) and c)

    def text(self) -> str:
        lines = [c if isinstance(c, str) else f"{c.label} {c.text()}".strip() for c in self.content]
        return "\n".join(line for line in lines if line)


def note_text(notes: list[str]) -> str | None:
    seen: list[str] = []
    for n in notes:
        if n and n not in seen:
            seen.append(n)
    return " ".join(seen)[:NOTE_LIMIT] or None


def block_number(div: Node) -> str:
    """The article number: ``data-num``, or (some Civillikums articles) the ``izd-pam`` attribute (``405p``)."""
    if div.attrs.get("data-num"):
        return div.attrs["data-num"]
    m = re.fullmatch(r"(?:pn?)?(\d+(?:_\d+)?)p?", div.attrs.get("izd-pam", ""))
    return m.group(1) if m else ""


def build_article(div: Node, kind: str, regulation: bool = False) -> Unit:
    """One TV213/TV208 block as a unit tree: the article with its parts, points and sub-points.

    In a Cabinet regulation (``regulation``) the block is a numbered point ("12. Licences turētājam ..."), its
    sub-points are numbered by the point ("12.1.", "12.1.2.") and its lists by letters ("a)")."""
    num = block_number(div)
    unit = Unit(kind, "", num or kind)
    paragraphs: list[Node] = []
    for child in div.children:
        if isinstance(child, str):
            if ws(child):
                raise LikumiParseError(f"unexpected text directly in article {num!r}: {ws(child)[:60]!r}")
            continue
        if child.tag == "a" or child.classes & SKIP_CLASSES:
            continue
        if child.tag != "p":
            raise LikumiParseError(f"unexpected <{child.tag}> in article {num!r}")
        if "labojumu_pamats" in child.classes:
            unit.notes.append(ws(render(child).text))
        else:
            paragraphs.append(child)

    lines: list[tuple[int, Rendered]] = []
    for i, p in enumerate(paragraphs):
        level = 0
        for token in p.classes:
            if m := re.fullmatch(r"limenis(\d)", token):
                level = int(m.group(1))
        skip: Node | None = None
        if i == 0:
            first = next((c for c in p.children if not (isinstance(c, str) and not ws(c))), None)
            if isinstance(first, Node) and first.tag == "b":
                skip = first
                bold = ws(render(first).text)
                if m := ARTICLE_HEAD.match(bold):
                    unit.heading = ws(m.group(2)) or None
                elif BARE_NUMBER.match(bold):
                    pass  # only the printed number; the text follows
                else:
                    unit.heading = bold
        rendered = render(p, skip)
        if i == 0 and skip is None and num and (kind == "transitional" or (regulation and kind == "article")):
            # "1. Šā likuma ..." / "25.³ (Izslēgts ...)": the printed number precedes the text
            m = NUMBER_LABEL.match(rendered.text)
            if m and key_of(m.group(1)) == num:
                rendered = Rendered(rendered.text[m.end() :], rendered.notes, rendered.refs)
        lines.append((level, rendered))

    daļa: Unit | None = None
    point: Unit | None = None
    sub: Unit | None = None
    dotted: dict[str, Unit] = {}

    def deepest() -> Unit:
        return sub or point or daļa or unit

    for level, r in lines:
        text = r.text
        target: Unit | None = None
        if level == 0 and (m := DALA.match(text)):
            target = Unit("paragraph", f"({m.group(1)})", key_of(m.group(1)))
            unit.content.append(target)
            daļa, point, sub = target, None, None
        elif regulation and level >= 2 and (m := DOTTED.match(text)):
            # "2.1. ..." / "2.1.3. ...": a sub-point of point 2 (or of 2.1); the parent is the unit whose number
            # is this one without its last part, else the point itself.
            label = m.group(1)
            key = dotted_key(label)
            parts = label.split(".")
            parent: Unit = unit
            for n in range(len(parts) - 1, 0, -1):
                if dotted_key(".".join(parts[:n])) in dotted:
                    parent = dotted[dotted_key(".".join(parts[:n]))]
                    break
            target = Unit("point", f"{label}.", key)
            parent.content.append(target)
            dotted[key] = target
            point, sub = target, None
        elif level == 2 and (m := POINT.match(text)):
            target = Unit("point", f"{m.group(1)})", key_of(m.group(1)))
            (daļa or unit).content.append(target)
            point, sub = target, None
        elif level == 3 and (m := SUBPOINT.match(text)):
            target = Unit("subpoint", f"{m.group(1)})", key_of(m.group(1)))
            (point or daļa or unit).content.append(target)
            sub = target
        if target is not None:
            assert m is not None
            target.content.append(text[m.end() :]) if text[m.end() :] else None
            target.notes.extend(r.notes)
            target.refs.extend(r.refs)
            continue
        # An unnumbered line: text of the open container (a closing paragraph after the points
        # ends them), or a deeper level the parser does not split further.
        if level == 0:
            point, sub = None, None
        holder = deepest() if level else (daļa or unit)
        if text:
            holder.content.append(text)
        holder.notes.extend(r.notes)
        holder.refs.extend(r.refs)
    return unit


# --------------------------------------------------------------------------- headings

UNIT_RANKS = {"daļa": 1, "sadaļa": 2, "nodaļa": 3, "apakšnodaļa": 4}


def heading_rank(text: str) -> int:
    """Level of a structural heading, from its words: part (daļa) < division (sadaļa) < chapter (nodaļa) <
    subchapter (apakšnodaļa) < "I." < "1.". The words come first in a heading ("A DAĻA", "1.nodaļa", "VIII¹ nodaļa",
    "TREŠĀ A NODAĻA"). Any other heading ranks below everything above it."""
    words = re.sub(r"[.,]", " ", text.lower()).split()
    for word in words[:4]:
        if word in UNIT_RANKS:
            return UNIT_RANKS[word]
    if re.match(r"^[IVXLC]+\.", text):
        return 5
    if re.match(r"^\d+\.", text):
        return 6
    return 9


# --------------------------------------------------------------------------- document metadata


MONTHS_GENITIVE = (
    "janvāra",
    "februāra",
    "marta",
    "aprīļa",
    "maija",
    "jūnija",
    "jūlija",
    "augusta",
    "septembra",
    "oktobra",
    "novembra",
    "decembra",
)


def date_genitive(d: date) -> str:
    """``2013-12-10`` -> ``2013. gada 10. decembra`` (the form that dates a Cabinet regulation in a citation)."""
    return f"{d.year}. gada {d.day}. {MONTHS_GENITIVE[d.month - 1]}"


def _date(value: str | None) -> date | None:
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", value or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def passport(root: Node) -> dict[str, str]:
    """The "Tiesību akta pase" fields: Nosaukums, Statuss, Izdevējs, Veids, Pieņemts, Stājas spēkā, Publicēts ..."""
    out: dict[str, str] = {}
    for span in root.find_all(lambda n: n.tag == "span"):
        first = next((c for c in span.children if isinstance(c, Node)), None)
        if first is None or first.tag != "font" or "fclg2" not in first.classes:
            continue
        label = ws(render(first).text).rstrip(":").strip()
        value = ws(render(span, first).text)
        if label and label not in out:
            out[label] = value
    return out


@dataclass(frozen=True)
class Edition:
    start: date
    end: date | None  # the last day, as the source states it; None when open ("...")
    status: str


def editions(root: Node) -> tuple[date, list[Edition]]:
    holder = root.find(lambda n: n.attrs.get("id") == "ver_date")
    if holder is None:
        raise LikumiParseError("the page lists no editions (id=ver_date)")
    raw = "".join(c for c in holder.children if isinstance(c, str))
    if not raw.strip():
        # An act with a single edition has an empty edition list; the page still names the edition it shows
        # (``<div id="version_date" data-version_date="30.08.2019">``), which has no end.
        shown = root.find(lambda n: n.attrs.get("id") == "version_date")
        start = _date(shown.attrs.get("data-version_date")) if shown is not None else None
        if start is None:
            raise LikumiParseError("the page lists no editions and names no edition date (id=version_date)")
        return start, [Edition(start, None, "Spēkā esošā")]
    try:
        data = json.loads(raw)
        selected = _date(data["selected"]["value"])
        items = data["data"]
    except (ValueError, KeyError, TypeError) as exc:
        raise LikumiParseError(f"cannot read the edition list: {exc}") from exc
    out: list[Edition] = []
    for item in items:
        label = ws(re.sub(r"<[^>]+>", " ", htmllib.unescape(str(item.get("content", "")))))
        m = re.match(r"(\d{2}\.\d{2}\.\d{4})\.?\s*-\s*(\.\.\.|\d{2}\.\d{2}\.\d{4})\.?\s*(.*)$", label)
        start = _date(m.group(1)) if m else None
        if not m or start is None:
            raise LikumiParseError(f"cannot read edition {label!r}")
        out.append(Edition(start, None if m.group(2) == "..." else _date(m.group(2)), m.group(3)))
    if selected is None or not any(e.start == selected for e in out):
        raise LikumiParseError("the shown edition is not in the edition list")
    return selected, out


# --------------------------------------------------------------------------- the document


def parse_likumi_lv(content: bytes) -> list[ParsedDocument]:
    root = parse_html(content)
    body = root.find(lambda n: n.tag == "div" and "doc-body" in n.classes)
    id_node = root.find(lambda n: n.attrs.get("id") == "doc_id")
    title_node = root.find(lambda n: n.tag == "title")
    if body is None or id_node is None or title_node is None:
        raise LikumiParseError("not a Likumi.lv document page (no doc-body, doc_id or title)")
    id_text = ws(render(id_node).text)
    if not id_text.isdigit():
        raise LikumiParseError(f"bad document id {id_text!r}")
    doc_id = int(id_text)
    title = ws(render(title_node).text)
    meta = passport(root)
    status = meta.get("Statuss", "")
    veids, issuer = meta.get("Veids", ""), meta.get("Izdevējs", "")
    # The manifest lists candidates (the sitemap says nothing about what a page is): only a law (Veids "likums") or a
    # Cabinet regulation (Veids "noteikumi" of the Ministru kabinets) that is in force is ingested.
    regulation = veids == "noteikumi" and issuer == "Ministru kabinets"
    if veids != "likums" and not regulation:
        raise ParseSkipped(f"{title}: a document of type {veids!r} ({issuer}), neither a law nor a Cabinet regulation")
    if "spēkā esošs" not in status.lower():
        raise ParseSkipped(f"{title}: status is {status!r}, not 'Spēkā esošs'; only acts in force are ingested")
    selected, all_editions = editions(root)
    shown = next(e for e in all_editions if e.start == selected)
    valid_to = shown.end + timedelta(days=1) if shown.end else None
    adopted = _date(meta.get("Pieņemts"))
    in_force = _date(meta.get("Stājas spēkā"))
    if adopted is None:
        raise LikumiParseError(f"{title}: no adoption date (Pieņemts)")

    units: list[tuple[Unit, str | None]] = []  # (unit, context heading)
    headings: list[tuple[int, str]] = []
    transitional = False
    eu_heading: tuple[str, list[str]] | None = None
    seen_article = False
    omitted: list[str] = []  # parts of the page that are not ingested (annexes, tables): disclosed in ext
    in_annex = False
    for block in body.elements():
        tokens = [t for t in block.classes if re.fullmatch(r"TV\d+", t)]
        if block.tag != "div" or block.classes & {"notify-msg"}:
            continue
        if not tokens:
            raise LikumiParseError(f"{title}: unexpected block <{block.tag} class={block.attrs.get('class')!r}>")
        cls = tokens[0]
        context = " › ".join(h for _, h in headings) or None
        if in_annex:
            continue  # the annexes (pielikumi) follow the text and the signatures; they are not ingested
        if cls == "TV218":
            in_annex = True
            omitted.append("pielikumi")
            continue
        if cls in ("TV206", "TV207", "TV215", "TV216", "TV217"):
            continue
        if cls == "TV212":
            r = render(block)
            text = ws(r.text.replace("\n", " "))
            rank = heading_rank(text)
            while headings and headings[-1][0] >= rank:
                headings.pop()
            headings.append((rank, text))
        elif cls == "TV214":
            transitional = True
            headings = [(0, ws(render(block).text))]
        elif cls == "TV213":
            pfx = block.attrs.get("data-pfx")
            if not block_number(block) or pfx not in ("p", "pn"):
                raise LikumiParseError(f"{title}: article without number/data-pfx: {block.attrs}")
            if (pfx == "pn") != transitional:
                raise LikumiParseError(f"{title}: article {block_number(block)} out of place ({pfx})")
            units.append((build_article(block, "transitional" if pfx == "pn" else "article", regulation), context))
            seen_article = True
        elif cls == "TV403":
            r = render(block)
            eu_heading = (ws(r.text), r.notes)
        elif cls == "TV208":
            if eu_heading is not None:
                unit = build_article(block, "informative_reference")
                unit.key, unit.heading = "eu-ref", eu_heading[0]
                unit.notes.extend(eu_heading[1])
                units.append((unit, None))
                eu_heading = None
            elif not seen_article:
                unit = build_article(block, "preamble")
                unit.key = "preamble"
                units.append((unit, None))
            else:
                raise LikumiParseError(f"{title}: unexpected TV208 block after the articles")
        elif cls == "TV900":
            # "Izdoti saskaņā ar <likums> <panta> ...": the enabling provision of a Cabinet regulation.
            r = render(block)
            basis = Unit("legal_basis", "", "legal-basis")
            basis.content.append(r.text)
            basis.notes.extend(r.notes)
            basis.refs.extend(r.refs)
            units.append((basis, None))
        elif cls == "TV444":
            if "tabulas" not in omitted:
                omitted.append("tabulas")  # a table between the articles: not ingested
        else:
            raise LikumiParseError(f"{title}: unsupported block class {cls}")
    if eu_heading is not None:
        raise LikumiParseError(f"{title}: the EU reference heading has no body")
    if not units:
        raise LikumiParseError(f"{title}: no provisions found")

    base = work_uri(doc_id)
    provisions: list[ParsedProvision] = []
    used: dict[str, int] = {}

    def unique(eid: str) -> str:
        n = used.get(eid, 0) + 1
        used[eid] = n
        return eid if n == 1 else f"{eid}-{n}"

    def refs_of(items: list[tuple[str, str]]) -> list[ParsedRef]:
        out: list[ParsedRef] = []
        for href, text in items:
            url = ref_href(href, doc_id)
            if url and all(r.href != url for r in out):
                out.append(ParsedRef(url, text, "cites"))
        return out

    def top_label(u: Unit) -> tuple[str, str]:
        """(eId prefix, num_path label) of a top-level unit."""
        if u.kind == "article":
            return f"art-{u.key}", f"art:{u.key}"
        if u.kind == "transitional":
            return f"pn-{u.key}", f"pn:{u.key}"
        return u.key, u.key

    def add(
        u: Unit, parent_eid: str | None, path: list[str], eid_prefix: str, top_kind: str, context: str | None
    ) -> None:
        eid = unique(eid_prefix)
        own = u.own_text()
        text = u.text()
        children = u.children()
        # "3) (izslēgts ar ... likumu);" leaves only the list punctuation once the annotation is a note
        repealed = not re.sub(r"[;,.\s—–-]+", "", text) and any(REPEAL.search(n) for n in u.notes)
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=u.kind,
                num_path=path,
                top_kind=top_kind,
                heading=u.heading,
                text="" if repealed else text,
                parent_eid=parent_eid,
                sort_order=len(provisions),
                status="repealed" if repealed else "in_force",
                valid_from=selected,
                valid_to=valid_to,
                extent=[JURISDICTION],
                note=note_text(u.notes),
                refs=refs_of(u.refs),
                is_leaf=not children,
                has_own_content=bool(own) and bool(children),
                context_heading=context,
            )
        )
        short = {"paragraph": "d", "point": "p", "subpoint": "ap"}
        for child in children:
            tag = short[child.kind]
            add(child, eid, [*path, f"{tag}:{child.key}"], f"{eid}-{tag}{child.key}", top_kind, context)

    for unit, context in units:
        prefix, label = top_label(unit)
        add(unit, None, [label], prefix, unit.kind, context)

    short_name, genitive = SHORT_NAMES.get(doc_id, (title, None))
    if genitive is None and title.endswith("likums"):
        genitive = title[: -len("likums")] + "likuma"
    if genitive is None and title.endswith("kodekss"):
        genitive = title[: -len("kodekss")] + "kodeksa"
    part = title.split(". ", 1)[1] if title.startswith("Civillikums. ") else None
    instrument_type = "constitution" if doc_id in CONSTITUTION_IDS else "act"
    number = None
    if regulation:
        # "Ministru kabineta 2013. gada 10. decembra noteikumi Nr. 1449 "Noteikumi par ..."": the way a Cabinet
        # regulation is cited (date of adoption and number); the citation of a unit follows the genitive.
        instrument_type = "regulation"
        number = meta.get("Numurs") or None
        if number is None:
            raise LikumiParseError(f"{title}: a Cabinet regulation without a number (Numurs)")
        genitive = f"Ministru kabineta {date_genitive(adopted)} noteikumu Nr. {number}"
        short_name = title
        title = f'Ministru kabineta {date_genitive(adopted)} noteikumi Nr. {number} "{title}"'
    outstanding = [
        {
            "affects_eid": None,
            "affected_label": None,
            "by_uri": None,
            "by_title": f"Edition in force from {e.start.isoformat()}",
            "description": (
                f"A later edition of this act is published on Likumi.lv, in force from {e.start.isoformat()} "
                f"(status '{e.status}'); its text is not ingested."
            ),
        }
        for e in sorted(all_editions, key=lambda e: e.start)
        if e.start > selected
    ]
    events = [
        ParsedEvent("enactment", adopted, None, meta.get("Izdevējs"), "passport/Pieņemts"),
        *(
            [ParsedEvent("entry_into_force", in_force, None, None, "passport/Stājas spēkā")]
            if in_force is not None
            else []
        ),
        ParsedEvent("version_change", selected, None, None, "ver_date/selected"),
    ]
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=instrument_type,
            jurisdiction_id=JURISDICTION,
            work_uri=base,
            expression_uri=f"{base}/lav@{selected.isoformat()}",
            work_title=title,
            title=title,
            short_title=short_name,
            number=number,
            work_date=adopted,
            language="lv",
            # Systematised texts are informative; the official publication in Latvijas Vēstnesis prevails.
            authoritative=False,
            text_origin="official_revised",
            consolidation_kind="point_in_time",
            version_date=selected,
            currency_date=None,
            extent=[JURISDICTION],
            ext_module="lv",
            ext={
                "likumi_id": doc_id,
                "short_name": short_name,
                **({"genitive": genitive} if genitive else {}),
                **({"part": part} if part else {}),
                **({"kind": "regulation"} if regulation else {}),
                **({"omitted": omitted} if omitted else {}),
                **({"issuer": meta["Izdevējs"]} if meta.get("Izdevējs") else {}),
                **({"published_in": meta["Publicēts"]} if meta.get("Publicēts") else {}),
            },
            outstanding_changes=outstanding,
            events=events,
            provisions=provisions,
            validity_basis="revision_snapshot",
        )
    ]
