"""Parser for the OECD Compendium of Legal Instruments (``oecd_legal_instruments`` connector bundles).

A bundle holds the instrument's record (JSON: key ``OECD/LEGAL/nnnn``, titles, dates, change history) and its text in
one language
(the HTML the OECD exports from Word), exactly as received. The parser produces one :class:`ParsedDocument` per bundle:

* the dates come from the record: adopted, in force, last updated or amended (the date the current wording dates from)
  and, for
  an abrogated instrument, abrogated. The current wording is valid from the latest of the entry into force and the last
  update/amendment, and until the abrogation date if there is one (``validity_basis = "source_periods"``). Earlier
  wordings of an
  amended instrument are not published and are not recorded;
* the structure is the text's own: for a convention, its articles (``Article 3``); for a Council act, the recitals
  before the
  first numbered paragraph (``preamble``), the Roman-numbered sections (``I. RECOMMENDS ...``) and the numbered
  paragraphs
  (``1.`` or ``1)``) in sequence; text after a rule of underscores (the OECD's closing notes) and anything else is kept
  as text
  blocks. Sub-points (``a)``, ``i)``) stay inside their paragraph;
* the English and French texts are the official ones ("The only official versions are the English and French texts
  available on
  the OECD website"), so both are ``authoritative``; no translation is made or stored.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Any

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParseSkipped
from ulis.sources.parsers.constitution_pages_b import (
    BLOCK_TAGS,
    SKIP_TAGS,
    Source,
    article_heading,
    chunk_paragraphs,
    clean_line,
    decode_text,
    html_source,
    split_items,
)

PARSER_VERSION = "1.0.0"
BUNDLE_SCHEMA = "ulis-oecd-legal-instrument-bundle/1"
JURISDICTION = "INT-OECD"
MODULE_ID = "oecd"
ROMAN = re.compile(r"^(?P<n>[IVX]{1,6})[.)]\s+(?P<rest>\S.*)$")
ARABIC = re.compile(r"^(?P<n>\d{1,3})[.)]\s+(?P<rest>\S.*)$")
DOTTED = re.compile(r"^(?P<a>\d{1,2})\.(?P<b>\d{1,2})\.?\s+(?P<rest>\S.*)$")
SECTION = re.compile(r"^section\s+(?P<n>\d{1,2})\s*[:.–—-]\s*\S", re.I)
LETTERED = re.compile(r"^(?P<r>[IVX]{1,6})\.(?P<l>[A-Z])[.)]\s+\S")
ANNEX = re.compile(r"^(?P<w>APPENDIX|ANNEX|APPENDICE|ANNEXE)(?:\s+(?P<n>[IVX\d]{1,4}))?$")
RULE = re.compile(r"^[_\-–—=]{8,}$")

# the instrument types of the profile, from the first words of the title
TYPE_RULES = (
    (re.compile(r"^(?:\w+ )?(?:revised )?decision-recommendation", re.I), "decision_recommendation"),
    (re.compile(r"^(?:(?:second|third|fourth|fifth) )?(?:revised )?decision\b", re.I), "decision"),
    (re.compile(r"^(?:dac |revised |\w+ revised )*recommendation\b", re.I), "recommendation"),
    (re.compile(r"^(?:\w+ )?(?:ministerial )?declaration\b", re.I), "declaration"),
    (
        re.compile(
            r"^(?:revised )?(?:\w+ )?(?:convention|agreement|protocol|multilateral|framework agreement)\b", re.I
        ),
        "treaty",
    ),
    (re.compile(r"\b(?:arrangement|understanding)\b", re.I), "arrangement"),
)


class OecdParseError(ValueError):
    """The bundle is not an OECD legal-instrument bundle, or its record lacks what the parser needs."""


def instrument_type(title: str) -> str:
    for pattern, kind in TYPE_RULES:
        if pattern.search(title.strip()):
            return kind
    return "other"


def read_bundle(content: bytes) -> dict[str, Any]:
    try:
        bundle = json.loads(content)
    except ValueError as exc:
        raise OecdParseError("not an OECD bundle (not JSON)") from exc
    if not isinstance(bundle, dict) or bundle.get("schema") != BUNDLE_SCHEMA:
        raise OecdParseError("not an OECD bundle (unexpected schema)")
    for key in ("key", "id", "language", "url", "retrieved_on", "record", "html_b64"):
        if not bundle.get(key):
            raise OecdParseError(f"bundle lacks {key}")
    try:
        bundle["html"] = base64.b64decode(bundle["html_b64"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise OecdParseError("bundle text is not base64") from exc
    return bundle


def roman_value(text: str) -> int:
    values = {"I": 1, "V": 5, "X": 10}
    total = 0
    for i, ch in enumerate(text):
        v = values[ch]
        total += -v if i + 1 < len(text) and values[text[i + 1]] > v else v
    return total


def iso(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise OecdParseError(f"not a date: {value!r}") from exc


class _BoldParagraphs(HTMLParser):
    """Paragraphs of the Word export whose whole text is bold: the OECD's section headings (the export has no heading
    tags)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: set[str] = set()
        self._bold = 0
        self._skip = 0
        self._text: list[str] = []
        self._plain = False  # some text outside bold in the current paragraph

    def _flush(self) -> None:
        line = clean_line("".join(self._text))
        if line and not self._plain:
            self.found.add(line)
        self._text, self._plain = [], False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag in ("b", "strong"):
            self._bold += 1
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in ("b", "strong"):
            self._bold = max(0, self._bold - 1)
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        data = re.sub(r"\s+", " ", data)
        if data.strip() and not self._bold:
            self._plain = True
        self._text.append(data)


