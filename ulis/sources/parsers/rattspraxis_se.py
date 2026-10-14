"""Parser for the Swedish courts' "Sök rättspraxis" open data (Domstolsverket's PUH API, JSON).

Domstolsverket publishes case law of the higher courts as open data (``https://rattspraxis.etjanst.domstol.se/api/v1``,
OpenAPI file ``/openapi/puh-openapi.yaml``; licence CC0 in Sveriges dataportal). One publication (``PubliceringDTO``) is
one JSON object. This parser reads the publications of form ``REFERAT``: the official case reports (rättsfallsreferat,
e.g. ``NJA 2026 s. 347``, ``HFD 2026 ref. 35``), whose text (``innehall``) is HTML.

* **Metadata**: court code, decision date (``avgorandedatum``), case numbers (``malNummerLista``), the report numbers
  (``referatNummerLista``; the citation is the ``NJA ... s. ...`` form for NJA, else the first), the nickname
  (``benamning``), the type (``typ``: PREJUDIKAT, VAGLEDANDE_MEN_EJ_PREJUDICERANDE ...), keywords, legal areas.
* **Paragraphs** (exact text, whitespace collapsed, soft hyphens removed): ``headnote`` = the summary
  (``sammanfattning``); the body in document order: ``heading`` (h1-h4), ``text`` (p), ``item`` (li) and, for the
  court's own numbered reasons, ``considerations``. Numbering is read from the text: a sequence "1. ", "2. ", ... of
  at least five consecutive paragraph numbers covering three quarters of the text between them; where a report has
  several such sequences (a lower court's numbered reasons) only the longest, latest one is the court's reasoning and
  carries ``num``. The number is
  stored in ``num`` and removed from the text. Reports without such a sequence have no paragraph numbers.
* **Links**: ``lagrumLista[].sfsNummer`` is the publisher's structured statute reference; it links (``source_markup``)
  to the work ``/se/sfs/{year}/{number}`` from the headnote paragraph. Cited cases (``hanvisadePubliceringarLista``)
  are free text and are kept in ``ext`` only.

The stored source document is the publication's JSON object, serialised canonically (sorted keys, UTF-8) by the
connector from the page the API returned.
"""

from __future__ import annotations

import json
import re
from datetime import date
from html.parser import HTMLParser
from typing import Any

from ulis.sources.normalized import ParsedDocument, ParsedJudgmentMeta, ParsedParagraph, ParsedRef, ParseSkipped

PARSER_VERSION = "1.0.0"
BASE = "https://rattspraxis.etjanst.domstol.se"
SPACE = re.compile(r"[ \t\r\n\xa0  ]+")
SOFT_HYPHEN = "­"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SFS = re.compile(r"^(1[89]\d{2}|20\d{2}):(\d{1,4})$")
NUMBERED = re.compile(r"^(\d{1,3})\.\s+(\S.*)$", re.S)
NJA_PAGE = re.compile(r"^NJA \d{4} s\. \d+")
BREAK = "\x1f"

# The API's court codes (GET /api/v1/domstolar) -> ULIS court ids (config/jurisdictions/se.yaml).
COURTS: dict[str, str] = {
    "HDO": "SE-HD",
    "HFD": "SE-HFD",
    "REGR": "SE-HFD",  # Regeringsrätten, until 2010 the Supreme Administrative Court
    "HSV": "SE-HOVR",
    "HGO": "SE-HOVR",
    "HVS": "SE-HOVR",
    "HON": "SE-HOVR",
    "HNN": "SE-HOVR",
    "HSB": "SE-HOVR",
    "HYOD": "SE-HOVR",  # Svea hovrätts hyresrättsliga avgöranden
    "KST": "SE-KR",
    "KGG": "SE-KR",
    "KSU": "SE-KR",
    "KJO": "SE-KR",
    "ADO": "SE-AD",
    "MIOD": "SE-MIOD",
    "MMOD": "SE-MMOD",
    "MOD": "SE-MMOD",  # Miljööverdomstolen, until 2011
    "PMOD": "SE-PMOD",
    "MDO": "SE-MD",
}
PREFIX = {"headnote": "hn", "heading": "h", "text": "p", "item": "li", "considerations": "s"}


