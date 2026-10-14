"""Parser for judgments of the Court of Justice and the General Court fetched from Cellar.

Input is a *bundle* built by :mod:`ulis.sources.connectors.eu_cellar_case_law`: the SPARQL answers
about one judgment (date, ECLI, court; case designation and parties in each language) and the
XHTML file of one language version, each exactly as received. Two layouts of the Publications
Office's XHTML are read:

* **converted from Formex** (``fmx2xhtml``; recent judgments - in the sample those from 2014): the
  header, then numbered points as two-column tables (a ``count`` cell holding the point number,
  ``id="pointN"``), headings, the operative part ("On those grounds ... rules:") as a nested table
  of items, links to cited judgments by ECLI. A table right after a point (a quotation, a list)
  belongs to that point;
* **html-simplified** (older judgments, digitised from the printed Reports): sections introduced by
  ``<a name="I1|I2|MO|CO|VS|DI">`` anchors (parties, subject, grounds, costs, endorsements,
  operative part), one ``<p>`` per block, in capitals for the oldest cases. The point number is
  part of the text: glued to the first word ("1BY ORDER"), followed by a space ("1 BY ORDER") or by
  a full stop ("1. By orders"). Quoted provisions and lists of questions start with numbers too, so
  the point numbers are taken to be the longest run of such numbers that increases by one (by two
  where the source lost a number); every other block is an unnumbered paragraph (headings,
  quotations, sub-items). The oldest judgments (1960s) have no point numbers at all.

Every text block is kept in order and verbatim (whitespace runs, including non-breaking spaces,
become one space; the non-breaking hyphen U+2011 is written as a hyphen-minus). Nothing is
summarised or translated. ``eid``s: ``header``/``index``/``subject``/``endorsements`` (header
material), ``para_N`` (numbered point N), ``u_K`` (K-th unnumbered block of the reasoning),
``op_N`` / ``opu_K`` (numbered / unnumbered blocks of the operative part). Numbered points keep
the Court's numbers (``num``); operative items are numbered ``op-N``.

References: a link to another judgment (ECLI) that the source marks is kept as a ``cites`` link
(``source_markup``); with a ``#pointN`` fragment it points at that point. References to the
EU legislation already ingested (TFEU, TEU, the Charter, Regulation (EU) 2016/679 ...) are added
only where the text names the article and the instrument in one of a few exact patterns
(``pattern``); the link is to the article, never to a guessed paragraph. Anything else (acts
cited by Official Journal reference, pre-Lisbon Treaty numbering ...) is not linked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from html.entities import name2codepoint
from typing import Any
from urllib.parse import unquote
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.connectors.eu_cellar import LANG3
from ulis.sources.normalized import ParsedDocument, ParsedJudgmentMeta, ParsedParagraph, ParsedRef
from ulis.sources.parsers.eu_cellar import EuParseError, _bindings, _date, _ws, load_bundle

PARSER_ID = "eu_cellar_case_law"
PARSER_VERSION = "1.0.0"
XH = "{http://www.w3.org/1999/xhtml}"
CDM = "http://publications.europa.eu/ontology/cdm#"
COURTS = {"CJ": "EU-CJEU", "GCEU": "EU-GC"}
LANG_OF = {v: k for k, v in LANG3.items()}
DESIGNATION_PREFIX = re.compile(
    r"^(?:joined\s+cases?|cases?|affaires?\s+jointes|affaire|verbundene\s+rechtssachen|rechtssache)\s+", re.IGNORECASE
)
OPERATIVE_START = re.compile(r"^(?:On those grounds|Par ces motifs|Aus diesen Gr(?:ü|u)nden)\b", re.IGNORECASE)
CHARTER_NAME = re.compile(
    r"Charter of Fundamental Rights of the European Union|Charte des droits fondamentaux de l.Union europ", re.I
)
OLD_SECTIONS = {
    "I1": ("header", "header"),
    "I2": ("header", "subject"),
    "MO": ("motivation", ""),
    "CO": ("motivation", ""),
    "VS": ("header", "endorsements"),
    "DI": ("decision", ""),
}


@dataclass
class CaseMeta:
    celex: str
    ecli: str
    decision_date: date
    court_id: str
    procedure_language: str | None


@dataclass
class Block:
    """One paragraph before it is given its identifier."""

    part: str
    num: str | None
    text: str
    refs: list[ParsedRef]
    eid: str | None = None  # fixed identifiers (header, index, ...)


# --------------------------------------------------------------------------- metadata


def parse_case_metadata(rows: list[dict[str, Any]], celex: str) -> CaseMeta:
    values: dict[str, set[str]] = {}
    for r in rows:
        values.setdefault(r["p"].removeprefix(CDM), set()).add(r["o"])

    def one(prop: str) -> str:
        vals = values.get(prop, set())
        if len(vals) != 1:
            raise EuParseError(f"{celex}: expected one {prop}, the metadata has {sorted(vals)}")
        return next(iter(vals))

    if celex not in values.get("resource_legal_id_celex", set()):
        raise EuParseError(f"{celex}: the metadata does not carry this CELEX number")
    if one("work_has_resource-type").rsplit("/", 1)[-1] != "JUDG":
        raise EuParseError(f"{celex}: not a judgment")
    courts = {v.rsplit("/", 1)[-1] for v in values.get("work_created_by_agent", set()) if "/corporate-body/" in v}
    if len(courts) != 1 or next(iter(courts)) not in COURTS:
        raise EuParseError(f"{celex}: unsupported court {sorted(courts)}")
    decided = _date(one("work_date_document"))
    if decided is None:
        raise EuParseError(f"{celex}: no decision date")
    proc = values.get("case-law_uses_procedure_language", set())
    proc_codes = {v.rsplit("/", 1)[-1] for v in proc}
    return CaseMeta(
        celex=celex,
        ecli=one("case-law_ecli"),
        decision_date=decided,
        court_id=COURTS[next(iter(courts))],
        procedure_language=LANG_OF.get(next(iter(proc_codes))) if len(proc_codes) == 1 else None,
    )


XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


def parse_xhtml(data: bytes) -> Element:
    """Parse the publisher's XHTML; the HTML named entities its DOCTYPE declares (``&nbsp;``) are
    written as numeric references first, since the DTD is not loaded."""

    def numeric(m: re.Match[bytes]) -> bytes:
        name = m.group(1).decode("ascii")
        if name in XML_ENTITIES or name not in name2codepoint:
            return m.group(0)
        return f"&#{name2codepoint[name]};".encode("ascii")

    return fromstring(re.sub(rb"&([A-Za-z][A-Za-z0-9]*);", numeric, data))  # type: ignore[no-any-return]


def _typography(text: str) -> str:
    return text.replace("\u2011", "-")


def _designation(text: str) -> str:
    return _ws(_typography(text)).rstrip(".").strip()


def language_facts(
    rows: list[dict[str, Any]], document_text: str
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Case designation, parties and title of every language version in the answer.

    Cellar sometimes records two values for one version (two titles, with and without the
    referring court; once a party name with a word of another language). Of several values the
    one whose words all occur in the judgment's own text is used; if that does not decide it, the
    designation and the parties are an error (nothing is guessed) and the longer title is taken.
    """
    found: dict[str, dict[str, set[str]]] = {"case": {}, "parties": {}, "title": {}}
    for r in rows:
        lang = LANG_OF.get(r.get("lang", "").rsplit("/", 1)[-1])
        if lang is None:
            continue
        if r.get("case"):
            found["case"].setdefault(lang, set()).add(_designation(r["case"]))
        if r.get("parties"):
            found["parties"].setdefault(lang, set()).add(_ws(_typography(r["parties"])))
        if r.get("title"):
            found["title"].setdefault(lang, set()).add(_ws(r["title"].replace("#", " ")))
    words = set(re.findall(r"\w+", document_text.lower()))

    def pick(values: set[str], what: str, lang: str) -> str:
        if len(values) == 1:
            return next(iter(values))
        fitting = {v for v in values if all(w in words for w in re.findall(r"\w+", v.lower()))}
        if len(fitting) == 1:
            return next(iter(fitting))
        if what == "title":
            return max(fitting or values, key=len)
        raise EuParseError(f"{lang}: expected one {what}, the metadata has {sorted(values)}")

    return (
        {lang: pick(v, "case designation", lang) for lang, v in found["case"].items()},
        {lang: pick(v, "parties", lang) for lang, v in found["parties"].items()},
        {lang: pick(v, "title", lang) for lang, v in found["title"].items()},
    )


