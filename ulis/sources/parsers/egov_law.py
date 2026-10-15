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

The help page states that where the e-Gov data differs from the Official Gazette (官報), the
Gazette prevails: the text is a revised (consolidated) text, not the authentic one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParseSkipped

PARSER_ID = "egov_law_xml"
PARSER_VERSION = "1.1.0"
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


class EgovParseError(ValueError):
    pass


def _date(value: str | None) -> date | None:
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


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


def law_url(law_id: str) -> str:
    return f"{SITE}/law/{law_id}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map an e-Gov law page URL to (ULIS work URI, eId); only Acts and the Constitution."""
    m = re.match(r"^https://laws\.e-gov\.go\.jp/law/([0-9]{3}(AC|CONSTITUTION)[0-9A-Z]*)/?$", uri)
    if not m:
        return None
    kind = "act" if m.group(2) == "AC" else "constitution"
    return f"/jp/{kind}/{m.group(1)}", None


def _law_info(root: Element) -> LawInfo:
    info = root.find("law_info")
    if info is None:
        raise EgovParseError("missing law_info")
    law_id = (info.findtext("law_id") or "").strip()
    if not re.fullmatch(r"[0-9A-Za-z]+", law_id):
        raise EgovParseError(f"invalid law_id {law_id!r}")
    return LawInfo(
        law_id=law_id,
        law_type=(info.findtext("law_type") or "").strip(),
        law_num=(info.findtext("law_num") or "").strip(),
        promulgation_date=_date(info.findtext("promulgation_date")),
    )


def _amendment(rev: Element) -> tuple[str | None, str | None]:
    """(e-Gov URL of the amending law, "title（number）") for a revision, or (None, None)."""
    law_id = (rev.findtext("amendment_law_id") or "").strip()
    title = inline_text(rev.find("amendment_law_title"))
    num = (rev.findtext("amendment_law_num") or "").strip()
    label = f"{title}（{num}）" if title and num else title or num or None
    return (law_url(law_id) if law_id else None), label


def parse_egov(content: bytes) -> list[ParsedDocument]:
    try:
        root = fromstring(content, forbid_dtd=True)
    except Exception as exc:
        raise EgovParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if root.tag == "law_data_response":
        return [_parse_law_data(root)]
    if root.tag == "law_revisions_response":
        return [_parse_revisions(root)]
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
    rev = root.find("revision_info")
    if rev is None:
        raise EgovParseError("missing revision_info")
    revision_id = (rev.findtext("law_revision_id") or "").strip()
    status = (rev.findtext("current_revision_status") or "").strip()
    if status not in IN_FORCE:
        raise EgovParseError(
            f"revision {revision_id} is {status or 'of unknown status'}: only enforced revisions are law; "
            "amendments not yet in force are reported from the revision list"
        )
    if (rev.findtext("repeal_status") or "None").strip() != "None":
        raise ParseSkipped(f"revision {revision_id}: repealed or expired (廃止・失効); such laws are not ingested")
    if (rev.findtext("amendment_scheduled_enforcement_date") or "").strip():
        # A placeholder date ("not necessarily the actual enforcement date"): never used as a date of law.
        raise EgovParseError(f"revision {revision_id}: enforcement date is a placeholder")
    version_date = _date(rev.findtext("amendment_enforcement_date"))
    if version_date is None:
        raise EgovParseError(f"revision {revision_id}: no enforcement date")
    law = root.find("law_full_text/Law")
    body = law.find("LawBody") if law is not None else None
    if law is None or body is None:
        raise EgovParseError("missing law_full_text/Law/LawBody (request response_format=xml)")
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

    original = (rev.findtext("amendment_type") or "").strip() == "1"
    by_uri, by_label = _amendment(rev)
    abbrev = (rev.findtext("abbrev") or "").strip()
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
                f"revision_info/law_revision_id[.='{revision_id}']",
            )
        ],
        provisions=builder.provisions,
        validity_basis="revision_snapshot",
    )


# --------------------------------------------------------------------------- revision list


def _parse_revisions(root: Element) -> ParsedDocument:
    info = _law_info(root)
    revisions = root.findall("revisions/revision")
    if not revisions:
        raise EgovParseError("no revisions listed")
    current = [r for r in revisions if (r.findtext("current_revision_status") or "").strip() == "CurrentEnforced"]
    if len(current) != 1:
        raise EgovParseError(f"expected one CurrentEnforced revision, found {len(current)}")
    current_date = _date(current[0].findtext("amendment_enforcement_date"))
    if current_date is None:
        raise EgovParseError("current revision has no enforcement date")
    title = inline_text(current[0].find("law_title"))

    outstanding: list[dict[str, object]] = []
    events: list[ParsedEvent] = []
    for rev in sorted(
        revisions, key=lambda r: ((r.findtext("amendment_enforcement_date") or ""), r.findtext("law_revision_id") or "")
    ):
        revision_id = (rev.findtext("law_revision_id") or "").strip()
        status = (rev.findtext("current_revision_status") or "").strip()
        enforced = _date(rev.findtext("amendment_enforcement_date"))
        placeholder = _date(rev.findtext("amendment_scheduled_enforcement_date"))
        comment = inline_text(rev.find("amendment_enforcement_comment"))
        promulgated = _date(rev.findtext("amendment_promulgate_date"))
        by_uri, by_label = _amendment(rev)
        if status == "UnEnforced":
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
            outstanding.append(
                {
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
            )
        elif status in IN_FORCE and enforced is not None and placeholder is None:
            original = (rev.findtext("amendment_type") or "").strip() == "1"
            events.append(
                ParsedEvent(
                    "entry_into_force" if original else "amendment",
                    enforced,
                    by_uri,
                    by_label,
                    f"law_revisions/revision[law_revision_id='{revision_id}']",
                )
            )
    if info.promulgation_date is not None:
        events.append(
            ParsedEvent("promulgation", info.promulgation_date, None, info.law_num, "law_info/promulgation_date")
        )
    abbrev = (current[0].findtext("abbrev") or "").strip()
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
