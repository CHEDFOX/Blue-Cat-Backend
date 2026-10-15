"""Parser for Argentine national legislation from InfoLEG (Ministerio de Justicia de la Nación).

InfoLEG ("Leyes Argentinas", https://www.argentina.gob.ar/normativa) shows every national norm as
a page with the norm's data (number, organ, sanction and publication dates, Boletín Oficial number
and page) and, behind it, the norm's text in HTML: the "texto original" and, when the norm has been
amended, the "texto actualizado" (updated by the InfoLEG editors, who record only express
modifications). The authentic text is the Boletín Oficial (decretos 659/1947 and 207/2016, quoted in
the site's "Aviso legal"); InfoLEG's updated text is informative. Texts are therefore
``authoritative: false``: ``official_revised`` for an updated text, ``official_conversion`` for an
original text.

Input: one *bundle* made by the connector (JSON, ``ulis-infoleg-bundle/1``): the norm's summary page
and the text page it links to, each exactly as received (HTML string, URL, retrieval time). The pages
state no date of update, so the retrieval date is the date the source attests the text
(``validity_basis = "attested_at_edition"``; provisions carry no start date).

What the text pages look like (what ULIS does with it):

* the text is ``<div class="infoleg-norma-body infoleg-mode-(actualizado|original)">``: free HTML in
  which paragraphs are ``<p>`` or ``<br>`` separated lines. The parser flattens it to lines;
* an article starts a line: ``ARTICULO 1°.- ...``, ``Art. 2° — ...``, ``Artículo 14 bis.- ...``,
  ``ARTICULO 78 bis. — ...`` (suffix in any case: ``Art. 132 BIS.``). Following lines are its text
  until the next article, structural heading or end marker. Each article is one provision (leaf):
  its paragraphs and lettered / numbered items stay in its text, because the page does not mark
  them reliably;
* structure lines (``LIBRO``, ``TITULO``, ``CAPITULO``, ``SECCION``, ``PARAGRAFO``, ``PRIMERA PARTE``,
  with the title on the next line) give each article its ``context_heading``; so do the page's centred lines
  that are no numbered heading (``Anulabilidad.`` between two articles of Ley 19.549, ``<seccion>`` titles of
  the Code): a centred line is a title, never article text;
* headings of articles: the three styles in use are kept as they are. ``paren`` (``ARTICULO 1° —
  (Objeto).``) and ``line`` (``Art. 2° — Ambito de aplicación.`` when that is all the line says and
  text follows; a longer article line starts the text itself) give a heading; in ``inline`` laws (the
  codes, the Constitution) the rubric is part of the first sentence and stays in the text;
* editorial notes written by InfoLEG ("(Artículo sustituido por art. 1° de la Ley N° 27.077 B.O.
  19/12/2014)", "(Nota Infoleg: ...)", "(Corresponde al Artículo 37)", ...) are lines in parentheses
  after the article they concern, or trailing parentheticals of a line. They go to ``note``, never to
  ``text``. An article without text and with a note saying "derogado" is ``repealed``;
* each law has a start and end marker (the Constitution: from ``PREÁMBULO`` to the last transitory
  clause; the Civil and Commercial Code: Anexo I of Ley 26.994) and the generic ends
  ("Antecedentes normativos", "DADA EN LA SALA ..."). The index (``INDICE TEMATICO``), the
  approving law's own articles, the annex of amendments and the "Antecedentes normativos" list of
  amending norms are not ingested.

Not handled (kept in the article text or dropped, see docs/jurisdictions/ar.md): items and
paragraphs as provisions of their own, tables, footnotes, the text of annexes, and the amendment
history (``Antecedentes normativos``). An unexpected page raises ``InfolegParseError``.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParseSkipped

PARSER_ID = "infoleg_ar_html"
PARSER_VERSION = "1.1.0"
BUNDLE_FORMAT = "ulis-infoleg-bundle/1"
# 2: the metadata come from the open-data CSV row (no summary page); any national law, not only the six instruments.
BUNDLE_FORMAT_V2 = "ulis-infoleg-bundle/2"
SITE = "https://www.argentina.gob.ar"
JURISDICTION = "AR"
NOTE_LIMIT = 4000
HEADING_LIMIT = 150  # an article line longer than this in a "line" law starts the text itself


class InfolegParseError(ValueError):
    pass


@dataclass(frozen=True)
class InstrumentInfo:
    """What the page itself does not say: the instrument's name, URI and layout conventions."""

    infoleg_id: int
    uri: str
    instrument_type: str
    title: str
    short_title: str | None
    heading_style: Literal["inline", "paren", "line"]
    start: str | None = None  # regex of the line where the instrument's own text starts
    end: str | None = None  # regex of the line after its last line (besides the generic ends)
    transitional_clauses: bool = False  # "DISPOSICIONES TRANSITORIAS" with ordinal clauses (Constitution)


