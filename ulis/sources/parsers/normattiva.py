"""Parser for Italian legislation exported from Normattiva as Akoma Ntoso 3.0.

Normattiva (https://www.normattiva.it, Istituto Poligrafico e Zecca dello Stato for the Presidency
of the Council of Ministers) offers every act in its "multivigente" form and exports it, as it
applies on a chosen date, with "esporta in Akoma ntoso" (``/do/atto/caricaAKN``). The legal notice
says the texts "non hanno carattere di ufficialità": only the Gazzetta Ufficiale (GU) is authentic.
Every document is therefore not authoritative. The "multivigente" texts are the consolidation that
Normattiva's editors maintain (``official_revised``); the text as first published is Normattiva's
reproduction of the Gazzetta Ufficiale (``official_conversion``). ``unofficial_compilation`` is not
used: ULIS words that limitation for the United States Code.

How the file is read:

* **One file, one date.** The export is the whole act as it applies from the date in its ELI
  (``eli/id/<GU date>/<redactional code>/CONSOLIDATED/<YYYYMMDD>``, or ``ORIGINAL`` for the text
  as first published). Provisions carry no dates of their own, so the document is a whole-instrument
  snapshot (``validity_basis = "revision_snapshot"``, ``ulis.ingestion.snapshots``): every in-force
  provision starts on the snapshot date, an unchanged wording continues from the previous snapshot
  and one that is absent ends. For an instrument with a single snapshot the start is therefore only
  the date of that snapshot, not of the provision.
* **Provisions** are articles, commi (``paragraph`` with a number) and lettere/numeri (``point``).
  ULIS eIds are built from the printed numbers (``art_2-bis``, ``art_2/para_1``,
  ``art_2/para_1/let_a``). Commi that the source does not number (older acts print them as
  unnumbered blocks) are part of their article, which is then one provision. A comma inserted by a
  later amendment is numbered inside its text (``((2-bis. ...))``); that number is used.
* **Codes** (Codice civile, Codice penale) are exported as the royal decree that approves them,
  with every article of the code as one attachment holding "Art. N. (rubrica) text" as flat lines.
  Each is read as one provision; the two articles of the approving decree are not read, and neither
  is the book/title/chapter structure, which the export does not carry. The preliminary provisions
  of the Codice civile ("preleggi") are ``prel_art_N``. The Constitution's transitional and final
  provisions (I-XVIII) are ``trans_<numeral>``.
* **Text is exact source text**, line by line, with whitespace collapsed. Normattiva's own markup
  stays: ``((...))`` surrounds wording inserted or replaced by a later amendment. Editorial footnote
  anchors (``((5))``, ``(14)``) are not text and are dropped, because they are renumbered whenever a
  note is added and would make unchanged wording look changed. The notes themselves
  ("AGGIORNAMENTO (5) ..."), set off by a line of dashes, go to ``note``.
* **Repealed** provisions are printed as ``((ARTICOLO ABROGATO DA ...))``: status ``repealed``,
  empty text, the marker in ``note``. The export gives no repeal date (the marker names the repealing
  act, not the day its effect began), so a repealed provision starts, like any other, on the date of
  the first version ingested that shows it repealed: in a series of versions that closes the earlier
  in-force version on the right snapshot, and with a single version it is the date of that version.
* **Not read**: preamble and conclusions, the structural divisions (parts, titles, chapters: the
  export renders them as unreliable run-together headings), annexes of the d.lgs. 196/2003, and the
  articles of the decrees that approve the codes.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.ingestion.snapshots import SNAPSHOT_BASIS
from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParsedRef

AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
PARSER_ID = "normattiva_akn"
PARSER_VERSION = "1.1.0"
NOTE_LIMIT = 3000


class NormattivaParseError(ValueError):
    pass


@dataclass(frozen=True)
class _Special:
    """Instruments whose export needs more than the generic reading (named by redactional code)."""

    title: str
    short_title: str
    label: str  # abbreviation used in citations
    instrument_type: str
    work_type: str
    # Attachment-name prefix (lower case) -> "main" (articles of the code) or "prel" (preleggi).
    flat: dict[str, str] = field(default_factory=dict)
    transitional: bool = False


SPECIAL: dict[str, _Special] = {
    "047U0001": _Special(
        "Costituzione della Repubblica Italiana",
        "Costituzione",
        "Cost.",
        "constitution",
        "costituzione",
        transitional=True,
    ),  # fmt: skip
    "042U0262": _Special(
        "Codice civile",
        "Codice civile",
        "c.c.",
        "code",
        "codice.civile",
        flat={"codice civile": "main", "disposizioni sulla legge in generale": "prel"},
    ),  # fmt: skip
    "030U1398": _Special(
        "Codice penale", "Codice penale", "c.p.", "code", "codice.penale", flat={"codice penale": "main"}
    ),
    # The other codes that Normattiva exports as the royal decree that approves them, with one attachment per
    # article (1.1.0). They keep the work URI of the decree (``/it/regio.decreto/<year>/<number>``) so that references
    # to the decree resolve to them.
    "040U1443": _Special(
        "Codice di procedura civile",
        "Codice di procedura civile",
        "c.p.c.",
        "code",
        "regio.decreto",
        flat={"codice di procedura civile": "main"},
    ),  # fmt: skip
    "041U1368": _Special(
        "Disposizioni per l'attuazione del Codice di procedura civile e disposizioni transitorie",
        "Disposizioni di attuazione del c.p.c.",
        "disp. att. c.p.c.",
        "royal_decree",
        "regio.decreto",
        flat={"disposizioni di attuazione del codice di procedura civile": "main"},
    ),  # fmt: skip
    "042U0318": _Special(
        "Disposizioni per l'attuazione del Codice civile e disposizioni transitorie",
        "Disposizioni di attuazione del c.c.",
        "disp. att. c.c.",
        "royal_decree",
        "regio.decreto",
        flat={"disposizioni per l'attuazione del codice civile e disposizioni transitorie": "main"},
    ),  # fmt: skip
    "042U0327": _Special(
        "Codice della navigazione",
        "Codice della navigazione",
        "cod. nav.",
        "code",
        "regio.decreto",
        flat={"codice della navigazione": "main"},
    ),  # fmt: skip
    # Two codes in one decree: their article numbers overlap, so each article is cited with its own abbreviation.
    "041U0303": _Special(
        "Codici penali militari di pace e di guerra",
        "Codici penali militari",
        "c.p.m.",
        "code",
        "regio.decreto",
        flat={
            "codice penale militare di pace": "cite:c.p.m.p.",
            "codice penale militare di guerra": "cite:c.p.m.g.",
        },
    ),  # fmt: skip
}

# urn:nir type -> (instrument type, abbreviation used in citations)
ACT_TYPES: dict[str, tuple[str, str]] = {
    "legge": ("act", "L."),
    "legge.costituzionale": ("constitutional_law", "L. cost."),
    "decreto.legislativo": ("legislative_decree", "d.lgs."),
    "decreto.legge": ("decree_law", "d.l."),
    "decreto.del.presidente.della.repubblica": ("presidential_decree", "d.P.R."),
    "regio.decreto": ("royal_decree", "R.D."),
}
CONSTITUTION_DATE = date(1947, 12, 27)

_SUFFIX = r"(?:bis|ter|quater|quinquies|sexies|septies|octies|novies|decies)"
_DASHES = re.compile(r"(?m)^[ \t]*-{5,}[ \t]*$")
_ELI = re.compile(
    r"^eli/id/(\d{4})/(\d{2})/(\d{2})/((?:\d{3}[A-Z]\d{4}|\d{2}[A-Z]\d{5}))/(ORIGINAL|CONSOLIDATED/(\d{8}))$"
)
_URN = re.compile(r"^urn:nir:stato:([a-z.]+)(?::(\d{4})-(\d{2})-(\d{2});(\d+))?$")
_LABEL = re.compile(r"^[0-9a-z][0-9a-z./\-]*$")  # "/" only in articles such as art. 314/2 c.c.
_INLINE_LABEL = re.compile(r"^(?:\(\(\s*)?(\d+(?:-[a-z]+)*(?:\.\d+)?)\.\s")
_ART_LINE = re.compile(r"^Art\.\s*(\d+(?:/\d+)?(?:[ -][a-z]+)*(?:\.\d+)?)\.?\s*(.*)$")
_ANCHOR = re.compile(r"\s*\(\(\d+\)\)")
_NOTE_NUMBER = re.compile(r"AGGIOR\w*MENTO \((\d+)\)")
_REPEAL_KIND = re.compile(r"^(?:ARTICOLO|COMMA|LETTERA|NUMERO|PUNTO|PARAGRAFO)\b")
_REPEAL_WORD = re.compile(r"(?i)abrogat|soppress|non pi\S* previst")
_ENTRY = re.compile(r"Entrata in vigore[^:]*:\s*(\d{1,2})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{4})")
_AKN_REF = re.compile(r"^/akn/it/act/([^/]+)/([^/]+)/(\d{4})(?:-\d{2}-\d{2})?/([^/]+)/!main(?:#(.+))?$")
_URN_LINK = re.compile(
    r"^(?:https?://www\.normattiva\.it/uri-res/N2Ls\?)?urn:nir:stato:([a-z.]+):(\d{4})(?:-\d{2}-\d{2})?;(\d+)(?:~art(.+))?$"
)


# "Codice civile-Art. 2043", "Codice penale militare di pace - art. 1", "Regolamento ...- art. 12 bis", "Norme-art. 3".
_ATTACHED_ARTICLE = re.compile(r"^(?P<prefix>.*?)\s*-\s*art\.\s*(?P<number>\S.*)$", re.I)
FLAT_MINIMUM = 10  # an act with at least this many such attachments is one whose articles are all attachments


def _a(tag: str) -> str:
    return f"{{{AKN}}}{tag}"


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _ws(text: str) -> str:
    return " ".join(text.split())


def _text(el: Element | None) -> str:
    return _ws("".join(el.itertext())) if el is not None else ""


def _stamp_date(stamp: str) -> date:
    try:
        return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
    except ValueError as exc:
        raise NormattivaParseError(f"invalid date {stamp!r} in the ELI") from exc


def _date(value: str | None) -> date | None:
    if not value or value.startswith("0000"):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------------- references


def _slug(kind: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", ".", kind).replace("_", ".").lower()


def work_uri_for(kind: str, year: int | str, number: str) -> str:
    if kind == "costituzione":
        return f"/it/costituzione/{CONSTITUTION_DATE.isoformat()}"
    return f"/it/{kind}/{year}/{number}"


def _ref_uri(href: str) -> str | None:
    """ULIS URI (``/it/<type>/<year>/<number>[/~art_N]``) of an Akoma Ntoso reference, if it is Italian law."""
    m = _AKN_REF.match(href)
    if not m or m.group(2) == "eu":
        return None
    kind = _slug(m.group(1))
    if kind == "regio.decreto" and (m.group(3), m.group(4)) == ("1930", "1398"):
        kind = "codice.penale"
    uri = work_uri_for(kind, m.group(3), m.group(4))
    if m.group(5) and m.group(5).startswith("art_"):
        article = re.split(r"-(?=(?:com|let|num|par)\w*)", m.group(5))[0]
        uri += f"/~{article}"
    return uri


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map a Normattiva URN (``urn:nir:stato:legge:1990-08-07;241~art2``) to (ULIS work URI, eId)."""
    m = _URN_LINK.match(uri)
    if m:
        article = m.group(4)
        return work_uri_for(m.group(1), m.group(2), m.group(3)), (f"art_{article}" if article else None)
    if uri.endswith("urn:nir:stato:costituzione") or "urn:nir:stato:costituzione:" in uri:
        return work_uri_for("costituzione", 1947, "0"), None
    return None


