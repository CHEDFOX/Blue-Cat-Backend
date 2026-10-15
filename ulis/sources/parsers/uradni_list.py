"""Parser for acts published in Uradni list RS (https://www.uradni-list.si), the official gazette of Slovenia.

Input is the HTML page of one act (``/glasilo-uradni-list-rs/vsebina/<year>-<part>-<number>``) exactly as the
publisher
serves it: a title line ("3978. Zakon o varstvu javnega reda in miru (ZJRM-2), stran 13596."), the issue line
("Uradni list
RS, št. 112/2025 z dne 29. 12. 2025"), a canonical address, and the text in ``div.content-segments`` as a sequence of
``div.esegment_<kind>`` blocks (``p`` paragraph, ``h4`` article number / heading / part heading, ``t`` title, ``p1``
and ``c1``
signature lines; Uradni list writes the newer kinds with a trailing ``x``).

What the pages are: the act *as published* in the issue named on the page - laws, the promulgation decree (ukaz)
before
them, and the official consolidated texts ("uradno prečiščeno besedilo", UPB) that the gazette publishes for some
laws.
Uradni list RS does not consolidate: an amendment is a separate act in a later issue and the page of the amended law
is
never updated, so every act is ingested as published with that stated as an outstanding change on the instrument. The
page states neither an entry-into-force date (it is in the text, usually the last article) nor an end of validity,
so no
validity period is recorded.

Structure: an article starts with a ``h4`` block "12. člen" (inserted articles "12.a člen") followed by a ``h4``
block "(heading)";
a paragraph ("odstavek") with "(2)", a point ("točka") with "1." and a sub-point ("podtočka") with "a)", as in
``ulis.sources.parsers.legal_html.NumberedArticle``. Everything before the first article (the decree and the title)
is the
preamble; blocks after the last article that are signature lines (``p1``/``c1`` or "Št. ...") are the closing text;
"PRILOGA"
starts an annex. Lines that match no numbering stay text of the open unit; nothing is dropped or rewritten.
"""

from __future__ import annotations

import re
from datetime import date

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision
from ulis.sources.parsers.legal_html import (
    HtmlParseError,
    Node,
    NumberedArticle,
    Unit,
    build_provisions,
    mark_repealed,
    parse_html,
    render,
    ws,
)

PARSER_VERSION = "1"
JURISDICTION = "SI"

CANONICAL = re.compile(r"/glasilo-uradni-list-rs/vsebina/(\d{4})-(\d{2})-(\d+)(?:/|$)")
ACT_TITLE = re.compile(r"^(\d+)\.\s+(.+?),\s*stran\s+\d+\.?$", re.DOTALL)
ISSUE_LINE = re.compile(r"^Uradni list RS,\s*št\.\s*(\d+)/(\d{4})\s+z dne\s+(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})$")
ARTICLE = re.compile(r"^(\d+)\.\s?([a-zčšž]{0,2})\.?\s*člen$", re.IGNORECASE)
REPEALED = re.compile(r"^\(?\s*(črtan[aoei]?|prenehal[aoi]? veljati)\s*\)?\.?$", re.IGNORECASE)
ANNEX = re.compile(r"^(PRILOGA|Priloga)\b")
STRUCTURAL = re.compile(r"^(?:[IVXLC]+\.\s|[A-ZČŠŽ]\.\s|DEL\b|POGLAVJE\b)")
SUBSTRUCTURAL = re.compile(r"^(?:\d+\.\s|[A-Za-zčšž]\)\s|\d+\.\d+\s)")
ABBREVIATION = re.compile(r"\(([A-ZČŠŽ][A-Za-zČŠŽčšž0-9\-]{1,14})\)\s*$")
SEGMENT = re.compile(r"^esegment_([a-z0-9]+?)x?$")


def rank_of(text: str) -> int | None:
    """Heading rank: 1 for parts and chapters, 2 for sections, ``None`` for a plain line."""
    t = text.strip()
    if STRUCTURAL.match(t) or (t.isupper() and len(t) > 3):
        return 1
    if SUBSTRUCTURAL.match(t):
        return 2
    return None


class Segment:
    __slots__ = ("kind", "text")

    def __init__(self, kind: str, text: str):
        self.kind, self.text = kind, text


def segments_of(root: Node) -> list[Segment]:
    holder = root.find(lambda n: n.tag == "div" and "content-segments" in n.classes)
    if holder is None:
        raise HtmlParseError("no act text (div.content-segments)")
    out: list[Segment] = []
    for el in holder.elements():
        kind = next((m.group(1) for c in el.classes if (m := SEGMENT.match(c))), None)
        if el.tag != "div" or kind is None:
            continue
        text = render(el).text
        if text:
            out.append(Segment(kind, text))
    return out


