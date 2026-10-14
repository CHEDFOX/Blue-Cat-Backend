"""Parser for international treaties as published by Switzerland in the Classified Compilation (SR 0.x) on Fedlex.

Same files as :mod:`ulis.sources.parsers.fedlex` (Akoma Ntoso 3.0, one file per consolidation and
language, German / French / Italian), read by the same reader, but the instrument is a **treaty** that
belongs to an international organisation or to the international legal order, not to Switzerland:

* ``TREATIES`` lists, by ELI, the treaties ULIS reads and the jurisdiction each belongs to (``COE`` for
  the European Convention on Human Rights, its Protocols and the Statute of the Council of Europe,
  ``INT`` for the Charter of the United Nations, the Statute of the ICJ, the Vienna Convention on the
  Law of Treaties and the Rome Statute). A treaty not listed is refused.
* The work URI is ``/<jurisdiction>/treaty/ch-sr-<SR number>`` - the Swiss publication of the treaty,
  not the depositary's.
* The Swiss file carries, as a second document (``<doc name="scope">``), the treaty's **scope table**
  ("Geltungsbereich" / "Champ d'application" / "Campo d'applicazione"): every State party with the date
  of ratification, accession or succession and of entry into force, as of the consolidation date. It is
  not law text; it becomes the treaty's parties (``ParsedTreatyParty``) for the States that have a
  jurisdiction profile (``STATES``). States without a profile are counted and left out.
* ``Texte original`` / ``Originaltext`` / ``Testo originale`` marks the original-language text; the
  others are ``Übersetzung`` / ``Traduction`` / ``Traduzione``. No Swiss file is the treaty's authentic
  text (the depositary's is), so ``authoritative`` is always false and ``ext.original_text`` says which
  version is the original-language one (French for all the treaties listed).

Provisions, validity (whole-instrument snapshots, one per Swiss consolidation), repeals, notes and
references are read exactly as for Swiss federal acts. The preamble, the final formulas and signatures and
the Swiss declaration component are not read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.ingestion.snapshots import SNAPSHOT_BASIS
from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedTreatyParty
from ulis.sources.parsers.fedlex import (
    LANGUAGES,
    MONTHS,
    XML_LANG,
    FedlexParseError,
    _a,
    _flat,
    _frbr_date,
    _local,
    _provisions,
    _Reader,
    _ws,
)

PARSER_ID = "fedlex_treaty"
PARSER_VERSION = "1.0.0"
ELI_CC = "https://fedlex.data.admin.ch/eli/cc/"


@dataclass(frozen=True)
class Treaty:
    sr: str  # number in the Swiss Classified Compilation, without the leading "RS"/"SR"
    jurisdiction: str  # profile the treaty belongs to
    # What the first two date columns of the scope table mean: "consent" (ratification, accession or
    # succession), "eif" (entry into force, or participation since) or None (ignored).
    columns: tuple[str | None, str | None] = ("consent", "eif")


# Keyed by the ELI path after https://fedlex.data.admin.ch/eli/cc/ (the work, without consolidation date).
TREATIES: dict[str, Treaty] = {
    "1974/2151_2151_2151": Treaty("0.101", "COE"),  # European Convention on Human Rights
    "1987/1807_1807_1807": Treaty("0.101.06", "COE"),  # Protocol No 6
    "1988/1598_1598_1598": Treaty("0.101.07", "COE"),  # Protocol No 7
    "2003/396": Treaty("0.101.093", "COE"),  # Protocol No 13
    "2009/377": Treaty("0.101.094", "COE"),  # Protocol No 14
    "2021/461": Treaty("0.101.095", "COE"),  # Protocol No 15
    "1963/772_769_797": Treaty("0.192.030", "COE"),  # Statute of the Council of Europe
    "2003/160": Treaty("0.120", "INT"),  # Charter of the United Nations
    # Second column of its table is the declaration under Article 36, which is not read.
    "1948/1048_1037_1010": Treaty("0.193.501", "INT", ("eif", None)),  # Statute of the ICJ
    "1990/1112_1112_1112": Treaty("0.111", "INT"),  # Vienna Convention on the Law of Treaties
    "2002/586": Treaty("0.312.1", "INT"),  # Rome Statute
}

ORIGINAL = {"texte original", "originaltext", "testo originale"}
TRANSLATION = ("übersetzung", "traduction", "traduzione")

# State -> jurisdiction id of the profiles that exist today, in the language of the scope table. A State
# with no entry here is not stored as a party. To add a State once its profile exists, add a row and
# raise PARSER_VERSION.
STATES: dict[str, dict[str, tuple[str, ...]]] = {
    "AR": {"de": ("Argentinien",), "fr": ("Argentine",), "it": ("Argentina",)},
    "AT": {"de": ("Österreich",), "fr": ("Autriche",), "it": ("Austria",)},
    "AU": {"de": ("Australien",), "fr": ("Australie",), "it": ("Australia",)},
    "CA": {"de": ("Kanada",), "fr": ("Canada",), "it": ("Canada",)},
    "CH": {"de": ("Schweiz",), "fr": ("Suisse",), "it": ("Svizzera",)},
    "DE": {"de": ("Deutschland",), "fr": ("Allemagne",), "it": ("Germania",)},
    "DK": {"de": ("Dänemark",), "fr": ("Danemark",), "it": ("Danimarca",)},
    "EE": {"de": ("Estland",), "fr": ("Estonie",), "it": ("Estonia",)},
    "ES": {"de": ("Spanien",), "fr": ("Espagne",), "it": ("Spagna",)},
    "FI": {"de": ("Finnland",), "fr": ("Finlande",), "it": ("Finlandia",)},
    "GB": {"de": ("Vereinigtes Königreich",), "fr": ("Royaume-Uni",), "it": ("Regno Unito",)},
    "IE": {"de": ("Irland",), "fr": ("Irlande",), "it": ("Irlanda",)},
    "IN": {"de": ("Indien",), "fr": ("Inde",), "it": ("India",)},
    "IT": {"de": ("Italien",), "fr": ("Italie",), "it": ("Italia",)},
    "JP": {"de": ("Japan",), "fr": ("Japon",), "it": ("Giappone",)},
    "LV": {"de": ("Lettland",), "fr": ("Lettonie",), "it": ("Lettonia",)},
    "NL": {"de": ("Niederlande",), "fr": ("Pays-Bas",), "it": ("Paesi Bassi",)},
    "NZ": {"de": ("Neuseeland",), "fr": ("Nouvelle-Zélande",), "it": ("Nuova Zelanda",)},
    "PL": {"de": ("Polen",), "fr": ("Pologne",), "it": ("Polonia",)},
    "SE": {"de": ("Schweden",), "fr": ("Suède",), "it": ("Svezia",)},
    "US": {"de": ("Vereinigte Staaten",), "fr": ("Etats-Unis", "États-Unis"), "it": ("Stati Uniti",)},
}
STATE_BY_NAME: dict[str, dict[str, str]] = {
    lang: {name: jid for jid, names in STATES.items() for name in names[lang]} for lang in LANGUAGES
}

# "19 novembre 1946", "1er juillet 2002", "1 er décembre 1976", "10. Februar 2003", "1° maggio 2003";
# the year may carry a marker ("2003 A": accession, "S"/"N": succession).
DATE_CELLS = re.compile(r"^(\d{1,2})\s*(?:\.|er|°)?\s*([^\W\d_]+\.?)\s+(\d{4})(?:\s+([A-Za-z]))?$")
CONSENT_MARKERS = {"A": "accession", "B": "accession", "S": "succession", "N": "succession"}


def work_uri(treaty: Treaty) -> str:
    return f"/{treaty.jurisdiction.lower()}/treaty/ch-sr-{treaty.sr}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Map the Fedlex URI of a listed treaty to its ULIS work URI (else None)."""
    m = re.match(r"^https?://(?:fedlex\.data\.admin\.ch|www\.fedlex\.admin\.ch)/eli/cc/([0-9A-Za-z]+/[0-9a-z_]+)/?$", uri)
    treaty = TREATIES.get(m.group(1)) if m else None
    return (work_uri(treaty), None) if treaty else None


