"""Parser for Acts of the Oireachtas as enacted, from the electronic Irish Statute Book (eISB).

The XML (``https://www.irishstatutebook.ie/eli/<year>/act/<number>/enacted/en/xml``, Office of
the Attorney General; re-use under the Oireachtas (Open Data) PSI Licence, i.e. CC BY 4.0) is the
text of the Act as passed. It is a *presentational* format, not Akoma Ntoso:

* ``metadata`` carries the title, the Act number, the year and ``dateofenactment`` (``YYYYMMDD``);
* ``body`` holds ``sect`` elements (optionally inside ``part`` / ``chapter``), ``backmatter`` holds the
  ``schedule`` elements (which may contain ``part``, tables and ``l1`` articles);
* a section is a flat run of ``<p>`` lines. The section number is printed in the first line and the
  structure of subsections ``(1)``, paragraphs ``(a)``, subparagraphs ``(i)``, clauses ``(I)`` and
  subclauses ``(A)`` exists only as printed labels, so it is rebuilt here from the labels (with the
  printed left indent, the ``class`` attribute, to separate the roman ``(i)`` from the letter ``(i)``);
* special characters are empty elements (``<emdash/>``, ``<odq/>``, ``<afada/>``, ``<unicode ch=.../>``);
* footnotes are inline ``<fn>`` elements; they go to ``note``, never to the text.

The file carries no commencement information and no amendments: it is the Act as enacted. Every
provision is therefore dated from the enactment date (``validity_basis = enactment_date_presumed``)
and the parser reports the missing amendments and commencement orders as outstanding changes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParsedRef

PARSER_ID = "eisb_act_xml"
PARSER_VERSION = "1.0.0"
SITE = "https://www.irishstatutebook.ie"

EMPTY_CHARS = {
    "emdash": "—",
    "odq": "“",
    "cdq": "”",
    "osq": "‘",
    "csq": "’",
    "pound": "£",
    "euro": "€",
    "nbsp": " ",
}
# Elements that carry no text (rules, images, column definitions).
NO_TEXT = {"hr1", "graphic", "col", "colgroup"}
FADA = re.compile(r"^([aeiouAEIOU])fada$")


class IrishParseError(ValueError):
    pass


# The one DOCTYPE form the publisher uses (some Acts): an external DTD reference and nothing else.
# The DTD is never fetched; any other DOCTYPE (internal subset, entities, another root) is rejected.
_BARE_DOCTYPE = re.compile(rb'\A(\xef\xbb\xbf)?(<\?xml[^>]*\?>\s*)?<!DOCTYPE\s+act\s+SYSTEM\s+"legislation\.dtd"\s*>')


def parse_xml(content: bytes) -> Element:
    m = _BARE_DOCTYPE.match(content)
    if m:
        content = (m.group(1) or b"") + (m.group(2) or b"") + content[m.end() :]
    try:
        return fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception as exc:
        raise IrishParseError(f"not well-formed or unsafe XML: {exc}") from exc


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9.]+", "-", ascii_text.lower()).strip("-")


def act_uri(year: int | str, number: int | str) -> str:
    return f"/ie/act/{int(year)}/{int(number)}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map an eISB ELI URL of an Act (``/eli/<year>/act/<n>/...``) to (ULIS work URI, eId)."""
    m = re.match(
        r"^https?://(?:www\.)?irishstatutebook\.ie/eli/(\d{4})/act/(\d+)(?:/enacted/en(?:/(?:html|xml))?)?/?$", uri
    )
    return (act_uri(m.group(1), m.group(2)), None) if m else None


# ----------------------------------------------------------------------------- inline text


