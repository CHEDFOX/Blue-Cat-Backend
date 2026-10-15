"""Parser for the decisions and acórdãos of the Superior Tribunal de Justiça (Brazil) from its open data portal.

The portal (``dadosabertos.web.stj.jus.br``, dataset "Íntegras de Decisões Terminativas e Acórdãos do Diário da
Justiça") publishes, per day (or per month in 2022), a ZIP of text files (one ``<SeqDocumento>.txt`` per decision or
acórdão) and a JSON list of metadata records. The connector (``stj_dados_abertos``) pairs each text with its metadata
record(s) and hands the parser one JSON *envelope* per decision (:func:`encode_envelope`): the verbatim metadata
records and the verbatim text file. The envelope is ULIS's container, not a file of the publisher.

What a decision becomes (``doc_kind == "judgment"``, court ``BR-STJ``, language ``pt``):

* **metadata**: the publisher's identifiers are kept - ``SeqDocumento`` (the document id, also the name of the text
  file), ``numeroRegistro`` and ``processo`` (procedural class and number, e.g. ``REsp 2093032``, used as docket and
  case name). The data has no case name and **no judgment date**, only the publication date in the Diário da Justiça
  eletrônico (``dataPublicacao``); that date is stored as the decision date and flagged ``date_kind:
  "dje_publication"`` in ``ext`` (the judgment date, where the text states one, is not extracted). Receipt and
  distribution dates, the reporting/deciding minister, the internal appeal (``recurso``), the outcome (``teor``),
  the monocratic conclusion (``descricaoMonocratica``, a template whose party name is masked by the Court) and the CNJ
  subject codes (``assuntos``) go to ``ext`` as published. The field names differ between files (``SeqDocumento`` /
  ``seqDocumento``, ``NM_MINISTRO`` / ``ministro``, ``dataDistribuicao`` / ``dataDistribuição``, ``DECISAO`` /
  ``DECISÃO``) and dates are ISO strings or milliseconds since 1970 (read as UTC dates; the portal writes midnight
  Brasília time, 02:00 or 03:00 UTC); all are accepted. The receipt and distribution dates of a few records hold the
  placeholder 1900-01-01 (``-2209150800000``): read as absent. When a metadata list repeats a document, the records
  must agree on the identifying fields; differing outcomes (``teor``) are all kept.
* **paragraphs** (exact text): the text file is HTML-like: lines are separated by ``<br>`` (and, in some files,
  carriage returns or line feeds) and a few character entities (``&quot;``, ``&mldr;``) occur; lines are split at those
  separators, the entities are decoded, surrounding whitespace is trimmed and empty lines are dropped. The lines of a
  section are then joined with a line feed into units of at most 2,500 characters (a longer line stays alone; units
  never cross a section), because a decision has some 30 one-sentence lines and one search passage per line would make
  the index ten times larger and its hits hard to read. A line that is
  exactly ``ACÓRDÃO``, ``EMENTA``, ``RELATÓRIO``, ``VOTO``, ``VOTO-VISTA``, ``VOTO-VOGAL`` or ``DECISÃO`` is a section
  heading: it is not stored as text but switches ``part`` (``acordao``, ``ementa``, ``relatorio``, ``voto``,
  ``voto_vista``, ``voto_vogal``, ``decisao``; ``text`` before the first heading). A section that returns (the ementa
  is usually printed twice) keeps numbering its paragraphs. ``num`` is never set: the text has no paragraph numbers
  a citation could point to. No other structure is guessed; party names are whatever the Court published.

Nothing is linked to legislation or other decisions (no Brazilian legislation is loaded). A metadata record without a
text file is never parsed (the connector does not pass it on); an envelope without text raises ``ParseSkipped``.
"""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, date, datetime
from typing import Any

from ulis.sources.normalized import ParsedDocument, ParsedJudgmentMeta, ParsedParagraph, ParseSkipped

PARSER_VERSION = "1.0.0"
FORMAT = "stj-integra-1"
COURT_ID = "BR-STJ"

