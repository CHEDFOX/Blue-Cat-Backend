"""Parser for consolidated Swedish statutes from the Riksdag's open data (SFS documents).

``https://data.riksdagen.se/dokument/sfs-<year>-<number>.json`` returns one document of type
``sfs``, subtype ``sfst``: the consolidated text of a statute as kept in the Government Offices'
legal database (Regeringskansliets rättsdatabaser, SFST; the record links to it as "Källa:
Fulltext (Regeringskansliet)"). The Riksdag republishes it; neither the Riksdag's copy nor the
Government Offices' consolidation is authentic. The authentic text is the statute as published
in Svensk författningssamling, which the Government publishes electronically (lag (1976:633) om
kungörande av lagar och andra författningar, 5 §: "En författning ska anses kungjord när den har
publicerats i Svensk författningssamling på webbplatsen").

Format notes, all taken from the documents:

* ``dokumentstatus.dokument`` carries the SFS number (``beteckning``), the title, the
  consolidation status (``subtitel`` "t.o.m. SFS 2022:1600": amendments up to that statute are
  worked in) and the date the record was published (``publicerad``); ``dokumentuppgift`` adds
  the date of issue (``utfardad``), reprints (``omtryck``), editorial remarks (``text2``) and,
  for a repealed statute, ``upphavd`` / ``upphnr`` (skipped here: only statutes in force are ingested).
* ``text`` is the consolidated text as plain text: blocks separated by blank lines, hard-wrapped
  lines. Chapters (``3 kap. Om brott mot liv och hälsa``), headings, sections (``1 §``),
  numbered points (``1.``), sub-points (``a)``), dash items (``-``) and, after
  ``Övergångsbestämmelser``, the transitional provisions grouped under the SFS number of the
  statute they come from. Paragraphs (stycken) are not numbered in the text; a new paragraph
  starts with a block that follows the end of a sentence, is not a list item and does not
  continue the sentence in lower case.
* ``Lag (2010:1408).`` at the end of a section names the statute that last amended it: an
  editorial marker, kept in ``note``, never in ``text``.
* No per-provision dates are given, except for wordings that change on a stated date: the
  wording that will cease is printed with ``/Upphör att gälla U:2027-01-01/`` and the new one with
  ``/Träder i kraft I:2027-01-01/`` (``Rubriken`` for headings, ``Kapitlet`` for whole chapters;
  "den dag som regeringen bestämmer" when the date is still to be set by the government).
  Everything else is attested only at the consolidation (``attested_at_edition``: start
  unknown). A new wording with a stated date is recorded from that date; one awaiting a date set
  by the government is ``prospective``; both are also reported as outstanding changes.
* Repealed sections are printed as a note ("7 § har upphävts genom lag (1988:942).", "3 a § Har
  betecknats 26 kap. 2 a § genom lag (2026:1318)."): status ``repealed``, empty text, the note
  kept. When no date is stated, the repeal is undated.

Identifiers are language-neutral and built from the printed numbers: ``chapter-3-section-1``
when sections restart in each chapter, ``section-36`` when they run through the statute, then
``-paragraph-2`` (stycke, by position), ``-point-1``, ``-subpoint-a``, ``-indent-1`` (dash item),
and ``transitional-2010-1408-point-2`` for transitional provisions. Chapters and headings are
context, not provisions. Whitespace is normalised (the line breaks are hard wraps); after a
line-final hyphen no space is inserted (except before a conjunction, "fri- och"), so a word the
layout hyphenated keeps its hyphen ("gär-ningsorterna").
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import unquote

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParseSkipped

PARSER_ID = "riksdagen_sfs_json"
PARSER_VERSION = "1.3.0"

# Fundamental laws (grundlagar) are named in regeringsformen 1 kap. 3 § ("Regeringsformen, successionsordningen,
# tryckfrihetsförordningen och yttrandefrihetsgrundlagen är rikets grundlagar"; read in the record 1974:152). They are
# cited by name, without the SFS number. The successionsordning's record prints its number as 1810:0926.
FUNDAMENTAL_LAWS = {
    "1974:152": "regeringsformen",
    "1810:0926": "successionsordningen",
    "1949:105": "tryckfrihetsförordningen",
    "1991:1469": "yttrandefrihetsgrundlagen",
}
# Established short names (kortnamn) not printed in the statute's own title. Documented in
# docs/jurisdictions/se.md; to be confirmed by a Swedish reviewer.
SHORT_NAMES = {"1915:218": "avtalslagen"}

# The SFS number as the Riksdag's records print it (``beteckning``): ``YYYY:N``, the serial kept as printed (the
# successionsordning is ``1810:0926``). A few statutes of the 1700s-1900s carry a suffix that is part of their number:
# the balk (book) of the 1734 law, ``1736:0123 1`` (byggningabalken) and ``1736:0123 2`` (handelsbalken), or the page
# of the volume where the statute begins, ``1904:48 s.1`` (also printed ``1907:36 s. 22``; one record spells it
# ``1904:48s1``).
# Supplement references (``1899:bih. 25``), the N series and RFS numbers are not SFS numbers of these forms.
_SFS = re.compile(r"^(\d{4}):(\d{1,4})(?: (\d)| ?s\.? ?(\d{1,4}))?$")
_NUM = r"\d+(?: ?[a-z])?"
# "4 b § Text", "4 c /Träder i kraft I:.../ § Text" (a marker before "§"), "53 g §Text" (no space). A repeal note
# follows the number in lower case: "7 § har upphävts genom lag (1988:942)." or, in the oldest statutes, "1 § upphävd
# genom förordning (1933:269)." (byggningabalken).
_REPEAL_NOTE = r"(?:[Hh]ar (?:upp?hävts|upphört|betecknats)|upphävd\b)"
_SECTION = re.compile(rf"^({_NUM}) ((?:/[^/]*/\s*)*)§\.?(?:\s*((?:[A-ZÅÄÖ/]|{_REPEAL_NOTE}).*))?$", re.S)
_SECTION_LINE = re.compile(rf"^{_NUM} §\s*(?=[A-ZÅÄÖ/])")
_SECTION_RANGE = re.compile(
    rf"^({_NUM})(?:\s*[-–]\s*|\s+och\s+)({_NUM}) §§ ([Hh]ar (?:upp?hävts|upphört att gälla) .*)$", re.S
)
# A chapter heading's title starts in upper case (or is a marker, or a repeal note); "16 kap. och 17 kap. 1, 2 och
# 5 §§ jordabalken samt ..." is an enumeration inside a section (lag (1907:36 s.22) 3 §), not a chapter.
_CHAPTER = re.compile(rf"^({_NUM}) [Kk]ap\.(?:\s+((?:[A-ZÅÄÖ0-9/(\"»]|{_REPEAL_NOTE}).*))?$", re.S)
_POINT = re.compile(rf"^({_NUM})[.)] (.*)$", re.S)
_SUBPOINT = re.compile(r"^([a-zåäö])\) (.*)$", re.S)
_INDENT = re.compile(r"^[-–] (.*)$", re.S)
_TRANSITIONAL_HEAD = re.compile(r"^(\d{4}):(\d{1,4})\.?$")
# An annex: a line "Bilaga" or "Bilaga 1" on its own; its text runs up to the transitional provisions or the end.
_ANNEX = re.compile(r"^Bilaga(?:\s+\S+)?$")
_ELISION = re.compile(r"^(?:- ?){3,}$|^-{3,}$|^\[\.\.\.\]$")
_MARKER = re.compile(
    r"^/\s*(?:(Rubriken|Kapitlet|Avdelningen)\s+)?(träder i kraft|upphör att gälla)\s+([IU])\s*:\s*(.*?)\s*/?$",
    re.I,
)
_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})\b")
_AMENDED_BY = re.compile(
    r"\s*(?:(Lag|Förordning|Kungörelse)\s*\(\s*(\d{4}\s*:\s*\d+)\s*\)|\(\s*(Lag|Förordning)\s+(\d{4}\s*:\s*\d+)\s*\))\s*\.?\s*$"
)
# "har upphävts genom lag (1988:942).", "upphävd genom förordning (1860:56 s.3).", "har upphävts genom förordning
# (1971:?)." (byggningabalken prints an unknown repealing statute so).
_SECTION_REPEAL = re.compile(
    r"^(?:[Hh]ar (?:upp?hävts|upphört att gälla)|[Hh]ar betecknats|upphävd) .*(?:genom|enligt) "
    r"(?:[Ll]ag|[Ff]örordning|[Kk]ungörelse) *\(?(?:\d{4}:(?:\d+(?: ?s\. ?\d+)?|\?))?\)?\s*\??\.?$"
)
_POINT_REPEAL = re.compile(r"^[Hh]ar upp?hävts genom [Ll]ag \(?\d{4}:\d+\)?\.?$")
_CHAPTER_REPEAL = re.compile(r"^(?:[Hh]ar (?:upp?hävts|upphört att gälla)|upphävd genom)\b")
_SENTENCE_END = re.compile(r"[.!?]['\")]*$")
_CONJUNCTION = re.compile(r"(?:och|eller|samt|som|respektive|resp\.|liksom)\b")
_HEADING_ABBREVIATION = re.compile(r"(?:m\. ?m\.|m\. ?fl\.|etc\.)$")


class SfsParseError(ValueError):
    pass


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _join(lines: list[str]) -> str:
    """Undo the hard wrapping of a block: lines are joined with a space, except after a line-final
    hyphen ("tv-|utsändningar", "1-|4"), where no space is inserted unless the next line starts with
    a conjunction ("fri-|och rättigheter"). Hyphens are kept as printed."""
    out = ""
    for line in lines:
        if out and not (re.search(r"\w-$", out) and not _CONJUNCTION.match(line)):
            out += " "
        out += line
    return _ws(out)


def _compact(label: str) -> str:
    """``2 a`` -> ``2a``."""
    return re.sub(r"[^0-9a-zåäö]", "", label.lower())


def _spaced(label: str) -> str:
    """``2a`` / ``2 a`` -> ``2 a`` (the printed form)."""
    m = re.fullmatch(r"(\d+) ?([a-zåäö]?)", label.strip())
    return f"{m.group(1)} {m.group(2)}".strip() if m else label.strip()


@dataclass(frozen=True)
class SfsNumber:
    """An SFS number read from a record: ``1962:700``, ``1810:0926``, ``1736:0123 2``, ``1904:48 s.1``."""

    year: int
    serial: str  # as printed: "700", "0926"
    balk: str | None = None  # "1" / "2": the balk of the 1734 law
    page: str | None = None  # "1", "22": the page of the volume where the statute begins

    @property
    def suffix(self) -> str | None:
        return self.balk or (f"s.{self.page}" if self.page else None)

    @property
    def canonical(self) -> str:
        """The number in one spelling, whatever the record's: ``1736:0123 2``, ``1904:48 s.1``, ``1907:36 s.22``."""
        head = f"{self.year}:{self.serial}"
        return f"{head} {self.suffix}" if self.suffix else head

    @property
    def work_uri(self) -> str:
        """``/se/sfs/1962/700`` (the serial as an integer); a suffixed number adds the suffix to the serial, without
        spaces or dots, so that every spelling of one statute's number names the same work: ``/se/sfs/1736/123-2``
        (handelsbalken), ``/se/sfs/1904/48-s1`` (lagen (1904:48 s.1) om samäganderätt)."""
        tail = str(int(self.serial))
        if self.balk:
            tail += f"-{self.balk}"
        elif self.page:
            tail += f"-s{self.page}"
        return f"/se/sfs/{self.year}/{tail}"


