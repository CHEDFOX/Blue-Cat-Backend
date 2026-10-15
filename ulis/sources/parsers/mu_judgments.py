"""Parser for decisions of the courts of Mauritius published on the Supreme Court website.

The connector (:mod:`ulis.sources.connectors.mu_supreme_court`) delivers one JSON *bundle* per decision:

* ``listing``: the row of the site's Judgment Search table, as printed (title, delivered by, document number,
  date delivered, summary, link, Drupal node id);
* ``pdf``: the URL, SHA-256 and size of the PDF the row links to;
* ``text``: the text layer of that PDF, as extracted by ``pdftotext`` (poppler) with no layout option.

How a decision is read:

* **text is exact source text.** The extractor's page separators (form feeds) are the only thing removed
  besides the page-number line the court prints at the foot of a page (``3``, ``Page 3``, ``Page 3 of 5``);
  wrapped lines are joined with one space (a line that ends in a hyphen is joined without one), blank-line
  separated blocks inside one paragraph with a line break. Nothing is added, corrected, summarised or translated;
* the **header** is everything before the heading line that names the kind of decision (``JUDGMENT``, ``RULING``,
  ``BAIL RULING``, ...): title, document number, court, parties. Without such a line there is no header part;
* **paragraphs** start at a line-start marker ``12.`` (the number may stand alone on its line) or ``[12]`` that
  fits the sequence (the next number, a number not above the highest seen plus one, or a repetition inside an
  open quotation is not a new paragraph). The printed number is kept as ``num`` (the court's own numbering
  has repeats and gaps; it is not corrected). Text without markers is split at blank lines, joined again when the
  block before does not end a sentence, and no paragraph exceeds about 4,000 characters (split only after a line
  that ends a sentence). The numbered "paragraphs" can therefore include numbered items of a list;
* the **court** comes from the listing's ``delivered by`` role (``... Magistrate Intermediate Court`` ->
  Intermediate Court, ``... President Industrial Court``, ``... District Magistrate``, ``... Judge``), else from
  the court named in the header lines of the text; a decision whose court cannot be told is an error, not a guess.
  The three-letter code in the document number (``2026 PL3 14``) names a court division, not a level, and is
  not used;
* the **neutral citation** is the document number printed in the listing (``2026 INT 287``); the work URI is
  built from it (``/mu/judgment/2026-int-287``), or from the node id when the number is missing or unusual;
* the **date** is the listing's ``Delivered On`` (dd/mm/yyyy); it is also the version date.

Not read: case numbers, judges beyond the ``delivered by`` text, cross-references (no links are made), headnotes
(the listing's summary column is kept in ``ext``), footnotes as such (they stay where the extractor puts them).
A PDF without a text layer (a scan) yields no text: the document is skipped (:class:`ParseSkipped`).
Language is a word-count heuristic (English or French) and says so in ``ext.language_basis``.

The site says its versions are "for convenience of reference only"; the official versions are the Mauritius
Reports and the Supreme Court Judgments of Mauritius, so nothing here is authoritative.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from ulis.sources.normalized import ParsedDocument, ParsedJudgmentMeta, ParsedParagraph, ParseSkipped

PARSER_VERSION = "1.0.0"
BUNDLE_SCHEMA = "ulis-mu-judgment-bundle/1"
MAX_PARAGRAPH = 4000

DOCUMENT_NUMBER = re.compile(r"^(\d{4}) ([A-Z][A-Z0-9]{1,5}) (\d{1,5})$")
MARKER = re.compile(r"^(?:(\d{1,3})\.|\[(\d{1,3})\])(?:\s+(.*))?$")
PAGE_NUMBER = re.compile(r"^(?:Page\s+)?\d{1,3}(?:\s*(?:of|/)\s*\d{1,3})?$", re.IGNORECASE)
HEADING = re.compile(
    r"^(?:(?:BAIL |FINAL |INTERIM |SUPPLEMENTARY |SENTENCING |SUMMARY )?"
    r"(?:JUDG(?:E)?MENT|RULING|ORDER|DECISION|SENTENCE)"
    r"(?:\s+ON\s+[A-Z ]{3,40})?)[\s.:–-]*$",
    re.IGNORECASE,
)
SENTENCE_END = tuple(".;:?!)”»\"'’")

ENGLISH_WORDS = frozenset(
    [
        "the",
        "of",
        "and",
        "to",
        "in",
        "that",
        "was",
        "is",
        "for",
        "with",
        "be",
        "as",
        "by",
        "on",
        "not",
        "his",
        "her",
        "it",
        "at",
        "this",
        "were",
        "has",
        "have",
    ]
)
FRENCH_WORDS = frozenset(
    [
        "le",
        "la",
        "les",
        "des",
        "du",
        "de",
        "et",
        "que",
        "qui",
        "dans",
        "est",
        "pour",
        "par",
        "sur",
        "au",
        "aux",
        "une",
        "un",
        "il",
        "elle",
        "ont",
        "été",
        "ne",
        "pas",
    ]
)


class MuParseError(ValueError):
    pass


def work_uri_for(document_number: str | None, node_id: str) -> str:
    m = DOCUMENT_NUMBER.match(document_number or "")
    if m:
        return f"/mu/judgment/{m.group(1)}-{m.group(2).lower()}-{m.group(3)}"
    return f"/mu/judgment/node-{node_id}"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def detect_language(text: str) -> str:
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœ]+", text.lower())
    english = sum(1 for w in words if w in ENGLISH_WORDS)
    french = sum(1 for w in words if w in FRENCH_WORDS)
    return "fr" if french > 1.5 * english and french >= 20 else "en"


def classify_court(delivered_by: str | None, header_lines: list[str]) -> tuple[str, str]:
    """(court id, basis): from the listing's ``delivered by`` role, else the court named in the header."""
    role = (delivered_by or "").lower()
    for needle, court in (
        ("industrial court", "MU-IND"),
        ("intermediate court", "MU-IC"),
        ("children", "MU-CC"),
        ("district magistrate", "MU-DC"),
        ("magistrate", "MU-DC"),
        ("chief justice", "MU-SC"),
        ("judge", "MU-SC"),
    ):
        if needle in role:
            return court, "listing_delivered_by"
    head = " ".join(header_lines[:12]).upper()
    for needle, court in (
        ("SUPREME COURT", "MU-SC"),
        ("INDUSTRIAL COURT", "MU-IND"),
        ("INTERMEDIATE COURT", "MU-IC"),
        ("CHILDREN", "MU-CC"),
        ("DISTRICT COURT", "MU-DC"),
        ("BAIL AND REMAND COURT", "MU-DC"),
    ):
        if needle in head:
            return court, "header_text"
    raise MuParseError(f"the court cannot be told from {delivered_by!r} or the header")


