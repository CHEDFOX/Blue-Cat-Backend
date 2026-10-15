"""Shared helpers for parsers of official legal texts published as HTML pages.

Used by the Croatian (Narodne novine), Slovenian (Uradni list RS), Liechtenstein (Lilex) and other
parsers whose sources serve ordinary, hand-written or generated HTML rather than a legal XML
schema. Nothing here knows a particular publisher; each parser decides which markup means what.

* a tiny tolerant DOM (``parse_html``) built on the standard library's ``HTMLParser`` - these pages
  are not well-formed, and no third-party HTML library is a dependency of ULIS;
* ``render``: the exact text of an element (``<br>`` becomes a line break, ``<sup>`` digits become
  superscript characters, tables become one line per row), with the publisher's annotations
  (footnote marks and the like) returned separately so they never end up in the law text;
* ``Unit`` / ``build_provisions``: a tree of provisions (article, paragraph, point ...) turned into
  the flat, ordered ``ParsedProvision`` list the pipeline stores.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser

from ulis.sources.normalized import ParsedProvision, ParsedRef

NOTE_LIMIT = 4000


class HtmlParseError(ValueError):
    """The page is not the kind of page the parser reads, or contains markup it does not understand."""


# --------------------------------------------------------------------------- tiny tolerant DOM

VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "wbr"})
# elements the HTML grammar closes implicitly when a sibling of the same kind starts
_SELF_CLOSING_SIBLINGS = frozenset({"p", "li", "tr", "td", "th", "dt", "dd", "option"})


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

    def find(self, pred: Callable[[Node], bool]) -> Node | None:
        stack: list[Node] = [self]
        while stack:
            node = stack.pop()
            if node is not self and pred(node):
                return node
            stack.extend(reversed(node.elements()))
        return None

    def find_all(self, pred: Callable[[Node], bool]) -> list[Node]:
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
        if tag in _SELF_CLOSING_SIBLINGS and self.cur.tag == tag:
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


def parse_html(content: bytes, encoding: str = "utf-8") -> Node:
    try:
        text = content.decode(encoding)
    except UnicodeDecodeError as exc:
        raise HtmlParseError(f"not {encoding}: {exc}") from exc
    builder = _Builder()
    builder.feed(text)
    builder.close()
    return builder.root


# --------------------------------------------------------------------------- text

SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def ws(text: str) -> str:
    """Collapse runs of whitespace (including non-breaking spaces) to single spaces."""
    return re.sub(r"[\s\xa0]+", " ", text).strip()


def lines_of(text: str) -> str:
    """Whitespace-normalise every line of a text and drop empty lines."""
    return "\n".join(line for line in (ws(x) for x in text.split("\n")) if line)


@dataclass
class Rendered:
    text: str
    notes: list[str] = field(default_factory=list)
    refs: list[tuple[str, str]] = field(default_factory=list)


def render(
    node: Node,
    *,
    skip: Callable[[Node], bool] | None = None,
    note: Callable[[Node], bool] | None = None,
    superscript: bool = True,
) -> Rendered:
    """The text of an element.

    ``skip(el)`` drops an element (and its text) altogether; ``note(el)`` moves an element's text
    to the returned ``notes`` instead of the text. ``<br>`` is a line break, ``<sup>`` digits are
    written as superscript characters (``superscript=False`` drops that conversion), a table is one
    line per row with the cells separated by `` | ``, and the links found are returned in ``refs``.
    """
    parts: list[str] = []
    notes: list[str] = []
    refs: list[tuple[str, str]] = []

    def walk(el: Node) -> None:
        for child in el.children:
            if isinstance(child, str):
                parts.append(child)
                continue
            if child.tag in ("script", "style", "noscript") or (skip is not None and skip(child)):
                continue
            if note is not None and note(child):
                text = ws(render(child, skip=skip, note=note, superscript=superscript).text)
                if text:
                    notes.append(text)
                continue
            if child.tag == "br":
                parts.append("\n")
            elif child.tag == "sup" and superscript:
                inner = ws(render(child, skip=skip, note=note, superscript=superscript).text)
                parts.append(inner.translate(SUPERSCRIPT) if inner.isdigit() else inner)
            elif child.tag == "table":
                for row in child.find_all(lambda n: n.tag == "tr"):
                    cells = [
                        ws(render(c, skip=skip, note=note, superscript=superscript).text)
                        for c in row.elements()
                        if c.tag in ("td", "th")
                    ]
                    parts.append("\n" + " | ".join(c for c in cells if c) + "\n")
            elif child.tag == "a":
                inner_r = render(child, skip=skip, note=note, superscript=superscript)
                href = child.attrs.get("href", "")
                if href:
                    refs.append((href, ws(inner_r.text)))
                parts.append(inner_r.text)
                notes.extend(inner_r.notes)
            elif child.tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"):
                parts.append("\n")
                walk(child)
                parts.append("\n")
            else:
                walk(child)

    walk(node)
    return Rendered(lines_of("".join(parts)), notes, refs)


# --------------------------------------------------------------------------- units and provisions


@dataclass
class Unit:
    """One provision of a legal text and the provisions inside it.

    ``tag`` and ``key`` identify it among its siblings (``art`` + ``117``, ``abs`` + ``2``, ``bst`` +
    ``a``): the eId is built from them and the language-neutral ``num_path`` carries ``tag:key``. ``label``
    is the numbering as printed (``(2)``, ``a)``), used only to assemble the composite text of the parent.
    """

    kind: str
    tag: str
    key: str
    label: str = ""
    heading: str | None = None
    content: list[str | Unit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    refs: list[tuple[str, str]] = field(default_factory=list)
    repealed: bool = False

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


def build_provisions(
    tops: list[tuple[Unit, str | None]],
    *,
    extent: list[str],
    valid_from: date | None,
    valid_to: date | None,
    status: str = "in_force",
    ref_target: Callable[[str], str | None] | None = None,
) -> list[ParsedProvision]:
    """Flatten a list of ``(top-level unit, context heading)`` pairs into ordered provisions.

    A repealed unit (``Unit.repealed``) is recorded with status ``repealed``, no text, and its note.
    Duplicate eIds get a numeric suffix. ``ref_target`` maps a link's ``href`` to a citation target
    (or ``None`` to drop the link).
    """
    provisions: list[ParsedProvision] = []
    used: dict[str, int] = {}

    def unique(eid: str) -> str:
        n = used.get(eid, 0) + 1
        used[eid] = n
        return eid if n == 1 else f"{eid}-{n}"

    def refs_of(items: list[tuple[str, str]]) -> list[ParsedRef]:
        out: list[ParsedRef] = []
        if ref_target is None:
            return out
        for href, text in items:
            target = ref_target(href)
            if target and all(r.href != target for r in out):
                out.append(ParsedRef(target, text, "cites"))
        return out

    def add(u: Unit, parent_eid: str | None, path: list[str], eid_base: str, top_kind: str, context: str | None) -> None:
        eid = unique(eid_base)
        children = u.children()
        own = u.own_text()
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=u.kind,
                num_path=path,
                top_kind=top_kind,
                heading=u.heading,
                text="" if u.repealed else u.text(),
                parent_eid=parent_eid,
                sort_order=len(provisions),
                status="repealed" if u.repealed else status,  # type: ignore[arg-type]
                valid_from=valid_from,
                valid_to=valid_to,
                extent=list(extent),
                note=note_text(u.notes),
                refs=refs_of(u.refs),
                is_leaf=not children,
                has_own_content=bool(own) and bool(children),
                context_heading=context,
            )
        )
        for child in children:
            add(child, eid, [*path, f"{child.tag}:{child.key}"], f"{eid}-{child.tag}{child.key}", top_kind, context)

    for unit, context in tops:
        label = f"{unit.tag}:{unit.key}" if unit.tag else unit.key
        base = f"{unit.tag}-{unit.key}" if unit.tag else unit.key
        add(unit, None, [label], base, unit.kind, context)
    return provisions


def key_of(label: str) -> str:
    """``(1)`` -> ``1``, ``a)`` -> ``a``, ``12a.`` -> ``12a``, ``3.a`` -> ``3_a`` (a language-neutral key)."""
    s = label.strip().strip("()").rstrip(")").rstrip(".")
    return re.sub(r"[^0-9A-Za-z]+", "_", s).strip("_").lower()
