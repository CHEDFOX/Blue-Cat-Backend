"""Parser for constitution texts that an official body publishes as a web page, a PDF with a text layer or plain text.

Used by the ``constitution_pages_b`` connector. The connector wraps every document in a *bundle* (JSON: the manifest's
facts about the document plus the file exactly as received, base64); this module reads the bundle and produces one
:class:`ParsedDocument` per constitution text.

What the parser does, and what it does not:

* the text is the document's own, character for character; only layout is undone (hard line wraps of PDF and text
  files are re-joined, running headers/footers and bare page numbers of PDFs are dropped, HTML becomes one
  paragraph per block element). Nothing is translated, summarised or corrected;
* articles are split by the document's own headings (``Article 12``, ``Artículo 12.-``, ``Статья 12.``, ``§ 12``,
  ``1 straipsnis``, ``1-modda`` ...); the label is the one printed in the document (ordinal marks such as ``º`` and
  ``er`` are not part of it);
* Titles, Chapters, Sections and Parts are kept as the *context heading* of the articles below them, not as provisions;
* editorial notes the publisher interleaves with the text (``Nota: Reformado el artículo ...``, reform annotations) are
  kept in the provision's ``note``, never in its text, when the manifest says how to recognise them (``note_regex``);
* a numbering restart inside a section headed "transitional" / "final" provisions is kept as a separate series whose
  labels start with ``T`` (``T1``); unnumbered text under such a heading becomes *blocks*;
* where fewer than ``min_articles`` articles are recognised (or the manifest says ``mode: blocks``) the whole text is
  divided into *blocks of paragraphs* in reading order. Blocks are ULIS's own division, labelled as such in the
  citation;
* dates: the document does not date its provisions, so versions are ``attested_at_edition`` (known in force on the
  edition date the publisher states, else the retrieval date).

Per-document ``parser_hints`` in the manifest (all optional): ``article_regex`` (named groups ``num`` and ``rest``),
``start_at`` / ``end_at`` (regular expressions on lines: text before the first / from the first match is not part of
the constitution), ``drop_lines``, ``note_regex``, ``preamble_start``, ``mode`` (``blocks``), ``min_articles``,
``edition_date`` / ``work_date`` (ISO dates the publisher states), ``edition_note`` or ``edition_note_regex``
(the first line of the document matching it is kept as the publisher's words about the edition), ``text_origin``,
``authoritative``, ``country_name`` / ``citation_label`` (citation), ``work_slug``, ``wrapped`` (the lines of an HTML
page are hard-wrapped) and ``ext_module`` (a profile that has its own module).
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Any

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParseSkipped
from ulis.sources.parsers.pdf_text import PdfTextError, pdf_pages

PARSER_VERSION = "1.0.0"
BUNDLE_SCHEMA = "ulis-constitution-pages-b-bundle/1"
MODULE_ID = "constitution_b"
NOTE_LIMIT = 4000
BLOCK_CHARS = 1800
HINT_KEYS = frozenset(
    {
        "article_regex",
        "start_at",
        "end_at",
        "drop_lines",
        "note_regex",
        "preamble_start",
        "mode",
        "min_articles",
        "edition_date",
        "work_date",
        "edition_note",
        "edition_note_regex",
        "text_origin",
        "authoritative",
        "country_name",
        "citation_label",
        "work_slug",
        "ext_module",
        "wrapped",
    }
)


class ConstitutionParseError(ValueError):
    """The bundle is not a constitution bundle, or its file cannot be read as the format the manifest declares."""


# --------------------------------------------------------------------------- bundle


def bundle_bytes(
    *,
    document_id: str,
    country: str,
    language: str,
    title: str,
    url: str,
    fmt: str,
    retrieved_on: str,
    content_type: str | None,
    data: bytes,
    status_note: str | None = None,
    hints: dict[str, Any] | None = None,
) -> bytes:
    bundle = {
        "schema": BUNDLE_SCHEMA,
        "document_id": document_id,
        "country": country,
        "language": language,
        "title": title,
        "url": url,
        "format": fmt,
        "retrieved_on": retrieved_on,
        "content_type": content_type,
        "status_note": status_note,
        "hints": hints or {},
        "data_b64": base64.b64encode(data).decode("ascii"),
    }
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def read_bundle(content: bytes) -> dict[str, Any]:
    try:
        bundle = json.loads(content)
    except ValueError as exc:
        raise ConstitutionParseError("not a constitution bundle (not JSON)") from exc
    if not isinstance(bundle, dict) or bundle.get("schema") != BUNDLE_SCHEMA:
        raise ConstitutionParseError("not a constitution bundle (unexpected schema)")
    for key in ("document_id", "country", "language", "title", "url", "format", "retrieved_on", "data_b64"):
        if not bundle.get(key):
            raise ConstitutionParseError(f"bundle lacks {key}")
    unknown = set(bundle.get("hints") or {}) - HINT_KEYS
    if unknown:
        raise ConstitutionParseError(f"unknown parser hints: {sorted(unknown)}")
    try:
        bundle["data"] = base64.b64decode(bundle["data_b64"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConstitutionParseError("bundle data is not base64") from exc
    return bundle


# --------------------------------------------------------------------------- text of the file

_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u200e\u200f\ufeff\u00ad"), None)
_SPACES = re.compile(r"[ \t\u00a0\u2000-\u200a\u202f\u205f\u3000]+")

BLOCK_TAGS = frozenset(
    {
        *("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "ul", "ol"),
        *("blockquote", "dd", "dt", "hr", "pre", "center", "main", "tbody", "thead", "tfoot", "form", "header"),
        *("footer", "aside", "nav", "address", "figure", "figcaption", "fieldset", "legend", "caption", "details"),
        "summary",
    }
)
SKIP_TAGS = frozenset(
    (
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "head",
        "select",
        "button",
        "textarea",
        "iframe",
        "object",
        "canvas",
    )
)


def clean_line(text: str) -> str:
    return _SPACES.sub(" ", text.translate(_INVISIBLE)).strip()


def decode_text(data: bytes, content_type: str | None) -> str:
    charsets: list[str] = []
    if content_type:
        m = re.search(r"charset=([\w-]+)", content_type, re.I)
        if m:
            charsets.append(m.group(1))
    meta = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", data[:4096], re.I)
    if meta:
        charsets.append(meta.group(1).decode("ascii"))
    charsets.append("utf-8")
    for charset in charsets:
        try:
            return data.decode(charset)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("cp1252", errors="replace")


SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
SUBSCRIPT_TO_DIGIT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._sup = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "sup":
            self._sup += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in BLOCK_TAGS and tag not in SKIP_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "sup":
            self._sup = max(0, self._sup - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            data = re.sub(r"\s+", " ", data)  # HTML collapses white space, line feeds included; only elements break lines
            self.parts.append(data.translate(SUPERSCRIPT) if self._sup else data)


@dataclass
class Source:
    lines: list[str]  # "" marks a blank line
    wrapped: bool  # True when lines are hard-wrapped (PDF, plain text): paragraphs must be re-joined
    pages: int = 0
    furniture_removed: int = 0


PAGE_NUMBER = re.compile(
    r"^(?:[-–—]\s*)?\d{1,4}(?:\s*[-–—])?$"
    r"|^(?:page|página|pagina|seite|strana|sida|sivu|стр\.?|страница|sayfa|oldal|puslapis|lpp\.?)\s*\d{1,4}"
    r"(?:\s*(?:/|of|de|von|din|iz|от)\s*\d{1,4})?$"
    r"|^\d{1,4}\s*(?:de|of|von|sur|/)\s*\d{1,4}$",
    re.I,
)


def html_source(data: bytes, content_type: str | None) -> Source:
    parser = _HtmlText()
    parser.feed(decode_text(data, content_type))
    parser.close()
    lines = [clean_line(ln) for ln in "".join(parser.parts).split("\n")]
    return Source([ln for ln in lines if ln], wrapped=False)


def text_source(data: bytes, content_type: str | None) -> Source:
    """Plain text; form feeds mark page breaks (as in ``pdftotext`` output), so running headers drop out as in a PDF."""
    text = decode_text(data, content_type)
    if "\f" in text:
        return pages_source(text.split("\f"))
    return Source([clean_line(ln) for ln in text.splitlines()], wrapped=True)


def pdf_source(data: bytes) -> Source:
    try:
        pages = pdf_pages(data)
    except PdfTextError as exc:
        raise ConstitutionParseError(str(exc)) from exc
    if sum(len(page.strip()) for page in pages) < 200:
        raise ConstitutionParseError("the PDF has no text layer (ULIS does no OCR)")
    return pages_source(pages)


def pages_source(pages: list[str]) -> Source:
    """Lines of a paged text; running headers and footers (the same line near the top or bottom of at least half of the
    pages, numbers aside) and bare page numbers at a page edge are dropped. Body lines are never dropped."""
    page_lines = [[clean_line(ln) for ln in page.split("\n")] for page in pages]

    def signature(line: str) -> str:
        return re.sub(r"\d+", "#", line.casefold())

    def edge_positions(pl: list[str]) -> set[int]:
        nonblank = [i for i, ln in enumerate(pl) if ln]
        return set(nonblank[:6] + nonblank[-4:])

    n = len(page_lines)
    counts: Counter[str] = Counter()
    for pl in page_lines:
        edges = edge_positions(pl)
        counts.update({signature(pl[i]) for i in edges})
    repeated = {s for s, c in counts.items() if n >= 4 and c >= max(3, n // 2)}
    lines: list[str] = []
    removed = 0
    for pl in page_lines:
        edges = edge_positions(pl)
        for i, ln in enumerate(pl):
            if not ln:
                lines.append("")
            elif i in edges and (
                (len(ln) <= 110 and signature(ln) in repeated and not ARTICLE_START.match(ln)) or PAGE_NUMBER.match(ln)
            ):
                removed += 1
            else:
                lines.append(ln)
    return Source(lines, wrapped=True, pages=n, furniture_removed=removed)


# --------------------------------------------------------------------------- headings

_ART_WORDS = (
    r"article|artículo|articulo|artigo|articolo|artikel|artikkel|artykuł|articolul|art\.|άρθρο|αρθρο|статья|стаття|"
    r"артыкул|член|чл\.|члан|član|članak|čl\.|madde|maddə|neni|հոդված|მუხლი|моддаи"
)
_SUFFIX_WORDS = r"straipsnis|modda|бап|cikk|madde|maddə|maddası"
_NUM = (
    r"(?P<num>\d{1,4}(?:\s?[-–]\s?\d{1,3}|[⁰¹²³⁴⁵⁶⁷⁸⁹]+)?"
    r"(?:[\s\-–]?(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies)\b|[-–][A-Za-z]\b|[-–][IVXLC]{1,6}\b|(?-i:[A-Z])\b)?"
    r"|premier|première|unique|único|unico|primero|primeiro)"
)
_ORD = r"(?:\.?\s?[º°ª]|o\b|er\b)?"
ARTICLE_PREFIX = re.compile(rf"^\s*(?:{_ART_WORDS})\s*{_NUM}{_ORD}(?:\s*[.:)\-–—]+\s*|\s+|$)(?P<rest>.*)$", re.I)
ARTICLE_SUFFIX = re.compile(
    rf"^\s*{_NUM}(?:-?(?:nji|nchi|inci))?\s*[-–.]?\s*(?:{_SUFFIX_WORDS})\b\.?\s*(?P<rest>.*)$", re.I
)
ARTICLE_PAREN = re.compile(rf"^\s*\(\s*(?:{_ART_WORDS})\s*{_NUM}{_ORD}\s*\)\s*[.:\-–—]*\s*(?P<rest>.*)$", re.I)
ARTICLE_START = re.compile(rf"^\s*(?:(?:{_ART_WORDS})\s*\d|\d{{1,4}}[-–.]?\s*(?:{_SUFFIX_WORDS})\b)", re.I)

_STRUCT_WORDS = (
    r"title|titre|título|titulo|titolo|titel|tittel|tytuł|titlu|τίτλος|тіт|раздел|глава|часть|chapter|chapitre|"
    r"capítulo|capitulo|capitolo|kapitel|kapittel|rozdział|capitolul|κεφάλαιο|skyrius|skirsnis|bölüm|kısım|fəsil|"
    r"kreu|kapitulli|poglavlje|glava|dio|odjeljak|odsek|oddelek|section|sección|seccion|sezione|abschnitt|avsnitt|"
    r"partie|parte|part|teil|μέρος|bob|bo'lim|bo‘lim|боб|бөлім|тарау|բաժին|գլուխ|თავი|ნაწილი|"
    r"fejezet|rész|cím|ч\.|sous-section|subsection|unterabschnitt|sottosezione|onderafdeling|subseção|subsección"
)
_ORDINALS = (
    r"primero|primera|segundo|segunda|tercero|tercera|cuarto|cuarta|quinto|quinta|sexto|sexta|séptimo|octavo|noveno|"
    r"décimo|único|única|premier|première|deuxième|troisième|quatrième|cinquième|unique|erster|erste|zweiter|zweite|"
    r"eerste|tweede|derde|prima|primo|secondo|seconda|terza|primeiro|segunda|terceiro|enda|einzig|einziger|"
    r"pirmoji|antroji|trečioji|pirmas|antras|trečias|первый|первая|вторая|второй|третья|третий|"
    r"ier|ière|iere|ère|re|er|i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii|xiii|xiv|xv|xvi|xvii|xviii|xix|xx|xxi"
)
STRUCT_LINE = re.compile(
    rf"^\s*(?P<word>{_STRUCT_WORDS})\s+(?P<n>(?:{_ORDINALS})\b|\d{{1,3}}\b|[IVXLC]{{1,6}}\b|[A-Z]\b)\s*(?:[ºª°]|\.)?\s*"
    rf"(?:[-–—:.]\s*(?P<title>\S.{{0,110}}))?$",
    re.I,
)
STRUCT_SUFFIX = re.compile(
    r"^\s*(?P<n>[IVXLC]{1,6}|\d{1,3}|[^\W\d_]{3,14})\s+(?P<word>bo[‘'’ʻ]lim|bob|skirsnis|skyrius|бөлім|тарау)\b"
    r"\s*[.:–—-]?\s*(?P<title>\S.{0,110})?$",
    re.I,
)
TITLE_CONNECTORS = frozenset(
    ("de", "du", "des", "la", "le", "les", "et", "d'", "l'", "of", "the", "and", "für", "von", "der", "die", "das")
    + ("и", "в", "на", "za", "do", "e", "y", "del", "en", "van", "het", "een", "og", "i", "på")
)
_TRANSITIONAL = re.compile(
    r"transitor|transitoire|übergang|transitional|transizion|tranzitor|пераходн|переходн|заключительн|final provisions|"
    r"disposiciones finales|dispositions finales|schlussbestimm|przejściow|prelazn|prijelazn|geçici|keçid|kalimtare|"
    r"საბოლოო|გარდამავალი|ժամանակավոր|անցումային|overgangs|slutbest|siirtymä|dispozi[tț]ii finale|"
    r"záver|zaključn|završn|ikinci|müvəqqəti|nihai|sonuç|kapanış|өтпелі|ўтиш|o‘tish|o'tish|"
    r"átmeneti|záró|pereinam|baigiam",
    re.I,
)
_PREAMBLE = re.compile(
    r"^\s*(?:preamble|preámbulo|preambulo|préambule|präambel|preambolo|preâmbulo|muqaddima|преамбула|прэамбула|başlangıç|"
    r"hyrje|proemio|προοίμιο|პრეამბულა|նախաբան|aldın söz|кіріспе|preambul|uvod|inleiding|innledning|"
    r"bevezető|preambula|įžanga|sissejuhatus)\s*[.:]?\s*$",
    re.I,
)
DEFAULT_NOTE = re.compile(r"^(?:notas?|notes?|notă|примечание|прим\.)\s*[:.]", re.I)
_TOC_REST = re.compile(r"^[.\s·…_\-–—\d]*$")
_PARA_START = re.compile(
    r"^(?:\(?\d{1,3}\)|\d{1,3}\s?[.)°º-]\s|\([a-z]{1,3}\)|[a-z]\)\s|[ivx]{1,4}\)\s|[-–—•·▪●■]\s)", re.I
)
_SENTENCE_END = re.compile(r"[.;:!?»”\")]$")


def mostly_upper(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return bool(letters) and sum(c.isupper() for c in letters) >= 0.75 * len(letters)


def struct_match(line: str) -> re.Match[str] | None:
    """A structural heading (Title, Chapter, Section, Part ...) in either word order, else None."""
    return STRUCT_LINE.match(line) or STRUCT_SUFFIX.match(line)


def normalise_label(raw: str) -> str:
    label = raw.strip().casefold()
    label = {
        "premier": "1",
        "première": "1",
        "primero": "1",
        "primeiro": "1",
        "único": "unique",
        "unico": "unique",
    }.get(label, label)
    label = re.sub(r"\s*[-–]\s*", "-", label)
    label = re.sub(r"[.\s]+$", "", label)
    return re.sub(r"\s+", " ", label)


def slug(label: str) -> str:
    plain = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+", lambda m: "-" + m.group().translate(SUBSCRIPT_TO_DIGIT), label.casefold())
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-") or "x"


def article_heading(line: str, regex: re.Pattern[str] | None, parenthesised: bool = False) -> tuple[str, str] | None:
    """(label, first text of the article) when the line is an article heading, else None.

    ``parenthesised`` also accepts ``(Artículo 10).- text``, the way some constitutions head their transitional
    provisions; it is only used inside a section headed "transitional" or "final" provisions.
    """
    patterns = [regex] if regex is not None else [ARTICLE_PREFIX, ARTICLE_SUFFIX]
    if parenthesised:
        patterns = [*patterns, ARTICLE_PAREN]
    for pattern in patterns:
        m = pattern.match(line)
        if not m or line.lstrip()[:1].islower():
            continue  # a lower-case "artículo 36." at the start of a wrapped line is a reference, not a heading
        rest = (m.group("rest") or "").strip()
        if _TOC_REST.match(rest):
            rest = ""
        elif rest[:1].islower() and rest.split(" ", 1)[0].casefold() not in ("bis", "ter"):
            continue  # "Artículo 12 de esta Constitución ..." at the start of a wrapped line: a reference
        return normalise_label(m.group("num")), rest
    return None


# --------------------------------------------------------------------------- structure


@dataclass
class Item:
    kind: str  # article | block | preamble
    label: str
    lines: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    heading: str | None = None
    context: str | None = None


LEVELS = {
    "part": 1, "parte": 1, "partie": 1, "teil": 1, "μέρος": 1, "rész": 1,
    "title": 2, "titre": 2, "título": 2, "titulo": 2, "titolo": 2, "titel": 2, "tittel": 2, "tytuł": 2, "titlu": 2,
    "cím": 2, "тіт": 2, "раздел": 2,
    "chapter": 3, "chapitre": 3, "capítulo": 3, "capitulo": 3, "capitolo": 3, "kapitel": 3, "kapittel": 3, "glava": 3,
    "глава": 3, "kreu": 3, "fejezet": 3, "skyrius": 3, "κεφάλαιο": 3,
    "section": 4, "sección": 4, "seccion": 4, "sezione": 4, "abschnitt": 4, "avsnitt": 4, "skirsnis": 4,
    "sous-section": 5, "subsection": 5, "unterabschnitt": 5, "sottosezione": 5, "onderafdeling": 5, "subseção": 5,
    "subsección": 5,
}  # fmt: skip


def split_items(
    source: Source, regex: re.Pattern[str] | None, note_re: re.Pattern[str] | None, *, series: bool = True
) -> tuple[list[Item], list[str]]:
    """Walk the lines once: articles, notes, structural headings; returns (items, lines before the first heading).

    ``series``: a numbering restart in a "transitional" / "final" section becomes a separate series (labels ``T1``...); a caller
    whose documents number such articles continuously (a treaty's final provisions) passes ``series=False``.
    """
    items: list[Item] = []
    before: list[str] = []
    stack: dict[int, str] = {}
    transitional = False
    last_level: int | None = None
    cur: Item | None = None
    orphan: Item | None = None
    started = False
    lines = source.lines

    def context() -> str | None:
        return " › ".join(stack[k] for k in sorted(stack)) or None

    def close() -> None:
        nonlocal cur, orphan
        if cur is not None:
            items.append(cur)
        if orphan is not None and any(ln for ln in orphan.lines):
            items.append(orphan)
        cur = orphan = None

    def next_line(i: int) -> tuple[int, str] | None:
        for j in range(i + 1, min(len(lines), i + 12)):
            if lines[j]:
                return j, lines[j]
        return None

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line:
            target = cur or orphan
            if target is not None:
                target.lines.append("")
            i += 1
            continue
        head = article_heading(line, regex, transitional)
        if head is not None:
            close()
            label, rest = head
            if transitional and series:
                label = "T" + label
            cur = Item("article", label, [rest] if rest else [], context=context())
            started = True
            i += 1
            continue
        sm = struct_match(line)
        if (
            sm
            and len(line) <= 130
            and not (cur is not None and source.wrapped and not _SENTENCE_END.search(lines[i - 1] or "."))
        ):
            close()
            started = True
            word = sm.group("word").casefold()
            title = (sm.group("title") or "").strip()
            label_text = (
                re.sub(r"\s+", " ", line.split(" - ")[0]).strip()
                if not title
                else line[: line.index(title)].strip(" -–—:.")
            )
            consumed = i
            if not title:
                nxt = next_line(i)
                if (
                    nxt is not None
                    and len(nxt[1]) <= 110
                    and not article_heading(nxt[1], regex)
                    and not struct_match(nxt[1])
                ):
                    after = next_line(nxt[0])
                    heading_follows = after is not None and bool(
                        article_heading(after[1], regex) or struct_match(after[1])
                    )
                    wrapped_para = source.wrapped and after is not None and after[1][:1].islower()
                    plain_title = (
                        not wrapped_para and not nxt[1].endswith(".") and not (note_re and note_re.match(nxt[1]))
                    )
                    if heading_follows or plain_title:
                        title = nxt[1]
                        consumed = nxt[0]
            full = f"{label_text} — {title}" if title else label_text
            level = LEVELS.get(word, 3)
            for k in [k for k in stack if k >= level]:
                del stack[k]
            stack[level] = full
            last_level = level
            transitional = bool(_TRANSITIONAL.search(full))
            orphan = Item("block", "", [], heading=full, context=context())
            i = consumed + 1
            continue
        if started and len(line) <= 90 and _TRANSITIONAL.search(line) and (line.isupper() or cur is None):
            # a stand-alone heading such as "TRANSITORIOS" or "Disposiciones finales"
            close()
            transitional = True
            last_level = None
            orphan = Item("block", "", [], heading=line, context=context())
            i += 1
            continue
        target = cur if cur is not None else orphan
        if note_re is not None and note_re.match(line) and target is not None:
            note = [line]
            j = i + 1
            heading_only = len(line) <= 12 and line.rstrip().endswith(
                ":"
            )  # "Notas:" opens a list that runs to a blank line
            while (
                j < len(lines)
                and lines[j]
                and (heading_only or not note[-1].endswith((".", ";")))
                and not article_heading(lines[j], regex)
                and not struct_match(lines[j])
            ):
                note.append(lines[j])
                j += 1
            target.notes.append(" ".join(note))
            i = j
            continue
        if cur is not None:
            cur.lines.append(line)
        elif orphan is not None:
            if (
                not orphan.lines
                and last_level is not None
                and orphan.heading
                and len(line) <= 80
                and not _SENTENCE_END.search(line)
                and (
                    mostly_upper(line)
                    or orphan.heading.split()[-1].casefold() in TITLE_CONNECTORS
                    or (len(orphan.heading) >= 80 and len(line) <= 110)
                )
            ):
                orphan.heading = f"{orphan.heading} {line}"  # the heading's title runs over several lines
                stack[last_level] = orphan.heading
                orphan.context = context()
                i += 1
                continue
            orphan.lines.append(line)
        elif not started:
            before.append(line)
        i += 1
    close()
    return items, before


def paragraphs(lines: list[str], wrapped: bool, width: int) -> list[str]:
    """Re-join the hard-wrapped lines of a PDF or text file into paragraphs (HTML lines already are paragraphs)."""
    if not wrapped:
        return [ln for ln in lines if ln]
    out: list[str] = []
    cur = ""
    prev = ""
    for line in lines:
        if not line:
            if cur and _SENTENCE_END.search(cur):
                out.append(cur)
                cur = ""
            prev = ""
            continue
        starts_new = bool(_PARA_START.match(line)) or (
            bool(cur)
            and bool(prev)
            and len(prev) < 0.7 * width
            and bool(_SENTENCE_END.search(prev))
            and (line[:1].isupper() or line[:1].isdigit())
        )
        if cur and starts_new:
            out.append(cur)
            cur = line
        elif cur:
            joiner = "" if cur.endswith("-") and line[:1].islower() else " "
            cur = cur + joiner + line
        else:
            cur = line
        prev = line
    if cur:
        out.append(cur)
    return out


def line_width(lines: list[str]) -> int:
    lengths = sorted(len(ln) for ln in lines if len(ln) > 30)
    return lengths[int(len(lengths) * 0.85)] if lengths else 80


def preamble_item(before: list[str], hint_start: re.Pattern[str] | None) -> Item | None:
    for index, line in enumerate(before):
        if hint_start is not None and hint_start.search(line):
            return Item("preamble", "", before[index:], heading=None)
        if hint_start is None and _PREAMBLE.match(line):
            body = before[index + 1 :]
            return Item("preamble", "", body) if any(body) else None
    return None


def split_long(para: str) -> list[str]:
    """A paragraph longer than a block is cut at sentence ends (only in blocks, which are ULIS's own division)."""
    if len(para) <= 1.5 * BLOCK_CHARS:
        return [para]
    pieces: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.;:!?])\s+", para):
        if current and len(current) + len(sentence) > BLOCK_CHARS:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    return [*pieces, current] if current else pieces


