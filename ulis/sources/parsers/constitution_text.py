"""Parser for the text of a national constitution published as an HTML page or a text-layer PDF.

One parser for every publisher of a constitution (ULIS ``constitution_docs`` connector): the connector wraps each
download in a *bundle* (the original bytes, base64, with the manifest entry that describes them), and this module
turns it into one ``ParsedDocument`` of ``instrument_type = constitution``.

What is kept and what is not:

* The text is exactly the text of the page or of the PDF's text layer (``pdftotext``), line by line. Nothing is
  summarised, translated, reflowed, hyphen-repaired or corrected. Only furniture that is not part of the text is
  left out, and only when the manifest names it: lines matching ``drop_lines`` (running heads and feet,
  watermarks), a page's first or last line when it is just a number (``page_numbers``), and everything before the
  body (``start_regex``; title pages, contents lists, front matter - the first provision's note says how many lines).
  Lines matching ``note_lines`` (a database's footer, an amendment footnote) are moved to the provision's note.
* Structure is recognised from the document's own headings, as far as they allow: articles or sections
  (``Article 12``, ``Section 3``, ``Статья 4.``, ``第五十一条``, ``12.`` ...) with the chapter heading above them as
  context, numbered paragraphs ``(1)`` or ``1.``, and a preamble. Which numbered headings form the body is decided
  by the most text-bearing run of consecutive numbers (a table of contents has the same numbers with almost no
  text between them), so contents lists and cross-references are not mistaken for articles. A document whose
  headings cannot be recognised (fewer than ``MIN_UNITS`` consecutive units) is stored in *blocks* of paragraphs
  (``kind = block``) and says so in each block's note.
* An article whose heading was not recognised is not lost: its text stays in the preceding article, whose note
  names the missing numbers. Text after the last article (schedules, annexes, the closing formula) is kept in
  trailing blocks.
* Every constitution is attested only as of its edition: the publisher presents the text as current, and no
  provision carries its own dates (``validity_basis = attested_at_edition``). Adoption and amendment dates are
  recorded as lifecycle events only when the manifest gives them (taken from the document's own text).

Identifiers are language-neutral: the work is ``/<cc>/constitution``, an article ``art-12`` (``sec-12`` for
documents that number *sections*), a numbered paragraph ``art-12-par1``, a block ``blk-1``, the preamble
``preamble``.
"""

from __future__ import annotations

import base64
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParseSkipped
from ulis.sources.parsers.legal_html import (
    HtmlParseError,
    Node,
    Unit,
    build_provisions,
    key_of,
    parse_html,
    render,
)
from ulis.sources.parsers.pdf_text import PdfTextError, pdf_pages

PARSER_VERSION = "1"
BUNDLE_SCHEMA = "ulis-constitution-bundle/1"
MIN_UNITS = 5  # fewer consecutive numbered units than this: the structure is not recognised, store blocks
MAX_GAP = 4  # the largest jump in numbering tolerated inside the body chain (missing or unnumbered headings)
BLOCK_CHARS = 2000
CHAPTER_TRAIL = 3  # a chapter heading is recognised among the last this-many text lines before the next article
TEXT_ORIGINS = {
    "official_authentic",
    "official_consolidation",
    "official_revised",
    "official_conversion",
    "unofficial_compilation",
}
SECTION_WORDS = {"section", "sec.", "sec", "clause"}
# invisible direction marks that Arabic and Hebrew pages put around words and numbers; they are layout, not text
BIDI_MARKS = frozenset("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff")

__all__ = [
    "BUNDLE_SCHEMA",
    "PARSER_VERSION",
    "ConstitutionParseError",
    "bundle_bytes",
    "document_lines",
    "parse_constitution",
    "parse_number",
    "resolve_uri",
]


class ConstitutionParseError(ValueError):
    pass


def bundle_bytes(bundle: dict[str, Any]) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    return None  # ULIS citation targets use the generic ``<work>/~<eId>`` form


# --------------------------------------------------------------------------- numbers

CJK_DIGITS = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CJK_UNITS = {"十": 10, "百": 100, "千": 1000}
FIRST_WORDS = {"premier", "première", "premiere", "unique"}
_CJK = "〇零一二两兩三四五六七八九十百千"
# the number token of a heading: digits in any script, a CJK numeral, or a word for "first"; then an optional ordinal
# sign (12.º) or the French ordinal (1er, 1ère), then an optional suffix: letters (12A, 12bis, 12.º-A), a dash or dot
# branch (12-1, 12.1).
BASE = (
    r"(?:[0-9٠-٩۰-۹๐-๙០-៩]{1,4}|[" + _CJK + r"]{1,6}|"
    r"premier|première|premiere|unique)"
)
SUFFIX = (
    r"(?:\s?-\s?[A-Za-z](?![A-Za-z])|\s?[-.]\s?[0-9]{1,2}(?![0-9])|"
    r"\s?(?:bis|ter|quater|quinquies)\b|[A-Za-z](?![A-Za-z]))"
)
NUMBER = r"(?P<base>" + BASE + r")(?P<ord>\.?[º°ᵒª]|(?<=[0-9])(?:er|ère)(?![A-Za-zÀ-ÿ]))?(?P<suffix>" + SUFFIX + r")?"
ORDINAL_SIGNS = "º°ᵒª"


