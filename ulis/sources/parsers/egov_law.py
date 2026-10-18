"""Parser for Japanese law data from e-Gov 法令検索 (Digital Agency), 法令API Version 2, XML.

Two kinds of response are parsed (``?response_format=xml``):

* ``law_data_response`` (``/law_data/{law_revision_id}``): the whole law as amended by one
  revision (law_info, revision_info and the 法令標準XML text). Each revision is a snapshot of
  the entire law in force from the revision's enforcement date (施行期日,
  ``amendment_enforcement_date``) until the next revision. The source gives no per-provision
  dates, so every provision is dated from the snapshot (``validity_basis =
  "revision_snapshot"``); the pipeline continues an unchanged wording from the previous snapshot
  and ends provisions that a later snapshot no longer contains (``ulis.ingestion.snapshots``).
* ``law_revisions_response`` (``/law_revisions/{law_id}``): the list of revisions. It becomes a
  *change report* for the current revision: amendments promulgated but not yet in force
  (``UnEnforced``) are outstanding changes, never law; enforced amendments are lifecycle events.

Format notes:

* structure: 編/章/節/款/目 (Part, Chapter, Section, Subsection, Division) are headings only;
  条 (Article), 項 (Paragraph), 号 (Item) and 号細分 (Subitem1..) are provisions. ``Num`` uses
  ``_`` for の (``27_2`` = 第二十七条の二) and ``:`` for ranges (``38:84``, deleted articles);
* identifiers are language-neutral: ``article-709``, ``article-27-paragraph-1-item-2``,
  ``suppl-article-2`` (original 附則), ``preamble``, ``appdx-table-1`` (別表). An article with a
  single paragraph is one provision (cited 第709条, not 第709条第1項); its items are
  ``article-N-item-M`` (cited 第N条第M号), following Japanese citation practice;
* article captions (見出し, e.g. ``（不法行為による損害賠償）``) are headings, not text;
* ruby readings (``<Rt>``, furigana) are dropped: the text keeps the base characters only;
* paragraph numbers and item titles are printed before their text, separated by an
  ideographic space, as e-Gov renders them; table cells are joined with " | ";
* a provision whose whole text is 削除 ("deleted") is repealed; 削除 is kept as its note;
* supplementary provisions of amending laws (改正附則, ``SupplProvision/@AmendLawNum``) are
  provisions of other laws and are not indexed; the table of contents and enacting formula
  (制定文) are not indexed either.

Two more inputs come from e-Gov's bulk download (``all_xml.zip``, the publisher's route for loading every law;
see ``ulis/sources/connectors/egov_laws.py``). They need what the connector states about them (``metadata``, see
the pipeline's ``METADATA_PARSERS``):

* a bare ``<Law>`` file (the archive member of one revision; the same 法令標準XML element the API returns in
  ``law_full_text``). The file carries no law id and no revision data, so the connector passes the API list's
  ``law_info`` and ``current_revision_info`` for the law (``metadata["egov_bulk"] == "law"``); the result is the
  document ``law_data_response`` gives for the same revision. The law type is the file's own ``LawType`` (what the
  text endpoint says, ``Act`` for the 21 laws the list types twice) and so are the abbreviations
  (``LawTitle/@Abbrev``, which the text endpoint repeats in ``revision_info``); the law number must match
  ``<LawNum>``;
* the lines of the archive's list (``all_law_list.csv``) for one law (``metadata["egov_bulk"] == "law_list"``):
  the change report for the revision in force. Rows marked 未施行 are the amendments not yet in force, as
  ``UnEnforced`` revisions are in a revision list; a row whose 施行日備考 (enforcement note) is filled has only a
  placeholder date (the API gives such a revision an ``amendment_scheduled_enforcement_date``). The list's dates
  are written in the Japanese era (令和八年三月三十一日); they are read as e-Gov's own ISO dates are (era year + base,
  checked against e-Gov's ISO dates for every row of the archive of 2026-10-17). The archive lists only the
  revisions in force or to come, not the earlier ones since 2017, so the report's events are the amendment in
  force and the promulgation (a revision list also has the earlier amendments).

The help page states that where the e-Gov data differs from the Official Gazette (官報), the
Gazette prevails: the text is a revised (consolidated) text, not the authentic one.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from typing import Any
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParseSkipped

PARSER_ID = "egov_law_xml"
PARSER_VERSION = "1.2.0"
SITE = "https://laws.e-gov.go.jp"
JURISDICTION = "JP"

# e-Gov law_type -> ULIS instrument type
INSTRUMENT_TYPES = {
    "Constitution": "constitution",
    "Act": "act",
    "CabinetOrder": "cabinet_order",
    "ImperialOrder": "imperial_ordinance",
    "MinisterialOrdinance": "ministerial_ordinance",
    "Rule": "rule",
    # e-Gov's list and revision-list endpoints give 21 laws two types ("Act,ImperialOrder", "Act,CabinetOrder",
    # "Act,MinisterialOrdinance": post-war orders that have the force of an Act) while its text endpoint types
    # them "Act". The text endpoint's classification is the one used for the instrument type, so a text and
    # its revision list agree; each response's own value is kept unchanged in ext.law_type.
    "Act,ImperialOrder": "act",
    "Act,CabinetOrder": "act",
    "Act,MinisterialOrdinance": "act",
}
IN_FORCE = {"CurrentEnforced", "PreviousEnforced"}
CONTAINERS = {"Part", "Chapter", "Section", "Subsection", "Division"}
# Children of a paragraph/item that are not body text (titles and numbers are printed separately).
LABELS = {
    "ParagraphNum",
    "ParagraphCaption",
    "ParagraphSentence",
    "ItemTitle",
    "ItemSentence",
    "ArticleTitle",
    "ArticleCaption",
}
DELETED = "削除"
SPACE = "　"  # ideographic space, as printed between a number or title and the text
_WS = " \t\r\n"  # pretty-printing whitespace; the ideographic space is never stripped
_PRETTY = re.compile(r"[ \t\r]*\n[ \t\r\n]*")
_SUBITEM = re.compile(r"^Subitem(\d+)$")
# The bulk download's list of revisions (all_law_list.csv), its columns in order.
BULK_LIST_COLUMNS = (
    "法令種別",
    "法令番号",
    "法令名",
    "法令名読み",
    "旧法令名",
    "公布日",
    "改正法令名",
    "改正法令番号",
    "改正法令公布日",
    "施行日",
    "施行日備考",
    "法令ID",
    "本文URL",
    "未施行",
)
PENDING_MARK = "○"  # 未施行: the revision is not in force yet
NO_AMENDMENT = "0" * 15  # the amending-law part of the revision id of a law as made
_BODY_URL = re.compile(r"^https://laws\.e-gov\.go\.jp/law/([0-9A-Z]+)/([0-9]{8})_([0-9A-Z]{15})$")
ERAS = {"明治": 1868, "大正": 1912, "昭和": 1926, "平成": 1989, "令和": 2019}
_KANJI = "〇一二三四五六七八九十"
_KANJI_DATE = re.compile(rf"^(明治|大正|昭和|平成|令和)([元{_KANJI}]+)年([{_KANJI}]+)月([{_KANJI}]+)日$")
_DIGITS = {c: i for i, c in enumerate("〇一二三四五六七八九")}


class EgovParseError(ValueError):
    pass


def _date(value: str | None) -> date | None:
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _kanji_number(text: str) -> int:
    """一 .. 九十九 (and 元 for the first year of an era)."""
    if text == "元":
        return 1
    tens, _, units = text.rpartition("十")
    if "十" in text:
        value = (_DIGITS[tens] if tens else 1) * 10 + (_DIGITS[units] if units else 0)
    elif len(text) == 1:
        value = _DIGITS[text]
    else:
        raise EgovParseError(f"unexpected number {text!r}")
    return value


def kanji_date(value: str) -> date | None:
    """A date as the bulk list writes it (``令和八年三月三十一日``), read as e-Gov writes it in ISO form: the era's base
    year + the era year - 1, the month and the day unchanged (so 明治五年十一月九日, before Japan adopted the Gregorian
    calendar, is 1872-11-09, as in the API). Empty means not stated (None); anything else unreadable is an error."""
    if not value.strip():
        return None
    m = _KANJI_DATE.match(value.strip())
    if not m:
        raise EgovParseError(f"unreadable date {value!r}")
    era, year, month, day = m.groups()
    try:
        return date(ERAS[era] + _kanji_number(year) - 1, _kanji_number(month), _kanji_number(day))
    except (KeyError, ValueError) as exc:
        raise EgovParseError(f"unreadable date {value!r}") from exc


def _raw(el: Element) -> str:
    """Inline text without ruby readings (``<Rt>``)."""
    parts = [el.text or ""]
    for child in el:
        if child.tag != "Rt":
            parts.append(_raw(child))
        parts.append(child.tail or "")
    return "".join(parts)


def inline_text(el: Element | None) -> str:
    """Element text with pretty-printing whitespace removed (line breaks and their indentation)."""
    if el is None:
        return ""
    return _PRETTY.sub("", _raw(el)).strip(_WS)


def sentence_text(block: Element | None) -> str:
    """Text of a ParagraphSentence / ItemSentence / Subitem*Sentence / Column / TableColumn."""
    if block is None:
        return ""
    columns = [c for c in block if c.tag == "Column"]
    if columns:
        return SPACE.join(t for t in (sentence_text(c) for c in columns) if t)
    sentences = [c for c in block if c.tag == "Sentence"]
    if sentences:
        return "".join(inline_text(s) for s in sentences)
    return inline_text(block)


def _table_lines(el: Element) -> list[str]:
    lines: list[str] = []
    for row in el.iter("TableRow"):
        cells = [sentence_text(col) for col in row.findall("TableColumn")]
        line = " | ".join(cells)
        if line.strip(" |"):
            lines.append(line)
    return lines


def _block_lines(el: Element) -> list[str]:
    """Lines of a non-provision block inside a paragraph or item (tables, lists, figures...)."""
    if el.tag in ("TableStruct", "Table"):
        title = el.find("TableStructTitle")
        head = [inline_text(title)] if title is not None and inline_text(title) else []
        return head + _table_lines(el)
    text = inline_text(el)
    return [text] if text else []


@dataclass(frozen=True)
class LawInfo:
    law_id: str
    law_type: str
    law_num: str
    promulgation_date: date | None

    @property
    def instrument_type(self) -> str:
        try:
            return INSTRUMENT_TYPES[self.law_type]
        except KeyError as exc:
            raise EgovParseError(f"unsupported law_type {self.law_type!r}") from exc

    @property
    def number(self) -> str | None:
        """The law number (法令番号) for the instruments table, whose column holds 40 characters; a longer one
        (a joint order of eight ministries) is only in ``ext.law_num``, never shortened."""
        return self.law_num if len(self.law_num) <= 40 else None

    @property
    def work_uri(self) -> str:
        return f"/jp/{self.instrument_type}/{self.law_id}"

    def ext(self, abbrev: str | None) -> dict[str, object]:
        return {"law_id": self.law_id, "law_num": self.law_num, "law_type": self.law_type, "abbrev": abbrev or None}


def _clean(value: str) -> str:
    """What :func:`inline_text` makes of an element holding only this text."""
    return _PRETTY.sub("", value).strip(_WS)


@dataclass(frozen=True)
class RevisionInfo:
    """The fields of one revision used here: a revision list's ``revision``, ``law_data_response``'s
    ``revision_info`` or, for the bulk download, the API list's ``current_revision_info`` (same names)."""

    law_revision_id: str
    status: str
    repeal_status: str
    enforcement_date: str
    scheduled: str  # amendment_scheduled_enforcement_date: a placeholder, never a date of law
    comment: str
    promulgate_date: str
    amendment_type: str
    amendment_law_id: str
    amendment_law_title: str
    amendment_law_num: str
    abbrev: str
    law_title: str

    @classmethod
    def from_xml(cls, rev: Element) -> RevisionInfo:
        def text(name: str) -> str:
            return (rev.findtext(name) or "").strip()

        return cls(
            law_revision_id=text("law_revision_id"),
            status=text("current_revision_status"),
            repeal_status=text("repeal_status") or "None",
            enforcement_date=text("amendment_enforcement_date"),
            scheduled=text("amendment_scheduled_enforcement_date"),
            comment=inline_text(rev.find("amendment_enforcement_comment")),
            promulgate_date=text("amendment_promulgate_date"),
            amendment_type=text("amendment_type"),
            amendment_law_id=text("amendment_law_id"),
            amendment_law_title=inline_text(rev.find("amendment_law_title")),
            amendment_law_num=text("amendment_law_num"),
            abbrev=text("abbrev"),
            law_title=inline_text(rev.find("law_title")),
        )

    @classmethod
    def from_metadata(cls, fields: Mapping[str, Any]) -> RevisionInfo:
        """The same fields from the texts of the elements, as the connector copied them."""

        def text(name: str) -> str:
            return str(fields.get(name) or "").strip()

        def inline(name: str) -> str:
            return _clean(str(fields.get(name) or ""))

        return cls(
            law_revision_id=text("law_revision_id"),
            status=text("current_revision_status"),
            repeal_status=text("repeal_status") or "None",
            enforcement_date=text("amendment_enforcement_date"),
            scheduled=text("amendment_scheduled_enforcement_date"),
            comment=inline("amendment_enforcement_comment"),
            promulgate_date=text("amendment_promulgate_date"),
            amendment_type=text("amendment_type"),
            amendment_law_id=text("amendment_law_id"),
            amendment_law_title=inline("amendment_law_title"),
            amendment_law_num=text("amendment_law_num"),
            abbrev=text("abbrev"),
            law_title=inline("law_title"),
        )

    @property
    def original(self) -> bool:
        return self.amendment_type == "1"

    def amendment(self) -> tuple[str | None, str | None]:
        """(e-Gov URL of the amending law, "title（number）"), or (None, None)."""
        return _amendment_label(self.amendment_law_id, self.amendment_law_title, self.amendment_law_num)


