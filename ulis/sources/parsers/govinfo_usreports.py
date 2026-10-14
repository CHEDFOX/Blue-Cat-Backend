"""Parser for United States Reports volumes from GovInfo (collection USREPORTS), Supreme Court of the United States.

Input (one raw document per volume, made by ``connectors/govinfo_usreports.py``): a JSON bundle holding the volume's
package MODS (``https://www.govinfo.gov/metadata/pkg/USREPORTS-<volume>/mods.xml``) and the volume's text rendition
(``.../content/pkg/USREPORTS-<volume>/text/USREPORTS-<volume>.txt``), both exactly as received. Volumes whose package
links no text rendition (the later, born-digital ones) come with the volume PDF (base64) instead of the text; its text
layer is read with ``pdftotext`` and goes through the same steps.

* The MODS lists every case of the volume (``granuleClass`` CASE) with the title, the decision date, the United
  States Reports citation (``usCitation``, e.g. ``410 U.S. 113``) and the number of its first page
  (``reportNumber``). All of these are taken from the MODS; nothing is guessed.
* The text rendition is the OCR text of the whole printed volume, in reading order, without page breaks. A case is cut
  out of it between the running heads of its first page and of the first page of the next case. Pages are recognised
  from the running heads (page number lines such as ``OCTOBER TERM, 1972`` / ``410 U. S.``, ``Cite as: ...``): a
  chain of page numbers 1, 2, 3, ... that increases through the text. A case is ingested only if the running heads of
  its first page and of the next case's first page are in that chain, a caption (``CERTIORARI TO ...``, ``APPEAL
  FROM ...``) follows its first page head and the caption names the case; otherwise it is left out (the layouts of the
  older volumes differ and are not recognised). ``volume_cases_listed`` / ``volume_cases_located`` in each judgment's
  ``ext`` say how many of the volume's cases were found.
* The units ("paragraphs") are the printed pages. ``num`` is the page number as printed in the United States Reports
  (a range ``153-154`` when a page's running head is not in the text); ``part`` is the label of the running head
  (``Syllabus``, ``Opinion of the Court``, ``Douglas, J., dissenting``, ...), ``text`` the page's lines in source order
  (footnotes stay where the OCR puts them, at the end of the page) without the recognised running head lines. The OCR
  text keeps its errors (for example the missing "fi" and "fl" ligatures: "defnition", "affrmed"); nothing is corrected.

Judicial treatment is not in the source and is never inferred.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import (
    ParsedDocument,
    ParsedJudgmentMeta,
    ParsedParagraph,
    ParseSkipped,
)
from ulis.sources.parsers.pdf_text import PdfTextError, pdf_pages

PARSER_VERSION = "1.0.0"
BUNDLE_SCHEMA = "ulis-us-govinfo-usreports/1"
MODS = "{http://www.loc.gov/mods/v3}"
COURT_ID = "US-SC"

__all__ = [
    "BUNDLE_SCHEMA",
    "PARSER_VERSION",
    "UsReportsParseError",
    "bundle_bytes",
    "parse_govinfo_usreports",
    "work_uri_for",
]


class UsReportsParseError(ValueError):
    pass


def bundle_bytes(bundle: dict[str, Any]) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def work_uri_for(volume: int, page: int) -> str:
    return f"/us/case/us/{volume}/{page}"


# --------------------------------------------------------------------------- MODS


@dataclass(frozen=True)
class Case:
    access_id: str
    page: int
    title: str
    citation: str
    decided: date | None
    decided_raw: str
    term: int | None


def _text(el: Any, path: str) -> str | None:
    found = el.find(path)
    return found.text.strip() if found is not None and found.text and found.text.strip() else None


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _case_name(title: str, citation: str) -> str:
    """The MODS title is "<name>, <volume> U.S. [(<n> Dall.)] <page> (<year>)": the name precedes the citation."""
    volume = citation.split(" ", 1)[0]
    name = re.split(rf",\s*{re.escape(volume)}\s+U\.\s?S\.", title, maxsplit=1)[0]
    return name.strip() or title


def read_mods(content: bytes | str) -> tuple[int, date | None, list[Case]]:
    data = content.encode("utf-8") if isinstance(content, str) else content
    try:
        root = fromstring(data, forbid_dtd=True)
    except Exception as exc:  # defusedxml raises several exception types
        raise UsReportsParseError(f"the package MODS cannot be read safely: {exc}") from exc
    if root.tag != f"{MODS}mods":
        raise UsReportsParseError(f"unexpected root element {root.tag!r}")
    volume = None
    for ext in root.findall(f"{MODS}extension"):
        if ext.findtext(f"{MODS}docClass") == "USREPORTS":
            volume = ext.findtext(f"{MODS}volume")
    if volume is None or not volume.strip().isdigit():
        raise UsReportsParseError("the package MODS names no volume")
    changed = _date(_text(root, f"{MODS}recordInfo/{MODS}recordChangeDate"))
    cases: list[Case] = []
    for item in root.findall(f"{MODS}relatedItem[@type='constituent']"):
        ext = item.find(f"{MODS}extension")
        if ext is None or _text(ext, f"{MODS}granuleClass") != "CASE":
            continue
        page = _text(ext, f"{MODS}reportNumber")
        citation = _text(ext, f"{MODS}usCitation")
        title = _text(item, f"{MODS}titleInfo/{MODS}title")
        access_id = _text(ext, f"{MODS}accessId")
        term = _text(ext, f"{MODS}courtTerm")
        if not (page and page.isdigit() and citation and title and access_id):
            continue
        raw = _text(ext, f"{MODS}decisionDate") or ""
        cases.append(
            Case(access_id, int(page), title, citation, _date(raw), raw, int(term) if term and term.isdigit() else None)
        )
    return int(volume), changed, cases


# --------------------------------------------------------------------------- page structure of the OCR text

BARE = re.compile(r"^\s*\(?(\d{1,4})\)?\s*$")
HEAD_END = re.compile(r"^(?=.*[A-Za-z])[^\n]{0,80}?[\s\t\]](\d{1,4})\s*$")
HEAD_START = re.compile(r"^(\d{1,4})[\s\t](?=.*[A-Za-z])[^\n]{0,80}$")
TERM = re.compile(r"^[O0]CTOBER TERM,? ?\d{0,4}\.?$|^[O0]CTOBER TERM, \d{4}\.?\s+\d{0,4}$")
VOLUME_TAG = re.compile(r"^(?:\d{1,3}\s*)?(?:U|I[TI]?)\.?\s?[S8]\.?\s*$")
PUNCT_TAIL = re.compile(r"[.,;:?!]$")
CAPS_TITLE = re.compile(
    r"^[A-Z0-9 .,'’&\-–—\[\]()/]*\s(?:v\.|ET AL\.?)\s?[A-Za-z0-9 .,'’&\-–—\[\]()/]*$|^[A-Z0-9 .,'’&\-–—\[\]()/]{5,70}$"
)
LABEL = re.compile(
    r"^(?:\d{1,4}\.?[\s\t]+)?(?:U\.?\s?S\.?[\s\t]+)?"
    r"(?P<label>Syllabus|Opinion of the Court|Per Curiam|Appendix[A-Za-z ,.']*"
    r"|Opinion of [A-Z][A-Za-z.' -]+, (?:C\. ?)?J\."
    r"|[A-Z][A-Za-z.' -]+, (?:C\. ?)?J\.(?:,? (?:concurring|dissenting)[A-Za-z ,]*)?"
    r"|Argument for [A-Za-z ,.']+|Counsel for [A-Za-z ,.']+|Statement of the Case)\.?"
    r"(?:\s+\d{0,3}\s?U\.? ?S\.?)?\s*$"
)
CAPTION = re.compile(
    r"^(?:appeal from|certiorari to|error to|on (?:writ|petition|appeal|certificate|application)|petition for|"
    r"original|motion|application)\b",
    re.IGNORECASE,
)
BACKMATTER = re.compile(r"^(?:MISCELLANEOUS )?ORDERS?\b[A-Z ,0-9.-]*$")
# Typesetter's slugs that the volume PDFs of the later volumes carry on every page ("550US1", "Unit: $U28", ...).
SLUG = re.compile(
    r"^(?:\d+US\d+|Unit: \$+\w+|\[\d\d-\d\d-\d\d \d\d:\d\d:\d\d\] PAGES .*"
    r"|\d+[A-Za-z]{2}\d+\$\w+ \d\d-\d\d-\d\d \d\d:\d\d:\d\d PAGES \w+)$"
)
# The Government Printing Office job mark at the foot of the pages of the OCR text ("741700 0-47-11").
PRINT_MARK = re.compile(r"^\d{5,7}\s+\S{1,3}\s*[-–—]+\s*\d{2}\s*[-–—]+\s*\d{1,3}$")
MAX_SKIP = 12  # pages whose running head is missing between two recognised ones


def candidate_markers(lines: list[str], max_page: int) -> dict[int, list[int]]:
    """Lines that could carry a page number: a bare number, or a short running head with the number at an end."""
    found: dict[int, list[int]] = {}
    for index, line in enumerate(lines):
        for pattern in (BARE, HEAD_END, HEAD_START):
            m = pattern.match(line)
            if m and 1 <= int(m.group(1)) <= max_page:
                bucket = found.setdefault(int(m.group(1)), [])
                if index not in bucket:
                    bucket.append(index)
    return found


def page_chain(lines: list[str], max_page: int) -> dict[int, int]:
    """The longest chain of page-number lines with increasing line numbers: page -> line index."""
    candidates = candidate_markers(lines, max_page)
    nodes = sorted((index, page) for page, indexes in candidates.items() for index in indexes)
    best: dict[tuple[int, int], tuple[float, tuple[int, int] | None]] = {}
    by_page: dict[int, list[int]] = {}
    for index, page in nodes:
        score, prev = 1.0, None
        for step in range(1, MAX_SKIP + 1):
            for j in by_page.get(page - step, []):
                if j < index and index - j < 700 * step:
                    value = best[(j, page - step)][0] + 1 - 0.3 * (step - 1) - (index - j) / 1e6
                    if value > score:
                        score, prev = value, (j, page - step)
        best[(index, page)] = (score, prev)
        by_page.setdefault(page, []).append(index)
    if not best:
        return {}
    chain: dict[int, int] = {}
    key: tuple[int, int] | None = max(best, key=lambda k: best[k][0])
    while key is not None:
        chain[key[1]] = key[0]
        key = best[key][1]
    return chain


def _is_head_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if TERM.match(s) or VOLUME_TAG.match(s) or LABEL.match(s) or s in {"[Sup. Ct.", "[Sup. Ct.]"}:
        return True
    if s.startswith("Cite as:") or re.match(r"^Oct\. \d{4}\.\]", s):
        return True
    letters = [c for c in s if c.isalpha()]
    return (
        len(s) <= 70
        and len(letters) >= 5
        and (
            not PUNCT_TAIL.search(s.replace("v.", "v"))
            or (s.endswith(".") and (" v. " in s or not any(c.islower() for c in s)))
        )
        and sum(c.islower() for c in letters) <= 3 + 2 * s.count(" v.")
        and bool(CAPS_TITLE.match(s))
    )


def head_block(lines: list[str], marker: int, lower: int, upper: int) -> tuple[int, int, str | None]:
    """Start and end (exclusive) of the running head around a page-number line, and its label (``part``)."""
    start = marker
    while start - 1 >= lower and marker - (start - 1) <= 3 and _is_head_line(lines[start - 1]):
        start -= 1
    end = marker + 1
    while end < upper and end - marker <= 3 and _is_head_line(lines[end]) and not _starts_caption(lines, end):
        end += 1
    label = None
    for line in lines[start:end]:
        for piece in re.split(r"\t+", line.strip()):
            m = LABEL.match(piece.strip())
            if m and label is None:
                label = re.sub(r"(?<![A-Z])\.$", "", m.group("label").strip())
    return start, end, label


# --------------------------------------------------------------------------- slicing

# What follows the caption of a case: "CERTIORARI TO THE UNITED STATES COURT OF APPEALS ...", "ON REHEARING", or the
# line "No. 26. Argued October 20, 1955--Decided November 7, 1955." / "No. 11, Original."; at the start of the line.
CAPTION_TAIL = re.compile(
    r"^(?:nos?\.?\s*[\w\-–—,& ]{1,24}?\.\s*)?(?:appeals? from|certiorari to|error to|on (?:writ|petition|appeal"
    r"|certificate|application|motion|bill|rehearing|exceptions)|petition for|application for|motion for)\b"
    r"|^nos?\.?\s*[\w\-–—,& ]{1,24}?,?\s*(?:orig(?:inal|\.)|(?:argued|submitted|reargued|decided)\b)",
    re.IGNORECASE,
)
FRONT_BOILERPLATE = re.compile(r"^CASES ADJUDGED\s*$")
MAX_STRAY = 80  # lines of the previous case that may follow the running head of a page shared by two cases


STOPWORDS = {"the", "and", "inc", "corp", "ltd", "co", "company", "united", "states", "of", "for", "ex", "rel", "al"}


def _tokens(text: str) -> set[str]:
    return {t[:4] for t in re.findall(r"[a-z]{3,}", text.lower()) if t not in STOPWORDS}


def _names_the_case(name: str, lines: list[str]) -> bool:
    """The caption must mention a word of the case name the MODS gives (4-letter prefixes), when it has one."""
    wanted = _tokens(name)
    return not wanted or bool(wanted & _tokens(" ".join(lines)))


def _is_caption_line(line: str) -> bool:
    s = line.strip()
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 6 or len(s) > 160 or re.match(r"^\d", s) or re.search(r"[\s\t]\d{1,4}$", s):
        return False  # too short, or a running head (a page number at either end)
    if TERM.match(s) or VOLUME_TAG.match(s) or LABEL.match(s) or "OCTOBER TERM" in s.upper():
        return False
    # "ROE et al. v. WADE", "UNITED STATES v. MARA", "EX PARTE FRENCH": capitals apart from v. / et al.
    stripped = re.sub(r"\b(?:v|et al|ex rel|etc)\b\.?", "", s, flags=re.IGNORECASE)
    letters = [c for c in stripped if c.isalpha()]
    return len(letters) >= 5 and sum(c.isupper() for c in letters) / len(letters) >= 0.8


def _starts_caption(lines: list[str], j: int) -> bool:
    """Capitals, followed within five lines by "CERTIORARI TO ..." or similar: the caption of a case."""
    return _is_caption_line(lines[j]) and any(CAPTION_TAIL.match(x.strip()) for x in lines[j + 1 : j + 6])


def _find_caption(lines: list[str], begin: int, upper: int, name: str = "") -> int | None:
    for j in range(begin, min(begin + MAX_STRAY, upper)):
        if _starts_caption(lines, j) and _names_the_case(name, lines[j : j + 4]):
            # a caption can begin with lines that are not in capitals ("WINKELMAN, a minor, by and through ... v.")
            wanted = _tokens(name)
            while j - 1 >= begin and j - begin < MAX_STRAY and wanted & _tokens(lines[j - 1]) and j - 1 >= 0:
                j -= 1
            return j
    return None


@dataclass
class Located:
    case: Case
    caption: int  # line of the caption: the case's text starts here
    head_start: int  # start of the running head of the case's first page
    head_end: int
    label: str | None
    stray: bool  # lines of the previous case follow the running head of the first page (a page shared by two cases)
    end: int = 0  # exclusive; set once the next case is known
    last_page: int = 0
    last_shared: bool = False  # the last page is the first page of the next case: its running head is not this case's


def _locate(lines: list[str], cases: list[Case], chain: dict[int, int]) -> list[Located | None]:
    """One entry per case (None when it cannot be cut out with confidence)."""
    n = len(lines)
    found: list[Located | None] = []
    for i, case in enumerate(cases):
        if (i and case.page == cases[i - 1].page) or (i + 1 < len(cases) and case.page == cases[i + 1].page):
            found.append(None)  # two cases begin on one page: where one ends would be a guess
            continue
        marker = chain.get(case.page)
        if marker is not None:
            h_start, h_end, label = head_block(lines, marker, 0, n)
            caption = _find_caption(lines, h_end, n, _case_name(case.title, case.citation))
            if caption is not None:
                stray = any(lines[k].strip() for k in range(h_end, caption))
                found.append(Located(case, caption, h_start, h_end, label, stray))
                continue
        if i == 0:  # the first page of a volume has no running head: it follows "CASES ADJUDGED IN THE SUPREME COURT"
            boiler = next((k for k, ln in enumerate(lines) if FRONT_BOILERPLATE.match(ln.strip())), None)
            name = _case_name(case.title, case.citation)
            caption = _find_caption(lines, boiler, n, name) if boiler is not None else None
            if caption is not None:
                found.append(Located(case, caption, caption, caption, None, False))
                continue
        found.append(None)
    for i, loc in enumerate(found):
        if loc is None:
            continue
        nxt = found[i + 1] if i + 1 < len(found) else None
        if i + 1 < len(cases):
            if nxt is None or nxt.caption <= loc.caption:
                found[i] = None  # where this case ends is unknown
                continue
            loc.end = nxt.caption if nxt.stray else nxt.head_start
            loc.last_page = cases[i + 1].page if nxt.stray else cases[i + 1].page - 1
            loc.last_shared = nxt.stray
        else:
            end = next((j for j in range(loc.head_end, n) if BACKMATTER.match(lines[j].strip())), None)
            if end is None:
                found[i] = None
                continue
            loc.end = end
            later = [p for p, ln in chain.items() if loc.caption < ln < end]
            loc.last_page = max(later, default=loc.case.page)
        if loc.last_page < loc.case.page or loc.end <= loc.caption:
            found[i] = None
    return found


def _units(lines: list[str], loc: Located, chain: dict[int, int]) -> list[tuple[str, str, str]]:
    """(num, part, text) for each printed page of the case."""
    marks: list[tuple[int, int, int, str | None]] = [(loc.case.page, loc.caption, loc.caption, loc.label)]
    for page in range(loc.case.page + 1, loc.last_page + 1):
        line = chain.get(page)
        if line is not None and loc.caption < line < loc.end:
            h_start, h_end, label = head_block(lines, line, loc.caption + 1, loc.end)
            # the head of the next case's first page is not part of this case: its tail lines have no label
            marks.append((page, h_start, h_end, None if (loc.last_shared and page == loc.last_page) else label))
    units: list[tuple[str, str, str]] = []
    for k, (page, _h_start, h_end, label) in enumerate(marks):
        stop = marks[k + 1][1] if k + 1 < len(marks) else loc.end
        next_page = marks[k + 1][0] if k + 1 < len(marks) else loc.last_page + 1
        body_lines = [ln.rstrip() for ln in lines[h_end:stop]]
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        foot = BARE.match(body_lines[-1]) if body_lines else None
        if foot and int(foot.group(1)) == page:
            body_lines.pop()  # a first page without running head carries its page number at the foot
        if body_lines and PRINT_MARK.match(body_lines[-1].strip()):
            body_lines.pop()  # the printer's job mark is not part of the opinion
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        last = page if next_page - page <= 1 else next_page - 1
        units.append((str(page) if last == page else f"{page}-{last}", label or "text", body))
    return units


# --------------------------------------------------------------------------- entry point

PART_MAX = 32  # width of judgment_paragraphs.part


def _part(label: str) -> str:
    """The unit's ``part``: the running head's label, or - when it does not fit the column - its kind."""
    if len(label) <= PART_MAX:
        return label
    low = label.lower()
    if "dissent" in low and "concur" in low:
        return "concurring and dissenting"
    for key, kind in (("appendix", "appendix"), ("dissent", "dissenting"), ("concur", "concurring")):
        if key in low:
            return kind
    return "separate opinion" if low.startswith("opinion of") else "text"


def _volume_lines(bundle: dict[str, Any]) -> list[str]:
    """The lines of the volume: the text rendition as it is, or - for a volume that has none - the text layer of the
    volume's PDF (blank lines and typesetter's slugs left out; soft hyphens joined, ligature glyphs expanded)."""
    if "pdf_base64" not in bundle:
        return str(bundle["text"]).lstrip("\ufeff").splitlines()
    try:
        pdf = base64.b64decode(bundle["pdf_base64"], validate=True)
        pages = pdf_pages(pdf)
    except (binascii.Error, ValueError, PdfTextError) as exc:
        raise UsReportsParseError(f"the volume PDF cannot be read: {exc}") from exc
    return [ln.rstrip() for page in pages for ln in page.splitlines() if ln.strip() and not SLUG.match(ln.strip())]


def parse_govinfo_usreports(content: bytes) -> list[ParsedDocument]:
    try:
        bundle = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsReportsParseError(f"not a ULIS bundle: {exc}") from exc
    if bundle.get("schema") != BUNDLE_SCHEMA:
        raise UsReportsParseError(f"unexpected bundle schema {bundle.get('schema')!r}")
    volume, changed, cases = read_mods(bundle["mods"])
    if bundle.get("volume") != volume:
        raise UsReportsParseError(f"bundle volume {bundle.get('volume')!r} differs from the MODS volume {volume}")
    if not cases:
        raise ParseSkipped(f"volume {volume}: the package lists no case")
    cases.sort(key=lambda c: c.page)
    lines = _volume_lines(bundle)
    chain = page_chain(lines, cases[-1].page + 400)
    located = [loc for loc in _locate(lines, cases, chain) if loc is not None]
    if not located:
        raise ParseSkipped(
            f"volume {volume}: no case could be cut out of the OCR text (page headers not recognised); "
            f"{len(cases)} cases listed"
        )
    version_date = changed or date(1970, 1, 1)
    documents: list[ParsedDocument] = []
    for loc in located:
        case = loc.case
        units = _units(lines, loc, chain)
        if not units or case.decided is None:
            continue
        name = _case_name(case.title, case.citation)
        work_uri = work_uri_for(volume, case.page)
        ext: dict[str, Any] = {
            "reporter": "U.S.",
            "volume": volume,
            "first_page": case.page,
            "govinfo_package": f"USREPORTS-{volume}",
            "govinfo_granule": case.access_id,
            "volume_cases_listed": len(cases),
            "volume_cases_located": 0,
        }
        if case.term is not None:
            ext["court_term"] = case.term
        if case.decided.month == 1 and case.decided.day == 1:
            ext["decision_date_caveat"] = (
                "the source gives 1 January, a day the Court did not sit: probably a year-only date"
            )
        long_labels = {num: label for num, label, _ in units if len(label) > PART_MAX}
        if long_labels:
            ext["long_part_labels"] = long_labels  # the full labels of the pages whose part had to be shortened
        paragraphs = [
            ParsedParagraph(eid=f"p{num}", num=num, part=_part(label), text=text, sort_order=index, refs=[])
            for index, (num, label, text) in enumerate(units, start=1)
        ]
        documents.append(
            ParsedDocument(
                doc_kind="judgment",
                instrument_type="judgment",
                jurisdiction_id="US",
                work_uri=work_uri,
                expression_uri=f"{work_uri}/eng@{version_date.isoformat()}",
                work_title=name,
                title=case.title,
                short_title=None,
                number=case.citation,
                work_date=case.decided,
                language="en",
                authoritative=False,  # OCR text of the official printed volume
                text_origin="official_conversion",
                consolidation_kind="unknown",
                version_date=version_date,
                currency_date=None,
                extent=["US"],
                ext_module="us",
                ext=ext,
                judgment=ParsedJudgmentMeta(
                    court_id=COURT_ID,
                    case_name=name,
                    neutral_citation=case.citation,
                    decision_date=case.decided,
                    docket=None,
                ),
                paragraphs=paragraphs,
            )
        )
    if not documents:
        raise ParseSkipped(f"volume {volume}: no case with a decision date and text")
    for document in documents:
        document.ext["volume_cases_located"] = len(documents)
    return documents