def parse_uradni_list(content: bytes) -> list[ParsedDocument]:
    root = parse_html(content)
    canonical = root.find(lambda n: n.tag == "link" and n.attrs.get("rel") == "canonical")
    m = CANONICAL.search(canonical.attrs.get("href", "")) if canonical is not None else None
    if m is None:
        raise HtmlParseError("not an Uradni list RS act page (no canonical address)")
    year, part, number_in_year = int(m.group(1)), m.group(2), int(m.group(3))
    ul_id = f"{year}-{part}-{number_in_year}"
    h2s = [ws(render(n).text) for n in root.find_all(lambda n: n.tag == "h2" and "ul-content-title" in n.classes)]
    issue = next((ISSUE_LINE.match(h) for h in h2s if ISSUE_LINE.match(h)), None)
    heading = next((ACT_TITLE.match(h) for h in h2s if ACT_TITLE.match(h)), None)
    if issue is None or heading is None:
        raise HtmlParseError(f"{ul_id}: no issue line or act title")
    if int(heading.group(1)) != number_in_year or int(issue.group(2)) != year:
        raise HtmlParseError(f"{ul_id}: the title names another act ({heading.group(1)}) or year")
    title = ws(heading.group(2))
    published = date(int(issue.group(5)), int(issue.group(4)), int(issue.group(3)))
    issue_ref = f"{issue.group(1)}/{issue.group(2)}"
    consolidated = bool(re.search(r"uradno prečiščeno besedilo", title, re.IGNORECASE))
    first_word = title.split()[0] if title.split() else ""
    if title.startswith("Ustavni zakon"):
        instrument_type = "constitutional_act"
    elif first_word == "Ustava":
        instrument_type = "constitution"
    elif first_word == "Zakon":
        instrument_type = "act"
    elif first_word in ("Uredba", "Pravilnik", "Odredba"):
        instrument_type = "regulation"
    else:
        instrument_type = "other"

    segments = segments_of(root)
    if not segments:
        raise HtmlParseError(f"{ul_id}: the page has no text")

    preamble = Unit("preamble", "", "preamble")
    tops: list[tuple[Unit, str | None]] = []
    headings: list[tuple[int, str]] = []
    article: NumberedArticle | None = None
    tail: Unit | None = None
    annexes = 0
    body_started = False  # the article's heading line "(…)" may follow its number

    def context() -> str | None:
        return " › ".join(h for _, h in headings) or None

    def push_heading(rank: int, line: str) -> None:
        while headings and headings[-1][0] >= rank:
            headings.pop()
        headings.append((rank, line))

    def finish_article() -> None:
        if article is not None:
            mark_repealed(article.unit, REPEALED)

    for seg in segments:
        first = seg.text.split("\n", 1)[0].strip()
        if tail is None and seg.kind == "h4" and (am := ARTICLE.match(first)):
            finish_article()
            unit = Unit("article", "art", f"{am.group(1)}{am.group(2).lower()}")
            article = NumberedArticle(unit)
            tops.append((unit, context()))
            body_started = False
            continue
        if article is None:
            if seg.kind == "h4":  # a part heading before the first article
                push_heading(rank_of(first) or 3, seg.text.replace("\n", " "))
            else:
                preamble.content.append(seg.text)
            continue
        if tail is None and (seg.kind in ("p1", "c1") or re.match(r"^Št\.\s", first)):
            finish_article()
            tail = Unit("closing", "zav", "1")
            tops.append((tail, None))
            tail.content.append(seg.text)
            continue
        if ANNEX.match(first) and seg.kind in ("t", "h4", "p", "c1"):
            finish_article()
            annexes += 1
            tail = Unit("annex", "pril", str(annexes), heading=first)
            tops.append((tail, None))
            continue
        if tail is not None:
            tail.content.append(seg.text)
            continue
        if seg.kind == "h4":
            if not body_started and first.startswith("(") and first.endswith(")") and article.unit.heading is None:
                article.unit.heading = first[1:-1].strip()
                continue
            # a part or section heading before the next article
            rank = rank_of(first) or 3
            push_heading(rank, seg.text.replace("\n", " "))
            continue
        body_started = True
        for line in seg.text.split("\n"):  # a block with <br> breaks holds several numbered lines
            article.add(line)
    finish_article()

    if not tops:
        if not preamble.content:
            raise HtmlParseError(f"{ul_id}: no text")
        preamble.kind, preamble.key = "text", "preamble"
        tops.append((preamble, None))
    elif preamble.content:
        tops.insert(0, (preamble, None))

    provisions: list[ParsedProvision] = build_provisions(tops, extent=[JURISDICTION], valid_from=None, valid_to=None)
    am_ = ABBREVIATION.search(re.sub(r"\s*\(uradno prečiščeno besedilo\)\s*(?=\()", " ", title, flags=re.IGNORECASE))
    abbreviation = am_.group(1) if am_ else None
    base = f"/si/ul/{ul_id}"
    outstanding = [
        {
            "affects_eid": None,
            "affected_label": "Amendments published after this issue are not applied",
            "by_uri": None,
            "by_title": f"Later issues of Uradni list RS (Uradni list RS, št. {issue_ref} is the text as published)",
            "description": (
                "Uradni list RS does not consolidate: the page of an act is never updated when a later act amends or "
                f"repeals it. This text is the act as published on {published.isoformat()}; whether it is still in "
                "force, and in what wording, is not stated."
            ),
        }
    ]
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=instrument_type,
            jurisdiction_id=JURISDICTION,
            work_uri=base,
            expression_uri=f"{base}/slv@{published.isoformat()}",
            work_title=title,
            title=title,
            short_title=abbreviation,
            number=f"Uradni list RS, št. {issue_ref}",
            work_date=None,
            language="sl",
            authoritative=False,
            text_origin="official_consolidation" if consolidated else "official_conversion",
            consolidation_kind="as_published",
            version_date=published,
            currency_date=None,
            extent=[JURISDICTION],
            ext_module="si",
            ext={
                "ul_id": ul_id,
                "ul_year": year,
                "ul_part": part,
                "act_number": number_in_year,
                "issue": issue_ref,
                "ul_citation": f"Uradni list RS, št. {issue_ref}",
                "consolidated_text": consolidated,
                **({"abbreviation": abbreviation} if abbreviation else {}),
            },
            outstanding_changes=outstanding,
            events=[ParsedEvent("publication", published, None, f"Uradni list RS, št. {issue_ref}", "issue line")],
            provisions=provisions,
        )
    ]