def _amendment_label(law_id: str, title: str, num: str) -> tuple[str | None, str | None]:
    label = f"{title}（{num}）" if title and num else title or num or None
    return (law_url(law_id) if law_id else None), label


def law_url(law_id: str) -> str:
    return f"{SITE}/law/{law_id}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map an e-Gov law page URL to (ULIS work URI, eId); only Acts and the Constitution."""
    m = re.match(r"^https://laws\.e-gov\.go\.jp/law/([0-9]{3}(AC|CONSTITUTION)[0-9A-Z]*)/?$", uri)
    if not m:
        return None
    kind = "act" if m.group(2) == "AC" else "constitution"
    return f"/jp/{kind}/{m.group(1)}", None


def _law_info_from(fields: Mapping[str, Any]) -> LawInfo:
    law_id = str(fields.get("law_id") or "").strip()
    if not re.fullmatch(r"[0-9A-Za-z]+", law_id):
        raise EgovParseError(f"invalid law_id {law_id!r}")
    return LawInfo(
        law_id=law_id,
        law_type=str(fields.get("law_type") or "").strip(),
        law_num=str(fields.get("law_num") or "").strip(),
        promulgation_date=_date(str(fields.get("promulgation_date") or "")),
    )


def _law_info(root: Element) -> LawInfo:
    info = root.find("law_info")
    if info is None:
        raise EgovParseError("missing law_info")
    return _law_info_from({c.tag: c.text or "" for c in info})