def bold_headings(html: bytes, content_type: str | None) -> frozenset[str]:
    parser = _BoldParagraphs()
    parser.feed(decode_text(html, content_type))
    parser.close()
    return frozenset(
        line for line in parser.found if len(line) <= 140 and not line.endswith((":", ".", ";")) and not line.isdigit()
    )


@dataclass
class Part:
    kind: str  # preamble | annex | roman | lettered | paragraph | dotted | section | article | block
    label: str = ""
    parent: str = ""  # the Roman paragraph a numbered or lettered paragraph sits under
    scope: str = ""  # the annex or appendix the unit is in ("" in the body of the act), as the text names it
    scope_id: str = (
        ""  # the same, as an identifier that does not depend on the language (``annex``, ``appendix-2``, ``part-2``)
    )
    lines: list[str] = field(default_factory=list)
    heading: str | None = None
    context: str | None = None


def slug(label: str) -> str:
    """An identifier part from a label: accents folded (``Procédures`` -> ``procedures``), anything else but letters and
    digits a dash."""
    plain = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-") or "x"


def join_headings(lines: list[str]) -> str | None:
    """One heading from the bold lines before a unit: a title that runs over two lines is joined with a space, separate
    headings with a dash."""
    out = ""
    for line in lines:
        if not out:
            out = line
        elif (
            out.endswith(",")
            or line[:1].islower()
            or line.split()[0].casefold() in ("and", "et", "of", "de", "du", "for")
        ):
            out = f"{out} {line}"
        else:
            out = f"{out} — {line}"
    return out or None


def annex_id(match: re.Match[str]) -> str:
    word = "appendix" if match.group("w") in ("APPENDIX", "APPENDICE") else "annex"
    return f"{word}-{match.group('n').lower()}" if match.group("n") else word


def annex_label(match: re.Match[str]) -> str:
    word = match.group("w").capitalize()
    return f"{word} {match.group('n')}" if match.group("n") else word