def _plain(el: Element) -> str:
    """Text of a cell without footnote markers (``<sup>``) and notes."""
    parts = [el.text or ""]
    for child in el:
        if _local(child.tag) not in ("sup", "authorialNote", "placeholder"):
            parts.append(_plain(child) if _local(child.tag) != "br" else " ")
        parts.append(child.tail or "")
    return "".join(parts)


def _state_name(cell: Element) -> str:
    """``Allemagne* **`` -> ``Allemagne``; ``Afrique du Sud a`` -> ``Afrique du Sud``."""
    name = _ws(_plain(cell))
    name = re.sub(r"(?:\s*\*+)+$", "", name).strip()
    return re.sub(r"\s+[a-z]$", "", name)


def _date_of(first: str, second: str) -> tuple[date | None, str | None]:
    text = _ws(f"{first} {second}")
    if not text:
        return None, None
    m = DATE_CELLS.match(text)
    month = MONTHS.get(m.group(2).lower()) if m else None
    if m is None or month is None:
        raise FedlexParseError(f"unreadable date {text!r} in the scope table")
    try:
        return date(int(m.group(3)), month, int(m.group(1))), m.group(4)
    except ValueError as exc:
        raise FedlexParseError(f"invalid date {text!r} in the scope table") from exc


SCOPE_WORDS = ("geltungsbereich", "champ d’application", "champ d'application", "campo d’applicazione", "campo d'applicazione")


