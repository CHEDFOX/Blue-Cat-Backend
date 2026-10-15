"""Parser for Ontario statutes (e-Laws, King's Printer for Ontario; HTML page of the current consolidation).

``https://www.ontario.ca/laws/statute/<id>`` (for example ``90c43``) is a flat stream of paragraphs whose CSS
class gives their level: ``section`` (the section number is in a leading ``<strong>`` and the first subsection
``(1)`` follows in the same paragraph), ``subsection``, ``paragraph``, ``subpara``, ``subsubpara``,
``definition``; ``headnote`` is the marginal note of the section or subsection that follows; ``partnum`` and
``heading1`` are Part and group headings (context only). The structure is rebuilt from those classes.

Format notes:

* the page states the consolidation period (``Consolidation Period: From December 11, 2025 to the e-Laws
  currency date``): the date from which this consolidation applies. It is the version date;
* after each section, ``Section Amendments with date in force (d/m/y)`` lists the amendments to the section with
  the date each came into force (or "not in force"). A section's current wording applies from the latest
  in-force date listed; a section without a dated amendment has NO start date (``valid_from`` None: the source
  does not say when it took effect). Amendments "not in force" are outstanding changes;
* the legislative history printed at the end of a section's last paragraph (``R.S.O. 1990, c. C.43, s. 1;
  1993, c. 27, Sched.``) is kept in ``note``, never in the text; it is recognised by its separator (a
  non-breaking space and a space, or two spaces) before an enactment citation;
* ``Repealed`` provisions keep their number: status ``repealed``, empty text, the repeal note in ``note``;
* a schedule is one provision whose text is the paragraphs that follow its ``schedule`` heading (Ontario
  schedules are free text); tables are rendered row by row;
* the French text, regulations and earlier consolidation periods are not parsed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParsedRef
from ulis.sources.parsers.subnational_common import ws

PARSER_ID = "ontario_elaws_html"
PARSER_VERSION = "1.0.0"
SITE = "https://www.ontario.ca"
JURISDICTION = "CA-ON"

HISTORY = re.compile(r"(?:\xa0 | {2,}|\xa0{2,})(?=(?:R\.S\.O\.|S\.O\.|\d{4},\s*c\.|O\.\s*Reg\.|See:))")
DEFINED_TERM = re.compile(r"^[“\"]([^”\"]+)[”\"]")
CHAPTER = re.compile(r"^(R\.S\.O\.|S\.O\.)\s*(\d{4}),\s*CHAPTER\s+(.+)$", re.I)
IN_FORCE_DATE = re.compile(r"-\s*(\d{2})/(\d{2})/(\d{4})")
LABELLED = re.compile(r"^(\([0-9A-Za-z.]+\))\s*(.*)$", re.S)
LEVELS = {"subsection": 2, "paragraph": 3, "subpara": 4, "subsubpara": 5}
CONTEXT_HEADINGS = {"heading1", "heading1x"}
STATUTE_URL = re.compile(
    r"^https?://(?:www\.)?ontario\.ca/laws/statute/([0-9]{2}[a-z][0-9]{2}[a-z]?|s[0-9]{5})(?:#.*)?$", re.I
)


class OntarioParseError(ValueError):
    pass


def work_uri(statute_id: str) -> str:
    return f"/ca/on/act/{statute_id.lower()}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map an e-Laws statute URL to (ULIS work URI, eId)."""
    m = STATUTE_URL.match(uri)
    return (work_uri(m.group(1)), None) if m else None


@dataclass
class _Para:
    cls: str
    text: str  # raw text, non-breaking spaces kept
    strong: str
    hrefs: list[str]
    table: list[list[str]] | None = None