def numbered_parts(lines: list[str], headings: frozenset[str] = frozenset()) -> list[Part]:
    """Split a Council act into its own units, in reading order.

    * ``I.`` ``II.`` ... (Roman, in sequence): the operative paragraphs ("I. RECOMMENDS that ...");
    * ``1.`` or ``1)`` (in sequence, restarting after each Roman paragraph): its numbered paragraphs;
    * ``I.A.`` ``I.B.`` ... (letters in sequence under Roman ``I``): the OECD's sub-principles;
    * ``1.1.`` ``1.2.`` ... (in sequence): numbered principles/recommendations;
    * ``Section 1: ...`` (in sequence): a section heading, which is also the context heading of what follows;
    * ``ANNEX`` / ``APPENDIX`` (a line of its own, capitals): the units after it are numbered afresh and cited with the
      annex.

    Everything else (recitals before the first unit, sub-points ``a)`` / ``i.`` / dashes, definitions) is the text of
    the unit it
    follows, except the bold headings (``headings``: lines of a bold-only paragraph) directly before a unit, which are
    that
    unit's heading.
    """
    parts: list[Part] = []
    current = Part("preamble")
    roman = arabic = major = minor = sections = 0
    letter = 0  # letters used under the current Roman paragraph
    implicit = 0  # numbering sets that start without an ANNEX / APPENDIX line
    parent = scope = scope_id = ""
    context: str | None = None
    pending: list[str] = []  # bold headings seen since the last line of text
    for line in lines:
        part: Part | None = None
        annex = ANNEX.match(line)
        if annex:
            roman = arabic = major = minor = sections = letter = 0
            parent, scope, scope_id, context = "", annex_label(annex), annex_id(annex), None
            parts.append(current)
            current = Part("annex", scope, scope_id=scope_id, lines=[line], heading=join_headings(pending))
            pending = []
            continue
        rm, am, dm, sm, lm = (
            ROMAN.match(line),
            ARABIC.match(line),
            DOTTED.match(line),
            SECTION.match(line),
            LETTERED.match(line),
        )
        if rm and roman_value(rm.group("n")) == roman + 1:
            roman += 1
            arabic = letter = 0
            parent = rm.group("n")
            part = Part("roman", parent, scope=scope, scope_id=scope_id, lines=[line], context=context)
        elif rm and roman_value(rm.group("n")) == 1 and roman >= 2:
            # "I." again after II, III ...: a second set of paragraphs ("Procedures" attached to a Decision), named by
            # its heading
            implicit += 1
            scope = pending[-1] if pending and len(pending[-1]) <= 40 else f"Part {implicit + 1}"
            scope_id = f"part-{implicit + 1}"
            roman, arabic, letter, major, minor, sections = 1, 0, 0, 0, 0, 0
            parent, context = "I", None
            part = Part("roman", parent, scope=scope, scope_id=scope_id, lines=[line])
        elif lm and lm.group("r") == parent and ord(lm.group("l")) - 64 == letter + 1:
            letter += 1
            part = Part(
                "lettered",
                f"{parent}.{lm.group('l')}",
                parent=parent,
                scope=scope,
                scope_id=scope_id,
                lines=[line],
                context=context,
            )
        elif sm and int(sm.group("n")) == sections + 1:
            sections += 1
            context = line
            part = Part("section", sm.group("n"), scope=scope, scope_id=scope_id, lines=[line])
        elif dm and (
            (int(dm.group("a")) == major and int(dm.group("b")) == minor + 1)
            or (int(dm.group("a")) == major + 1 and int(dm.group("b")) == 1)
        ):
            major, minor = int(dm.group("a")), int(dm.group("b"))
            part = Part("dotted", f"{major}.{minor}", scope=scope, scope_id=scope_id, lines=[line], context=context)
        elif am and int(am.group("n")) == arabic + 1:
            arabic += 1
            part = Part(
                "paragraph", am.group("n"), parent=parent, scope=scope, scope_id=scope_id, lines=[line], context=context
            )
        if part is None:
            if line in headings:
                if current.kind == "section" and not pending and len(current.lines) < 3:
                    current.lines.append(line)  # the section's title runs over a second bold line
                    context = " ".join(current.lines)
                else:
                    pending.append(line)
                continue
            current.lines.extend(pending)
            pending = []
            current.lines.append(line)
            continue
        part.heading = join_headings(pending)
        pending = []
        parts.append(current)
        current = part
    current.lines.extend(pending)
    parts.append(current)
    return parts


