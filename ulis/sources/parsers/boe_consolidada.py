"""Parser for Spain's consolidated State legislation from the BOE open-data API.

Source: Agencia Estatal Boletín Oficial del Estado (AEBOE),
``https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/{id}`` - one XML document per
norm with ``<metadatos>``, ``<analisis>``, ``<metadata-eli>`` and ``<texto>``. The AEBOE states
that consolidation integrates the express amendments "en un solo texto, sin valor oficial" and that
consolidated texts are "meramente informativo"; only the BOE publication is official and authentic.

Format (API documentation "APIconsolidada.pdf"):

* ``<texto>`` is a sequence of ``<bloque>`` (``tipo`` precepto, encabezado, preambulo, firma, ...).
  Each block holds every ``<version>`` it has had: ``fecha_publicacion`` (publication of the norm
  that produced the wording), ``fecha_vigencia`` (entry into force of the wording, optional) and
  ``id_norma`` (that norm). The full history is in one document.
* a version holds HTML ``<p>`` paragraphs (``class="articulo"`` is the article heading) and
  ``<blockquote>`` editorial notes (amendment notes, "Téngase en cuenta...", earlier wordings).
* repealed provisions get a version whose body is "(Derogado)", "(Suprimido)", "(Sin contenido)"...;
  articles repealed together appear as a range block ("Artículos 617 a 622").
* ``fecha_caducidad`` on a block is the date from which the consolidated text stops showing it.

Mapping to ULIS:

* one ``ParsedDocument`` per consolidated version date (``fecha_publicacion``) with the provision
  wordings that version introduced (``consolidation_kind = version_delta``); the first is the text
  as originally published, so the indexed history starts with the publication of the norm;
* a provision version applies from its ``fecha_vigencia``; its end is derived from the next version,
  or is explicit when the unit disappears from the block or the block expires (``fecha_caducidad``);
  a wording without ``fecha_vigencia`` is recorded as ``prospective`` (no entry into force recorded);
* articles are split into apartados ("1."), letters ("a)") and ordinal items ("1.ª", "1.º") from the
  numbering printed at the start of their paragraphs, only when it runs in sequence; an apartado
  keeps its version while its own words are unchanged;
* editorial notes go to ``note``; text is the exact paragraph text (whitespace normalised);
* a norm repealed in full (``estatus_derogacion`` S, ``fecha_derogacion``) or annulled: the last wording of each
  provision ends on that date (earlier wordings keep the end derived from the next one); a norm repealed with
  exceptions keeps ``estatus_derogacion`` N and the BOE states the repeal only in its initial note ("Norma derogada,
  con efectos de ..."), which ends the last wordings on the date it states, except the articles the note names as
  excepted (1.2.0).
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParseSkipped

PARSER_ID = "boe_consolidada_xml"
PARSER_VERSION = "1.2.0"
SITE = "https://www.boe.es"

# BOE rango -> (ULIS instrument type, ELI type code used in the work URI)
RANGOS = {
    "constitucion": ("constitution", "c"),
    "ley organica": ("organic_law", "lo"),
    "ley": ("law", "l"),
    "real decreto legislativo": ("royal_legislative_decree", "rdlg"),
    "real decreto-ley": ("royal_decree_law", "rdl"),
    "real decreto": ("royal_decree", "rd"),
    # Lower-ranking State norms that the AEBOE also consolidates (1.1.0). The ELI type codes are the ones the BOE's
    # own ELI URIs use (https://www.boe.es/eli/es/<code>/...).
    "decreto-ley": ("decree_law", "dl"),
    "decreto": ("decree", "d"),
    "reglamento": ("regulation", "reg"),
    "orden": ("ministerial_order", "o"),
    "resolucion": ("resolution", "res"),
    "circular": ("circular", "cir"),
    "instruccion": ("instruction", "ins"),
    "acuerdo": ("agreement", "a"),
    "acuerdo internacional": ("international_agreement", "ai"),
}
BOE_ID = re.compile(r"^BOE-[A-Z]-\d{4}-\d{1,6}$")
SKIPPED_BLOCKS = {"firma", "nota_inicial", "encabezado", "cabecera"}
HEADING_CLASSES = {
    "titulo",
    "titulo_num",
    "titulo_tit",
    "capitulo",
    "capitulo_num",
    "capitulo_tit",
    "seccion",
    "subseccion",
    "libro_num",
    "libro_tit",
    "anexo",
}
# Only ordinary paragraphs can start an apartado/letter: indented ("sangrado") paragraphs are quoted
# text of other norms (e.g. amending provisions).
MARKER_CLASSES = {"parrafo", "parrafo_2", "parrafo_3"}
REPEAL = re.compile(
    r"^\(?\s*(derogad[oa]s?|suprimid[oa]s?|anulad[oa]s?|sin contenido|sin efecto)\s*\)?\.?$", re.IGNORECASE
)
# The initial note of a norm repealed as a whole: "Norma derogada, con efectos de 7 de diciembre de 2018, ...".
NOTE_REPEAL = re.compile(r"^Norma derogada\b", re.IGNORECASE)
EFFECT_DATE = re.compile(r"con efectos (?:de(?:sde)?(?: el)?|del) (\d{1,2}) de ([a-z]+) de (\d{4})")  # on folded text
MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
SUFFIXES = (
    "bis",
    "ter",
    "quater",
    "quinquies",
    "sexies",
    "septies",
    "octies",
    "nonies",
    "decies",
    "undecies",
    "duodecies",
    "terdecies",
    "quaterdecies",
    "quindecies",
)
_SUFFIX = "|".join(SUFFIXES)
ART_TITLE = re.compile(rf"^Art(?:[íi]culo|\.)?\s*(\d+)(?:\s*({_SUFFIX}))?\s*\.?$", re.IGNORECASE)
ART_WORD_TITLE = re.compile(r"^Art[íi]culo\s+([A-Za-zÁÉÍÓÚáéíóú]+)\.?$", re.IGNORECASE)
RANGE_TITLE = re.compile(
    rf"^Art(?:[íi]culos?|s?\.?)\s*(\d+)(?:\s*({_SUFFIX}))?\s+(a|al|y)\s+(\d+)(?:\s*({_SUFFIX}))?\s*\.?$",
    re.IGNORECASE,
)
DISP_TITLE = re.compile(r"^Disposici[oó]n\s+(adicional|transitoria|derogatoria|final)(?:\s+(.+?))?\s*\.?$", re.I)
DISP_PREFIX = {"adicional": "da", "transitoria": "dt", "derogatoria": "dd", "final": "df"}
DISP_KIND = {
    "da": "additional_provision",
    "dt": "transitional_provision",
    "dd": "repealing_provision",
    "df": "final_provision",
}
# Articles of the norm itself that an initial repeal note keeps in force: "Norma derogada, excepción hecha de su
# artículo 8, que permanecerá vigente ..." (Real Decreto 255/1996).
EXCEPTED_ARTICLES = re.compile(
    rf"(?:excepci[oó]n hecha de|a excepci[oó]n de|salvo) (?:su|sus|el|los) art[íi]culos? "
    rf"(\d+(?: ?(?:{_SUFFIX}))?(?:(?:, | y )\d+(?: ?(?:{_SUFFIX}))?)*)",
    re.IGNORECASE,
)
EXCEPTED_NUMBER = re.compile(rf"(\d+)(?: ?({_SUFFIX}))?", re.IGNORECASE)
NUM = re.compile(rf"^(\d{{1,3}})(?:\s+({_SUFFIX}))?\.\s+\S")
ORD = re.compile(r"^(\d{1,3})\.?\s?([ºª°])\.?\s+\S")
LET = re.compile(r"^([a-zñ])\)\s+\S")
LETTERS = "abcdefghijklmnñopqrstuvwxyz"
NOTE_LIMIT = 1000
_UNITS = {
    "primer": 1,
    "segund": 2,
    "tercer": 3,
    "cuart": 4,
    "quint": 5,
    "sext": 6,
    "septim": 7,
    "octav": 8,
    "noven": 9,
}
_ORDINAL = re.compile(
    r"^(?:(decim|vigesim|trigesim|cuadragesim|quincuagesim)[oa]?)?"
    r"(primer|segund|tercer|cuart|quint|sext|septim|octav|noven)?[oa]?$"
)
_TENS = {"decim": 10, "vigesim": 20, "trigesim": 30, "cuadragesim": 40, "quincuagesim": 50}


class BoeParseError(ValueError):
    pass


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _fold(text: str) -> str:
    """Lower case without accents (for matching words, never for text)."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _fold(text)).strip("-")