def cjk_to_int(text: str) -> int | None:
    if not text or any(ch not in CJK_DIGITS and ch not in CJK_UNITS for ch in text):
        return None
    if all(ch in CJK_DIGITS for ch in text):  # positional: 二〇二
        return int("".join(str(CJK_DIGITS[ch]) for ch in text))
    total, current = 0, 0
    for ch in text:
        if ch in CJK_DIGITS:
            current = CJK_DIGITS[ch]
        else:
            total += (current or 1) * CJK_UNITS[ch]
            current = 0
    return total + current


def ascii_digits(text: str) -> str:
    return "".join(str(unicodedata.decimal(ch, 0)) if ch.isdigit() else ch for ch in text)


def parse_number(token: str, suffix: str = "") -> tuple[int, str] | None:
    """``(base number, suffix key)`` of a heading's number: ``12`` -> (12, ""), ``12`` + ``A`` -> (12, "a"),
    ``12`` + ``-1`` -> (12, "_1"), ``premier`` -> (1, ""), ``五十一`` -> (51, "")."""
    token = token.strip().rstrip(ORDINAL_SIGNS + ".").strip()
    if token.lower() in FIRST_WORDS:
        return 1, ""
    token = ascii_digits(token)
    if token and all(ch in _CJK for ch in token):
        value = cjk_to_int(token)
        return (value, "") if value is not None else None
    if not token.isdigit():
        return None
    suffix = suffix.strip()
    key = ""
    if suffix:
        m = re.fullmatch(r"[-.]\s?(?P<branch>[0-9]{1,2})", suffix)
        key = f"_{int(m.group('branch'))}" if m else re.sub(r"[^A-Za-z]", "", suffix.translate(BENGALI_LETTERS)).lower()
    return int(token), key


# the letters Bengali texts add to an article number (2ক, 7খ) are the a, b, c ... of the Latin-script numbering
BENGALI_LETTERS = str.maketrans("কখগঘঙচছজ", "abcdefgh")

# --------------------------------------------------------------------------- heading patterns

DEFAULT_KEYWORDS = [
    "Article",
    "Art.",
    "Art",
    "Section",
    "Sec.",
    "Clause",
    "Artículo",
    "Artigo",
    "Articolo",
    "Статья",
    "Стаття",
    "Madde",
    "Maddə",
    "Pasal",
    "Điều",
    "มาตรา",
    "មាត្រា",
    "ມາດຕາ",
    "المادة",
    "المواد",
    "مادة",
    "اصل",
    "ماده",
]
_CHAPTER_WORDS = (
    r"CHAPTER|Chapter|PART|Part|TITLE|Title|TITRE|Titre|CHAPITRE|Chapitre|CAPÍTULO|Capítulo|CAPITULO|TÍTULO|Título|PARTE|Parte|"
    r"Глава|ГЛАВА|Раздел|РАЗДЕЛ|Розділ|РОЗДІЛ|BAB|Bab|BÖLÜM|Bölüm|KISIM|Kısım|BÖLÜMÜ|CHƯƠNG|Chương|หมวด|ภาค|ជំពូក|ບົດ|"
    r"الفصل|الباب|الجزء"
)
DEFAULT_CHAPTER = re.compile(
    r"^\s*(?:(?:" + _CHAPTER_WORDS + r")\s*(?:[IVXLCDM]{1,8}\b|[0-9]{1,3}\b|[A-ZÀ-ÖА-ЯІЇЄ]{3,}\b|"
    r"(?:ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)\b|[Pp]rimer\w*|[Ss]egund\w*|[Pp]remi\w+|[Dd]euxi\w+)|"
    r"(?:" + _CHAPTER_WORDS + r")\s*[:.]?\s*$|"
    r"第[" + _CJK + r"0-9]{1,6}[章编編节節])"
)
DEFAULT_PREAMBLE = re.compile(
    r"^\s*(?:PREAMBLE|Preamble|PRÉAMBULE|Préambule|PREAMBULE|Preambule|PREÂMBULO|Preâmbulo|PREÁMBULO|Preámbulo|"
    r"ПРЕАМБУЛА|Преамбула|Mukadimah|MUKADIMAH|Pembukaan|PEMBUKAAN|Başlangıç|BAŞLANGIÇ|Lời nói đầu|LỜI NÓI ĐẦU|"
    r"คำปรารภ|序言|前言|الديباجة|ديباجة|المقدمة|مقدمة)\s*$"
)
DEFAULT_TAIL = re.compile(
    r"^\s*(?:SCHEDULES?|Schedules?|ANNEXES?|Annexes?|ANEXOS?|Anexos?|APPENDIX|Appendix|FIRST SCHEDULE|"
    r"ПРИЛОЖЕНИЕ|Приложение|附件|附則|附录|附錄)\b"
)