def _pages(text: str) -> list[list[str]]:
    """Lines per page without the page-number line, blank lines trimmed at both ends of a page."""
    pages: list[list[str]] = []
    for page in text.split("\f"):
        lines = [ln.rstrip() for ln in page.replace("\r", "").split("\n")]
        while lines and not lines[-1].strip():
            lines.pop()
        if lines and PAGE_NUMBER.match(lines[-1].strip()):
            lines.pop()
            while lines and not lines[-1].strip():
                lines.pop()
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines:
            pages.append(lines)
    return pages


def _join(lines: list[str]) -> str:
    out = ""
    for raw in lines:
        line = _norm(raw)
        if not line:
            continue
        out = line if not out else (out + line if out.endswith("-") else out + " " + line)
    return out


def _quote_open(text: str) -> bool:
    return text.count("“") > text.count("”") or text.count("«") > text.count("»")


SENTENCE_BREAK = re.compile(r"(?<=[.;:?!”»])[ \n]+(?=[A-Z“«\"(\[])")


def _split_long(text: str) -> list[str]:
    """Split text above MAX_PARAGRAPH into contiguous pieces, only where a sentence ends."""
    if len(text) <= MAX_PARAGRAPH:
        return [text]
    pieces: list[str] = []
    start = 0
    for m in SENTENCE_BREAK.finditer(text):
        if m.start() - start >= MAX_PARAGRAPH * 0.6:
            pieces.append(text[start : m.start()])
            start = m.end()
    pieces.append(text[start:])
    return pieces


def split_paragraphs(text: str) -> tuple[list[str], list[tuple[str | None, str]]]:
    """(header lines, [(printed number or None, text)]) of one decision's extracted text."""
    pages = _pages(text)
    if not pages:
        raise ParseSkipped("the PDF has no text layer")
    lines: list[str] = []
    for page in pages:  # a page break continues the paragraph: no blank line is inserted
        lines.extend(page)
    # Header: up to the line that names the kind of decision, within the first 40 lines.
    header: list[str] = []
    body_from = 0
    for i, line in enumerate(lines[:40]):
        if HEADING.match(line.strip()):
            header = [ln for ln in lines[: i + 1] if ln.strip()]
            body_from = i + 1
            break
    body = lines[body_from:]

    units: list[tuple[str | None, list[str]]] = []  # (printed number, lines)
    highest = 0
    current: list[str] | None = None
    current_num: str | None = None
    for line in body:
        stripped = line.strip()
        m = MARKER.match(stripped)
        take = False
        if m:
            n = int(m.group(1) or m.group(2))
            joined = _join(current) if current else ""
            plausible = (highest == 0 and n == 1) or (highest > 0 and 1 <= n <= highest + 2)
            if plausible and not (_quote_open(joined) and n != highest + 1):
                take = True
        if take and m:
            if current is not None:
                units.append((current_num, current))
            current_num = m.group(1) or m.group(2)
            highest = max(highest, int(current_num))
            current = [m.group(3)] if m.group(3) else []
        else:
            if current is None:
                current, current_num = [], None
            current.append(line)
    if current is not None:
        units.append((current_num, current))

    paragraphs: list[tuple[str | None, str]] = []
    for num, unit_lines in units:
        if num is not None:
            blocks = _blocks(unit_lines)
            paragraphs.extend((num if i == 0 else None, t) for i, t in enumerate(_split_long("\n".join(blocks))))
        else:
            # Unnumbered stretch: blocks separated by blank lines, merged when a block does not end a sentence.
            merged: list[str] = []
            for block in _blocks(unit_lines):
                if merged and not merged[-1].rstrip().endswith(SENTENCE_END):
                    merged[-1] = _join([merged[-1], block])
                else:
                    merged.append(block)
            for block in merged:
                paragraphs.extend((None, t) for t in _split_long(block))
    return header, [(n, t) for n, t in paragraphs if t]