def parse_egov(content: bytes, metadata: Mapping[str, Any] | None = None) -> list[ParsedDocument]:
    """``metadata`` is what the connector states about the document; only the bulk download's documents need it."""
    bulk = (metadata or {}).get("egov_bulk")
    if bulk == "law_list":
        assert metadata is not None
        return [_parse_bulk_list(content, metadata)]
    try:
        root = fromstring(content, forbid_dtd=True)
    except Exception as exc:
        raise EgovParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if root.tag == "law_data_response":
        return [_parse_law_data(root)]
    if root.tag == "law_revisions_response":
        return [_parse_revisions(root)]
    if root.tag == "Law":
        if bulk != "law" or metadata is None:
            raise EgovParseError(
                "a bare <Law> file (bulk download) carries no law id or revision data: "
                "it needs the connector's metadata (law_info, revision_info)"
            )
        return [_parse_bulk_law(root, metadata)]
    raise EgovParseError(f"unsupported root element {root.tag!r}")


# --------------------------------------------------------------------------- law text


class _Builder:
    def __init__(self, version_date: date):
        self.version_date = version_date
        self.provisions: list[ParsedProvision] = []
        self._used: dict[str, int] = {}

    def eid(self, base: str) -> str:
        n = self._used.get(base, 0) + 1
        self._used[base] = n
        return base if n == 1 else f"{base}-{n}"

    def add(
        self,
        *,
        eid: str,
        kind: str,
        top_kind: str,
        num_path: list[str],
        parent_eid: str | None,
        heading: str | None,
        text: str,
        own: str,
        has_children: bool,
        context: str | None,
    ) -> None:
        deleted = own == DELETED and not has_children
        self.provisions.append(
            ParsedProvision(
                eid=eid,
                kind=kind,
                num_path=num_path,
                top_kind=top_kind,
                heading=heading or None,
                text="" if deleted else text,
                parent_eid=parent_eid,
                sort_order=len(self.provisions),
                status="repealed" if deleted else "in_force",
                valid_from=self.version_date,
                valid_to=None,
                extent=[JURISDICTION],
                note=DELETED if deleted else None,
                refs=[],
                is_leaf=not has_children,
                has_own_content=bool(own) and has_children,
                context_heading=context,
            )
        )

    # -- text of each level (own text excludes the provision's own number or title)

    def subitem_lines(self, el: Element, level: int) -> list[str]:
        title = inline_text(el.find(f"Subitem{level}Title"))
        own = sentence_text(el.find(f"Subitem{level}Sentence"))
        lines = [f"{title}{SPACE}{own}" if title and own else title or own]
        lines.extend(self.subitem_body(el, level))
        return [line for line in lines if line]

    def subitem_body(self, el: Element, level: int) -> list[str]:
        """Lines below a subitem's own sentence: its subitems (with their titles) and other blocks."""
        lines: list[str] = []
        for child in el:
            m = _SUBITEM.match(child.tag)
            if m and int(m.group(1)) == level + 1:
                lines.extend(self.subitem_lines(child, level + 1))
            elif child.tag not in (f"Subitem{level}Title", f"Subitem{level}Sentence"):
                lines.extend(_block_lines(child))
        return lines

    def item_body(self, item: Element) -> tuple[str, list[str]]:
        own = sentence_text(item.find("ItemSentence"))
        lines: list[str] = []
        for child in item:
            if _SUBITEM.match(child.tag):
                lines.extend(self.subitem_lines(child, 1))
            elif child.tag not in LABELS:
                lines.extend(_block_lines(child))
        return own, lines

    def paragraph_body(self, para: Element) -> tuple[str, list[str]]:
        own = sentence_text(para.find("ParagraphSentence"))
        lines: list[str] = []
        for child in para:
            if child.tag == "Item":
                title = inline_text(child.find("ItemTitle"))
                item_own, item_lines = self.item_body(child)
                lines.append(f"{title}{SPACE}{item_own}" if title and item_own else title or item_own)
                lines.extend(item_lines)
            elif child.tag not in LABELS:
                lines.extend(_block_lines(child))
        return own, [line for line in lines if line]

    # -- provisions

    def subitems(
        self, parent: Element, level: int, parent_eid: str, path: list[str], top: str, ctx: str | None
    ) -> None:
        for el in parent.findall(f"Subitem{level}"):
            num = el.get("Num") or str(len(self.provisions))
            eid = self.eid(f"{parent_eid}-subitem{level}-{num}")
            title = inline_text(el.find(f"Subitem{level}Title"))
            own = sentence_text(el.find(f"Subitem{level}Sentence"))
            children = el.findall(f"Subitem{level + 1}")
            text = "\n".join(line for line in [own, *self.subitem_body(el, level)] if line)
            self.add(
                eid=eid,
                kind=f"subitem{level}",
                top_kind=top,
                num_path=[*path, title or num],
                parent_eid=parent_eid,
                heading=None,
                text=text,
                own=own,
                has_children=bool(children),
                context=ctx,
            )
            self.subitems(el, level + 1, eid, [*path, title or num], top, ctx)

    def items(self, parent: Element, parent_eid: str, path: list[str], top: str, ctx: str | None) -> None:
        for item in parent.findall("Item"):
            num = item.get("Num") or str(len(self.provisions))
            eid = self.eid(f"{parent_eid}-item-{num}")
            own, lines = self.item_body(item)
            self.add(
                eid=eid,
                kind="item",
                top_kind=top,
                num_path=[*path, num],
                parent_eid=parent_eid,
                heading=None,
                text="\n".join(line for line in [own, *lines] if line),
                own=own,
                has_children=bool(item.findall("Subitem1")),
                context=ctx,
            )
            self.subitems(item, 1, eid, [*path, num], top, ctx)

    def paragraph(
        self, para: Element, parent_eid: str | None, path: list[str], top: str, ctx: str | None, prefix: str = ""
    ) -> None:
        num = para.get("Num") or str(len(self.provisions))
        eid = self.eid(f"{parent_eid}-paragraph-{num}" if parent_eid else f"{prefix}paragraph-{num}")
        own, lines = self.paragraph_body(para)
        self.add(
            eid=eid,
            kind="paragraph",
            top_kind=top,
            num_path=[*path, num],
            parent_eid=parent_eid,
            heading=inline_text(para.find("ParagraphCaption")) or None,
            text="\n".join(line for line in [own, *lines] if line),
            own=own,
            has_children=bool(para.findall("Item")),
            context=ctx,
        )
        self.items(para, eid, [*path, num], top, ctx)

    def article(self, art: Element, prefix: str, ctx: str | None) -> None:
        num = art.get("Num") or str(len(self.provisions))
        eid = self.eid(f"{prefix}article-{num}")
        caption = inline_text(art.find("ArticleCaption")) or None
        paragraphs = art.findall("Paragraph")
        if len(paragraphs) == 1:
            # One paragraph: the article is the provision (cited 第N条), its items are 第N条第M号.
            para = paragraphs[0]
            own, sole = self.paragraph_body(para)
            pnum = inline_text(para.find("ParagraphNum"))
            first = f"{pnum}{SPACE}{own}" if pnum and own else own
            self.add(
                eid=eid,
                kind="article",
                top_kind="article",
                num_path=[num],
                parent_eid=None,
                heading=caption or inline_text(para.find("ParagraphCaption")) or None,
                text="\n".join(line for line in [first, *sole] if line),
                own=own,
                has_children=bool(para.findall("Item")),
                context=ctx,
            )
            self.items(para, eid, [num], "article", ctx)
            return
        lines: list[str] = []
        for para in paragraphs:
            own, body = self.paragraph_body(para)
            pnum = inline_text(para.find("ParagraphNum"))
            lines.append(f"{pnum}{SPACE}{own}" if pnum and own else own or pnum)
            lines.extend(body)
        self.add(
            eid=eid,
            kind="article",
            top_kind="article",
            num_path=[num],
            parent_eid=None,
            heading=caption,
            text="\n".join(line for line in lines if line),
            own="",
            has_children=bool(paragraphs),
            context=ctx,
        )
        for para in paragraphs:
            self.paragraph(para, eid, [num], "article", ctx)

    def walk(self, el: Element, prefix: str, ctx: list[str]) -> None:
        for child in el:
            if child.tag in CONTAINERS:
                title = inline_text(child.find(f"{child.tag}Title"))
                self.walk(child, prefix, [*ctx, title] if title else ctx)
            elif child.tag == "Article":
                self.article(child, prefix, " ".join(ctx) or None)
            elif child.tag == "Paragraph":
                self.paragraph(child, None, [], "paragraph", " ".join(ctx) or None, prefix)

    def preamble(self, el: Element) -> None:
        lines = []
        for para in el.findall("Paragraph"):
            own, body = self.paragraph_body(para)
            lines.extend([own, *body])
        text = "\n".join(line for line in lines if line)
        self.add(
            eid=self.eid("preamble"),
            kind="preamble",
            top_kind="preamble",
            num_path=[],
            parent_eid=None,
            heading=None,
            text=text,
            own=text,
            has_children=False,
            context=None,
        )

    def appendix_table(self, el: Element, index: int) -> None:
        title = inline_text(el.find("AppdxTableTitle"))
        related = inline_text(el.find("RelatedArticleNum"))
        lines: list[str] = []
        for child in el:
            if child.tag in ("AppdxTableTitle", "RelatedArticleNum"):
                continue
            if child.tag == "Item":
                item_title = inline_text(child.find("ItemTitle"))
                own, body = self.item_body(child)
                lines.append(f"{item_title}{SPACE}{own}" if item_title and own else item_title or own)
                lines.extend(body)
            else:
                lines.extend(_block_lines(child))
        text = "\n".join(line for line in lines if line)
        self.add(
            eid=self.eid(f"appdx-table-{index}"),
            kind="appendix_table",
            top_kind="appendix_table",
            num_path=[title or "別表"],
            parent_eid=None,
            heading=f"{title}{related}" or None,
            text=text,
            own=text,
            has_children=False,
            context=None,
        )