def _raw(el: Element, notes: list[str] | None = None, refs: list[tuple[str, str]] | None = None) -> str:
    """Flattened text of an element: special characters resolved, footnotes diverted to ``notes``."""
    tag = el.tag if isinstance(el.tag, str) else ""
    if tag == "fn":
        if notes is not None:
            marker = el.find("marker")
            mark = _ws(_raw(marker)) if marker is not None else ""
            body = _ws(" ".join(_raw(p) for p in el.findall("p")))
            if mark and body.startswith(mark):
                body = body[len(mark) :].strip()
            notes.append(f"[{mark}] {body}" if mark else body)
        return ""
    if tag in NO_TEXT:
        return ""
    if tag in EMPTY_CHARS:
        return EMPTY_CHARS[tag]
    if tag == "unicode":
        code = el.get("ch") or ""
        try:
            return chr(int(code, 16))
        except ValueError as exc:
            raise IrishParseError(f"bad <unicode ch={code!r}/>") from exc
    fada = FADA.match(tag)
    if fada:
        return unicodedata.normalize("NFC", fada.group(1) + "́")
    if tag == "xref" and refs is not None and (el.get("href") or "").startswith("#"):
        refs.append((el.get("href") or "", _ws("".join(el.itertext()))))
    if (
        tag
        not in (
            "p",
            "i",
            "b",
            "font",
            "xref",
            "su",
            "pc",
            "td",
            "tr",
            "table",
            "tbody",
            "title",
            "number",
            "l1",
            "sectionreference",
            "marker",
        )
        and len(el) == 0
        and not (el.text or "").strip()
    ):
        raise IrishParseError(f"unsupported empty element <{tag}/>")
    parts = [el.text or ""]
    for child in el:
        parts.append(_raw(child, notes, refs))
        parts.append(child.tail or "")
    return "".join(parts)


def inline_text(el: Element, notes: list[str] | None = None, refs: list[tuple[str, str]] | None = None) -> str:
    # Some Acts are pretty-printed with line breaks inside the quotation marks (<odq/>\nterm\n<cdq/>): the
    # marks hug the quoted words, as printed.
    text = _ws(_raw(el, notes, refs))
    return re.sub(r"\s+\u201d", "\u201d", re.sub(r"\u201c\s+", "\u201c", text))


def _left_indent(el: Element) -> int:
    m = re.match(r"^\s*-?\d+\s+(-?\d+)", el.get("class") or "")
    return int(m.group(1)) if m else 0


def _table_lines(table: Element) -> list[str]:
    rows: list[str] = []
    for tr in table.iter("tr"):
        cells = [inline_text(td) for td in tr if td.tag == "td"]
        if any(cells):
            rows.append(" | ".join(cells))
    return rows


# ----------------------------------------------------------------------------- labels

ROMAN = {
    "i",
    "ii",
    "iii",
    "iv",
    "v",
    "vi",
    "vii",
    "viii",
    "ix",
    "x",
    "xi",
    "xii",
    "xiii",
    "xiv",
    "xv",
    "xvi",
    "xvii",
    "xviii",
    "xix",
    "xx",
    "xxi",
    "xxii",
}
AMBIGUOUS = {"i": "h", "v": "u", "x": "w"}  # roman numeral that is also the next letter after ...
DEPTH = {"num": 1.0, "definition": 1.5, "alpha": 2.0, "roman": 3.0, "ROMAN": 4.0, "ALPHA": 5.0}
KIND_NAME = {
    "num": "subsection",
    "alpha": "paragraph",
    "roman": "subparagraph",
    "ROMAN": "clause",
    "ALPHA": "subclause",
    "definition": "definition",
}
LABEL_RE = re.compile(r"^\(([A-Za-z0-9]{1,5})\)\s*")
SECTION_NO_RE = re.compile(r"^\s*(\d+[A-Z]{0,2})\s*\.\s*(?:—\s*)?")
DEF_LEAD = re.compile(r"^(?:In|For the purposes of)\b[^“”]{0,250}[—:]$")
DEF_TERM = re.compile(r"^“([^”]{1,120})”")


def _label_kind(token: str) -> str | None:
    if re.fullmatch(r"\d{1,3}[A-Z]{0,2}", token):
        return "num"
    if re.fullmatch(r"[a-z]+", token):
        if token in ROMAN and token not in AMBIGUOUS:
            return "roman"
        if token in AMBIGUOUS:
            return "ambiguous"
        if len(token) <= 2:
            return "alpha"
        return None
    if re.fullmatch(r"[A-Z]+", token):
        low = token.lower()
        if low in ROMAN and low not in AMBIGUOUS:
            return "ROMAN"
        if low in AMBIGUOUS:
            return "ambiguous-upper"
        if len(token) <= 2 and len(set(token)) == 1:
            return "ALPHA"
        return None
    return None


