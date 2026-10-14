"""Parser for Luxembourg consolidated legislation: Akoma Ntoso (CSD13) XML from Legilux.

One file is one consolidation (the whole text applicable from a date) of a code, the Constitution, a law
or a regulation, in French. Legilux publishes consolidations as whole texts; the file states the date from
which the text applies (``jolux:dateApplicability``) but no start or end date for individual articles, so the
document is a *revision snapshot* (``validity_basis = "revision_snapshot"``, see ``ulis/ingestion/snapshots.py``):
every provision is dated from the snapshot, and a version starts only when its wording changed.

Reading rules:

* Provisions are articles and, where an article has numbered paragraphs ``(1)``, ``(2)``..., those paragraphs.
  Books, titles, chapters and sections are not provisions; their headings are kept as ``context_heading``.
  The eId is the article's ``id`` attribute (``art_1er``, ``art_l_010-1``), the same in every consolidation.
* Text is the source text. Struck-through text of the consolidation (``<del>`` and elements with
  ``style="del"``) is wording that no longer applies and is not read; empty markers (``<mod>``, ``<noteRef>``)
  carry no text. Amendment references that Legilux prints inside the text, such as ``(L. 4 juillet 2014)``,
  are part of the printed consolidated text and stay in it. Numbered and lettered list items carry their label
  (``1°``, ``a)``) as given by the list's ``symbol``.
* The amendment reference printed beside an article number, footnotes (``noteRef``) and the article's own
  ``dateApplicability`` go in the note, never in the text. An article whose text is only "Abrogé ..." is
  recorded as ``repealed`` with empty text and that wording as note.
* Annexes (``attachments``), the subject index and unnumbered content directly in a chapter are not read
  (see :func:`skipped_blocks`).
* Consolidated texts have no legal value (the authentic text is the one published in the Mémorial A):
  ``authoritative`` is false and ``text_origin`` is ``official_consolidation``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import fromstring

from ulis.ingestion.snapshots import SNAPSHOT_BASIS
from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParsedRef, ParseSkipped

PARSER_VERSION = "1.0.0"
BUNDLE_SCHEMA = "ulis-lu-legilux-bundle/1"
AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0/CSD13"
SCL = "http://www.scl.lu"
ELI_BASE = "http://data.legilux.public.lu"
ELI_LEG = f"{ELI_BASE}/eli/etat/leg/"

CONTAINERS = {"book", "part", "title", "chapter", "section", "subsection", "subchapter"}
# Inline elements whose content is not current text, or that only mark a position.
DROPPED_INLINE = {"del", "mod", "noteRef", "eop", "JOLUXWork", "indexterms"}
INSTRUMENT_TYPES = {
    "constitution": "constitution",
    "code": "code",
    "loi": "law",
    "rgd": "grand_ducal_regulation",
    "rmin": "ministerial_regulation",
    "agd": "grand_ducal_decree",
    "argd": "grand_ducal_decree",
    "amin": "ministerial_decree",
}
FALLBACK_TYPE = "other_act"

_TITLE_PREFIX = re.compile(r"^Version consolidée applicable au \d{2}/\d{2}/\d{4}\s*:\s*", re.I)
REPEALED = re.compile(r"^(?:\(\.\.\.\)\s*)?\(?\s*(?:Abrogés?|Aprogés?|Implicitement abrogés?|Supprimés?)\b", re.I)
DOTS = re.compile(r"^[.…\s]+$")
ROMAN = re.compile(r"^[ivxlcdm]+$", re.I)


class LegiluxParseError(ValueError):
    pass


def _a(tag: str) -> str:
    return f"{{{AKN}}}{tag}"


def _s(tag: str) -> str:
    return f"{{{SCL}}}{tag}"


def _attr(el: Element, name: str) -> str:
    """An attribute that Legilux writes either plain or in the ``scl:`` namespace (``style``, ``symbol``, ``start``)."""
    return el.get(name) or el.get(_s(name)) or ""


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _ws(text: str) -> str:
    """Collapse white space. Space after "(" and before ")", "." and "," is dropped: the XML of different
    consolidations breaks lines inside "(<ref>L. 4 juillet 2014</ref>)" differently, and the same wording must not
    look like a change."""
    return re.sub(r"\s+([).,])", r"\1", re.sub(r"\(\s+", "(", " ".join(text.split())))


def work_uri(path: str) -> str:
    """``loi/2018/08/01/a686/consolide`` -> ``/lu/loi/2018/08/01/a686``; ``code/civil`` -> ``/lu/code/civil``."""
    return "/lu/" + path.removesuffix("/consolide")


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map a Legilux ELI (act, code, consolidation or manifestation) to (ULIS work URI, eId or None)."""
    m = re.match(r"^(?:https?://(?:data\.|eli\.)?legilux\.public\.lu)?/eli/etat/leg/([a-z]{1,12}(?:/[^?#]+)+?)/?$", uri)
    if not m:
        return None
    parts = m.group(1).split("/")
    while len(parts) > 2 and (
        parts[-1] in ("fr", "de", "en", "lb", "xml", "html", "pdf", "jo", "consolide")
        or re.fullmatch(r"\d{8}", parts[-1])
        or parts[-1].startswith("art_")
    ):
        parts.pop()
    return work_uri("/".join(parts)), None


