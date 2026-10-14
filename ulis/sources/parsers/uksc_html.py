"""Parser for UK Supreme Court judgments published as HTML on the Court's own website.

``https://www.supremecourt.uk/cases/judgments/uksc-<year>-<number>`` shows the judgment in full (156 pages for 126
judgments of 2021-2026 on 2026-10-14; earlier judgments are PDFs only). The page has stable element ids:
``case-title`` (h1), ``case-id`` ("UKSC/2022/0124"), ``appellant-N`` / ``respondent-N`` / ``intervener-N``,
``judgment-appealed-link``, ``judgment-date`` ("9 July 2024"), ``neutral-citation-number`` ("[2024] UKSC 23"),
``hearing-start-date`` / ``hearing-end-date``, the justices in ``justices``, then the judgment itself after the heading
``judgment-details`` up to the page footer.

* **Paragraphs** (exact text, whitespace collapsed): every ``p`` and ``li`` of the judgment in document order. A
  block that is entirely bold is a ``heading`` (so are h2-h4); a paragraph starting "47. " that continues the
  sequence 1, 2, 3 ... (or restarts at 1 and is followed by 2) is a numbered paragraph (``considerations``):
  ``num`` "47", the number removed from the text; the rest is ``text`` (counsel's names, the opening line of an
  opinion) or ``item``.
* **Links**: a hyperlink to ``legislation.gov.uk/id/...`` in the text is the Court's own statute reference and is
  recorded as it stands (``source_markup``; the pipeline resolves it to the Act or the section where that is
  ingested). A neutral citation of the Court in the text ("[2019] UKSC 41") is linked by pattern to
  ``/gb/uksc/2019/41``. Links to the National Archives and BAILII are ignored.
* **Work**: ``/gb/uksc/<year>/<n>`` from the neutral citation; the text is the Court's website version, not the
  signed judgment, and carries no version date other than the judgment date.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any

from ulis.sources.normalized import ParsedDocument, ParsedJudgmentMeta, ParsedParagraph, ParsedRef, ParseSkipped

PARSER_VERSION = "1.0.0"
SPACE = re.compile(r"[ \t\r\n\xa0  ]+")
NUMBERED = re.compile(r"^(\d{1,4})\.\s+(\S.*)$", re.S)
NEUTRAL = re.compile(r"^\[(\d{4})\] UKSC (\d{1,3})$")
OWN_COURT_CITATION = re.compile(r"\[(\d{4})\] UKSC (\d{1,3})(?!\d)")
CASE_ID = re.compile(r"^UKSC/(\d{4})/(\d{4})$")
LEGISLATION = re.compile(r"^https?://www\.legislation\.gov\.uk/id/[a-z]+/\d{4}/\d+(?:/[^?#\s]*)?$")
BREAK = "\x1f"
SKIP = {"script", "style", "svg", "button", "img", "noscript"}
BLOCKS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "table", "tr", "blockquote"}
PREFIX = {"heading": "h", "text": "t", "item": "li"}


class UkscParseError(ValueError):
    pass


class _Blocks(HTMLParser):
    """Blocks (kind, text, links) of the judgment; kind: heading (h2-h5 or all-bold), text (p), item (li)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.units: list[tuple[str, str, list[tuple[str, str]]]] = []
        self._kind = "text"
        self._buffer: list[str] = []
        self._bold = 0
        self._bold_chars = 0
        self._links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._link_text: list[str] = []
        self._skip = 0

    def _flush(self) -> None:
        text = SPACE.sub(" ", "".join(self._buffer))
        text = re.sub(f" ?{BREAK} ?", BREAK, text).strip(f" {BREAK}").replace(BREAK, "\n")
        if text:
            kind = self._kind
            if kind == "text" and self._bold_chars >= len(text.replace("\n", " ")) - 1 and len(text) < 250:
                kind = "heading"  # a block set entirely in bold
            self.units.append((kind, text, self._links))
        self._buffer, self._links, self._bold_chars = [], [], 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP:
            self._skip += 1
        elif self._skip:
            return
        elif tag == "br":
            self._buffer.append(BREAK)
        elif tag in BLOCKS:
            self._flush()
            self._kind = "heading" if tag in ("h1", "h2", "h3", "h4", "h5") else ("item" if tag == "li" else "text")
        elif tag in ("b", "strong"):
            self._bold += 1
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP:
            self._skip = max(0, self._skip - 1)
        elif self._skip:
            return
        elif tag in BLOCKS:
            self._flush()
            self._kind = "text"
        elif tag in ("b", "strong"):
            self._bold = max(0, self._bold - 1)
        elif tag == "a" and self._href is not None:
            self._links.append((self._href, SPACE.sub(" ", "".join(self._link_text)).strip()))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self._buffer.append(data)
        if self._bold:
            self._bold_chars += len(SPACE.sub(" ", data).strip())
        if self._href is not None:
            self._link_text.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _by_id(html: str, element: str, element_id: str) -> str | None:
    m = re.search(rf'<{element}\b[^>]*\bid="{re.escape(element_id)}"[^>]*>(.*?)</{element}>', html, re.S)
    if not m:
        return None
    text = re.sub(r"<[^>]+>", " ", m.group(1))
    return SPACE.sub(" ", text.replace("&amp;", "&").replace("&nbsp;", " ")).strip() or None