INSTRUMENTS: dict[int, InstrumentInfo] = {
    804: InstrumentInfo(
        804,
        "/ar/constitucion/nacional",
        "constitution",
        "Constitución de la Nación Argentina",
        "Constitución Nacional",
        "inline",
        start=r"^PREÁMBULO$",
        end=r"^DADA EN LA SALA DE SESIONES DE LA CONVENCI",
        transitional_clauses=True,
    ),
    235975: InstrumentInfo(
        235975,
        "/ar/ley/26994",
        "law",
        "Código Civil y Comercial de la Nación",
        "Código Civil y Comercial",
        "inline",
        start=r"^ANEXO I$",
        end=r"^ANEXO II$",
    ),
    16546: InstrumentInfo(
        16546, "/ar/ley/11179", "law", "Código Penal de la Nación Argentina", "Código Penal", "inline"
    ),
    64790: InstrumentInfo(64790, "/ar/ley/25326", "law", "Ley de Protección de los Datos Personales", None, "paren"),
    25552: InstrumentInfo(25552, "/ar/ley/20744", "law", "Ley de Contrato de Trabajo", None, "line"),
    22363: InstrumentInfo(22363, "/ar/ley/19549", "law", "Ley de Procedimientos Administrativos", None, "inline"),
}
GENERIC_END = re.compile(r"^(?:Antecedentes normativos|DADA EN LA SALA\b|[—–-]\s*REGISTRAD[OA] BAJO\b)")