def ordinal_number(word: str) -> int | None:
    """``primera`` -> 1, ``decimotercera`` / ``Décimotercera`` -> 13, ``vigésima primera`` -> 21."""
    w = re.sub(r"[\s-]+", "", _fold(word))
    if w in ("undecimo", "undecima"):
        return 11
    if w in ("duodecimo", "duodecima"):
        return 12
    m = _ORDINAL.match(w)
    if not w or not m or not (m.group(1) or m.group(2)):
        return None
    return _TENS.get(m.group(1) or "", 0) + _UNITS.get(m.group(2) or "", 0)


def _date(value: str | None) -> date | None:
    value = (value or "").strip()
    if not re.fullmatch(r"\d{8}", value):
        return None
    try:
        parsed = date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return None
    return parsed if parsed.year >= 1800 else None


def _text(el: Element) -> str:
    return _ws("".join(el.itertext()))


def _flag(meta: Element, tag: str) -> bool:
    return (meta.findtext(tag) or "").strip().upper() == "S"


def _repealing_norm(root: Element) -> str | None:
    """The first norm the BOE analysis lists as repealing this one ("SE DEROGA"), as printed there."""
    for posterior in root.iterfind("data/analisis/referencias/posteriores/posterior"):
        if (posterior.findtext("relacion") or "").strip().upper() == "SE DEROGA":
            ident, texto = (posterior.findtext("id_norma") or "").strip(), (posterior.findtext("texto") or "").strip()
            return _ws(ident + ("" if texto.startswith(",") else " ") + texto) or None
    return None


