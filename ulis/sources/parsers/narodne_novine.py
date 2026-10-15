"""Parser for acts published in Narodne novine (https://narodne-novine.nn.hr), the official gazette of Croatia.

Input is the HTML page of one act at its ELI address (``/eli/sluzbeni/<year>/<edition>/<number>``) exactly as
the publisher serves it. The page carries the act's ELI metadata as RDFa ``<meta>`` elements (title, adoption date,
publication date, relations to other acts) and the text in ``div.sl-content``.

What the pages are: the act *as published* in the edition of the gazette named in the address (laws, constitutional
laws, the Constitution, and the "pročišćeni tekst" - consolidated texts - that the gazette publishes for some laws).
Narodne novine does not consolidate: an amendment is a separate act in a later edition, and the page of the amended
law is never updated. Every such act is therefore ingested as published, with this stated as an outstanding change
on the instrument; the source gives no end date and no entry-into-force date in its metadata (the date is in the
text of the act, usually its last article), so no validity period is recorded.

The markup varies with the age of the act (Word conversions with ``MsoPlainText`` paragraphs for the 1990s and early
2000s, typographic classes such as ``Clanak--`` and ``T-9-8 pleft`` since). The parser therefore reads the text, not the
class names, except for centred paragraphs (headings): an article starts with a line "Članak 12." or "Članak 12.a",
a paragraph ("stavak") with "(2)", a point ("točka") with "1." or "1)" and a sub-point ("podtočka") with "a)" - points
only where a list is open (the line before ends with a colon, or a point precedes). Everything before the first article
(the promulgation decree, the title and any preamble) is the preamble; everything after "Klasa:" the closing text
(signature); "Prilog" starts an annex. A line that matches no numbering stays text of the open unit; nothing is dropped
or rewritten.
"""

from __future__ import annotations

import re
from datetime import date

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision
from ulis.sources.parsers.legal_html import (
    HtmlParseError,
    Node,
    Unit,
    build_provisions,
    parse_html,
    render,
    ws,
)

PARSER_VERSION = "1"
JURISDICTION = "HR"
ELI_BASE = "https://narodne-novine.nn.hr/eli/"
ELI_NS = "http://data.europa.eu/eli/ontology#"

ARTICLE = re.compile(r"^Članak\s+(\d+)\s*\.?\s*([a-z]{0,2})\s*\.?$", re.IGNORECASE)
PARAGRAPH = re.compile(r"^\((\d+[a-z]?)\)\s*(.*)$", re.DOTALL)
POINT = re.compile(r"^(\d+)[.)]\s+(\S.*)$", re.DOTALL)
SUBPOINT = re.compile(r"^([a-zšćčđž]{1,2})\)\s+(\S.*)$", re.DOTALL)
REPEALED = re.compile(r"^\(?\s*brisan[oaie]?\s*\)?\.?$", re.IGNORECASE)
CLOSING = re.compile(r"^(Klasa|KLASA|Urbroj|URBROJ|Broj|BROJ)\s*:|^(U\s+)?Zagreb(u)?,\s")
ANNEX = re.compile(r"^(PRILOG|Prilog|DODATAK|Dodatak)\b")
STRUCTURAL = re.compile(
    r"^(?:[IVXLC]+\.\s|(?:PRVI|DRUGI|TREĆI|ČETVRTI|PETI|ŠESTI|SEDMI|OSMI|DEVETI|DESETI)\s+DIO\b|DIO\b|GLAVA\b|POGLAVLJE\b|NASLOV\b)"
)
SUBSTRUCTURAL = re.compile(r"^(?:\d+\.\s|[A-ZŠĐČĆŽ]\)\s|ODJELJAK\b|ODSJEK\b|Odjeljak\b|Odsjek\b)")
CENTRED = re.compile(r"(?:pcenter|sred|curz|kurz|mn1|mn2|center)", re.IGNORECASE)


# --------------------------------------------------------------------------- metadata