# ----------------------------------------------------------------------------------- reading


def _flatten(p: Element) -> tuple[str, list[tuple[int, str, str]]]:
    """Text of an element and the (offset, href, text) of every reference in it."""
    parts: list[str] = []
    refs: list[tuple[int, str, str]] = []
    size = 0

    def walk(e: Element) -> None:
        nonlocal size
        if e.text:
            parts.append(e.text)
            size += len(e.text)
        for child in e:
            start = size
            walk(child)
            if _local(child.tag) == "ref" and child.get("href"):
                refs.append((start, child.get("href", ""), "".join(child.itertext())))
            if child.tail:
                parts.append(child.tail)
                size += len(child.tail)

    walk(p)
    return "".join(parts), refs


def _lines(raw: str) -> list[str]:
    return [line for line in (_ws(x) for x in raw.split("\n")) if line]


@dataclass
class _State:
    in_note: bool = False  # a line of dashes has been seen: everything after is editorial


@dataclass
class _Text:
    lines: list[str] = field(default_factory=list)
    note: list[str] = field(default_factory=list)
    refs: list[ParsedRef] = field(default_factory=list)


def _read_ps(ps: Iterable[Element], state: _State) -> _Text:
    """Law text, editorial note and references of some ``p`` elements, in document order."""
    out = _Text()
    for p in ps:
        raw, refs = _flatten(p)
        cut = len(raw)
        if state.in_note:
            cut = 0
        else:
            m = _DASHES.search(raw)
            if m:
                cut = m.start()
                state.in_note = True
        out.lines.extend(_lines(raw[:cut]))
        if cut < len(raw):
            tail = raw[cut:]
            out.note.extend(line for line in _lines(tail) if not set(line) <= {"-"})
        for offset, href, text in refs:
            uri = _ref_uri(href) if offset < cut else None
            if uri and all(r.href != uri for r in out.refs):
                out.refs.append(ParsedRef(href=uri, text=_ws(text), relation="cites"))
    return out