def _blocks(lines: list[str]) -> list[str]:
    blocks: list[str] = []
    chunk: list[str] = []
    for line in lines:
        if line.strip():
            chunk.append(line)
        elif chunk:
            blocks.append(_join(chunk))
            chunk = []
    if chunk:
        blocks.append(_join(chunk))
    return [b for b in blocks if b]


def _delivered_on(text: str) -> date:
    try:
        return datetime.strptime(text.strip(), "%d/%m/%Y").date()
    except ValueError as exc:
        raise MuParseError(f"unreadable date delivered {text!r}") from exc


def parse_mu_judgment(content: bytes) -> list[ParsedDocument]:
    try:
        bundle = json.loads(content)
    except ValueError as exc:
        raise MuParseError("not a JSON bundle") from exc
    if not isinstance(bundle, dict) or bundle.get("schema") != BUNDLE_SCHEMA:
        raise MuParseError(f"unexpected bundle schema {bundle.get('schema') if isinstance(bundle, dict) else None!r}")
    listing: dict[str, Any] = bundle["listing"]
    pdf: dict[str, Any] = bundle["pdf"]
    node_id = str(listing["node_id"])
    title = _norm(listing["title"])
    if not title:
        raise MuParseError(f"node {node_id}: no title")
    document_number = _norm(listing.get("document_number") or "") or None
    decision_date = _delivered_on(listing["delivered_on"])
    delivered_by = _norm(listing.get("delivered_by") or "") or None

    header, units = split_paragraphs(bundle.get("text") or "")
    if not units:
        raise ParseSkipped(f"node {node_id}: the text layer is empty")
    court_id, court_basis = classify_court(
        delivered_by, header or [ln for ln in (bundle["text"] or "").split("\n")[:12]]
    )

    paragraphs: list[ParsedParagraph] = []
    used: set[str] = set()

    def add(base: str, num: str | None, part: str, text: str) -> None:
        eid, n = base, 1
        while eid in used:
            n += 1
            eid = f"{base}-{n}"
        used.add(eid)
        paragraphs.append(ParsedParagraph(eid, num, part, text, len(paragraphs), []))

    if header:
        add("hdr", None, "header", "\n".join(_norm(ln) for ln in header))
    for i, (num, text) in enumerate(units, start=1):
        add(f"para-{num}" if num else f"u-{i}", num, "text", text)

    body_text = " ".join(t for _, t in units)
    language = detect_language(body_text)
    work_uri = work_uri_for(document_number, node_id)
    neutral = document_number if DOCUMENT_NUMBER.match(document_number or "") else None
    ext = {
        "node_id": node_id,
        "document_number": document_number,
        "delivered_by": delivered_by,
        "court_basis": court_basis,
        "pdf_url": pdf["url"],
        "pdf_sha256": pdf["sha256"],
        "summary": _norm(listing.get("summary") or "") or None,
        "language_basis": "word_count_heuristic",
    }
    lang3 = "fra" if language == "fr" else "eng"
    return [
        ParsedDocument(
            doc_kind="judgment",
            instrument_type="judgment",
            jurisdiction_id="MU",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/{lang3}@{decision_date.isoformat()}",
            work_title=title,
            title=title,
            short_title=None,
            number=document_number,
            work_date=decision_date,
            language=language,
            authoritative=False,  # the site: "prepared for convenience of reference only"
            text_origin="official_conversion",
            consolidation_kind="unknown",
            version_date=decision_date,
            currency_date=None,
            extent=["MU"],
            ext_module="mu",
            ext=ext,
            judgment=ParsedJudgmentMeta(
                court_id=court_id,
                case_name=title,
                neutral_citation=neutral,
                decision_date=decision_date,
                docket=None,
            ),
            paragraphs=paragraphs,
        )
    ]