@dataclass
class _Node:
    kind: str  # section | subsection | paragraph | ... | definition | schedule | schedule_part | long_title
    key: str  # language-neutral label: 60, (1), (a), def:term, part:1
    printed: str  # label as printed, used to rebuild composite text
    indent: int
    depth: float
    heading: str | None = None
    items: list[str | _Node] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    refs: list[tuple[str, str]] = field(default_factory=list)
    def_mode: bool = False
    parent: _Node | None = None

    @property
    def children(self) -> list[_Node]:
        return [i for i in self.items if isinstance(i, _Node)]

    @property
    def own_lines(self) -> list[str]:
        return [i for i in self.items if isinstance(i, str)]


def _render(node: _Node) -> str:
    lines: list[str] = []
    for item in node.items:
        if isinstance(item, str):
            lines.append(item)
        else:
            body = _render(item)
            if item.kind == "definition" or not item.printed:
                lines.append(body)
            else:
                lines.append(f"{item.printed} {body}".strip())
    return "\n".join(line for line in lines if line)


# ----------------------------------------------------------------------------- section builder


class _SectionBuilder:
    def __init__(self, number: str, heading: str | None):
        self.root = _Node("section", number, "", 0, 0.0, heading)
        self.stack: list[_Node] = [self.root]
        self.first = True
        # Open double quotes (an amending section quotes the text it inserts or substitutes, often over
        # several lines with its own numbering): inside a quotation the labels are not this Act's structure.
        self.quote_depth = 0

    def _classify(self, token: str, x: int) -> str:
        kind = _label_kind(token)
        if kind in ("ambiguous", "ambiguous-upper"):
            letter_kind, roman_kind = ("alpha", "roman") if kind == "ambiguous" else ("ALPHA", "ROMAN")
            prev = AMBIGUOUS[token.lower()]
            prev = prev if kind == "ambiguous" else prev.upper()
            for node in reversed(self.stack):
                if node.kind != "definition" and node.depth == DEPTH[letter_kind] and node.key == f"({prev})":
                    # (h) then (i) at the same indent: the next letter; indented further: roman (i) under (h)
                    return letter_kind if x <= node.indent else roman_kind
            return roman_kind
        assert kind is not None
        return kind

    def _open(self, kind: str, key: str, printed: str, x: int) -> _Node:
        depth = DEPTH[kind]
        while len(self.stack) > 1 and self.stack[-1].depth >= depth:
            self.stack.pop()
        parent = self.stack[-1]
        node = _Node(KIND_NAME[kind], key, printed, x, depth, parent=parent)
        parent.items.append(node)
        self.stack.append(node)
        return node

    def _in_definitions(self) -> _Node | None:
        for node in reversed(self.stack):
            if node.def_mode:
                return node
        return None

    def add_line(self, text: str, x: int, notes: list[str], refs: list[tuple[str, str]]) -> None:
        if self.quote_depth > 0 and not self.first:
            self.quote_depth = max(0, self.quote_depth + text.count("\u201c") - text.count("\u201d"))
            self.add_plain(text, notes, refs)
            return
        depth_after = self.quote_depth + text.count("\u201c") - text.count("\u201d")
        self.quote_depth = max(0, depth_after)
        if self.first:
            self.first = False
            m = SECTION_NO_RE.match(text)
            if m and m.group(1) == self.root.key:
                text = text[m.end() :]
                self.root.indent = x
        # leading labels: (1), (a), (i) ... possibly several on one line
        rest = text
        placed: _Node | None = None
        while True:
            m = LABEL_RE.match(rest)
            if not m or _label_kind(m.group(1)) is None:
                break
            token = m.group(1)
            kind = self._classify(token, x)
            placed = self._open(kind, f"({token})", f"({token})", x)
            rest = rest[m.end() :]
        if placed is not None:
            target = placed
        else:
            term = DEF_TERM.match(rest)
            holder = self._in_definitions()
            if term and holder is not None:
                while self.stack[-1] is not holder:
                    self.stack.pop()
                target = _Node("definition", f"def:{term.group(1)}", "", x, holder.depth + 0.5, parent=holder)
                holder.items.append(target)
                self.stack.append(target)
            else:
                # A line without label closes the nodes below its owner, so later labels attach to the
                # owner and the printed order is kept (e.g. "require that individual to-" before (i), (ii)).
                target = self._owner(x)
                while self.stack[-1] is not target:
                    self.stack.pop()
        if rest:
            target.items.append(rest)
            if not target.def_mode and len(target.items) == 1 and DEF_LEAD.match(rest):
                target.def_mode = True
        target.notes.extend(notes)
        target.refs.extend(refs)

    def _owner(self, x: int) -> _Node:
        """The node that a line without label belongs to: the deepest open node printed at or left of it."""
        for node in reversed(self.stack):
            if node.indent <= x:
                return node
        return self.root

    def add_plain(self, text: str, notes: list[str], refs: list[tuple[str, str]]) -> None:
        """A table row or other block: kept with the deepest open node."""
        node = self.stack[-1]
        node.items.append(text)
        node.notes.extend(notes)
        node.refs.extend(refs)