def _label(raw: str) -> str | None:
    label = re.sub(r"\s+", "-", raw.strip().strip("()").strip().rstrip(".)").strip().lower())
    return label if _LABEL.match(label) else None


def _heading(el: Element | None) -> str | None:
    """Rubrica without Normattiva's ``((...))`` amendment markers and its enclosing parentheses."""
    if el is None:
        return None
    text = _ws("".join(el.itertext()).replace("((", " ").replace("))", " "))
    m = re.fullmatch(r"\((.*)\)\.?", text)
    if m and m.group(1).count("(") == m.group(1).count(")"):
        text = m.group(1).strip()
    elif text.count(")") > text.count("(") and re.search(r"\)\.?$", text):
        text = re.sub(r"\)\.?$", "", text).strip()  # the source dropped the opening parenthesis
    return text or None


def _repeal_marker(text: str) -> str | None:
    """The marker if ``text`` is nothing but ``((ARTICOLO ABROGATO DA ...))`` (or COMMA, LETTERA ...)."""
    t = re.sub(r"^Art\.\s*[^()]*?(?=\(\()", "", _ws(text))
    m = re.fullmatch(r"\(\((.*)\)\)\.?", t)
    inner = m.group(1).strip() if m else t.rstrip(".")  # some markers come without the parentheses
    if "((" in inner or "))" in inner:
        return None
    return inner if _REPEAL_KIND.match(inner) and _REPEAL_WORD.search(inner) else None


