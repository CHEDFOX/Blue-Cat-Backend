"""Helpers shared by the parsers of United States state codes (Washington, Texas, Oregon, Utah).

* a tolerant HTML tree (the legislatures' pages are not well-formed XML),
* whitespace handling (runs of spaces collapse; nothing else in the text is altered),
* the conventions every state document follows: instrument type ``act`` (a code, title or chapter of a
  state's statutes), jurisdiction ``US-<postal code>``, extent that jurisdiction only, English, not
  authoritative (the online text is not the state's authenticated edition), dated by the retrieval date
  with ``validity_basis = "attested_at_edition"``: the publisher presents the text as current, and no
  provision start dates are stated unless a parser says otherwise.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from ulis.sources.normalized import ParsedDocument, ParsedProvision, ParsedRef
from ulis.sources.us_state_bundle import StateBundle

VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "wbr"})
NOTE_LIMIT = 4000


class StateParseError(ValueError):
    pass


# --------------------------------------------------------------------------- tolerant DOM


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

    def iter(self) -> Any:
        """Depth-first iteration over this node and its descendant elements."""
        stack: list[Node] = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.elements()))

    def find_all(self, pred: Callable[[Node], bool]) -> list[Node]:
        return [n for n in self.iter() if n is not self and pred(n)]

    def find(self, pred: Callable[[Node], bool]) -> Node | None:
        return next((n for n in self.iter() if n is not self and pred(n)), None)


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


def parse_html(text: str) -> Node:
    builder = _Builder()
    builder.feed(text)
    builder.close()
    return builder.root


# --------------------------------------------------------------------------- text


def ws(text: str) -> str:
    """Collapse runs of whitespace (including no-break spaces) into one space."""
    return re.sub(r"[\s\xa0​﻿]+", " ", text).strip()


def raw_text(node: Node | str, *, skip: Callable[[Node], bool] | None = None, br: str = "\n") -> str:
    """The text of an element; ``<br>`` becomes ``br``; elements for which ``skip`` is true are left out."""
    parts: list[str] = []

    def walk(el: Node) -> None:
        for child in el.children:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag in ("script", "style") or skip is not None and skip(child):
                continue
            elif child.tag == "br":
                parts.append(br)
            else:
                walk(child)

    if isinstance(node, str):
        return node
    walk(node)
    return "".join(parts)


def lines_of(text: str) -> str:
    """Whitespace-collapsed lines, empty lines dropped."""
    return "\n".join(line for line in (ws(x) for x in text.split("\n")) if line)


def join_note(parts: list[str]) -> str | None:
    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    text = " ".join(seen)
    return text[:NOTE_LIMIT] or None


# --------------------------------------------------------------------------- documents


@dataclass
class SectionDraft:
    """A section as the state parsers collect it; :func:`build_provisions` turns drafts into provisions."""

    kind: str  # "section" or "article"
    number: str  # as printed: "19.02", "9A.32.030", "76-5-203"
    heading: str | None
    lines: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    refs: list[ParsedRef] = field(default_factory=list)
    repealed: bool = False
    context: str | None = None
    valid_from: Any = None
    valid_to: Any = None
    status: str | None = None  # overrides the default in-force / repealed


def build_provisions(drafts: list[SectionDraft], jurisdiction: str, *, id_prefix: str = "") -> list[ParsedProvision]:
    """One leaf provision per section.

    Versions the source dates (a start or an end) share the section's eId, so they are versions of one
    provision. A number the source prints more than once *without* dates (conflicting amendments, a text
    and its replacement) gets a numbered eId and a note, never a merge.
    """
    provisions: list[ParsedProvision] = []
    undated_seen: dict[str, int] = {}
    for draft in drafts:
        base = f"{id_prefix}{draft.kind[:3]}-{re.sub(r'[^A-Za-z0-9.-]', '_', draft.number)}"
        notes = list(draft.notes)
        if draft.valid_from is not None or draft.valid_to is not None:
            eid = base
        else:
            undated_seen[base] = undated_seen.get(base, 0) + 1
            eid = base if undated_seen[base] == 1 else f"{base}-v{undated_seen[base]}"
            if undated_seen[base] > 1:
                notes.append(
                    "Ambiguous pinpoint: the source prints this number more than once (versions or conflicting "
                    "amendments); identify the passage by its text."
                )
        status = "repealed" if draft.repealed else (draft.status or "in_force")
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=draft.kind,
                num_path=[draft.number],
                top_kind=draft.kind,
                heading=draft.heading,
                text="" if draft.repealed else "\n".join(draft.lines),
                parent_eid=None,
                sort_order=len(provisions),
                status=status,  # type: ignore[arg-type]
                valid_from=draft.valid_from,
                valid_to=draft.valid_to,
                extent=[jurisdiction],
                note=join_note(notes),
                refs=draft.refs,
                is_leaf=True,
                has_own_content=False,
                context_heading=draft.context,
            )
        )
    return provisions


def state_document(
    bundle: StateBundle,
    *,
    jurisdiction: str,
    module: str,
    work_uri: str,
    work_title: str,
    title: str,
    short_title: str | None,
    number: str | None,
    ext: dict[str, Any],
    provisions: list[ParsedProvision],
    edition: str,
    text_origin: str = "official_consolidation",
) -> ParsedDocument:
    if not provisions:
        raise StateParseError(f"{work_uri}: no sections found")
    retrieved = bundle.retrieved_on
    return ParsedDocument(
        doc_kind="legislation",
        instrument_type="act",
        jurisdiction_id=jurisdiction,
        work_uri=work_uri,
        expression_uri=f"{work_uri}/en@{retrieved.isoformat()}/{edition}",
        work_title=work_title,
        title=title,
        short_title=short_title,
        number=number,
        work_date=None,
        language="en",
        authoritative=False,
        text_origin=text_origin,
        consolidation_kind="state_code_online",
        version_date=retrieved,
        currency_date=retrieved,
        extent=[jurisdiction],
        ext_module=module,
        ext=ext,
        provisions=provisions,
        validity_basis="attested_at_edition",
    )