RESOLVE = re.compile(
    r"^https?://(?:www\.)?argentina\.gob\.ar/normativa/nacional/(?:[a-z0-9]+(?:-[a-z0-9]+)*-)?(\d+)"
    r"(?:/(?:texto|actualizacion|normas-modificadas|normas-modifican))?/?$"
)
LEGACY_RESOLVE = re.compile(r"^https?://servicios\.infoleg\.gob\.ar/infolegInternet/verNorma\.do\?id=(\d+)$")


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map an InfoLEG address to (ULIS work URI, None): the pages have no per-article addresses."""
    m = RESOLVE.match(uri) or LEGACY_RESOLVE.match(uri)
    if not m:
        return None
    info = INSTRUMENTS.get(int(m.group(1)))
    return (info.uri, None) if info else None


# --------------------------------------------------------------------------- page reading

_CENTER = "\x02"  # prefix of a line that the page centres: a heading, never text of an article
_SECCION = re.compile(r"<seccion>(.*?)</seccion>", re.S)
_CENTER_BLOCK = re.compile(r"<(p|div)\b[^>]*text-align:\s*center[^>]*>(.*?)</\1>", re.S | re.I)
_BREAKS = re.compile(r"(?i)<br\s*/?>|</?(?:p|div|tr|td|th|table|tbody|thead|details|summary|li|ul|ol|h[1-6])\b[^>]*>")
_FORBIDDEN = re.compile(r"(?i)<(?:sup|sub|script|style)\b")
_IMAGE = re.compile(r"(?i)<img\b[^>]*>")
_IMAGE_LINE = "\x01IMAGE"


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def _body_html(page: str, mode: str) -> str:
    marker = f'<div class="infoleg-norma-body infoleg-mode-{mode}"'
    start = page.find(marker)
    if start < 0:
        raise InfolegParseError(f"no infoleg-norma-body in mode {mode!r} (not an InfoLEG {mode} text page)")
    depth = 0
    for m in re.finditer(r"<(/?)div\b[^>]*>", page[start:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return page[start : start + m.end()]
    raise InfolegParseError("unbalanced infoleg-norma-body")


def _lines(fragment: str) -> list[str]:
    if _FORBIDDEN.search(fragment):
        raise InfolegParseError("unsupported markup (sup/sub/script) in the text; refusing to alter it silently")
    fragment = _IMAGE.sub(f"\n{_IMAGE_LINE}\n", fragment)
    fragment = _SECCION.sub(lambda m: f'<div style="text-align: center">{m.group(1)}</div>', fragment)

    def centered(m: re.Match[str]) -> str:
        inner = htmllib.unescape(re.sub(r"<[^>]+>", "", _BREAKS.sub("\n", m.group(2))))
        cleaned = (re.sub(r"\s+", " ", x).strip() for x in inner.split("\n"))
        return "\n" + "\n".join(f"{_CENTER}{x}" for x in cleaned if x) + "\n"

    fragment = _CENTER_BLOCK.sub(centered, fragment)
    fragment = _BREAKS.sub("\n", fragment)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    fragment = htmllib.unescape(fragment)
    lines = [re.sub(r"\s+", " ", x).strip() for x in fragment.split("\n")]
    return [x for x in lines if x and x != _CENTER]


def _bare(line: str) -> str:
    return line.lstrip(_CENTER)


# --------------------------------------------------------------------------- line classification

_SUFFIXES = "bis|ter|qu[aá]ter|quinquies|quinque|quinto|sexies|sexto|septies|octies|nonies|decies|undecies|duodecies"
HEAD = re.compile(
    r"^(?:ART[IÍ]CULO|Art[ií]culo|Art\.)\s+(?P<num>\d{1,4})\s*(?:[º°]\s*)?"
    rf"(?:(?P<suffix>(?i:{_SUFFIXES}))\b\.?\s*)?(?:[º°]\s*)?"
    # The nineteenth-century laws print "Art. 1° Abrese un crédito ...": an ordinal sign and a capital, no stop.
    r"(?:(?:(?:,-|[.\-—–:])+\s*)+|$|(?<=[º°])\s*(?=[A-ZÁÉÍÓÚÑ¡«“\"(]))(?P<rest>.*)$"
)
STRUCT = re.compile(
    r"^(?:(?P<kind>LIBRO|T[IÍ]TULO|CAP[IÍ]TULO|SECCI[OÓ]N|PAR[AÁ]GRAFO)\s+(?P<n>[\wÁÉÍÓÚáéíóúñÑºª°]+)"
    rf"(?:\s+(?i:{_SUFFIXES})\b)?(?:\.?|\s*[:.\-–]\s*(?P<title>.{{1,150}}))"
    r"|(?P<part>(?:PRIMERA|SEGUNDA)\s+PARTE))$",
    re.I,
)
NOTE_WORDS = re.compile(
    r"B\.O\.|Nota Infoleg|Corresponde|sustituid|incorporad|derogad|vetad|observad|modificad|texto según|fe de erratas",
    re.I,
)
NOTE_KEYS = re.compile(r"sustituid|incorporad|derogad|vetad|modificad|observad", re.I)
REPEALED = re.compile(r"^\(?\s*(?:Art[íi]culo\s+)?(?:derogado|suprimido|sin efecto)\b", re.I)
ORDINAL_CLAUSE = re.compile(
    r"^(?P<word>Primera|Segunda|Tercera|Cuarta|Quinta|Sexta|S[ée]ptima|Octava|Novena|D[ée]cima|Und[ée]cim[oa]|"
    r"Duod[ée]cima|Decimotercera|Decimocuarta|Decimoquinta|Decimosexta|Decimos[ée]ptima)\.\s+(?P<rest>.*)$"
)
CLAUSE_NUMBER = {
    "primera": 1, "segunda": 2, "tercera": 3, "cuarta": 4, "quinta": 5, "sexta": 6, "séptima": 7, "septima": 7,
    "octava": 8, "novena": 9, "décima": 10, "decima": 10, "undécimo": 11, "undécima": 11, "undecimo": 11,
    "undecima": 11, "duodécima": 12, "duodecima": 12, "decimotercera": 13, "decimocuarta": 14,
    "decimoquinta": 15, "decimosexta": 16, "decimoséptima": 17, "decimoseptima": 17,
}  # fmt: skip
# Order of the Latin multiplicative suffixes of an article number ("144 bis" < "144 ter" < "144 quater" < "144 quinto").
SUFFIX_ORDER = {
    "": 0, "bis": 1, "ter": 2, "quater": 3, "quinquies": 4, "quinque": 4, "quinto": 4, "sexies": 5, "sexto": 5,
    "septies": 6, "octies": 7, "nonies": 8, "decies": 9, "undecies": 10, "duodecies": 11,
}  # fmt: skip


def _is_note(line: str) -> bool:
    if line.startswith("(") and re.search(r"\)[.)]*$", line) and NOTE_WORDS.search(line):
        return True
    if re.match(r"^NOTA:", line):
        return True
    return line.startswith("- ") and "B.O." in line


def _suffix(raw: str | None) -> str:
    return (raw or "").lower().replace("á", "a")


def _split_trailing_note(line: str) -> tuple[str, str] | None:
    """Split ``text (inciso sustituido por ... B.O. 1/1/2000)`` into the text and the parenthetical."""
    stripped = line.rstrip()
    stripped = stripped[:-1] if stripped.endswith(").") else stripped
    if not stripped.endswith(")"):
        return None
    depth = 0
    for idx in range(len(stripped) - 1, -1, -1):
        depth += {")": 1, "(": -1}.get(stripped[idx], 0)
        if depth == 0:
            note = line[idx:].strip()
            before = line[:idx].rstrip()
            if "B.O." in note and NOTE_KEYS.search(note) and re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{3}", before):
                return before, note
            return None
    return None


@dataclass
class _Unit:
    kind: str
    eid: str
    label: str | None
    heading: str | None
    context: str | None
    lines: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _plausible_title(nxt: str, end: re.Pattern[str] | None) -> bool:
    nxt = _bare(nxt)
    return not (
        len(nxt) > 200
        or STRUCT.match(nxt)
        or HEAD.match(nxt)
        or _is_note(nxt)
        or nxt.startswith(_IMAGE_LINE)
        or (end and end.match(nxt))
        or GENERIC_END.match(nxt)
        or nxt in ("DISPOSICIONES TRANSITORIAS", "PREÁMBULO")
    )


def _anchored(before: str, note: str) -> str:
    """A trailing note belongs to one line of the article: keep that line's item marker with it."""
    marker = re.match(r"^((?:\d{1,3}|[a-z])[º°.)\-]*)\s", before)
    return f"{marker.group(1)} {note}" if marker else note