def _rubrica(line: str, *, bare: bool = False) -> str | None:
    """The rubrica printed as ``(Title).`` or ``((Title))`` on its own line, else None."""
    t = _ws(line.replace("((", " ").replace("))", " ")) if "((" in line or "))" in line else _ws(line)
    t = re.sub(r"\)[\s.]*$", ")", t)
    m = re.fullmatch(r"\((.*)\)", t)
    if m and m.group(1).strip() and m.group(1).count("(") == m.group(1).count(")"):
        return m.group(1).strip()
    if bare:  # "((Title.))": wholly inside amendment markers, short, and not the only line
        m = re.fullmatch(r"\(\(\s*([^()]{3,120}?)\s*\)\)\.?", _ws(line))
        if m and not _REPEAL_KIND.match(m.group(1)):
            return m.group(1)
    return None


@dataclass
class _Node:
    eid: str
    kind: str
    num_path: list[str]
    heading: str | None
    top_kind: str = "article"
    lines: list[str] = field(default_factory=list)
    tail: list[str] = field(default_factory=list)  # unnumbered text after the children
    note: list[str] = field(default_factory=list)
    refs: list[ParsedRef] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)
    parent: _Node | None = None
    label: str | None = None  # printed number, prefixed to the text of a container
    inline: bool = False  # the number is part of the text itself
    context_heading: str | None = None