def _is_scope(doc: Element) -> bool:
    """The component document that holds the scope table. Two Italian files give it the name "annex" instead
    of "scope"; it is then recognised by its mainBody being a table and its header naming the scope."""
    if doc.get("name") == "scope":
        return True
    body = doc.find(_a("mainBody"))
    if doc.get("name") != "annex" or body is None or body.find(_a("table")) is None:
        return False
    return any(w in _ws(_flat_all(doc)).lower() for w in SCOPE_WORDS)


def _flat_all(el: Element) -> str:
    return "".join(el.itertext())


def _parties(act: Element, treaty: Treaty, language: str) -> tuple[list[ParsedTreatyParty], int]:
    """Parties of the treaty with a profile, and the number of States in the table."""
    table = None
    for doc in act.iter(_a("doc")):
        if _is_scope(doc) and table is None:
            table = doc.find(f".//{_a('table')}")
    if table is None:
        raise FedlexParseError("the file has no scope table")
    parties: dict[str, ParsedTreatyParty] = {}
    states = 0
    for tr in table.iter(_a("tr")):
        cells = list(tr)
        if len(cells) < 3 or any(_local(c.tag) != "td" for c in cells) or int(cells[0].get("colspan") or 1) >= 4:
            continue  # header, spacer and footnote rows (a footnote cell spans the table)
        name = _state_name(cells[0])
        if not name:
            continue
        states += 1
        data = [_ws(_flat(c)) for c in cells[1:]]
        data += [""] * (4 - len(data))
        values = {}
        consent_marker = None
        for column, (first, second) in zip(treaty.columns, ((data[0], data[1]), (data[2], data[3])), strict=True):
            if column is None:
                continue
            when, marker = _date_of(first, second)
            values[column] = when
            if column == "consent":
                consent_marker = marker
        jid = STATE_BY_NAME[language].get(name)
        if jid is None:
            continue
        if jid in parties:
            raise FedlexParseError(f"{name} appears twice in the scope table")
        consented = values.get("consent")
        if consented is None and values.get("eif") is None:
            continue  # a row without dates (e.g. the Kingdom of the Netherlands, listed by territory in one file)
        parties[jid] = ParsedTreatyParty(
            party=jid,
            signed_on=None,
            consent_type=(CONSENT_MARKERS.get(consent_marker or "", "ratification") if consented else None),
            consented_on=consented,
            entry_into_force_on=values.get("eif"),
        )
    if states < 2:
        raise FedlexParseError("the scope table lists no States")
    return list(parties.values()), states


def _marker(preface: Element | None) -> bool:
    """True for the original-language text, False for a translation."""
    found = [_ws(_flat(p)).lower() for p in (preface.iter(_a("p")) if preface is not None else [])]
    if any(f in ORIGINAL for f in found):
        return True
    if any(f.startswith(TRANSLATION) for f in found):
        return False
    raise FedlexParseError("the preface says neither that this is the original text nor a translation")


