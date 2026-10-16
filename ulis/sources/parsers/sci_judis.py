"""Parser for Supreme Court of India judgments published as PDF on the Court's API host (JUDIS documents).

``https://api.sci.gov.in/jonew/judis/<id>.pdf`` serves the judgment (the id is the Court's own document number; the
body is the PDF preceded by four bytes the server adds, which are dropped before reading). Two layouts are seen
(read from ids 100 to 44900 on 2026-10-16):

* **JUDIS header** (judgments from 1950 to about 2008, "http://JUDIS.NIC.IN SUPREME COURT OF INDIA" on every page): a
  labelled block ``PETITIONER:`` / ``RESPONDENT:`` / ``DATE OF JUDGMENT:`` / ``BENCH:`` / ``CITATION:`` (reporter
  citations such as ``1961 AIR 331``, ``1961 SCR (2) 40``, ``1979 SCC (1) 161``) / ``CITATOR INFO:`` / ``ACT:`` (the
  catchwords) / ``HEADNOTE:`` and then ``JUDGMENT:`` with the text. The date is stated in the block.
* **Signed judgment** (about 2009 on): the judgment as signed - ``REPORTABLE``, ``IN THE SUPREME COURT OF INDIA``, the
  jurisdiction and case number, the parties on either side of "Versus", ``JUDGMENT`` / ``ORDER``, the author's name and
  the numbered paragraphs ("1.", or "1)"), ending with the judges' names and a place and date line ("NEW DELHI;
  MAY 11, 2011."). No header block: the date is read from that closing line (or from a "Date : dd/mm/yyyy" line of an
  order); a judgment whose date cannot be read is not stored (``ParseSkipped``), never given a guessed date.

What is kept and what is left out
* Text is the text layer of the PDF read with ``pdftotext -layout`` (no OCR); a PDF without a text layer is skipped.
  Line breaks inside a paragraph become spaces (a line ending in a hyphen is joined without one); nothing else is
  changed.
* Left out as page furniture: the running head ``http://JUDIS.NIC.IN SUPREME COURT OF INDIA Page n of m``, ``Page n``
  lines and lines holding only a number (the reporter's or the Court's page numbers). A year printed alone on a line
  is kept unless it continues the page-number sequence. The ``CITATOR INFO`` table (later cases that cite this one,
  added by the Court's editors) is not stored.
* Numbered paragraphs (``1.`` / ``1)`` in sequence from 1) become paragraphs with ``num``; everything before the first
  one, and whole judgments without numbered paragraphs (the older ones have none, and no paragraph breaks in the text
  layer), are cut into unnumbered ``text`` passages at sentence ends after about 1,500 characters. ``catchwords`` (ACT:)
  and ``headnote`` are separate parts.
* Case name = the petitioner and the respondent as printed (including the capitals), joined with " v. ".
* Work: ``/in/sc/<date>/<case slug>-<6 hex of the text>`` (the parser is not told the Court's document id); the text is
  the Court's website version, not the signed original, and carries no version date other than the judgment date.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any

from ulis.sources.normalized import ParsedDocument, ParsedJudgmentMeta, ParsedParagraph, ParseSkipped
from ulis.sources.parsers.pdf_text import PdfTextError, pdf_pages

PARSER_VERSION = "1.0.0"
CHUNK = 1500
MONTHS = {
    m: i + 1
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ]
    )
}
SPACE = re.compile(r"[ \t\r\xa0  ]+")
HEAD_LINE = re.compile(r"^https?://judis\.nic\.in\b", re.I)
PAGE_LINE = re.compile(r"^page\s+\d+(\s+of\s+\d+)?$", re.I)
BARE_NUMBER = re.compile(r"^\d{1,4}$")
LABEL = re.compile(
    r"^(CASE NO\.?|PETITIONER|RESPONDENT|BENCH|CITATION|CITATOR INFO|ACT|HEADNOTE|JUDGMENT)\s*:\s*(.*)$"
    r"|^(DATE OF JUDGMENT)\s*:?\s*(.*)$"
)
VERSUS = re.compile(r"^(-?\s*versus\s*-?|vs\.?|v\.|v/s\.?|-?\s*v\s*-?)$", re.I)
ROLE = re.compile(
    r"[\s…·-]*\b(?:appellants?|respondents?|petitioners?|defendants?|plaintiffs?|applicants?|"
    r"contemnors?|revisionists?|opposite\s+part(?:y|ies))\s*(?:\(s\))?[\s.…-]*$",
    re.I,
)
DOTS = re.compile(r"[.…·]{2,}")
NUMBERED = re.compile(r"^(\d{1,3})\s*[.)]\s+(\S.*)$|^(\d{1,3})\s*[.)]$")
JURISDICTION = re.compile(
    r"\b(?:APPELLATE|ORIGINAL|ADVISORY|WRIT|INHERENT|CIVIL|CRIMINAL|REVIEW|CONTEMPT)\b.*\bJURISDICTION\b", re.I
)
CASE_NO = re.compile(
    r"\b((?:Civil|Criminal|Special Leave|Writ|Transfer|Review|Contempt|Original|Miscellaneous|Election|"
    r"Arbitration|Curative|Death Reference|Tax Reference|Reference)[A-Za-z ()\[\].-]{0,45}?"
    r"(?:Appeals?|Petitions?|Suits?|Cases?|Applications?|Reference)(?:\s*\([A-Za-z]+\))?\s+"
    r"N[Oo]s?\.?\s*[\w()/\-,&. ]{1,60}?\s+(?:of|OF)\s+\d{4})",
    re.I,
)
DATELINE = re.compile(
    r"(?i)\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})\b"
)
DATELINE_DMY = re.compile(
    r"(?i)\b(\d{1,2})(?:st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|"
    r"november|december)\s*,?\s*(\d{4})\b"
)
DATE_COLON = re.compile(r"(?i)\bdate\s*:\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")
SIGNED_NAME = re.compile(r"(?:^|\s)[(\[]\s*([A-Z][A-Za-z.' -]{3,60}?)\s*[)\]]$")
AUTHOR = re.compile(r"^([A-Z][A-Za-z.' -]{2,60}?),?\s*((?:C\.?J\.?I?|J|JJ)\.?)\s*[:.]?$")
CITATION = re.compile(r"\b(\d{4})\s+(AIR|SCR|SCC|SCALE|JT)\s+(?:\((\d+)\)\s+)?(\d+)\b")
SENTENCE_END = re.compile(r"[.?!][\"'”’)]*$")


class SciJudisParseError(ValueError):
    pass


def _pdf_payload(content: bytes) -> bytes:
    start = content.find(b"%PDF-", 0, 64)
    if start < 0:
        raise SciJudisParseError("the document is not a PDF file")
    return content[start:]


def _clean_lines(pages: list[str]) -> list[str]:
    """Non-empty lines of all pages, whitespace collapsed, page furniture removed."""
    out: list[str] = []
    last_page_number = 0
    for page in pages:
        for raw in page.splitlines():
            line = SPACE.sub(" ", raw).strip()
            if not line or HEAD_LINE.match(line) or PAGE_LINE.match(line):
                continue
            if BARE_NUMBER.match(line):
                value = int(line)
                year_like = 1800 <= value <= 2100
                if not year_like or value == last_page_number + 1:
                    last_page_number = value
                    continue
            out.append(line)
    return out


def _join(lines: list[str]) -> str:
    """Wrapped lines of one passage as a single line of text (a hyphen at a line end joins without a space)."""
    text = ""
    for line in lines:
        if not text:
            text = line
        elif re.search(r"[A-Za-z]-$", text) and line[:1].islower():
            text += line
        else:
            text += " " + line
    return text


def _chunks(lines: list[str]) -> list[str]:
    """Unnumbered text cut at a sentence end once a passage reaches about CHUNK characters (at a ";" or ":" from 2,400,
    at any line end from 4,000, for text with no sentence end in sight)."""
    passages: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        current.append(line)
        size += len(line) + 1
        if (
            (size >= CHUNK and SENTENCE_END.search(line))
            or (size >= CHUNK * 1.6 and line.endswith((";", ":")))
            or size >= CHUNK * 2.7
        ):
            passages.append(_join(current))
            current, size = [], 0
    if current:
        passages.append(_join(current))
    return [p for p in passages if p]


def _date(day: int, month: int, year: int, what: str) -> date:
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise SciJudisParseError(f"{what}: impossible date {day:02d}/{month:02d}/{year}") from exc


def _party(lines: list[str]) -> str | None:
    """A party as printed: the role words ("Appellant(s)") and dot leaders are dropped, the name is not touched."""
    cleaned: list[str] = []
    for line in lines:
        if VERSUS.match(line):
            continue
        line = ROLE.sub("", DOTS.sub(" ", line))
        line = SPACE.sub(" ", line).strip(" ,;:-\u2026")
        line = re.sub(r"\s+\.$", "", line)
        if line:
            cleaned.append(line)
    return _join(cleaned) or None


def _split_bench(lines: list[str]) -> list[str]:
    names: list[str] = []
    for line in lines:
        parts = [line]
        if " & " in line:
            parts = line.split(" & ")
        elif ", " in line and re.search(r"[A-Z]\.", line.split(", ")[0]):
            parts = line.split(", ")  # "B.P. JEEVAN REDDY, K.S. PARIPOORNAN"; "SHAH, J.C." stays whole
        for part in parts:
            part = part.strip(" ,;")
            if part and part not in names:
                names.append(part)
    return names


def _citations(lines: list[str]) -> list[str]:
    found: list[str] = []
    for m in CITATION.finditer(" ".join(lines)):
        year, series, vol, page = m.groups()
        text = f"{year} {series}" + (f" ({vol})" if vol else "") + f" {page}"
        if text not in found:
            found.append(text)
    return found


def _first_case_number(lines: list[str]) -> list[str]:
    found: list[str] = []
    for m in CASE_NO.finditer(_join(lines)):
        number = re.sub(r"^.*\bJURISDICTION\b[:\s]*", "", SPACE.sub(" ", m.group(1)), flags=re.I).strip()
        if number and number not in found:
            found.append(number)
    return found


def _closing_date(lines: list[str]) -> date | None:
    """The date at the end of a signed judgment ("NEW DELHI; MAY 11, 2011.") or of an order ("Date : 23/09/2014").

    Only a short line among the last 15 counts, and only next to a place ("Delhi") or after "Dated:" / "Date:": a date
    quoted in the last paragraphs of the reasons is not the date of the judgment."""
    tail = lines[-15:]
    for i in range(len(tail) - 1, -1, -1):
        line = tail[i]
        if len(line) > 45:
            continue
        near = " ".join(tail[max(0, i - 1) : i + 1])
        if not (re.search(r"(?i)\bdelhi\b", near) or re.match(r"(?i)^dated?\s*:", line)):
            continue
        found = DATELINE.search(line)
        if found:
            return _date(int(found.group(2)), MONTHS[found.group(1).lower()], int(found.group(3)), "closing date")
        found_dmy = DATELINE_DMY.search(line)
        if found_dmy:
            day, month, year = int(found_dmy.group(1)), MONTHS[found_dmy.group(2).lower()], int(found_dmy.group(3))
            return _date(day, month, year, "closing date")
    return None


def _record_date(lines: list[str]) -> date | None:
    """The "Date : 23/09/2014" line of a Record of Proceedings (the day the order was made)."""
    for line in lines:
        colon = DATE_COLON.search(line)
        if colon:
            return _date(int(colon.group(1)), int(colon.group(2)), int(colon.group(3)), "record date")
    return None


def _is_next(value: int, text: str, expected: int, started: bool) -> bool:
    """Whether a line "<value>. <text>" is the next numbered paragraph.

    The sequence normally starts at 1 and goes up by one. A judgment whose "1." is lost in the text layer starts at 2 or
    3, and a missing number is skipped (one or two), but only for a line that reads like a sentence start."""
    if value == expected and (started or value == 1):
        return True
    sentence = bool(text) and (text[0].isupper() or text[0] in "\"'(\u201c\u2018")
    if not started:
        return value in (2, 3) and sentence
    return expected < value <= expected + 2 and sentence


def _body_paragraphs(body: list[str]) -> list[tuple[str, str | None, str]]:
    """(part, num, text) for a judgment body: numbered paragraphs, and unnumbered passages before / without them."""
    out: list[tuple[str, str | None, str]] = []
    preface: list[str] = []
    current: list[str] = []
    num: str | None = None
    expected = 1
    for line in body:
        found = NUMBERED.match(line)
        if found:
            value = int(found.group(1) or found.group(3))
            if _is_next(value, found.group(2) or "", expected, num is not None):
                if num is not None:
                    out.append(("considerations", num, _join(current)))
                elif preface:
                    out.extend(("text", None, p) for p in _chunks(preface))
                    preface = []
                num, current = str(value), [found.group(2)] if found.group(2) else []
                expected = value + 1
                continue
        (current if num is not None else preface).append(line)
    if num is not None:
        out.append(("considerations", num, _join(current)))
    else:
        out.extend(("text", None, p) for p in _chunks(preface))
    return [p for p in out if p[2]]


def _split_record(lines: list[str]) -> tuple[list[str], list[str]]:
    """A signed judgment's PDF may end with the Registry's "RECORD OF PROCEEDINGS" page (counsel, the order). It is
    kept apart from the judgment: (judgment lines, record lines)."""
    for i, line in enumerate(lines):
        if re.match(r"(?i)^record of proceedings$", line):
            cut = i
            for back in range(max(0, i - 4), i):
                if re.match(r"(?i)^item no\.?\s*\d", lines[back]):
                    cut = back
                    break
            return lines[:cut], lines[cut:]
    return lines, []


def _section(part: str, lines: list[str]) -> list[tuple[str, str | None, str]]:
    return [(part, None, text) for text in _chunks(lines)]


def _is_title_furniture(line: str) -> bool:
    """A line of the cause title that is not part of a party's name: case numbers, jurisdiction, "WITH"."""
    return bool(
        CASE_NO.search(line)
        or JURISDICTION.search(line)
        or re.search(r"^\(?\s*(?:arising out of\b|@)|^\[\s*@|\bSLP\b|^w\s*i\s*t\s*h$", line, re.I)
    )