def _inline(el: Element, skip: frozenset[str] = frozenset()) -> str:
    """Text of an element as printed: struck-through text and position markers are left out."""
    parts = [el.text or ""]
    for child in el:
        tag = _local(child.tag)
        if tag == "br":
            parts.append("\n")
        elif (
            tag not in DROPPED_INLINE and tag not in skip and tag not in ("ol", "ul") and _attr(child, "style") != "del"
        ):
            inner = _inline(child, skip)
            if tag == "sup" and len(inner.strip()) <= 4 and "".join(parts).rstrip()[-1:].isalnum():
                # "1<sup>er</sup>": some consolidations break the line before the superscript ("1 er").
                parts = ["".join(parts).rstrip()]
                inner = inner.strip()
            parts.append(f" {inner} " if tag == "p" else inner)
        parts.append(child.tail or "")
    return "".join(parts)


def _label_sequence(symbol: str, start: int, count: int) -> list[str]:
    """Labels of an ordered list from its ``symbol`` (``1°``, ``1.``, ``a)``, ``(1)``, ``i)``...)."""
    m = re.fullmatch(r"(\()?([0-9]+|[a-z]|[A-Z]|[ivxlcdm]+|[IVXLCDM]+)([°.)]*)", symbol)
    if symbol in ("", "none", "custom") or m is None:
        return [""] * count
    opening, kind, closing = m.group(1) or "", m.group(2), m.group(3)

    def label(i: int) -> str:
        n = start + i
        if kind.isdigit():
            body = str(n)
        elif ROMAN.match(kind) and (len(kind) > 1 or kind in "iI"):
            body = _roman(n)
            body = body.upper() if kind.isupper() else body.lower()
        else:
            if n > 26:
                return ""
            body = chr(ord("A" if kind.isupper() else "a") + n - 1)
        return f"{opening}{body}{closing}"

    return [label(i) for i in range(count)]