@dataclass
class _NotedRepeal:
    on: date
    text: str  # the note as printed
    norma: str  # the repealing norm (the note version's id_norma)
    locator: str
    kept: set[tuple[int, str]]  # articles (number, suffix) the note names as excepted from the repeal


def _noted_repeal(texto: Element) -> _NotedRepeal | None:
    """The repeal of the whole norm as the initial note states it ("Norma derogada, con efectos de ...").

    A norm repealed with exceptions keeps ``estatus_derogacion`` N in the metadata; the BOE states its repeal only in a
    dated version of the ``nota_inicial`` block, which its web page prints as a banner above the text. The date is the
    one the note states ("con efectos de 7 de diciembre de 2018"), else the version's ``fecha_vigencia``; without
    either nothing is inferred.
    """
    for bloque in texto.findall("bloque"):
        if (bloque.get("tipo") or "").strip().lower() != "nota_inicial":
            continue
        for v in bloque.findall("version"):
            text = _text(v)
            if not NOTE_REPEAL.match(text):
                continue
            stated = EFFECT_DATE.search(_fold(text))
            on = None
            if stated and stated.group(2) in MONTHS:
                try:
                    on = date(int(stated.group(3)), MONTHS[stated.group(2)], int(stated.group(1)))
                except ValueError:
                    on = None
            on = on or _date(v.get("fecha_vigencia"))
            if on is None:
                continue
            kept = {
                (int(n), s.lower()) for m in EXCEPTED_ARTICLES.finditer(text) for n, s in EXCEPTED_NUMBER.findall(m[1])
            }
            norma = (v.get("id_norma") or "").strip()
            locator = f"bloque[@tipo='nota_inicial']/version[@id_norma='{norma}']"
            return _NotedRepeal(on, text, norma, locator, kept)
    return None


def table_text(el: Element) -> str:
    rows = []
    for row in el.iter("tr"):
        cells = [_text(c) for c in row if c.tag in ("td", "th")]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


# --------------------------------------------------------------------------- structures


@dataclass
class _Node:
    key: str  # local key used in eIds ("1", "4bis", "a", "1o")
    kind: str
    label: str  # language-neutral label for num_path ("1", "4 bis", "a", "1.ª")
    lines: list[str] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)

    def text(self) -> str:
        return "\n".join([*self.lines, *(c.text() for c in self.children)])


@dataclass
class _Unit:
    eid: str
    kind: str
    num_path: list[str]
    parent_eid: str | None
    text: str
    is_leaf: bool
    has_own: bool
    order: int


@dataclass
class _Version:
    pub: date
    vig: date | None
    norma: str
    order: int
    heading: str | None
    units: list[_Unit]  # empty when the version repeals the block
    marker: str | None  # "(Derogado)" etc.
    note: str | None
    signature: str

    @property
    def repealed(self) -> bool:
        return self.marker is not None


@dataclass
class _Block:
    index: int
    block_id: str
    eid: str
    top_kind: str
    num_path: list[str]
    title: str
    expires: date | None
    versions: list[_Version]
    article_key: tuple[int, str] | None = None
    covers: tuple[int, str, int, str, bool] | None = None  # range: lo, lo suffix, hi, hi suffix, "y"


@dataclass
class _Cand:
    block: _Block
    unit: _Unit
    status: str
    valid_from: date | None
    pub: date
    norma: str
    note: str | None
    heading: str | None
    dup: int = 0
    valid_to: date | None = None


# --------------------------------------------------------------------------- helpers


def _marker(text: str) -> tuple[str, tuple[int, int], str, str] | None:
    """(type, value, key, label) for a paragraph starting with "1.", "1 bis.", "a)" or "1.ª"."""
    m = ORD.match(text)
    if m:
        n = int(m.group(1))
        mark = "º" if m.group(2) in "º°" else "ª"
        return "ord", (n, 0), f"{n}o", f"{n}.{mark}"
    m = NUM.match(text)
    if m:
        n, suffix = int(m.group(1)), (m.group(2) or "").lower()
        rank = SUFFIXES.index(suffix) + 1 if suffix else 0
        return "num", (n, rank), f"{n}{suffix}", f"{n} {suffix}".strip()
    m = LET.match(text)
    if m:
        letter = m.group(1)
        return "let", (LETTERS.index(letter), 0), letter, letter
    return None


def _is_first(kind: str, value: tuple[int, int]) -> bool:
    return value == ((0, 0) if kind == "let" else (1, 0))


def _is_next(kind: str, current: tuple[int, int], value: tuple[int, int]) -> bool:
    if kind == "let":
        nxt = current[0] + 1
        # "ñ" is usually skipped in lettered lists
        return value[0] == nxt or (LETTERS[current[0]] == "n" and value[0] == nxt + 1)
    return value == (current[0] + 1, 0) or (value[0] == current[0] and value[1] > current[1])