@dataclass
class Candidate:
    line: int
    base: int
    suffix: str
    remainder: str
    keyword: str = ""
    weight: int = 0
    mark: str = ""  # footnote reference printed before the number ("5[2A. ...")

    @property
    def key(self) -> str:
        return f"{self.base}{self.suffix}"


def keyword_regex(keywords: list[str]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(k) for k in sorted(set(keywords), key=len, reverse=True))
    return re.compile(
        r"^\s*[«“„\"\-–—•]?\s*(?P<kw>" + alternatives + r")\s*[\s:#№]*(?:\(\s*)?" + NUMBER + r"\s*\)?"
        r"(?P<tail>(?:\s*[.:–—-]+\s*|\s+|(?=\()|$).*)$",
        re.IGNORECASE,
    )


def numword_regex(words: list[str]) -> re.Pattern[str]:
    """Headings that print the number first: ``1-модда.``, ``1-modda``, ``1 maddə``."""
    alternatives = "|".join(re.escape(k) for k in sorted(set(words), key=len, reverse=True))
    return re.compile(
        r"^\s*" + NUMBER + r"\s*[-–]?\s*(?P<kw>" + alternatives + r")\b\.?(?P<tail>(?:\s*[.:–—-]+\s*|\s+|$).*)$",
        re.IGNORECASE,
    )


CJK_ARTICLE = re.compile(r"^\s*第(?P<base>[" + _CJK + r"0-9]{1,6})(?P<unit>[条條])(?P<tail>.*)$")
KOREAN_ARTICLE = re.compile(r"^\s*제\s?(?P<base>[0-9]{1,4})\s?조(?:의\s?(?P<branch>[0-9]{1,2}))?(?P<tail>.*)$")
NUMDOT = re.compile(
    r"^\s*(?:(?P<mark>[0-9]{1,3})\s?\[\s*)?(?P<base>\d{1,3})"
    r"(?P<suffix>[A-Za-z\u0995-\u099c]{1,2}(?![A-Za-z\u0995-\u099c])|-[0-9]{1,2})?"
    r"(?:\.(?P<t1>(?:\s.*)?)|\s?[\u0964\u09f7](?P<t2>.*))$"
)
TOC_LEADER = re.compile(r"(\.{4,}|…{2,}|\s\.\s\.\s\.)\s*[0-9ivxlc]*\s*$")


def find_candidates(lines: list[str], style: str, keywords: list[str], words_after: list[str]) -> list[Candidate]:
    out: list[Candidate] = []
    pattern = keyword_regex(keywords) if style == "keyword" else None
    after = numword_regex(words_after) if style == "numword" and words_after else None
    for i, line in enumerate(lines):
        if TOC_LEADER.search(line):  # a contents entry (dot leaders, page number), never a heading
            continue
        parsed: tuple[int, str] | None
        keyword = ""
        mark = ""
        if style == "numdot":
            m = NUMDOT.match(line)
            if not m:
                continue
            parsed = parse_number(m.group("base"), m.group("suffix") or "")
            remainder = (m.group("t1") or m.group("t2") or "").strip()
            mark = m.group("mark") or ""
        elif style == "numword":
            if after is None:
                continue
            m = after.match(line)
            if not m:
                continue
            parsed = parse_number(m.group("base"), m.group("suffix") or "")
            remainder = m.group("tail").strip(" \t.:–—-")
            keyword = m.group("kw")
        else:
            assert pattern is not None
            cjk = CJK_ARTICLE.match(line)
            ko = KOREAN_ARTICLE.match(line)
            m = pattern.match(line)
            if cjk:
                parsed = parse_number(cjk.group("base"))
                remainder = cjk.group("tail").strip()
            elif ko:
                parsed = (int(ko.group("base")), f"_{int(ko.group('branch'))}" if ko.group("branch") else "")
                remainder = ko.group("tail").strip()
            elif m:
                parsed = parse_number(m.group("base"), m.group("suffix") or "")
                remainder = m.group("tail").strip(" \t.:–—-")
                if remainder.isdigit():  # a footnote mark printed after the number
                    remainder = ""
                keyword = m.group("kw")
            else:
                continue
        if parsed is None or parsed[0] == 0:
            continue
        out.append(Candidate(i, parsed[0], parsed[1], remainder, keyword, mark=mark))
    return out