class _Reader:
    def __init__(self) -> None:
        self.nodes: list[_Node] = []  # top-level provisions in document order
        self.eids: set[str] = set()
        self.warnings: list[str] = []

    def add(self, node: _Node) -> None:
        if node.eid in self.eids:
            raise NormattivaParseError(f"duplicate provision {node.eid!r}")
        self.eids.add(node.eid)
        self.nodes.append(node)

    # -- structured acts: article > paragraph (comma) > point (lettera / numero)
    def article(self, art: Element) -> None:
        raw = _text(art.find(_a("num")))
        m = re.match(r"^Art(?:icolo)?\.?\s*(.+?)\s*\.?$", raw)
        label = _label(m.group(1)) if m else None
        if label is None and art.get("eId", "").startswith("art_"):
            label = _label(art.get("eId", "")[4:])
        if label is None:
            raise NormattivaParseError(f"article without a number: {raw!r}")
        eid, again = f"art_{label}", 2
        while eid in self.eids:  # the act numbers two different articles alike (d.lgs. 152/2006 has two art. 298-bis)
            self.warnings.append(f"article {label} appears more than once")
            eid, again = f"art_{label}--{again}", again + 1
        node = _Node(eid, "article", [label], _heading(art.find(_a("heading"))))
        state = _State()
        last, seen = node, set()
        for para in art.findall(_a("paragraph")):
            content, listing = para.find(_a("content")), para.find(_a("list"))
            text = _read_ps(content.iter(_a("p")), state) if content is not None else _Text()
            intro_el = listing.find(_a("intro")) if listing is not None else None
            if intro_el is not None:
                intro = _read_ps(intro_el.iter(_a("p")), state)
                text.lines += intro.lines
                text.note += intro.note
                text.refs += intro.refs
            num = para.find(_a("num"))
            label_p, inline = (_label(_text(num)) if num is not None else None), False
            if label_p is None and not _repeal_marker(" ".join(text.lines)):
                head = text.lines[1] if text.lines[:1] == ["(("] and len(text.lines) > 1 else " ".join(text.lines[:1])
                found = _INLINE_LABEL.match(head + " ")
                label_p, inline = (found.group(1) if found else None), found is not None
            if label_p is None or label_p in seen:
                self._append(last, text.lines)
                owner = last
            else:
                child = _Node(
                    f"{node.eid}/para_{label_p}", "paragraph", [*node.num_path, f"comma:{label_p}"], None,
                    lines=text.lines, parent=node, label=label_p, inline=inline, context_heading=node.heading,
                )  # fmt: skip
                node.children.append(child)
                seen.add(label_p)
                last = owner = child
                if listing is not None:
                    self._points(listing, child, state)
            owner.note += text.note
            owner.refs += [r for r in text.refs if all(r.href != x.href for x in owner.refs)]
        self.add(node)

    def _points(self, listing: Element, parent: _Node, state: _State) -> None:
        seen: set[str] = set()
        for point in listing.findall(_a("point")):
            text = _read_ps(point.iter(_a("p")), state)
            num = point.find(_a("num"))
            label = _label(_text(num)) if num is not None else None
            if label is None or label in seen:
                self._append(parent.children[-1] if parent.children else parent, text.lines)
                parent.note += text.note
                continue
            seen.add(label)
            prefix, tag = ("lett", "let") if re.fullmatch(r"[a-z]+(?:-[a-z]+)*", label) else ("num", "num")
            parent.children.append(
                _Node(
                    f"{parent.eid}/{tag}_{label}",
                    "point",
                    [*parent.num_path, f"{prefix}:{label}"],
                    None,
                    lines=text.lines,
                    note=text.note,
                    refs=text.refs,
                    parent=parent,
                    label=label,
                    context_heading=parent.context_heading,
                )  # fmt: skip
            )

    @staticmethod
    def _append(node: _Node, lines: list[str]) -> None:
        (node.tail if node.children else node.lines).extend(lines)

    # -- attachments: one article of a code, or one transitional provision of the Constitution
    def code_article(self, name: str, main_body: Element, group: str, duplicate: bool = False) -> bool:
        """Read one attachment as an article; False when it is not read (see below)."""
        named = _ATTACHED_ARTICLE.match(name)
        number = named.group("number").strip() if named else ""
        label = _label(number)
        if label is None:
            raise NormattivaParseError(f"attachment {name!r}: no article number")
        text = _read_ps(main_body.iter(_a("p")), _State())
        lines = text.lines
        if lines and lines[0].lower() == name.lower():  # some repealed articles repeat the attachment name
            lines = lines[1:]
        first = next((i for i, line in enumerate(lines[:3]) if _ART_LINE.match(line)), None)
        if first is not None:
            m = _ART_LINE.match(lines[first])
            assert m is not None
            printed = _label(m.group(1))
            if printed != label and duplicate:
                # Two attachments carry this name and this one prints another article's number (the codici penali
                # militari have one for art. 33 that is art. 22): the source mislabelled it, so it is not read.
                self.warnings.append(f"{name}: mislabelled duplicate (article line says {m.group(1)!r}); not read")
                return False
            if printed != label:
                self.warnings.append(f"{name}: article line says {m.group(1)!r}")
                # Where both carry a suffix (Latin multiplier) only the spelling differs and the printed
                # one is the number of the article; a printed number without suffix is a source slip.
                if printed and "-" in printed and "-" in label and printed.split("-")[0] == label.split("-")[0]:
                    label = printed
            lines = ([m.group(2)] if m.group(2) else []) + lines[first + 1 :]
        else:
            self.warnings.append(f"{name}: no 'Art.' line")
        heading = _rubrica(lines[0], bare=len(lines) > 1) if lines else None
        if heading:
            lines = lines[1:]
        prel = group == "prel"
        cite = group.removeprefix("cite:") if group.startswith("cite:") else None
        text.refs = [
            r for r in text.refs if not r.href.endswith(f"/~art_{label.replace('/', '_')}")
        ]  # "CODICE ... Art. N"
        stem = "prel_" if prel else (re.sub(r"[^a-z0-9]+", "-", cite.lower())[:60].strip("-") + "_" if cite else "")
        self.add(
            _Node(
                f"{stem}art_{label.replace('/', '_')}",
                "article",
                [f"prel:{label}" if prel else (f"cite:{cite}:{label}" if cite else label)],
                heading,
                lines=lines,
                note=text.note,
                refs=text.refs,
            )  # fmt: skip
        )
        return True

    def transitional(self, name: str, main_body: Element) -> None:
        numeral = name.partition("-")[2]
        if not re.fullmatch(r"[IVXL]+", numeral):
            raise NormattivaParseError(f"attachment {name!r}: not a transitional provision")
        text = _read_ps(main_body.iter(_a("p")), _State())
        lines = text.lines
        if lines and lines[0].upper() == "DISPOSIZIONI TRANSITORIE E FINALI":
            lines = lines[1:]
        if lines and lines[0] == numeral:
            lines = lines[1:]
        self.add(
            _Node(
                f"trans_{numeral}",
                "transitional",
                [f"trans:{numeral}"],
                None,
                top_kind="transitional",
                lines=lines,
                note=text.note,
                refs=text.refs,
            )  # fmt: skip
        )


