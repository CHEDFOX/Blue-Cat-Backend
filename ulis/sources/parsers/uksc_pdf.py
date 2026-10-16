"""Parser for UK Supreme Court judgments that the Court publishes as PDF (the judgments of 2009 to 2020).

Input (one raw document per judgment, made by ``connectors/uksc_pdf.py``): a JSON bundle holding the Court's case page
(``https://supremecourt.uk/cases/uksc-<year>-<number>``, HTML) and the judgment PDF it links
(``https://supremecourt.uk/uploads/uksc_<year>_<number>_judgment_<hash>.pdf``), both exactly as received (the PDF
base64-encoded).

* Metadata comes from the case page (the same markup as the HTML judgments of ``uksc_html``): case id
  (``UKSC/2012/0066``),
  case title, judgment date, neutral citation (``[2012] UKSC 61``) and the justices; the court from which the appeal
  came
  ("On appeal from: [2012] CSIH 0009") is read from the first page of the PDF.
* The text is the PDF's text layer, extracted with ``pdftotext`` (poppler-utils): the page footers ("Page 17") are
  removed,
  lines are joined into paragraphs, and the Court's numbered paragraphs ("1." on a line of its own, followed by the
  text)
  are ``considerations`` units with their number. A number counts only if it continues the sequence (1, 2, 3 ...) or
  restarts at 1 and is followed by 2 (another opinion). Short lines that follow a finished sentence and precede a
  numbered
  paragraph are headings ("Conclusion"), and the opinion lines ("LORD REED: (with whom ...)") are headings. Everything
  before paragraph 1 (cover page, counsel) is kept as unnumbered ``text`` units.
* The PDF is not tagged: footnotes at the bottom of a page stay in the text at the place the PDF puts them (the end of
  the
  paragraph that ends the page), and a table or a quotation in a different layout may be joined into its paragraph.
  A PDF
  without a text layer (a scan) is skipped: ULIS does no OCR.

Judicial treatment is not in the source and is never inferred. The PDF is the Court's judgment as handed down; the
neutral citation and date of the page are those of the judgment.
"""

from __future__ import annotations

import base64
import binascii
import html as html_lib
import json
import re
from typing import Any

from ulis.sources.normalized import ParsedDocument, ParsedJudgmentMeta, ParsedParagraph, ParsedRef, ParseSkipped
from ulis.sources.parsers.pdf_text import PdfTextError, pdf_pages
from ulis.sources.parsers.uksc_html import SPACE, UkscParseError, _by_id, _date

PARSER_VERSION = "1.0.0"
BUNDLE_SCHEMA = "ulis.uksc-pdf-bundle.v1"
NEUTRAL = re.compile(r"^\[(\d{4})\] UKSC (\d{1,3})$")
OWN_COURT_CITATION = re.compile(r"\[(\d{4})\] UKSC (\d{1,3})(?!\d)")
CASE_ID = re.compile(r"^UKSC/(\d{4})/(\d{4})$")
FOOTER = re.compile(r"^Page \d+$")
NUM_ALONE = re.compile(r"^(\d{1,4})\.$")
NUM_INLINE = re.compile(r"^(\d{1,4})\.\s+(\S.*)$")
OPINION = re.compile(r"^(?:LORD|LADY|BARONESS|SIR|JUSTICE)\b[A-Z ,.'’-]*(?:\b|:|\()")
SENTENCE_END = (".", "?", "!", "”", '"', "’", ")", "]")
MIN_TEXT_CHARS = 400
PREFIX = {"heading": "h", "text": "t"}


class UkscPdfParseError(ValueError):
    pass


def bundle_bytes(bundle: dict[str, Any]) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _join(lines: list[str]) -> str:
    """Join the lines of a paragraph; a line that ends with a hyphen before a lower-case letter is joined without
    space."""
    out = ""
    for line in (ln for ln in lines if ln):
        if out.endswith("-") and line[:1].islower():
            out += line
        else:
            out = f"{out} {line}" if out else line
    return out


