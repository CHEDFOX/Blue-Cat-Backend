"""Parser for CourtListener bulk case-law bundles (one bundle = one case: a cluster with its opinions).

The connector (``connectors/courtlistener_bulk.py``) yields one JSON bundle per cluster:

* ``court`` / ``docket`` / ``cluster`` - the rows of the publisher's tables, verbatim (only the fields used);
* ``citations`` - the cluster's reporter citations in the publisher's order (``volume reporter page`` + a type code);
* ``opinions`` - each opinion of the cluster (lead opinion, concurrence, dissent ...) with the verbatim value of the ONE
  text column the connector chose for it (the first non-empty of the manifest's ``text_columns``).

Text is exact source text. HTML and XML mark-up is removed (it is not law text), whitespace is collapsed inside a
paragraph,
and nothing else is changed: no spelling or OCR correction, no summary. What is left out on purpose and why:

* headnotes, summaries and the lists of counsel and dates that the Harvard XML places inside the opinion
  (``headnotes``, ``summary``, ``attorneys``, ``decisiondate``, ``otherdate``, ``docketnumber``): editorial or head
  matter,
  not the court's opinion; they stay in the stored bundle;
* the columns ``html_with_citations`` (CourtListener's own citation links), ``html_anon_2020`` (a third-party
database that
  carries editorial summaries) and ``xml_scan`` are never read.

Units. A US opinion has no numbered paragraphs; its pinpoint is the page of the reporter. Where the text carries the
reporter's page breaks (``<page-number>`` in the Harvard XML, ``star-pagination`` in the HTML of the other
collections) and
the first break fits one of the case's own citations, a unit is one printed page (``ext.unit_scheme`` = ``page``,
``ext.page_citation`` the citation whose pages they are, ``num`` the page). Otherwise (court-website text, scans
without page
marks, a page scheme that does not fit) a unit is a block of whole paragraphs of about 2,400 characters
(``unit_scheme`` = ``block``, ``num`` a running number that names nothing in the source, so no pinpoint is cited).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Any

from ulis.sources.normalized import (
    ParsedDocument,
    ParsedJudgmentMeta,
    ParsedParagraph,
    ParseSkipped,
)
from ulis.sources.parsers.courtlistener_courts import COURTS, CourtInfo

PARSER_VERSION = "1.0.0"
BUNDLE_SCHEMA = "ulis-us-courtlistener/1"
TEXT_COLUMNS_ALL = ("html_columbia", "html_lawbox", "xml_harvard", "html", "plain_text", "html_anon_2020", "xml_scan")
# CourtListener's own order of preference when it shows an opinion, without the third-party column html_anon_2020.
TEXT_COLUMNS_DEFAULT = ("html_columbia", "html_lawbox", "xml_harvard", "html", "plain_text")
OPINION_TYPE_ORDER = {
    "010combined": 10,
    "015unamimous": 15,
    "020lead": 20,
    "025plurality": 25,
    "030concurrence": 30,
    "035concurrenceinpart": 35,
    "040dissent": 40,
    "045dissentinpart": 45,
    "050addendum": 50,
    "060remittitur": 60,
    "070rehearing": 70,
    "080onthemerits": 80,
    "090onmotiontostrike": 90,
    "100trialcourt": 100,
}
OPINION_LABELS = {
    "010combined": "Opinion",
    "015unamimous": "Unanimous opinion",
    "020lead": "Lead opinion",
    "025plurality": "Plurality opinion",
    "030concurrence": "Concurring opinion",
    "035concurrenceinpart": "Opinion concurring in part",
    "040dissent": "Dissenting opinion",
    "045dissentinpart": "Opinion dissenting in part",
    "050addendum": "Addendum",
    "060remittitur": "Remittitur",
    "070rehearing": "Opinion on rehearing",
    "080onthemerits": "Opinion on the merits",
    "090onmotiontostrike": "Opinion on motion to strike",
    "100trialcourt": "Trial court opinion",
}
# CourtListener's citation type codes (cl/search/models.py, Citation): 1 federal, 2 state, 3 state regional, 4
# specialty,
# 5 early Supreme Court, 6 Lexis, 7 Westlaw, 8 neutral.
NEUTRAL, FEDERAL, STATE, REGIONAL, SPECIALTY, SCOTUS_EARLY, LEXIS, WEST = 8, 1, 2, 3, 4, 5, 6, 7
CITATION_PRIORITY = {NEUTRAL: 0, FEDERAL: 1, STATE: 2, REGIONAL: 3, SPECIALTY: 4, SCOTUS_EARLY: 5, WEST: 6, LEXIS: 7}

WORK_PREFIX = "/us/case/cl"
US_REPORTS = re.compile(r"\d+ U\.S\. \d+")
TARGET_UNIT = 2400  # characters: a block unit closes once it has reached this
MIN_UNIT = 700
MAX_UNIT = 8000  # a single paragraph longer than this is cut at sentence ends
PART_MAX = 32  # width of judgment_paragraphs.part
NUM_MAX = 20  # width of judgment_paragraphs.num
PAGE_FIT = 60  # the first page break of a case must lie within this many pages after a citation's first page

SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "head",
        "title",
        "img",
        "headnotes",
        "summary",
        "attorneys",
        "decisiondate",
        "otherdate",
        "docketnumber",
        "footnotemark",
        "nav",
    }
)
BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "ul",
        "ol",
        "tr",
        "table",
        "center",
        "pre",
        "footnote",
        "opinion",
        "author",
        "judges",
        "section",
        "dd",
        "dt",
        "hr",
        "partyblock",
        "article",
    }
)
VOID_TAGS = frozenset({"img", "br", "hr", "meta", "link", "input"})


def bundle_bytes(bundle: dict[str, Any]) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def court_for(court_id: str) -> CourtInfo | None:
    return COURTS.get(court_id)


def work_uri_for(cluster_id: int) -> str:
    return f"{WORK_PREFIX}/{cluster_id}"


# --------------------------------------------------------------------------- text extraction


@dataclass
class Atom:
    kind: str  # "p" paragraph (or fragment) of text, "page" a page break of the reporter
    value: str


@dataclass
class _Extractor(HTMLParser):
    """Turns the HTML/XML of one opinion into paragraphs and page breaks."""

    atoms: list[Atom] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._buf: list[str] = []
        self._skip: dict[str, int] = {}
        self._star_depth = 0
        self._star_label = ""
        self._star_text: list[str] = []
        self._star_index = "1"
        self._pre = 0
        self._footnote_label: str | None = None

    # ---- helpers
    def _skipping(self) -> bool:
        return any(n > 0 for n in self._skip.values())

    def flush(self) -> None:
        raw = "".join(self._buf)
        self._buf.clear()
        parts = re.split(r"\n\s*\n", raw) if self._pre else [raw]
        for part in parts:
            text = re.sub(r"\s+", " ", part).strip()
            if text:
                if self._footnote_label is not None:
                    label, self._footnote_label = self._footnote_label, None
                    text = f"{label}{'' if text[0] in '.,;:)] ' else ' '}{text}"
                self.atoms.append(Atom("p", text))

    # ---- parser callbacks
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if self._star_depth:
            if tag == "span":
                self._star_depth += 1
            return
        if tag == "page-number":
            self.flush()
            self._star_depth = 1
            self._star_label = a.get("label", "")
            self._star_index = a.get("citation-index", "1")
            self._star_text = []
            self._skip["page-number"] = 1
            return
        if tag == "span" and "star-pagination" in a.get("class", "").split():
            self.flush()
            self._star_depth = 1
            self._star_label = a.get("label", "")
            self._star_index = a.get("citation-index", "1")
            self._star_text = []
            return
        if tag in SKIP_TAGS:
            if tag not in VOID_TAGS:
                self._skip[tag] = self._skip.get(tag, 0) + 1
            return
        if tag == "br":
            self._buf.append(" " if not self._pre else "\n")
            return
        if tag in BLOCK_TAGS:
            self.flush()
            if tag == "pre":
                self._pre += 1
            if tag == "footnote":
                self._footnote_label = a.get("label") or None
        elif tag in ("td", "th"):
            self._buf.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._star_depth:
            if tag == "page-number" or (tag == "span" and self._star_depth == 1):
                self._star_depth = 0
                self._skip.pop("page-number", None)
                label = self._star_label or "".join(self._star_text)
                m = re.search(r"\d+", label)
                if (
                    self._star_index == "1"
                    and m
                    and len(m.group(0)) <= 6
                    and re.fullmatch(r"\*?\s*\d+\s*", label.strip() or "x")
                ):
                    self.atoms.append(Atom("page", m.group(0)))
            elif tag == "span":
                self._star_depth -= 1
            return
        if tag in SKIP_TAGS:
            if self._skip.get(tag, 0) > 0:
                self._skip[tag] -= 1
            return
        if tag in BLOCK_TAGS:
            self.flush()
            if tag == "pre" and self._pre:
                self._pre -= 1
            if tag == "footnote":
                self._footnote_label = None

    def handle_data(self, data: str) -> None:
        if self._star_depth:
            self._star_text.append(data)
        elif not self._skipping():
            self._buf.append(data)


def extract_html(markup: str) -> list[Atom]:
    parser = _Extractor()
    parser.feed(markup)
    parser.close()
    parser.flush()
    return parser.atoms


def extract_plain(text: str) -> list[Atom]:
    atoms = []
    for part in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\f", "\n\n")):
        collapsed = re.sub(r"\s+", " ", part).strip()
        if collapsed:
            atoms.append(Atom("p", collapsed))
    return atoms


def extract(column: str, value: str) -> list[Atom]:
    return extract_plain(value) if column == "plain_text" else extract_html(value)


# --------------------------------------------------------------------------- units


def _split_long(text: str, limit: int = MAX_UNIT) -> list[str]:
    """Cut a very long paragraph after sentence ends (or at a space) so that no unit is huge; the pieces re-join
    exactly."""
    out = []
    while len(text) > limit:
        cut = max(text.rfind(". ", 0, limit), text.rfind("; ", 0, limit))
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < 1:
            cut = limit - 1
        out.append(text[: cut + 1].rstrip())
        text = text[cut + 1 :].lstrip()
    if text:
        out.append(text)
    return out


def pack(paragraphs: list[str]) -> list[str]:
    """Blocks of whole paragraphs of about TARGET_UNIT characters."""
    pieces: list[str] = []
    for p in paragraphs:
        pieces.extend(_split_long(p) if len(p) > MAX_UNIT else [p])
    units: list[str] = []
    current: list[str] = []
    size = 0
    for piece in pieces:
        if current and size >= MIN_UNIT and size + len(piece) > TARGET_UNIT:
            units.append("\n\n".join(current))
            current, size = [], 0
        current.append(piece)
        size += len(piece)
    if current:
        units.append("\n\n".join(current))
    return units


def page_units(atoms: list[Atom]) -> list[tuple[str | None, str]]:
    """(page label or None, text) per printed page; text before the first break is on the page before that break."""
    first = next((int(a.value) for a in atoms if a.kind == "page"), None)
    if first is None:  # an opinion without page breaks in a case that has them elsewhere: blocks without a page
        return [(None, text) for text in pack([a.value for a in atoms if a.kind == "p"])]
    page: str | None = str(first - 1) if first >= 2 else None
    units: list[tuple[str | None, str]] = []
    current: list[str] = []

    def close() -> None:
        if current:
            units.append((page, "\n\n".join(current)))
            current.clear()

    for atom in atoms:
        if atom.kind == "page":
            close()
            page = atom.value
        else:
            current.append(atom.value)
    close()
    out: list[tuple[str | None, str]] = []
    for label, text in units:
        if len(text) > 2 * MAX_UNIT:  # an enormous page (a table, a footnote): same page label, several units
            out.extend((label, piece) for piece in pack([text]))
        else:
            out.append((label, text))
    return out


def first_break(atoms: list[Atom]) -> int | None:
    return next((int(a.value) for a in atoms if a.kind == "page"), None)


# --------------------------------------------------------------------------- citations


@dataclass(frozen=True)
class Cite:
    text: str
    type: int
    page: int | None


def clean_citations(raw: list[dict[str, Any]]) -> list[Cite]:
    seen: set[str] = set()
    out: list[Cite] = []
    for c in raw:
        reporter, page = str(c.get("reporter") or "").strip(), str(c.get("page") or "").strip()
        volume = str(c.get("volume") or "").strip()
        if not reporter or not page:
            continue
        text = " ".join(p for p in (volume, reporter, page) if p)
        if text in seen:
            continue
        seen.add(text)
        kind = int(c.get("type") or 0)
        # The publisher files some database citations under the type of the court's own reporters (for example
        # "N.Y. App. Div. LEXIS"); a citation of a commercial database is never a reporter citation.
        if "LEXIS" in reporter.upper():
            kind = LEXIS
        elif reporter.upper() in ("WL", "WL (BNA)"):
            kind = WEST
        out.append(Cite(text, kind, int(page) if page.isdigit() else None))
    return out


def primary_citation(cites: list[Cite]) -> Cite | None:
    """By type, then the official United States Reports before the other federal reporters, then publisher order."""
    ranked = sorted(
        enumerate(cites),
        key=lambda ic: (CITATION_PRIORITY.get(ic[1].type, 9), not US_REPORTS.fullmatch(ic[1].text), ic[0]),
    )
    return ranked[0][1] if ranked else None


# --------------------------------------------------------------------------- parse


def _date(value: str, what: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ValueError(f"{what}: not a date: {value!r}") from exc


def _part(label: str, author: str) -> str:
    author = author.strip()
    text = f"{label} ({author})" if author else label
    return text if len(text) <= PART_MAX else label


def parse(raw: bytes) -> list[ParsedDocument]:
    bundle = json.loads(raw)
    if bundle.get("schema") != BUNDLE_SCHEMA:
        raise ValueError(f"not a {BUNDLE_SCHEMA} bundle")
    court = bundle["court"]
    info = court_for(court["id"])
    if info is None:
        raise ValueError(f"court {court['id']!r} is not modelled")
    cluster, docket = bundle["cluster"], bundle["docket"]
    cluster_id = int(cluster["id"])
    decided = _date(cluster["date_filed"], "date_filed")
    name = (cluster.get("case_name") or cluster.get("case_name_full") or docket.get("case_name") or "").strip()
    if not name:
        raise ParseSkipped(f"cluster {cluster_id}: the source gives no case name")
    docket_number = (docket.get("docket_number") or "").strip() or None
    cites = clean_citations(bundle.get("citations") or [])

    # --- text of every opinion
    parts: list[tuple[str, list[Atom]]] = []  # (part label, atoms)
    columns_used: list[str] = []
    long_authors: list[str] = []  # authors whose name did not fit the width of the part label
    ocr = False
    syllabus = (cluster.get("syllabus") or "").strip()
    if syllabus:
        atoms = extract_html(syllabus)
        if atoms:
            parts.append(("Syllabus", atoms))
    for op in bundle["opinions"]:
        column = op["text_column"]
        if column not in TEXT_COLUMNS_ALL or column in ("html_anon_2020", "xml_scan"):
            raise ValueError(f"opinion {op['id']}: text column {column!r} is not accepted")
        atoms = extract(column, op["text"])
        if not any(a.kind == "p" for a in atoms):
            continue
        if column not in columns_used:
            columns_used.append(column)
        ocr = ocr or bool(op.get("extracted_by_ocr"))
        label = OPINION_LABELS.get(op["type"], "Opinion")
        part = _part(label, op.get("author_str") or "")
        if (op.get("author_str") or "").strip() and part == label:
            long_authors.append(f"{label}: {op['author_str'].strip()}")
        parts.append((part, atoms))
    if not any(label != "Syllabus" for label, _ in parts):
        raise ParseSkipped(f"cluster {cluster_id}: no opinion text")

    # --- page scheme: only if the first page break fits one of the case's citations
    page_cite: Cite | None = None
    breaks = [first_break(atoms) for label, atoms in parts if label != "Syllabus"]
    first = next((b for b in breaks if b is not None), None)
    if first is not None:
        for cite in cites:
            if (
                cite.page is not None
                and cite.type not in (LEXIS, WEST, NEUTRAL)
                and cite.page < first <= cite.page + PAGE_FIT
            ):
                page_cite = cite
                break
    paragraphs: list[ParsedParagraph] = []
    block_no = 0
    for label, atoms in parts:
        if page_cite is not None and label != "Syllabus":
            for page, text in page_units(atoms):
                paragraphs.append(
                    ParsedParagraph(
                        eid=f"u{len(paragraphs) + 1}",
                        num=page,
                        part=label,
                        text=text,
                        sort_order=len(paragraphs) + 1,
                        refs=[],
                    )
                )
        else:
            for text in pack([a.value for a in atoms if a.kind == "p"]):
                block_no += 1
                paragraphs.append(
                    ParsedParagraph(
                        eid=f"u{len(paragraphs) + 1}",
                        num=str(block_no),
                        part=label,
                        text=text,
                        sort_order=len(paragraphs) + 1,
                        refs=[],
                    )
                )
    scheme = "page" if page_cite is not None else "block"
    if scheme == "page":
        # one unit scheme per case: renumber the syllabus (not paged) after the pages so that nums never collide
        for p in paragraphs:
            if p.part == "Syllabus":
                block_no += 1
                p.num = f"syllabus {block_no}"

    primary = page_cite or primary_citation(cites)
    kind = "docket"
    if primary is not None:
        kind = "neutral" if primary.type == NEUTRAL else "database" if primary.type in (LEXIS, WEST) else "reporter"
    work_uri = work_uri_for(cluster_id)
    opinion_ids = [int(o["id"]) for o in bundle["opinions"]]
    continuation = bool(bundle.get("continuation"))
    expression = f"{work_uri}/eng@{decided.isoformat()}" + (f"+{opinion_ids[0]}" if continuation else "")
    ext: dict[str, Any] = {
        "courtlistener_cluster": cluster_id,
        "courtlistener_docket": int(docket["id"]),
        "courtlistener_court": court["id"],
        "court_name": info.name,
        "court_citation": (court.get("citation_string") or info.citation or "").strip() or None,
        "citations": [c.text for c in cites] or None,
        "citation_kind": kind,
        "unit_scheme": scheme,
        "page_citation": page_cite.text if page_cite else None,
        "text_columns": columns_used,
        "source_codes": cluster.get("source") or None,
        "precedential_status": cluster.get("precedential_status") or None,
        "date_filed_is_approximate": True if cluster.get("date_filed_is_approximate") else None,
        "judges": (cluster.get("judges") or "").strip() or None,
        "authors": long_authors or None,
        "disposition": (cluster.get("disposition") or "").strip() or None,
        "scdb_id": cluster.get("scdb_id") or None,
        "harvard_path": cluster.get("filepath_json_harvard") or None,
        "opinion_ids": opinion_ids,
        "ocr": True if ocr else None,
        "continuation": True if continuation else None,
        "download_urls": sorted({o["download_url"] for o in bundle["opinions"] if o.get("download_url")}) or None,
    }
    ext = {k: v for k, v in ext.items() if v is not None}
    return [
        ParsedDocument(
            doc_kind="judgment",
            instrument_type="judgment",
            jurisdiction_id="US",
            work_uri=work_uri,
            expression_uri=expression,
            work_title=name,
            title=name,
            short_title=None,
            number=primary.text if primary else docket_number,
            work_date=decided,
            language="en",
            authoritative=False,
            text_origin="unofficial_compilation",
            consolidation_kind="unknown",
            version_date=decided,
            currency_date=None,
            extent=["US"],
            ext_module="us",
            ext=ext,
            judgment=ParsedJudgmentMeta(
                court_id=info.ulis_id,
                case_name=name,
                neutral_citation=primary.text if primary else None,
                decision_date=decided,
                docket=docket_number,
            ),
            paragraphs=paragraphs,
        )
    ]