# --------------------------------------------------------------------------- text helpers


def _classes(el: Element) -> set[str]:
    return {c.removeprefix("coj-") for c in (el.get("class") or "").split()}


def _tag(el: Element) -> str:
    return el.tag.removeprefix(XH) if isinstance(el.tag, str) else ""


def inline(el: Element) -> str:
    parts: list[str] = []

    def walk(e: Element) -> None:
        if e.text:
            parts.append(e.text)
        for child in e:
            if _tag(child) == "br":
                parts.append(" ")
            else:
                walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(el)
    return _ws(_typography("".join(parts)))


def _own_rows(table: Element) -> list[list[Element]]:
    rows = []
    for child in table:
        if _tag(child) == "tr":
            rows.append([c for c in child if _tag(c) == "td"])
        elif _tag(child) in ("tbody", "thead"):
            rows.extend([c for c in tr if _tag(c) == "td"] for tr in child if _tag(tr) == "tr")
    return [r for r in rows if r]


def block_lines(container: Element) -> list[str]:
    """The text lines of a table cell or division: paragraphs, and nested tables row by row."""
    lines: list[str] = []
    for child in container:
        tag = _tag(child)
        if tag == "p":
            text = inline(child)
            if text:
                lines.append(text)
        elif tag == "table":
            lines.extend(table_lines(child))
        elif tag == "div":
            lines.extend(block_lines(child))
    return lines