def _is_heading_line(line: str) -> bool:
    return (
        0 < len(line) <= 70
        and (line[0].isupper() or line[0].isdigit())
        and not line.endswith((",", ";", ":", ".", "?", "!", "”", '"', "’", ")", "]"))
    )


def _split_trailing_headings(buf: list[str]) -> tuple[list[str], list[list[str]]]:
    """(lines of the paragraph, heading blocks that follow it): short lines after a finished sentence, or an
    opinion line."""
    lines = list(buf)
    while lines and not lines[-1]:
        lines.pop()
    headings: list[list[str]] = []
    # An opinion line ("LORD REED: (with whom ...)") with its continuation lines, among the last few lines.
    for start in range(max(0, len(lines) - 4), len(lines)):
        if OPINION.match(lines[start]) and (
            start == 0 or not lines[start - 1] or lines[start - 1].endswith(SENTENCE_END)
        ):
            headings.insert(0, [ln for ln in lines[start:] if ln])
            lines = lines[:start]
            break
    # Short heading lines directly after a finished sentence.
    while lines:
        tail: list[str] = []
        j = len(lines)
        while j > 0 and lines[j - 1] and _is_heading_line(lines[j - 1]) and len(tail) < 2:
            tail.insert(0, lines[j - 1])
            j -= 1
        before = next((ln for ln in reversed(lines[:j]) if ln), "")
        if tail and (j == 0 or not lines[j - 1] or before.endswith(SENTENCE_END)) and j > 0:
            headings.insert(0, tail)
            lines = lines[:j]
            while lines and not lines[-1]:
                lines.pop()
            continue
        break
    return lines, headings


def _justices(html: str) -> list[str]:
    """The justices listed in the "Appeal" section of the case page (names in ``<p>`` elements or image alt texts)."""
    start = html.find('id="appeal"')
    if start < 0:
        return []
    names = re.findall(r'(?:<p\b[^>]*>|\balt=")((?:Lord|Lady|Baroness|Sir|Dame)\s[^<">]{2,60})(?:</p>|")', html[start:])
    return list(dict.fromkeys(html_lib.unescape(n).strip() for n in names))