def _walk(el: Element, reader: _Reader) -> None:
    for child in el:
        tag = _local(child.tag)
        if tag == "article":
            reader.article(child)
        elif tag in ("chapter", "section", "part", "title", "book", "subsection", "level", "hcontainer"):
            _walk(child, reader)


def _flat_attachments(act: Element) -> int:
    """How many attachments are named like one article of a code (``<code>-art. N``)."""
    return sum(
        1
        for doc in act.iter(_a("doc"))
        if _ATTACHED_ARTICLE.match(doc.get("name", "")) and doc.find(_a("mainBody")) is not None
    )


def _is_flat(act: Element) -> bool:
    """An act whose articles are its attachments (a code approved by a decree of two or three articles).

    An act with many articles of its own that merely has annexed codes of conduct (d.lgs. 196/2003) is not."""
    attached = _flat_attachments(act)
    body = act.find(_a("body"))
    own = sum(1 for _ in body.iter(_a("article"))) if body is not None else 0
    return attached >= FLAT_MINIMUM and attached > own


def _attachments(act: Element, special: _Special | None, reader: _Reader) -> list[str]:
    """Read the attachments an instrument needs; return the names of those that are not read."""
    skipped: list[str] = []
    generic_flat = not (special and special.flat) and _is_flat(act)
    names = [doc.get("name", "") for doc in act.iter(_a("doc"))]
    # A generic flat act with several sets of attachments (an Allegato 1 and an Allegato 2, each numbering its
    # articles from 1) keeps the most frequent prefix as the plain "art. N" and cites the others by their name.
    parts = [n for n in (_ATTACHED_ARTICLE.match(x) for x in names) if n]
    by_number: dict[str | None, set[str]] = {}
    for n in parts:
        by_number.setdefault(_label(n.group("number")), set()).add(n.group("prefix").strip().lower())
    collide = any(len(v) > 1 for v in by_number.values())
    prefix_counts = Counter(n.group("prefix").strip().lower() for n in parts)
    main_prefix = prefix_counts.most_common(1)[0][0] if prefix_counts else ""
    for doc in act.iter(_a("doc")):
        name = doc.get("name", "")
        main_body = doc.find(_a("mainBody"))
        named = _ATTACHED_ARTICLE.match(name)
        if special and special.flat:
            group = next(
                (g for prefix, g in special.flat.items() if named and named.group("prefix").lower() == prefix), None
            )
        elif named and generic_flat:
            prefix = named.group("prefix").strip()
            group = "main" if not collide or prefix.lower() == main_prefix else f"cite:{prefix}"
        else:
            group = None
        if main_body is not None and group:
            if not reader.code_article(name, main_body, group, duplicate=names.count(name) > 1):
                skipped.append(name)
        elif main_body is not None and special and special.transitional and name.startswith("Allegato-"):
            reader.transitional(name, main_body)
        else:
            skipped.append(name)
    return skipped


# ----------------------------------------------------------------------------------- output


def _subtree(node: _Node) -> list[_Node]:
    return [node, *(n for child in node.children for n in _subtree(child))]


def _strip_anchors(top: _Node) -> None:
    """Drop editorial footnote anchors from the law text (see the module docstring)."""
    nodes = _subtree(top)
    numbers = set(_NOTE_NUMBER.findall(" ".join(line for n in nodes for line in n.note)))
    for n in nodes:
        for attr in ("lines", "tail"):
            cleaned = []
            for line in getattr(n, attr):
                line = _ANCHOR.sub("", line)
                while (m := re.search(r"(?:^|\s)\((\d+)\)$", line)) and m.group(1) in numbers:
                    line = line[: m.start()]
                line = line.strip()
                if line and set(line) != {"."}:
                    cleaned.append(line)
            setattr(n, attr, cleaned)