def eli_metadata(root: Node) -> dict[str, list[str]]:
    """The ELI properties (short name -> values) of the act itself, from the RDFa ``<meta>`` elements."""
    metas = root.find_all(lambda n: n.tag == "meta" and "property" in n.attrs and "about" in n.attrs)
    about = None
    for m in metas:
        if m.attrs["property"] == ELI_NS + "type_document":
            about = m.attrs["about"]
            break
    if about is None:
        return {}  # older acts carry no ELI block in the page
    out: dict[str, list[str]] = {}
    for m in metas:
        prop = m.attrs["property"]
        if not prop.startswith(ELI_NS):
            continue
        short = prop[len(ELI_NS) :]
        subject = m.attrs["about"]
        value = m.attrs.get("content") or m.attrs.get("resource") or ""
        if subject == about or (short == "title" and subject == about + "/hrv"):
            out.setdefault(short, []).append(value)
    out["_about"] = [about]
    return out


def _iso(values: list[str] | None) -> date | None:
    return date.fromisoformat(values[0]) if values else None


def eli_path(about: str) -> str:
    """``https://narodne-novine.nn.hr/eli/sluzbeni/2023/30/510`` -> ``sluzbeni/2023/30/510``."""
    if not about.startswith(ELI_BASE):
        raise HtmlParseError(f"unexpected ELI address {about!r}")
    return about[len(ELI_BASE) :].rstrip("/")