# ----------------------------------------------------------------------------- document


def _enactment_date(value: str | None) -> date:
    if not value or not re.fullmatch(r"\d{8}", value.strip()):
        raise IrishParseError(f"missing or invalid dateofenactment {value!r}")
    v = value.strip()
    try:
        return date(int(v[:4]), int(v[4:6]), int(v[6:8]))
    except ValueError as exc:
        raise IrishParseError(f"invalid dateofenactment {value!r}") from exc


def _title_lines(title: Element | None) -> list[str]:
    """The printed lines of a ``title`` (a Part's "PART 3" and its name are separate lines)."""
    if title is None:
        return []
    lines = [inline_text(c) for c in title if c.tag in ("p", "pc")] or [inline_text(title)]
    return [line for line in lines if line]


def _heading(title: Element | None) -> str | None:
    return " ".join(_title_lines(title)) or None


def _short_title(sections: list[Element], fallback: str) -> tuple[str, str | None]:
    """(citation title, short title as printed in the Act). The comma before the year is dropped."""
    for sect in sections:
        title = _heading(sect.find("title")) or ""
        if "short title" not in title.lower():
            continue
        text = " ".join(inline_text(p) for p in sect.findall("p"))
        m = re.search(r"may be cited as (?:the )?(.+?)\.(?:\s|$)", text)
        if m:
            printed = m.group(1).strip()
            return re.sub(r",\s*(\d{4})$", r" \1", printed), printed
    return re.sub(r",\s*(\d{4})$", r" \1", _ws(fallback)), None


def _commencement_basis(sections: list[Element]) -> str:
    for sect in sections:
        title = (_heading(sect.find("title")) or "").lower()
        if "commencement" not in title:
            continue
        text = " ".join(inline_text(p) for p in sect.findall("p")).lower()
        by_order = "appoint" in text
        relative = "passing" in text
        if by_order and relative:
            return "by_order_and_passing"
        if by_order:
            return "by_order"
        if relative:
            return "relative_to_passing"
    return "unstated"