def _parse_text_lines(info: InstrumentInfo, lines: list[str]) -> list[_Unit]:
    """Split the flattened text into units (articles, transitory clauses, preamble) with their notes."""
    if info.start:
        pattern = re.compile(info.start)
        starts = [i for i, ln in enumerate(lines) if pattern.match(_bare(ln))]
        if not starts:
            raise InfolegParseError(f"start marker {info.start!r} not found")
        lines = lines[starts[0] :]
    end = re.compile(info.end) if info.end else None
    units: list[_Unit] = []
    outline: list[tuple[str, str, str | None]] = []  # (kind, label, title)
    pending: list[str] = []  # notes written under a structural heading, given to the next unit
    current: _Unit | None = None
    last_key: tuple[int, int] | None = None
    in_transitory = False
    used: set[str] = set()

    def context() -> str | None:
        parts = [f"{label}: {title}" if title else label for _, label, title in outline]
        return " – ".join(parts) or None

    def open_unit(kind: str, eid: str, label: str | None, heading: str | None) -> _Unit:
        base, n = eid, 2
        while eid in used:
            eid = f"{base}-{n}"
            n += 1
        used.add(eid)
        unit = _Unit(kind, eid, label, heading, context(), notes=list(pending))
        pending.clear()
        units.append(unit)
        return unit

    def push(kind: str, label: str) -> None:
        while kind != "sub" and outline and outline[-1][0] == "sub":
            outline.pop()  # an unnumbered sub-heading ends where a numbered heading starts
        for idx, (k, _, _) in enumerate(outline):
            if k == kind:
                del outline[idx:]
                break
        outline.append((kind, label, None))

    i = 0
    n = len(lines)
    while i < n:
        line = _bare(lines[i])
        centered = lines[i].startswith(_CENTER)
        i += 1
        if (end and end.match(line)) or GENERIC_END.match(line):
            break
        # a note followed on the same line by the next article: "(Artículo sustituido ...) ARTICULO 5 ..."
        glued = re.match(r"^(\(.*\))\s+((?:ART[IÍ]CULO|Art[ií]culo|Art\.)\s+\d.*)$", line)
        if glued and _is_note(glued.group(1)) and HEAD.match(glued.group(2)):
            lines.insert(i, glued.group(2))
            n += 1
            line = glued.group(1)
        sm = STRUCT.match(line)
        if sm:
            current = None
            label = line[: sm.start("title")].rstrip(" :.-–") if sm.group("title") else line.rstrip(".")
            push((sm.group("kind") or "parte").lower().replace("í", "i").replace("ó", "o").replace("á", "a"), label)
            while i < n and _is_note(_bare(lines[i])):  # "TITULO XIII" / "(Título incorporado por ...)" / title
                pending.append(f"[{label}] {_bare(lines[i])}")
                i += 1
            if sm.group("title"):
                kind, lab, _ = outline[-1]
                outline[-1] = (kind, lab, sm.group("title").strip().rstrip("."))
            elif i < n and _plausible_title(lines[i], end):
                kind, lab, _ = outline[-1]
                outline[-1] = (kind, lab, _bare(lines[i]).rstrip("."))
                i += 1
            continue
        if info.transitional_clauses and line == "DISPOSICIONES TRANSITORIAS":
            current = None
            in_transitory = True
            outline.clear()
            push("transitorias", line)
            continue
        if info.transitional_clauses and line == "PREÁMBULO":
            current = open_unit("preamble", "preamble", None, None)
            continue
        if centered and not HEAD.match(line) and not _is_note(line):
            # a centred line that is no numbered heading: the title of an unnumbered section ("Anulabilidad.")
            current = None
            push("sub", line.rstrip("."))
            continue
        if in_transitory:
            cm = ORDINAL_CLAUSE.match(line)
            if cm:
                word = cm.group("word").lower()
                current = open_unit("transitional_provision", f"dt-{CLAUSE_NUMBER[word]}", word, None)
                current.lines.append(cm.group("rest"))
                continue
        hm = HEAD.match(line)
        if hm and not in_transitory:
            num, suffix = int(hm.group("num")), _suffix(hm.group("suffix"))
            key = (num, SUFFIX_ORDER.get(suffix, 0))
            if last_key is None or key > last_key:
                last_key = key
                rest = hm.group("rest").strip()
                heading: str | None = None
                if info.heading_style == "paren":
                    pm = re.fullmatch(r"\(([^()]*)\)\.?", rest)
                    if pm and not REPEALED.match(rest):
                        heading, rest = pm.group(1).strip().rstrip("."), ""
                elif (
                    info.heading_style == "line"
                    and rest
                    and len(rest) <= HEADING_LIMIT
                    and not rest.endswith((":", ";"))
                    and not rest[0].islower()
                    and not REPEALED.match(rest)
                    and not _is_note(rest)
                    and i < n
                    and not (
                        STRUCT.match(_bare(lines[i])) or HEAD.match(_bare(lines[i])) or lines[i].startswith(_CENTER)
                    )
                    and (not _is_note(_bare(lines[i])) or re.search(r"derogad", lines[i], re.I) is not None)
                ):
                    heading, rest = rest.rstrip(".").strip(), ""
                current = open_unit(
                    "article", f"art-{num}" + (f"-{suffix}" if suffix else ""), f"{num} {suffix}".strip(), heading
                )
                if rest:
                    current.lines.append(rest)
                continue
            # an out-of-order "Artículo N" at the start of a line is text (a quotation), not a new article
        if current is None:
            if _is_note(line) and outline:  # a note under a heading, after its title
                pending.append(f"[{outline[-1][1]}] {line}")
            continue
        if line == _IMAGE_LINE:
            raise InfolegParseError(f"an image inside the text of {current.eid}; refusing to drop it silently")
        if _is_note(line):
            current.notes.append(line)
            continue
        split = _split_trailing_note(line)
        if split:
            line = split[0]
            current.notes.append(_anchored(*split))
        current.lines.append(line)
    return units


