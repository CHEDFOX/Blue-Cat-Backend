"""Parser for tribunal decisions published on GOV.UK (Employment Appeal Tribunal, Upper Tribunal Administrative
Appeals Chamber).

Input (one raw document per decision, made by ``connectors/govuk_decisions.py``): the JSON that GOV.UK's Content API
(``https://www.gov.uk/api/content/<path>``) returns for the decision, exactly as received. It holds the title (with the
neutral citation), the decision date (``details.metadata.tribunal_decision_decision_date``), the list of attachments
(the
decision as a PDF on assets.publishing.service.gov.uk) and ``details.metadata.hidden_indexable_content``: the text of
that
PDF as GOV.UK extracts it for its own search index. That text is the text stored here.

* The text is split into blocks at blank lines. A block that starts "12." (or "12.<tab>") and continues the sequence
  1, 2, 3 ... (or restarts at 1 and is followed by 2) is a numbered paragraph (``considerations``) with its number; a
  lettered or numbered sub-item ("a.", "(ii)") is an ``item``; a short block with no final full stop is a ``heading``;
  anything else is ``text``. Everything before paragraph 1 (the cover, the summary, the order) stays as unnumbered
  units.
* The title gives the neutral citation: ``[2026] EAT 152`` (work ``/gb/eat/2026/152``) or ``[2026] UKUT 349 (AAC)``
  (work ``/gb/ukut/2026/349``; the Upper Tribunal numbers its decisions in one series for all chambers). A decision
  whose
  title has no neutral citation is a work ``/gb/<court>/<slug>`` of its page name, with no neutral citation.
* The case number is read from the first lines of the text ("Case No: EA-2024-001184-DXA", "Appeal No.
  UA-2025-001549-USTA");
  where there is none the page name stands in. A decision whose PDF has no text layer (``hidden_indexable_content``
  empty)
  is skipped: ULIS does no OCR and does not fetch the PDF.

Citations of other decisions are linked by pattern (``[2019] UKSC 25``, ``[2020] UKPC 29``) only for the Supreme Court
and the
Privy Council, whose judgments are ingested. Judicial treatment is not in the source and is never inferred. The text is
GOV.UK's extraction of the tribunal's PDF, not the signed decision.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from ulis.sources.normalized import ParsedDocument, ParsedJudgmentMeta, ParsedParagraph, ParsedRef, ParseSkipped

PARSER_VERSION = "1.0.0"
# document type -> (court id, work path segment, citation series)
COURTS = {
    "employment_appeal_tribunal_decision": ("GB-EAT", "eat", "EAT"),
    "utaac_decision": ("GB-UTAAC", "ukut", "UKUT"),
}
CITATION = re.compile(r"\[(\d{4})\]\s+(EAT|UKUT)\s+0*(\d{1,4})(?:\s+\(([A-Z]{2,4})\))?")
CASE_NO = re.compile(r"(?im)^(?:Case|Appeal)\s*(?:No\.?|Number)[:.\s]+([A-Z0-9][A-Za-z0-9/._-]{4,40})\s*$")
NUMBERED = re.compile(r"^(\d{1,4})\.(?:\t|\s+)(\S.*)$", re.S)
NUMBERED_PLAIN = re.compile(
    r"^(\d{1,4})\s+(\S.*)$"
)  # older decisions number their paragraphs "12 Text" without a full stop
SUB_ITEM = re.compile(r"^(?:[a-z]{1,2}\.|\([a-z]{1,3}\)|\([ivx]{1,5}\)|[ivx]{1,5}\.)\s+\S")
OWN_COURT_CITATION = re.compile(r"\[(\d{4})\] (UKSC|UKPC) (\d{1,3})(?!\d)")
SPACE = re.compile(r"[ \t\r\n\xa0  ]+")
PREFIX = {"heading": "h", "text": "t", "item": "li"}
MIN_TEXT_CHARS = 300


class GovUkDecisionParseError(ValueError):
    pass


def _clean(block: str) -> str:
    return SPACE.sub(" ", block).strip()


def _is_heading(text: str) -> bool:
    return 0 < len(text) <= 110 and not text.endswith((".", ",", ";", ":", "?")) and not text[0].isdigit()


def parse_govuk_decision(content: bytes) -> list[ParsedDocument]:
    try:
        data: dict[str, Any] = json.loads(content.decode("utf-8"))
        title = data["title"].strip()
        doc_type = str(data["document_type"])
        details = data["details"]
        base_path = str(data["base_path"])
        meta = details["metadata"]
        decided = date.fromisoformat(str(meta["tribunal_decision_decision_date"])[:10])
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, KeyError, TypeError, ValueError) as exc:
        raise GovUkDecisionParseError(f"not a GOV.UK tribunal decision document: {exc!r}") from exc
    if doc_type not in COURTS:
        raise GovUkDecisionParseError(f"unsupported document type {doc_type!r}")
    court_id, court_path, series = COURTS[doc_type]
    slug = base_path.rstrip("/").rsplit("/", 1)[-1]
    text = str(meta.get("hidden_indexable_content") or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(text.strip()) < MIN_TEXT_CHARS:
        raise ParseSkipped(f"{base_path}: the decision has no text in the GOV.UK document (a scan); ULIS does no OCR")

    found = CITATION.search(title)
    neutral = None
    work_uri = f"/gb/{court_path}/{slug}"
    if found and found.group(2) == series:
        neutral = f"[{found.group(1)}] {series} {int(found.group(3))}" + (
            f" ({found.group(4)})" if found.group(4) else ""
        )
        work_uri = f"/gb/{court_path}/{found.group(1)}/{int(found.group(3))}"
    name = re.sub(r"\s+", " ", CITATION.sub("", title)).strip(" :-\u2013") if neutral else title
    case_no = CASE_NO.search(text[:3000])
    case_id = case_no.group(1) if case_no else slug

    units: list[tuple[str, str | None, str]] = []
    expected = 1
    lines: list[str] = []  # the lines of the block (or numbered paragraph) being collected
    number: str | None = None  # the number of the paragraph being collected, None for an unnumbered block

    def flush() -> None:
        nonlocal lines, number
        body = _clean(" ".join(lines))
        if body:
            if number is not None:
                units.append(("considerations", number, body))
            elif SUB_ITEM.match(body) and expected > 1:
                units.append(("item", None, body))
            elif len(lines) == 1 and _is_heading(body):
                units.append(("heading", None, body))
            else:
                units.append(("text", None, body))
        lines, number = [], None

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            flush()
            continue
        found_number = NUMBERED.match(line)
        plain = NUMBERED_PLAIN.match(line) if found_number is None else None
        # Without a full stop a number is a paragraph number only at the start of a block of text: the block collected
        # so far is empty, a finished sentence or a heading (a line starting "5 points" inside a sentence is not).
        if plain is not None and (
            not lines
            or lines[-1].endswith((".", "?", "!", ":", ";", "\u201d", '"', "\u2019", ")"))
            or _is_heading(lines[-1])
        ):
            found_number = plain
        # The numbers must go 1, 2, 3 ...; a gap of one or two is tolerated (a number lost in the extraction); a lower
        # number is a quoted passage and stays in its paragraph.
        if found_number and expected <= int(found_number.group(1)) <= expected + 2:
            if (
                number is not None
                and len(lines) >= 2
                and _is_heading(lines[-1])
                and lines[-2].endswith((".", "?", "!", "\u201d"))
            ):
                heading = lines.pop()  # a heading directly above this paragraph, not separated by a blank line
                flush()
                units.append(("heading", None, heading))
            else:
                flush()
            number = found_number.group(1)
            expected = int(number) + 1
            lines = [found_number.group(2)]
            continue
        lines.append(line)
    flush()
    if not units:  # a short decision without numbered paragraphs is kept as unnumbered text
        raise GovUkDecisionParseError(f"{base_path}: no text blocks found")

    paragraphs: list[ParsedParagraph] = []
    used: set[str] = set()
    for kind, num, body in units:
        base = f"para-{num}" if num else f"{PREFIX[kind]}-{len(paragraphs) + 1}"
        eid, n = base, 1
        while eid in used:
            n += 1
            eid = f"{base}-{n}"
        used.add(eid)
        refs: list[ParsedRef] = []
        seen: set[str] = set()
        for cited in OWN_COURT_CITATION.finditer(body):
            href = f"/gb/{cited.group(2).lower()}/{cited.group(1)}/{int(cited.group(3))}"
            if href not in seen:
                seen.add(href)
                refs.append(
                    ParsedRef(href, f"[{cited.group(1)}] {cited.group(2)} {cited.group(3)}", "cites", "pattern")
                )
        paragraphs.append(ParsedParagraph(eid, num, kind, body, len(paragraphs), refs))

    ext: dict[str, Any] = {"case_id": case_id}
    return [
        ParsedDocument(
            doc_kind="judgment",
            instrument_type="judgment",
            jurisdiction_id="GB",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/eng@{decided.isoformat()}",
            work_title=name,
            title=name,
            short_title=None,
            number=case_id,
            work_date=decided,
            language="en",
            authoritative=False,  # GOV.UK's extraction of the tribunal's PDF; the signed decision is the original
            text_origin="official_conversion",
            consolidation_kind="unknown",
            version_date=decided,
            currency_date=None,
            extent=["GB"],
            ext_module="gb",
            ext=ext,
            judgment=ParsedJudgmentMeta(
                court_id=court_id,
                case_name=name,
                neutral_citation=neutral,
                decision_date=decided,
                docket=case_id,
            ),
            paragraphs=paragraphs,
        )
    ]
