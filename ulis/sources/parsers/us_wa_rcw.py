"""Parser for the Revised Code of Washington (RCW): one chapter page of https://app.leg.wa.gov/rcw/.

Input: the bundle (see :mod:`ulis.sources.us_state_bundle`) of ``default.aspx?cite=<chapter>&full=true``, the
page that prints every section of a chapter. Format notes:

* ``<div id='contentWrapper'>`` holds the chapter. Each section sits in a block that starts with an anchor
  ``<a name='9A.32.030'>``, followed by a heading block (``RCW 9A.32.030`` with a PDF button), a block with the
  section's caption, a block with the section's paragraphs (``(1) ...`` lines, one ``<div>`` each), a block
  with the session-law credit in square brackets (``[ 1990 c 200 s 1; ... ]``) and, for some sections, a
  ``NOTES:`` block of the Code Reviser's editorial notes.
* The text of a section is its paragraphs, exactly as printed (runs of spaces collapse). The credit and the
  notes are not law text: they go in the provision note. Chapter-level notes are not ingested.
* Cross references are links (``default.aspx?cite=46.61.520``); they become citation links.
* The page carries no edition date and no section start dates: the document is dated by its retrieval date
  (the Legislature presents the online RCW as current, updated twice a year) and attested as of that date
  (``validity_basis = "attested_at_edition"``). The certified PDF in the Code Reviser's RCW Archive is the
  official publication; this text is not.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from ulis.sources.normalized import ParsedDocument, ParsedRef
from ulis.sources.parsers.us_state_common import (
    Node,
    SectionDraft,
    StateParseError,
    build_provisions,
    parse_html,
    raw_text,
    state_document,
    ws,
)
from ulis.sources.us_state_bundle import read_bundle

PARSER_ID = "us_wa_rcw_html"
PARSER_VERSION = "1.0.0"
JURISDICTION = "US-WA"
MODULE = "us_wa"
SECTION_ANCHOR = re.compile(r"^([0-9]{1,2}[A-Z]{0,2}\.[0-9]{1,3}[A-Z]{0,2})\.([0-9]{1,4}[A-Z]{0,2})$")
CITE = re.compile(r"^([0-9]{1,2}[A-Z]{0,2})\.([0-9]{1,3}[A-Z]{0,2})(?:\.([0-9]{1,4}[A-Z]{0,2}))?$")


def work_uri(chapter: str) -> str:
    return f"/us-wa/rcw/{chapter}"


def section_eid(number: str) -> str:
    return f"sec-{number}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """``https://app.leg.wa.gov/RCW/default.aspx?cite=46.61.520`` -> (``/us-wa/rcw/46.61``, ``sec-46.61.520``)."""
    parts = urlsplit(uri)
    if parts.netloc.lower() != "app.leg.wa.gov" or not parts.path.lower().startswith("/rcw/"):
        return None
    cite = (parse_qs(parts.query, keep_blank_values=False).get("cite") or parse_qs(parts.query).get("Cite") or [""])[0]
    m = CITE.match(cite)
    if m is None:
        return None
    chapter = f"{m.group(1)}.{m.group(2)}"
    return (work_uri(chapter), section_eid(cite) if m.group(3) else None)


def _cite_ref(href: str, text: str) -> ParsedRef | None:
    target = resolve_uri(href)
    if target is None:
        return None
    work, eid = target
    return ParsedRef(href=f"{work}/~{eid}" if eid else work, text=ws(text), relation="cites")


def _blocks(node: Node) -> list[str]:
    """The lines of a body block: every innermost ``<div>`` (or table row) is one line."""
    inner = [c for c in node.elements() if c.tag in ("div", "table", "p", "ul", "ol")]
    if not inner:
        return [line for line in (ws(raw_text(node, br=" ")),) if line]
    out: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            if ws(child):
                out.append(ws(child))
        elif child.tag == "table":
            for row in child.find_all(lambda n: n.tag == "tr"):
                line = ws(" ".join(ws(raw_text(c, br=" ")) for c in row.elements() if c.tag in ("td", "th")))
                if line:
                    out.append(line)
        elif child.tag in ("div", "p", "ul", "ol", "li", "span"):
            out.extend(_blocks(child))
        elif child.tag != "br":
            line = ws(raw_text(child, br=" "))
            if line:
                out.append(line)
    return out


def _refs(node: Node) -> list[ParsedRef]:
    refs: dict[str, ParsedRef] = {}
    for a in node.find_all(lambda n: n.tag == "a" and bool(n.attrs.get("href"))):
        ref = _cite_ref(a.attrs["href"], raw_text(a))
        if ref is not None:
            refs.setdefault(ref.href, ref)
    return list(refs.values())