def body_chain(cands: list[Candidate], lines: list[str], max_gap: int = MAX_GAP, max_first: int = 3) -> list[Candidate]:
    """The most text-bearing run of consecutive numbers among the candidates (dynamic programming)."""
    if not cands:
        return []
    for k, c in enumerate(cands):
        end = cands[k + 1].line if k + 1 < len(cands) else len(lines)
        c.weight = sum(len(x) for x in lines[c.line + 1 : end]) + 1
    best = [0] * len(cands)
    prev = [-1] * len(cands)
    for i, c in enumerate(cands):
        best[i] = c.weight if c.base <= max_first else 0
        for j in range(i):
            d = c.base - cands[j].base
            follows = (1 <= d <= max_gap) or (d == 0 and c.suffix and c.suffix != cands[j].suffix)
            if best[j] and follows and best[j] + c.weight > best[i]:
                best[i] = best[j] + c.weight
                prev[i] = j
    top = max(range(len(cands)), key=lambda i: best[i])
    if not best[top]:
        return []
    chain = []
    k = top
    while k != -1:
        chain.append(cands[k])
        k = prev[k]
    chain.reverse()
    return chain


# --------------------------------------------------------------------------- text extraction

PAGE_NUMBER = re.compile(r"^\s*(?:[-–—]\s*)?[0-9]{1,4}(?:\s*[-–—])?\s*$")


def _html_lines(body: bytes, hints: dict[str, Any], encoding: str) -> list[str]:
    # markup repairs named by the manifest, applied before the page is parsed (e.g. a space the page draws with CSS
    # between a label and its title); they never touch the words of the text
    pairs = hints.get("html_replace") or []
    if pairs:
        page = body.decode(encoding, errors="replace")
        for pattern, replacement in pairs:
            page = re.sub(str(pattern), str(replacement), page)
        body = page.encode(encoding)
    try:
        root = parse_html(body, encoding)
    except HtmlParseError as exc:
        raise ConstitutionParseError(str(exc)) from exc
    sel = hints.get("html_select") or {}
    node: Node | None = root
    if sel:

        def match(n: Node) -> bool:
            return not (
                ("id" in sel and n.attrs.get("id") != sel["id"])
                or ("class" in sel and sel["class"] not in n.classes)
                or ("tag" in sel and n.tag != sel["tag"])
            )

        node = root if match(root) else root.find(match)
        if node is None:
            raise ConstitutionParseError(f"html_select {sel!r} matches nothing in the page")
    skip_classes = set(hints.get("html_skip_classes") or [])
    skip_tags = set(hints.get("html_skip_tags") or [])
    skip_ids = set(hints.get("html_skip_ids") or [])

    def skip(n: Node) -> bool:
        return n.tag in skip_tags or bool(n.classes & skip_classes) or n.attrs.get("id", "\0") in skip_ids

    assert node is not None
    return render(node, skip=skip, superscript=False).text.split("\n")


def _pdf_lines(body: bytes, hints: dict[str, Any]) -> list[str]:
    try:
        pages = pdf_pages(
            body,
            layout=bool(hints.get("layout")),
            soft_hyphen=str(hints.get("soft_hyphen", "drop")),
            columns=int(hints.get("columns", 1)),
            column_top=float(hints.get("columns_top", 0)),
            column_bottom=float(hints.get("columns_bottom", 0)),
        )
    except PdfTextError as exc:
        raise ConstitutionParseError(str(exc)) from exc
    first = int(hints.get("first_page", 1))
    last = hints.get("last_page")
    lines: list[str] = []
    for number, page in enumerate(pages, start=1):
        if number < first or (last is not None and number > int(last)):
            continue
        page_lines = page.split("\n")
        if hints.get("page_numbers"):
            nonblank = [k for k, x in enumerate(page_lines) if x.strip()]
            for k in (nonblank[-1:] + nonblank[:1]) if nonblank else []:
                if PAGE_NUMBER.match(page_lines[k]):
                    page_lines[k] = ""
        lines.extend(page_lines)
        lines.append("")
    return lines


