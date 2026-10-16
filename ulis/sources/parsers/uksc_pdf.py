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
from ulis.sources.parsers.court_pdf_units import page_lines, units_from_lines
from ulis.sources.parsers.pdf_text import PdfTextError, pdf_pages
from ulis.sources.parsers.uksc_html import COURTS, UkscParseError, _by_id, _date

PARSER_VERSION = "1.1.0"
BUNDLE_SCHEMA = "ulis.uksc-pdf-bundle.v1"
NEUTRAL = re.compile(r"^\[(\d{4})\] (UKSC|UKPC) (\d{1,3})$")
OWN_COURT_CITATION = re.compile(r"\[(\d{4})\] (UKSC|UKPC) (\d{1,3})(?!\d)")
CASE_ID = re.compile(r"^(UKSC|JCPC)/(\d{4})/(\d{4})$")
MIN_TEXT_CHARS = 400
PREFIX = {"heading": "h", "text": "t"}


class UkscPdfParseError(ValueError):
    pass


def bundle_bytes(bundle: dict[str, Any]) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
    court_id, series = COURTS[m.group(1)]
    title = html_lib.unescape(_by_id(html, "h1", "case-title") or "")
    citation = _by_id(html, "p", "neutral-citation-number")
    nc = NEUTRAL.match(citation or "")
    if not title or not nc or nc.group(2) != series:
        raise UkscPdfParseError(f"{case_id}: no case title or no neutral citation ({citation!r})")
    try:
        decision_date = _date(_by_id(html, "p", "judgment-date"), "judgment date")
    except UkscParseError as exc:
        raise UkscPdfParseError(f"{case_id}: {exc}") from exc
    try:
        pages = pdf_pages(pdf)
    except PdfTextError as exc:
        raise UkscPdfParseError(f"{case_id}: the PDF cannot be read: {exc}") from exc
    lines = page_lines(pages)
    if sum(len(ln) for ln in lines) < MIN_TEXT_CHARS:
        raise ParseSkipped(f"{case_id}: the PDF has no text layer (a scan); ULIS does no OCR")

    year, number = nc.group(1), nc.group(3)
    own = f"[{year}] {series} {number}"
    appealed = re.search(r"On appeals? from:\s*(.+)", "\n".join(pages[:2]))
    units = units_from_lines(lines)
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
            target = f"[{cited.group(1)}] {cited.group(2)} {cited.group(3)}"
            href = f"/gb/{cited.group(2).lower()}/{cited.group(1)}/{int(cited.group(3))}"
            if target != own and href not in seen:
                seen.add(href)
                refs.append(ParsedRef(href, target, "cites", extraction_method="pattern"))
        paragraphs.append(
            ParsedParagraph(
                eid, num, "considerations" if kind == "considerations" else kind, text, len(paragraphs), refs
            )
        )

    work_uri = f"/gb/{series.lower()}/{year}/{int(number)}"
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
                court_id=court_id,
                case_name=title,
                neutral_citation=own,
                decision_date=decision_date,
                docket=case_id,
            ),
            paragraphs=paragraphs,
        )
    ]