def parse_sfs_number(text: str) -> SfsNumber | None:
    """The SFS number a record prints (whitespace runs collapsed), or None when it is not one of the forms above."""
    m = _SFS.match(" ".join(str(text).split()))
    return SfsNumber(int(m.group(1)), m.group(2), m.group(3), m.group(4)) if m else None


def _number_pattern(number: str) -> str:
    """A pattern for a canonical number that also matches the spellings titles use (``s. 22``, ``s.22``)."""
    return re.escape(number).replace("\\ ", r"\s*").replace(r"s\.", r"s\.\s*")


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map a Riksdag SFS document URL (``https://data.riksdagen.se/dokument/sfs-1974-152``, with or without a format
    suffix; ``sfs-1904-48%20s.1`` for a suffixed number) to (ULIS work URI, None)."""
    m = re.match(r"^https?://data\.riksdagen\.se/dokument/sfs-(\d{4})-(.+?)(?:\.(?:json|html|text|xml))?$", uri)
    number = parse_sfs_number(f"{m.group(1)}:{unquote(m.group(2))}") if m else None
    return (number.work_uri, None) if number else None


def citation_name(number: str, title: str) -> tuple[str, str]:
    """How the statute is named in a citation, and on what basis.

    ``Brottsbalk (1962:700)`` -> ``brottsbalken``; ``Förvaltningslag (2017:900)`` ->
    ``förvaltningslagen (2017:900)``; ``Lag (1982:80) om anställningsskydd`` -> ``lagen (1982:80)
    om anställningsskydd``; fundamental laws and established short names from the tables above.
    """
    if number in FUNDAMENTAL_LAWS:
        return FUNDAMENTAL_LAWS[number], "fundamental_law"
    if number in SHORT_NAMES:
        return SHORT_NAMES[number], "established_short_name"
    m = re.fullmatch(rf"(\w+)(balk|lag|förordning|ordning) \({_number_pattern(number)}\)", title)
    if m:
        definite = (m.group(1) + m.group(2)).lower() + "en"  # brottsbalk -> brottsbalken
        # Codes (balkar) are cited without the SFS number; other named statutes with it.
        return (definite if m.group(2) == "balk" else f"{definite} ({number})"), "title"
    m = re.fullmatch(rf"(Lag|Förordning|Kungörelse) \({_number_pattern(number)}\) (.+)", title)
    if m:
        head = {"Lag": "lagen", "Förordning": "förordningen", "Kungörelse": "kungörelsen"}[m.group(1)]
        return f"{head} ({number}) {m.group(2)}", "title"
    return title, "title"


def _instrument_type(number: str, title: str) -> str:
    if number in FUNDAMENTAL_LAWS:
        return "constitution"
    if re.match(r"^(?:Förordning\b|\w+förordning \()", title):
        return "ordinance"
    return "act"


# --------------------------------------------------------------------------- markers


@dataclass(frozen=True)
class Marker:
    scope: str  # "" (the section), "Rubriken", "Kapitlet", "Avdelningen"
    kind: str  # "I" (enters into force) or "U" (ceases to apply)
    when: date | None  # None: the date is still to be set (e.g. by the government)
    raw: str


def _marker(line: str) -> Marker | None:
    m = _MARKER.match(line.strip())
    if not m:
        return None
    iso = _ISO.match(m.group(4))
    when = date.fromisoformat(iso.group(1)) if iso else None
    scope = (m.group(1) or "").capitalize()
    inner = _ws(line.strip()).strip("/").strip()
    return Marker(scope, m.group(3).upper(), when, f"/{inner}/")


def _split_markers(lines: list[str]) -> tuple[list[Marker], list[str]]:
    """Leading ``/.../`` marker lines of a block, and the remaining lines."""
    markers: list[Marker] = []
    i = 0
    while i < len(lines):
        mk = _marker(lines[i])
        if mk is None:
            break
        markers.append(mk)
        i += 1
    return markers, lines[i:]


# --------------------------------------------------------------------------- structures


@dataclass
class _Item:
    kind: str  # point | indent
    num: str
    text: str
    subpoints: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class _Stycke:
    intro: list[str] = field(default_factory=list)
    items: list[_Item] = field(default_factory=list)
    closing: list[str] = field(default_factory=list)


@dataclass
class _Variant:
    num: str  # printed number, e.g. "2 a"
    chapter: str | None
    chapter_heading: str | None
    heading: str | None
    markers: list[Marker]
    blocks: list[str] = field(default_factory=list)
    order: int = 0
    printed: str | None = None  # the source's note for sections repealed together ("13-27 §§ Har upphävts ...")


def _blocks(text: str) -> list[list[str]]:
    """Blank-line separated blocks (lists of lines). A line starting a section after a line that
    ends a sentence also starts a block (the source sometimes omits the blank line)."""
    out: list[list[str]] = []
    current: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            if current:
                out.append(current)
                current = []
            continue
        if current and _SECTION_LINE.match(line) and re.search(r"[.:]$", current[-1]):
            out.append(current)
            current = []
        current.append(line)
    if current:
        out.append(current)
    return out


def _strip_amended_by(text: str) -> tuple[str, str | None]:
    """Remove a trailing ``Lag (2010:1408).`` marker; return (text, ``Lag (2010:1408)``)."""
    m = _AMENDED_BY.search(text)
    before = text[: m.start()].rstrip() if m else ""
    # Only after the end of a sentence: "... genom Lag (1990:443)." is a repeal note, not a marker.
    if not m or (before and not re.search(r"""[.!?:;]['"’”]*$""", before)):
        return text, None
    kind = m.group(1) or m.group(3)
    number = re.sub(r"\s+", "", m.group(2) or m.group(4))
    return before, f"{kind} ({number})"


def _stycken(blocks: list[str]) -> list[_Stycke]:
    """Group a section's blocks into paragraphs (stycken).

    A list item belongs to the current paragraph (until a closing clause has been seen); a block
    continues the current paragraph when the previous block does not end a sentence or when it
    starts in lower case (a closing clause such as "döms ... till fängelse"); otherwise it starts
    a new paragraph.
    """
    out: list[_Stycke] = []
    previous = ""
    for b in blocks:
        current = out[-1] if out else None
        point, sub, indent = _POINT.match(b), _SUBPOINT.match(b), _INDENT.match(b)
        continues = current is not None and (not _SENTENCE_END.search(previous) or b[:1].islower())
        if current is not None and sub and current.items and current.items[-1].kind == "point" and not current.closing:
            current.items[-1].subpoints.append((sub.group(1), sub.group(2)))
        elif point or indent:
            if current is None or current.closing:
                current = _Stycke()
                out.append(current)
            if point:
                current.items.append(_Item("point", _spaced(point.group(1)), point.group(2)))
            else:
                assert indent is not None
                n = sum(1 for i in current.items if i.kind == "indent") + 1
                current.items.append(_Item("indent", str(n), indent.group(1)))
        elif current is not None and continues:
            (current.closing if current.items else current.intro).append(b)
        else:
            out.append(_Stycke(intro=[b]))
        previous = b
    return out


def _item_text(item: _Item) -> str:
    lines = [item.text] + [f"{letter}) {text}" for letter, text in item.subpoints]
    return "\n".join(lines)


def _stycke_text(st: _Stycke) -> str:
    lines = list(st.intro)
    for item in st.items:
        label = f"{item.num}. " if item.kind == "point" else "- "
        lines.append(label + _item_text(item))
    lines.extend(st.closing)
    return "\n".join(lines)


# --------------------------------------------------------------------------- parser


def parse_riksdagen_sfs(content: bytes) -> list[ParsedDocument]:
    try:
        data = json.loads(content)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SfsParseError(f"not JSON: {exc}") from exc
    status = data.get("dokumentstatus") if isinstance(data, dict) else None
    dok = status.get("dokument") if isinstance(status, dict) else None
    if not isinstance(dok, dict):
        raise SfsParseError("not a Riksdag document (no dokumentstatus.dokument)")
    if dok.get("typ") != "sfs" or dok.get("subtyp") != "sfst":
        raise SfsParseError(
            f"unsupported document type {dok.get('typ')!r}/{dok.get('subtyp')!r}: only consolidated statutes (sfs/sfst)"
        )
    facts_list = (status.get("dokumentuppgift") or {}).get("uppgift") or []  # type: ignore[union-attr]
    facts = {str(u.get("kod")): str(u.get("text") or "") for u in facts_list if isinstance(u, dict)}
    if facts.get("upphavd"):
        by = facts.get("upphnr") or "repealing act not stated"
        raise ParseSkipped(
            f"the statute is repealed (upphävd {facts['upphavd'][:10]}, {by}); only statutes in force are ingested"
        )
    sfs = parse_sfs_number(str(dok.get("beteckning") or ""))
    if sfs is None:
        raise SfsParseError(f"unsupported SFS number {_ws(str(dok.get('beteckning') or ''))!r}")
    number = sfs.canonical
    title = _ws(str(dok.get("titel") or ""))
    if _squash(f"({number})") not in _squash(title):  # the title may space the number differently ("s. 22")
        raise SfsParseError(f"title {title!r} does not carry the SFS number {number}")
    text = str(dok.get("text") or "")
    if not text.strip():
        raise ParseSkipped(f"{number}: the record has no text")  # nothing to store (not flagged as repealed)
    try:
        published = datetime.strptime(str(dok.get("publicerad"))[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise SfsParseError(f"missing or invalid publicerad {dok.get('publicerad')!r}") from exc
    version_date = published.date()
    issued = _date(facts.get("utfardad"))
    status_line = _ws(facts.get("andrattillochmed") or str(dok.get("subtitel") or ""))
    consolidated_to = re.sub(r"^t\.o\.m\.\s*", "", status_line) or None
    name, name_basis = citation_name(number, title)

    # ------------------------------------------------------------------ pass 1: classify blocks
    raw_blocks = _blocks(text)
    kinds: list[str] = []
    bodies: list[str] = []
    markers_of: list[list[Marker]] = []
    in_transitional = False
    in_annex = False
    for lines in raw_blocks:
        markers, rest = _split_markers(lines)
        body = _join(rest)
        markers_of.append(markers)
        bodies.append(body)
        if _ANNEX.match(body):
            in_annex, in_transitional = True, False
            kinds.append("annex_start")
        elif in_annex and body == "Övergångsbestämmelser":
            in_annex, in_transitional = False, True
            kinds.append("transitional_start")
        elif in_annex:
            kinds.append("annex")
        elif in_transitional:
            kinds.append("transitional")
        elif not body:
            kinds.append("marker")
        elif body == "Övergångsbestämmelser":
            in_transitional = True
            kinds.append("transitional_start")
        elif _SECTION.match(body):
            kinds.append("section")
        elif _SECTION_RANGE.match(body):
            kinds.append("section_range")
        elif _CHAPTER.match(body):
            kinds.append("chapter")
        elif _POINT.match(body) or _SUBPOINT.match(body) or _INDENT.match(body):
            kinds.append("item")
        else:
            kinds.append("text")
    # Headings: text blocks without closing punctuation that are followed by structure (a section,
    # a chapter, a marker, the transitional provisions, the end, or another heading).
    structured = any(k in ("section", "section_range", "chapter") for k in kinds)  # else nothing for a heading to head
    for i in range(len(kinds) - 1, -1, -1):
        if kinds[i] != "text" or not structured:
            continue
        body = _strip_amended_by(bodies[i])[0]
        marked = any(mk.scope == "Rubriken" for mk in markers_of[i])
        # A heading is one phrase: no closing punctuation (except an abbreviation such as "m.m."), no
        # sentence boundary inside, and it does not continue a sentence in lower case.
        candidate = (
            len(body) <= 200
            and not body[:1].islower()
            and (not re.search(r"[.,;:]$", body) or bool(_HEADING_ABBREVIATION.search(body)))
            and not re.search(
                r"[.!?] [A-ZÅÄÖ]", re.sub(r"^(?:AVDELNING |Avdelning )?[IVXLC]+\. ", "", body)
            )  # "I. Om ..."
        )
        nxt = kinds[i + 1] if i + 1 < len(kinds) else "end"
        if marked or (candidate and nxt in ("section", "chapter", "marker", "transitional_start", "end", "heading")):
            kinds[i] = "heading"

    # ------------------------------------------------------------------ pass 2: sections
    variants: list[_Variant] = []
    unattached: list[str] = []
    chapter: str | None = None
    chapter_heading: dict[str, str | None] = {"current": None, "new": None}
    chapter_markers: list[Marker] = []
    group: dict[str, str | None] = {"current": None, "new": None}
    pending: list[Marker] = []
    heading_run: list[str] = []
    current: _Variant | None = None
    transitional_blocks: list[str] = []
    annexes: list[tuple[str, list[str]]] = []  # (printed heading, blocks)
    for i, (kind, body, markers) in enumerate(zip(kinds, bodies, markers_of, strict=True)):
        if kind == "transitional":
            transitional_blocks.append(body)
            continue
        if kind == "annex_start":
            current = None
            annexes.append((body, []))
            continue
        if kind == "annex":
            if body:
                annexes[-1][1].append(body)
            continue
        if kind == "marker":
            pending.extend(markers)
            continue
        if kind == "transitional_start":
            current = None
            continue
        markers = pending + markers
        pending = []
        if kind == "chapter":
            m = _CHAPTER.match(body)
            assert m is not None
            current = None
            heading_run = []
            group = {"current": None, "new": None}
            chapter_rest = _ws(m.group(2) or "")
            number_printed = _spaced(m.group(1))
            ch_markers = [mk for mk in markers if mk.scope == "Kapitlet"]
            if number_printed != chapter:  # a new chapter
                chapter_heading = {"current": None, "new": None}
                chapter_markers = ch_markers
            elif ch_markers:  # another wording of the same chapter (e.g. the chapter as it will read)
                chapter_markers = ch_markers
            chapter = number_printed
            if _CHAPTER_REPEAL.match(chapter_rest):  # "8 kap. har upphört att gälla genom lag (1978:855)."
                continue
            # "/Rubriken upphör.../" and "/Rubriken träder i kraft.../" give two wordings of the heading.
            for lane in _lanes([mk for mk in markers if mk.scope != "Kapitlet"] or ch_markers):
                chapter_heading[lane] = f"{number_printed} kap. {chapter_rest}".strip()
            continue
        if kind == "heading":
            current = None
            prev_kind = kinds[i - 1] if i > 0 else ""
            heading_run = [*heading_run, body] if prev_kind == "heading" else [body]
            for lane in _lanes(markers):
                group[lane] = " – ".join(heading_run)
            continue
        if kind == "section_range":
            r = _SECTION_RANGE.match(body)
            assert r is not None
            for num in _range(r.group(1), r.group(2), " och " in body):
                variants.append(
                    _Variant(
                        num=num,
                        chapter=chapter,
                        chapter_heading=chapter_heading["current"],
                        heading=group["current"],
                        markers=list(markers),
                        blocks=[r.group(3)],
                        order=len(variants),
                        printed=body,
                    )
                )
            current = None
            heading_run = []
            continue
        if kind == "section":
            m = _SECTION.match(body)
            assert m is not None
            num = _spaced(m.group(1))
            tail = m.group(3) or ""
            # markers printed between the number and "§", or after "N §" on the same line(s)
            own: list[Marker] = [mk for mk in map(_marker, re.findall(r"/[^/]*/", m.group(2))) if mk is not None]
            while tail.startswith("/"):
                end = tail.find("/", 1)
                if end < 0:
                    break
                mk = _marker(tail[: end + 1])
                if mk is None:
                    break
                own.append(mk)
                tail = tail[end + 1 :].strip()
            sec_markers = markers + own
            starts_new = any(mk.kind == "I" for mk in sec_markers) or any(mk.kind == "I" for mk in chapter_markers)
            current = _Variant(
                num=num,
                chapter=chapter,
                chapter_heading=chapter_heading["new" if starts_new else "current"],
                heading=group["new" if starts_new else "current"],
                markers=[*sec_markers, *[mk for mk in chapter_markers if mk.kind not in {s.kind for s in sec_markers}]],
                blocks=[tail] if tail else [],
                order=len(variants),
            )
            variants.append(current)
            heading_run = []
            continue
        # text or list item
        if current is None:
            unattached.append(body)
        else:
            current.blocks.append(body)
    if unattached and not variants:
        # Some statutes (a repeal, an index figure, an approval of an agreement) have no numbered section: the whole
        # text before the transitional provisions is cited as the statute itself ("första stycket förordningen ...").
        variants.append(
            _Variant(num="", chapter=None, chapter_heading=None, heading=None, markers=[], blocks=unattached)
        )
        unattached = []
    if unattached:
        raise SfsParseError(f"text outside any section: {unattached[0][:80]!r}")
    if not variants:
        raise SfsParseError("no sections found")

    # sections restart in each chapter?
    chapters_of: dict[str, set[str | None]] = {}
    for v in variants:
        chapters_of.setdefault(v.num, set()).add(v.chapter)
    per_chapter = any(len(chs) > 1 for chs in chapters_of.values())

    provisions: list[ParsedProvision] = []
    outstanding: list[dict[str, Any]] = []
    seen: set[tuple[str, str, date | None]] = set()
    subtree: dict[tuple[str, int], set[str]] = {}
    ceasing: list[tuple[str, str, str]] = []  # (eId, label, markers) of wordings that cease

    def section_eid(v: _Variant) -> str:
        if not v.num:
            return "main-text"
        local = f"section-{_compact(v.num)}"
        return f"chapter-{_compact(v.chapter)}-{local}" if per_chapter and v.chapter else local

    def label(v: _Variant) -> str:
        if not v.num:
            return "the text"
        return f"{v.chapter} kap. {v.num} §" if per_chapter and v.chapter else f"{v.num} §"

    def emit(
        eid: str,
        kind: str,
        path: list[str],
        text_value: str,
        parent: str | None,
        *,
        status: str,
        start: date | None,
        end: date | None,
        note: str | None,
        heading: str | None,
        context: str | None,
        leaf: bool,
        own: bool,
        top_kind: str = "section",
    ) -> bool:
        key = (eid, status, start)
        if key in seen:
            return False
        seen.add(key)
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=kind,
                num_path=path,
                top_kind=top_kind,
                heading=heading,
                text="" if status == "repealed" else text_value,
                parent_eid=parent,
                sort_order=len(provisions),
                status=status,  # type: ignore[arg-type]
                valid_from=start,
                valid_to=end if start is None or end is None or end > start else None,
                extent=["SE"],
                note=(note or "")[:600] or None,
                refs=[],
                is_leaf=leaf,
                has_own_content=own and not leaf,
                context_heading=context,
            )
        )
        return True

    for v in variants:
        eid = section_eid(v)
        base_path = ([f"ch:{v.chapter}"] if v.chapter else []) + ([f"s:{v.num}"] if v.num else ["body:"])
        start_marker = next((mk for mk in v.markers if mk.kind == "I"), None)
        end_marker = next((mk for mk in v.markers if mk.kind == "U"), None)
        start = start_marker.when if start_marker else None
        end = end_marker.when if end_marker else None
        # "Lag (2010:1408)." normally ends the section; the source occasionally prints it before a
        # last paragraph. Wherever it ends a block, it is editorial.
        blocks: list[str] = []
        markers_found: list[str] = []
        for block in v.blocks:
            cleaned, marker = _strip_amended_by(block)
            if marker:
                markers_found.append(marker)
            if cleaned:
                blocks.append(cleaned)
        amended_by = markers_found[-1] if markers_found else None
        notes = [mk.raw for mk in v.markers]
        if markers_found:
            notes.append(f"Wording from {', '.join(markers_found)}.")
        whole = " ".join(blocks)
        if start_marker is not None and start is None:
            status = "prospective"
        elif len(blocks) == 1 and _SECTION_REPEAL.match(whole):
            status = "repealed"
            notes.insert(0, v.printed or f"{v.num} § {whole}")
        else:
            status = "in_force"
        if status != "repealed" and not blocks:
            raise SfsParseError(f"{label(v)}: no text")
        note = " ".join(notes) or None
        stycken = _stycken(blocks) if status != "repealed" else []
        sec_text = "\n\n".join(_stycke_text(s) for s in stycken)
        if not emit(
            eid,
            "section",
            base_path,
            sec_text,
            None,
            status=status,
            start=start,
            end=end,
            note=note,
            heading=v.heading,
            context=v.chapter_heading,
            leaf=not stycken,
            own=False,
        ):
            continue  # the same section, status and start printed twice: the first is kept
        children: set[str] = set()
        for n, st in enumerate(stycken, start=1):
            st_eid = f"{eid}-paragraph-{n}"
            st_path = [*base_path, f"st:{n}"]
            children.add(st_eid)
            emit(
                st_eid,
                "paragraph",
                st_path,
                _stycke_text(st),
                eid,
                status=status,
                start=start,
                end=end,
                note=note,
                heading=None,
                context=v.chapter_heading,
                leaf=not st.items,
                own=bool(st.intro or st.closing),
            )
            for item in st.items:
                if item.kind == "point":
                    it_eid, it_path = f"{st_eid}-point-{_compact(item.num)}", [*st_path, f"p:{item.num}"]
                else:
                    it_eid, it_path = f"{st_eid}-indent-{item.num}", [*st_path, f"ind:{item.num}"]
                children.add(it_eid)
                emit(
                    it_eid,
                    item.kind,
                    it_path,
                    _item_text(item),
                    st_eid,
                    status=status,
                    start=start,
                    end=end,
                    note=note,
                    heading=None,
                    context=v.chapter_heading,
                    leaf=not item.subpoints,
                    own=True,
                )
                for letter, sub_text in item.subpoints:
                    sp_eid = f"{it_eid}-subpoint-{letter}"
                    children.add(sp_eid)
                    emit(
                        sp_eid,
                        "subpoint",
                        [*it_path, f"sp:{letter}"],
                        sub_text,
                        it_eid,
                        status=status,
                        start=start,
                        end=end,
                        note=note,
                        heading=None,
                        context=v.chapter_heading,
                        leaf=True,
                        own=False,
                    )
        subtree[(eid, v.order)] = children
        # Changes not yet in force, worded so that they read correctly on every version of the section.
        printed = " ".join(mk.raw for mk in v.markers)
        if start_marker is not None and (start is None or start > version_date):
            when = f"recorded from {start}" if start else "recorded as prospective (commencement date not yet set)"
            if end is not None:
                when += f" until {end}"
            outstanding.append(
                {
                    "affects_eid": eid,
                    "affected_label": label(v),
                    "by_uri": None,
                    "by_title": amended_by,
                    "description": f"A new wording is printed in the consolidation ({printed}); {when}.",
                }
            )
        elif end_marker is not None and (end is None or end > version_date) and status == "in_force":
            ceasing.append((eid, label(v), printed))

    amended_sections = {c["affects_eid"] for c in outstanding}
    for eid, sec_label, printed in ceasing:
        if eid not in amended_sections:  # otherwise the new wording's entry says it
            outstanding.append(
                {
                    "affects_eid": eid,
                    "affected_label": sec_label,
                    "by_uri": None,
                    "by_title": None,
                    "description": f"The wording in force at the consolidation date ceases to apply ({printed}); "
                    "no new wording is printed.",
                }
            )

    # A wording that ceases on a stated date ends then; what has no successor on that date (the whole
    # section, or a paragraph or point the new wording no longer has) is recorded as repealed then.
    for v in variants:
        eid = section_eid(v)
        end_marker = next((mk for mk in v.markers if mk.kind == "U"), None)
        if end_marker is None or end_marker.when is None or (eid, v.order) not in subtree:
            continue
        end = end_marker.when
        successors = [
            w for w in variants if section_eid(w) == eid and any(mk.kind == "I" and mk.when == end for mk in w.markers)
        ]
        kept: set[str] = set()
        for w in successors:
            kept |= subtree.get((eid, w.order), set())
        gone = sorted(subtree[(eid, v.order)] - kept)
        if not successors:
            gone.insert(0, eid)
        for g in gone:
            original = next(p for p in provisions if p.eid == g)
            emit(
                g,
                original.kind,
                original.num_path,
                "",
                original.parent_eid if g != eid else None,
                status="repealed",
                start=end,
                end=None,
                note=f"Not part of the wording that applies from {end} ({end_marker.raw}).",
                heading=None,
                context=original.context_heading,
                leaf=True,
                own=False,
            )

    # ------------------------------------------------------------------ transitional provisions
    _transitional(transitional_blocks, emit)
    _annexes(annexes, emit)
    # A repealed version has no children; when another wording of the same provision has some, the
    # provision is a container (the index uses the first version recorded to decide what to index).
    containers = {p.eid for p in provisions if not p.is_leaf}
    for p in provisions:
        if p.status == "repealed" and p.eid in containers:
            p.is_leaf = False

    events = []
    if issued is not None:
        events.append(
            ParsedEvent("enactment", issued, None, f"Utfärdad {issued.isoformat()}", "dokumentuppgift/utfardad")
        )
    events.append(ParsedEvent("version_change", version_date, None, consolidated_to, "dokument/publicerad"))

    uri = sfs.work_uri
    ext: dict[str, Any] = {
        "sfs_number": number,
        "year": sfs.year,
        "number": int(sfs.serial),
        "suffix": sfs.suffix,
        "citation_name": name,
        "citation_name_basis": name_basis,
        "sections_per_chapter": per_chapter,
        "consolidated_to": consolidated_to,
        "department": _ws(str(dok.get("organ") or "")) or None,
        "reprints": facts.get("omtryck") or None,
        "editorial_remark": _ws(facts.get("text2") or "") or None,
        "record_published": published.isoformat(),
    }
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=_instrument_type(number, title),
            jurisdiction_id="SE",
            work_uri=uri,
            expression_uri=f"{uri}/swe@{version_date.isoformat()}",
            work_title=title,
            title=title,
            short_title=title,
            number=number,
            work_date=issued,
            language="sv",
            authoritative=False,
            # Consolidated by the Government Offices' editors; the authentic text is the statute as
            # published in Svensk författningssamling.
            text_origin="official_revised",
            consolidation_kind="current_consolidation",
            version_date=version_date,
            currency_date=version_date,
            extent=["SE"],
            ext_module="se",
            ext={k: v for k, v in ext.items() if v is not None},
            outstanding_changes=outstanding,
            events=events,
            provisions=provisions,
            validity_basis="attested_at_edition",
        )
    ]


