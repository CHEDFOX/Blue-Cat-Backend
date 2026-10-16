"""Parser for judgments published by the Judiciary of Northern Ireland (https://www.judiciaryni.uk).

Input (one raw document per decision, made by ``connectors/ni_judiciary.py``): a JSON bundle holding the decision page
(``https://www.judiciaryni.uk/judicial-decisions/<slug>``, HTML) and the judgment PDF it links
(``https://www.judiciaryni.uk/files/judiciaryni/...pdf``), both exactly as received (the PDF base64-encoded).

* Metadata comes from the decision page: the title (``h1``), "Neutral Citation No. [2026] NICA 38", the date delivered
  (``<time datetime="2026-09-23...">``) and the judges (links to ``/about-judiciary/...``). The series of the neutral
  citation gives the court: NICA is the Court of Appeal in Northern Ireland, NIQB / NIKB / NICh / NIFam / NIMaster
  the High
  Court of Justice, NICC the Crown Court, anything else (county court, coroners, magistrates) "other courts of Northern
  Ireland".
* The text is the PDF's text layer, extracted with ``pdftotext``. The courts number their paragraphs "[1]" on a line of
  their own, followed by the text; the paragraph splitting is that of ``court_pdf_units`` (page footers removed, lines
  joined, headings recognised, quoted paragraph numbers not taken for the judgment's own). Footnotes stay where the
  PDF puts
  them. A PDF without a text layer is skipped (ULIS does no OCR); a decision without numbered paragraphs (sentencing
  remarks,
  rulings) is kept as unnumbered text.

Judicial treatment is not in the source and is never inferred. The PDF is the court's judgment as handed down.
"""

from __future__ import annotations

import base64
import binascii
import html as html_lib
import json
import re
from datetime import date
from typing import Any

from ulis.sources.normalized import ParsedDocument, ParsedJudgmentMeta, ParsedParagraph, ParsedRef, ParseSkipped
from ulis.sources.parsers.court_pdf_units import page_lines, units_from_lines
from ulis.sources.parsers.pdf_text import PdfTextError, pdf_pages

PARSER_VERSION = "1.0.0"
BUNDLE_SCHEMA = "ulis.ni-judiciary-bundle.v1"
NEUTRAL = re.compile(r"Neutral Citation No\.?\s*(\[(\d{4})\]\s+(NI[A-Za-z]+)\s+(\d{1,4}))")
OWN_SERIES = re.compile(r"\[(\d{4})\]\s+(UKSC|UKPC|UKHL|NICA|NIQB|NIKB|NICh|NIFam|NIMaster|NICC)\s+(\d{1,4})(?!\d)")
TIME = re.compile(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})')
H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
JUDGE = re.compile(r'<a[^>]*href="/about-judiciary/[^"]*"[^>]*>(.*?)</a>', re.S)
COURTS = {
    "NICA": "GB-NICA",
    "NIQB": "GB-NIHC",
    "NIKB": "GB-NIHC",
    "NICH": "GB-NIHC",
    "NIFAM": "GB-NIHC",
    "NIMASTER": "GB-NIHC",
    "NICC": "GB-NICC",
}
OTHER_COURTS = "GB-NIOTH"
MIN_TEXT_CHARS = 400
PREFIX = {"heading": "h", "text": "t"}


class NiJudiciaryParseError(ValueError):
    pass


def bundle_bytes(bundle: dict[str, Any]) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_ni_judiciary(content: bytes) -> list[ParsedDocument]:
    try:
        bundle = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NiJudiciaryParseError(f"not a ULIS bundle: {exc}") from exc
    if bundle.get("schema") != BUNDLE_SCHEMA:
        raise NiJudiciaryParseError(f"unexpected bundle schema {bundle.get('schema')!r}")
    try:
        html = bundle["decision_page"]
        page_url = str(bundle["decision_page_url"])
        pdf = base64.b64decode(bundle["pdf_base64"], validate=True)
    except (KeyError, binascii.Error, TypeError) as exc:
        raise NiJudiciaryParseError(f"incomplete bundle: {exc}") from exc

    slug = page_url.rstrip("/").rsplit("/", 1)[-1]
    found = NEUTRAL.search(_text(html))
    heading = H1.search(html)
    title = _text(heading.group(1)) if heading else ""
    if not found or not title:
        raise NiJudiciaryParseError(f"{slug}: no title or no neutral citation on the decision page")
    citation, year, series, number = found.group(1), found.group(2), found.group(3), int(found.group(4))
    day = TIME.search(html)
    if not day:
        raise NiJudiciaryParseError(f"{slug}: no date delivered on the decision page")
    decided = date.fromisoformat(day.group(1))
    judges = list(dict.fromkeys(_text(j) for j in JUDGE.findall(html) if _text(j)))
    try:
        pages = pdf_pages(pdf)
    except PdfTextError as exc:
        raise NiJudiciaryParseError(f"{slug}: the PDF cannot be read: {exc}") from exc
    lines = page_lines(pages)
    if sum(len(ln) for ln in lines) < MIN_TEXT_CHARS:
        raise ParseSkipped(f"{slug}: the PDF has no text layer (a scan); ULIS does no OCR")
    units = units_from_lines(lines)
    if not units:
        raise NiJudiciaryParseError(f"{slug}: no text found")

    own = f"[{year}] {series} {number}"
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
        for cited in OWN_SERIES.finditer(text):
            target = f"[{cited.group(1)}] {cited.group(2)} {cited.group(3)}"
            href = f"/gb/{cited.group(2).lower()}/{cited.group(1)}/{int(cited.group(3))}"
            if target != own and href not in seen:
                seen.add(href)
                refs.append(ParsedRef(href, target, "cites", extraction_method="pattern"))
        paragraphs.append(ParsedParagraph(eid, num, kind, text, len(paragraphs), refs))

    work_uri = f"/gb/{series.lower()}/{year}/{number}"
    court_id = COURTS.get(series.upper(), OTHER_COURTS)
    ext: dict[str, Any] = {"case_id": slug, "justices": judges}
    return [
        ParsedDocument(
            doc_kind="judgment",
            instrument_type="judgment",
            jurisdiction_id="GB-NIR",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/eng@{decided.isoformat()}",
            work_title=title,
            title=title,
            short_title=None,
            number=slug,
            work_date=decided,
            language="en",
            authoritative=False,  # the judiciary's PDF as published on its website; the signed judgment is the original
            text_origin="official_conversion",  # text layer of the PDF
            consolidation_kind="unknown",
            version_date=decided,
            currency_date=None,
            extent=["GB-NIR"],
            ext_module="gb",
            ext={k: v for k, v in ext.items() if v not in (None, [], "")},
            judgment=ParsedJudgmentMeta(
                court_id=court_id,
                case_name=title,
                neutral_citation=citation,
                decision_date=decided,
                docket=slug,
            ),
            paragraphs=paragraphs,
        )
    ]