def split_units(lines: list[tuple[str, str | None]], root: _Node) -> None:
    """Distribute paragraphs into apartados / letters / ordinal items following their numbering.

    A marker starts a unit only when it continues the sequence at its level (or starts a new level
    at "1" / "a"); anything else - including paragraphs inside a «quotation» - is continuation text
    of the current unit.
    """
    stack: list[tuple[str | None, tuple[int, int], _Node]] = [(None, (0, 0), root)]
    quote_depth = 0
    kinds = {"num": "paragraph", "let": "point", "ord": "item"}
    for text, cls in lines:
        placed = False
        found = _marker(text) if quote_depth == 0 and cls in MARKER_CLASSES else None
        if found:
            mtype, value, key, label = found
            idx = next((i for i in range(len(stack) - 1, 0, -1) if stack[i][0] == mtype), None)
            if idx is not None and _is_next(mtype, stack[idx][1], value):
                del stack[idx:]
                placed = True
            elif idx is None and _is_first(mtype, value):
                placed = True
            if placed:
                node = _Node(key=key, kind=kinds[mtype], label=label, lines=[text])
                stack[-1][2].children.append(node)
                stack.append((mtype, value, node))
        if not placed:
            stack[-1][2].lines.append(text)
        quote_depth = max(0, quote_depth + text.count("«") - text.count("»"))


def _flatten(root: _Node, eid: str, kind: str, num_path: list[str], order_base: int) -> list[_Unit]:
    units: list[_Unit] = []

    def visit(node: _Node, node_eid: str, node_kind: str, path: list[str], parent: str | None) -> None:
        units.append(
            _Unit(
                eid=node_eid,
                kind=node_kind,
                num_path=path,
                parent_eid=parent,
                text=node.text(),
                is_leaf=not node.children,
                has_own=bool(node.lines) and bool(node.children),
                order=order_base + len(units),
            )
        )
        for child in node.children:
            visit(child, f"{node_eid}-{child.key}", child.kind, [*path, child.label], node_eid)

    visit(root, eid, kind, num_path, None)
    return units


def _note(parts: list[str]) -> str | None:
    text = _ws(" ".join(p for p in parts if p))
    if len(text) > NOTE_LIMIT:
        text = text[: NOTE_LIMIT - 4].rstrip() + " […]"
    return text or None


def _parse_version(v: Element, order: int, block: _Block, splittable: bool) -> tuple[str, _Version | None]:
    """Return (role, version). Role is "heading" for versions that only print a heading."""
    paras: list[tuple[str, str | None]] = []
    notes: list[str] = []
    images = 0
    for child in v:
        if child.tag == "blockquote":
            notes.extend(_text(p) for p in child.iter("p"))
            images += sum(1 for _ in child.iter("img"))
        elif child.tag == "p":
            text = _text(child)
            if text and child.get("class") == "cita_con_pleca" and text.startswith("(*)"):
                notes.append(text)  # editorial remark of the consolidators, not law text
            elif text:
                paras.append((text, child.get("class")))
        elif child.tag == "table":
            text = table_text(child)
            if text:
                paras.append((text, "table"))
        elif child.tag == "img":
            images += 1
    if images:
        notes.append(f"[ULIS: {images} image(s) in the source not reproduced in the text]")
    classes = {cls for _, cls in paras}
    if block.top_kind not in ("preamble", "enacting_terms", "final_part") and (
        not paras or (paras[0][1] != "articulo" and classes <= HEADING_CLASSES)
    ):
        return "heading", None
    heading = None
    if paras and paras[0][1] == "articulo":
        heading = paras.pop(0)[0]
    pub = _date(v.get("fecha_publicacion"))
    if pub is None:
        raise BoeParseError(f"block {block.block_id}: version without a valid fecha_publicacion")
    body = "\n".join(t for t, _ in paras)
    marker = body if REPEAL.match(_ws(body)) else None
    tail = re.search(r"\([^()]*\)\s*\.?$", heading or "")
    if not paras and tail and REPEAL.match(tail.group(0)):
        marker = tail.group(0)  # "Artículo 1863 bis. (Derogado)" in the heading paragraph itself
    root = _Node(key="", kind=block.top_kind, label="")
    if marker is None:
        if splittable:
            split_units(paras, root)
        else:
            root.lines.extend(t for t, _ in paras)
    units = (
        []
        if marker is not None
        else _flatten(root, block.eid, block.top_kind, list(block.num_path), block.index * 1000)
    )
    return "provision", _Version(
        pub=pub,
        vig=_date(v.get("fecha_vigencia")),
        norma=(v.get("id_norma") or "").strip(),
        order=order,
        heading=heading,
        units=units,
        marker=marker,
        note=_note([marker or "", *notes]) if marker else _note(notes),
        signature=f"{heading}\n{body}",
    )