def _parse_law_data(root: Element) -> ParsedDocument:
    info = _law_info(root)
    rev_el = root.find("revision_info")
    if rev_el is None:
        raise EgovParseError("missing revision_info")
    rev = RevisionInfo.from_xml(rev_el)
    law = root.find("law_full_text/Law")
    if law is None or law.find("LawBody") is None:
        raise EgovParseError("missing law_full_text/Law/LawBody (request response_format=xml)")
    return _law_document(law, info, rev, f"revision_info/law_revision_id[.='{rev.law_revision_id}']")


def _parse_bulk_law(law: Element, metadata: Mapping[str, Any]) -> ParsedDocument:
    """A bare <Law> file of the bulk download, with the API list's law_info and current_revision_info."""
    listed = metadata.get("law_info")
    revision = metadata.get("revision_info")
    if not isinstance(listed, Mapping) or not isinstance(revision, Mapping):
        raise EgovParseError("a bulk <Law> file needs metadata law_info and revision_info")
    stated = _law_info_from(listed)
    rev = RevisionInfo.from_metadata(revision)
    if rev.law_revision_id != str(metadata.get("law_revision_id") or "") or not rev.law_revision_id.startswith(
        f"{stated.law_id}_"
    ):
        raise EgovParseError(
            f"the file is revision {metadata.get('law_revision_id')!r}, the metadata describes "
            f"{rev.law_revision_id!r} of {stated.law_id}"
        )
    # The file's own law type is what the text endpoint gives (Act for the laws the list types twice).
    law_type = (law.get("LawType") or "").strip()
    info = LawInfo(stated.law_id, law_type, stated.law_num, stated.promulgation_date)
    if info.instrument_type != stated.instrument_type:
        raise EgovParseError(f"{stated.law_id}: the file is a {law_type}, the list says {stated.law_type}")
    law_num = inline_text(law.find("LawNum"))
    if law_num != stated.law_num:
        raise EgovParseError(f"{stated.law_id}: the file is {law_num!r}, the list says {stated.law_num!r}")
    title = law.find("LawBody/LawTitle")
    if title is None:
        raise EgovParseError("missing Law/LawBody/LawTitle")
    if title.get("Abbrev") is not None:
        # The file's own abbreviations, which law_data's revision_info repeats; the list orders some differently
        # (労働三法,労調法 for 労調法,労働三法) and marks a few 「抄」.
        rev = replace(rev, abbrev=title.get("Abbrev", "").strip())
    return _law_document(law, info, rev, f"metadata/revision_info/law_revision_id[.='{rev.law_revision_id}']")