def parse_oecd_legal_instruments(content: bytes) -> list[ParsedDocument]:
    bundle = read_bundle(content)
    record: dict[str, Any] = bundle["record"]
    key, lang = str(bundle["key"]), str(bundle["language"])
    names = {n["lang"]: " ".join(str(n["value"]).split()) for n in (record.get("title") or {}).get("name", [])}
    title = names.get(lang) or names.get("en")
    if not title:
        raise OecdParseError(f"{key}: the record has no title")
    summary: dict[str, Any] = record.get("statusSummary") or {}
    adopted, in_force = iso(summary.get("adoptionDate")), iso(summary.get("inForceDate"))
    updated, amended = iso(summary.get("lastUpdateDate")), iso(summary.get("lastAmendmentDate"))
    abrogated = iso(summary.get("abrogationDate"))
    published = iso(summary.get("lastPublishDate")) or iso(bundle["retrieved_on"])
    starts = [d for d in (in_force, updated, amended) if d is not None]
    valid_from = max(starts) if starts else adopted
    retrieved = iso(bundle["retrieved_on"])
    assert retrieved is not None and published is not None

    source: Source = html_source(bundle["html"], bundle.get("content_type"))
    lines = [ln for ln in source.lines if ln]
    if not lines:
        raise ParseSkipped(f"{key}: no text in {lang}")
    parts: list[Part] = []
    items, _ = split_items(Source(lines, wrapped=False), None, None, series=False)
    if (
        sum(1 for it in items if it.kind == "article" and any(it.lines)) >= 3
        and sum(1 for ln in lines if article_heading(ln, None)) >= 3
    ):
        numbering = "articles"
        first_article = next(i for i, ln in enumerate(lines) if article_heading(ln, None))
        if first_article:
            parts.append(Part("preamble", lines=lines[:first_article]))
        for it in items:
            if it.kind == "article" and any(it.lines):
                parts.append(Part("article", it.label, lines=it.lines, context=it.context))
            elif it.kind == "block" and any(it.lines):
                parts.append(Part("block", lines=it.lines, heading=it.heading, context=it.context))
    else:
        rule = next((i for i, ln in enumerate(lines) if RULE.match(ln)), None)
        body, closing = (lines[:rule], lines[rule + 1 :]) if rule is not None else (lines, [])
        numbered = numbered_parts(body, bold_headings(bundle["html"], bundle.get("content_type")))
        if sum(1 for p in numbered if p.kind != "preamble") >= 2:
            numbering = "paragraphs"
            parts.extend(numbered)
        else:
            numbering = "blocks"
            parts.extend(Part("block", lines=group) for group in chunk_paragraphs(body))
        if closing:
            parts.append(Part("block", lines=closing))

    provisions: list[ParsedProvision] = []
    seen: set[str] = set()
    block_no = 0

    def add(eid: str, kind: str, num_path: list[str], text: str, **kw: Any) -> None:
        base, k = eid, 1
        while eid in seen:
            k += 1
            eid = f"{base}-{k}"
        seen.add(eid)
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=kind,
                num_path=num_path,
                top_kind=kind,
                heading=kw.get("heading"),
                text=text,
                parent_eid=kw.get("parent_eid"),
                sort_order=len(provisions),
                status="in_force",
                valid_from=valid_from,
                valid_to=abrogated,
                extent=[JURISDICTION],
                note=None,
                refs=[],
                is_leaf=kw.get("is_leaf", True),
                has_own_content=kw.get("has_own_content", False),
                context_heading=kw.get("context"),
            )
        )

    for index, part in enumerate(parts):
        text = "\n".join(part.lines).strip()
        if not text:
            continue
        prefix = [part.scope] if part.scope else []
        stem = f"{part.scope_id}-" if part.scope_id else ""
        if part.kind == "preamble":
            add("preamble", "preamble", [], text)
        elif part.kind == "annex":
            add(part.scope_id, "annex", [part.label], text, heading=part.heading)
        elif part.kind == "roman":
            has_children = any(
                p.kind in ("paragraph", "lettered") and p.parent == part.label and p.scope_id == part.scope_id
                for p in parts[index + 1 :]
            )
            add(
                f"{stem}para-{slug(part.label)}",
                "paragraph",
                [*prefix, part.label],
                text,
                is_leaf=not has_children,
                has_own_content=True,
                heading=part.heading,
                context=part.context,
            )
        elif part.kind in ("paragraph", "lettered"):
            label = (
                part.label if part.kind == "lettered" else f"{part.parent}.{part.label}" if part.parent else part.label
            )
            path = [*prefix, label]
            parent_eid = f"{stem}para-{slug(part.parent)}" if part.parent else None
            add(
                f"{stem}para-{slug(label)}",
                "paragraph",
                path,
                text,
                parent_eid=parent_eid,
                heading=part.heading,
                context=part.context,
            )
        elif part.kind == "dotted":
            add(
                f"{stem}para-{slug(part.label)}",
                "paragraph",
                [*prefix, part.label],
                text,
                heading=part.heading,
                context=part.context,
            )
        elif part.kind == "section":
            add(f"{stem}sec-{part.label}", "section", [*prefix, part.label], text, heading=part.heading)
        elif part.kind == "article":
            add(f"art-{slug(part.label)}", "article", [part.label], text, context=part.context)
        else:
            block_no += 1
            add(f"blk-{block_no:04d}", "block", [str(block_no)], text, heading=part.heading, context=part.context)
    if not provisions:
        raise ParseSkipped(f"{key}: no provisions recognised in {lang}")

    number = key.rsplit("/", 1)[-1]
    work_uri = f"/int/oecd/{number}"
    events = []
    if adopted:
        events.append(ParsedEvent("enactment", adopted, None, f"{key} adopted", "record/statusSummary/adoptionDate"))
    if in_force and in_force != adopted:
        events.append(
            ParsedEvent("entry_into_force", in_force, None, f"{key} in force", "record/statusSummary/inForceDate")
        )
    if amended:
        events.append(
            ParsedEvent("amendment", amended, None, f"{key} last amended", "record/statusSummary/lastAmendmentDate")
        )
    if abrogated:
        events.append(ParsedEvent("repeal", abrogated, None, f"{key} abrogated", "record/statusSummary/abrogationDate"))
    events.append(ParsedEvent("version_change", retrieved, None, "Retrieval date of the text", "bundle"))
    ext: dict[str, Any] = {
        "key": key,
        "oecd_id": int(bundle["id"]),
        "record_url": bundle.get("record_url") or bundle["url"],
    }
    if abrogated:
        ext["abrogated_on"] = abrogated.isoformat()
    ext["numbering"] = numbering
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=instrument_type(names.get("en") or title),
            jurisdiction_id=JURISDICTION,
            work_uri=work_uri,
            expression_uri=f"{work_uri}/{lang}@{published.isoformat()}",
            work_title=names.get("en") or title,
            title=title,
            short_title=None,
            number=key,
            work_date=adopted,
            language=lang,
            authoritative=True,  # the English and French texts are the official ones
            text_origin="official_authentic",
            consolidation_kind="current_consolidation",
            version_date=published,
            currency_date=retrieved,  # the Compendium served this text as the current one on the day it was read
            extent=[JURISDICTION],
            ext_module=MODULE_ID,
            ext=ext,
            events=events,
            provisions=provisions,
            validity_basis="source_periods",
        )
    ]


__all__ = ["PARSER_VERSION", "parse_oecd_legal_instruments"]
