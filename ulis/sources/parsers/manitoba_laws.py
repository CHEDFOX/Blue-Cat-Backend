"""Parser for Manitoba statutes (Manitoba Laws, King's Printer; HTML page of a C.C.S.M. Act's current consolidation).

``https://web2.gov.mb.ca/laws/statutes/ccsm/<id>.php?lang=en`` (for example ``h175``) lays the Act out in
"rows" (``div.regrow``), each with an English column (``regcol-e``) and a French column (``regcol-f``) that are
the same provision. Only the English column is read (the French column, which the site presents as the other
half of the same bilingual text, is not ingested yet).

Format notes:

* the page states ``This version is current as of October 12, 2026. It has been in effect since June 3,
  2025.``: the version date is the "in effect since" date and the currency date the "current as of" date. The
  page dates the version, not individual provisions, so the version is a snapshot (``validity_basis =
  revision_snapshot``) and earlier wordings are a history gap, not "not yet in force". The page says "This is
  an unofficial version"; the bilingual PDF is the official copy;
* one row is a section or one subsection of it (``secnum`` ``8(1.1)``); ``h6.secheading`` is its marginal note;
  ``p.def1`` is a definition, ``p.ind1`` a paragraph and ``p.ind2`` a subparagraph (or a paragraph of the
  definition above it); ``p.note`` lists the amending enactments (kept in ``note``); ``h2.part`` is a Part
  heading (context only);
* rows before the first section hold the preamble; the end note (coming into force) is not indexed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision
from ulis.sources.parsers.subnational_common import ws

PARSER_ID = "manitoba_laws_html"
PARSER_VERSION = "1.0.0"
SITE = "https://web2.gov.mb.ca"
JURISDICTION = "CA-MB"

SECNUM = re.compile(r"^(\d+(?:\.\d+)*[A-Za-z]?)((?:\([0-9A-Za-z.]+\))*)$")
SUBLABEL = re.compile(r"\([0-9A-Za-z.]+\)")
DATE_TEXT = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})")


class ManitobaParseError(ValueError):
    pass


def work_uri(chapter_id: str) -> str:
    return f"/ca/mb/act/{chapter_id.lower()}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map a Manitoba Laws C.C.S.M. Act URL to (ULIS work URI, eId)."""
    m = re.match(
        r"^https://web2\.gov\.mb\.ca/laws/statutes/ccsm/([a-z]\d{1,3}[a-z]?)(?:e)?\.php(?:\?.*)?(?:#.*)?$", uri
    )
    return (work_uri(m.group(1)), None) if m else None


@dataclass
class _Block:
    tag: str
    cls: str
    text: str
    secnum: str = ""
    clause: str = ""
    bold: str = ""


@dataclass
class _Row:
    id: str
    blocks: list[_Block] = field(default_factory=list)


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_Row] = []
        self.title: str = ""
        self.chapter: str = ""
        self.meta_text: list[str] = []
        self._divs: list[str] = []  # classes of the open <div>s
        self._row: _Row | None = None
        self._block: dict[str, object] | None = None
        self._span: str | None = None
        self._bold = False
        self._h1: list[str] = []
        self._h1_span = False
        self._in_h1 = False

    @property
    def _english(self) -> bool:
        return "regcol-e" in self._divs

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "div":
            self._divs.append(cls)
            if cls == "regrow":
                self._row = _Row(a.get("id") or "")
                self.rows.append(self._row)
            return
        if tag == "h1" and self._english and "title_en" in "".join(a.get("id") or "") + "title_en":
            self._in_h1 = True
        if self._in_h1 and tag == "span":
            self._h1_span = True
        if not self._english or self._row is None:
            return
        if tag in ("p", "h2", "h6"):
            self._block = {"tag": tag, "cls": cls, "text": [], "secnum": [], "clause": [], "bold": []}
        elif tag == "span" and self._block is not None and cls in ("secnum", "clause"):
            self._span = cls
        elif tag == "b" and self._block is not None:
            self._bold = True
        elif tag == "br" and self._block is not None:
            self._block["text"].append(" ")  # type: ignore[union-attr]

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._divs:
            cls = self._divs.pop()
            if cls == "regrow":
                self._row = None
            return
        if tag == "h1":
            self._in_h1 = False
            self._h1_span = False
        if tag == "span":
            self._span = None
        elif tag == "b":
            self._bold = False
        elif tag in ("p", "h2", "h6") and self._block is not None and self._row is not None:
            b = self._block
            self._row.blocks.append(
                _Block(
                    str(b["tag"]),
                    str(b["cls"]),
                    ws("".join(b["text"])),  # type: ignore[arg-type]
                    ws("".join(b["secnum"])),  # type: ignore[arg-type]
                    ws("".join(b["clause"])),  # type: ignore[arg-type]
                    ws("".join(b["bold"])),  # type: ignore[arg-type]
                )
            )
            self._block = None

    def handle_data(self, data: str) -> None:
        if self._in_h1:
            if self._h1_span:
                self.chapter += data
            else:
                self.title += data
            return
        self.meta_text.append(data)
        if self._block is None:
            return
        if self._span == "secnum":
            self._block["secnum"].append(data)  # type: ignore[union-attr]
        elif self._span == "clause":
            self._block["clause"].append(data)  # type: ignore[union-attr]
        else:
            self._block["text"].append(data)  # type: ignore[union-attr]
            if self._bold:
                self._block["bold"].append(data)  # type: ignore[union-attr]


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", label.lower().strip("() ")).strip("-")