SPLIT = re.compile(r"<br\s*/?>|\r\n|\r|\n", re.IGNORECASE)
ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
SPACES = re.compile(r"\s+")
# Standalone lines that open a section -> part
HEADINGS = {
    "ACÓRDÃO": "acordao",
    "EMENTA": "ementa",
    "RELATÓRIO": "relatorio",
    "VOTO": "voto",
    "VOTO-VISTA": "voto_vista",
    "VOTO-VOGAL": "voto_vogal",
    "DECISÃO": "decisao",
}
PREFIX = {
    "acordao": "ac",
    "ementa": "em",
    "relatorio": "rel",
    "voto": "vt",
    "voto_vista": "vv",
    "voto_vogal": "vg",
    "decisao": "dec",
    "text": "tx",
}
# SeqDocumento / seqDocumento ...: the publisher's field names vary between files.
KEYS = {
    "seq": ("SeqDocumento", "seqDocumento"),
    "published": ("dataPublicacao",),
    "type": ("tipoDocumento",),
    "registro": ("numeroRegistro",),
    "processo": ("processo",),
    "received": ("dataRecebimento",),
    "distributed": ("dataDistribuicao", "dataDistribuição"),
    "minister": ("NM_MINISTRO", "ministro"),
    "recurso": ("recurso",),
    "teor": ("teor",),
    "monocratic": ("descricaoMonocratica",),
    "assuntos": ("assuntos",),
}
MIN_YEAR, MAX_YEAR = 1990, 2100
# A decision has some 30 lines; units of a few lines keep a hit readable and the index a tenth of the size.
MAX_CHARS = 2500


class StjParseError(ValueError):
    pass


def work_uri_for(seq: str) -> str:
    return f"/br/stj/{seq}"


def encode_envelope(seq: str, records: list[dict[str, Any]], text: str) -> bytes:
    """The deterministic container the connector stores and the parser reads (verbatim records and text). It does not
    name the portal file it came from (that is in the raw document's metadata), so a day the portal uploaded twice
    gives identical bytes."""
    envelope = {"format": FORMAT, "seq": seq, "records": records, "text": text}
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def record_seq(record: dict[str, Any]) -> str | None:
    value = _get(record, "seq")
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _get(record: dict[str, Any], key: str) -> Any:
    for name in KEYS[key]:
        if name in record:
            return record[name]
    return None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = SPACES.sub(" ", str(value)).strip()
    return text or None