def _law_document(law: Element, info: LawInfo, rev: RevisionInfo, locator: str) -> ParsedDocument:
    """One revision's text (``law``, a 法令標準XML <Law> element) as a dated snapshot of the whole law."""
    revision_id = rev.law_revision_id
    if rev.status not in IN_FORCE:
        raise EgovParseError(
            f"revision {revision_id} is {rev.status or 'of unknown status'}: only enforced revisions are law; "
            "amendments not yet in force are reported from the revision list"
        )
    if rev.repeal_status != "None":
        raise ParseSkipped(f"revision {revision_id}: repealed or expired (廃止・失効); such laws are not ingested")
    if rev.scheduled:
        # A placeholder date ("not necessarily the actual enforcement date"): never used as a date of law.
        raise EgovParseError(f"revision {revision_id}: enforcement date is a placeholder")
    version_date = _date(rev.enforcement_date)
    if version_date is None:
        raise EgovParseError(f"revision {revision_id}: no enforcement date")
    body = law.find("LawBody")
    assert body is not None
    title = inline_text(body.find("LawTitle"))
    if not title:
        raise EgovParseError("missing LawTitle")

    builder = _Builder(version_date)
    preamble = body.find("Preamble")
    if preamble is not None:
        builder.preamble(preamble)
    main = body.find("MainProvision")
    if main is not None:
        builder.walk(main, "", [])
    for suppl in body.findall("SupplProvision"):
        if suppl.get("AmendLawNum"):
            continue  # supplementary provisions of amending laws belong to those laws
        builder.walk(suppl, "suppl-", [])
    for index, table in enumerate(body.findall("AppdxTable"), start=1):
        builder.appendix_table(table, index)
    if not builder.provisions:
        parts = [*body.findall("MainProvision"), *body.findall("SupplProvision"), *body.findall("AppdxTable")]
        if any("".join(p.itertext()).strip() for p in parts):
            raise EgovParseError(f"revision {revision_id}: no provisions found in a law that has text")
        raise ParseSkipped(f"revision {revision_id}: the law has no text to index")

    original = rev.original
    by_uri, by_label = rev.amendment()
    abbrev = rev.abbrev
    return ParsedDocument(
        doc_kind="legislation",
        instrument_type=info.instrument_type,
        jurisdiction_id=JURISDICTION,
        work_uri=info.work_uri,
        expression_uri=f"{info.work_uri}/jpn@{version_date.isoformat()}",
        work_title=title,
        title=title,
        short_title=title,
        number=info.number,
        work_date=info.promulgation_date,
        language="ja",
        # The Official Gazette (官報) is authentic; e-Gov data is a revised text that yields to it.
        authoritative=False,
        text_origin="official_conversion" if original else "official_revised",
        consolidation_kind="as_made" if original else "point_in_time",
        version_date=version_date,
        currency_date=None,  # set from the revision list (change report) for the current revision
        extent=[JURISDICTION],
        ext_module="jp",
        ext=info.ext(abbrev),
        events=[
            ParsedEvent(
                "version_change",
                version_date,
                by_uri,
                by_label,
                locator,
            )
        ],
        provisions=builder.provisions,
        validity_basis="revision_snapshot",
    )