class RattspraxisParseError(ValueError):
    pass


class _Blocks(HTMLParser):
    """Block units (kind, text) of a report: h1-h4 -> heading, p -> text, li -> item; <br> is a line break."""

    HEADINGS = {"h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.units: list[tuple[str, str]] = []
        self._kind = "text"
        self._buffer: list[str] = []

    def _flush(self) -> None:
        text = SPACE.sub(" ", "".join(self._buffer)).replace(SOFT_HYPHEN, "")
        text = re.sub(f" ?{BREAK} ?", BREAK, text).strip(f" {BREAK}").replace(BREAK, "\n")
        if text:
            self.units.append((self._kind, text))
        self._buffer = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self._buffer.append(BREAK)
        elif tag in self.HEADINGS or tag in ("p", "li", "table", "tr"):
            self._flush()
            self._kind = "heading" if tag in self.HEADINGS else ("item" if tag == "li" else "text")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.HEADINGS or tag in ("p", "li", "table", "tr"):
            self._flush()
            self._kind = "text"

    def handle_data(self, data: str) -> None:
        self._buffer.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _blocks(html: str) -> list[tuple[str, str]]:
    parser = _Blocks()
    parser.feed(html)
    parser.close()
    return parser.units


def _norm(text: str) -> str:
    return SPACE.sub(" ", text.replace(SOFT_HYPHEN, "")).strip()


def _reasons(units: list[tuple[str, str]]) -> tuple[int, int] | None:
    """Index range (first, last) of the court's numbered reasons: the longest, latest run of consecutive paragraph
    numbers 1, 2, 3 ... that has at least five numbers and covers at least three quarters of the text units between its
    first and last (an enumeration inside a text, or a list of claims, does not qualify)."""
    best: tuple[int, int, int] | None = None  # (length, first, last)
    expected, first, last = 0, -1, -1

    def close() -> None:
        nonlocal best
        length = expected - 1
        if first < 0 or length < 5:
            return
        body = sum(1 for kind, _ in units[first : last + 1] if kind in ("text", "item"))
        if length >= 0.75 * body and (best is None or length >= best[0]):
            best = (length, first, last)

    for i, (kind, text) in enumerate(units):
        m = NUMBERED.match(text) if kind in ("text", "item") else None
        if m and int(m.group(1)) == 1:
            close()
            expected, first, last = 2, i, i
        elif m and int(m.group(1)) == expected and first >= 0:
            expected, last = expected + 1, i
    close()
    return (best[1], best[2]) if best else None


def _date(value: Any, what: str) -> date:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError as exc:
        raise RattspraxisParseError(f"{what}: not an ISO date: {value!r}") from exc


def _strings(values: Any) -> list[str]:
    return [s for v in (values or []) if isinstance(v, str) and (s := _norm(v))]


def parse_rattspraxis(content: bytes) -> list[ParsedDocument]:
    try:
        item = json.loads(content)
    except ValueError as exc:
        raise RattspraxisParseError(f"not JSON: {exc}") from exc
    if not isinstance(item, dict):
        raise RattspraxisParseError("not a publication object")
    pid = str(item.get("id") or "")
    if not UUID.match(pid):
        raise RattspraxisParseError(f"unexpected publication id {pid!r}")
    if item.get("publiceringsform") != "REFERAT":
        raise RattspraxisParseError(f"{pid}: publication form {item.get('publiceringsform')!r} is not supported")
    court = item.get("domstol") or {}
    code = str(court.get("domstolKod") or "")
    court_id = COURTS.get(code)
    if court_id is None:
        raise RattspraxisParseError(f"{pid}: court code {code!r} is not mapped to a ULIS court")
    decision_date = _date(item.get("avgorandedatum"), "avgorandedatum")
    published = _date(item.get("publiceringstid"), "publiceringstid")

    references = _strings(item.get("referatNummerLista"))
    reference = next((r for r in references if NJA_PAGE.match(r)), references[0] if references else None)
    numbers = _strings(item.get("malNummerLista"))
    nickname = _norm(str(item.get("benamning") or "")) or None
    headnote = _norm(str(item.get("sammanfattning") or ""))

    units = _blocks(str(item.get("innehall") or ""))
    if not units and not headnote:
        raise ParseSkipped(f"{pid}: the report has no text")
    span = _reasons(units)

    statute_refs: dict[str, ParsedRef] = {}
    for law in item.get("lagrumLista") or []:
        sfs = str((law or {}).get("sfsNummer") or "")
        m = SFS.match(sfs)
        if m:
            href = f"/se/sfs/{m.group(1)}/{m.group(2)}"
            statute_refs.setdefault(href, ParsedRef(href, _norm(str(law.get("referens") or sfs)), "references"))

    paragraphs: list[ParsedParagraph] = []
    counters: dict[str, int] = {}

    def add(part: str, num: str | None, text: str, refs: list[ParsedRef]) -> None:
        counters[part] = counters.get(part, 0) + 1
        paragraphs.append(ParsedParagraph(f"{PREFIX[part]}-{counters[part]}", num, part, text, len(paragraphs), refs))

    if headnote:
        add("headnote", None, headnote, [])
    for i, (kind, text) in enumerate(units):
        inside = span is not None and span[0] <= i <= span[1]
        num = None
        if inside and kind in ("text", "item") and (m := NUMBERED.match(text)):
            num, text = m.group(1), m.group(2).strip()
        part = kind if kind == "heading" or not inside else "considerations"
        add(part, num, text, [])
    paragraphs[0].refs = list(statute_refs.values())  # the publisher's statute references, on the first paragraph

    case_name = nickname or reference or (numbers[0] if numbers else pid)
    work_uri = f"/se/rattspraxis/{pid}"
    ext = {
        "publication_id": pid,
        "group_id": str(item.get("gruppKorrelationsnummer") or "") or None,
        "court_code": code,
        "court_name": _norm(str(court.get("domstolNamn") or "")) or None,
        "type": str(item.get("typ") or "") or None,
        "references": references,
        "ecli": str(item.get("ecliNummer") or "") or None,
        "case_numbers": numbers,
        "labour_court_number": str(item.get("arbetsdomstolenDomsnummer") or "") or None,
        "nickname": nickname,
        "keywords": _strings(item.get("nyckelordLista")),
        "legal_areas": _strings(item.get("rattsomradeLista")),
        "statutes": [
            {"sfs": str(x.get("sfsNummer") or "") or None, "reference": _norm(str(x.get("referens") or ""))}
            for x in item.get("lagrumLista") or []
            if isinstance(x, dict) and x.get("referens")
        ],
        "cited_cases": [
            _norm(str(x.get("fritext") or ""))
            for x in item.get("hanvisadePubliceringarLista") or []
            if isinstance(x, dict) and x.get("fritext")
        ],
        "literature": [
            {"title": _norm(str(x.get("titel") or "")), "authors": _norm(str(x.get("forfattare") or ""))}
            for x in item.get("litteraturLista") or []
            if isinstance(x, dict)
        ],
        "preparatory_works": _strings(item.get("forarbeteLista")),
        "published": str(item.get("publiceringstid") or "") or None,
    }
    return [
        ParsedDocument(
            doc_kind="judgment",
            instrument_type="judgment",
            jurisdiction_id="SE",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/swe@{published.isoformat()}",
            work_title=case_name,
            title=case_name,
            short_title=None,
            number=numbers[0] if numbers else None,
            work_date=decision_date,
            language="sv",
            authoritative=False,  # the electronic report; the printed NJA / the court's own judgment is the original
            text_origin="official",
            consolidation_kind="unknown",
            version_date=published,
            currency_date=None,
            extent=["SE"],
            ext_module="se",
            ext={k: v for k, v in ext.items() if v not in (None, [], "")},
            judgment=ParsedJudgmentMeta(
                court_id=court_id,
                case_name=case_name,
                neutral_citation=reference,
                decision_date=decision_date,
                docket="; ".join(numbers) or None,
            ),
            paragraphs=paragraphs,
        )
    ]