def parse_date(value: Any, what: str, *, placeholder_ok: bool = False) -> date | None:
    """An ISO date/datetime string or milliseconds since 1970 (UTC calendar date); None when absent. A date before
    1990 is not a date of these proceedings: it is refused, or with ``placeholder_ok`` (the receipt and distribution
    dates, where the files hold the placeholder 1900-01-01) read as absent."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        if isinstance(value, bool):
            raise ValueError("boolean")
        if isinstance(value, int | float) or (isinstance(value, str) and re.fullmatch(r"-?\d{9,14}", value.strip())):
            parsed = datetime.fromtimestamp(float(value) / 1000, UTC).date()
        else:
            parsed = date.fromisoformat(str(value).strip()[:10])
    except (ValueError, OverflowError, OSError) as exc:
        raise StjParseError(f"invalid {what}: {value!r}") from exc
    if not MIN_YEAR <= parsed.year <= MAX_YEAR:
        if placeholder_ok:
            return None
        raise StjParseError(f"implausible {what}: {value!r}")
    return parsed


def document_type(value: Any) -> str:
    """``acordao`` or ``decisao`` from the published ``tipoDocumento`` (ACÓRDÃO/ACORDAO, DECISÃO/DECISAO)."""
    text = (_clean(value) or "").upper().replace("Ó", "O").replace("Ã", "A")
    if text in ("ACORDAO", "DECISAO"):
        return text.lower()
    raise StjParseError(f"unknown tipoDocumento {value!r}")


def split_paragraphs(text: str, limit: int = MAX_CHARS) -> list[tuple[str, str]]:
    """(part, text) units: the non-empty lines of a section joined with "\n" into units of at most ``limit``
    characters (a longer line stays alone); headings switch the part and are not returned."""
    out: list[tuple[str, str]] = []
    part = "text"
    buf: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal size
        if buf:
            out.append((part, "\n".join(buf)))
            buf.clear()
            size = 0

    for line in SPLIT.split(text):
        line = ENTITY.sub(lambda m: html.unescape(m.group(0)), line).strip()
        if not line:
            continue
        if line in HEADINGS:
            flush()
            part = HEADINGS[line]
            continue
        if buf and size + 1 + len(line) > limit:
            flush()
        size += len(line) + (1 if buf else 0)
        buf.append(line)
    flush()
    return out


def _distinct(values: list[str | None]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


def _agree(records: list[dict[str, Any]], key: str, what: str) -> Any:
    values = [_get(r, key) for r in records]
    first = values[0]
    if any(v != first for v in values[1:]):
        raise StjParseError(f"the metadata records of one document disagree on {what}: {values!r}")
    return first


def parse_stj_integra(content: bytes) -> list[ParsedDocument]:
    try:
        envelope = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise StjParseError(f"not a JSON envelope: {exc}") from exc
    if not isinstance(envelope, dict) or envelope.get("format") != FORMAT:
        raise StjParseError("not an stj-integra-1 envelope")
    seq = str(envelope.get("seq") or "").strip()
    records = envelope.get("records")
    text = envelope.get("text")
    if not re.fullmatch(r"[0-9]{1,15}", seq):
        raise StjParseError(f"invalid SeqDocumento {seq!r}")
    if not isinstance(records, list) or not records or not all(isinstance(r, dict) for r in records):
        raise StjParseError(f"{seq}: no metadata record")
    if any(record_seq(r) != seq for r in records):
        raise StjParseError(f"{seq}: a metadata record belongs to another document")
    if not isinstance(text, str):
        raise StjParseError(f"{seq}: no text")

    kind = document_type(_agree(records, "type", "tipoDocumento"))
    tipo_published = _clean(_get(records[0], "type")) or ""
    processo = _clean(_agree(records, "processo", "processo"))
    if not processo:
        raise StjParseError(f"{seq}: no processo")
    published = parse_date(_agree(records, "published", "dataPublicacao"), "dataPublicacao")
    if published is None:
        raise StjParseError(f"{seq}: no dataPublicacao")
    registro = _clean(_agree(records, "registro", "numeroRegistro"))
    received = parse_date(_agree(records, "received", "dataRecebimento"), "dataRecebimento", placeholder_ok=True)
    distributed = parse_date(
        _agree(records, "distributed", "dataDistribuicao"), "dataDistribuicao", placeholder_ok=True
    )

    units = split_paragraphs(text)
    if not units:
        raise ParseSkipped(f"{seq}: the text file is empty")
    counters: dict[str, int] = {}
    paragraphs: list[ParsedParagraph] = []
    for part, line in units:
        counters[part] = counters.get(part, 0) + 1
        paragraphs.append(ParsedParagraph(f"{PREFIX[part]}-{counters[part]}", None, part, line, len(paragraphs), []))

    ministers = _distinct([_clean(_get(r, "minister")) for r in records])
    recurso = _distinct([_clean(_get(r, "recurso")) for r in records])
    case_name = processo
    detail = ", ".join([tipo_published, *recurso])
    work_uri = work_uri_for(seq)
    ext: dict[str, Any] = {
        "seq_documento": seq,
        "numero_registro": registro,
        "processo": processo,
        "tipo_documento": tipo_published,
        "document_type": kind,
        "recurso": recurso,
        "ministros": ministers,
        "teor": _distinct([_clean(_get(r, "teor")) for r in records]),
        "descricao_monocratica": _distinct([_clean(_get(r, "monocratic")) for r in records]),
        "assuntos": _distinct([_clean(_get(r, "assuntos")) for r in records]),
        "data_publicacao": published.isoformat(),
        "data_recebimento": received.isoformat() if received else None,
        "data_distribuicao": distributed.isoformat() if distributed else None,
        "date_kind": "dje_publication",
    }
    return [
        ParsedDocument(
            doc_kind="judgment",
            instrument_type="judgment",
            jurisdiction_id="BR",
            work_uri=work_uri,
            expression_uri=f"{work_uri}/por@{published.isoformat()}",
            work_title=case_name,
            title=f"{case_name} ({detail})" if detail else case_name,
            short_title=None,
            number=processo,
            work_date=published,
            language="pt",
            authoritative=False,  # a dataset derived from the Diário da Justiça, not the authentic publication
            text_origin="official",
            consolidation_kind="unknown",
            version_date=published,
            currency_date=None,
            extent=["BR"],
            ext_module="br",
            ext=ext,
            judgment=ParsedJudgmentMeta(
                court_id=COURT_ID,
                case_name=case_name,
                neutral_citation=None,
                decision_date=published,
                docket=processo,
            ),
            paragraphs=paragraphs,
        )
    ]
