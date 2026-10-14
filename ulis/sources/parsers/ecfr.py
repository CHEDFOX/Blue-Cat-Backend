"""Parser for the Electronic Code of Federal Regulations (eCFR) XML, one CFR part per document.

Input is the envelope the ``ecfr`` connector yields (root ``ecfr-part``): the context of a title file (its
``Last-Modified`` date, the volume's amendment date, the enclosing chapter) around one ``DIV5`` element
(a CFR part) exactly as GPO published it. The eCFR is an *unofficial* editorial compilation: ``authoritative``
is false and the text is attested only as of the date GPO generated the file (``attested_at_edition``).

Structure. GPO's XML is flat inside a section: one ``P`` per paragraph, with the paragraph number as plain text
("(b)", "(1)", "(i)", italic "(1)" for the fifth level). Paragraph provisions are derived from those labels by
the usual CFR sequence (a, 1, i, A, italic 1, italic i); a label that does not fit the sequence of its
predecessors makes the whole section one provision, never a guessed pinpoint. eIds follow the USLM CFR convention
used for the annual-edition sample (``sec_220.1__para_b__para_1``), so ``/us/cfr/t12/s220.1/b/1`` resolves.

Exactness. Paragraph text is the source text; the italic caption of a paragraph stays in the text (and is also the
heading when it ends in a full stop). Source credits (``CITA``), section authority, editorial notes and footnotes
go in ``note``. Tables are flattened to one line per row ("cell | cell"). Graphics and formulas (``img``, ``MATH``)
are not text; a note says a provision has them. Reserved sections and ranges carry no text and are not provisions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedProvision, ParseSkipped

PARSER_ID = "ecfr_xml"
PARSER_VERSION = "1.0.0"
MAX_LABELLED = 3000  # more labelled paragraphs in one section than this: not split into paragraphs

AMBIGUOUS = (
    "Ambiguous pinpoint: this number repeats at the same level in the source (numbering starts again, e.g. under "
    "separate defined terms); identify the passage by its text."
)
ITALIC_ON, ITALIC_OFF = "\x01", "\x02"
GRAPHICS = {"img", "MATH", "GPH", "GID", "LDRFIG"}
NOTE_TAGS = {"CITA": "Source credit", "SECAUTH": "Section authority", "EDNOTE": None, "EFFDNOT": None}
PARA_TAGS = {"P", "P-1"}  # the elements whose leading "(a)" labels may open a paragraph provision
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}
SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with"}
_TOKEN = re.compile(rf"\({ITALIC_ON}?([A-Za-z0-9]{{1,6}}){ITALIC_OFF}?\)\s*")
_CAPTION = re.compile(rf"{ITALIC_ON}([^{ITALIC_ON}{ITALIC_OFF}]*){ITALIC_OFF}\s*")
_DASH = re.compile(r"[—–]\s*")
_RANGE = re.compile(rf"[-–—]\s*\({ITALIC_ON}?([A-Za-z0-9]{{1,6}}){ITALIC_OFF}?\)\s*")
_ROMAN = re.compile(r"^(x{0,3})(ix|iv|v?i{0,3})$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10}


class EcfrParseError(ValueError):
    pass


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("­", "")).strip()


def _strip_marks(text: str) -> str:
    return text.replace(ITALIC_ON, "").replace(ITALIC_OFF, "")


def _display_case(text: str) -> str:
    """'CREDIT BY BROKERS AND DEALERS (REGULATION T)' -> 'Credit by Brokers and Dealers (Regulation T)'.

    A display label for titles only (law text is never re-cased); headings with lower-case letters are kept."""
    if any(c.islower() for c in text):
        return text
    words = []
    for i, word in enumerate(text.split()):
        core = word.strip("()[],;:")
        if re.fullmatch(r"(?:[A-Za-z]\.)+[A-Za-z]?\.?", core) or re.search(r"\d", core) or len(core) == 1:
            words.append(word)
            continue
        lower = word.lower()
        if i and core.lower() in SMALL_WORDS:
            words.append(lower)
            continue
        first = next((k for k, c in enumerate(lower) if c.isalpha()), None)
        words.append(lower if first is None else lower[:first] + lower[first].upper() + lower[first + 1 :])
    return " ".join(words)


def _parse_amddate(text: str) -> date | None:
    m = re.search(r"([A-Za-z]+)\.?\s+(\d{1,2}),\s+(\d{4})", text)
    if not m or m.group(1)[:3].lower() not in MONTHS:
        return None
    try:
        return date(int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
    except ValueError:
        return None


@dataclass
class _Ctx:
    graphics: bool = False


def _flat(el: Element, ctx: _Ctx, marks: bool = False) -> str:
    """Inline text of an element: footnote marks and graphics dropped, tables not entered."""
    out = [el.text or ""]
    kids = list(el)
    for i, child in enumerate(kids):
        tag = child.tag
        if tag in GRAPHICS:
            ctx.graphics = True
        elif tag == "FTREF" or (tag == "SU" and i + 1 < len(kids) and kids[i + 1].tag == "FTREF"):
            pass  # the footnote mark ("3") would otherwise run into the number before it
        elif tag == "br":
            out.append(" ")
        elif marks and (tag == "I" or (tag == "E" and child.get("T") == "03")):
            out.append(ITALIC_ON + _flat(child, ctx) + ITALIC_OFF)
        else:
            out.append(_flat(child, ctx, marks))
        out.append(child.tail or "")
    return "".join(out)


def _table_lines(table: Element, ctx: _Ctx) -> list[str]:
    lines = []
    for row in table.iter():
        if row.tag in ("TR", "ROW"):
            cells = [_ws(_flat(c, ctx)) for c in row if c.tag in ("TH", "TD", "ENT")]
            if any(cells):
                lines.append(" | ".join(cells))
    return lines


def _lines(el: Element, ctx: _Ctx) -> list[str]:
    """Text lines of a block (paragraph-like elements one line each, containers recursed, tables by row)."""
    tag = el.tag
    if tag in GRAPHICS:
        ctx.graphics = True
        return []
    if tag in NOTE_TAGS or tag in ("FTNT", "STARS", "CFRTOC", "PRTPAGE"):
        return []
    if tag in ("TABLE", "GPOTABLE"):
        return _table_lines(el, ctx)
    if tag in ("EXTRACT", "NOTE", "EXAMPLE", "LIST", "DIV", "FP-DASH-GROUP") or (len(el) and any(c.tag == "P" for c in el)):
        out = [_ws(el.text or "")] if (el.text or "").strip() else []
        for child in el:
            out.extend(_lines(child, ctx))
            if (child.tail or "").strip():
                out.append(_ws(child.tail or ""))
        return [x for x in out if x]
    text = _ws(_flat(el, ctx))
    return [text] if text else []


# ------------------------------------------------------------------------------------------ paragraph labels


def _letters(s: str) -> int | None:
    if re.fullmatch(r"[a-z]", s):
        return ord(s) - 96
    if re.fullmatch(r"([a-z])\1+", s):
        return 26 * (len(s) - 1) + ord(s[0]) - 96
    return None


def _roman(s: str) -> int | None:
    if not s or not _ROMAN.match(s):
        return None
    total = 0
    for i, c in enumerate(s):
        v = _ROMAN_VALUES[c]
        total += -v if i + 1 < len(s) and _ROMAN_VALUES[s[i + 1]] > v else v
    return total


def _candidates(label: str, italic: bool) -> list[tuple[int, int]]:
    """(level, ordinal) readings of a paragraph label: a, 1, i, A, then italic 1, italic i."""
    out: list[tuple[int, int]] = []
    if label.isdigit():
        out.append((5 if italic else 2, int(label)))
    elif label.islower():
        letter, roman = _letters(label), _roman(label)
        if roman is not None and len(label) > 1:
            out.append((6 if italic else 3, roman))
        if letter is not None and not italic:
            out.append((1, letter))
        if roman is not None and len(label) == 1:
            out.append((6 if italic else 3, roman))
    elif label.isupper():
        letter = _letters(label.lower())
        if letter is not None:
            out.append((7 if italic else 4, letter))
    return out


def _span_readings(first: str, last: str, italic: bool) -> list[tuple[int, int, int]]:
    return [
        (a[0], a[1], b[1])
        for a in _candidates(first, italic)
        for b in _candidates(last, italic)
        if a[0] == b[0] and b[1] > a[1]
    ]


def _readings(token: _Token) -> list[tuple[int, int, int]]:
    if token.end is None:
        return [(level, ordinal, ordinal) for level, ordinal in _candidates(token.label, token.italic)]
    first = token.label.split(")-(")[0]
    return _span_readings(first, token.end, token.italic)


def _advance(stack: tuple[int, ...], reading: tuple[int, int, int], restart: bool = False) -> tuple[int, ...] | None:
    """The stack of ordinals after a label read as (level, first, last); a range "(d)-(e)" spans several."""
    level, first, last = reading
    if not stack:  # the first label of a section may sit at any level (a section that starts at "(1)")
        return (0,) * (level - 1) + (last,) if first == 1 else None
    depth = len(stack)
    if level == depth + 1:
        return (*stack, last) if first == 1 else None
    if level <= depth and stack[level - 1] and first == stack[level - 1] + 1:
        return (*stack[: level - 1], last)
    if restart and first == 1:  # after unnumbered text (e.g. "Term means:"), numbering may start again or go deeper
        if level <= depth and stack[level - 1]:
            return (*stack[: level - 1], last)
        if level > depth + 1:
            return (*stack, *(0,) * (level - depth - 1), last)
    return None


def _assign_levels(candidates: list[list[tuple[int, int, int]]], gaps: list[bool]) -> list[int] | None:
    """Levels for a sequence of labels such that each is the first child or the next sibling of an earlier one
    (or, only after unnumbered text, a restart at 1); ``gaps[i]``: unnumbered text precedes label ``i``."""
    n = len(candidates)
    before: list[tuple[int, ...]] = [()] * (n + 1)
    chosen = [-1] * n
    levels = [0] * n
    i = steps = 0
    while i < n:
        steps += 1
        if steps > 100_000:
            return None
        placed = False
        for k in range(chosen[i] + 1, len(candidates[i])):
            nxt = _advance(before[i], candidates[i][k], restart=gaps[i])
            if nxt is not None:
                chosen[i], levels[i], before[i + 1], placed = k, candidates[i][k][0], nxt, True
                break
        if placed:
            i += 1
        else:
            chosen[i] = -1
            i -= 1
            if i < 0:
                return None
    return levels


@dataclass
class _Token:
    label: str  # for a range "(d)-(e)" the label is "d)-(e", so that "(" + label + ")" is the printed form
    italic: bool
    end: str | None = None  # last label of a range
    caption: str | None = None
    consumed: bool = False  # the caption was taken out of the paragraph text (another label follows it)
    own: str | None = None  # that caption as printed, to be kept as text of the paragraph


def _leading_labels(marked: str) -> tuple[list[_Token], str]:
    """Labels at the start of a paragraph ("(b) <I>Scope.</I> (1) This ...") and the text after the last one."""
    tokens: list[_Token] = []
    pos = 0
    while True:
        m = _TOKEN.match(marked, pos)
        if m is None or not _candidates(m.group(1), marked[m.start() + 1] == ITALIC_ON):
            break
        token = _Token(m.group(1), italic=marked[m.start() + 1] == ITALIC_ON)
        tokens.append(token)
        pos = m.end()
        span = _RANGE.match(marked, pos)
        if span and _span_readings(token.label, span.group(1), token.italic):
            token.label, token.end, pos = f"{token.label})-({span.group(1)}", span.group(1), span.end()
        cap = _CAPTION.match(marked, pos)
        if cap is None:
            continue
        text = _ws(cap.group(1))
        dash = _DASH.match(marked, cap.end())
        after = dash.end() if dash else cap.end()
        if _TOKEN.match(marked, after) and (dash or text.endswith((".", "—", "–"))):
            # "(b) Scope. (1) ..." or "(b) Methods-(1) ...": the caption sits between two labels
            token.caption, token.consumed = text.rstrip("—–- "), True
            token.own = _ws(_strip_marks(marked[cap.start() : after]))
            pos = after
        elif text.endswith("."):
            token.caption = text
    return tokens, marked[pos:]


@dataclass
class _Node:
    label: str | None = None
    heading: str | None = None
    blocks: list[Any] = field(default_factory=list)  # text lines (str) and child nodes, in source order

    @property
    def children(self) -> list[_Node]:
        return [b for b in self.blocks if isinstance(b, _Node)]

    @property
    def own_text(self) -> bool:
        return any(isinstance(b, str) and b for b in self.blocks)

    def composite(self) -> str:
        lines = []
        for b in self.blocks:
            if isinstance(b, str):
                lines.append(b)
            else:
                body = b.composite()
                lines.append(f"({b.label}) {body}".rstrip() if body else f"({b.label})")
        return "\n".join(lines)


def _build_tree(items: list[tuple[str, Any]]) -> _Node | None:
    """Arrange a section's paragraphs by label; ``None`` if the labels do not form a regular sequence."""
    parsed: list[tuple[list[_Token], str] | str] = []
    for kind, value in items:
        if kind == "para":
            tokens, rest = _leading_labels(value)
            parsed.append((tokens, _ws(_strip_marks(rest))) if tokens else _ws(_strip_marks(value)))
        else:
            parsed.extend(value)
    all_tokens: list[_Token] = []
    gaps: list[bool] = []
    gap = False
    for entry in parsed:
        if isinstance(entry, str):
            gap = gap or bool(entry)
            continue
        for token in entry[0]:
            all_tokens.append(token)
            gaps.append(gap)
            gap = False
    root = _Node()
    if not all_tokens:
        root.blocks = [entry for entry in parsed if isinstance(entry, str) and entry]
        return root
    if len(all_tokens) > MAX_LABELLED:
        return None
    levels = _assign_levels([_readings(t) for t in all_tokens], gaps)
    if levels is None:
        return None
    path: list[_Node] = [root]  # path[k] is the open node at level k (root at 0)
    index = 0
    last: _Node = root
    for entry in parsed:
        if isinstance(entry, str):
            if entry:
                last.blocks.append(entry)
            continue
        tokens, rest = entry
        for token in tokens:
            level = levels[index]
            index += 1
            parent = path[min(level - 1, len(path) - 1)]
            node = _Node(label=token.label, heading=token.caption)
            if token.own:
                node.blocks.append(token.own)  # the caption stays part of the paragraph's text
            parent.blocks.append(node)
            del path[level:]
            while len(path) < level:  # a section that starts below the first level: virtual parents are the root
                path.append(parent)
            path.append(node)
            last = node
        if rest:
            last.blocks.append(rest)
    return root