def parse_fedlex_treaty(content: bytes) -> list[ParsedDocument]:
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
        raise FedlexParseError(f"unsupported language {language!r}")
    uri_el = work.find(_a("FRBRuri"))
    m = re.match(
        r"^https://fedlex\.data\.admin\.ch/eli/cc/([0-9A-Za-z]+/[0-9a-z_]+)/(\d{8})$",
        uri_el.get("value", "") if uri_el is not None else "",
    )
    if not m:
        raise FedlexParseError("FRBRWork/FRBRuri is not an SR consolidation (eli/cc/<work>/<YYYYMMDD>)")
    treaty = TREATIES.get(m.group(1))
    if treaty is None:
        raise FedlexParseError(f"{m.group(1)} is not a treaty this parser reads (see TREATIES)")
    eli = ELI_CC + m.group(1)
    version_date = _frbr_date(work, "jolux:dateApplicability")
    if version_date is None or version_date.strftime("%Y%m%d") != m.group(2):
        raise FedlexParseError("missing or inconsistent jolux:dateApplicability")
    preface = act.find(_a("preface"))
    doc_number = preface.find(f".//{_a('docNumber')}") if preface is not None else None
    number_el = work.find(_a("FRBRnumber"))
    sr = _ws(_flat(doc_number)) if doc_number is not None else ""
    sr = sr or (number_el.get("value", "") if number_el is not None else "")
    if sr != treaty.sr:
        raise FedlexParseError(f"{eli}: SR number {sr!r} is not {treaty.sr}")
    name_el = next((el for el in work.findall(_a("FRBRname")) if el.get(XML_LANG) == language), None)
    if name_el is None or not name_el.get("value"):
        raise FedlexParseError(f"no FRBRname in {language}")
    title = _ws(name_el.get("value", ""))
    original = _marker(preface)
    adopted = _frbr_date(work, "jolux:dateDocument")
    swiss_in_force = _frbr_date(work, "jolux:dateEntryInForce")

    body = act.find(_a("body"))
    if body is None:
        raise FedlexParseError("missing body")
    reader = _Reader()
    reader.walk(body, "", [], None)
    for doc in act.iter(_a("doc")):
        if doc.get("name") == "annex" and not _is_scope(doc):
            reader.annex(doc)
    if not reader.nodes:
        raise FedlexParseError("no provisions found")
    provisions = _provisions(reader, version_date)
    for p in provisions:
        p.extent = []  # a treaty has no territorial extent; its scope is its parties
    parties, states = _parties(act, treaty, language)

    uri = work_uri(treaty)
    events = [
        ParsedEvent("version_change", version_date, None, None, "FRBRWork/FRBRdate[@name='jolux:dateApplicability']")
    ]
    if adopted:
        events.append(
            ParsedEvent("adoption", adopted, None, "Conclusion of the treaty", "FRBRWork/FRBRdate[@name='jolux:dateDocument']")
        )
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type="treaty",
            jurisdiction_id=treaty.jurisdiction,
            work_uri=uri,
            expression_uri=f"{uri}/{language}@{version_date.isoformat()}",
            work_title=title,
            title=title,
            short_title=_ws(name_el.get("shortForm", "")) or None,
            number=treaty.sr,
            work_date=adopted,
            language=language,
            authoritative=False,
            text_origin="official_consolidation",
            consolidation_kind="point_in_time",
            version_date=version_date,
            currency_date=version_date,
            extent=[],
            ext_module=treaty.jurisdiction.lower(),
            ext={
                "sr_number": treaty.sr,
                "eli": eli,
                "original_text": original,
                "ch_entry_into_force": swiss_in_force.isoformat() if swiss_in_force else None,
                "parties_in_table": states,
                "parties_recorded": len(parties),
            },
            events=events,
            provisions=provisions,
            treaty_parties=parties,
            validity_basis=SNAPSHOT_BASIS,
        )
    ]


__all__ = ["PARSER_ID", "PARSER_VERSION", "TREATIES", "parse_fedlex_treaty", "resolve_uri", "work_uri"]