# --------------------------------------------------------------------------- summary page


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InfolegParseError(f"invalid date {value!r}") from exc


_DATASET_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OBSERVATION_LIMIT = 1500
ABROGATION = re.compile(r"(?i)abrogad|derogad|sin efecto")


def _dotted(number: str) -> str:
    return f"{int(number):,}".replace(",", ".")


def _row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """The summary-page facts, from a row of the open-data CSV (``dataset_row`` of a v2 bundle)."""

    def text(key: str) -> str | None:
        value = row.get(key)
        return (str(value).strip() or None) if value is not None else None

    def day(key: str) -> str | None:
        value = text(key)
        return value if value and _DATASET_DATE.match(value) else None

    def count(key: str) -> int | None:
        value = text(key)
        return int(value) if value and value.isdigit() else None

    if text("tipo_norma") != "Ley" or not (text("numero_norma") or "").isdigit():
        raise InfolegParseError("dataset row: only numbered laws (tipo_norma 'Ley') are supported")
    return {
        "tipo": "Ley",
        "numero": str(int(row["numero_norma"])),
        "organismo": text("organismo_origen"),
        "boletin_numero": text("numero_boletin"),
        "boletin_pagina": text("pagina_boletin"),
        "fecha_sancion": day("fecha_sancion"),
        "fecha_boletin": day("fecha_boletin"),
        "titulo_resumido": text("titulo_resumido"),
        "titulo_sumario": text("titulo_sumario"),
        "observaciones": text("observaciones"),
        "modificada_por": count("modificada_por"),
        "modifica_a": count("modifica_a"),
    }