# ------------------------------------------------------------------------------------------ the document


def _sanitize(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]", "_", value)


class _PartBuilder:
    def __init__(self) -> None:
        self.provisions: list[ParsedProvision] = []
        self.eids: dict[str, int] = {}
        self.unstructured = 0

    def _eid(self, base: str) -> tuple[str, bool]:
        self.eids[base] = self.eids.get(base, 0) + 1
        return (base, False) if self.eids[base] == 1 else (f"{base}_dup{self.eids[base]}", True)

    def add_unit(self, el: Element) -> None:
        kind = "appendix" if el.get("TYPE") == "APPENDIX" else "section"
        ctx = _Ctx()
        heading_el = el.find("HEAD")
        head_text = _ws(_flat(heading_el, ctx)) if heading_el is not None else ""
        notes: list[str] = []
        items: list[tuple[str, Any]] = []  # ("para", text with italic marks) or ("lines", [text, ...])
        for child in el:
            tag = child.tag
            if tag == "HEAD":
                continue
            if tag in NOTE_TAGS or tag == "FTNT":
                text = _ws(_flat(child, ctx))
                label = NOTE_TAGS.get(tag, "Footnote")
                if text:
                    notes.append(f"{label}: {text}" if label else text)
            elif kind == "section" and tag in PARA_TAGS:
                items.append(("para", _flat(child, ctx, marks=True)))
            else:
                lines = _lines(child, ctx)
                if lines:
                    items.append(("lines", lines))
        number_attr = (el.get("N") or "").strip()
        if kind == "section":
            m = re.match(r"^§\s*(\S+)$", number_attr)  # "§§ 3.4-3.9" (a reserved range) does not match
            num = m.group(1) if m else ""
            heading = re.sub(r"^§+\s*\S+\s*", "", head_text).strip() or None
            root = _build_tree(items)
        else:
            num = re.sub(r"^Appendix\s+", "", number_attr).strip() or f"unnumbered{len(self.provisions) + 1}"
            heading = head_text or None
            root = _Node(blocks=[line for _, value in items for line in value])
        if root is None:
            self.unstructured += 1
            root = _Node()
            for k, v in items:
                root.blocks.extend([_ws(_strip_marks(v))] if k == "para" else v)
            notes.insert(0, "Paragraph levels were not derived for this section (its labels are not in regular sequence).")
        if not num or not root.composite().strip():
            return  # a reserved or removed section or range: no text, not a provision
        if ctx.graphics:
            notes.append("The source has graphics or formulas here that are not reproduced in the text.")
        self._emit(kind, num, heading, root, " ".join(notes) or None)

    def _emit(self, kind: str, num: str, heading: str | None, root: _Node, note: str | None) -> None:
        prefix = "sec" if kind == "section" else "app"
        eid, duplicate = self._eid(f"{prefix}_{_sanitize(num)}")
        if duplicate:
            note = f"{note} {AMBIGUOUS}" if note else AMBIGUOUS
        self._add(eid, kind, [num], kind, heading, root, None, heading, note)

    def _add(
        self,
        eid: str,
        kind: str,
        num_path: list[str],
        top_kind: str,
        heading: str | None,
        node: _Node,
        parent_eid: str | None,
        context_heading: str | None,
        note: str | None,
    ) -> None:
        children = node.children
        self.provisions.append(
            ParsedProvision(
                eid=eid,
                kind=kind,
                num_path=num_path,
                top_kind=top_kind,
                heading=heading,
                text=node.composite(),
                parent_eid=parent_eid,
                sort_order=len(self.provisions),
                status="in_force",
                valid_from=None,
                valid_to=None,
                extent=["US"],
                note=note,
                refs=[],
                is_leaf=not children,
                has_own_content=bool(children) and node.own_text,
                context_heading=context_heading,
            )
        )
        for child in children:
            child_eid, duplicate = self._eid(f"{eid}__para_{_sanitize(child.label or '')}")
            self._add(
                child_eid,
                "paragraph",
                [*num_path, child.label or ""],
                top_kind,
                child.heading,
                child,
                eid,
                context_heading,
                AMBIGUOUS if duplicate else None,
            )