def _range(first: str, last: str, pair: bool) -> list[str]:
    """Section numbers of "13-27 §§" (13 to 27), "4 a-4 c §§" (4 a to 4 c) or "5 och 6 §§" (5, 6)."""
    a, b = _spaced(first), _spaced(last)
    ma, mb = re.fullmatch(r"(\d+) ?([a-z]?)", a), re.fullmatch(r"(\d+) ?([a-z]?)", b)
    if pair or ma is None or mb is None:
        return [a, b]
    if not ma.group(2) and not mb.group(2) and int(ma.group(1)) <= int(mb.group(1)):
        return [str(n) for n in range(int(ma.group(1)), int(mb.group(1)) + 1)]
    if ma.group(1) == mb.group(1) and ma.group(2) and mb.group(2) and ma.group(2) <= mb.group(2):
        return [f"{ma.group(1)} {chr(c)}" for c in range(ord(ma.group(2)), ord(mb.group(2)) + 1)]
    return [a, b]


def _lanes(markers: list[Marker]) -> tuple[str, ...]:
    """Which wording a heading belongs to: the current one (ceasing), the new one, or both."""
    kinds = {mk.kind for mk in markers}
    if kinds == {"U"}:
        return ("current",)
    if kinds == {"I"}:
        return ("new",)
    return ("current", "new")


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _annexes(annexes: list[tuple[str, list[str]]], emit: Callable[..., bool]) -> None:
    """Annexes (bilagor): each is one provision with the annex's text as printed (tables, forms, agreement texts keep
    only the source's line breaks as paragraph breaks)."""
    for n, (heading, blocks) in enumerate(annexes, start=1):
        if not blocks:
            continue
        emit(
            f"annex-{n}",
            "annex",
            [f"bil:{heading.partition(' ')[2]}"],
            "\n\n".join(blocks),
            None,
            status="in_force",
            start=None,
            end=None,
            note=None,
            heading=heading,
            context=None,
            leaf=True,
            own=False,
            top_kind="annex",
        )