def _classify(
    bloque: Element, context: str, in_decree: bool
) -> tuple[str, str, list[str], tuple[int, str] | None, tuple[int, str, int, str, bool] | None] | None:
    """(eid stem, top kind, num_path, article key, range) for a block, or None when skipped."""
    tipo = (bloque.get("tipo") or "").strip().lower()
    title = _ws(bloque.get("titulo") or "")
    if tipo in SKIPPED_BLOCKS:
        return None
    if tipo == "preambulo":
        return "preambulo", "preamble", [], None, None
    if tipo == "parte_dispositiva":
        return "parte-dispositiva", "enacting_terms", [], None, None
    if tipo == "parte_final":
        return "parte-final", "final_part", [], None, None
    if tipo != "precepto":
        return None
    prefix, kind_prefix = ("rd-", "decree_") if in_decree else ("", "")
    m = ART_TITLE.match(title)
    if m:
        suffix = (m.group(2) or "").lower()
        number = f"{int(m.group(1))} {suffix}".strip()
        key = (int(m.group(1)), suffix)
        return f"{prefix}art-{number.replace(' ', '')}", f"{kind_prefix}article", [number], key, None
    m = RANGE_TITLE.match(title)
    if m:
        lo, lo_s, conj, hi, hi_s = m.groups()
        lo_s, hi_s = (lo_s or "").lower(), (hi_s or "").lower()
        label = f"{int(lo)}{' ' + lo_s if lo_s else ''} {'y' if conj.lower() == 'y' else 'a'} {int(hi)}"
        label += f" {hi_s}" if hi_s else ""
        covers = (int(lo), lo_s, int(hi), hi_s, conj.lower() == "y")
        stem = f"{prefix}arts-{int(lo)}{lo_s}-{int(hi)}{hi_s}"
        return stem, f"{kind_prefix}article_range", [label], None, covers
    m = ART_WORD_TITLE.match(title)
    if m and (ordinal_number(m.group(1)) or _fold(m.group(1)) == "unico"):
        art_word = m.group(1).lower()
        return f"{prefix}art-{slug(art_word)}", f"{kind_prefix}article", [art_word], None, None
    m = DISP_TITLE.match(title)
    if m:
        code, word = DISP_PREFIX[_fold(m.group(1))], (m.group(2) or "").strip()
    elif ordinal_number(title) or _fold(title) == "unica":
        ctx = _fold(context)
        code = next((c for k, c in DISP_PREFIX.items() if k in ctx), "")
        word = title
        if not code:
            return f"{prefix}bloque-{slug(title)}", f"{kind_prefix}block", [title], None, None
    else:
        return f"{prefix}bloque-{slug(title or bloque.get('id') or '')}", f"{kind_prefix}block", [title], None, None
    n = ordinal_number(word) if word else None
    stem = f"{prefix}{code}" + (f"-{n}" if n else f"-{slug(word)}" if word else "")
    return stem, f"{kind_prefix}{DISP_KIND[code]}", [word.lower() if word else ""], None, None


def _root_unit(block: _Block) -> _Unit:
    return _Unit(block.eid, block.top_kind, list(block.num_path), None, "", True, False, block.index * 1000)


def _chain_candidates(
    block: _Block, chain: list[_Version], range_repeals: list[tuple[_Block, _Version]]
) -> list[_Cand]:
    """Provision versions of one block from its dated versions in order of entry into force."""
    cands: list[_Cand] = []
    open_: dict[str, _Cand] = {}  # current wording of each unit of the block
    for v in chain:
        if v.repealed:
            root = open_.get(block.eid)
            repealed = [
                _Cand(
                    block,
                    root.unit if root else _root_unit(block),
                    "repealed",
                    v.vig,
                    v.pub,
                    v.norma,
                    v.note,
                    v.heading,
                )
            ]
            repealed += [
                _Cand(block, c.unit, "repealed", v.vig, v.pub, v.norma, v.note, None)
                for eid, c in open_.items()
                if eid != block.eid and c.status == "in_force"
            ]
            cands.extend(repealed)
            open_ = {c.unit.eid: c for c in repealed}
            continue
        present: dict[str, _Cand] = {}
        for unit in v.units:
            prev = open_.get(unit.eid)
            if unit.eid != block.eid and prev and prev.status == "in_force" and prev.unit.text == unit.text:
                present[unit.eid] = prev  # this apartado's own words did not change
                continue
            heading = v.heading if unit.eid == block.eid else None
            cand = _Cand(block, unit, "in_force", v.vig, v.pub, v.norma, v.note, heading)
            cands.append(cand)
            present[unit.eid] = cand
        for eid, cand in open_.items():
            if eid not in present and cand.status == "in_force" and cand.valid_to is None:
                cand.valid_to = v.vig  # the unit is no longer part of the block's wording
        open_ = present
    last = chain[-1] if chain else None
    if last is None or last.repealed or last.vig is None:
        return cands
    still_open = [c for c in open_.values() if c.status == "in_force" and c.valid_to is None]
    later = sorted((r for r in range_repeals if r[1].vig and r[1].vig > last.vig), key=lambda r: r[1].vig or date.max)
    if later:
        # e.g. "Artículos 617 a 622. (Derogados)": the range block repeals each article it covers.
        rng, rv = later[0]
        note = _note([f"{rng.title}:", rv.note or ""])
        for c in still_open:
            heading = c.heading if c.unit.eid == block.eid else None
            cands.append(_Cand(block, c.unit, "repealed", rv.vig, rv.pub, rv.norma, note, heading))
    elif block.expires and block.expires > last.vig:
        for c in still_open:
            c.valid_to = block.expires
            c.note = _note(
                [
                    c.note or "",
                    f"[ULIS: the consolidated text stops showing this block from {block.expires.isoformat()} "
                    "(BOE fecha_caducidad); the source does not state the date on which this wording ceased "
                    "to apply.]",
                ]
            )
    return cands


