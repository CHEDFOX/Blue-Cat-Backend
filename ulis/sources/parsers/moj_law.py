"""Parser for laws of the Republic of China (Taiwan) from 全國法規資料庫 (law.moj.gov.tw).

Input: the law's full-text page ``/LawClass/LawAll.aspx?pcode=<PCode>`` (所有條文), HTML. The
site (Ministry of Justice) is an official compilation; its footer states that the data is made
from electronic files or printed text supplied by the competent authorities and that, where it
differs from the text they published, the published text prevails. The authentic text is the
promulgation in the Presidential Office Gazette (總統府公報). Texts are therefore
``authoritative: false``: ``official_revised`` for amended laws, ``official_conversion`` for a
law never amended (the page shows 公布日期 instead of 修正日期).

Format notes (what the page provides and how ULIS uses it):

* header table: 法規名稱 (title), 修正日期 (date the latest amendment was promulgated) or
  公布日期 (promulgation, for a law never amended), 法規類別 (category) and, when some articles
  are not yet in force, 生效狀態 with a memo (``law-vaildMemo``) naming them;
* body (``law-reg-content``): 編/章/節/款/目 headings (``h3 char-1`` … ``char-5``, used as context
  only), a preamble (前言, a row without an article number) and articles (``第 184 條``,
  ``第 21-1 條`` = 第21條之1). Each article is a list of lines. The ``line-NNNN`` class is the
  hanging indent of the line's label, not its level (``十一、`` items are ``line-0006`` like
  ``（一）`` sub-items), so the structure is read from the labels: a line starting with a
  numeral and ``、`` (or, in the Constitution, a numeral and spaces) is a 款 (item), ``（一）``
  a 目 (sub-item), any other line a 項 (paragraph). Paragraphs are not numbered in print;
* identifiers are language-neutral: ``article-184``, ``article-184-paragraph-1``,
  ``article-6-paragraph-1-item-2``, ``article-21_1`` (第21條之1), ``preamble``. An article with a
  single paragraph is one provision (cited 第N條) and its items are ``article-N-item-M``
  (第N條第M款), following Taiwanese citation practice;
* text is the page's text, exactly: an item's own text starts after its label, a paragraph's text
  includes its items as printed; ``（刪除）`` marks a deleted article or item (``repealed``,
  empty text, the marker kept as note);
* **dates:** the page gives no per-article dates. The text is the consolidation up to the
  database's cut-off date (法規整編資料截止日, in the footer): every provision is
  ``attested_at_edition`` (in force on that date, start unknown). Articles named in the 生效狀態
  memo are not in force yet: their text is the amended wording, recorded as ``prospective`` with
  the memo as note; the wording still in force for them is not on this page (it is on the
  site's "old version" page, which robots.txt disallows) and is not recorded.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision

PARSER_ID = "moj_law_html"
PARSER_VERSION = "1.0.0"
SITE = "https://law.moj.gov.tw"
JURISDICTION = "TW"
LANGUAGE = "zh-Hant"
MAX_HTML_BYTES = 32 * 1024 * 1024
DELETED = "（刪除）"
ATTACHMENT = "本條文有附件"

# Conventional names are rendered by the jurisdiction module; the instrument type follows the
# statutory naming rules (中央法規標準法 arts 2-3: 法律 are named 法/律/條例/通則; 命令 are named
# 規程/規則/細則/辦法/綱要/標準/準則).
CONSTITUTIONAL = {"中華民國憲法", "中華民國憲法增修條文"}
ORDER_SUFFIXES = ("規程", "規則", "細則", "辦法", "綱要", "標準", "準則")
ACT_SUFFIXES = ("法", "律", "條例", "通則")

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
NUMERALS = "零〇一二三四五六七八九十百"
IDEOGRAPHIC_SPACE = chr(0x3000)
# 一、 (laws) or 一 followed by an ideographic space or one or two spaces (the Constitution)
_ITEM = re.compile(rf"^([{NUMERALS}]+)(、|{IDEOGRAPHIC_SPACE}+| {{1,2}})(?=\S)")
_SUBITEM = re.compile(rf"^[（(]([{NUMERALS}]+)[）)]")
_ARABIC_LIST = re.compile(r"^[0-9０-９]+[.、．)）]")
_ARTICLE = re.compile(r"^第\s*([0-9]+(?:-[0-9]+)?)\s*條$")
_ROC_DATE = re.compile(r"民國\s*([0-9]+)\s*年\s*([0-9]+)\s*月\s*([0-9]+)\s*日")
_CUTOFF = re.compile(r"法規整編資料截止日：\s*(民國\s*[0-9]+\s*年\s*[0-9]+\s*月\s*[0-9]+\s*日)")
# "第 1-1、12、22～26 條" in the 生效狀態 memo
_MEMO_ARTICLES = re.compile(r"第\s*([0-9]+(?:-[0-9]+)?(?:\s*[、～~]\s*[0-9]+(?:-[0-9]+)?)*)\s*條")
_WS = " \t\r\n"


class MojParseError(ValueError):
    pass


# --------------------------------------------------------------------------- minimal HTML tree


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    children: list[Node | str] = field(default_factory=list)

    @property
    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()

    def elements(self) -> list[Node]:
        return [c for c in self.children if isinstance(c, Node)]

    def iter(self) -> list[Node]:
        out: list[Node] = [self]
        for c in self.elements():
            out.extend(c.iter())
        return out

    def find(self, predicate: Callable[[Node], bool]) -> Node | None:
        return next((n for n in self.iter() if predicate(n)), None)

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack[-1].children.append(Node(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def parse_html(content: bytes) -> Node:
    builder = _TreeBuilder()
    builder.feed(content.decode("utf-8"))
    builder.close()
    return builder.root


def _has_class(name: str) -> Callable[[Node], bool]:
    return lambda n: name in n.classes


def _clean(text: str) -> str:
    return text.strip(_WS)


def _heading(text: str) -> str:
    return " ".join(text.split())


# --------------------------------------------------------------------------- values


def roc_date(text: str) -> date | None:
    """``民國 115 年 10 月 02 日`` -> 2026-10-02 (Minguo year + 1911)."""
    m = _ROC_DATE.search(text)
    if not m:
        return None
    try:
        return date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def numeral(text: str) -> int:
    """Chinese numerals as used for items: 一 1, 十一 11, 二十 20, 一百零一 101, 一百十 110."""
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    total, current = 0, 0
    for ch in text:
        if ch in digits:
            current = digits[ch]
        elif ch == "十":
            total += (current or 1) * 10
            current = 0
        elif ch == "百":
            total += (current or 1) * 100
            current = 0
        else:
            raise MojParseError(f"not a numeral: {text!r}")
    return total + current


def article_key(number: str) -> str:
    """``21-1`` (第21條之1) -> ``21_1``, the language-neutral form used in eIds."""
    return number.replace("-", "_")


def instrument_type(title: str) -> str:
    if title in CONSTITUTIONAL:
        return "constitution"
    if title.endswith(ORDER_SUFFIXES):
        return "regulation"
    if title.endswith(ACT_SUFFIXES):
        return "act"
    raise MojParseError(f"cannot tell the kind of {title!r} from its name")


# --------------------------------------------------------------------------- structure


@dataclass
class _Line:
    kind: str  # paragraph | item | subitem
    number: int | None
    printed: str  # the whole line as printed
    own: str  # the line without its label


def _classify(text: str) -> _Line:
    if _ARABIC_LIST.match(text):
        raise MojParseError(f"unsupported list level: {text[:20]!r}")
    m = _SUBITEM.match(text)
    if m:
        return _Line("subitem", numeral(m.group(1)), text, _clean(text[m.end() :]))
    m = _ITEM.match(text)
    if m:
        return _Line("item", numeral(m.group(1)), text, _clean(text[m.end() :]))
    return _Line("paragraph", None, text, text)


@dataclass
class _Item:
    line: _Line
    subitems: list[_Line] = field(default_factory=list)

    def printed(self) -> list[str]:
        return [self.line.printed, *(s.printed for s in self.subitems)]


@dataclass
class _Paragraph:
    line: _Line | None
    items: list[_Item] = field(default_factory=list)

    def printed(self) -> list[str]:
        head = [self.line.printed] if self.line else []
        return head + [p for item in self.items for p in item.printed()]


def _paragraphs(lines: list[str], where: str) -> list[_Paragraph]:
    paragraphs: list[_Paragraph] = []
    for text in lines:
        line = _classify(text)
        if line.kind == "paragraph":
            paragraphs.append(_Paragraph(line))
            continue
        if not paragraphs:  # an article that starts with an item
            paragraphs.append(_Paragraph(None))
        para = paragraphs[-1]
        if line.kind == "item":
            expected = len(para.items) + 1
            if line.number != expected:
                raise MojParseError(f"{where}: item {line.number} where item {expected} was expected")
            para.items.append(_Item(line))
        else:
            if not para.items:
                raise MojParseError(f"{where}: sub-item {text[:12]!r} outside an item")
            item = para.items[-1]
            expected = len(item.subitems) + 1
            if line.number != expected:
                raise MojParseError(f"{where}: sub-item {line.number} where {expected} was expected")
            item.subitems.append(line)
    return paragraphs


class _Builder:
    def __init__(self, prospective: dict[str, str]):
        self.prospective = prospective  # article number -> memo note
        self.provisions: list[ParsedProvision] = []
        self._used: set[str] = set()

    def add(
        self,
        *,
        eid: str,
        kind: str,
        num_path: list[str],
        parent_eid: str | None,
        text: str,
        own: str,
        has_children: bool,
        context: str | None,
        status: str,
        note: str | None,
        top_kind: str = "article",
    ) -> None:
        if eid in self._used:
            raise MojParseError(f"duplicate provision {eid}")
        self._used.add(eid)
        deleted = own == DELETED and not has_children
        if deleted:
            status = "repealed" if status == "in_force" else status
            note = DELETED + (f" {note}" if note else "")
        self.provisions.append(
            ParsedProvision(
                eid=eid,
                kind=kind,
                num_path=num_path,
                top_kind=top_kind,
                heading=None,
                text="" if deleted else text,
                parent_eid=parent_eid,
                sort_order=len(self.provisions),
                status=status,  # type: ignore[arg-type]
                valid_from=None,
                valid_to=None,
                extent=[JURISDICTION],
                note=note,
                refs=[],
                is_leaf=not has_children,
                has_own_content=bool(own) and has_children and not deleted,
                context_heading=context,
            )
        )

    def items(
        self, para: _Paragraph, parent: str, path: list[str], ctx: str | None, status: str, note: str | None
    ) -> None:
        for item in para.items:
            n = str(item.line.number)
            eid = f"{parent}-item-{n}"
            self.add(
                eid=eid,
                kind="item",
                num_path=[*path, n],
                parent_eid=parent,
                text="\n".join([item.line.own, *(s.printed for s in item.subitems)]),
                own=item.line.own,
                has_children=bool(item.subitems),
                context=ctx,
                status=status,
                note=note,
            )
            for sub in item.subitems:
                k = str(sub.number)
                self.add(
                    eid=f"{eid}-subitem-{k}",
                    kind="subitem",
                    num_path=[*path, n, k],
                    parent_eid=eid,
                    text=sub.own,
                    own=sub.own,
                    has_children=False,
                    context=ctx,
                    status=status,
                    note=note,
                )

    def article(self, number: str, lines: list[str], ctx: str | None, attachment: bool) -> None:
        key = article_key(number)
        eid = f"article-{key}"
        where = f"第 {number} 條"
        paragraphs = _paragraphs(lines, where)
        if not paragraphs:
            raise MojParseError(f"{where}: no text")
        memo = self.prospective.get(number)
        status = "prospective" if memo is not None else "in_force"
        notes = [n for n in (memo, f"{ATTACHMENT} (not ingested)" if attachment else None) if n]
        note = " ".join(notes) or None
        printed = [p for para in paragraphs for p in para.printed()]
        if len(paragraphs) == 1:
            para = paragraphs[0]
            own = para.line.own if para.line else ""
            self.add(
                eid=eid,
                kind="article",
                num_path=[number],
                parent_eid=None,
                text="\n".join(printed),
                own=own,
                has_children=bool(para.items),
                context=ctx,
                status=status,
                note=note,
            )
            self.items(para, eid, [number], ctx, status, memo)
            return
        self.add(
            eid=eid,
            kind="article",
            num_path=[number],
            parent_eid=None,
            text="\n".join(printed),
            own="",
            has_children=True,
            context=ctx,
            status=status,
            note=note,
        )
        for p, para in enumerate(paragraphs, start=1):
            if para.line is None:
                raise MojParseError(f"{where}: items before the first paragraph of a multi-paragraph article")
            peid = f"{eid}-paragraph-{p}"
            self.add(
                eid=peid,
                kind="paragraph",
                num_path=[number, str(p)],
                parent_eid=eid,
                text="\n".join(para.printed()),
                own=para.line.own,
                has_children=bool(para.items),
                context=ctx,
                status=status,
                note=memo,
            )
            self.items(para, peid, [number, str(p)], ctx, status, memo)

    def preamble(self, text: str) -> None:
        self.add(
            eid="preamble",
            kind="preamble",
            num_path=[],
            parent_eid=None,
            text=text,
            own=text,
            has_children=False,
            context=None,
            status="in_force",
            note=None,
            top_kind="preamble",
        )


# --------------------------------------------------------------------------- not yet in force


def _expand(spec: str, order: list[str]) -> list[str]:
    """``1-1、12、22～26`` -> article numbers; a range runs in document order, both ends included."""
    out: list[str] = []
    for part in re.split(r"\s*、\s*", spec.strip()):
        ends = [s.strip() for s in re.split(r"[～~]", part)]
        if len(ends) == 1:
            out.append(ends[0])
            continue
        first, last = ends[0], ends[-1]
        if len(ends) != 2 or first not in order or last not in order or order.index(first) > order.index(last):
            raise MojParseError(f"生效狀態: cannot resolve the range {part!r}")
        out.extend(order[order.index(first) : order.index(last) + 1])
    return out


def not_in_force(memo_items: list[str], order: list[str]) -> dict[str, str]:
    """Article number -> the memo item(s) stating that its current wording is not in force yet."""
    out: dict[str, str] = {}
    for item in memo_items:
        found = [n for spec in _MEMO_ARTICLES.findall(item) for n in _expand(spec, order)]
        if not found:
            raise MojParseError(f"生效狀態: no article named in {item!r}; the law cannot be dated safely")
        for number in found:
            if number not in order:
                raise MojParseError(f"生效狀態: article {number} is not in the text")
            out[number] = f"{out[number]} {item}" if number in out else item
    return out


# --------------------------------------------------------------------------- document


def _header(root: Node) -> dict[str, Node]:
    table = root.find(_has_class("table-title"))
    if table is None:
        raise MojParseError("missing header table (table-title)")
    out: dict[str, Node] = {}
    for tr in (n for n in table.iter() if n.tag == "tr"):
        th = next((c for c in tr.elements() if c.tag == "th"), None)
        td = next((c for c in tr.elements() if c.tag == "td"), None)
        if th is not None and td is not None:
            out[re.sub(r"[\s：:]", "", th.text())] = td
    return out


def _memo_items(cell: Node) -> list[str]:
    memo = cell.find(_has_class("law-vaildMemo"))
    if memo is None:
        raise MojParseError("生效狀態 without a memo naming the articles")
    parts: list[list[str]] = [[]]
    for child in memo.children:
        if isinstance(child, Node) and child.tag == "br":
            parts.append([])
        else:
            parts[-1].append(child if isinstance(child, str) else child.text())
    items = [" ".join("".join(p).split()) for p in parts]
    return [re.sub(r"^\d+\.\s*", "", i) for i in items if i]


@dataclass
class _Row:
    kind: str  # heading | preamble | article
    value: str = ""  # heading text or article number
    level: int = 0  # heading level (char-N)
    lines: list[str] = field(default_factory=list)
    attachment: bool = False


def _rows(body: Node) -> list[_Row]:
    """The law text in document order: headings, the preamble and articles with their lines."""
    rows: list[_Row] = []
    for el in body.elements():
        if "h3" in el.classes:
            level = next((int(c[5:]) for c in el.classes if re.fullmatch(r"char-[1-5]", c)), None)
            if level is None:
                raise MojParseError(f"heading without a level: {el.classes}")
            rows.append(_Row("heading", _heading(el.text()), level))
            continue
        if "row" not in el.classes:
            raise MojParseError(f"unexpected element in the law text: {el.tag} {el.classes}")
        col_no = next((c for c in el.elements() if "col-no" in c.classes), None)
        data = el.find(_has_class("law-article"))
        if col_no is None or data is None:
            raise MojParseError("article row without number or text")
        lines = [_clean(n.text()) for n in data.elements() if any(c.startswith("line-") for c in n.classes)]
        if not lines:  # the preamble is a block of text without line divisions
            lines = [_clean(data.text())]
        lines = [line for line in lines if line]
        anchor = next((n for n in col_no.iter() if n.tag == "a"), None)
        if anchor is None:
            if any(r.kind == "article" for r in rows):
                raise MojParseError("unnumbered row after the first article")
            rows.append(_Row("preamble", lines=lines))
            continue
        m = _ARTICLE.match(" ".join(anchor.text().split()))
        if not m:
            raise MojParseError(f"unexpected article number {anchor.text()!r}")
        rows.append(_Row("article", m.group(1), lines=lines, attachment=ATTACHMENT in col_no.text()))
    return rows


def parse_moj_law(content: bytes) -> list[ParsedDocument]:
    if len(content) > MAX_HTML_BYTES:
        raise MojParseError("page too large")
    try:
        root = parse_html(content)
    except UnicodeDecodeError as exc:
        raise MojParseError(f"not UTF-8: {exc}") from exc
    name_el = root.find(lambda n: n.attrs.get("id") == "hlLawName")
    body = root.find(_has_class("law-reg-content"))
    if name_el is None or body is None:
        raise MojParseError("not a law full-text page (no hlLawName / law-reg-content)")
    title = _clean(name_el.text())
    href = name_el.attrs.get("href", "")
    pm = re.search(r"pcode=([A-Z][0-9]{7})", href)
    if not title or pm is None:
        raise MojParseError("missing law name or PCode")
    pcode = pm.group(1)
    header = _header(root)
    if "廢止日期" in header:
        raise MojParseError(f"{title}: repealed laws (廢止) are not supported")
    amended = roc_date(header["修正日期"].text()) if "修正日期" in header else None
    promulgated = roc_date(header["公布日期"].text()) if "公布日期" in header else None
    if amended is None and promulgated is None:
        raise MojParseError(f"{title}: neither 修正日期 nor 公布日期 is stated")
    category = " ".join(header["法規類別"].text().split()) if "法規類別" in header else None
    cutoff_m = _CUTOFF.search(root.text())
    cutoff = roc_date(cutoff_m.group(1)) if cutoff_m else None
    if cutoff is None:
        raise MojParseError("missing 法規整編資料截止日 (consolidation cut-off date)")

    rows = _rows(body)
    order = [r.value for r in rows if r.kind == "article"]
    if not order:
        raise MojParseError("no articles found")
    if len(order) != len(set(order)):
        raise MojParseError("an article number occurs twice")
    pending = not_in_force(_memo_items(header["生效狀態"]), order) if "生效狀態" in header else {}

    builder = _Builder(pending)
    levels: dict[int, str] = {}  # open headings: 1 編, 2 章, 3 節, 4 款, 5 目
    for row in rows:
        if row.kind == "heading":
            for deeper in [k for k in levels if k >= row.level]:
                del levels[deeper]
            levels[row.level] = row.value
        elif row.kind == "preamble":
            builder.preamble("\n".join(row.lines))
        else:
            context = " ".join(levels[k] for k in sorted(levels)) or None
            builder.article(row.value, row.lines, context, row.attachment)

    work_type = instrument_type(title)
    work_uri = f"/tw/{work_type}/{pcode}"
    events = [ParsedEvent("version_change", cutoff, None, "法規整編資料截止日 (consolidation cut-off date)", "footer")]
    if amended is not None:
        events.insert(
            0, ParsedEvent("amendment", amended, None, "修正日期 (latest amendment promulgated)", "header/修正日期")
        )
    if promulgated is not None:
        events.insert(0, ParsedEvent("promulgation", promulgated, None, "公布日期", "header/公布日期"))
    ext: dict[str, object] = {"pcode": pcode}
    if category:
        ext["category"] = category
    if amended is not None:
        ext["amended_on"] = amended.isoformat()
    never_amended = amended is None
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=work_type,
            jurisdiction_id=JURISDICTION,
            work_uri=work_uri,
            expression_uri=f"{work_uri}/zho@{cutoff.isoformat()}",
            work_title=title,
            title=title,
            short_title=title,
            number=None,
            # Promulgation date only when the page states it (a law never amended); the original
            # promulgation of an amended law is in its history (沿革), which this page does not carry.
            work_date=promulgated,
            language=LANGUAGE,
            authoritative=False,  # the Presidential Office Gazette is authentic; the database yields to it
            text_origin="official_conversion" if never_amended else "official_revised",
            consolidation_kind="as_made" if never_amended else "current_consolidation",
            version_date=cutoff,
            currency_date=cutoff,
            extent=[JURISDICTION],
            ext_module="tw",
            ext=ext,
            outstanding_changes=[],
            events=events,
            provisions=builder.provisions,
            validity_basis="attested_at_edition",
        )
    ]