def _roman(n: int) -> str:
    out = ""
    for value, sign in ((10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= value:
            out += sign
            n -= value
    return out


def _list_lines(lst: Element) -> list[str]:
    items = [li for li in lst if _local(li.tag) == "li" and _attr(li, "style") != "del"]
    if _local(lst.tag) == "ul":
        sign = {"dash": "-", "dot": "•"}.get(_attr(lst, "symbol"), "")
        labels = [sign] * len(items)
    else:
        try:
            start = int(_attr(lst, "start") or "1")
        except ValueError:
            start = 1
        labels = _label_sequence(_attr(lst, "symbol"), start, len(items))
    lines: list[str] = []
    for label, li in zip(labels, items, strict=True):
        label = _attr(li, "symbol") or label
        line = f"{label} {_ws(_inline(li))}".strip()
        if line:
            lines.append(line)
        for sub in li:
            if _local(sub.tag) in ("ol", "ul"):
                lines.extend(_list_lines(sub))
    return lines


def _content_lines(el: Element) -> list[str]:
    """Lines of the ``alinea`` elements directly under ``el`` (paragraphs and list items)."""
    lines: list[str] = []
    for alinea in el:
        if _local(alinea.tag) != "alinea" or _attr(alinea, "style") == "del":
            continue
        for content in alinea:
            if _local(content.tag) != "content":
                continue
            for block in content:
                tag = _local(block.tag)
                if _attr(block, "style") == "del":
                    continue
                if tag == "p":
                    text = _ws(_inline(block))
                    if text:
                        lines.append(text)
                elif tag in ("ol", "ul"):
                    lines.extend(_list_lines(block))
    return lines


def _printed(el: Element, tag: str) -> str:
    child = el.find(_a(tag))
    return _ws(_inline(child)) if child is not None else ""


def _article_label(num: str, art_id: str | None) -> str:
    label = re.sub(r"^(?:Art(?:icle)?\.?)\s*", "", _ws(num), flags=re.I).rstrip(". ").strip()
    if label:
        return label
    return re.sub(r"^art_", "", art_id or "").replace("_", " ").strip()


@dataclass
class _Node:
    element: Element
    eid: str
    kind: str
    num_path: list[str]
    heading: str | None
    context: str | None
    parent: _Node | None
    own: list[str] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class _Reader:
    def __init__(self, note_texts: dict[str, str]) -> None:
        self.note_texts = note_texts
        self.nodes: list[_Node] = []
        self.used: dict[str, int] = {}
        self.skipped: list[str] = []

    def _new(
        self,
        element: Element,
        eid: str,
        kind: str,
        path: list[str],
        heading: str | None,
        context: str | None,
        parent: _Node | None,
    ) -> _Node:
        n = self.used.get(eid, 0) + 1
        self.used[eid] = n
        node = _Node(element, eid if n == 1 else f"{eid}-{n}", kind, path, heading or None, context, parent)
        self.nodes.append(node)
        if parent is not None:
            parent.children.append(node)
        return node

    def walk(self, el: Element, chain: list[str]) -> None:
        for child in el:
            tag = _local(child.tag)
            if tag == "article":
                self.article(child, chain)
            elif tag in CONTAINERS:
                title = _ws(f"{_printed(child, 'num')} {_container_heading(child)}")
                self.walk(child, [*chain, title] if title else chain)
            elif tag in ("num", "heading", "index"):
                continue
            elif tag == "alinea":
                if _content_lines(el):
                    self.skipped.append(f"{el.get('id')}: unnumbered content outside an article")
            elif any(True for _ in child.iter(_a("article"))):
                self.walk(child, chain)  # an unexpected wrapper around articles
            else:
                self.skipped.append(f"{tag} {child.get('id') or ''}".strip())

    def article(self, el: Element, chain: list[str]) -> None:
        if _attr(el, "style") == "del":
            return  # wholly struck-through wording of an earlier version
        art_id = el.get("id")
        label = _article_label(_printed(el, "num"), art_id)
        if not label:
            self.skipped.append(f"article without number ({art_id})")
            return
        heading, amendment = _article_heading(el)
        node = self._new(
            el,
            art_id or f"art_{label}",
            "article",
            [label],
            heading,
            " > ".join(chain) or None,
            None,
        )
        if amendment:
            node.notes.append(amendment)
        node.own = _content_lines(el)
        for child in el:
            tag = _local(child.tag)
            if tag != "paragraph" or _attr(child, "style") == "del":
                continue
            key = re.sub(r"[().\s]", "", _printed(child, "num"))
            lines = _content_lines(child)
            if not key:
                node.own.extend(lines)  # an unnumbered paragraph is part of the article
                continue
            sub = self._new(child, f"{node.eid}/para_{key}", "paragraph", [label, f"para:{key}"], None, None, node)
            sub.own = lines


def _container_heading(el: Element) -> str:
    heading = el.find(_a("heading"))
    return _ws(_inline(heading, frozenset({"docNumber"}))) if heading is not None else ""


def _article_heading(el: Element) -> tuple[str | None, str | None]:
    """The article's own heading, and the amendment reference printed in its heading place, if any."""
    heading = el.find(_a("heading"))
    if heading is None:
        return None, None
    text = _ws(_inline(heading, frozenset({"docNumber"})))
    amendment = _ws(" ".join(_inline(d) for d in heading.iter(_a("docNumber")))) or None
    # "(L. 23 décembre 2022)": the reference of the amending act, not a title.
    if text.startswith("(") and text.endswith(")") and heading.find(f".//{_a('ref')}") is not None:
        return None, text
    return text or None, amendment


def _note_texts(act: Element) -> dict[str, str]:
    return {n.get("id", ""): _ws("".join(n.itertext())) for n in act.iter(_a("note")) if n.get("id")}


def _own_nodes_refs(node: _Node, reader: _Reader) -> tuple[list[str], list[ParsedRef]]:
    """Footnotes and cited acts of a node's own content (not of its child provisions)."""
    skip = {c.element for c in node.children}
    notes: list[str] = []
    refs: list[ParsedRef] = []

    def visit(e: Element) -> None:
        for child in e:
            if (
                child in skip
                or _attr(child, "style") == "del"
                or _local(child.tag) in ("JOLUXWork", "indexterms", "heading")
            ):
                continue
            tag = _local(child.tag)
            if tag == "noteRef":
                text = reader.note_texts.get((child.get("href") or "").lstrip("#"))
                if text and text not in notes:
                    notes.append(text)
            elif tag == "ref":
                href = child.get("href") or ""
                if href.startswith("/eli/etat/leg/"):
                    href = ELI_BASE + href
                if href.startswith(ELI_LEG) and all(r.href != href for r in refs):
                    refs.append(ParsedRef(href=href, text=_ws("".join(child.itertext())), relation="cites"))
            visit(child)

    visit(node.element)
    return notes, refs


def _article_applicability(el: Element) -> str | None:
    for j in el.iter(_s("jolux")):
        if j.get(_s("name")) == "dateApplicability":
            return _ws("".join(j.itertext())) or None
    return None


def _provisions(reader: _Reader, version_date: date, end: date | None) -> list[ParsedProvision]:
    full: dict[str, list[str]] = {}

    def collect(node: _Node) -> list[str]:
        lines = list(node.own)
        for child in node.children:
            lines.extend(collect(child))
        full[node.eid] = lines
        return lines

    for node in reader.nodes:
        if node.parent is None:
            collect(node)

    out: list[ParsedProvision] = []
    for order, node in enumerate(reader.nodes):
        lines = full[node.eid]
        repealed = bool(lines) and all((REPEALED.match(x) or DOTS.match(x)) and len(x) <= 200 for x in lines)
        notes, refs = _own_nodes_refs(node, reader)
        node_notes = [*node.notes, *notes]
        if node.kind == "article":
            applic = _article_applicability(node.element)
            if applic:
                node_notes.append(f"dateApplicability: {applic}")
        if repealed:
            node_notes = [*lines, *node_notes]
        out.append(
            ParsedProvision(
                eid=node.eid,
                kind=node.kind,
                num_path=node.num_path,
                top_kind="article",
                heading=node.heading,
                text="" if repealed else "\n".join(lines),
                parent_eid=node.parent.eid if node.parent else None,
                sort_order=order,
                status="repealed" if repealed else "in_force",
                valid_from=version_date,
                valid_to=end,
                extent=["LU"],
                note=" | ".join(n[:400] for n in node_notes[:6]) or None,
                refs=refs,
                is_leaf=not node.children,
                has_own_content=bool(node.children) and bool(node.own),
                context_heading=node.context,
            )
        )
    return [p for p in out if p.text or p.status == "repealed" or not p.is_leaf]


def _jolux(act: Element) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    meta = act.find(_a("meta"))
    if meta is None:
        return values
    # Only the document's own legal resource and expression; other JOLUX blocks describe related resources.
    for block in (*meta.iter(_s("JOLUXLegalResource")), *meta.iter(_s("JOLUXExpression"))):
        for j in block.iter(_s("jolux")):
            name = j.get(_s("name"))
            text = _ws("".join(j.itertext()))
            if name and text:
                values.setdefault(name, []).append(text)
    return values


def _first(meta: dict[str, list[str]], name: str) -> str | None:
    return meta[name][0] if meta.get(name) else None


def _iso(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def _unbundle(content: bytes) -> tuple[bytes, date | None]:
    """The XML and the consolidation date of a connector bundle; a bare XML file has no date of its own."""
    if not content.lstrip().startswith(b"{"):
        return content, None
    try:
        bundle = json.loads(content)
        if bundle["schema"] != BUNDLE_SCHEMA:
            raise LegiluxParseError(f"unsupported bundle schema {bundle['schema']!r}")
        return bundle["xml"].encode("utf-8"), date.fromisoformat(bundle["consolidation"])
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        if isinstance(exc, LegiluxParseError):
            raise
        raise LegiluxParseError(f"not a Legilux bundle: {exc}") from exc


def parse_legilux(content: bytes) -> list[ParsedDocument]:
    content, consolidation = _unbundle(content)
    try:
        root = fromstring(content, forbid_dtd=True)
    except Exception as exc:
        raise LegiluxParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if root.tag != _a("akomaNtoso"):
        raise LegiluxParseError(f"unsupported root element {root.tag!r}")
    act = root.find(_a("act"))
    if act is None:
        raise LegiluxParseError("only <act> documents are supported")
    ident = act.find(f"{_a('meta')}/{_a('identification')}")
    work_el = ident.find(f"{_a('FRBRWork')}/{_a('FRBRthis')}") if ident is not None else None
    lang_el = ident.find(f"{_a('FRBRExpression')}/{_a('FRBRlanguage')}") if ident is not None else None
    if work_el is None or lang_el is None:
        raise LegiluxParseError("missing FRBRWork/FRBRthis or FRBRExpression/FRBRlanguage")
    if not lang_el.get("language", "").endswith("/FRA"):
        raise LegiluxParseError(f"unsupported language {lang_el.get('language')!r} (French is read)")
    # The work is the code, the Constitution or the act; the date in the ELI is not used (some early consolidations
    # carry another date there than in their file name, and a consolidated law is named by its original act).
    resolved = resolve_uri(work_el.get("value", ""))
    if resolved is None:
        raise LegiluxParseError(f"FRBRWork/FRBRthis {work_el.get('value')!r} is not a Legilux ELI")
    uri = resolved[0]
    path = uri.removeprefix("/lu/")
    meta = _jolux(act)
    stated = _iso(_first(meta, "dateApplicability"))
    # The date of the consolidation is the one in its ELI (carried by the connector's bundle); the file's own
    # jolux:dateApplicability is used only for a bare file. When they differ the file carries another consolidation's
    # metadata: the stated date is kept in ext (metadata_date) and the stated end date is not trusted.
    applic = consolidation or stated
    if applic is None:
        raise LegiluxParseError("missing jolux:dateApplicability")
    mismatch = consolidation is not None and stated != consolidation
    # The last day the text applied (inclusive); provision periods are half-open, so it ends on the next day.
    last_day = None if mismatch else _iso(_first(meta, "dateEndApplicability"))
    end = last_day + timedelta(days=1) if last_day is not None else None
    if end is not None and end <= applic:
        raise LegiluxParseError("jolux:dateEndApplicability is before jolux:dateApplicability")
    act_type = path.split("/")[0]
    instrument_type = INSTRUMENT_TYPES.get(act_type, FALLBACK_TYPE)

    long_title = (meta.get("title") or [""])[0]
    short = (meta.get("titleShort") or [""])[0]
    title = _TITLE_PREFIX.sub("", long_title).rstrip(". ").strip() or short.rstrip(". ").strip()
    preface = act.find(_a("preface"))
    if not title and preface is not None:
        title = _TITLE_PREFIX.sub("", _ws("".join(preface.itertext()))).rstrip(". ").strip()
    if not title:
        raise LegiluxParseError("no title")
    short_title = short.rstrip(". ").strip() or None

    body = act.find(_a("body"))
    if body is None:
        raise LegiluxParseError("missing body")
    reader = _Reader(_note_texts(act))
    reader.walk(body, [])
    if not reader.nodes:
        # Some acts (decisions, statutes) are one unnumbered text; there is no article to cite, so nothing is stored.
        raise ParseSkipped("no numbered articles")

    enacted = _iso(_first(meta, "dateDocument"))
    in_force = _iso(_first(meta, "dateEntryInForce"))
    events = [ParsedEvent("version_change", applic, None, None, "JOLUXLegalResource/dateApplicability")]
    if enacted:
        events.append(ParsedEvent("enactment", enacted, None, None, "JOLUXLegalResource/dateDocument"))
    # For the Constitution Legilux states 1989-04-18 (the coordinated text), which is not an entry into force.
    if in_force and instrument_type not in ("constitution", "code"):
        events.append(ParsedEvent("entry_into_force", in_force, None, None, "JOLUXLegalResource/dateEntryInForce"))
    base = _first(meta, "isMemberOf")
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=instrument_type,
            jurisdiction_id="LU",
            work_uri=uri,
            expression_uri=f"{uri}/fr@{applic.isoformat()}",
            work_title=title,
            title=title,
            short_title=short_title,
            number=None,
            work_date=enacted,
            language="fr",
            authoritative=False,
            text_origin="official_consolidation",
            consolidation_kind="point_in_time",
            version_date=applic,
            currency_date=applic,
            extent=["LU"],
            ext_module="lu",
            ext={
                "eli": ELI_LEG + path,
                "act_type": act_type,
                "base_act": base,
                "entry_into_force": in_force.isoformat() if in_force else None,
                "metadata_date": stated.isoformat() if mismatch and stated else None,
            },
            events=events,
            provisions=_provisions(reader, applic, end),
            validity_basis=SNAPSHOT_BASIS,
        )
    ]


def skipped_blocks(content: bytes) -> list[str]:
    """Blocks the parser does not read (for reports and tests): annexes and unexpected structure."""
    root = fromstring(content, forbid_dtd=True)
    act = root.find(_a("act"))
    if act is None:
        return []
    reader = _Reader({})
    body = act.find(_a("body"))
    if body is not None:
        reader.walk(body, [])
    skipped = list(reader.skipped)
    attachments = act.find(_a("attachments"))
    if attachments is not None:
        skipped.extend(f"annex {a.get('id')}" for a in attachments)
    return skipped