def parse_sci_judis(content: bytes) -> list[ParsedDocument]:
    try:
        pages = pdf_pages(_pdf_payload(content), layout=True)
    except PdfTextError as exc:
        raise SciJudisParseError(str(exc)) from exc
    lines = _clean_lines(pages)
    if sum(len(ln) for ln in lines) < 200:
        raise ParseSkipped("the PDF has no text layer (a scan); ULIS does no OCR")
    head = lines[:30]
    ext: dict[str, Any] = {}
    sections: list[tuple[str, str | None, str]] = []
    record: list[str] = []
    if any(re.match(r"^(PETITIONER|CASE NO\.?)\s*:", ln) for ln in head[:12]):
        layout = "judis_header"
        labelled: dict[str, list[str]] = {}
        label: str | None = None
        body_start = None
        for i, line in enumerate(lines):
            m = LABEL.match(line)
            if m:
                label = (m.group(1) or m.group(3)).rstrip(".").upper()
                rest = m.group(2) if m.group(1) else m.group(4)
                if label == "JUDGMENT":
                    body_start = i + 1
                    if rest:
                        lines[i] = rest
                        body_start = i
                    break
                labelled.setdefault(label, [])
                if rest:
                    labelled[label].append(rest)
            elif label is not None:
                labelled[label].append(line)
        if body_start is None:
            raise SciJudisParseError("no JUDGMENT: label after the header block")
        stated = " ".join(labelled.get("DATE OF JUDGMENT", []))
        dm = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})\b", stated)
        if not dm:
            raise SciJudisParseError(f"no judgment date in the header ({stated!r})")
        decision_date = _date(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)), "DATE OF JUDGMENT")
        petitioner = _party(labelled.get("PETITIONER", []))
        respondent = _party(labelled.get("RESPONDENT", []))
        body = lines[body_start:]
        ext["bench"] = _split_bench(labelled.get("BENCH", []))
        ext["reporter_citations"] = _citations(labelled.get("CITATION", []))
        case_numbers = _first_case_number(labelled.get("CASE NO", []) + body[:12])
        sections += _section("catchwords", labelled.get("ACT", []))
        sections += _section("headnote", labelled.get("HEADNOTE", []))
    elif any("IN THE SUPREME COURT OF INDIA" in ln.upper() for ln in head[:15]):
        layout = "signed_judgment"
        start = next(i for i, ln in enumerate(lines[:15]) if "IN THE SUPREME COURT OF INDIA" in ln.upper())
        title_end = next(
            (
                i
                for i, ln in enumerate(lines[start:60], start)
                if re.match(r"^(J\s*U\s*D\s*G\s*M\s*E\s*N\s*T|O\s*R\s*D\s*E\s*R)\b", ln)
            ),
            None,
        )
        if title_end is None:
            raise SciJudisParseError("no JUDGMENT / ORDER line after the cause title")
        title = lines[start + 1 : title_end]
        flags = [ln.strip().upper().replace("-", " ") for ln in lines[: start + 1]]
        if any(f.startswith("NON REPORTABLE") for f in flags):
            ext["reportable"] = False
        elif any(f.startswith("REPORTABL") for f in flags):
            ext["reportable"] = True
        case_numbers = _first_case_number(title)
        versus = next((i for i, ln in enumerate(title) if VERSUS.match(ln)), None)
        petitioner = respondent = None
        if versus is not None:
            stop = max((i for i, ln in enumerate(title[:versus]) if _is_title_furniture(ln)), default=-1)
            after: list[str] = []
            for i, ln in enumerate(title[versus + 1 :], versus + 1):
                if _is_title_furniture(ln):
                    break
                after.append(ln)
                if ROLE.search(ln):
                    following = title[i + 1 : i + 2]
                    if following and re.match(r"^(AND|&|ORS|OTHERS|ANR|ANOTHER)\b", following[0], re.I):
                        after.append(following[0])
                    break
            petitioner, respondent = _party(title[stop + 1 : versus]), _party(after)
        judgment_lines, record = _split_record(lines)
        found_date = _closing_date(judgment_lines) or _record_date(record or judgment_lines[-40:])
        if found_date is None:
            raise ParseSkipped(
                "no judgment date in the text (neither a closing place-and-date line nor a 'Date :' line)"
            )
        decision_date = found_date
        body = judgment_lines[title_end + 1 :]
        author = next((m.group(1) for ln in body[:3] if (m := AUTHOR.match(ln))), None)
        if author:
            ext["author"] = author
        tail = judgment_lines[-40:]
        ext["bench"] = [
            m.group(1)
            for i, ln in enumerate(tail)
            if (m := SIGNED_NAME.search(ln)) and i and re.search(r"\bJJ?\.?$", tail[i - 1])
        ]
    else:
        raise SciJudisParseError("neither a JUDIS header block nor a signed judgment's cause title")

    sections += _body_paragraphs(body)
    sections += _section("record", record)
    if not sections or not any(p[0] in ("considerations", "text") for p in sections):
        raise ParseSkipped("the PDF has no judgment text")
    if petitioner and respondent:
        case_name = f"{petitioner} v. {respondent}"
    elif case_numbers:
        case_name = case_numbers[0]
    else:
        raise SciJudisParseError("no parties and no case number could be read")
    docket = case_numbers[0] if case_numbers else None

    digest = hashlib.sha256(
        "\n".join([decision_date.isoformat(), case_name, docket or "", *[p[2] for p in sections[:3]]]).encode()
    ).hexdigest()[:6]
    slug = re.sub(r"[^a-z0-9]+", "-", case_name.lower()).strip("-")[:60].strip("-") or "judgment"
    work_uri = f"/in/sc/{decision_date.isoformat()}/{slug}-{digest}"

    prefix = {"catchwords": "cw", "headnote": "hn", "text": "t", "considerations": "para", "record": "rec"}
    paragraphs: list[ParsedParagraph] = []
    counters: dict[str, int] = {}
    for part, num, text in sections:
        counters[part] = counters.get(part, 0) + 1
        eid = f"para-{num}" if num else f"{prefix[part]}-{counters[part]}"
        paragraphs.append(ParsedParagraph(eid, num, part, text, len(paragraphs), []))
    if len({p.eid for p in paragraphs}) != len(paragraphs):
        raise SciJudisParseError("duplicate paragraph ids")

    ext.update({"layout": layout, "petitioner": petitioner, "respondent": respondent, "case_numbers": case_numbers})
    return [
        ParsedDocument(
            doc_kind="judgment",
            instrument_type="judgment",
            jurisdiction_id="IN",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/eng@{decision_date.isoformat()}",
            work_title=case_name,
            title=case_name,
            short_title=None,
            number=docket,
            work_date=decision_date,
            language="en",
            authoritative=False,  # the Court's website version of the PDF; the signed judgment is the original
            text_origin="official_conversion",
            consolidation_kind="unknown",
            version_date=decision_date,
            currency_date=None,
            extent=["IN"],
            ext_module="in",
            ext={k: v for k, v in ext.items() if v not in (None, [], "")},
            judgment=ParsedJudgmentMeta(
                court_id="IN-SC", case_name=case_name, neutral_citation=None, decision_date=decision_date, docket=docket
            ),
            paragraphs=paragraphs,
        )
    ]
