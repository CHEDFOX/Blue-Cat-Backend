"""Parser for Swiss federal law published on Fedlex (Akoma Ntoso 3.0, Federal Chancellery).

Each file is one language version (German, French or Italian) of one consolidation of the
Classified Compilation (SR): the whole enactment as it applies from the consolidation's date of
applicability (``FRBRdate name="jolux:dateApplicability"``, printed "Stand am" / "État le" /
"Stato"). Under the Publications Act (PublA, SR 170.512) the version published in the Official
Compilation (AS) is authoritative (Art. 15); the SR is a consolidated, continuously updated
collection of the AS texts (Art. 11). German, French and Italian enactments are equally binding
(Art. 14 para. 1).

How the file is read:

* **Provisions** are articles, numbered paragraphs (Abs. / al. / cpv.) and labelled list items
  (lit. / let. / lett., Ziff. / ch. / n.). ULIS eIds are built from the printed labels
  (``art_10a``, ``art_8/para_2``, ``art_5/para_1/lbl_c/lbl_1``), not taken from the file, whose
  eId conventions changed between consolidations (``lbl_abis`` / ``lbl_a_bis``) and which numbers
  unlabelled elements by position. An unnumbered paragraph (a single-paragraph article) is part
  of its article. Articles divided into unnumbered subdivisions (Art. 196 and 197 BV) are one
  provision each.
* **Text is exact source text** without footnotes. Footnotes (amendment history,
  cross-references) are editorial and go to ``note``.
* **Repealed** provisions are printed as "…" with a footnote ("Aufgehoben ... mit Wirkung seit
  1. Jan. 2016"); the repeal date is taken from that footnote, or left unknown.
* **Validity**: a consolidation states the text applicable from its date, so every in-force
  provision starts on the consolidation date (``validity_basis = consolidation_series``). The
  pipeline continues a version whose wording is unchanged in a later consolidation and ends the
  versions of provisions absent from it. Dates before the earliest indexed consolidation are a
  history gap.
* **Annexes** are one provision each. The final title of the Civil Code (Schlusstitel) is
  read; other final and transitional provisions (``proviso`` / ``transitional`` blocks, such as
  the Schlussbestimmungen of the Code of Obligations, each with its own Art. 1) and the subject
  index of the Constitution are not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParsedRef

AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
FEDLEX = "http://fedlex.admin.ch/"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
PARSER_ID = "fedlex_akn"
PARSER_VERSION = "1.0.0"
ELI_CC = "https://fedlex.data.admin.ch/eli/cc/"
LANGUAGES = ("de", "fr", "it")
VALIDITY_BASIS = "consolidation_series"

# Structural divisions: walked through, never provisions.
CONTAINERS = {"book", "part", "title", "subtitle", "chapter", "subchapter", "section", "subsection", "level"}
TEXT_BLOCKS = {"p", "listIntroduction", "block", "docTitle"}
LABELLED = {"paragraph", "item", "subdivision", "level", "article"}
SUFFIX = r"(?:bis|ter|quater|quinquies|sexies|septies|octies|novies|nonies|decies)"
_NUM = rf"\d+[a-z]{{0,2}}{SUFFIX}?(?:[a-z]{SUFFIX}?)?"
ITEM_LABEL = re.compile(rf"^(?:[a-z]{{1,3}}{SUFFIX}?|{_NUM}(?:-{_NUM})?)$")
PARA_LABEL = re.compile(rf"^{_NUM}(?:-{_NUM})?$")
ARTICLE_LABEL = PARA_LABEL
DOTS = re.compile(r"^[.…\s]*$")
# Repealed text: "…", or the word itself in some versions ("2 Abrogé").
REPEALED_TEXT = re.compile(r"^(?:[.…\s]*|(?:Aufgehoben|Abrogée?s?|Abrogat[oaie])\.?)$")
DASHES = re.compile(r"[\u2010-\u2015\u2212]")
FINAL_TITLE = re.compile(r"^(?:Schlusstitel|Titre final|Titolo finale)\b")
FEDERAL_ASSEMBLY = re.compile(
    r"^(?:Die Bundesversammlung der Schweizerischen Eidgenossenschaft"
    r"|L[’']Assemblée fédérale de la Confédération suisse"
    r"|L[’']Assemblea federale della Confederazione Svizzera)"
)
# Enactments of the Federal Assembly are federal acts, ordinances or federal decrees (Art. 163 BV).
NOT_ACTS = re.compile(r"^(?:Verordnung|Bundesbeschluss|Ordonnance|Arrêté fédéral|Ordinanza|Decreto federale)\b")

# Repeal footnotes: "Aufgehoben durch ..., mit Wirkung seit 1. Jan. 2016", "Abrogé par ..., avec effet
# au 1er janv. 2016", "Abrogato dal ..., con effetto dal 1° gen. 2016". Provisions that have become
# obsolete ("Gegenstandslos" / "Sans objet" / "Privo d'oggetto") are printed the same way.
REPEAL_NOTE = re.compile(r"\b(?:Aufgehoben|Abrogée?s?|Abrogat[oaie]|Gegenstandslos|Sans objet|Privo d[’']oggetto)\b")
_DATE = r"(\d{1,2})(?:\.|er|°)?\s+([^\s\d]+)\s+(\d{4})"
EFFECT = re.compile(
    r"(?:mit Wirkung(?: seit| ab| auf den| am)*|avec effet(?: au| le| dès le| depuis le| à partir du)?"
    rf"|con effetto(?: dal| a contare dal| il| al)?)\s+{_DATE}"
)
# Some repeal footnotes give the date the repealing act came into force instead.
IN_FORCE = re.compile(rf"(?:in Kraft seit|en vigueur depuis le|in vigore dal)\s+{_DATE}")
MONTHS = {
    # German
    "jan.": 1, "januar": 1, "febr.": 2, "feb.": 2, "februar": 2, "märz": 3, "april": 4, "apr.": 4, "mai": 5,
    "juni": 6, "juli": 7, "aug.": 8, "august": 8, "sept.": 9, "sep.": 9, "september": 9, "okt.": 10,
    "oktober": 10, "nov.": 11, "november": 11, "dez.": 12, "dezember": 12,
    # French
    "janv.": 1, "janvier": 1, "fév.": 2, "févr.": 2, "février": 2, "mars": 3, "avr.": 4, "avril": 4,
    "juin": 6, "juil.": 7, "juillet": 7, "août": 8, "septembre": 9, "oct.": 10, "octobre": 10,
    "novembre": 11, "déc.": 12, "décembre": 12,
    # Italian
    "gen.": 1, "gennaio": 1, "febbraio": 2, "mar.": 3, "marzo": 3, "aprile": 4, "mag.": 5, "maggio": 5,
    "giu.": 6, "giugno": 6, "lug.": 7, "luglio": 7, "ago.": 8, "agosto": 8, "set.": 9, "settembre": 9,
    "ott.": 10, "ottobre": 10, "dic.": 12, "dicembre": 12,
}  # fmt: skip


class FedlexParseError(ValueError):
    pass


def _a(tag: str) -> str:
    return f"{{{AKN}}}{tag}"


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _flat(el: Element) -> str:
    """Inline text of an element, without footnotes and conversion placeholders ("[tab]")."""
    parts = [el.text or ""]
    for child in el:
        tag = _local(child.tag)
        if tag == "br":
            parts.append(" ")
        elif tag not in ("authorialNote", "placeholder"):
            parts.append(_flat(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _printed(el: Element, tag: str) -> str:
    found = el.find(_a(tag))
    return _ws(_flat(found)) if found is not None else ""


def _key(label: str) -> str:
    """Language-neutral label key: ``a<sup>bis</sup>.`` -> ``abis``, ``Art. 2–4`` -> ``2-4``."""
    label = re.sub(r"^(?:Art\.?|Artikel|Article|Articolo)\s*", "", label.strip(), flags=re.I)
    return re.sub(r"[^0-9a-z-]", "", DASHES.sub("-", label).lower()).strip("-")


def _group_key(eid: str) -> str:
    """``art_28_d_28_f`` -> ``28d-28f``, ``para_3_5`` -> ``3-5`` (first and last of a group)."""
    labels: list[str] = []
    for token in re.sub(r"^[a-z]+_", "", eid.rsplit("/", 1)[-1]).split("_"):
        if token.isdigit() or not labels:
            labels.append(token)
        else:
            labels[-1] += token
    return "-".join(labels) if len(labels) == 2 else ""


def label_key(label: str, eid: str | None) -> str:
    """Key of a printed article, paragraph or item label. Groups printed in words ("Art. 50 und 51",
    "3 à 5", "37 a 39") take their key from the file's eId, which is the same in every language."""
    if len(re.findall(r"\d+", label)) > 1:
        return _group_key(eid or "")
    return _key(label)


