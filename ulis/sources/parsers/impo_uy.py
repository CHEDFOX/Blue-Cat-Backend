"""Parser for Uruguayan national legislation from IMPO (Dirección Nacional de Impresiones y Publicaciones Oficiales).

IMPO (https://www.impo.com.uy) publishes its database of norms as open data (Licencia de Datos Abiertos - Uruguay):
any public norm is available as JSON by adding ``?json=true`` to its address, in the schema
http://www.impo.com.uy/resources/basesIMPO.json. The JSON is IMPO's *updated* text ("Documento Actualizado",
the ``leyenda`` field): the norm as amended, with editorial notes. The Diario Oficial is the publication of record;
IMPO's updated texts are informative, so the texts are ``authoritative: false`` and ``official_consolidation``.

Input: one *bundle* made by the connector (JSON, ``ulis-impo-bundle/1``): IMPO's answer exactly as received (the
server sends ISO-8859-1; the bytes travel as a Latin-1 string), the path ``<kind>/<number>-<year>`` and the
retrieval time. The page states no date of update, so the retrieval date is the date the source attests the text
(``validity_basis = "attested_at_edition"``; provisions carry no start date).

What the JSON looks like (what ULIS does with it):

* a norm has ``tipoNorma``, ``nroNorma``, ``anioNorma``, ``nombreNorma``, ``fechaPromulgacion`` and
  ``fechaPublicacion`` (``DD/MM/YYYY``) and a list ``articulos``. Each entry is one article, ``tituloArticulo`` its
  label ("Artículo 9-BIS", "Artículo 131-144" for a range IMPO keeps as one entry), ``textoArticulo`` its text.
  Each article is one provision (leaf). The constitution, the laws, the decree-laws (``decretos-ley``, 1973-85),
  the decrees and the codes (``codigo-*``) are supported. The JSON is not strictly valid (bare control characters
  inside strings): it is read leniently;
* ``titulosArticulo`` carries the headings (TITULO, CAPITULO, SECCION ...) that start at that article, separated
  by ``<br>``; the parser keeps the running hierarchy as the provision's ``context_heading``;
* ``textoArticulo`` is plain text hard-wrapped at about 72 columns with a few HTML tags (``<i>``, ``<b>``, ``<br>``,
  ``<pre>``). The parser removes the tags, joins lines that merely wrap (a line break after a sentence-ending mark or
  between paragraphs, items and list entries is kept) and drops IMPO's ``(*)`` editorial markers; the words and
  punctuation are those of the source. Some entries hold several articles of the code, labelled inline
  (``</pre><br><b>242-1</b><br><pre>``): they become one provision per label;
* an article whose whole text is a repeal statement ("DEROGADO por Ley No. 10.084 del 3.12.41 (*)") or is empty with
  a ``Derogado/s por:`` note is ``repealed``: empty text, the statement and notes in ``note``. An amending law's
  article whose text IMPO omits ("Este artículo dio nueva redacción a: ...") stays ``in_force`` with empty text and
  that note;
* ``notasArticulo`` (HTML) holds IMPO's editorial notes ("Redacción dada por: Ley Nº 18.719 de 27/12/2010 artículo
  152", "Ver en esta norma, artículos: ...", "TEXTO ORIGINAL: ..."). It becomes the provision's ``note`` (plain text,
  never part of ``text``); its links become ``cites`` references; each "dada/agregado/derogado/suprimido ... por: <law>
  de <date>" gives one ``amendment`` or ``repeal`` lifecycle event with the amending law's own date (not the date the
  change took effect, which the notes give only as "Ver vigencia" links).

Not handled (see docs/jurisdictions/uy.md): the older wording of amended articles (IMPO links to the original text
only), tables beyond their plain text, the "referencias" pages, and annexes. A norm whose articles have neither text
nor notes (some 19th-century laws) raises ``ParseSkipped``; an unexpected answer raises ``ImpoParseError``.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from datetime import UTC, date, datetime
from typing import Any, Literal

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParsedRef, ParseSkipped

PARSER_ID = "impo_uy_json"
PARSER_VERSION = "1.0.0"
BUNDLE_FORMAT = "ulis-impo-bundle/1"
SITE = "https://www.impo.com.uy"
JURISDICTION = "UY"
NOTE_LIMIT = 4000
# IMPO's own address of a norm: bases/<kind>/<number>-<year>, e.g. leyes/18331-2008, decretos/414-2009,
# constitucion/1967-1967, codigo-civil/16603-1994.
PATH_PATTERN = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*/[1-9][0-9]{0,6}-[0-9]{4}"
_PATH = re.compile(r"(?P<kind>[a-z][a-z0-9]*(?:-[a-z0-9]+)*)/[1-9][0-9]{0,6}-[0-9]{4}")
_NORM_URI = re.compile(
    rf"^(?:{re.escape(SITE)}|https?://(?:www\.)?impo\.com\.uy)/bases/(?P<path>{PATH_PATTERN})(?:/(?P<article>[0-9A-Za-z_-]+))?/?$"
)
_ARTICLE_LABEL = re.compile(r"^[0-9A-Za-zÑñ]+(?:-[0-9A-Za-zÑñ]+)*$")


class ImpoParseError(ValueError):
    pass


def instrument_type_of(kind: str) -> str | None:
    """ULIS instrument type of an IMPO kind (the first segment of the path); None if it is not supported."""
    if kind == "constitucion":
        return "constitution"
    if kind in ("leyes", "decretos-ley") or kind.startswith("codigo-"):
        return "law"
    if kind == "decretos":
        return "decree"
    return None


def work_uri(path: str) -> str:
    return f"/uy/{path}"


def article_eid(label: str) -> str:
    """``9-BIS`` -> ``art-9-bis``; IMPO's address form ``18_BIS`` -> ``art-18-bis``."""
    return "art-" + label.replace("_", "-").lower()


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map an IMPO address to (ULIS work URI, eId or None): ``.../bases/leyes/18331-2008/18_BIS``."""
    m = _NORM_URI.match(uri)
    if not m:
        return None
    path = m.group("path")
    kind = path.split("/", 1)[0]
    if instrument_type_of(kind) is None:
        return None
    article = m.group("article")
    return work_uri(path), article_eid(article) if article else None


# --------------------------------------------------------------------------- text

_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_BR = re.compile(r"(?i)<br\s*/?>")
_PRE = re.compile(r"(?i)</?pre\b[^>]*>")
_MARKER = re.compile(r"[ \t]*\(\*+\)")
# Several articles inside one entry: ``</pre><br><b>242-1</b><br><pre>``
_SUBARTICLE = re.compile(r"(?is)</pre>\s*<br\s*/?>\s*<b>\s*([^<]{1,30}?)\s*</b>\s*<br\s*/?>\s*<pre[^>]*>")
_TERMINAL = re.compile(r"[.:;!?)\"»”]$")
_ITEM = re.compile(r"^(?:[A-Za-zÑñ]\)|\d{1,3}[º°]?\)|\d{1,3}\.-)\s")  # "A) ", "2º) ", "3) ", "4.- ": starts a line
_TABULAR = re.compile(r"\S {3,}\S|\.{4,}")  # columns or dot leaders: a table row is never joined to its neighbour
_ABBREVIATION = re.compile(r"(?i)(?:\bN[oº°]s?|\bart[s]?|\bart[ií]culos?|\binc|\bnum|\bdec|\bap|\blit)\.$")
_REPEAL_TEXT = re.compile(r"(?i)^\(?\s*(?:DEROGAD[OA]S?|SUPRIMID[OA]S?|SUPRIMIDA\s+LA|DEJAD[OA]S?\s+SIN\s+EFECTO)\b")
_REPEAL_NOTE = re.compile(r"(?i)(?:derogad|suprimid|dejad[oa]s? sin efecto)")


def decode_body(body: str) -> str:
    """The connector carries the server's bytes as a Latin-1 string; IMPO sends ISO-8859-1 (UTF-8 is tried first)."""
    raw = body.encode("latin-1")
    for encoding in ("utf-8", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body


def _plain(fragment: str) -> str:
    """Tags out, entities decoded, line ends normalised; no other change."""
    s = _BR.sub("\n\n", fragment)  # an explicit <br> is a paragraph break
    s = _PRE.sub("", s)
    s = _TAG.sub("", s)
    s = htmllib.unescape(s)
    return s.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")


def _reflow(text: str) -> str:
    """Join lines that only wrap; keep a break after a sentence-ending mark when the next line starts a new
    paragraph, item or list entry. Words and punctuation are untouched."""
    out: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        if out and out[-1] != "":
            prev = out[-1]
            continues = not (_ITEM.match(line) or _TABULAR.search(prev) or _TABULAR.search(line)) and (
                not _TERMINAL.search(prev)
                or line[0].islower()
                or (line[0].isdigit() and bool(_ABBREVIATION.search(prev)))
            )
            if continues:
                out[-1] = f"{prev} {line}"
                continue
        out.append(line)
    paragraphs = [p for p in out if p]
    return "\n".join(paragraphs)


def clean_article_text(fragment: str) -> str:
    return _reflow(_MARKER.sub("", _plain(fragment)))


def _flat(fragment: str) -> str:
    return re.sub(r"\s+", " ", _plain(fragment)).strip()


def _note(fragment: str) -> str:
    """An editorial note (HTML) as plain text."""
    return re.sub(r"\s+", " ", _plain(_BR.sub(" ", fragment))).strip()


# --------------------------------------------------------------------------- dates, headings, notes


def _date(value: Any) -> date | None:
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", value) if isinstance(value, str) else None
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _headings(raw: Any) -> list[str]:
    parts = [
        re.sub(r"\s+", " ", _MARKER.sub("", htmllib.unescape(_TAG.sub("", p)))).strip() for p in _BR.split(raw or "")
    ]
    return [p for p in parts if p]


def _heading_key(heading: str) -> str:
    """The kind of a heading: its first word (TITULO, CAPITULO, SECCION ...); ordinals ("1º - DE ...") are one kind."""
    first = heading.split(" ", 1)[0].upper()
    return "ORDINAL" if re.fullmatch(r"\d+[º°.)]*", first) else first


def _update_context(stack: list[str], new: list[str]) -> None:
    """IMPO lists a heading on the first article under it (``CAPITULO II``); a heading of a kind already open
    (CAPITULO) closes it and what is below it, any other kind opens a level."""
    for heading in new:
        key = _heading_key(heading)
        for i, open_heading in enumerate(stack):
            if _heading_key(open_heading) == key:
                del stack[i:]
                break
        stack.append(heading)


_NOTE_LABEL = re.compile(
    r"<b>(?:<font[^>]*>)?\s*(?P<label>[^<]*?)\s*(?:</font>)?</b>(?P<body>.*?)(?=<b>|<br|$)", re.I | re.S
)
_LAW_REF = re.compile(
    r"(?:^|,)\s*(?P<desig>[^,<>]+?)\s+de\s+(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+art[ií]culos?\s*[^<,]*?"
    r'<a[^>]*?href="/bases/(?P<kind>[a-z0-9-]+)/(?P<id>\d+-\d{4})',
    re.I,
)
_AMENDING_LABEL = re.compile(r"(?i)dada por|dada anteriormente|agregad|derogad|suprimid|reincorporad|sustituid")
_LINK = re.compile(r'(?is)<a[^>]*?href="(?P<href>/bases/[^"#?]+)"[^>]*>(?P<text>.*?)</a>')
_SKIPPED_KINDS = {"textos-originales-ley", "leyes-originales"}


def _events_from_notes(notes_html: str, eid: str) -> list[ParsedEvent]:
    flat = re.sub(r"\s+", " ", notes_html)
    events: list[ParsedEvent] = []
    for block in _NOTE_LABEL.finditer(flat):
        label = re.sub(r"\s+", " ", block.group("label")).strip().rstrip(":").strip()
        if not _AMENDING_LABEL.search(label):
            continue
        event_type = "repeal" if re.search(r"(?i)derogad|suprimid", label) else "amendment"
        for ref in _LAW_REF.finditer(block.group("body")):
            when = _date(ref.group("date"))
            if when is None or ref.group("kind") in _SKIPPED_KINDS:
                continue
            designation = re.sub(r"\s+", " ", _TAG.sub("", ref.group("desig"))).strip()
            events.append(
                ParsedEvent(
                    event_type,
                    when,
                    work_uri(f"{ref.group('kind')}/{ref.group('id')}"),
                    f"{label}: {designation} de {ref.group('date')} (date of that norm)",
                    eid,
                )
            )
    return events


def _refs_from_notes(notes_html: str, own_path: str) -> list[ParsedRef]:
    refs: dict[str, ParsedRef] = {}
    for link in _LINK.finditer(notes_html):
        target = link.group("href")
        m = _NORM_URI.match(SITE + target)
        if not m or m.group("path").split("/", 1)[0] in _SKIPPED_KINDS:
            continue
        if instrument_type_of(m.group("path").split("/", 1)[0]) is None:
            continue
        text = _flat(link.group("text")) or target
        refs.setdefault(target, ParsedRef(href=SITE + target, text=text, relation="cites"))
    return list(refs.values())


# --------------------------------------------------------------------------- designation of the norm


def dotted(number: str) -> str:
    """``18331`` -> ``18.331``."""
    return f"{int(number):,}".replace(",", ".")


def designation(tipo: str, number: str | None, year: int | None, kind: str) -> str:
    if kind == "leyes" and number:
        return f"Ley Nº {dotted(number)}"
    if kind == "decretos" and number and year:
        return f"Decreto Nº {number}/{year % 1000:03d}"
    if kind == "decretos-ley" and number:
        return f"Decreto Ley Nº {dotted(number)}"
    return tipo


def parse_impo_uy(content: bytes) -> list[ParsedDocument]:
    try:
        bundle = json.loads(content)
    except ValueError as exc:
        raise ImpoParseError(f"not an IMPO bundle: {exc}") from exc
    if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE_FORMAT:
        raise ImpoParseError(f"unsupported bundle (expected format {BUNDLE_FORMAT!r})")
    raw_path = bundle.get("path")
    m = _PATH.fullmatch(raw_path) if isinstance(raw_path, str) else None
    if m is None or not isinstance(raw_path, str):
        raise ImpoParseError(f"invalid norm path {raw_path!r}")
    path: str = raw_path
    kind = m.group("kind")
    instrument_type = instrument_type_of(kind)
    if instrument_type is None:
        raise ImpoParseError(
            f"IMPO kind {kind!r} is not one ULIS knows (constitucion, leyes, decretos-ley, decretos, codigo-*)"
        )
    try:
        retrieved = datetime.fromisoformat(bundle["retrieved_at"]).astimezone(UTC).date()
        norm = json.loads(decode_body(bundle["body"]), strict=False)  # bare control characters occur in strings
    except (KeyError, TypeError, ValueError) as exc:
        raise ImpoParseError(f"incomplete bundle: {exc}") from exc
    articles = norm.get("articulos") if isinstance(norm, dict) else None
    if not isinstance(articles, list) or not articles:
        raise ImpoParseError("the norm has no articles")

    tipo = _flat(str(norm.get("tipoNorma") or "")) or kind
    nombre = _flat(str(norm.get("nombreNorma") or "")) or None
    nro = str(norm.get("nroNorma") or "").strip() or None
    anio = norm.get("anioNorma")
    norm_year = int(anio) if str(anio or "").isdigit() else None
    updated = "actualizado" in str(norm.get("leyenda") or "").lower()

    provisions: list[ParsedProvision] = []
    events: list[ParsedEvent] = []
    seen_events: set[tuple[str, date, str | None]] = set()
    seen: set[str] = set()
    context: list[str] = []
    for article in articles:
        if not isinstance(article, dict):
            raise ImpoParseError("an article is not an object")
        label_match = re.match(r"^Art[ií]culo\s+(.+)$", str(article.get("tituloArticulo") or "").strip())
        label = (label_match.group(1).strip() if label_match else "").replace(" ", "")
        if not label:
            label = str(article.get("nroArticulo") or "").strip()
            if article.get("secArticulo"):
                label += f"-{str(article['secArticulo']).strip()}"
        if not _ARTICLE_LABEL.match(label):
            raise ImpoParseError(f"unexpected article label {label!r}")
        _update_context(context, _headings(article.get("titulosArticulo")))
        raw_text = str(article.get("textoArticulo") or "")
        notes_html = str(article.get("notasArticulo") or "")
        note_text = _note(notes_html)
        # Entries that hold several articles are split at their inline labels.
        pieces = _SUBARTICLE.split(raw_text)
        segments = [(label, pieces[0])] + [
            (pieces[i].strip().replace(" ", ""), pieces[i + 1]) for i in range(1, len(pieces) - 1, 2)
        ]
        ref_list = _refs_from_notes(notes_html, path)
        for index, (seg_label, seg_text) in enumerate(segments):
            if not _ARTICLE_LABEL.match(seg_label):
                raise ImpoParseError(f"unexpected article label {seg_label!r} inside article {label}")
            eid = article_eid(seg_label)
            if eid in seen:
                raise ImpoParseError(f"article {seg_label} appears twice")
            seen.add(eid)
            text = clean_article_text(seg_text)
            statement = _flat(_MARKER.sub("", seg_text))
            notes = [note_text] if note_text else []
            repealed = False
            if statement and len(statement) < 400 and _REPEAL_TEXT.match(statement):
                repealed = True
                notes.insert(0, statement)
            elif not statement and len(segments) == 1 and _REPEAL_NOTE.search(note_text):
                repealed = True
            note = " ".join(notes)[:NOTE_LIMIT] or None
            provisions.append(
                ParsedProvision(
                    eid=eid,
                    kind="article",
                    num_path=[seg_label],
                    top_kind="article",
                    heading=None,
                    text="" if repealed else text,
                    parent_eid=None,
                    sort_order=len(provisions),
                    status="repealed" if repealed else "in_force",
                    valid_from=None,
                    valid_to=None,
                    extent=[JURISDICTION],
                    note=note,
                    refs=ref_list if index == 0 else [],
                    is_leaf=True,
                    has_own_content=False,
                    context_heading=" / ".join(context) or None,
                )
            )
        for event in _events_from_notes(notes_html, article_eid(label)):
            key = (event.event_type, event.date, event.affecting_uri)
            if key not in seen_events:
                seen_events.add(key)
                events.append(event)

    if not any(p.text or p.note for p in provisions):
        # some 19th-century laws are listed with article numbers but no text at all: nothing to store
        raise ParseSkipped(f"IMPO publishes no text for {path}")
    promulgated, published = _date(norm.get("fechaPromulgacion")), _date(norm.get("fechaPublicacion"))
    is_constitution = instrument_type == "constitution"
    named = designation(tipo, nro, norm_year, kind)
    if promulgated:
        what = f"Promulgación de la {named}" if kind == "leyes" else f"Promulgación ({named}; {nombre or kind})"
        events.insert(0, ParsedEvent("promulgation", promulgated, None, what, "ficha/fechaPromulgacion"))
    if published:
        events.append(
            ParsedEvent(
                "publication", published, None, f"Publicación en el Diario Oficial ({named})", "ficha/fechaPublicacion"
            )
        )
    events.append(
        ParsedEvent("version_change", retrieved, None, "Retrieval date of the updated text shown by IMPO", "json")
    )
    uri = work_uri(path)
    ext: dict[str, Any] = {"impo_path": path, "kind": kind, "tipo": tipo}
    if nro:
        ext["numero"] = nro
    if norm_year:
        ext["anio"] = norm_year
    if nombre:
        ext["nombre"] = nombre
    if promulgated:
        ext["fecha_promulgacion"] = promulgated.isoformat()
    if published:
        ext["fecha_publicacion"] = published.isoformat()
    title = nombre if kind in ("leyes", "decretos", "decretos-ley") and nombre else tipo
    status_kind: Literal["current_consolidation", "as_made"] = "current_consolidation" if updated else "as_made"
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=instrument_type,
            jurisdiction_id=JURISDICTION,
            work_uri=uri,
            expression_uri=f"{uri}/spa@{retrieved.isoformat()}",
            work_title=title,
            title=title,
            short_title=None if title == named else named,
            number=nro,
            work_date=None if is_constitution else promulgated,
            language="es",
            authoritative=False,  # the Diario Oficial is the publication of record; IMPO's updated text is informative
            text_origin="official_consolidation" if updated else "official_conversion",
            consolidation_kind=status_kind,
            version_date=retrieved,
            currency_date=retrieved,
            extent=[JURISDICTION],
            ext_module="uy",
            ext=ext,
            events=events,
            provisions=provisions,
            validity_basis="attested_at_edition",
        )
    ]