def parse_irish_statute_book(content: bytes) -> list[ParsedDocument]:
    root = parse_xml(content)
    if root.tag != "act":
        raise IrishParseError(f"unsupported root element {root.tag!r} (only Acts are supported)")
    meta = root.find("metadata")
    body = root.find("body")
    if meta is None or body is None:
        raise IrishParseError("missing metadata or body")
    year_text, number_text = _ws(meta.findtext("year") or ""), _ws(meta.findtext("number") or "")
    if not year_text.isdigit() or not number_text.isdigit():
        raise IrishParseError("missing Act year or number")
    year, number = int(year_text), int(number_text)
    enacted = _enactment_date(meta.findtext("dateofenactment"))
    if enacted.year != year:
        raise IrishParseError(f"enactment date {enacted} is not in the Act year {year}")
    sections = list(body.iter("sect"))
    if not sections:
        raise IrishParseError("no sections found")
    title, printed_title = _short_title(sections, meta.findtext("title") or f"Act {number} of {year}")
    work_uri = act_uri(year, number)

    provisions: list[ParsedProvision] = []
    used: dict[str, int] = {}

    def refs_of(raw: list[tuple[str, str]]) -> list[ParsedRef]:
        out: dict[str, ParsedRef] = {}
        for href, text in raw:
            m = re.fullmatch(r"#SEC(\d+)", href)
            s = re.fullmatch(r"#SCHED(\d+)", href)
            target = f"section-{m.group(1)}" if m else f"schedule-{s.group(1)}" if s else None
            if target:
                out.setdefault(target, ParsedRef(f"{work_uri}/~{target}", text, "cites"))
        return list(out.values())

    def unique(eid: str) -> str:
        n = used.get(eid, 0) + 1
        used[eid] = n
        return eid if n == 1 else f"{eid}-{n}"

    def emit(
        node: _Node,
        parent_eid: str | None,
        num_path: list[str],
        top_kind: str,
        context: str | None,
        eid: str,
    ) -> None:
        path = [*num_path, node.key]
        text = _render(node)
        children = node.children
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=node.kind,
                num_path=path,
                top_kind=top_kind,
                heading=node.heading,
                text=text,
                parent_eid=parent_eid,
                sort_order=len(provisions),
                status="in_force",
                valid_from=enacted,
                valid_to=None,
                extent=["IE"],
                note=" | ".join(node.notes) or None,
                refs=refs_of(node.refs),
                is_leaf=not children,
                has_own_content=bool(node.own_lines) and bool(children),
                context_heading=context,
            )
        )
        for child in children:
            if child.kind == "definition":
                local = "def-" + (slug(child.key[4:]) or str(len(provisions)))
            else:
                local = slug(child.key.strip("()")) or f"{child.kind}{len(provisions)}"
            emit(child, eid, path, top_kind, context, unique(f"{eid}-{local}"))

    # --- long title (front matter): the first line that starts "An Act ..."
    front = root.find("frontmatter")
    if front is not None:
        for p in front.findall("p"):
            notes: list[str] = []
            raw_refs: list[tuple[str, str]] = []
            line = inline_text(p, notes, raw_refs)
            if re.match(r"(?i)^an act\b", line):
                node = _Node("long_title", "long title", "", 0, 0.0)
                node.items.append(line)
                node.notes.extend(notes)
                emit(node, None, [], "long_title", None, "long-title")
                break

    # --- sections, in document order, with the Part / Chapter they sit in as context
    def walk(container: Element, context: list[str]) -> None:
        for el in container:
            if el.tag in ("part", "chapter"):
                head = _heading(el.find("title"))
                walk(el, [*context, head] if head else context)
            elif el.tag == "sect":
                add_section(el, " / ".join(context) or None)

    def add_section(sect: Element, context: str | None) -> None:
        number_el = sect.find("number")
        num = _ws(number_el.text or "").rstrip(".") if number_el is not None else ""
        if not num:
            raise IrishParseError(f"section without number: {sect.get('id')}")
        builder = _SectionBuilder(num, _heading(sect.find("title")))
        for child in sect:
            if child.tag == "p" or child.tag == "pc":
                notes: list[str] = []
                raw_refs: list[tuple[str, str]] = []
                line = inline_text(child, notes, raw_refs)
                if line:
                    builder.add_line(line, _left_indent(child), notes, raw_refs)
                elif notes:
                    builder.stack[-1].notes.extend(notes)
            elif child.tag == "table":
                for row in _table_lines(child):
                    builder.add_plain(row, [], [])
            elif child.tag in ("number", "title", "fn"):
                continue
            else:
                raise IrishParseError(f"section {num}: unsupported element <{child.tag}>")
        emit(builder.root, None, [], "section", context, unique(f"section-{slug(num)}"))

    walk(body, [])

    # --- schedules
    def schedule_lines(el: Element, node: _Node) -> None:
        for child in el:
            if child.tag == "title":
                continue
            if child.tag == "sectionreference":
                node.notes.append(inline_text(child))
            elif child.tag == "part":
                first = (_title_lines(child.find("title")) or [""])[0]
                m = re.match(r"(?i)^part\s+(\S+)", first)
                key = f"part:{m.group(1)}" if m else f"part:{len(node.children) + 1}"
                part = _Node(
                    "schedule_part", key, f"Part {key[5:]}", 0, 1.0, _heading(child.find("title")), parent=node
                )
                node.items.append(part)
                schedule_lines(child, part)
            elif child.tag == "table":
                node.items.extend(_table_lines(child))
            elif child.tag in ("p", "pc", "l1"):
                notes: list[str] = []
                raw_refs: list[tuple[str, str]] = []
                if child.tag == "l1":
                    for sub in child.iter("p"):
                        text = inline_text(sub, notes, raw_refs)
                        if text:
                            node.items.append(text)
                else:
                    text = inline_text(child, notes, raw_refs)
                    if text:
                        node.items.append(text)
                node.notes.extend(notes)
                node.refs.extend(raw_refs)
            elif child.tag in ("fn", "number"):
                continue
            else:
                raise IrishParseError(f"schedule: unsupported element <{child.tag}>")

    back = root.find("backmatter")
    if back is not None:
        for sched in back.findall("schedule"):
            lines = _title_lines(sched.find("title"))
            m = re.match(r"(?i)^schedule\s*(\S*)", lines[0]) if lines else None
            label = (m.group(1) if m else "") or ""
            if not label:
                m = re.fullmatch(r"SCHED(\d+)", sched.get("id") or "")
                label = m.group(1) if m else ""
            node = _Node("schedule", label, "", 0, 0.0, " ".join(lines[1:]) or None)
            schedule_lines(sched, node)
            eid = unique("schedule" + (f"-{slug(label)}" if label else ""))
            emit(node, None, [], "schedule", None, eid)
    if not any(p.kind == "section" for p in provisions):
        raise IrishParseError("no sections found")

    commencement = _commencement_basis(sections)
    outstanding: list[dict[str, object]] = [
        {
            "affects_eid": None,
            "affected_label": "Later amendments, repeals and revocations",
            "by_uri": None,
            "by_title": "Acts and statutory instruments made after enactment",
            "description": (
                "This is the Act as enacted. Changes made by later Acts and statutory instruments are not "
                "applied; they are recorded in the eISB Legislation Directory and, for many Acts, in the "
                "Law Reform Commission's Revised Acts, neither of which is ingested."
            ),
        }
    ]
    if commencement != "unstated":
        outstanding.append(
            {
                "affects_eid": None,
                "affected_label": "Commencement",
                "by_uri": None,
                "by_title": "Commencement orders and commencement provisions",
                "description": (
                    "The Act provides for commencement "
                    + {
                        "by_order": "by ministerial order (days appointed by order)",
                        "relative_to_passing": "on or after the date of its passing",
                        "by_order_and_passing": "partly by ministerial order and partly relative to its passing",
                    }[commencement]
                    + ". Commencement orders and provision-level commencement dates are not ingested: each "
                    "provision is dated from the enactment date, which is a lower bound only."
                ),
            }
        )
    ext: dict[str, object] = {
        "act_year": year,
        "act_number": number,
        "commencement_basis": commencement,
    }
    if printed_title:
        ext["short_title_as_enacted"] = printed_title
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type="act",
            jurisdiction_id="IE",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/en@{enacted.isoformat()}",
            work_title=title,
            title=f"{title} (Number {number} of {year}, as enacted)",
            short_title=title,
            number=str(number),
            work_date=enacted,
            language="en",
            authoritative=False,
            text_origin="official_conversion",
            consolidation_kind="as_enacted",
            version_date=enacted,
            currency_date=None,
            extent=["IE"],
            ext_module="ie",
            ext=ext,
            outstanding_changes=outstanding,
            events=[
                ParsedEvent("enactment", enacted, None, "Enacted (date of enactment in the eISB metadata)", "metadata")
            ],
            provisions=provisions,
            validity_basis="enactment_date_presumed",
        )
    ]