def _is_credit(node: Node) -> bool:
    text = ws(raw_text(node))
    return text.startswith("[") and text.endswith("]") and "margin-top" in node.attrs.get("style", "")


def parse_wa_rcw(content: bytes) -> list[ParsedDocument]:
    bundle = read_bundle(content, "WA")
    chapter = bundle.key
    root = parse_html(bundle.body_bytes.decode("utf-8-sig"))
    wrapper = root.find(lambda n: n.tag == "div" and n.attrs.get("id") == "contentWrapper")
    h1 = root.find(lambda n: n.tag == "h1" and "RCW" in raw_text(n))
    if wrapper is None or h1 is None or ws(raw_text(h1)) != f"Chapter {chapter} RCW":
        raise StateParseError(f"not the page of RCW chapter {chapter}")
    block = h1.parent.parent if h1.parent is not None and h1.parent.parent is not None else root
    caption_el = block.find(lambda n: n.tag == "h2")
    caption = ws(raw_text(caption_el)) if caption_el is not None else ""
    title_link = root.find(lambda n: n.tag == "a" and re.fullmatch(r"Title [0-9]+[A-Z]*", ws(raw_text(n))) is not None)
    rcw_title = ws(raw_text(title_link))[len("Title ") :] if title_link is not None else chapter.split(".")[0]

    drafts: list[SectionDraft] = []
    chapter_context = f"Chapter {chapter} RCW" + (f" - {caption}" if caption else "")
    subhead = ""
    # The page's <div> nesting is unbalanced, so section blocks are looked up in the whole page: they are the
    # elements that directly contain a section anchor.
    for span in root.iter():
        anchors = [c for c in span.elements() if c.tag == "a" and SECTION_ANCHOR.match(c.attrs.get("name", ""))]
        if not anchors:
            continue
        number = anchors[0].attrs["name"]
        if not number.startswith(chapter + "."):
            raise StateParseError(f"section {number} is not in chapter {chapter}")
        siblings = span.elements()
        rest = siblings[siblings.index(anchors[0]) + 1 :]
        all_divs = [c for c in rest if c.tag == "div"]
        # [centered division captions] "RCW <number>" (with the PDF button), the section caption, body, credit, notes.
        at = next((i for i, d in enumerate(all_divs) if ws(raw_text(d)).endswith(f"RCW {number}")), None)
        if at is None or at + 1 >= len(all_divs) or all_divs[at + 1].find(lambda n: n.tag == "h3") is None:
            raise StateParseError(f"RCW {number}: unexpected section layout")
        for caption_div in all_divs[:at]:
            division = ws(raw_text(caption_div, br=" "))
            if division:
                subhead = division
        divs = all_divs[at:]
        heading = ws(raw_text(divs[1])) or None
        lines: list[str] = []
        credit: str | None = None
        notes: list[str] = []
        refs: list[ParsedRef] = []
        in_notes = False
        for block in divs[2:]:
            head = block.find(lambda n: n.tag == "h3")
            if head is not None and ws(raw_text(head)).upper().startswith("NOTES"):
                in_notes = True
                continue
            if in_notes:
                notes.extend(_blocks(block))
            elif _is_credit(block):
                credit = ws(raw_text(block, br=" "))
            else:
                lines.extend(_blocks(block))
                refs.extend(_refs(block))
        draft_notes = ([f"Source credit: {credit}"] if credit else []) + (
            ["Notes: " + " ".join(notes)] if notes else []
        )
        drafts.append(
            SectionDraft(
                kind="section",
                number=number,
                heading=heading,
                lines=lines,
                notes=draft_notes,
                refs=list({r.href: r for r in refs}.values()),
                context=f"{chapter_context}; {subhead}" if subhead else chapter_context,
            )
        )
    provisions = build_provisions(drafts, JURISDICTION)
    name = f"Chapter {chapter} RCW" + (f" - {caption}" if caption else "")
    return [
        state_document(
            bundle,
            jurisdiction=JURISDICTION,
            module=MODULE,
            work_uri=work_uri(chapter),
            work_title=name,
            title=f"Revised Code of Washington, {name} (Title {rcw_title} RCW)",
            short_title=f"RCW ch. {chapter}",
            number=chapter,
            ext={"rcw_title": rcw_title, "rcw_chapter": chapter},
            provisions=provisions,
            edition="rcw-online",
        )
    ]


__all__ = ["PARSER_ID", "PARSER_VERSION", "parse_wa_rcw", "resolve_uri", "work_uri"]