# --------------------------------------------------------------------------- revision list


def _outstanding(
    revision_id: str,
    promulgated: date | None,
    enforced: date | None,
    placeholder: date | None,
    comment: str,
    by_uri: str | None,
    by_label: str | None,
) -> dict[str, object]:
    """An amendment promulgated but not in force yet, as an outstanding change of the revision in force."""
    if placeholder is not None:
        when = (
            f"enforcement date not yet fixed (「{comment}」); e-Gov orders it at the placeholder date "
            f"{placeholder.isoformat()}, which is not the actual date"
        )
        effective = None
    else:
        when = f"to take effect on {enforced.isoformat()}" if enforced else "enforcement date not stated"
        if comment:
            when += f" (「{comment}」)"
        effective = enforced.isoformat() if enforced else None
    return {
        "affects_eid": None,
        "affected_label": None,
        "by_uri": by_uri,
        "by_title": by_label,
        "description": (
            f"Amendment promulgated {promulgated.isoformat() if promulgated else '(date not stated)'}, "
            f"not yet in force: {when}. Not applied to this text (e-Gov revision {revision_id})."
        ),
        "effective_date": effective,
        "law_revision_id": revision_id,
    }


def _change_report(
    info: LawInfo,
    current_date: date,
    title: str,
    abbrev: str,
    outstanding: list[dict[str, object]],
    events: list[ParsedEvent],
    promulgation_locator: str,
) -> ParsedDocument:
    if info.promulgation_date is not None:
        events.append(ParsedEvent("promulgation", info.promulgation_date, None, info.law_num, promulgation_locator))
    return ParsedDocument(
        doc_kind="legislation",
        instrument_type=info.instrument_type,
        jurisdiction_id=JURISDICTION,
        work_uri=info.work_uri,
        # The change report applies to the expression of the current revision.
        expression_uri=f"{info.work_uri}/jpn@{current_date.isoformat()}",
        work_title=title,
        title=title,
        short_title=title,
        number=info.number,
        work_date=info.promulgation_date,
        language="ja",
        authoritative=False,
        text_origin="official_revised",
        consolidation_kind="change_report",
        version_date=current_date,
        currency_date=None,  # the report attests the current revision as current when it was retrieved
        extent=[JURISDICTION],
        ext_module="jp",
        ext=info.ext(abbrev),
        outstanding_changes=outstanding,
        events=events,
        provisions=[],
        validity_basis="revision_snapshot",
    )