def document_lines(bundle: dict[str, Any]) -> list[str]:
    """The lines of the document in reading order, furniture removed, blank lines kept as paragraph breaks."""
    document = bundle["document"]
    hints = document.get("structure") or {}
    body = base64.b64decode(bundle["body_b64"])
    media = bundle["media"]
    if media == "pdf":
        raw = _pdf_lines(body, hints)
    elif media == "html":
        encoding = hints.get("encoding", "utf-8")
        raw = []
        # a constitution published as one page per chapter: the pages in the manifest's order, each optionally
        # preceded by the heading the manifest names (the publisher's words for what the page is)
        for part in bundle.get("parts") or [{"body_b64": bundle["body_b64"]}]:
            if part.get("heading"):
                raw += [str(part["heading"]), ""]
            raw += _html_lines(base64.b64decode(part["body_b64"]), hints, encoding)
            raw.append("")
    else:
        raise ConstitutionParseError(f"unknown media {media!r}")
    drop = [re.compile(p) for p in hints.get("drop_lines") or []]
    layout = media == "pdf" and bool(hints.get("layout"))
    lines = []
    for line in raw:
        # control characters (a PDF font's bullet comes out as BEL) are not text; no-break spaces become spaces
        line = "".join(
            ch
            for ch in line.replace("\u00a0", " ")
            if (ch == "\t" or unicodedata.category(ch) != "Cc") and ch not in BIDI_MARKS
        ).strip()
        # canonically equivalent spellings (Vietnamese letters typed as base + accent) are stored in one form
        line = unicodedata.normalize("NFC", line)
        if layout:  # the column layout of the page is spacing, not text
            line = re.sub(r"\s{2,}", " ", line)
        if line and any(p.search(line) for p in drop):
            continue
        lines.append(line)
    return lines


# --------------------------------------------------------------------------- structure

PAREN_PARAGRAPH = re.compile(r"^\(\s*(?P<num>[0-9]{1,2}[a-z]?)\s*\)\s*(?P<text>.*)$")
DOT_PARAGRAPH = re.compile(r"^(?P<num>[0-9]{1,2})\.\s+(?P<text>\S.*)$")
SENTENCE_END = (".", ";", ":", ",", "؛", "。")


def _heading_text(remainder: str, mode: str) -> tuple[str | None, str]:
    """(heading, leftover text) of the rest of a heading line."""
    remainder = remainder.strip()
    if not remainder:
        return None, ""
    if mode == "text":
        return None, remainder
    short = len(remainder) <= 150 and not remainder.startswith("(")
    if mode == "same_line" or (mode == "auto" and short and not remainder.endswith(SENTENCE_END)):
        return remainder, ""
    return None, remainder


def _paragraph_units(content: list[str], style: str) -> list[str | Unit]:
    """Split an article's lines at ``(1)``, ``(2)`` ... (or ``1.``, ``2.``) when they form a sequence from 1."""
    if style == "none":
        return list(content)
    pattern = DOT_PARAGRAPH if style == "dot" else PAREN_PARAGRAPH
    marks: list[tuple[int, str, str]] = []
    expect = 1
    for i, line in enumerate(content):
        m = pattern.match(line)
        if not m:
            continue
        num = m.group("num")
        value = int(re.sub(r"[a-z]", "", num))
        if (value == expect and num.isdigit()) or (marks and value == expect - 1 and not num.isdigit()):
            marks.append((i, num, m.group("text")))
            if num.isdigit():
                expect += 1
    if sum(1 for _, n, _ in marks if n.isdigit()) < 2:
        return list(content)
    out: list[str | Unit] = list(content[: marks[0][0]])
    for k, (i, num, first) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(content)
        label = f"({num})" if style != "dot" else f"{num}."
        unit = Unit("paragraph", "par", key_of(num), label=label)
        if first:
            unit.content.append(first)
        unit.content.extend(content[i + 1 : end])
        out.append(unit)
    return out


def _nonblank(lines: list[str]) -> list[str]:
    return [x for x in lines if x]


def _trim(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start]:
        start += 1
    while end > start and not lines[end - 1]:
        end -= 1
    return lines[start:end]