def _units(el: Element) -> list[Element]:
    """Sections and appendices of a part, in document order (subparts and subject groups are only descended)."""
    out: list[Element] = []
    for child in el:
        if child.tag in ("DIV8", "DIV9"):
            out.append(child)
        elif child.tag in ("DIV6", "DIV7"):
            out.extend(_units(child))
    return out


def parse_ecfr(content: bytes) -> list[ParsedDocument]:
    try:
        root = fromstring(content, forbid_dtd=True)
    except Exception as exc:
        raise EcfrParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if root.tag != "ecfr-part":
        raise EcfrParseError("root element is not an eCFR part envelope")
    try:
        title_no = int(root.get("title-number") or "")
        modified = datetime.fromisoformat(root.get("last-modified") or "")
    except ValueError as exc:
        raise EcfrParseError("envelope lacks a title number or a Last-Modified date") from exc
    version_date = modified.date()
    part_el = root.find("DIV5")
    if part_el is None or part_el.get("TYPE") != "PART":
        raise EcfrParseError("envelope holds no CFR part")
    part_no = (part_el.get("N") or "").strip()
    ctx = _Ctx()
    head = _ws(_flat(part_el.find("HEAD"), ctx)) if part_el.find("HEAD") is not None else ""
    heading = re.sub(r"^PARTS?\s+\S+\s*[—–-]\s*", "", head).strip() or head
    builder = _PartBuilder()
    for unit in _units(part_el):
        builder.add_unit(unit)
    if not builder.provisions:
        raise ParseSkipped(f"{title_no} CFR part {part_no} has no section or appendix text (reserved or removed)")

    agency = chapter = None
    for anc in root.findall("ancestor"):
        if anc.get("type") == "CHAPTER" and anc.find("HEAD") is not None:
            chapter = anc.get("n") or None
            name = re.sub(r"^CHAPTER\s+\S+\s*[—–-]\s*", "", _ws(_flat(anc.find("HEAD"), ctx)))
            agency = _display_case(re.sub(r"\s*\(continued\)\s*$", "", name, flags=re.I).strip())
    amended = _parse_amddate(root.findtext("amddate") or "")
    authority_el = part_el.find("AUTH")
    authority = _ws(_flat(authority_el, ctx)) if authority_el is not None else ""
    authority = re.sub(r"^Authority:\s*", "", authority) or None

    work_uri = f"/us/cfr/t{title_no}/p{part_no}"
    name = f"{title_no} CFR Part {part_no}"
    label = _display_case(heading)
    stamp = modified.strftime("%H%M%SZ")
    ext: dict[str, Any] = {
        "cfr_title": title_no,
        "cfr_part": part_no,
        "cfr_compilation": "ecfr",
        "agency": agency,
        "cfr_chapter": chapter,
        "cfr_authority": authority,
        "volume_amended_on": amended.isoformat() if amended else None,
    }
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type="regulation",
            jurisdiction_id="US",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/en@{version_date.isoformat()}/ecfr-{stamp}",
            work_title=f"{name}: {label}",
            title=f"{name}: {label} (eCFR, file of {version_date.isoformat()})",
            short_title=name,
            number=part_no,
            work_date=None,
            language="en",
            authoritative=False,
            text_origin="official_consolidation",
            consolidation_kind="editorial_compilation",
            version_date=version_date,
            currency_date=version_date,
            extent=["US"],
            ext_module="us",
            ext={k: v for k, v in ext.items() if v is not None},
            provisions=builder.provisions,
            validity_basis="attested_at_edition",
        )
    ]