def chunk_paragraphs(paras: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    size = 0
    for para in (piece for whole in paras for piece in split_long(whole)):
        if current and size + len(para) > BLOCK_CHARS:
            groups.append(current)
            current, size = [], 0
        current.append(para)
        size += len(para) + 1
    if current:
        groups.append(current)
    return groups


def block_items(lines: list[str], wrapped: bool, width: int) -> list[Item]:
    return [Item("block", "", group) for group in chunk_paragraphs(paragraphs(lines, wrapped, width))]


def compile_hint(hints: dict[str, Any], key: str) -> re.Pattern[str] | None:
    value = hints.get(key)
    if not value:
        return None
    try:
        return re.compile(str(value), re.I)
    except re.error as exc:
        raise ConstitutionParseError(f"parser hint {key} is not a regular expression: {exc}") from exc


def apply_cuts(lines: list[str], hints: dict[str, Any]) -> list[str]:
    start, end, drop = compile_hint(hints, "start_at"), compile_hint(hints, "end_at"), compile_hint(hints, "drop_lines")
    if start is not None:
        for index, line in enumerate(lines):
            if start.search(line):
                lines = lines[index:]
                break
        else:
            raise ConstitutionParseError("start_at matches no line of the document")
    if end is not None:
        for index, line in enumerate(lines):
            if index > 0 and end.search(line):
                lines = lines[:index]
                break
    if drop is not None:
        lines = [ln for ln in lines if not drop.search(ln)]
    return lines


def iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ConstitutionParseError(f"not an ISO date: {value!r}") from exc


# --------------------------------------------------------------------------- parser


def parse_constitution_pages_b(content: bytes) -> list[ParsedDocument]:
    bundle = read_bundle(content)
    hints: dict[str, Any] = bundle.get("hints") or {}
    fmt, data = bundle["format"], bundle["data"]
    if fmt == "pdf":
        source = pdf_source(data)
    elif fmt == "html":
        source = html_source(data, bundle.get("content_type"))
    elif fmt == "text":
        source = text_source(data, bundle.get("content_type"))
    else:
        raise ConstitutionParseError(f"unknown format {fmt!r}")
    edition_re = compile_hint(hints, "edition_note_regex")
    edition_line = next((ln for ln in source.lines if edition_re and edition_re.search(ln)), None)
    source.lines = apply_cuts(source.lines, hints)
    if "wrapped" in hints:
        source.wrapped = bool(hints["wrapped"])
    if not any(source.lines):
        raise ParseSkipped("no text")
    regex = compile_hint(hints, "article_regex")
    note_re = compile_hint(hints, "note_regex") or DEFAULT_NOTE
    width = line_width(source.lines)

    items, before = split_items(source, regex, note_re)
    articles = [it for it in items if it.kind == "article" and any(it.lines)]
    min_articles = int(hints.get("min_articles") or 3)
    forced_blocks = hints.get("mode") == "blocks"
    if forced_blocks or len(articles) < min_articles:
        chosen = block_items(source.lines, source.wrapped, width)
        numbering = "blocks"
    else:
        chosen = []
        pre = preamble_item(before, compile_hint(hints, "preamble_start"))
        if pre is not None:
            chosen.append(pre)
        chosen.extend(
            it for it in items if (it.kind == "article" and any(it.lines)) or (it.kind == "block" and any(it.lines))
        )
        numbering = "mixed" if any(it.kind == "block" for it in chosen) else "articles"

    country, language = str(bundle["country"]), str(bundle["language"])
    retrieved = iso_date(bundle["retrieved_on"])
    assert retrieved is not None
    edition = iso_date(hints.get("edition_date"))
    cc = country.lower()
    slugged = re.sub(r"[^a-z0-9-]+", "-", str(hints.get("work_slug") or "constitution"))
    work_uri = f"/{cc}/{slugged}"

    entries: list[tuple[Item, str, bool]] = []  # (item, text, first piece of the item)
    for item in chosen:
        if item.kind == "block" and numbering == "blocks":  # fallback blocks: the lines already are paragraphs
            entries.append((item, "\n".join(item.lines), True))
        elif item.kind == "block":
            groups = chunk_paragraphs(paragraphs(item.lines, source.wrapped, width))
            entries.extend((item, "\n".join(group), index == 0) for index, group in enumerate(groups))
        else:
            entries.append((item, "\n".join(paragraphs(item.lines, source.wrapped, width)), True))

    provisions: list[ParsedProvision] = []
    seen: set[str] = set()
    block_no = 0
    for item, text, first in entries:
        text = text.strip()
        if not text:
            continue
        if item.kind == "article":
            base = "art-" + slug(item.label)
            kind, num_path, top = "article", [item.label], "article"
        elif item.kind == "preamble":
            base, kind, num_path, top = "preamble", "preamble", [], "preamble"
        else:
            block_no += 1
            base, kind, num_path, top = f"blk-{block_no:04d}", "block", [str(block_no)], "block"
        eid, k = base, 1
        while eid in seen:
            k += 1
            eid = f"{base}-{k}"
            if kind == "article":
                num_path = [f"{item.label}-{k}"]
        seen.add(eid)
        note = (" ".join(item.notes)[:NOTE_LIMIT] or None) if first else None
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=kind,
                num_path=num_path,
                top_kind=top,
                heading=item.heading if kind == "block" else None,
                text=text,
                parent_eid=None,
                sort_order=len(provisions),
                status="in_force",
                valid_from=None,
                valid_to=None,
                extent=[country],
                note=note,
                refs=[],
                is_leaf=True,
                has_own_content=False,
                context_heading=item.context,
            )
        )
    if not provisions:
        raise ParseSkipped("no provisions recognised")

    module = str(hints.get("ext_module") or MODULE_ID)
    ext: dict[str, Any] = {}
    if module == MODULE_ID:
        ext = {
            "country_name": str(hints.get("country_name") or country),
            "document_id": bundle["document_id"],
            "source_url": bundle["url"],
            "numbering": numbering,
        }
        if hints.get("edition_note") or edition_line:
            ext["edition_note"] = str(hints.get("edition_note") or edition_line)[:500]
        if hints.get("citation_label"):
            ext["citation_label"] = str(hints["citation_label"])
    title = str(bundle["title"])
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type="constitution",
            jurisdiction_id=country,
            work_uri=work_uri,
            expression_uri=f"{work_uri}/{language}@{retrieved.isoformat()}",
            work_title=title,
            title=title,
            short_title=None,
            number=None,
            work_date=iso_date(hints.get("work_date")),
            language=language,
            authoritative=bool(hints.get("authoritative", False)),
            text_origin=str(hints.get("text_origin") or "official_consolidation"),
            consolidation_kind="current_consolidation",
            version_date=retrieved,
            currency_date=edition or retrieved,
            extent=[country],
            ext_module=module,
            ext=ext,
            events=[
                ParsedEvent(
                    "version_change",
                    retrieved,
                    None,
                    "Retrieval date of the text as the publisher showed it",
                    "document",
                )
            ],
            provisions=provisions,
            validity_basis="attested_at_edition",
        )
    ]