def _last_open_wordings(cands: list[_Cand], end: date) -> list[_Cand]:
    """The wordings a repeal of the whole norm on ``end`` ends: of each unit, the latest wording that started before
    ``end``, when it is in force and has no end. An earlier wording keeps the end derived from the next one; an
    explicit end there would make it overlap its successors (1.2.0: before, every wording of the 1995 Workers'
    Statute's art. 11 read as in force until 2015-11-13)."""
    latest: dict[str, date] = {}
    for c in cands:
        if c.status in ("in_force", "repealed") and c.valid_from is not None and c.valid_from < end:
            latest[c.unit.eid] = max(latest.get(c.unit.eid, c.valid_from), c.valid_from)
    return [
        c
        for c in cands
        if c.status == "in_force"
        and c.valid_to is None
        and c.valid_from is not None
        and c.valid_from == latest.get(c.unit.eid)
    ]


def _covered(block: _Block, article: _Block) -> bool:
    if block.covers is None or article.article_key is None:
        return False
    lo, lo_s, hi, hi_s, pair = block.covers
    n, suffix = article.article_key
    if pair:
        return (n, suffix) in ((lo, lo_s), (hi, hi_s))
    if lo_s or hi_s:
        return lo <= n <= hi and suffix == (lo_s or hi_s)
    return lo <= n <= hi


# --------------------------------------------------------------------------- main


