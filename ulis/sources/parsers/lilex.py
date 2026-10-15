"""Parser for the consolidated law of Liechtenstein published on Lilex (https://www.gesetze.li).

Input is the bundle written by ``ulis.sources.connectors.lilex``: the text of one version of one law
exactly as Lilex serves it (``html``) plus the version number and the dates the version list prints
for it. The text of a version does not state the dates it applied; the list does:

* ``01.02.2021 - 31.12.2025``: applied from 1 February 2021 to 31 December 2025 inclusive;
* ``01.01.2026`` (the last, open entry): applies from 1 January 2026.

Every version is the whole law as amended (``validity_basis = "revision_snapshot"``, the shared
snapshot mechanism of ``ulis.ingestion.snapshots``): provisions are dated from the version, a wording
that did not change continues across versions, a provision a later version no longer contains ends
there. Versions of a law are ingested oldest first.

Markup (class names as Lilex writes them): ``htit1``/``vom``/``htit2`` the title; ``einl`` the enacting
formula; ``tit1m``, ``tit1``, ``tit2``, ``tit3``, ``tit1ue``, ``tits`` headings; ``art`` an article with
its ``sacht`` heading, ``abs`` paragraphs ("1) ..."), ``bst1`` letter points ("a) ...") and ``ziff``
numbered points ("1. ..."). Footnote marks (``<a href="#fnN">``) are removed from the text and the
footnote text - the amendment history of the place ("Art. 1 Abs. 1 Bst. d aufgehoben durch LGBl. 2018
Nr. 112") - becomes the provision's note. Blocks the parser does not know are kept as plain text of the
enclosing unit, never dropped.

The text is a consolidation for information: Lilex states "Konsolidierte Rechtsvorschriften dienen
ausschliesslich der Information und entfalten keine Rechtswirkung"; the authentic text is the
Landesgesetzblatt. ``authoritative`` is therefore false.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from ulis.ingestion.snapshots import SNAPSHOT_BASIS
from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision
from ulis.sources.parsers.legal_html import (
    HtmlParseError,
    Node,
    Rendered,
    Unit,
    build_provisions,
    key_of,
    parse_html,
    render,
    ws,
)

PARSER_VERSION = "1"
JURISDICTION = "LI"
BUNDLE_SCHEMA = "ulis-li-lilex-bundle/1"

MONTHS = {
    "Januar": 1,
    "Februar": 2,
    "März": 3,
    "April": 4,
    "Mai": 5,
    "Juni": 6,
    "Juli": 7,
    "August": 8,
    "September": 9,
    "Oktober": 10,
    "November": 11,
    "Dezember": 12,
}
HEADING_RANK = {"tit1m": 0, "tit1": 1, "tit1ue": 1, "tits": 1, "tit2": 2, "tit3": 3}
ABS_LABEL = re.compile(r"^(\d+[a-z]*)\)\s*")
BST_LABEL = re.compile(r"^([a-z]{1,3}\d?)\)\s*")
ZIFF_LABEL = re.compile(r"^(\d+[a-z]*)\.\s+")
REPEALED = re.compile(r"^aufgehoben\.?$", re.IGNORECASE)
ART_NUMBER = re.compile(r"^Art\.\s*(\d+[a-z]*(?:bis|ter|quater)?)\b", re.IGNORECASE)


def work_uri(lgbl: str) -> str:
    return f"/li/lgbl/{lgbl}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    return None  # ULIS citation targets use the generic ``<work>/~<eId>`` form


def german_date(text: str) -> date | None:
    m = re.search(r"(\d{1,2})\.\s*(\w+)\s+(\d{4})", text)
    if not m or m.group(2) not in MONTHS:
        return None
    return date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))


def dotted_date(text: str) -> date:
    d, m, y = (int(x) for x in text.split("."))
    return date(y, m, d)


def version_dates(label: str) -> tuple[date, date | None]:
    """(first day, last day inclusive or ``None``) from a printed version label."""
    m = re.fullmatch(r"(\d{2}\.\d{2}\.\d{4})(?:\s*-\s*(\d{2}\.\d{2}\.\d{4}))?", label.strip())
    if not m:
        raise HtmlParseError(f"unreadable version dates {label!r}")
    return dotted_date(m.group(1)), (dotted_date(m.group(2)) if m.group(2) else None)


# --------------------------------------------------------------------------- text


def _is_mark(el: Node) -> bool:
    return el.tag == "a" and el.attrs.get("href", "").startswith("#fn")


def _skip(el: Node) -> bool:
    return el.tag == "a" and "name" in el.attrs and not el.attrs.get("href") and not el.children


def own_render(node: Node, block_classes: tuple[str, ...]) -> Rendered:
    """The text of ``node`` without its nested block elements (the points of a paragraph)."""
    clone = Node(node.tag, node.attrs, None)
    clone.children = [
        c for c in node.children if not (isinstance(c, Node) and (c.classes & set(block_classes) or c.tag == "table"))
    ]
    return render(clone, skip=_skip, note=_is_mark, superscript=False)


def full_render(node: Node) -> Rendered:
    return render(node, skip=_skip, note=_is_mark, superscript=False)


def footnotes(body: Node) -> dict[str, str]:
    out: dict[str, str] = {}
    for div in body.find_all(lambda n: n.tag == "div" and "fntext" in n.classes):
        first = div.find(lambda n: n.tag == "a" and n.attrs.get("name", "").startswith("fn"))
        if first is None:
            continue
        number = ws("".join(c for c in first.children if isinstance(c, str)))
        clone = Node("div", {}, None)
        clone.children = [c for c in div.children if c is not first]
        out[number] = ws(render(clone, superscript=False).text)
    return out


class _Notes:
    """Collects the footnote texts a unit refers to."""

    def __init__(self, table: dict[str, str]):
        self.table = table

    def of(self, marks: list[str]) -> list[str]:
        return [self.table[m] for m in marks if m in self.table]


def _label_and_text(text: str, pattern: re.Pattern[str]) -> tuple[str | None, str]:
    m = pattern.match(text)
    return (m.group(1), text[m.end() :]) if m else (None, text)


def _points(div: Node, notes: _Notes, unit: Unit, depth: int = 0) -> None:
    """Append the letter and number points found in ``div`` to ``unit`` (recursively)."""
    for child in div.elements():
        if child.tag != "div":
            continue
        cls = next((c for c in child.classes if re.fullmatch(r"(bst|ziff)\d*", c)), None)
        if cls is None:
            continue
        pattern, tag, kind = (BST_LABEL, "bst", "point") if cls.startswith("bst") else (ZIFF_LABEL, "ziff", "subpoint")
        r = own_render(child, ("bst1", "bst2", "bst3", "ziff", "ziff2"))
        label, text = _label_and_text(r.text, pattern)
        if label is None:
            # no number: keep the text with the enclosing unit rather than inventing one
            unit.content.append(r.text)
            unit.notes.extend(notes.of(r.notes))
            continue
        point = Unit(kind, tag, key_of(label), label=f"{label}{')' if tag == 'bst' else '.'}")
        if text:
            point.content.append(text)
        point.notes.extend(notes.of(r.notes))
        _points(child, notes, point, depth + 1)
        if REPEALED.match(point.text()) and not point.children():
            point.repealed = True
            point.content = []
        unit.content.append(point)


def _paragraphs(div: Node, notes: _Notes, unit: Unit) -> None:
    abs_divs = [c for c in div.elements() if c.tag == "div" and "abs" in c.classes]
    other = [
        c
        for c in div.elements()
        if c.tag == "div" and not (c.classes & {"abs", "sacht", "bst1", "bst2", "bst3", "ziff", "ziff2"})
    ]
    for i, a in enumerate(abs_divs, start=1):
        r = own_render(a, ("bst1", "bst2", "bst3", "ziff", "ziff2"))
        label, text = _label_and_text(r.text, ABS_LABEL)
        if label is None and len(abs_divs) == 1:
            # a single unnumbered paragraph is the text of the article itself
            if text:
                unit.content.append(text)
            unit.notes.extend(notes.of(r.notes))
            _points(a, notes, unit)
            continue
        para = Unit("paragraph", "abs", key_of(label) if label else str(i), label=f"{label})" if label else "")
        if text:
            para.content.append(text)
        para.notes.extend(notes.of(r.notes))
        _points(a, notes, para)
        if REPEALED.match(para.text()) and not para.children():
            para.repealed = True
            para.content = []
        unit.content.append(para)
    # points placed directly in the article (no paragraphs)
    _points(div, notes, unit)
    for o in other:
        r = full_render(o)
        if r.text:
            unit.content.append(r.text)
        unit.notes.extend(notes.of(r.notes))


def build_article(div: Node, notes: _Notes) -> Unit:
    head = ws("".join(c for c in div.children if isinstance(c, str)))
    m = ART_NUMBER.match(head)
    if not m:
        raise HtmlParseError(f"article without a number: {head[:60]!r}")
    unit = Unit("article", "art", key_of(m.group(1)))
    marks = [ws(full_render(c).text) for c in div.elements() if _is_mark(c)]
    unit.notes.extend(notes.of([x for x in marks if x]))
    heading = div.find(lambda n: n.tag == "div" and "sacht" in n.classes)
    if heading is not None:
        r = full_render(heading)
        unit.heading = r.text or None
        unit.notes.extend(notes.of(r.notes))
    _paragraphs(div, notes, unit)
    if REPEALED.match(unit.text()) and not unit.children():
        unit.repealed = True
        unit.content = []
    return unit


# --------------------------------------------------------------------------- document


def parse_bundle(content: bytes) -> dict[str, Any]:
    try:
        bundle = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HtmlParseError(f"not a Lilex bundle: {exc}") from exc
    if not isinstance(bundle, dict) or bundle.get("schema") != BUNDLE_SCHEMA:
        raise HtmlParseError("not a Lilex bundle (schema mismatch)")
    for key in ("lgbl", "version", "dates", "html"):
        if key not in bundle:
            raise HtmlParseError(f"bundle without {key!r}")
    return bundle


def instrument_type_of(title: str) -> str:
    first = title.split()[0] if title.split() else ""
    if first == "Verfassung":
        return "constitution"
    if first == "Verfassungsgesetz":
        return "constitutional_act"
    if first in ("Gesetz", "Hausgesetz"):
        return "act"
    if first in ("Verordnung", "Fürstliche", "Kundmachung", "Regierungsverordnung"):
        return "regulation"
    return "other"


def abbreviation_of(title: str) -> str | None:
    """The abbreviation printed in a parenthesis of the title ("(Urheberrechtsgesetz, URG)" -> "URG").

    The last parenthesis that ends in an abbreviation (at least two capitals, no spaces) wins; "(LGBl. 1964
    Nr. 4)" and similar references are not abbreviations.
    """
    for group in reversed(re.findall(r"\(([^()]*)\)", title)):
        candidate = re.split(r"[,;]", group)[-1].strip()
        if re.fullmatch(r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9\-/]{1,13}", candidate) and sum(c.isupper() for c in candidate) >= 2:
            return candidate
    return None


def parse_lilex(content: bytes) -> list[ParsedDocument]:
    bundle = parse_bundle(content)
    lgbl = str(bundle["lgbl"])
    start, last_day = version_dates(str(bundle["dates"]))
    valid_to = last_day + timedelta(days=1) if last_day else None
    root = parse_html(str(bundle["html"]).encode("utf-8"))
    html_el = root.find(lambda n: n.tag == "html")
    body = html_el.find(lambda n: n.tag == "body") if html_el is not None else None
    if body is None:
        raise HtmlParseError(f"{lgbl}: no document body")
    table = footnotes(body)
    notes = _Notes(table)

    title_parts: list[str] = []
    work_date: date | None = None
    lr_number = published = issued = None
    tops: list[tuple[Unit, str | None]] = []
    headings: list[tuple[int, str]] = []
    sequence = {"txt": 0, "ueb": 0, "anh": 0}
    seen_article = False
    transitional = False  # inside a "Übergangsbestimmungen" heading: loose paragraphs are transitional provisions

    def context() -> str | None:
        return " › ".join(h for _, h in headings) or None

    for el in body.elements():
        if el.tag in ("hr", "script") or "fntext" in el.classes:
            continue
        cls = next(iter(sorted(el.classes)), "")
        if el.tag != "div":
            continue
        if not cls:
            text_here = full_render(el).text
            header = el.find(lambda n: n.tag == "table")
            if header is not None and "Landesgesetzblatt" in text_here and not seen_article and not tops:
                cells = [ws(render(c).text) for c in header.find_all(lambda n: n.tag in ("td", "th"))]
                lr_number = next((c for c in cells if re.fullmatch(r"\d+(\.\d+)*", c)), None)
                joined = " ".join(cells)
                m = re.search(r"Jahrgang\s+(\d{4})\s+Nr\.\s+(\d+)\s+ausgegeben am\s+(.+)$", joined)
                if m:
                    published = f"LGBl. {m.group(1)} Nr. {m.group(2)}"
                    issued = german_date(m.group(3))
                continue
            if not text_here:
                continue
            sequence["anh"] += 1
            kind = "closing" if el.find(lambda n: bool(n.classes & {"udat", "gezf", "gezr"})) else "annex"
            unit = Unit(kind, "anh", str(sequence["anh"]))
            unit.content.append(text_here)
            unit.notes.extend(notes.of(full_render(el).notes))
            unit.heading = None
            tops.append((unit, context()))
            continue
        if cls == "htit1" or cls == "htit2":
            if not seen_article:
                title_parts.append(full_render(el).text.replace("\n", " "))
            continue
        if cls == "vom":
            text = full_render(el).text
            if not seen_article:
                title_parts.append(text)
                work_date = work_date or german_date(text)
            continue
        if cls == "einl":
            r = full_render(el)
            unit = Unit("preamble", "", "preamble")
            unit.content.append(r.text)
            unit.notes.extend(notes.of(r.notes))
            tops.append((unit, None))
            continue
        if cls in HEADING_RANK:
            r = full_render(el)
            rank = HEADING_RANK[cls]
            transitional = cls == "tit1ue" or (transitional and rank > 1)
            while headings and headings[-1][0] >= rank:
                headings.pop()
            headings.append((rank, r.text.replace("\n", " ")))
            continue
        if cls == "art":
            tops.append((build_article(el, notes), context()))
            seen_article = True
            transitional = False
            continue
        if cls == "abs":
            r = own_render(el, ("bst1", "bst2", "bst3", "ziff", "ziff2"))
            label, text = _label_and_text(r.text, ABS_LABEL)
            if transitional:
                sequence["ueb"] += 1
                unit = Unit("transitional", "ueb", key_of(label) if label else str(sequence["ueb"]), label="")
            elif label:  # a law without articles: its numbered paragraphs are the top-level provisions
                unit = Unit("paragraph", "abs", key_of(label), label="")
            else:
                sequence["txt"] += 1
                unit = Unit("text", "txt", str(sequence["txt"]))
            if text:
                unit.content.append(text)
            unit.notes.extend(notes.of(r.notes))
            _points(el, notes, unit)
            tops.append((unit, context()))
            continue
        # any other block (ab, sacht, zent, ...): kept as text, never dropped
        r = full_render(el)
        if r.text:
            sequence["txt"] += 1
            unit = Unit("text", "txt", str(sequence["txt"]))
            unit.content.append(r.text)
            unit.notes.extend(notes.of(r.notes))
            tops.append((unit, context()))

    if not any(u.text() for u, _ in tops):
        raise HtmlParseError(f"{lgbl}: no text found")
    title = ws(" ".join(title_parts))
    if not title:
        raise HtmlParseError(f"{lgbl}: no title")
    provisions: list[ParsedProvision] = build_provisions(
        tops, extent=[JURISDICTION], valid_from=start, valid_to=valid_to
    )
    abbreviation = abbreviation_of(title)
    short = abbreviation or re.split(r"\s+vom\s", title, maxsplit=1)[0]
    year, _, number = lgbl.partition(".")
    base = work_uri(lgbl)
    events = [
        *([ParsedEvent("enactment", work_date, None, None, "title/vom")] if work_date else []),
        ParsedEvent("version_change", start, None, f"Lilex version {bundle['version']}", "version list"),
    ]
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=instrument_type_of(title),
            jurisdiction_id=JURISDICTION,
            work_uri=base,
            expression_uri=f"{base}/deu@{start.isoformat()}",
            work_title=title,
            title=title,
            short_title=short,
            number=lgbl,
            work_date=work_date,
            language="de",
            authoritative=False,
            text_origin="official_consolidation",
            consolidation_kind="point_in_time",
            version_date=start,
            currency_date=None,
            extent=[JURISDICTION],
            ext_module="li",
            ext={
                "lgbl": lgbl,
                "lgbl_year": int(year),
                "lgbl_number": int(number),
                **({"lr_number": lr_number} if lr_number else {}),
                **({"abbreviation": abbreviation} if abbreviation else {}),
                **({"published_in": published} if published else {}),
                **({"issued_on": issued.isoformat()} if issued else {}),
                "version_number": int(bundle["version"]),
                "version_label": str(bundle["dates"]),
            },
            outstanding_changes=[],
            events=events,
            provisions=provisions,
            validity_basis=SNAPSHOT_BASIS,
        )
    ]