def _cap(parts: list[str]) -> str | None:
    note = " | ".join(parts)
    if len(note) > NOTE_LIMIT:
        note = note[: NOTE_LIMIT - 1].rstrip() + "…"
    return note or None


def _full_lines(node: _Node) -> list[str]:
    out = list(node.lines)
    for child in node.children:
        body = _full_lines(child)
        if body and child.label and not child.inline:
            sep = ")" if child.kind == "point" else "."
            body[0] = f"{child.label}{sep} {body[0]}"
        out.extend(body)
    return out + node.tail


def _provisions(reader: _Reader, version_date: date) -> list[ParsedProvision]:
    for node in reader.nodes:
        _strip_anchors(node)
    state: dict[str, tuple[str, str | None]] = {}  # eid -> (status, repeal marker)

    def resolve(node: _Node) -> None:
        for child in node.children:
            resolve(child)
        own = " ".join(node.lines + node.tail)
        marker = _repeal_marker(own) if own else None
        if node.children and not own:
            kids = [state[c.eid] for c in node.children]
            if all(s == "repealed" for s, _ in kids):
                marker = "; ".join(m for _, m in kids if m)
        elif node.children and own:
            marker = None
        state[node.eid] = ("repealed", marker) if marker else ("in_force", None)

    for node in reader.nodes:
        resolve(node)

    out: list[ParsedProvision] = []

    def emit(node: _Node) -> None:
        status, marker = state[node.eid]
        repealed = status == "repealed"
        notes = ([marker] if marker else []) + node.note
        out.append(
            ParsedProvision(
                eid=node.eid,
                kind=node.kind,
                num_path=node.num_path,
                top_kind=node.top_kind,
                heading=node.heading,
                text="" if repealed else "\n".join(_full_lines(node)),
                parent_eid=node.parent.eid if node.parent else None,
                sort_order=len(out),
                status=status,  # type: ignore[arg-type]
                valid_from=version_date,  # also when repealed: the first version that attests that state
                valid_to=None,
                extent=["IT"],
                note=_cap(notes),
                refs=node.refs,
                is_leaf=not node.children,
                has_own_content=bool(node.children) and bool(node.lines) and not repealed,
                context_heading=node.context_heading,
            )
        )
        for child in node.children:
            emit(child)

    for node in reader.nodes:
        emit(node)
    return out


def _clean_title(text: str) -> str:
    text = _ws(text.replace("((", " ").replace("))", " "))
    return re.sub(r"\s+([,.;])", r"\1", re.sub(r"\s*\((?:\d{3}[A-Z]\d{4}|\d{2}[A-Z]\d{5})\)\s*$", "", text)).strip()


def skipped_attachments(content: bytes) -> list[str]:
    """Names of the attachments the parser does not read (for reports and tests)."""
    root = fromstring(content, forbid_dtd=True)
    act = root.find(_a("act"))
    if act is None:
        return []
    ident = act.find(f"{_a('meta')}/{_a('identification')}/{_a('FRBRWork')}")
    code = ""
    if ident is not None:
        for alias in ident.findall(_a("FRBRalias")):
            m = _ELI.match(alias.get("value", ""))
            if alias.get("name") == "eli" and m:
                code = m.group(4)
    return _attachments(act, SPECIAL.get(code), _Reader())