def _generic_info(infoleg_id: int, meta: dict[str, Any], ambiguous: bool) -> InstrumentInfo:
    """Any other national law: URI by number (and year of sanction where the number was issued twice), the
    editors' summary title, the article style where the rubric is part of the first sentence."""
    number = meta["numero"]
    uri = f"/ar/ley/{number}"
    if ambiguous:
        year = (meta["fecha_sancion"] or "")[:4]
        uri += f"-{year}" if year else f"-id{infoleg_id}"
    title = f"Ley {_dotted(number)}" + (f" - {meta['titulo_resumido']}" if meta["titulo_resumido"] else "")
    return InstrumentInfo(infoleg_id, uri, "law", title, None, "inline")


def _summary(page: str, infoleg_id: int) -> dict[str, Any]:
    if f"/normativa/nacional/norma-{infoleg_id}" not in page:
        raise InfolegParseError(f"the summary page is not that of InfoLEG norm {infoleg_id}")
    h1 = re.search(r'<h1 class="h5 m-b-0">(.*?)</h1>', page, re.S)
    organ = re.search(r'<p class="lead m-b-0">(.*?)</p>', page, re.S)
    if not h1:
        raise InfolegParseError("summary page: norm heading (h1) not found")
    name = _text(h1.group(1))
    nm = re.fullmatch(r"([A-Za-zÁÉÍÓÚáéíóúñ ]+?)\s+(\d{1,6})(?:/(\d{4}))?", name)
    if not nm:
        raise InfolegParseError(f"summary page: unrecognised norm designation {name!r}")
    data: dict[str, Any] = {
        "tipo": nm.group(1).strip(),
        "numero": nm.group(2),
        "organismo": _text(organ.group(1)) if organ else None,
        "boletin_numero": None,
        "boletin_pagina": None,
        "fecha_sancion": None,
        "fecha_boletin": None,
    }
    for dt, dd in re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", page, re.S):
        label, value = _text(dt), _text(dd)
        times = re.findall(r'<time[^>]*datetime="(\d{4}-\d{2}-\d{2})"', dd)
        if label.startswith("Sanción"):
            data["fecha_sancion"] = times[0] if times else None
        elif label.startswith("Publicada en el Boletín Oficial"):
            data["fecha_boletin"] = times[0] if times else None
        elif label.startswith("Número"):
            digits = re.search(r"(\d+)\s*$", value)
            data["boletin_numero"] = digits.group(1) if digits else None
        elif label.startswith("Página"):
            data["boletin_pagina"] = value or None
    return data