def _all(html: str, element: str, prefix: str) -> list[str]:
    ids = re.findall(rf'<{element}\b[^>]*\bid="({re.escape(prefix)}-\d+)"', html)
    return [t for i in ids if (t := _by_id(html, element, i))]


def _date(value: str | None, what: str) -> date:
    try:
        return datetime.strptime((value or "").strip(), "%d %B %Y").date()
    except ValueError as exc:
        raise UkscParseError(f"{what}: not a date like '9 July 2024': {value!r}") from exc


def parse_uksc(content: bytes) -> list[ParsedDocument]:
    try:
        html = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UkscParseError(f"not UTF-8: {exc}") from exc
    case_id = _by_id(html, "p", "case-id")
    m = CASE_ID.match(case_id or "")
    if not m:
        raise UkscParseError(f"no case id (UKSC/yyyy/nnnn) on the page: {case_id!r}")
    title = _by_id(html, "h1", "case-title")
    citation = _by_id(html, "p", "neutral-citation-number")
    nc = NEUTRAL.match(citation or "")
    if not title or not nc:
        raise UkscParseError(f"{case_id}: no case title or no neutral citation ({citation!r})")
    decision_date = _date(_by_id(html, "p", "judgment-date"), "judgment date")
    start = html.rfind('id="judgment-details"')
    if start < 0:
        raise UkscParseError(f"{case_id}: no judgment-details section")
    start = html.find(">", html.find("</h", start)) + 1  # after the heading "Judgment details" itself
    end = html.find("<footer", start)
    parser = _Blocks()
    parser.feed(html[start : end if end > 0 else len(html)])
    parser.close()
    units = parser.units
    if not units:
        raise ParseSkipped(f"{case_id}: the page has no judgment text")

    year, number = nc.group(1), nc.group(2)
    own = f"[{year}] UKSC {number}"
    paragraphs: list[ParsedParagraph] = []
    used: set[str] = set()
    expected = 1

    def add(part: str, num: str | None, text: str, refs: list[ParsedRef]) -> None:
        base = f"para-{num}" if num else f"{PREFIX[part]}-{len(paragraphs) + 1}"
        eid, n = base, 1
        while eid in used:
            n += 1
            eid = f"{base}-{n}"
        used.add(eid)
        paragraphs.append(ParsedParagraph(eid, num, part, text, len(paragraphs), refs))

    leading = [
        int(f.group(1)) if kind in ("text", "item") and (f := NUMBERED.match(text)) else None for kind, text, _ in units
    ]

    def restarts(i: int) -> bool:
        """A "1." after higher numbers starts a new run (another opinion) only if "2." follows before the number
        that continues the old run (a quoted clause "1." does not)."""
        nxt = next((n for n in leading[i + 1 :] if n is not None), None)
        return nxt == 2

    for i, (kind, text, links) in enumerate(units):
        num = None
        part = kind
        found = NUMBERED.match(text) if kind in ("text", "item") else None
        if found and (int(found.group(1)) == expected or (int(found.group(1)) == 1 and restarts(i))):
            num, text = found.group(1), found.group(2).strip()
            expected = int(num) + 1
            part = "considerations"
        refs: list[ParsedRef] = []
        seen: set[str] = set()
        for href, label in links:
            if LEGISLATION.match(href) and href not in seen:
                seen.add(href)
                refs.append(ParsedRef(href, label or href, "references"))
        for cited in OWN_COURT_CITATION.finditer(text):
            target = f"[{cited.group(1)}] UKSC {cited.group(2)}"
            href = f"/gb/uksc/{cited.group(1)}/{int(cited.group(2))}"
            if target != own and href not in seen:
                seen.add(href)
                refs.append(ParsedRef(href, target, "cites", extraction_method="pattern"))
        add(part, num, text, refs)
    if not any(p.part == "considerations" for p in paragraphs):
        raise UkscParseError(f"{case_id}: no numbered paragraphs found")

    work_uri = f"/gb/uksc/{year}/{int(number)}"
    ext: dict[str, Any] = {
        "case_id": case_id,
        "appellants": _all(html, "p", "appellant"),
        "respondents": _all(html, "p", "respondent"),
        "interveners": _all(html, "p", "intervener"),
        "judgment_appealed": _by_id(html, "a", "judgment-appealed-link"),
        "justices": re.findall(r'<a id="((?:Lord|Lady|Baroness|Sir|Dame)[^"]+)"', html),
        "hearing_start": _by_id(html, "p", "hearing-start-date"),
        "hearing_end": _by_id(html, "p", "hearing-end-date"),
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
            authoritative=False,  # the Court's website version; the signed judgment is the original
            text_origin="official",
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