def relation_paths(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        path = re.sub(r"^https?:/+narodne-novine\.nn\.hr/eli/", "", v).rstrip("/")
        if re.fullmatch(r"[a-z]+/\d{4}/\d+/\d+", path) and path not in out:
            out.append(path)
    return out


# --------------------------------------------------------------------------- blocks


class Block:
    __slots__ = ("centred", "kind", "text")

    def __init__(self, kind: str, text: str, centred: bool):
        self.kind, self.text, self.centred = kind, text, centred


def _centred(node: Node) -> bool:
    if node.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return True
    style = (node.attrs.get("style") or "").replace(" ", "").lower()
    return (
        "text-align:center" in style
        or node.attrs.get("align", "").lower() == "center"
        or any(CENTRED.search(c) for c in node.classes)
    )


def blocks_of(content: Node) -> list[Block]:
    """The paragraphs, headings, list items and tables of the act text, in document order."""
    out: list[Block] = []

    def walk(node: Node) -> None:
        for child in node.elements():
            if child.tag in ("script", "style"):
                continue
            if child.tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre"):
                text = render(child).text
                if text:
                    out.append(Block("p", text, _centred(child)))
            elif child.tag == "table":
                text = render(child).text
                if text:
                    out.append(Block("table", text, False))
            else:
                direct = ws("".join(c for c in child.children if isinstance(c, str)))
                if direct and not any(e.tag in ("p", "table", "li") for e in child.elements()):
                    out.append(Block("p", render(child).text, _centred(child)))
                else:
                    walk(child)

    walk(content)
    return out


# --------------------------------------------------------------------------- units


class _Article:
    """The open article while its lines are read."""

    def __init__(self, unit: Unit):
        self.unit = unit
        self.para: Unit | None = None
        self.point: Unit | None = None
        self.sub: Unit | None = None

    def deepest(self) -> Unit:
        return self.sub or self.point or self.para or self.unit

    def last_line(self) -> str:
        holder = self.deepest()
        for item in reversed(holder.content):
            if isinstance(item, str):
                return item
            return ""  # the last content is a child unit
        return ""

    def add(self, text: str) -> None:
        if m := PARAGRAPH.match(text):
            para = Unit("paragraph", "par", m.group(1), label=f"({m.group(1)})")
            if m.group(2):
                para.content.append(m.group(2))
            self.unit.content.append(para)
            self.para, self.point, self.sub = para, None, None
            return
        listing = self.point is not None or self.last_line().rstrip().endswith(":")
        if listing and (m := POINT.match(text)):
            holder = self.para or self.unit
            point = Unit("point", "pt", m.group(1), label=f"{m.group(1)}.")
            point.content.append(m.group(2))
            holder.content.append(point)
            self.point, self.sub = point, None
            return
        if (self.point is not None or self.sub is not None or listing) and (m := SUBPOINT.match(text)):
            holder = self.point or self.para or self.unit
            sub = Unit("subpoint", "sub", m.group(1), label=f"{m.group(1)})")
            sub.content.append(m.group(2))
            holder.content.append(sub)
            self.sub = sub
            return
        self.deepest().content.append(text)


def _mark_repealed(unit: Unit) -> None:
    for child in unit.children():
        _mark_repealed(child)
    if not unit.children() and REPEALED.match(unit.own_text()):
        unit.repealed = True
        unit.content = []


def _article_key(m: re.Match[str]) -> str:
    return f"{m.group(1)}{m.group(2).lower()}"


def rank_of(text: str, *, allow_upper: bool = True) -> int | None:
    """Heading rank: 1 for parts and chapters, 2 for sections, ``None`` for a plain (article) heading."""
    t = text.strip()
    if STRUCTURAL.match(t) or (allow_upper and t.isupper() and len(t) > 3):
        return 1
    if SUBSTRUCTURAL.match(t):
        return 2
    return None


# --------------------------------------------------------------------------- document

TYPE_MAP = {"ZAKON": "act", "USTAV": "constitution"}


FULL_VIEW = re.compile(r"/clanci/sluzbeni/full/(\d{4})_(\d{2})_(\d+)_(\d+)\.html")
PRINT_TITLE = re.compile(r"^NN\s+(\d+)/(\d{4})\s+\((\d{1,2})\.(\d{1,2})\.(\d{4})\.\),\s*(.+)$", re.DOTALL)


def parse_narodne_novine(content: bytes) -> list[ParsedDocument]:
    root = parse_html(content)
    meta = eli_metadata(root)
    # Identity and publication date from the page itself (present for every act, old and new): the address of
    # the full view and the print title "NN 85/2010 (9.7.2010.), <title>"; the ELI block, where present, adds more.
    full = FULL_VIEW.search(content.decode("utf-8"))
    holder_title = root.find(lambda n: n.tag == "span" and "title-for-print" in n.classes)
    printed = PRINT_TITLE.match(ws(render(holder_title).text)) if holder_title is not None else None
    if full is None or printed is None:
        raise HtmlParseError("not a Narodne novine act page (no full-view address or print title)")
    year, edition, number = int(full.group(1)), int(full.group(3)), int(full.group(4))
    path = f"sluzbeni/{year}/{edition}/{number}"
    if (int(printed.group(1)), int(printed.group(2))) != (edition, year):
        raise HtmlParseError(f"{path}: the print title names another edition ({printed.group(1)}/{printed.group(2)})")
    if meta and eli_path(meta["_about"][0]) != path:
        raise HtmlParseError(f"{path}: the ELI metadata describe another act")
    title = ws((meta.get("title") or [printed.group(6)])[0])
    published = _iso(meta.get("date_publication")) or date(
        int(printed.group(5)), int(printed.group(4)), int(printed.group(3))
    )
    adopted = _iso(meta.get("date_document"))
    type_token = (meta.get("type_document") or [""])[0].rsplit("/", 1)[-1]
    consolidated = bool(re.search(r"pročišć?eni tekst", title, re.IGNORECASE))
    if title.startswith("Ustavni zakon"):
        instrument_type = "constitutional_act"
    elif title.startswith("Ustav "):
        instrument_type = "constitution"
    else:
        instrument_type = TYPE_MAP.get(type_token, "act")

    holder = root.find(lambda n: n.tag == "div" and "sl-content" in n.classes)
    if holder is None:
        raise HtmlParseError(f"{path}: no text (div.sl-content)")
    blocks = blocks_of(holder)
    if not blocks:
        raise HtmlParseError(f"{path}: the page has no text")

    preamble = Unit("preamble", "", "preamble")
    tops: list[tuple[Unit, str | None]] = []
    headings: list[tuple[int, str]] = []
    pending: list[str] = []
    article: _Article | None = None
    tail: Unit | None = None  # closing text or an annex, once reached
    annexes = 0

    def context() -> str | None:
        return " › ".join(h for _, h in headings) or None

    def finish_article() -> None:
        nonlocal article
        if article is not None:
            for line in pending:  # centred lines inside the article are its text
                article.deepest().content.append(line)
            pending.clear()
            _mark_repealed(article.unit)

    def push_heading(rank: int, line: str) -> None:
        while headings and headings[-1][0] >= rank:
            headings.pop()
        headings.append((rank, line.replace("\n", " ")))

    def take_pending(first_article: bool) -> str | None:
        """The centred lines before an article marker: structural headings go to the context, the last plain
        line is the article's heading (before the first article the other lines belong to the preamble)."""
        lines = pending[:]
        pending.clear()
        plain: list[str] = []
        for line in lines:
            rank = rank_of(line, allow_upper=not first_article)
            if rank is None:
                plain.append(line)
            else:
                push_heading(rank, line)
        for line in plain[:-1]:
            if first_article:
                preamble.content.append(line)
            else:
                push_heading(3, line)
        if plain and first_article and plain[-1].isupper():
            preamble.content.append(plain[-1])  # the title of the act ("O DOPUNAMA ZAKONA ..."), not a heading
            return None
        return plain[-1].replace("\n", " ") if plain else None

    for b in blocks:
        text = b.text
        first = text.split("\n", 1)[0].strip()
        if tail is None and (am := ARTICLE.match(first)) and "\n" not in text:
            heading = take_pending(article is None)
            finish_article()
            unit = Unit("article", "art", _article_key(am), heading=heading)
            article = _Article(unit)
            tops.append((unit, context()))
            continue
        centred_line = (
            b.centred and b.kind == "p" and text.count("\n") <= 2 and len(text) <= 240 and not PARAGRAPH.match(text)
        )
        if article is None:
            if centred_line:
                pending.append(text)
            else:
                preamble.content.extend(pending)
                pending.clear()
                preamble.content.append(text)
            continue
        if tail is None and CLOSING.match(first):
            finish_article()
            tail = Unit("closing", "zav", "1")
            tops.append((tail, None))
            tail.content.append(text)
            continue
        if ANNEX.match(first) and (b.centred or tail is not None):
            finish_article()
            annexes += 1
            tail = Unit("annex", "pril", str(annexes), heading=first)
            tops.append((tail, None))
            continue
        if tail is not None:
            tail.content.append(text)
            continue
        if centred_line:
            pending.append(text)
            continue
        for line in pending:
            article.deepest().content.append(line)
        pending.clear()
        for line in text.split("\n"):  # a block with <br> breaks holds several numbered lines (a), b), c))
            article.add(line)
    finish_article()

    if not tops:
        # no numbered articles (a short decision-like act): the whole text is the preamble
        if not preamble.content:
            raise HtmlParseError(f"{path}: no text")
        preamble.kind, preamble.key = "text", "preamble"
        tops.append((preamble, None))
    elif preamble.content:
        tops.insert(0, (preamble, None))

    provisions: list[ParsedProvision] = build_provisions(tops, extent=[JURISDICTION], valid_from=None, valid_to=None)
    amends = relation_paths(meta.get("amends", []))
    repeals = relation_paths(meta.get("repeals", []))
    base = f"/hr/nn/{year}/{edition}/{number}"
    nn = f"NN {edition}/{year}"
    outstanding = [
        {
            "affects_eid": None,
            "affected_label": "Amendments published after this edition are not applied",
            "by_uri": None,
            "by_title": f"Later editions of Narodne novine ({nn} is the text as published)",
            "description": (
                "Narodne novine does not consolidate: the page of an act is never updated when a later act amends "
                "or repeals it. This text is the act as published on "
                f"{published.isoformat()}; whether it is still in force, and in what wording, is not stated."
            ),
        }
    ]
    events = [
        *(
            [ParsedEvent("enactment", adopted, None, "date_document in the ELI metadata", "eli/date_document")]
            if adopted
            else []
        ),
        ParsedEvent("publication", published, None, nn, "eli/date_publication"),
    ]
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=instrument_type,
            jurisdiction_id=JURISDICTION,
            work_uri=base,
            expression_uri=f"{base}/hrv@{published.isoformat()}",
            work_title=title,
            title=title,
            short_title=None,
            number=nn,
            work_date=adopted,
            language="hr",
            authoritative=False,
            text_origin="official_consolidation" if consolidated else "official_conversion",
            consolidation_kind="as_published",
            version_date=published,
            currency_date=None,
            extent=[JURISDICTION],
            ext_module="hr",
            ext={
                "eli": path,
                "nn_year": year,
                "nn_edition": edition,
                "nn_number": number,
                "nn_citation": nn,
                "document_type": type_token or None,
                "consolidated_text": consolidated,
                **({"amends": amends} if amends else {}),
                **({"repeals": repeals} if repeals else {}),
            },
            outstanding_changes=outstanding,
            events=events,
            provisions=provisions,
        )
    ]