def _blocks(lines: list[str], note: str, start: int = 1) -> list[Unit]:
    """Blocks of about ``BLOCK_CHARS`` characters: whole paragraphs (runs of lines between blanks) where possible."""
    paragraphs: list[list[str]] = [[]]
    for line in lines:
        if line:
            paragraphs[-1].append(line)
        elif paragraphs[-1]:
            paragraphs.append([])
    paragraphs = [p for p in paragraphs if p]
    units: list[Unit] = []
    current: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            unit = Unit("block", "blk", str(start + len(units)), notes=[note])
            unit.content.extend(current)
            units.append(unit)
        current, size = [], 0

    for paragraph in paragraphs:
        for line in paragraph:
            if size and size + len(line) > BLOCK_CHARS and (size >= BLOCK_CHARS // 2 or len(paragraph) == 1):
                flush()
            current.append(line)
            size += len(line)
        if size >= BLOCK_CHARS:
            flush()
    flush()
    return units


def _pop_heading(region: list[str], line_mode: bool = False) -> tuple[list[str], str | None]:
    """For texts that print an article's heading *above* its number: the short last paragraph of the lines before
    the number (at most two lines, 100 characters, not a sentence) is that heading and leaves the previous article."""
    if line_mode:  # lines are not separated by blank lines (HTML): the last line, when short and not a sentence
        positions = [i for i, x in enumerate(region) if x]
        if not positions:
            return region, None
        text = region[positions[-1]]
        if len(text) > 100 or text.endswith(SENTENCE_END) or text.startswith("("):
            return region, None
        return region[: positions[-1]], text
    end = len(region)
    while end and not region[end - 1]:
        end -= 1
    start = end
    while start and region[start - 1]:
        start -= 1
    run = region[start:end]
    if not run or len(run) > 2 or sum(len(x) for x in run) > 100 or run[-1].endswith(SENTENCE_END):
        return region, None
    if start and region[start - 1] == "" or start == 0:
        return region[:start], " ".join(run)
    return region, None


def _is_tail(line: str, tail_re: re.Pattern[str], strict: bool) -> bool:
    """The start of the text after the last article. A pattern of the manifest decides alone; the built-in pattern
    (schedules, annexes) only counts for a short line that is not a sentence."""
    if not tail_re.match(line):
        return False
    return not strict or (len(line.split()) <= 8 and not line.endswith((".", ";", ",")))


WRAPPED_TAIL = re.compile(r"^[a-z][^()]{1,70}\.$")
WRAPPED_HEADING = re.compile(r"^[A-Z][^()]{2,90}[^.;:,\u2014\u2013]$")


def _split_region(
    region: list[str],
    chapter_re: re.Pattern[str],
    title_lines: int,
    before: bool,
    heading_re: re.Pattern[str] | None = None,
    line_mode: bool = False,
    wrap: bool = False,
) -> tuple[list[str], str | None, str | None]:
    """Split the lines between two articles into (rest, chapter heading, heading of the next article).

    The chapter heading is the block of the last few non-blank lines that starts with a chapter/part keyword; the lines
    before it stay with the article above. For texts that print an article's heading above its number (``before``),
    the last line of that block (beyond the chapter's own title lines) - or, without a chapter heading, the short last
    paragraph - is the heading of the article that follows. A ``heading_re`` (Turkish "II. Cumhuriyetin nitelikleri")
    names the heading line itself: the last line before the number, when it matches."""
    positions = [i for i, x in enumerate(region) if x]
    if (
        before
        and heading_re is not None
        and wrap
        and len(positions) > 1
        and not heading_re.match(region[positions[-1]])
    ):
        # the second line of a wrapped heading may start in lower case ("... anti-corruption" / "commission.")
        tail, first = region[positions[-1]], region[positions[-2]]
        if (
            WRAPPED_TAIL.match(tail)
            and WRAPPED_HEADING.match(first)
            and not first.isupper()
            and not chapter_re.match(first)
        ):
            rest, chapter, _ = _split_region(region[: positions[-2]], chapter_re, title_lines, False)
            return rest, chapter, f"{first} {tail}"
    if before and heading_re is not None and positions and heading_re.match(region[positions[-1]]):
        start, heading_text = positions[-1], region[positions[-1]]
        # a heading wrapped over two lines: the line above has the shape of a heading's first line (no end mark)
        if (
            wrap
            and len(positions) > 1
            and WRAPPED_HEADING.match(region[positions[-2]])
            and not region[positions[-2]].isupper()
            and not chapter_re.match(region[positions[-2]])
        ):
            start, heading_text = positions[-2], f"{region[positions[-2]]} {heading_text}"
        rest, chapter, _ = _split_region(region[:start], chapter_re, title_lines, False)
        return rest, chapter, heading_text
    window = positions[-(CHAPTER_TRAIL + title_lines + (1 if before else 0)) :]
    for i in window:
        if chapter_re.match(region[i]):
            block = [x for x in region[i:] if x]
            if before and len(block) > 1 + title_lines:
                return region[:i], " ".join(block[:-1]), block[-1]
            return region[:i], " ".join(block), None
    if before:
        rest, heading = _pop_heading(region, line_mode)
        return rest, None, heading
    return region, None, None


def _structure(lines: list[str], document: dict[str, Any], media: str) -> tuple[list[tuple[Unit, str | None]], bool]:
    """(top-level units with their context heading, whether numbered units were recognised)."""
    hints = document.get("structure") or {}
    note_re = [re.compile(p) for p in hints.get("note_lines") or []]
    if hints.get("start_regex"):
        start_re = re.compile(hints["start_regex"])
        start = next((i for i, x in enumerate(lines) if start_re.search(x)), None)
        if start is None:
            raise ConstitutionParseError(f"start_regex {hints['start_regex']!r} matches no line")
        lines = lines[start:]
    style = hints.get("style", "auto")
    keywords = list(hints.get("keywords") or DEFAULT_KEYWORDS)
    chain: list[Candidate] = []
    chosen = "blocks"
    if style != "blocks":
        words_after = list(hints.get("keywords_after") or [])
        order = ["keyword", "numdot", *(["numword"] if words_after else [])] if style == "auto" else [style]
        for name in order:
            found = body_chain(
                find_candidates(lines, name, keywords, words_after),
                lines,
                int(hints.get("max_gap", MAX_GAP)),
                int(hints.get("max_first", 3)),
            )
            if len(found) >= MIN_UNITS and len(found) > len(chain):
                chain, chosen = found, name
    if not chain:
        blocks = _blocks(
            _trim(lines), "Structure not recognised: this block is a run of the document's paragraphs, in order."
        )
        if not blocks:
            raise ParseSkipped("the document has no text")
        return [(b, None) for b in blocks], False

    first_kw = next((c.keyword for c in chain if c.keyword), "")
    unit_kind = hints.get("unit_kind") or (
        "section" if chosen == "numdot" or first_kw.lower() in SECTION_WORDS else "article"
    )
    tag = "sec" if unit_kind == "section" else "art"
    word = "section" if unit_kind == "section" else "article"
    # in a PDF the lines are hard-wrapped: what follows the number on its line is the start of the text, not a heading
    heading_mode = hints.get("heading", "text" if media == "pdf" else "auto")
    heading_lines = int(hints.get("heading_lines", 0))
    heading_paren = hints.get("heading_next") == "paren"
    paragraph_style = hints.get("paragraphs", "paren")
    chapter_re = re.compile(hints["chapter_regex"]) if hints.get("chapter_regex") else DEFAULT_CHAPTER
    chapter_title_lines = int(hints.get("chapter_title_lines", 1))
    tail_re = re.compile(hints["tail_regex"]) if hints.get("tail_regex") else DEFAULT_TAIL
    preamble_re = re.compile(hints["preamble_regex"]) if hints.get("preamble_regex") else DEFAULT_PREAMBLE

    heading_re = re.compile(hints["heading_before_regex"]) if hints.get("heading_before_regex") else None
    heading_before = bool(hints.get("heading_before")) or heading_re is not None
    heading_line = hints.get("heading_before") == "line"
    heading_wrap = bool(hints.get("heading_wrap"))
    pending: str | None = None  # the heading printed above the next number (heading_before)
    first = chain[0].line
    tops: list[tuple[Unit, str | None]] = []
    stored_from = first  # the first line the body keeps (everything before it is front matter)
    context: str | None = None
    pre_region = lines[:first]
    if hints.get("preamble", True):
        markers = [i for i in range(first) if preamble_re.match(lines[i])]
        if markers:
            stored_from = markers[-1]
            region = lines[markers[-1] + 1 : first]
            region, context, found_heading = _split_region(
                region, chapter_re, chapter_title_lines, heading_before, heading_re, heading_line, heading_wrap
            )
            pending = found_heading
            body = [x for x in _nonblank(region) if not any(p.search(x) for p in note_re)]
            if body:
                unit = Unit("preamble", "", "preamble", heading=lines[markers[-1]])
                unit.content.extend(body)
                unit.notes.extend(x for x in _nonblank(region) if any(p.search(x) for p in note_re))
                tops.append((unit, None))
    if context is None:
        _, context, found_heading = _split_region(
            pre_region, chapter_re, chapter_title_lines, heading_before, heading_re, heading_line, heading_wrap
        )
        pending = found_heading
    front_lines = len([x for x in lines[:stored_from] if x])
    front_note = (
        f"The title page, contents and other front matter before the text ({front_lines} non-blank lines) "
        "are not stored."
        if front_lines
        else None
    )

    interlude_re = re.compile(hints["interlude_regex"]) if hints.get("interlude_regex") else None
    block_no = 1
    for n, cand in enumerate(chain):
        last = n + 1 == len(chain)
        end = chain[n + 1].line if not last else len(lines)
        region = lines[cand.line + 1 : end]
        tail_from: int | None = None
        next_context = context
        next_heading: str | None = None
        inter_blocks: list[Unit] = []
        if last:
            for k, line in enumerate(region):
                if _is_tail(line, tail_re, not hints.get("tail_regex")):
                    tail_from = cand.line + 1 + k
                    region = region[:k]
                    break
        else:
            cut = next((k for k, x in enumerate(region) if interlude_re is not None and interlude_re.match(x)), None)
            if cut is not None:  # unnumbered text between two numbered units (provisional provisions ...)
                region, between = region[:cut], region[cut:]
                between, found_chapter, next_heading = _split_region(
                    between, chapter_re, chapter_title_lines, heading_before, heading_re, heading_line, heading_wrap
                )
                inter_blocks = _blocks(
                    _trim(between),
                    f"Unnumbered text between the numbered {word}s (e.g. provisional provisions), "
                    "stored as blocks of paragraphs.",
                    block_no,
                )
                block_no += len(inter_blocks)
            else:
                region, found_chapter, next_heading = _split_region(
                    region, chapter_re, chapter_title_lines, heading_before, heading_re, heading_line, heading_wrap
                )
            if found_chapter:
                next_context = found_chapter
        heading, leftover = _heading_text(
            cand.remainder, heading_mode if "heading" in hints or chosen in ("keyword", "numword") else "text"
        )
        if heading_before and not heading:
            heading = pending
        content = _nonblank(region)
        parts: list[str] = []
        if not heading and heading_paren and content and content[0].startswith("("):
            while content and len(parts) < 3:
                parts.append(content.pop(0))
                if parts[-1].endswith(")"):
                    break
            heading = " ".join(parts).strip("() ") or None
        elif not heading and heading_lines:
            while content and len(parts) < heading_lines:
                parts.append(content.pop(0))
            heading = " ".join(parts) or None
        if leftover:
            content.insert(0, leftover)
        notes: list[str] = []
        if note_re:
            kept: list[str] = []
            for x in content:
                (notes if any(p.search(x) for p in note_re) else kept).append(x)
            content = kept
        if heading and not content:  # the "heading" is all there is of the article: it is the text
            content, heading = [heading], None
        unit = Unit(word, tag, key_of(cand.key), heading=heading)
        unit.notes.extend(notes)
        if cand.mark:
            unit.notes.append(
                f"The source prints the footnote reference {cand.mark} before the number of this {word}, "
                f"in the form '{cand.mark}[{cand.key}.'."
            )
        unit.content.extend(_paragraph_units(content, paragraph_style))
        if not last:
            following = chain[n + 1]
            missing = [x for x in range(cand.base + 1, following.base)]
            if missing:
                unit.notes.append(
                    f"The heading of {word} {', '.join(str(x) for x in missing)} was not recognised: "
                    "if it exists, its text is included in this one."
                )
        if n == 0 and front_note:
            unit.notes.append(front_note)
        tops.append((unit, context))
        tops.extend((b, None) for b in inter_blocks)
        context = next_context
        pending = next_heading
        if tail_from is not None:
            tail = _blocks(
                _trim(lines[tail_from:]),
                f"Text after the last numbered {word} (schedules, annexes, closing formula), "
                "stored as blocks of paragraphs.",
                block_no,
            )
            tops.extend((b, None) for b in tail)
    if not tops:
        raise ParseSkipped("no provisions found")
    return tops, True


# --------------------------------------------------------------------------- document


def _date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def parse_constitution(content: bytes) -> list[ParsedDocument]:
    try:
        bundle = json.loads(content)
    except ValueError as exc:
        raise ConstitutionParseError("not a constitution bundle (invalid JSON)") from exc
    if not isinstance(bundle, dict) or bundle.get("schema") != BUNDLE_SCHEMA:
        found = bundle.get("schema") if isinstance(bundle, dict) else type(bundle).__name__
        raise ConstitutionParseError(f"not a constitution bundle (schema {found!r})")
    document = bundle["document"]
    cc = str(document["jurisdiction"])
    lang = str(document["language"])
    retrieved = date.fromisoformat(str(bundle["retrieved_at"])[:10])
    edition = _date(document.get("edition_date"))
    version_date = edition or retrieved
    text_origin = document.get("text_origin", "official_consolidation")
    if text_origin not in TEXT_ORIGINS:
        raise ConstitutionParseError(f"unknown text_origin {text_origin!r}")

    lines = document_lines(bundle)
    tops, recognised = _structure(lines, document, str(bundle["media"]))
    if document.get("note") and tops:  # a statement of the manifest taken from the document (e.g. its heading line)
        tops[0][0].notes.append(str(document["note"]))
    provisions: list[ParsedProvision] = build_provisions(
        tops, extent=[cc], valid_from=None, valid_to=None, status="in_force"
    )
    if not any(p.text for p in provisions):
        raise ParseSkipped("the document has no text")

    work = f"/{cc.lower()}/constitution"
    events = [
        ParsedEvent(
            str(e["type"]), date.fromisoformat(str(e["date"])), None, e.get("description"), str(e.get("locator", ""))
        )
        for e in document.get("events") or []
    ]
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type="constitution",
            jurisdiction_id=cc,
            work_uri=work,
            expression_uri=f"{work}/{lang}@{version_date.isoformat()}",
            work_title=str(document["title"]),
            title=str(document["title"]),
            short_title=document.get("citation_name"),
            number=None,
            work_date=_date(document.get("adopted_on")),
            language=lang,
            authoritative=bool(document.get("authoritative", False)),
            text_origin=text_origin,
            consolidation_kind="constitution_text" if recognised else "constitution_blocks",
            version_date=version_date,
            currency_date=version_date,
            extent=[cc],
            ext_module=None,
            ext={},
            outstanding_changes=[],
            events=events,
            provisions=provisions,
            validity_basis="attested_at_edition",
        )
    ]