# --------------------------------------------------------------------------- document


def _join(notes: list[str]) -> str | None:
    text = " ".join(notes).strip()
    return text[:NOTE_LIMIT] if text else None


def parse_infoleg_ar(content: bytes) -> list[ParsedDocument]:
    try:
        bundle = json.loads(content)
    except ValueError as exc:
        raise InfolegParseError(f"not an InfoLEG bundle: {exc}") from exc
    formats = (BUNDLE_FORMAT, BUNDLE_FORMAT_V2)
    if not isinstance(bundle, dict) or bundle.get("format") not in formats:
        raise InfolegParseError(f"unsupported bundle (expected format {BUNDLE_FORMAT!r} or {BUNDLE_FORMAT_V2!r})")
    v2 = bundle["format"] == BUNDLE_FORMAT_V2
    infoleg_id = bundle.get("infoleg_id")
    kind = bundle.get("text_kind")
    if not isinstance(infoleg_id, int):
        raise InfolegParseError(f"InfoLEG norm {infoleg_id!r} is not an integer id")
    if kind not in ("actualizado", "original"):
        raise InfolegParseError(f"unknown text kind {kind!r}")
    pages = bundle.get("pages") or {}
    try:
        text_page = pages["text"]["html"]
        retrieved = datetime.fromisoformat(pages["text"]["retrieved_at"]).astimezone(UTC).date()
        summary_page = None if v2 else pages["summary"]["html"]
    except (KeyError, TypeError, ValueError) as exc:
        raise InfolegParseError(f"incomplete bundle: {exc}") from exc

    if v2:
        meta = _row_metadata(bundle.get("dataset_row") or {})
        info = INSTRUMENTS.get(infoleg_id) or _generic_info(infoleg_id, meta, bool(bundle.get("number_ambiguous")))
    else:
        found = INSTRUMENTS.get(infoleg_id)
        if found is None:
            raise InfolegParseError(f"InfoLEG norm {infoleg_id!r} is not one of the instruments ULIS knows")
        info = found
        assert summary_page is not None
        meta = _summary(summary_page, info.infoleg_id)
    try:
        lines = _lines(_body_html(text_page, kind))
    except InfolegParseError as exc:
        if v2 and str(exc).startswith("unsupported markup"):
            # Superscripts and subscripts (m2, 1o ...) cannot be kept as plain text without changing it.
            raise ParseSkipped(f"InfoLEG norm {infoleg_id}: {exc}") from exc
        raise
    try:
        units = _parse_text_lines(info, lines)
    except InfolegParseError as exc:
        if v2 and str(exc).startswith("an image inside"):
            raise ParseSkipped(f"InfoLEG norm {infoleg_id}: {exc}") from exc
        raise
    if not units:
        if v2:  # a well-formed page whose text has no article ULIS can read (annex only, a list, an image ...)
            raise ParseSkipped(f"InfoLEG norm {infoleg_id}: the {kind} text has no article to read")
        raise InfolegParseError("no provisions found")

    provisions: list[ParsedProvision] = []
    for unit in units:
        text_lines = list(unit.lines)
        notes = list(unit.notes)
        repealed = False
        if unit.kind == "article":
            if text_lines and REPEALED.match(text_lines[0]) and len(text_lines) == 1:
                notes.insert(0, text_lines[0])
                text_lines = []
                repealed = True
            elif not text_lines and any(re.search(r"derogad|suprimid", n, re.I) for n in notes):
                repealed = True
        text = "\n".join(text_lines)
        provisions.append(
            ParsedProvision(
                eid=unit.eid,
                kind=unit.kind,
                num_path=[unit.label] if unit.label else [],
                top_kind=unit.kind,
                heading=unit.heading,
                text="" if repealed else text,
                parent_eid=None,
                sort_order=len(provisions),
                status="repealed" if repealed else "in_force",
                valid_from=None,
                valid_to=None,
                extent=[JURISDICTION],
                note=_join(notes),
                refs=[],
                is_leaf=True,
                has_own_content=False,
                context_heading=unit.context,
            )
        )

    sancion, boletin = _date(meta["fecha_sancion"]), _date(meta["fecha_boletin"])
    # The Constitution is not "a law of that date": InfoLEG files its text under Ley 24.430, which
    # orders the publication of the official text (the dates below are that law's).
    is_constitution = info.instrument_type == "constitution"
    law = f"{meta['tipo']} {meta['numero']}"
    events: list[ParsedEvent] = []
    if sancion:
        description = f"Sanción de la {law}" + (" (ordena la publicación del texto oficial)" if is_constitution else "")
        events.append(ParsedEvent("enactment", sancion, None, description, "summary/Sanción"))
    if boletin:
        number = f" nº {meta['boletin_numero']}" if meta["boletin_numero"] else ""
        page_no = f", p. {meta['boletin_pagina']}" if meta["boletin_pagina"] else ""
        events.append(ParsedEvent("publication", boletin, None, f"{law}: Boletín Oficial{number}{page_no}", "summary"))
    events.append(
        ParsedEvent(
            "version_change", retrieved, None, f"Retrieval date of the texto {kind} shown by InfoLEG", "text-page"
        )
    )
    ext: dict[str, Any] = {
        "infoleg_id": info.infoleg_id,
        "tipo": meta["tipo"],
        "numero": meta["numero"],
        "texto": kind,
    }
    for key in ("organismo", "fecha_sancion", "fecha_boletin", "boletin_numero", "boletin_pagina"):
        if meta[key]:
            ext[key] = meta[key]
    outstanding: list[dict[str, Any]] = []
    if v2:
        for key in ("titulo_resumido", "titulo_sumario", "modificada_por", "modifica_a"):
            if meta[key] is not None:
                ext[key] = meta[key]
        observation = meta["observaciones"]
        if observation:
            ext["observaciones"] = observation[:OBSERVATION_LIMIT]
            if ABROGATION.search(observation):
                outstanding.append(
                    {
                        "affects_eid": None,
                        "affected_label": None,
                        "by_uri": None,
                        # shown in the "not yet incorporated changes" warning: the editors' words, as they are
                        "by_title": "InfoLEG observation: " + observation[:300],
                        "description": "InfoLEG's observation on this law reads: " + observation[:OBSERVATION_LIMIT],
                    }
                )
        if kind == "original" and meta["modificada_por"]:
            outstanding.append(
                {
                    "affects_eid": None,
                    "affected_label": None,
                    "by_uri": None,
                    "by_title": f"{meta['modificada_por']} norm(s) listed as modifying this law; no updated text",
                    "description": f"InfoLEG lists {meta['modificada_por']} norm(s) that modify this law "
                    "(modificada_por) but publishes no updated text: the original text shown may not reflect "
                    "those modifications.",
                }
            )
    updated = kind == "actualizado"
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=info.instrument_type,
            jurisdiction_id=JURISDICTION,
            work_uri=info.uri,
            expression_uri=f"{info.uri}/spa@{retrieved.isoformat()}",
            work_title=info.title,
            title=info.title,
            short_title=info.short_title,
            number=meta["numero"],
            work_date=None if is_constitution else sancion,
            language="es",
            authoritative=False,  # the Boletín Oficial is authentic; InfoLEG's texts are informative
            text_origin="official_revised" if updated else "official_conversion",
            consolidation_kind="current_consolidation" if updated else "as_made",
            version_date=retrieved,
            currency_date=retrieved,
            extent=[JURISDICTION],
            ext_module="ar",
            ext=ext,
            outstanding_changes=outstanding,
            events=events,
            provisions=provisions,
            validity_basis="attested_at_edition",
        )
    ]