def _parse_revisions(root: Element) -> ParsedDocument:
    info = _law_info(root)
    revisions = [RevisionInfo.from_xml(r) for r in root.findall("revisions/revision")]
    if not revisions:
        raise EgovParseError("no revisions listed")
    current = [r for r in revisions if r.status == "CurrentEnforced"]
    if len(current) != 1:
        raise EgovParseError(f"expected one CurrentEnforced revision, found {len(current)}")
    current_date = _date(current[0].enforcement_date)
    if current_date is None:
        raise EgovParseError("current revision has no enforcement date")

    outstanding: list[dict[str, object]] = []
    events: list[ParsedEvent] = []
    for rev in sorted(revisions, key=lambda r: (r.enforcement_date, r.law_revision_id)):
        enforced = _date(rev.enforcement_date)
        placeholder = _date(rev.scheduled)
        by_uri, by_label = rev.amendment()
        if rev.status == "UnEnforced":
            outstanding.append(
                _outstanding(
                    rev.law_revision_id,
                    _date(rev.promulgate_date),
                    enforced,
                    placeholder,
                    rev.comment,
                    by_uri,
                    by_label,
                )
            )
        elif rev.status in IN_FORCE and enforced is not None and placeholder is None:
            events.append(
                ParsedEvent(
                    "entry_into_force" if rev.original else "amendment",
                    enforced,
                    by_uri,
                    by_label,
                    f"law_revisions/revision[law_revision_id='{rev.law_revision_id}']",
                )
            )
    return _change_report(
        info,
        current_date,
        current[0].law_title,
        current[0].abbrev,
        outstanding,
        events,
        "law_info/promulgation_date",
    )