def parse_normattiva(content: bytes) -> list[ParsedDocument]:
    root = fromstring(content, forbid_dtd=True)
    if root.tag != _a("akomaNtoso"):
        raise NormattivaParseError(f"unsupported root element {root.tag!r}")
    act = root.find(_a("act"))
    if act is None:
        raise NormattivaParseError("only <act> documents are supported")
    ident = act.find(f"{_a('meta')}/{_a('identification')}")
    work = ident.find(_a("FRBRWork")) if ident is not None else None
    expression = ident.find(_a("FRBRExpression")) if ident is not None else None
    if work is None or expression is None:
        raise NormattivaParseError("missing FRBRWork or FRBRExpression")
    aliases = {a.get("name"): a.get("value", "") for a in work.findall(_a("FRBRalias"))}
    eli = _ELI.match(aliases.get("eli", ""))
    if not eli:
        raise NormattivaParseError(f"unexpected ELI {aliases.get('eli')!r}")
    code = eli.group(4)
    gazzetta = date(int(eli.group(1)), int(eli.group(2)), int(eli.group(3)))
    expr_el = expression.find(_a("FRBRdate"))
    expr_date = _date(expr_el.get("date") if expr_el is not None else None)
    original = eli.group(5) == "ORIGINAL"
    # The ELI states the date of a consolidated version; the expression date of a code is that of the
    # code, not of the version. The text as first published is dated by its expression (entry into force).
    version_date = expr_date if original else _stamp_date(eli.group(6))
    if version_date is None:
        raise NormattivaParseError("no date for this version")

    urn = _URN.match(aliases.get("urn:nir", ""))
    if not urn:
        raise NormattivaParseError(f"unexpected urn:nir {aliases.get('urn:nir')!r}")
    special = SPECIAL.get(code)
    kind = urn.group(1)
    if special is None and kind not in ACT_TYPES:
        raise NormattivaParseError(f"unsupported kind of act {kind!r} ({code})")

    preface = act.find(_a("preface"))
    doc_type = _text(preface.find(f".//{_a('docType')}")) if preface is not None else ""
    doc_date_el = preface.find(f".//{_a('docDate')}") if preface is not None else None
    doc_date_text = _text(doc_date_el)
    doc_number = _text(preface.find(f".//{_a('docNumber')}")) if preface is not None else ""
    number = doc_number
    doc_title = _clean_title(_text(preface.find(f".//{_a('docTitle')}"))) if preface is not None else ""
    work_date = _date(doc_date_el.get("date") if doc_date_el is not None else None)
    year = int(urn.group(2)) if urn.group(2) else None
    if special and special.work_type == "costituzione":
        work_date, year, number = CONSTITUTION_DATE, CONSTITUTION_DATE.year, ""
        wuri = work_uri_for("costituzione", year, "0")
    else:
        if not (year and urn.group(5)):
            raise NormattivaParseError("urn:nir without date and number")
        number = urn.group(5)
        wuri = work_uri_for(special.work_type if special else kind, year, number)
    instrument_type = special.instrument_type if special else ACT_TYPES[kind][0]
    if special:
        title, short_title, label = special.title, special.short_title, special.label
    else:
        label = f"{ACT_TYPES[kind][1]} {number}/{year}"
        day_text = re.sub(r"^0(?=\d )", "", doc_date_text)
        designation = f"{doc_type.capitalize()} {day_text}, n. {number}".strip()
        title, short_title = f"{designation} - {doc_title}" if doc_title else designation, None
    entry = _ENTRY.search(
        " ".join(_text(a) for a in (preface.iter(_a("authorialNote")) if preface is not None else []))
    )
    in_force = None
    if entry:
        try:
            in_force = date(int(entry.group(3)), int(entry.group(2)), int(entry.group(1)))
        except ValueError:
            in_force = None

    reader = _Reader()
    body = act.find(_a("body"))
    if body is None:
        raise NormattivaParseError("missing body")
    flat_act = bool(special and special.flat) or (special is None and _is_flat(act))
    if not flat_act:
        _walk(body, reader)  # the articles of a code's approving decree are not the code
    _attachments(act, special, reader)
    if not reader.nodes:
        raise NormattivaParseError("no provisions found")

    publication = act.find(f"{_a('meta')}/{_a('publication')}")
    ext: dict[str, object] = {
        "act_type": special.work_type if special else kind,
        "citation_label": label,
        "codice_redazionale": code,
        "urn": aliases["urn:nir"],
        "gazzetta_ufficiale_date": gazzetta.isoformat(),
    }
    if publication is not None and publication.get("number"):
        ext["gazzetta_ufficiale_number"] = publication.get("number")
    if number:
        ext["number"] = number
    if year:
        ext["year"] = year
    if in_force:
        ext["entry_into_force"] = in_force.isoformat()
    if flat_act:
        ext["approved_by"] = _ws(f"{doc_type.capitalize()} {doc_date_text}, n. {doc_number}")

    events = [ParsedEvent("version_change", version_date, None, None, "FRBRExpression/FRBRdate")]
    if work_date:
        events.append(ParsedEvent("enactment", work_date, None, None, "preface/docDate"))
    events.append(ParsedEvent("publication", gazzetta, None, None, "FRBRalias[@name='eli']"))
    if in_force:
        events.append(ParsedEvent("entry_into_force", in_force, None, None, "preface/authorialNote"))
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=instrument_type,
            jurisdiction_id="IT",
            work_uri=wuri,
            expression_uri=f"{wuri}/it@{version_date.isoformat()}",
            work_title=title,
            title=title,
            short_title=short_title,
            number=number or None,
            work_date=work_date,
            language="it",
            authoritative=False,
            text_origin="official_conversion" if original else "official_revised",
            consolidation_kind="as_made" if original else "point_in_time",
            version_date=version_date,
            currency_date=None,
            extent=["IT"],
            ext_module="it",
            ext=ext,
            events=events,
            provisions=_provisions(reader, version_date),
            validity_basis=SNAPSHOT_BASIS,
        )
    ]