def parse_boe_consolidada(content: bytes) -> list[ParsedDocument]:
    try:
        root = fromstring(content, forbid_dtd=True)
    except Exception as exc:
        raise BoeParseError(f"not well-formed or unsafe XML: {exc}") from exc
    if root.tag != "response":
        raise BoeParseError(f"unexpected root element {root.tag!r}")
    code = (root.findtext("status/code") or "").strip()
    if code != "200":
        raise BoeParseError(f"API status {code!r}: {root.findtext('status/text')}")
    meta = root.find("data/metadatos")
    texto = root.find("data/texto")
    if meta is None or texto is None:
        raise BoeParseError("missing <metadatos> or <texto> (the full norm document is required)")
    boe_id = (meta.findtext("identificador") or "").strip()
    if not BOE_ID.match(boe_id):
        raise BoeParseError(f"invalid identifier {boe_id!r}")
    ambito = meta.find("ambito")
    if ambito is None or ambito.get("codigo") != "1":
        raise BoeParseError(f"{boe_id}: only State (estatal) legislation is supported")
    rango = _ws(meta.findtext("rango") or "")
    if _fold(rango) not in RANGOS:
        raise BoeParseError(f"{boe_id}: unsupported rango {rango!r}")
    instrument_type, eli_type = RANGOS[_fold(rango)]
    title = _ws(meta.findtext("titulo") or "")
    numero = _ws(meta.findtext("numero_oficial") or "") or None
    updated = (meta.findtext("fecha_actualizacion") or "").strip()
    if not re.fullmatch(r"\d{8}T\d{6}Z", updated):
        raise BoeParseError(f"{boe_id}: invalid fecha_actualizacion {updated!r}")
    estado = _ws(meta.findtext("estado_consolidacion") or "")
    finalised = _fold(estado) == "finalizado"
    work_uri = f"/es/{eli_type}/{boe_id}"
    work_date = _date(meta.findtext("fecha_disposicion"))
    published = _date(meta.findtext("fecha_publicacion"))
    in_force = _date(meta.findtext("fecha_vigencia"))
    ext = {
        "boe_id": boe_id,
        "rango": rango,
        "numero_oficial": numero,
        "eli": (meta.findtext("url_eli") or "").strip() or None,
        "departamento": _ws(meta.findtext("departamento") or "") or None,
    }
    # Whole-norm repeal / annulment: the text of such a norm keeps showing its last wording, so without this every
    # provision of a repealed norm would still read as in force. The dates are the source's (metadatos).
    repealed_on = _date(meta.findtext("fecha_derogacion")) if _flag(meta, "estatus_derogacion") else None
    annulled_on = _date(meta.findtext("fecha_anulacion")) if _flag(meta, "estatus_anulacion") else None
    if repealed_on:
        ext["fecha_derogacion"] = repealed_on.isoformat()
    if annulled_on:
        ext["fecha_anulacion"] = annulled_on.isoformat()
    if _flag(meta, "vigencia_agotada"):
        ext["vigencia_agotada"] = True
    norm_end = min((d for d in (repealed_on, annulled_on) if d), default=None)
    repealer = _repealing_norm(root) if repealed_on else None
    # A norm repealed with exceptions keeps estatus_derogacion N; its repeal is stated in the initial note (1.2.0).
    noted = _noted_repeal(texto) if norm_end is None else None

    # ---- pass 1: blocks and their versions
    bloques = texto.findall("bloque")
    annex_at = next((i for i, b in enumerate(bloques) if any(p.get("class") == "anexo" for p in b.iter("p"))), None)
    blocks: list[_Block] = []
    used: set[str] = set()
    context = ""
    for index, bloque in enumerate(bloques):
        tipo = (bloque.get("tipo") or "").strip().lower()
        title_attr = _ws(bloque.get("titulo") or "")
        if tipo in ("encabezado", "cabecera"):
            context = title_attr or context
            continue
        classified = _classify(bloque, context, annex_at is not None and index < annex_at)
        if classified is None:
            continue
        stem, top_kind, num_path, article_key, covers = classified
        eid = stem if stem not in used else f"{stem}--{slug(bloque.get('id') or str(index))}"
        used.add(eid)
        block = _Block(
            index=index,
            block_id=bloque.get("id") or "",
            eid=eid,
            top_kind=top_kind,
            num_path=num_path,
            title=title_attr,
            expires=_date(bloque.get("fecha_caducidad")),
            versions=[],
            article_key=article_key,
            covers=covers,
        )
        splittable = top_kind.endswith(
            ("article", "additional_provision", "transitional_provision", "repealing_provision", "final_provision")
        )
        for order, v in enumerate(bloque.findall("version")):
            _, parsed = _parse_version(v, order, block, splittable)
            if parsed is not None:
                block.versions.append(parsed)
        if not block.versions:
            context = title_attr or context  # a heading typed as "precepto"
            continue
        blocks.append(block)
    if not blocks:
        raise ParseSkipped(
            f"{boe_id}: the consolidated text holds no provision (no article, disposition or block with words)"
        )

    # ---- pass 2: per-block version chains
    chains: dict[str, tuple[list[_Version], list[tuple[_Version, int]], _Version | None]] = {}
    for block in blocks:
        primaries: dict[tuple[date, date | None], _Version] = {}
        dups: list[tuple[_Version, int]] = []
        for v in block.versions:
            key = (v.pub, v.vig)
            if key not in primaries:
                primaries[key] = v
            elif v.signature != primaries[key].signature and v.repealed == primaries[key].repealed:
                dups.append((v, sum(1 for d, _ in dups if (d.pub, d.vig) == key) + 1))
        dated: list[_Version] = []
        for v in sorted((v for v in primaries.values() if v.vig), key=lambda v: (v.pub, v.order)):
            # A wording replaced, before it took effect, by a later-published one never applied.
            dated = [u for u in dated if not (u.pub < v.pub and u.vig is not None and v.vig and u.vig >= v.vig)]
            dated.append(v)
        chain = sorted(dated, key=lambda v: (v.vig or date.max, v.pub, v.order))
        undated = [v for v in primaries.values() if v.vig is None]
        prospective = max(undated, key=lambda v: (v.pub, v.order)) if undated else None
        kept = {id(v) for v in chain} | ({id(prospective)} if prospective else set())
        chains[block.eid] = (chain, [(d, k) for d, k in dups if id(primaries[(d.pub, d.vig)]) in kept], prospective)

    # Range blocks ("Artículos 617 a 622. (Derogados)") repeal the articles they cover.
    range_repeals: dict[str, list[tuple[_Block, _Version]]] = defaultdict(list)
    articles = [b for b in blocks if b.article_key is not None]
    for block in blocks:
        if block.covers is None:
            continue
        for v in chains[block.eid][0]:
            if v.repealed:
                for article in articles:
                    if _covered(block, article):
                        range_repeals[article.eid].append((block, v))

    # ---- pass 3: provision versions
    cands: list[_Cand] = []
    for block in blocks:
        chain, dups, prospective = chains[block.eid]
        cands.extend(_chain_candidates(block, chain, range_repeals.get(block.eid, [])))
        if prospective is not None and not prospective.repealed:  # an undated repeal has no date to record
            for unit in prospective.units:
                heading = prospective.heading if unit.eid == block.eid else None
                cands.append(
                    _Cand(
                        block, unit, "prospective", None, prospective.pub, prospective.norma, prospective.note, heading
                    )
                )
        for dup, k in dups:
            # A second, different listing of the same version: recorded under its own expression so the
            # pipeline reports the discrepancy as a conflict instead of silently picking one.
            status = "repealed" if dup.repealed else ("in_force" if dup.vig else "prospective")
            unit = dup.units[0] if dup.units else _root_unit(block)
            cands.append(_Cand(block, unit, status, dup.vig, dup.pub, dup.norma, dup.note, dup.heading, dup=k))
    blocks_by_eid = {b.eid for b in blocks}
    for c in cands:
        if c.unit.eid != c.block.eid and c.unit.eid in blocks_by_eid:
            raise BoeParseError(f"{boe_id}: identifier {c.unit.eid} used by two provisions")

    if norm_end is not None:
        how, field_name = (
            ("repealed in full", "fecha_derogacion") if norm_end == repealed_on else ("annulled", "fecha_anulacion")
        )
        remark = (
            f"[ULIS: the BOE metadata state that the whole norm was {how} on {norm_end.isoformat()} "
            f"({field_name}); the consolidated text keeps showing its last wording.]"
        )
        for c in _last_open_wordings(cands, norm_end):
            c.valid_to = norm_end
            c.note = _note([c.note or "", remark])
    elif noted is not None:
        # Ley Orgánica 15/1999: "Norma derogada, con efectos de 7 de diciembre de 2018, sin perjuicio de lo previsto en
        # la disposición adicional 14 de la Ley Orgánica 3/2018 ...". The last wording of each provision ends on that
        # date except the articles the note itself names as excepted; an exception made by reference to another norm
        # cannot be resolved here, so the note is quoted first on every wording it ends.
        quoted = noted.text if len(noted.text) <= 600 else noted.text[:596].rstrip() + " […]"
        remark = (
            f"[ULIS: the BOE's initial note states that the norm is repealed from {noted.on.isoformat()}: "
            f"«{quoted}» The consolidated text keeps showing its last wording; any exception the note states "
            "is quoted, not applied, unless it names an article of this norm.]"
        )
        for c in _last_open_wordings(cands, noted.on):
            if c.block.article_key not in noted.kept:
                c.valid_to = noted.on
                c.note = _note([remark, c.note or ""])

    # Index units: a provision is indexed if any of its versions is a leaf or has its own words.
    indexable: dict[str, bool] = defaultdict(bool)
    for c in cands:
        indexable[c.unit.eid] |= c.unit.is_leaf or c.unit.has_own

    # ---- pass 4: one document per consolidated version date
    by_delta: dict[tuple[date, int], list[_Cand]] = defaultdict(list)
    for c in cands:
        by_delta[(c.pub, c.dup)].append(c)
    currency = date(int(updated[:4]), int(updated[4:6]), int(updated[6:8])) if finalised else None
    outstanding: list[dict[str, object]] = []
    if not finalised:
        outstanding.append(
            {
                "affects_eid": None,
                "affected_label": None,
                "by_uri": None,
                "by_title": None,
                "description": f"The publisher marks this consolidation as '{estado}': amendments published "
                "since the last consolidated version may not be incorporated.",
            }
        )
    docs: list[ParsedDocument] = []
    first = min(by_delta)
    for pub, listing in sorted(by_delta):
        group = by_delta[(pub, listing)]
        provisions = [
            ParsedProvision(
                eid=c.unit.eid,
                kind=c.unit.kind,
                num_path=list(c.unit.num_path),
                top_kind=c.block.top_kind,
                heading=c.heading,
                text="" if c.status == "repealed" else c.unit.text,
                parent_eid=c.unit.parent_eid,
                sort_order=c.unit.order,
                status=c.status,  # type: ignore[arg-type]
                valid_from=c.valid_from,
                valid_to=c.valid_to,
                extent=["ES"],
                note=c.note,
                refs=[],
                is_leaf=c.unit.is_leaf,
                has_own_content=indexable[c.unit.eid] and not c.unit.is_leaf,
            )
            for c in sorted(group, key=lambda c: (c.unit.order, c.valid_from or date.max, c.status))
        ]
        events = [
            ParsedEvent(
                "version_change",
                pub,
                f"{SITE}/buscar/doc.php?id={norma}",
                f"Consolidated version published on {pub.isoformat()} ({norma})",
                f"version[@id_norma='{norma}'][@fecha_publicacion='{pub:%Y%m%d}']",
            )
            for norma in sorted({c.norma for c in group if c.norma and c.norma != boe_id})
        ]
        if (pub, listing) == first:
            if norm_end is not None:
                what = "repealed in full" if norm_end == repealed_on else "annulled"
                events.append(
                    ParsedEvent(
                        "repeal",
                        norm_end,
                        None,
                        f"Norm {what} (BOE metadata)"
                        + (f"; repealing norm per the BOE analysis: {repealer}" if repealer else ""),
                        "metadatos/fecha_derogacion" if norm_end == repealed_on else "metadatos/fecha_anulacion",
                    )
                )
            elif noted is not None:
                events.append(
                    ParsedEvent(
                        "repeal",
                        noted.on,
                        f"{SITE}/buscar/doc.php?id={noted.norma}" if BOE_ID.match(noted.norma) else None,
                        _note([f"Norm repealed (BOE initial note): {noted.text}"]),
                        noted.locator,
                    )
                )
            if published:
                events.append(ParsedEvent("publication", published, None, None, "metadatos/fecha_publicacion"))
            if in_force:
                events.append(ParsedEvent("entry_into_force", in_force, None, None, "metadatos/fecha_vigencia"))
        suffix = f"/listado-{listing + 1}" if listing else ""
        docs.append(
            ParsedDocument(
                doc_kind="legislation",
                instrument_type=instrument_type,
                jurisdiction_id="ES",
                work_uri=work_uri,
                expression_uri=f"{work_uri}/spa@{pub.isoformat()}{suffix};actualizacion={updated}",
                work_title=title,
                title=title,
                short_title=title.rstrip(". ") or None,
                number=numero,
                work_date=work_date,
                language="es",
                authoritative=False,
                text_origin="official_revised",
                consolidation_kind="version_delta",
                version_date=pub,
                currency_date=currency,
                extent=["ES"],
                ext_module="es",
                ext={k: v for k, v in ext.items() if v is not None},
                outstanding_changes=list(outstanding),
                events=events,
                provisions=provisions,
                validity_basis="source_periods",
            )
        )
    return docs