# --------------------------------------------------------------------------- bulk download list


@dataclass(frozen=True)
class BulkListRow:
    """One line of the bulk download's all_law_list.csv: one revision of one law."""

    fields: dict[str, str]

    @property
    def law_id(self) -> str:
        return self.fields["法令ID"]

    @property
    def pending(self) -> bool:
        return self.fields["未施行"] == PENDING_MARK

    @property
    def revision_id(self) -> str:
        m = _BODY_URL.match(self.fields["本文URL"])
        if not m or m.group(1) != self.law_id:
            raise EgovParseError(f"unexpected 本文URL {self.fields['本文URL']!r} for {self.law_id}")
        return f"{m.group(1)}_{m.group(2)}_{m.group(3)}"

    @property
    def enforcement_date(self) -> date:
        """The date in the revision id (e-Gov's own ISO form; equal to 施行日 on every row of the archive)."""
        day = self.revision_id.split("_")[1]
        return date(int(day[:4]), int(day[4:6]), int(day[6:]))

    @property
    def amendment_law_id(self) -> str:
        amending = self.revision_id.split("_")[2]
        return "" if amending == NO_AMENDMENT else amending


def bulk_list_rows(content: bytes) -> list[BulkListRow]:
    """The rows of all_law_list.csv (or of the lines of it the connector passes for one law), header checked."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EgovParseError(f"the bulk list is not UTF-8: {exc}") from exc
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header is None or tuple(header) != BULK_LIST_COLUMNS:
        raise EgovParseError(f"unexpected bulk list columns {header!r}")
    rows = []
    for values in reader:
        if not values:
            continue
        if len(values) != len(BULK_LIST_COLUMNS) or values[-1] not in ("", PENDING_MARK):
            raise EgovParseError(f"unexpected bulk list row {values!r}")
        rows.append(BulkListRow(dict(zip(BULK_LIST_COLUMNS, values, strict=True))))
    return rows


def _parse_bulk_list(content: bytes, metadata: Mapping[str, Any]) -> ParsedDocument:
    """The change report of the revision in force from one law's lines of the bulk list: rows marked 未施行 are
    outstanding changes; the row of the revision in force (described exactly by the API list's
    current_revision_info) and any other enforced row the archive still lists are lifecycle events."""
    listed = metadata.get("law_info")
    revision = metadata.get("revision_info")
    if not isinstance(listed, Mapping) or not isinstance(revision, Mapping):
        raise EgovParseError("a bulk list needs metadata law_info and revision_info")
    info = _law_info_from(listed)
    current = RevisionInfo.from_metadata(revision)
    current_date = _date(current.enforcement_date)
    if current_date is None or current.status not in IN_FORCE:
        raise EgovParseError(f"{info.law_id}: the list gives no revision in force")
    rows = bulk_list_rows(content)
    if any(r.law_id != info.law_id for r in rows):
        raise EgovParseError(f"the lines are not all of {info.law_id}")
    in_force = [r for r in rows if not r.pending]
    if current.law_revision_id not in {r.revision_id for r in in_force}:
        raise EgovParseError(f"{info.law_id}: revision {current.law_revision_id} is not listed in force")
    if any(r.enforcement_date > current_date for r in in_force):
        raise EgovParseError(f"{info.law_id}: the bulk list has a revision in force later than {current_date}")

    outstanding: list[dict[str, object]] = []
    events: list[ParsedEvent] = []
    for row in sorted(rows, key=lambda r: (r.enforcement_date, r.revision_id)):
        f = row.fields
        locator = f"all_law_list.csv[本文URL='{f['本文URL']}']"
        if row.revision_id == current.law_revision_id:
            by_uri, by_label = current.amendment()
            original = current.original
        else:
            by_uri, by_label = _amendment_label(row.amendment_law_id, f["改正法令名"], f["改正法令番号"])
            original = not row.amendment_law_id
        note = f["施行日備考"]  # filled only where the enforcement date is not fixed yet
        if row.pending:
            outstanding.append(
                _outstanding(
                    row.revision_id,
                    kanji_date(f["改正法令公布日"]),
                    row.enforcement_date,
                    row.enforcement_date if note else None,
                    note,
                    by_uri,
                    by_label,
                )
            )
        elif not note:
            kind = "entry_into_force" if original else "amendment"
            events.append(ParsedEvent(kind, row.enforcement_date, by_uri, by_label, locator))
    return _change_report(
        info,
        current_date,
        current.law_title,
        current.abbrev,
        outstanding,
        events,
        "metadata/law_info/promulgation_date",
    )