class _Page(HTMLParser):
    """Collects the paragraphs and tables of the page (tables of contents dropped)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[_Para] = []
        self._p: dict[str, list[str]] | None = None
        self._cls = ""
        self._table: list[list[str]] | None = None
        self._table_is_toc = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._strong = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "table":
            self._table, self._table_is_toc = [], False
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "p":
            self._cls = re.sub(r"-e$", "", a.get("class") or "")  # older pages use a "-e" (English) variant
            if self._table is not None and self._cls.startswith("TOC"):
                self._table_is_toc = True
            self._p = {"text": [], "strong": [], "hrefs": []}
        elif tag == "br" and self._p is not None:
            self._p["text"].append(" ")
        elif tag == "strong" and self._p is not None:
            self._strong = True
        elif tag == "a" and self._p is not None and a.get("href"):
            self._p["hrefs"].append(a["href"] or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "strong":
            self._strong = False
        elif tag == "p" and self._p is not None:
            text = "".join(self._p["text"])
            if self._cell is not None:
                self._cell.append(text)
            elif self._table is None:
                self.items.append(_Para(self._cls, text, "".join(self._p["strong"]), list(self._p["hrefs"])))
            self._p = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(ws(" ".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if not self._table_is_toc and any(any(c for c in r) for r in self._table):
                self.items.append(_Para("table", "", "", [], table=self._table))
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._p is not None:
            self._p["text"].append(data)
            if self._strong:
                self._p["strong"].append(data)


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", label.lower().strip("() ")).strip("-")


@dataclass
class _Node:
    kind: str
    label: str
    heading: str | None = None
    items: list[str | _Node] = field(default_factory=list)  # lines of text and child nodes, in document order
    history: str | None = None
    repealed: bool = False
    refs: list[str] = field(default_factory=list)
    note: list[str] = field(default_factory=list)

    @property
    def lines(self) -> list[str]:
        return [i for i in self.items if isinstance(i, str)]

    @property
    def children(self) -> list[_Node]:
        return [i for i in self.items if isinstance(i, _Node)]

    def set_text(self, text: str, history: str | None) -> None:
        if re.match(r"^Repealed\b", text):
            self.repealed = True
            self.note.append(text)
        elif text:
            self.items.append(text)
        if history:
            self.history = history if self.history is None else f"{self.history} {history}"


def _body_text(p: _Para) -> tuple[str, str | None]:
    m = HISTORY.search(p.text)
    text, hist = (p.text[: m.start()], p.text[m.end() :]) if m else (p.text, None)
    return ws(text), (ws(hist) if hist else None)


def parse_ontario_elaws(content: bytes) -> list[ParsedDocument]:
    html = content.decode("utf-8", "replace")
    page = _Page()
    page.feed(html)
    items = page.items
    title_p = next((i for i in items if i.cls == "shorttitle"), None)
    chapter_p = next((i for i in items if i.cls == "chapter"), None)
    period_p = next((i for i in items if i.cls == "ConsolidationPeriod"), None)
    url_m = re.search(r'property="og:url"\s+content="https?://(?:www\.)?ontario\.ca/laws/statute/([A-Za-z0-9]+)"', html)
    if title_p is None or chapter_p is None or period_p is None or url_m is None:
        raise OntarioParseError("not an e-Laws statute page (title, chapter, consolidation period or id missing)")
    title = ws(title_p.text)
    cm = CHAPTER.match(ws(chapter_p.text))
    if not title or cm is None:
        raise OntarioParseError(f"unrecognised chapter line {ws(chapter_p.text)!r}")
    series, year = cm.group(1).replace(".", "").upper(), cm.group(2)
    number = re.sub(r"[\s,]*Sched(?:ule|\.)?\s*", ", Sch ", cm.group(3), flags=re.I).strip()
    period = ws(period_p.text)
    pm = re.search(r"From\s+([A-Z][a-z]+ \d{1,2}, \d{4})", period)
    if pm is None:
        raise OntarioParseError(f"no consolidation period start in {period!r}")
    version_date = datetime.strptime(pm.group(1), "%B %d, %Y").date()
    if re.search(r"\bto\s+(?!the e-Laws currency date)[A-Z][a-z]+ \d{1,2}, \d{4}", period):
        raise OntarioParseError("a consolidation period that has ended (an earlier version) is not supported")
    uri = work_uri(url_m.group(1))

    body_start = next((n for n, i in enumerate(items) if i.cls == "toc"), 0)
    nodes: list[_Node] = []  # top level: sections and schedules
    section: _Node | None = None
    sub = defn = para = subpara = subsub = None  # the open node at each level
    schedule: _Node | None = None
    pending: list[str] = []
    context: str | None = None
    part: str | None = None
    ctx_of: dict[int, str | None] = {}
    section_dates: dict[int, list[date]] = {}
    not_in_force: dict[int, list[str]] = {}

    def make(kind: str, label: str, parent: _Node | None) -> _Node:
        node = _Node(kind, label)
        if parent is not None:
            parent.items.append(node)
        else:
            nodes.append(node)
        return node

    def innermost() -> _Node | None:
        return subsub or subpara or para or defn or sub or section

    for p in items[body_start:]:
        cls = p.cls
        if schedule is not None and cls not in ("footnoteLeft", "footnote", "table"):
            text, _ = _body_text(p)
            if text:
                schedule.items.append(text)
            continue
        if cls == "partnum":
            part, context = ws(p.text), None
        elif cls in CONTEXT_HEADINGS:
            context = ws(p.text)
        elif cls == "headnote":
            pending.append(ws(p.text))
        elif cls == "schedule":
            schedule = make("schedule", ws(re.sub(r"(?i)^schedule\b", "", ws(p.text))), None)
            section = sub = defn = para = subpara = subsub = None
            ctx_of[id(schedule)] = None
        elif cls == "section":
            schedule = None
            rest = p.text.split(p.strong, 1)[1] if p.strong and p.strong in p.text else p.text
            text, hist = _body_text(_Para(cls, rest, p.strong, p.hrefs))
            section = make("section", ws(p.strong).rstrip("."), None)
            section.heading = pending.pop(0) if pending else None
            ctx_of[id(section)] = " — ".join(x for x in (part, context) if x) or None
            sub = defn = para = subpara = subsub = None
            target = section
            m = LABELLED.match(text)
            if m:
                target = sub = make("subsection", m.group(1), section)
                target.heading = pending.pop(0) if pending else None
                text = m.group(2)
            target.set_text(text, hist)
            target.refs.extend(h for h in p.hrefs if resolve_uri(h))
        elif (cls in LEVELS or cls == "definition") and section is not None:
            text, hist = _body_text(p)
            if cls == "definition":
                dm = DEFINED_TERM.match(text)
                node = defn = make("definition", f"def:{dm.group(1) if dm else ''}", sub or section)
                para = subpara = subsub = None
            else:
                m = LABELLED.match(text)
                label, text = (m.group(1), m.group(2)) if m else ("", text)
                level = LEVELS[cls]
                if not label and level > 2:  # closing words of the paragraphs above (no number of their own)
                    parents = (subpara, para, defn, sub, section)[5 - level :]
                    holder: _Node | None = next((n for n in parents if n is not None), section)
                    if holder is not None:
                        holder.set_text(text, hist)
                    continue
                if level == 2:
                    node = sub = make("subsection", label, section)
                    node.heading = pending.pop(0) if pending else None
                    defn = para = subpara = subsub = None
                elif level == 3:
                    node = para = make("paragraph", label, defn or sub or section)
                    subpara = subsub = None
                elif level == 4:
                    node = subpara = make("subparagraph", label, para or defn or sub or section)
                    subsub = None
                else:
                    node = subsub = make("subsubparagraph", label, subpara or para or defn or sub or section)
            node.set_text(text, hist)
            node.refs.extend(h for h in p.hrefs if resolve_uri(h))
        elif cls == "table":
            holder = schedule or innermost()
            if holder is not None and p.table:
                holder.items.append("\n".join(" | ".join(r) for r in p.table))
        elif cls in ("footnote", "Pnote"):
            if section is not None:
                section.note.append(ws(p.text))
        elif cls == "footnoteLeft":
            if section is None or schedule is not None:
                continue
            text = ws(p.text)
            if text.startswith("Section Amendments with date in force"):
                continue
            for chunk in text.split(";"):
                m = IN_FORCE_DATE.search(chunk)
                if m:
                    d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                    section_dates.setdefault(id(section), []).append(d)
                elif re.search(r"-\s*not in force", chunk):
                    not_in_force.setdefault(id(section), []).append(ws(re.sub(r"-\s*not in force.*", "", chunk)))
        elif cls in ("paranoindt", "leadText"):
            holder = innermost()
            text, _ = _body_text(p)
            if holder is not None and text:
                holder.items.append(text)
        # other classes (captions of tables, spacing) carry no law text

    if not nodes:
        raise OntarioParseError("no provisions found")

    provisions: list[ParsedProvision] = []
    used: dict[str, int] = {}

    def text_of(node: _Node) -> str:
        lines: list[str] = []
        for item in node.items:
            if isinstance(item, str):
                lines.append(item)
            else:
                label = item.label if not item.label.startswith("def:") else ""
                body = text_of(item)
                lines.append(f"{label} {body}".strip() if label else body)
        return "\n".join(line for line in lines if line)

    def flatten(
        node: _Node, parent_eid: str | None, num_path: list[str], top_kind: str, start: date | None, ctx: str | None
    ) -> str:
        if node.kind == "definition":
            local = f"def-{_slug(node.label[4:])[:60] or len(provisions)}"
        elif node.kind == "section":
            local = f"section-{_slug(node.label)}"
        elif node.kind == "schedule":
            local = "schedule" + (f"-{_slug(node.label)}" if node.label else "")
        else:
            local = _slug(node.label) or f"{node.kind}{len(provisions)}"
        eid = f"{parent_eid}-{local}" if parent_eid else local
        n = used.get(eid, 0) + 1
        used[eid] = n
        eid = eid if n == 1 else f"{eid}--{n}"  # "--" cannot occur in a label slug
        path = [*num_path, node.label]
        note = " ".join(x for x in [*node.note, node.history or ""] if x)
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
                valid_from=start,
                valid_to=None,
                extent=[JURISDICTION],
                note=note[:2000] or None,
                refs=[ParsedRef(h, "", "cites") for h in dict.fromkeys(node.refs)],
                is_leaf=not node.children or node.repealed,
                has_own_content=bool(node.lines) and bool(node.children) and not node.repealed,
                context_heading=ctx,
            )
        )
        if not node.repealed:
            for child in node.children:
                flatten(child, eid, path, top_kind, start, ctx)
        return eid

    outstanding: list[dict[str, object]] = []
    for node in nodes:
        dates = section_dates.get(id(node), [])
        eid = flatten(node, None, [], node.kind, max(dates) if dates else None, ctx_of.get(id(node)))
        for label in not_in_force.get(id(node), []):
            outstanding.append(
                {
                    "affects_eid": eid,
                    "affected_label": node.label,
                    "by_uri": None,
                    "by_title": label or None,
                    "description": f"Amendment not in force: {label}",
                }
            )

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
            number=f"{year}, c. {number}",
            work_date=None,
            language="en",
            authoritative=False,
            text_origin="official_consolidation",
            consolidation_kind="point_in_time",
            version_date=version_date,
            currency_date=None,
            extent=[JURISDICTION],
            ext_module="ca_prov",
            ext={
                "province": "ON",
                "provincial_id": url_m.group(1).lower(),
                "statute_citation": f"{series} {year}, c {number}",
            },
            outstanding_changes=outstanding,
            events=[ParsedEvent("version_change", version_date, None, None, "Consolidation Period")],
            provisions=provisions,
            validity_basis="source_periods",
        )
    ]