@dataclass
class _Node:
    kind: str
    label: str
    heading: str | None = None
    lines: list[str] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)
    note: list[str] = field(default_factory=list)
    repealed: bool = False

    def set_text(self, text: str) -> None:
        if re.match(r"^\[?Repealed\b", text):
            self.repealed = True
            self.note.append(text)
        elif text:
            self.lines.append(text)


def _date(text: str) -> date:
    return datetime.strptime(text, "%B %d, %Y").date()


def parse_manitoba_laws(content: bytes) -> list[ParsedDocument]:
    html = content.decode("utf-8", "replace")
    page = _Page()
    page.feed(html)
    title = ws(page.title).rstrip(",").strip()
    chapter = ws(page.chapter)
    cm = re.match(r"^C\.C\.S\.M\. c\. ([A-Z]\d{1,3}[A-Za-z]?)$", chapter)
    meta = ws(" ".join(page.meta_text))
    current_m = re.search(r"This version is current as of\s+([A-Z][a-z]+ \d{1,2}, \d{4})", meta)
    since_m = re.search(r"It has been in effect since\s+([A-Z][a-z]+ \d{1,2}, \d{4})", meta)
    if not title or cm is None or current_m is None or since_m is None:
        raise ManitobaParseError("not a Manitoba Laws C.C.S.M. Act page (title, chapter or version dates missing)")
    chapter_id = cm.group(1)
    version_date, currency = _date(since_m.group(1)), _date(current_m.group(1))
    assent_m = re.search(r"\(Assented to ([A-Z][a-z]+ \d{1,2}, \d{4})\)", meta)

    nodes: list[_Node] = []
    preamble = _Node("preamble", "")
    section: _Node | None = None
    sub: _Node | None = None
    row_node: _Node | None = None
    defn = ind1 = None
    part: str | None = None
    ctx_of: dict[int, str | None] = {}
    seen_section = False

    for row in page.rows:
        if row.id == "mainTOCdiv":  # the table of contents
            continue
        blocks = [b for b in row.blocks if b.cls not in ("end_note",)]
        heading = next((b.text for b in blocks if b.cls == "secheading"), None)
        for b in blocks:
            if b.tag == "h2" and b.cls == "part":
                part = b.text
                continue
            if b.secnum:
                m = SECNUM.match(b.secnum.replace(" ", ""))
                if m is None:
                    raise ManitobaParseError(f"unrecognised section number {b.secnum!r}")
                number, subs = m.group(1), SUBLABEL.findall(m.group(2))
                seen_section = True
                if section is None or section.label != number:
                    section = _Node("section", number)
                    nodes.append(section)
                    ctx_of[id(section)] = part
                    sub = None
                row_node = section
                if subs:
                    sub = _Node("subsection", subs[0])
                    section.children.append(sub)
                    row_node = sub
                    if len(subs) > 1:
                        row_node = _Node("paragraph", subs[1])
                        sub.children.append(row_node)
                if heading and row_node.heading is None:
                    row_node.heading = heading
                defn = ind1 = None
                row_node.set_text(b.text)
            elif b.cls == "def1" and row_node is not None:
                term = b.bold.strip('"“” ')
                defn = _Node("definition", f"def:{term}")
                row_node.children.append(defn)
                ind1 = None
                defn.set_text(b.text)
            elif b.cls == "ind1" and row_node is not None:
                ind1 = _Node("paragraph", b.clause.strip())
                row_node.children.append(ind1)
                defn = None
                ind1.set_text(b.text)
            elif b.cls == "ind2" and row_node is not None:
                if defn is not None:
                    node = _Node("paragraph", b.clause.strip())
                    defn.children.append(node)
                elif ind1 is not None:
                    node = _Node("subparagraph", b.clause.strip())
                    ind1.children.append(node)
                else:
                    node = _Node("paragraph", b.clause.strip())
                    row_node.children.append(node)
                node.set_text(b.text)
            elif b.cls == "note" and row_node is not None:
                row_node.note.append(b.text)
            elif b.tag == "p" and not seen_section and b.text and b.cls != "left":  # "(Assented to ...)" is not text
                label = b.clause.strip()
                preamble.lines.append(f"{label} {b.text}".strip())
            elif b.tag == "p" and row_node is not None and b.text and b.cls in ("", "left"):
                (ind1 or defn or row_node).lines.append(b.text)

    if preamble.lines:
        nodes.insert(0, preamble)
    if not [n for n in nodes if n.kind == "section"]:
        raise ManitobaParseError("no sections found")

    provisions: list[ParsedProvision] = []
    used: dict[str, int] = {}

    def text_of(node: _Node) -> str:
        lines = list(node.lines)
        for child in node.children:
            label = child.label if not child.label.startswith("def:") else ""
            body = text_of(child)
            lines.append(f"{label} {body}".strip() if label else body)
        return "\n".join(line for line in lines if line)

    def flatten(node: _Node, parent_eid: str | None, num_path: list[str], top_kind: str, ctx: str | None) -> None:
        if node.kind == "definition":
            local = f"def-{_slug(node.label[4:]) or len(provisions)}"
        elif node.kind == "section":
            local = f"section-{_slug(node.label)}"
        elif node.kind == "preamble":
            local = "preamble"
        else:
            local = _slug(node.label) or f"{node.kind}{len(provisions)}"
        eid = f"{parent_eid}-{local}" if parent_eid else local
        n = used.get(eid, 0) + 1
        used[eid] = n
        eid = eid if n == 1 else f"{eid}-{n}"
        path = [*num_path, node.label] if node.kind != "preamble" else []
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=node.kind,
                num_path=path,
                top_kind=top_kind,
                heading=node.heading,
                text="" if node.repealed else text_of(node),
                parent_eid=parent_eid,
                sort_order=len(provisions),
                status="repealed" if node.repealed else "in_force",
                valid_from=version_date,
                valid_to=None,
                extent=[JURISDICTION],
                note=" ".join(node.note)[:2000] or None,
                refs=[],
                is_leaf=not node.children or node.repealed,
                has_own_content=bool(node.lines) and bool(node.children) and not node.repealed,
                context_heading=ctx,
            )
        )
        if not node.repealed:
            for child in node.children:
                flatten(child, eid, path, top_kind, ctx)

    for node in nodes:
        flatten(node, None, [], node.kind, ctx_of.get(id(node)))

    uri = work_uri(chapter_id)
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type="act",
            jurisdiction_id=JURISDICTION,
            work_uri=uri,
            expression_uri=f"{uri}/en@{version_date.isoformat()}",
            work_title=title,
            title=title,
            short_title=title,
            number=f"C.C.S.M. c. {chapter_id}",
            work_date=_date(assent_m.group(1)) if assent_m else None,
            language="en",
            authoritative=False,
            text_origin="official_consolidation",
            consolidation_kind="point_in_time",
            version_date=version_date,
            currency_date=currency,
            extent=[JURISDICTION],
            ext_module="ca_prov",
            ext={
                "province": "MB",
                "provincial_id": chapter_id.lower(),
                "statute_citation": f"CCSM c {chapter_id}",
            },
            outstanding_changes=[],
            events=[ParsedEvent("version_change", version_date, None, None, "in effect since")],
            provisions=provisions,
            validity_basis="revision_snapshot",
        )
    ]