def repeal_date(note: str) -> date | None:
    """The date a repeal took effect, as stated in a Fedlex repeal footnote (de/fr/it)."""
    repeal = REPEAL_NOTE.search(note)
    if repeal is None:
        return None
    m = EFFECT.search(note, repeal.end()) or IN_FORCE.search(note, repeal.end())
    month = MONTHS.get(m.group(2).lower()) if m else None
    if m is None or month is None:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(1)))
    except ValueError:
        return None


def work_uri(eli: str) -> str:
    """``https://fedlex.data.admin.ch/eli/cc/1999/404`` -> ``/ch/cc/1999/404``."""
    return "/ch/cc/" + eli.removeprefix(ELI_CC).strip("/")


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map a Fedlex SR consolidation URI (``eli/cc/<year>/<number>``) to (ULIS work URI, eId)."""
    m = re.match(
        r"^https?://(?:fedlex\.data\.admin\.ch|www\.fedlex\.admin\.ch)/eli/cc/([0-9A-Za-z]+)/([0-9a-z_]+)/?$", uri
    )
    return (f"/ch/cc/{m.group(1)}/{m.group(2)}", None) if m else None


@dataclass
class _Node:
    """A provision while the document is read."""

    element: Element
    eid: str
    kind: str
    num_path: list[str]
    heading: str | None
    parent: _Node | None
    whole: bool = False  # read as one unit (no child provisions)
    children: list[_Node] = field(default_factory=list)


def _is_child_provision(el: Element) -> bool:
    tag = _local(el.tag)
    key = label_key(_printed(el, "num"), el.get("eId"))
    if tag == "paragraph":
        return bool(PARA_LABEL.match(key))
    if tag == "item":
        return bool(el.get("eId")) and bool(ITEM_LABEL.match(key))
    return False


class _Reader:
    def __init__(self) -> None:
        self.nodes: list[_Node] = []
        self.provision_elements: set[Element] = set()
        self.used: dict[str, int] = {}
        self.skipped: list[str] = []

    def _new(
        self, element: Element, eid: str, kind: str, path: list[str], heading: str | None, parent: _Node | None
    ) -> _Node:
        n = self.used.get(eid, 0) + 1
        self.used[eid] = n
        node = _Node(element, eid if n == 1 else f"{eid}-{n}", kind, path, heading or None, parent)
        self.nodes.append(node)
        self.provision_elements.add(element)
        if parent is not None:
            parent.children.append(node)
        return node

    # ------------------------------------------------------------------ structure
    def walk(self, el: Element, prefix: str, path: list[str], marginal: str | None) -> None:
        for child in el:
            tag = _local(child.tag)
            if tag == "article":
                self.article(child, prefix, path, marginal)
            elif tag in CONTAINERS:
                num, head = _printed(child, "num"), _printed(child, "heading")
                title = _ws(f"{num} {head}")
                is_marginal = tag == "level" and bool(title)
                self.walk(child, prefix, path, title if is_marginal else marginal)
            elif tag in ("proviso", "transitional"):
                head = _printed(child, "heading")
                if FINAL_TITLE.match(head) and not prefix:
                    self.walk(child, "final_title/", ["final_title"], None)
                else:
                    self.skipped.append(f"{child.get('eId')}: {head[:80]}")
            elif tag in ("content", "hcontainer", "intro", "wrapUp"):
                self.walk(child, prefix, path, marginal)

    def article(self, el: Element, prefix: str, path: list[str], marginal: str | None) -> None:
        label = _printed(el, "num")
        key = label_key(label, el.get("eId"))
        if not ARTICLE_LABEL.match(key):
            raise FedlexParseError(f"unexpected article label {label!r} ({el.get('eId')})")
        node = self._new(el, f"{prefix}art_{key}", "article", [*path, key], _printed(el, "heading") or marginal, None)
        if el.find(f".//{_a('subdivision')}") is not None:
            # Unnumbered subdivisions (transitional provisions of Art. 196/197 BV) have no stable,
            # language-neutral label: the article is read as one provision.
            node.whole = True
            return
        self._children(el, node)

    def _children(self, el: Element, node: _Node) -> None:
        for child in el:
            tag = _local(child.tag)
            if tag in ("num", "heading", "authorialNote") or tag in TEXT_BLOCKS:
                continue
            if tag in ("paragraph", "item") and _is_child_provision(child):
                key = label_key(_printed(child, "num"), child.get("eId"))
                if tag == "paragraph":
                    kind, eid, token = "paragraph", f"{node.eid}/para_{key}", f"para:{key}"
                elif key[:1].isdigit():
                    kind, eid, token = "number", f"{node.eid}/lbl_{key}", f"num:{key}"
                else:
                    kind, eid, token = "letter", f"{node.eid}/lbl_{key}", f"let:{key}"
                self._children(child, self._new(child, eid, kind, [*node.num_path, token], None, node))
            else:  # content, blockList, unnumbered paragraph, bullet item, table, ...
                self._children(child, node)

    def annex(self, doc: Element) -> None:
        body = doc.find(_a("mainBody"))
        if body is None:
            return
        block = next((b for b in doc.iter(_a("block")) if b.get("name") == "num"), None)
        m = re.search(r"(\d+[a-z]?)$", _ws(_flat(block)) if block is not None else "")
        key = m.group(1) if m else ""
        head = next(body.iter(_a("heading")), None)
        node = self._new(
            body,
            f"annex_{key}" if key else "annex",
            "annex",
            [f"annex:{key}"],
            _ws(_flat(head)) if head is not None else None,
            None,
        )
        node.whole = True

    # ------------------------------------------------------------------ text
    def lines(self, el: Element, skip: set[Element] | None, own_meta: bool = False) -> list[str]:
        """Text lines of ``el``'s content; ``el``'s own num and heading are excluded unless
        ``own_meta``. Child provisions in ``skip`` are left out (their text is their own)."""
        out: list[str] = []

        def emit(text: str | None) -> None:
            if text and text.strip():
                out.append(_ws(text))

        emit(el.text)
        for child in el:
            tag = _local(child.tag)
            if tag == "authorialNote" or (skip is not None and child in skip):
                pass
            elif tag in ("num", "heading"):
                if own_meta:
                    emit(_flat(child))
            elif tag in TEXT_BLOCKS:
                emit(_flat(child))
            elif tag == "table":
                for row in child.iter(_a("tr")):
                    cells = [_ws(_flat(cell)) for cell in row if _local(cell.tag) in ("td", "th")]
                    if any(cells):
                        out.append(" | ".join(cells))
            elif tag in LABELLED:
                label, head = _printed(child, "num"), _printed(child, "heading")
                body = self.lines(child, skip)
                if head:
                    out.append(_ws(f"{label} {head}"))
                    label = ""
                if body:
                    body[0] = _ws(f"{label} {body[0]}")
                    out.extend(body)
                elif label:
                    out.append(label)
            else:  # content, blockList, mainBody, container, mod, quotedStructure, ...
                out.extend(self.lines(child, skip, own_meta=True))
            emit(child.tail)
        return out

    def notes_and_refs(self, el: Element, skip: set[Element]) -> tuple[list[str], list[ParsedRef]]:
        notes: list[str] = []
        refs: list[ParsedRef] = []

        def visit(e: Element) -> None:
            for child in e:
                if child in skip:
                    continue
                tag = _local(child.tag)
                if tag == "authorialNote":
                    text = _ws("".join(child.itertext()))
                    if text and text not in notes:
                        notes.append(text)
                if tag == "ref":
                    target = child.get(f"{{{FEDLEX}}}rs-uri") or child.get("href") or ""
                    if target.startswith(ELI_CC) and all(r.href != target for r in refs):
                        refs.append(ParsedRef(href=target, text=_ws(_flat(child)), relation="cites"))
                visit(child)

        visit(el)
        return notes, refs


def _provisions(reader: _Reader, version_date: date) -> list[ParsedProvision]:
    texts: dict[str, str] = {}
    own: dict[str, list[str]] = {}
    notes: dict[str, list[str]] = {}
    refs: dict[str, list[ParsedRef]] = {}
    for node in reader.nodes:
        children = {c.element for c in node.children}
        texts[node.eid] = "\n".join(reader.lines(node.element, None))
        own[node.eid] = reader.lines(node.element, children)
        notes[node.eid], refs[node.eid] = reader.notes_and_refs(node.element, children)

    status: dict[str, tuple[str, date | None]] = {}

    def resolve(node: _Node) -> None:
        for child in node.children:
            resolve(child)
        own_text = "\n".join(own[node.eid])
        if REPEALED_TEXT.match(own_text) and all(status[c.eid][0] == "repealed" for c in node.children):
            stated = next((d for d in map(repeal_date, notes[node.eid]) if d), None)
            child_dates = [status[c.eid][1] for c in node.children]
            if stated is None and child_dates and all(child_dates):
                stated = max(d for d in child_dates if d)
            status[node.eid] = ("repealed", stated)
        else:
            status[node.eid] = ("in_force", version_date)

    for node in reader.nodes:
        if node.parent is None:
            resolve(node)

    out: list[ParsedProvision] = []
    for order, node in enumerate(reader.nodes):
        state, start = status[node.eid]
        node_notes = notes[node.eid]
        if state == "repealed" and not DOTS.match(texts[node.eid]):
            node_notes = [texts[node.eid], *node_notes]  # e.g. "Abrogé": kept as a note, not as text
        out.append(
            ParsedProvision(
                eid=node.eid,
                kind=node.kind,
                num_path=node.num_path,
                top_kind="annex" if node.kind == "annex" else "article",
                heading=node.heading,
                text="" if state == "repealed" else texts[node.eid],
                parent_eid=node.parent.eid if node.parent else None,
                sort_order=order,
                status=state,  # type: ignore[arg-type]
                valid_from=start,
                valid_to=None,
                extent=["CH"],
                note=" | ".join(n[:400] for n in node_notes[:5]) or None,
                refs=refs[node.eid],
                is_leaf=not node.children,
                has_own_content=bool(node.children) and any(not DOTS.match(line) for line in own[node.eid]),
            )
        )
    return out


def _frbr_date(work: Element, name: str) -> date | None:
    for el in work.findall(_a("FRBRdate")):
        if el.get("name") == name and el.get("date"):
            try:
                return date.fromisoformat(el.get("date", "")[:10])
            except ValueError:
                return None
    return None


def parse_fedlex(content: bytes) -> list[ParsedDocument]:
    try:
        root = fromstring(content, forbid_dtd=True)
    except Exception as exc:
        raise FedlexParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if root.tag != _a("akomaNtoso"):
        raise FedlexParseError(f"unsupported root element {root.tag!r}")
    act = root.find(_a("act"))
    if act is None:
        raise FedlexParseError("only <act> documents (SR consolidations) are supported")
    ident = act.find(f"{_a('meta')}/{_a('identification')}")
    work = ident.find(_a("FRBRWork")) if ident is not None else None
    lang_el = ident.find(f"{_a('FRBRExpression')}/{_a('FRBRlanguage')}") if ident is not None else None
    if work is None or lang_el is None:
        raise FedlexParseError("missing FRBRWork or FRBRExpression/FRBRlanguage")
    language = lang_el.get("language", "")
    if language not in LANGUAGES:
        raise FedlexParseError(f"unsupported language {language!r} (German, French and Italian are read)")
    uri_el = work.find(_a("FRBRuri"))
    m = re.match(
        r"^(https://fedlex\.data\.admin\.ch/eli/cc/[0-9A-Za-z]+/[0-9a-z_]+)/(\d{8})$",
        uri_el.get("value", "") if uri_el is not None else "",
    )
    if not m:
        raise FedlexParseError("FRBRWork/FRBRuri is not an SR consolidation (eli/cc/<work>/<YYYYMMDD>)")
    eli = m.group(1)
    version_date = _frbr_date(work, "jolux:dateApplicability")
    if version_date is None or version_date.strftime("%Y%m%d") != m.group(2):
        raise FedlexParseError("missing or inconsistent jolux:dateApplicability")
    adopted = _frbr_date(work, "jolux:dateDocument")
    in_force = _frbr_date(work, "jolux:dateEntryInForce")
    name_el = next((el for el in work.findall(_a("FRBRname")) if el.get(XML_LANG) == language), None)
    if name_el is None or not name_el.get("value"):
        raise FedlexParseError(f"no FRBRname in {language}")
    title = _ws(name_el.get("value", ""))
    preface = act.find(_a("preface"))
    doc_number = preface.find(f".//{_a('docNumber')}") if preface is not None else None
    number_el = work.find(_a("FRBRnumber"))
    sr = _ws(_flat(doc_number)) if doc_number is not None else ""
    sr = sr or (number_el.get("value", "") if number_el is not None else "")
    if not re.match(r"^\d+(?:\.\d+)*$", sr):
        raise FedlexParseError(f"missing or unexpected SR number {sr!r}")
    doc_title = preface.find(f".//{_a('docTitle')}") if preface is not None else None
    preamble = act.find(_a("preamble"))
    first_p = preamble.find(_a("p")) if preamble is not None else None
    if sr == "101":
        instrument_type = "constitution"
    elif (
        first_p is not None
        and FEDERAL_ASSEMBLY.match(_ws(_flat(first_p)))
        and not NOT_ACTS.match(_ws(_flat(doc_title)) if doc_title is not None else title)
    ):
        instrument_type = "federal_act"
    else:
        raise FedlexParseError(f"SR {sr}: only the Federal Constitution and federal acts are supported")

    body = act.find(_a("body"))
    if body is None:
        raise FedlexParseError("missing body")
    reader = _Reader()
    reader.walk(body, "", [], None)
    for doc in act.iter(_a("doc")):
        if doc.get("name") == "annex":
            reader.annex(doc)
    if not reader.nodes:
        raise FedlexParseError("no provisions found")

    uri = work_uri(eli)
    events = [
        ParsedEvent("version_change", version_date, None, None, "FRBRWork/FRBRdate[@name='jolux:dateApplicability']")
    ]
    if adopted:
        events.append(ParsedEvent("enactment", adopted, None, None, "FRBRWork/FRBRdate[@name='jolux:dateDocument']"))
    if in_force:
        events.append(
            ParsedEvent("entry_into_force", in_force, None, None, "FRBRWork/FRBRdate[@name='jolux:dateEntryInForce']")
        )
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=instrument_type,
            jurisdiction_id="CH",
            work_uri=uri,
            expression_uri=f"{uri}/{language}@{version_date.isoformat()}",
            work_title=title,
            title=title,
            short_title=_ws(name_el.get("shortForm", "")) or None,
            number=sr,
            work_date=adopted,
            language=language,
            authoritative=True,
            text_origin="official_consolidation",
            consolidation_kind="point_in_time",
            version_date=version_date,
            currency_date=version_date,
            extent=["CH"],
            ext_module="ch",
            ext={"sr_number": sr, "eli": eli, "entry_into_force": in_force.isoformat() if in_force else None},
            events=events,
            provisions=_provisions(reader, version_date),
            validity_basis=VALIDITY_BASIS,
        )
    ]


def skipped_blocks(content: bytes) -> list[str]:
    """Final/transitional provision blocks the parser does not read (for reports and tests)."""
    root = fromstring(content, forbid_dtd=True)
    body = root.find(f"{_a('act')}/{_a('body')}")
    reader = _Reader()
    if body is not None:
        reader.walk(body, "", [], None)
    return reader.skipped