def _transitional(blocks: list[str], emit: Callable[..., bool]) -> None:
    """Transitional provisions, grouped under the SFS number of the statute they come from."""
    groups: list[tuple[str, list[str]]] = []
    for b in blocks:
        head = _TRANSITIONAL_HEAD.match(b)
        if head:
            groups.append((f"{head.group(1)}:{head.group(2)}", []))
        elif _ELISION.match(b):
            continue  # an elision printed by the source: provisions not reproduced
        else:
            if not groups:
                groups.append(("", []))
            groups[-1][1].append(b)
    used: dict[str, int] = {}
    for sfs, group_blocks in groups:
        if not group_blocks:
            continue
        key = f"transitional-{sfs.replace(':', '-')}" if sfs else "transitional"
        used[key] = used.get(key, 0) + 1
        if used[key] > 1:
            key = f"{key}-{used[key]}"
        path = [f"tr:{sfs}"]
        intro: list[str] = []
        points: list[tuple[str, list[str]]] = []
        for b in group_blocks:
            m = _POINT.match(b)
            if m and re.fullmatch(r"\d+", m.group(1)) and b[len(m.group(1))] == ".":
                points.append((m.group(1), [m.group(2)]))
            elif points:
                points[-1][1].append(b)
            else:
                intro.append(b)

        def clean(lines: list[str]) -> tuple[str, str | None]:
            lines = list(lines)
            lines[-1], amended = _strip_amended_by(lines[-1])
            lines = [line for line in lines if line]
            return "\n\n".join(lines), (f"Wording from {amended}." if amended else None)

        if not points:
            body, note = clean(intro)
            emit(
                key,
                "transitional",
                path,
                body,
                None,
                status="in_force",
                start=None,
                end=None,
                note=note,
                heading="Övergångsbestämmelser",
                context=None,
                leaf=True,
                own=False,
                top_kind="transitional",
            )
            continue
        cleaned: list[tuple[str, str, str | None, bool]] = []  # number, text, note, repealed
        for n, lines in points:
            body, note = clean(lines)
            repealed = len(lines) == 1 and bool(_POINT_REPEAL.match(lines[0]))
            cleaned.append((n, body, f"{n}. {lines[0]}" if repealed else note, repealed))
        # The group's text: its introduction and the points in force, without editorial markers.
        composite = "\n\n".join(
            [*(["\n\n".join(intro)] if intro else []), *(f"{n}. {body}" for n, body, _, gone in cleaned if not gone)]
        )
        emit(
            key,
            "transitional",
            path,
            composite,
            None,
            status="in_force",
            start=None,
            end=None,
            note=None,
            heading="Övergångsbestämmelser",
            context=None,
            leaf=False,
            own=bool(intro),
            top_kind="transitional",
        )
        for n, body, note, repealed in cleaned:
            emit(
                f"{key}-point-{n}",
                "point",
                [*path, f"p:{n}"],
                body,
                key,
                status="repealed" if repealed else "in_force",
                start=None,
                end=None,
                note=note,
                heading=None,
                context=None,
                leaf=True,
                own=False,
                top_kind="transitional",
            )