def table_lines(table: Element) -> list[str]:
    lines: list[str] = []
    for cells in _own_rows(table):
        if len(cells) == 1:
            lines.extend(block_lines(cells[0]))
            continue
        label = " ".join(inline(p) for p in cells[0].iter(XH + "p") if inline(p))
        body = block_lines(cells[1])
        if label and body:
            body[0] = f"{label} {body[0]}"
        elif label:
            body = [label]
        lines.extend(body)
    return lines


def _cell_label(cell: Element) -> str:
    return " ".join(t for t in (inline(p) for p in cell.iter(XH + "p")) if t)


# --------------------------------------------------------------------------- references

ECLI_LINK = re.compile(
    r"^https?://publications\.europa\.eu/resource/ecli/(ECLI(?:%3A|:)EU(?:%3A|:)[CT](?:%3A|:)\d{4}(?:%3A|:)[0-9A-Za-z.]+)"
    r"(?:#point(\d+))?$"
)


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map a link to a judgment (Cellar ECLI resource) to its ULIS work URI and point.

    ``http://publications.europa.eu/resource/ecli/ECLI%3AEU%3AC%3A2014%3A238#point68`` ->
    (``/eu/case/ECLI:EU:C:2014:238``, ``para_68``)
    """
    m = ECLI_LINK.match(uri)
    if not m:
        return None
    return f"/eu/case/{unquote(m.group(1))}", (f"para_{m.group(2)}" if m.group(2) else None)


def source_links(container: Element) -> list[ParsedRef]:
    """Links to other judgments that the source marks, one per distinct target.

    The source writes a citation as up to three links (case number, ECLI, and the point cited);
    a link to a point replaces the links to the same judgment without a point in that paragraph.
    """
    found: dict[str, ParsedRef] = {}
    for a in container.iter(XH + "a"):
        href = a.get("href") or ""
        if ECLI_LINK.match(href) and href not in found:
            found[href] = ParsedRef(href=href, text=inline(a), relation="cites")
    pinned = {href.partition("#")[0] for href in found if "#" in href}
    return [ref for href, ref in found.items() if "#" in href or href not in pinned]


# Instruments already ingested from the EU legislation source (docs/jurisdictions/eu.md): the work
# URIs are the ELI-derived ones of ulis.sources.parsers.eu_cellar.
TARGETS_EN = (
    ("/eu/treaty/tfeu_2016", r"(?:TFEU|(?:of\s+)?the\s+Treaty\s+on\s+the\s+Functioning\s+of\s+the\s+European\s+Union)"),
    ("/eu/treaty/teu_2016", r"(?:TEU|(?:of\s+)?the\s+Treaty\s+on\s+European\s+Union)"),
    ("/eu/reg/2016/679", r"of\s+Regulation\s+(?:\(EU\)\s+)?2016/679"),
    ("/eu/reg/2008/593", r"of\s+Regulation\s+(?:\(EC\)\s+)?(?:No\s+)?593/2008"),
    ("/eu/reg/2012/1215", r"of\s+Regulation\s+(?:\(EU\)\s+)?(?:No\s+)?1215/2012"),
    ("/eu/dir/2011/83", r"of\s+Directive\s+(?:\(EU\)\s+)?2011/83(?:/EU)?"),
    ("/eu/reg/2024/1689", r"of\s+Regulation\s+(?:\(EU\)\s+)?2024/1689"),
)
TARGETS_FR = (
    ("/eu/treaty/tfeu_2016", r"TFUE"),
    ("/eu/treaty/teu_2016", r"TUE"),
    ("/eu/reg/2016/679", r"du\s+règlement\s+(?:\(UE\)\s+)?2016/679"),
    ("/eu/reg/2008/593", r"du\s+règlement\s+(?:\(CE\)\s+)?(?:n°\s*)?593/2008"),
    ("/eu/reg/2012/1215", r"du\s+règlement\s+(?:\(UE\)\s+)?(?:n°\s*)?1215/2012"),
    ("/eu/dir/2011/83", r"de\s+la\s+directive\s+(?:\(UE\)\s+)?2011/83(?:/UE)?"),
    ("/eu/reg/2024/1689", r"du\s+règlement\s+(?:\(UE\)\s+)?2024/1689"),
)
# The Charter of Fundamental Rights. "the Charter" alone is taken for it only if the judgment names
# that Charter and no other (Charter of the United Nations, social charters); the full name always.
CHARTER_FULL = {
    "en": r"of\s+the\s+Charter\s+of\s+Fundamental\s+Rights(?:\s+of\s+the\s+European\s+Union)?",
    "fr": r"de\s+la\s+Charte\s+des\s+droits\s+fondamentaux(?:\s+de\s+l.Union\s+européenne)?",
}
CHARTER_SHORT = {
    "en": r"of\s+the\s+Charter(?:\s+of\s+Fundamental\s+Rights(?:\s+of\s+the\s+European\s+Union)?)?(?!\s+of\s+the\b)",
    "fr": r"de\s+la\s+Charte(?:\s+des\s+droits\s+fondamentaux(?:\s+de\s+l.Union\s+européenne)?)?(?!\s+des\s+Nations)",
}
CHARTER_WORK = "/eu/treaty/char_2016"
OTHER_CHARTER = re.compile(
    r"Charter of the United Nations|Charte des Nations unies|Social Charter|Charte sociale|Charter of Paris", re.I
)
ART_EN = r"\d+(?:\(\d+\))?(?:\([a-z]\))?"
ART_FR = r"\d+(?:,\s+paragraphe\s+\d+)?(?:,\s+(?:premier\s+alinéa,\s+)?sous\s+[a-z]\))?"


def _article_pattern(word: str, art: str, joiner: str, targets: tuple[tuple[str, str], ...]) -> re.Pattern[str]:
    names = "|".join(f"(?P<t{i}>{pattern})" for i, (_, pattern) in enumerate(targets))
    return re.compile(rf"\b(?:{word})\s+(?P<arts>{art}(?:(?:,\s*|\s+{joiner}\s+){art})*)(?:,)?\s+(?:{names})")


PATTERNS = {
    "en": (_article_pattern("Articles?", ART_EN, "and", TARGETS_EN), TARGETS_EN),
    "fr": (_article_pattern("articles?", ART_FR, "et", TARGETS_FR), TARGETS_FR),
}
CHARTER_PATTERNS = {
    (mode, lang): _article_pattern(word, art, joiner, ((CHARTER_WORK, table[lang]),))
    for mode, table in (("full", CHARTER_FULL), ("short", CHARTER_SHORT))
    for lang, word, art, joiner in (("en", "Articles?", ART_EN, "and"), ("fr", "articles?", ART_FR, "et"))
}


def _article_numbers(arts: str) -> list[str]:
    """Article numbers of ``7 and 8``, ``6(1)(a)``, ``6, paragraphe 1, sous a)``; the part after is dropped."""
    # In French "articles 7 et 8"; split on the joiners only between whole references.
    out = []
    for piece in re.split(r",\s*(?=\d)|\s+(?:and|et)\s+", arts):
        m = re.match(r"\d+", piece.strip())
        if m:
            out.append(m.group(0))
    return out


def legislation_refs(text: str, language: str, charter: str) -> list[ParsedRef]:
    """Links to articles of ingested EU instruments named in one exact pattern (never guessed).

    ``Articles 7 and 8 of the Charter`` (see ``CHARTER_FULL``), ``Article 101 TFEU``,
    ``Article 6(1)(a) of Regulation (EU) 2016/679`` ... The link is to the article (``art_6``);
    paragraphs and points stay in the citation text.
    """
    lang = language if language in PATTERNS else "en"
    out: dict[str, ParsedRef] = {}

    def add(work: str, match: re.Match[str]) -> None:
        for number in _article_numbers(match.group("arts")):
            href = f"{work}/~art_{number}"
            out.setdefault(href, ParsedRef(href, match.group(0), "cites", extraction_method="pattern"))

    pattern, targets = PATTERNS[lang]
    for m in pattern.finditer(text):
        for i, (work, _) in enumerate(targets):
            if m.group(f"t{i}"):
                add(work, m)
    if charter != "none":
        for m in CHARTER_PATTERNS[(charter, lang)].finditer(text):
            add(CHARTER_WORK, m)
    return list(out.values())


# --------------------------------------------------------------------------- layouts


def _numbered_point(cells: list[Element]) -> str | None:
    if len(cells) < 2:
        return None
    ps = [p for p in cells[0] if _tag(p) == "p"]
    if len(ps) == 1 and "count" in _classes(ps[0]) and re.fullmatch(r"\d{1,4}", inline(ps[0])):
        return inline(ps[0])
    return None


def _operative_items(cells: list[Element]) -> list[tuple[str | None, list[str], Element]]:
    """The items of a row of the operative part: (label, lines, element)."""
    items: list[tuple[str | None, list[str], Element]] = []
    for child in cells[-1]:
        tag = _tag(child)
        if tag == "table":
            for row in _own_rows(child):
                label = _cell_label(row[0]) if len(row) > 1 else None
                content = block_lines(row[-1])
                if content:
                    items.append((label or None, content, row[-1]))
        elif tag == "p" and inline(child):
            items.append((None, [inline(child)], child))
    return items


def parse_modern(root: Element, language: str, charter: str) -> list[Block]:
    body = root.find(XH + "body")
    if body is None:
        raise EuParseError("XHTML without a body")
    blocks: list[Block] = []
    header: list[str] = []
    footnotes: list[str] = []
    index: list[str] = []
    in_decision = False
    seen_points = False
    continuable = False  # the previous block is a numbered point that tables right after it continue

    def refs_of(element: Element, text: str) -> list[ParsedRef]:
        return source_links(element) + legislation_refs(text, language, charter)

    for child in body:
        tag, cls = _tag(child), _classes(child)
        if tag == "p":
            text = inline(child)
            if not text:
                continue
            if "note" in cls:
                footnotes.append(text)
            elif not seen_points and "index" in cls:
                index.append(text)
            elif not seen_points:
                header.append(text)
            else:
                blocks.append(Block("decision" if in_decision else "motivation", None, text, []))
                continuable = False
            continue
        if tag != "table" or any("signaturecase" in _classes(d) for d in child.iter(XH + "div")):
            continue
        rows = _own_rows(child)
        if not rows:
            continue
        cells = rows[0]
        number = _numbered_point(cells)
        if number is not None:
            seen_points = True
            text = "\n".join(block_lines(cells[1]))
            blocks.append(Block("motivation", number, text, refs_of(cells[1], text)))
            continuable = True
            continue
        lines = table_lines(child)
        if not seen_points:
            header.extend(lines)
        elif lines:
            if OPERATIVE_START.match(lines[0]):
                in_decision = True
            if continuable and not in_decision:
                # A table right after a numbered point (a quotation, a list) belongs to that point.
                previous = blocks[-1]
                previous.text += "\n" + "\n".join(lines)
                previous.refs = list({r.href: r for r in [*previous.refs, *refs_of(child, previous.text)]}.values())
                continue
            items = _operative_items(cells) if in_decision and len(cells) > 1 else []
            if items and not OPERATIVE_START.match(lines[0]):
                for label, item_lines, element in items:
                    num = label[:-1] if label and re.fullmatch(r"\d{1,3}[.)]", label) else None
                    if label and not num:
                        item_lines = [f"{label} {item_lines[0]}", *item_lines[1:]]
                    text = "\n".join(item_lines)
                    blocks.append(Block("decision", f"op-{num}" if num else None, text, refs_of(element, text)))
            else:
                text = "\n".join(lines)
                blocks.append(Block("decision" if in_decision else "motivation", None, text, refs_of(cells[-1], text)))
    if not seen_points:
        raise EuParseError("no numbered points found")
    lead = [Block("header", None, "\n".join([*header, *footnotes]), [], eid="header")]
    if index:
        lead.append(Block("header", None, "\n".join(index), [], eid="index"))
    return lead + blocks


# "1. By orders", "1 . ARTICLE 12", "1 BY ORDER", "1BY ORDER": a number, then a full stop and/or space,
# or directly the first word. Only the next number in sequence is accepted.
NUMBER_AT_START = re.compile(r"^(\d{1,3})(?:\s*\.\s+|\s+|(?=[A-Za-z\u2018\u201c'\"]))(?=\S)")


def numbered_lines(lines: list[str]) -> dict[int, tuple[str, str]]:
    """Which lines of the grounds start with a point number: index -> (number, rest of the line).

    A line that begins with a number is a candidate, but quoted provisions and lists of questions
    also start with numbers. The point numbers are the longest run of candidates that increases by
    one (or by two where the source lost a number), so a quoted "8." between points 6 and 7 or a
    list "1.", "2." inside a quotation is not taken for a point.
    """
    cands = [(i, m) for i, line in enumerate(lines) if (m := NUMBER_AT_START.match(line))]
    best: list[tuple[int, list[int]]] = []  # per candidate: (chain length, chain of candidate positions)
    for k, (_, m) in enumerate(cands):
        n = int(m.group(1))
        chain = [k] if n <= 2 else []
        for j in range(k):
            step = n - int(cands[j][1].group(1))
            if step in (1, 2) and best[j][1] and len(best[j][1]) + 1 > len(chain):
                chain = [*best[j][1], k]
        best.append((len(chain), chain))
    if not best:
        return {}
    top = max(best, key=lambda t: t[0])[1]
    return {cands[k][0]: (cands[k][1].group(1), lines[cands[k][0]][cands[k][1].end() :].strip()) for k in top}


def parse_simplified(root: Element, language: str, charter: str) -> list[Block]:
    div = root.find(f"{XH}body/{XH}div/{XH}div")
    if div is None:
        raise EuParseError("unexpected layout of the simplified XHTML")
    sections: list[tuple[str, list[str]]] = []
    for child in div:
        tag = _tag(child)
        if tag == "a" and child.get("name"):
            sections.append((child.get("name") or "", []))
        elif tag == "p" and sections:
            text = inline(child)
            if text:
                sections[-1][1].append(text)
    for name, _ in sections:
        if name not in OLD_SECTIONS:
            raise EuParseError(f"unknown section {name!r} in the simplified XHTML")
    grounds = [line for name, lines in sections if OLD_SECTIONS[name][0] == "motivation" for line in lines]
    numbered = numbered_lines(grounds)
    blocks: list[Block] = []
    position = 0
    for name, lines in sections:
        part, eid = OLD_SECTIONS[name]
        if part == "header":
            blocks.append(Block("header", None, "\n".join(lines), [], eid=eid))
            continue
        for line in lines:
            num: str | None = None
            text = line
            if part == "motivation":
                if position in numbered:
                    num, text = numbered[position]
                position += 1
            blocks.append(Block(part, num, text, legislation_refs(text, language, charter)))
    if not any(b.part == "motivation" for b in blocks):
        raise EuParseError("no grounds found")
    return blocks


def assign_ids(blocks: list[Block]) -> list[ParsedParagraph]:
    out: list[ParsedParagraph] = []
    unnumbered = {"motivation": 0, "decision": 0}
    used: set[str] = set()
    for order, b in enumerate(blocks):
        if b.eid:
            eid = b.eid
        elif b.part == "motivation" and b.num:
            eid = f"para_{b.num}"
        elif b.part == "decision" and b.num:
            eid = "op_" + b.num.removeprefix("op-")
        else:
            unnumbered[b.part] += 1
            eid = f"{'u' if b.part == 'motivation' else 'opu'}_{unnumbered[b.part]}"
        if eid in used:
            raise EuParseError(f"duplicate paragraph identifier {eid}")
        used.add(eid)
        out.append(ParsedParagraph(eid=eid, num=b.num, part=b.part, text=b.text, sort_order=order, refs=b.refs))
    return out


# --------------------------------------------------------------------------- document


def parse_eu_case_law(content: bytes) -> list[ParsedDocument]:
    bundle, parts = load_bundle(content)
    celex, language = bundle.get("celex"), bundle.get("language")
    if bundle.get("kind") != "case_law" or not isinstance(celex, str) or language not in LANG3:
        raise EuParseError("not a Cellar case-law bundle")
    meta = parse_case_metadata(_bindings(parts, "sparql:metadata"), celex)
    files = [p for p in parts if p.role == "item:xhtml"]
    if len(files) != 1:
        raise EuParseError(f"{celex}: expected one XHTML file, the bundle has {len(files)}")
    try:
        root = parse_xhtml(files[0].data)
    except Exception as exc:
        raise EuParseError(f"{celex}: XHTML not well-formed or unsafe: {exc}") from exc
    text_all = " ".join(root.itertext())
    designations, parties, titles = language_facts(_bindings(parts, "sparql:items:xhtml"), text_all)
    if language not in designations or language not in parties:
        raise EuParseError(f"{celex}: incomplete bundle for language {language}")
    charter = "none"
    if CHARTER_NAME.search(text_all):
        charter = "full" if OTHER_CHARTER.search(text_all) else "short"
    if root.find(f"{XH}head/{XH}meta[@content='html-simplified; charset=utf-8']") is not None:
        blocks = parse_simplified(root, language, charter)
    else:
        blocks = parse_modern(root, language, charter)
    paragraphs = assign_ids(blocks)

    first = "en" if "en" in designations else language
    docket = DESIGNATION_PREFIX.sub("", designations[first])
    case_name = parties[first]
    short_name = bundle.get("short_name")
    work_uri = f"/eu/case/{meta.ecli}"
    title = titles.get(language) or case_name
    ext: dict[str, Any] = {
        "celex": celex,
        "ecli": meta.ecli,
        "resource_type": "JUDG",
        "designations": designations,
        "short_name": short_name,
        "procedure_language": meta.procedure_language,
    }
    return [
        ParsedDocument(
            doc_kind="judgment",
            instrument_type="judgment",
            jurisdiction_id="EU",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/{LANG3[language].lower()}@{meta.decision_date.isoformat()}",
            work_title=title,
            title=title,
            short_title=short_name,
            number=docket,
            work_date=meta.decision_date,
            language=language,
            authoritative=meta.procedure_language == language,
            text_origin="official_conversion",
            consolidation_kind="as_published",
            version_date=meta.decision_date,
            currency_date=None,
            extent=["EU"],
            ext_module="eu",
            ext={k: v for k, v in ext.items() if v is not None},
            judgment=ParsedJudgmentMeta(
                court_id=meta.court_id,
                case_name=case_name,
                neutral_citation=meta.ecli,
                decision_date=meta.decision_date,
                docket=docket,
            ),
            paragraphs=paragraphs,
        )
    ]