def parse_uksc_pdf(content: bytes) -> list[ParsedDocument]:
    try:
        bundle = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UkscPdfParseError(f"not a ULIS bundle: {exc}") from exc
    if bundle.get("schema") != BUNDLE_SCHEMA:
        raise UkscPdfParseError(f"unexpected bundle schema {bundle.get('schema')!r}")
    try:
        html = bundle["case_page"]
        pdf = base64.b64decode(bundle["pdf_base64"], validate=True)
    except (KeyError, binascii.Error, TypeError) as exc:
        raise UkscPdfParseError(f"incomplete bundle: {exc}") from exc

    case_id = _by_id(html, "p", "case-id")
    m = CASE_ID.match(case_id or "")
    if not m:
        raise UkscPdfParseError(f"no case id (UKSC/yyyy/nnnn) on the case page: {case_id!r}")
    title = html_lib.unescape(_by_id(html, "h1", "case-title") or "")
    citation = _by_id(html, "p", "neutral-citation-number")
    nc = NEUTRAL.match(citation or "")
    if not title or not nc:
        raise UkscPdfParseError(f"{case_id}: no case title or no neutral citation ({citation!r})")
    try:
        decision_date = _date(_by_id(html, "p", "judgment-date"), "judgment date")
    except UkscParseError as exc:
        raise UkscPdfParseError(f"{case_id}: {exc}") from exc
    try:
        pages = pdf_pages(pdf)
    except PdfTextError as exc:
        raise UkscPdfParseError(f"{case_id}: the PDF cannot be read: {exc}") from exc
    lines: list[str] = []
    for page in pages:
        for raw in page.splitlines():
            line = SPACE.sub(" ", raw).strip()
            if not FOOTER.match(line):
                lines.append(line)
    if sum(len(ln) for ln in lines) < MIN_TEXT_CHARS:
        raise ParseSkipped(f"{case_id}: the PDF has no text layer (a scan); ULIS does no OCR")

    year, number = nc.group(1), nc.group(2)
    own = f"[{year}] UKSC {number}"
    appealed = re.search(r"On appeal from:\s*(.+)", "\n".join(pages[:2]))

    def number_at(i: int) -> tuple[int, str | None] | None:
        a, b = NUM_ALONE.match(lines[i]), NUM_INLINE.match(lines[i])
        if a:
            return int(a.group(1)), None
        if b:
            return int(b.group(1)), b.group(2)
        return None

    def restarts(i: int) -> bool:
        for j in range(i + 1, len(lines)):
            found = number_at(j)
            if found:
                return found[0] == 2
        return False

    units: list[tuple[str, str | None, str]] = []  # (kind, number, text)
    buf: list[str] = []
    current: str | None = None  # the number of the paragraph being collected, None before paragraph 1
    expected = 1

    def flush_text_block(block: list[str]) -> None:
        text = _join(block)
        if text:
            units.append(("text", None, text))

    def flush(last: bool = False) -> None:
        nonlocal buf
        body, headings = (buf, []) if last else _split_trailing_headings(buf)
        if current is None:
            # Front matter: blocks separated by blank lines; an opinion line is a heading.
            block: list[str] = []
            for ln in [*body, ""]:
                if ln:
                    block.append(ln)
                    continue
                if block:
                    (
                        units.append(("heading", None, _join(block)))
                        if OPINION.match(block[0])
                        else flush_text_block(block)
                    )
                block = []
        else:
            text = _join(body)
            if text:
                units.append(("considerations", str(current), text))
        for h in headings:
            units.append(("heading", None, _join(h)))
        buf = []

    for i, line in enumerate(lines):
        found = number_at(i)
        if found and (found[0] == expected or (found[0] == 1 and expected > 1 and restarts(i))):
            flush()
            current = found[0]
            expected = found[0] + 1
            buf = [found[1]] if found[1] else []
            continue
        buf.append(line)
    flush(last=True)
    if not any(u[0] == "considerations" for u in units):
        raise UkscPdfParseError(f"{case_id}: no numbered paragraphs found")

    paragraphs: list[ParsedParagraph] = []
    used: set[str] = set()
    for kind, num, text in units:
        base = f"para-{num}" if num else f"{PREFIX[kind]}-{len(paragraphs) + 1}"
        eid, n = base, 1
        while eid in used:
            n += 1
            eid = f"{base}-{n}"
        used.add(eid)
        refs: list[ParsedRef] = []
        seen: set[str] = set()
        for cited in OWN_COURT_CITATION.finditer(text):
            target = f"[{cited.group(1)}] UKSC {cited.group(2)}"
            href = f"/gb/uksc/{cited.group(1)}/{int(cited.group(2))}"
            if target != own and href not in seen:
                seen.add(href)
                refs.append(ParsedRef(href, target, "cites", extraction_method="pattern"))
        paragraphs.append(
            ParsedParagraph(
                eid, num, "considerations" if kind == "considerations" else kind, text, len(paragraphs), refs
            )
        )

    work_uri = f"/gb/uksc/{year}/{int(number)}"
    ext: dict[str, Any] = {
        "case_id": case_id,
        "judgment_appealed": appealed.group(1).strip() if appealed else None,
        "justices": _justices(html),
    }
    return [
        ParsedDocument(
            doc_kind="judgment",
            instrument_type="judgment",
            jurisdiction_id="GB",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/eng@{decision_date.isoformat()}",
            work_title=title,
            title=title,
            short_title=None,
            number=case_id,
            work_date=decision_date,
            language="en",
            authoritative=False,  # the Court's PDF as published on its website; the signed judgment is the original
            text_origin="official_conversion",  # text layer of the PDF
            consolidation_kind="unknown",
            version_date=decision_date,
            currency_date=None,
            extent=["GB"],
            ext_module="gb",
            ext={k: v for k, v in ext.items() if v not in (None, [], "")},
            judgment=ParsedJudgmentMeta(
                court_id="GB-UKSC",
                case_name=title,
                neutral_citation=own,
                decision_date=decision_date,
                docket=case_id,
            ),
            paragraphs=paragraphs,
        )
    ]
