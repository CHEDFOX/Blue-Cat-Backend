"""Riksdagen SFS parser, fetcher and Swedish search behaviour (real Riksdag open data responses)."""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport, Ingestor, resolve_uri
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.riksdagen import FetchError, RiksdagenSfsConnector, document_id
from ulis.sources.normalized import ParsedDocument, ParsedProvision, ParseSkipped
from ulis.sources.parsers.riksdagen_sfs import SfsParseError, citation_name, parse_riksdagen_sfs
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "riksdagen" / "data.riksdagen.se" / "dokument"
RF, BRB, AVTL, AB, LAS, DSL = (
    "sfs-1974-152",
    "sfs-1962-700",
    "sfs-1915-218",
    "sfs-1958-637",
    "sfs-1982-80",
    "sfs-2018-218",
)


def _read(name: str) -> bytes:
    """Fixtures are the API responses as received, stored gzip-compressed."""
    return gzip.decompress((FIXTURES / f"{name}.json.gz").read_bytes())


_parsed: dict[str, ParsedDocument] = {}


def _doc(name: str) -> ParsedDocument:
    if name not in _parsed:
        docs = parse_riksdagen_sfs(_read(name))
        assert len(docs) == 1
        _parsed[name] = docs[0]
    return _parsed[name]


def _versions(doc: ParsedDocument, eid: str) -> list[ParsedProvision]:
    return [p for p in doc.provisions if p.eid == eid]


def _one(doc: ParsedDocument, eid: str) -> ParsedProvision:
    (p,) = _versions(doc, eid)
    return p


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _versions(doc, eid)[0]
    return get_module("se").provision_citation(
        ProvisionCitationInput(
            doc.short_title or doc.title, doc.instrument_type, "SE", p.top_kind, p.num_path, "sv", ext=doc.ext
        )
    )


def test_regeringsformen_consolidation() -> None:
    rf = _doc(RF)
    assert (rf.jurisdiction_id, rf.work_uri, rf.language, rf.instrument_type) == (
        "SE",
        "/se/sfs/1974/152",
        "sv",
        "constitution",
    )
    assert rf.expression_uri == "/se/sfs/1974/152/swe@2025-10-17"
    # Consolidated by the Government Offices, republished by the Riksdag: not authentic, no start dates.
    assert rf.text_origin == "official_revised" and rf.authoritative is False
    assert rf.validity_basis == "attested_at_edition" and rf.consolidation_kind == "current_consolidation"
    assert rf.work_date == date(1974, 2, 28)  # utfärdad
    assert rf.version_date == rf.currency_date == date(2025, 10, 17)  # the record's publication date
    assert rf.ext["consolidated_to"] == "SFS 2022:1600" and rf.ext["sections_per_chapter"] is True
    assert rf.ext["citation_name"] == "regeringsformen" and rf.outstanding_changes == []
    first = _one(rf, "chapter-1-section-1-paragraph-1")
    assert first.text == "All offentlig makt i Sverige utgår från folket."
    assert (first.status, first.valid_from, first.valid_to) == ("in_force", None, None)
    assert _one(rf, "chapter-1-section-1").heading is None
    assert _one(rf, "chapter-1-section-1").context_heading == "1 kap. Statsskickets grunder"
    # 2 kap. 1 §: a list of points in the first paragraph, then two more paragraphs.
    sec = _one(rf, "chapter-2-section-1")
    assert sec.heading == "Opinionsfriheter" and sec.context_heading == "2 kap. Grundläggande fri- och rättigheter"
    assert sec.note == "Wording from Lag (2010:1408)."
    assert "Lag (2010:1408)" not in sec.text  # the amendment marker is editorial
    intro = _one(rf, "chapter-2-section-1-paragraph-1")
    assert intro.text.startswith("Var och en är gentemot det allmänna tillförsäkrad\n1. yttrandefrihet:")
    assert not intro.is_leaf and intro.indexable  # its own introductory words are indexed with the list
    assert _one(rf, "chapter-2-section-1-paragraph-1-point-6").text.startswith("religionsfrihet: frihet att ensam")
    assert _one(rf, "chapter-2-section-1-paragraph-3").text.startswith("I tryckfrihetsförordningen finns också")
    assert "chapter-2-section-1-paragraph-4" not in {p.eid for p in rf.provisions}
    assert {e.event_type for e in rf.events} == {"enactment", "version_change"}


def test_citations() -> None:
    rf, brb, avtl, ab, las, dsl = (_doc(n) for n in (RF, BRB, AVTL, AB, LAS, DSL))
    assert _cite(rf, "chapter-1-section-2-paragraph-1") == "1 kap. 2 § första stycket regeringsformen"
    assert _cite(rf, "chapter-2-section-1-paragraph-1-point-1") == "2 kap. 1 § första stycket 1 regeringsformen"
    assert _cite(brb, "chapter-3-section-1") == "3 kap. 1 § brottsbalken"
    assert (
        _cite(brb, "chapter-30-section-1-paragraph-1-indent-2")
        == "30 kap. 1 § första stycket andra strecksatsen brottsbalken"
    )
    # Sections numbered through the statute: no chapter in the citation.
    assert avtl.ext["sections_per_chapter"] is False and avtl.ext["citation_name_basis"] == "established_short_name"
    assert _cite(avtl, "section-36-paragraph-1") == "36 § första stycket avtalslagen"
    assert _cite(ab, "chapter-3-section-1-paragraph-1") == "3 kap. 1 § första stycket ärvdabalken"
    assert _cite(las, "section-7-paragraph-1") == "7 § första stycket lagen (1982:80) om anställningsskydd"
    assert (
        _cite(dsl, "chapter-1-section-3-paragraph-1-point-4")
        == "1 kap. 3 § första stycket 4 lagen (2018:218) med kompletterande bestämmelser till EU:s dataskyddsförordning"
    )
    assert (
        _cite(rf, "transitional-1976-871-point-2")
        == "regeringsformen, övergångsbestämmelserna till SFS 1976:871 punkt 2"
    )
    assert citation_name("2017:900", "Förvaltningslag (2017:900)") == ("förvaltningslagen (2017:900)", "title")
    assert citation_name("1942:740", "Rättegångsbalk (1942:740)") == ("rättegångsbalken", "title")
    # The four fundamental laws named in regeringsformen 1 kap. 3 § are cited by name (the successionsordning's record
    # prints its number as 1810:0926).
    assert citation_name("1949:105", "Tryckfrihetsförordning (1949:105)") == (
        "tryckfrihetsförordningen",
        "fundamental_law",
    )
    assert citation_name("1991:1469", "Yttrandefrihetsgrundlag (1991:1469)") == (
        "yttrandefrihetsgrundlagen",
        "fundamental_law",
    )
    assert citation_name("1810:0926", "Successionsordning (1810:0926);") == ("successionsordningen", "fundamental_law")
    assert document_id("1810:0926") == "sfs-1810-0926" and document_id("1982:80") == "sfs-1982-80"


def test_pending_wordings_are_dated_as_the_source_states() -> None:
    dsl = _doc(DSL)
    # 1 kap. 3 §: the current wording ceases and a new one (with a fourth point) applies from 2027-01-01.
    current, new = _versions(dsl, "chapter-1-section-3")
    assert (current.status, current.valid_from, current.valid_to) == ("in_force", None, date(2027, 1, 1))
    assert (new.status, new.valid_from, new.valid_to) == ("in_force", date(2027, 1, 1), None)
    assert current.note == "/Upphör att gälla U:2027-01-01/ Wording from Lag (2021:1174)."
    assert new.note == "/Träder i kraft I:2027-01-01/ Wording from Lag (2026:1589)."
    assert [p.valid_from for p in _versions(dsl, "chapter-1-section-3-paragraph-1-point-4")] == [date(2027, 1, 1)]
    # Reported once, in words that hold for both wordings of the section.
    assert dsl.outstanding_changes == [
        {
            "affects_eid": "chapter-1-section-3",
            "affected_label": "1 kap. 3 §",
            "by_uri": None,
            "by_title": "Lag (2026:1589)",
            "description": "A new wording is printed in the consolidation (/Träder i kraft I:2027-01-01/); "
            "recorded from 2027-01-01.",
        }
    ]
    brb = _doc(BRB)
    # 32 kap. 1 §: three wordings, one of which applies for a single day.
    assert [(p.valid_from, p.valid_to) for p in _versions(brb, "chapter-32-section-1")] == [
        (None, date(2027, 1, 1)),
        (date(2027, 1, 1), date(2027, 1, 2)),
        (date(2027, 1, 2), None),
    ]
    # 27 kap.: the whole chapter is replaced on a date the Government has yet to set.
    old, replacement = _versions(brb, "chapter-27-section-1")
    assert (old.status, replacement.status) == ("in_force", "prospective")
    assert old.context_heading == "27 kap. Om villkorlig dom"
    assert replacement.context_heading == "27 kap. Om tilläggssanktioner till villkorligt fängelse"
    assert replacement.valid_from is None and "den dag som regeringen bestämmer" in (replacement.note or "")
    pending = [c["description"] for c in brb.outstanding_changes if c["affects_eid"] == "chapter-27-section-1"]
    assert pending == [
        "A new wording is printed in the consolidation (/Kapitlet träder i kraft I:den dag som regeringen "
        "bestämmer/); recorded as prospective (commencement date not yet set)."
    ]
    # A section of the replaced chapter that the new chapter does not have: it ceases, nothing replaces it.
    (gone,) = [c for c in brb.outstanding_changes if c["affects_eid"] == "chapter-27-section-2a"]
    assert gone["description"].endswith("no new wording is printed.")


def test_repealed_renumbered_and_list_structure() -> None:
    brb, ab, las, rf = _doc(BRB), _doc(AB), _doc(LAS), _doc(RF)
    gone = _one(brb, "chapter-22-section-6")
    assert (gone.status, gone.text, gone.valid_from) == ("repealed", "", None)
    assert gone.note == "6 § har uphävts genom lag (2014:407)."  # spelled so in the source
    moved = _one(brb, "chapter-26-section-3a")
    assert moved.status == "repealed" and moved.note == "3 a § Har betecknats 26 kap. 2 a § genom lag (2026:1318)."
    assert _one(ab, "chapter-7-section-6").note == "6 § har upphört att gälla genom lag (1978:855)."
    # "8 kap. har upphört att gälla genom lag (1978:855)." is a repealed chapter, not a heading.
    assert not any(p.eid.startswith("chapter-8-") for p in ab.provisions)
    assert _one(rf, "transitional-1976-871-point-3").status == "repealed"
    # A closing clause after a list continues the paragraph ("döms, om gärningen ...").
    first = _one(brb, "chapter-4-section-3-paragraph-1")
    assert first.text.splitlines()[-1].startswith("döms, om gärningen har bevittnats av ett barn")
    assert _one(brb, "chapter-4-section-3-paragraph-2").text.startswith("Om brottet är grovt")
    # An unnumbered list stays in its paragraph (39 §: "16 månadslöner ..., 24 ..., 32 ...").
    second = _one(las, "section-39-paragraph-2")
    assert second.text.splitlines()[1:] == [
        "16 månadslöner vid mindre än fem års anställningstid,",
        "24 månadslöner vid minst fem men mindre än tio års anställningstid,",
        "32 månadslöner vid minst tio års anställningstid.",
    ]
    assert _one(las, "section-39-paragraph-3").text.startswith("Skadeståndet får dock inte")
    # Source quirks: a marker printed before "§" ("4 c /Träder i kraft I:.../ § Ett ..."), and the
    # amendment marker printed before a last paragraph (35 kap. 7 §).
    four_c = _one(brb, "chapter-26-section-4c")
    assert four_c.status == "prospective" and four_c.text.startswith(
        "Ett villkorligt fängelsestraff är helt verkställt"
    )
    current_7 = [p for p in _versions(brb, "chapter-35-section-7-paragraph-3") if p.status == "in_force"]
    assert [p.text for p in current_7] == ["Första och andra styckena gäller även vite som har dömts ut."]
    assert "Lag (2025:151)" not in _versions(brb, "chapter-35-section-7-paragraph-2")[0].text
    assert "Lag (" not in _one(rf, "transitional-1976-871").text  # the group's text has no markers
    # Hard-wrapped lines: no space after a line-final hyphen, except before a conjunction.
    assert "gär-ningsorterna" in _one(brb, "chapter-2-section-5-paragraph-1").text
    assert "fri- och rättigheterna" in _one(rf, "chapter-2-section-20-paragraph-2").text


def test_statute_without_numbered_sections_is_one_provision() -> None:
    # "Regeringen föreskriver att följande uppräkningsfaktorer ska användas ..." has no "1 §": cited as the statute.
    doc = _doc("sfs-2014-1308")
    assert [p.eid for p in doc.provisions] == ["main-text", "main-text-paragraph-1", "main-text-paragraph-2"]
    assert _one(doc, "main-text").text.startswith(
        "Regeringen föreskriver att följande uppräkningsfaktorer ska användas"
    )
    # the table that follows is part of the text (a line like "Inkomstår Uppräkningsfaktor" is not taken for a heading)
    assert _one(doc, "main-text-paragraph-2").text == "Inkomstår Uppräkningsfaktor\n2014 1,033\n2015 1,046"
    assert _cite(doc, "main-text-paragraph-1").startswith(
        "första stycket förordningen (2014:1308) om uppräkningsfaktorer"
    )
    assert doc.instrument_type == "ordinance" and _one(doc, "main-text").status == "in_force"


def _with_text(text: str) -> bytes:
    """The record of 2014:1308 with another text (for layouts absent from the fixtures)."""
    raw = json.loads(_read("sfs-2014-1308"))
    raw["dokumentstatus"]["dokument"]["text"] = text
    return json.dumps(raw).encode()


def test_an_announcement_of_one_sentence_is_not_taken_for_a_heading() -> None:
    # Tillkännagivande (2000:162): one line without closing punctuation ("... 2,5 procent.2", a footnote mark), nothing
    # else; with no section or chapter in the record there is nothing for a heading to head.
    doc = _doc("sfs-2000-162")
    assert [p.eid for p in doc.provisions] == ["main-text", "main-text-paragraph-1"]
    assert _one(doc, "main-text").text.startswith("Riksbanken ger till känna att Riksbankens diskonto")


def test_division_headings_and_headings_ending_in_m_m() -> None:
    text = (
        "AVDELNING I. INLEDANDE BESTÄMMELSER\r\n\r\n1 kap. Innehåll och tillämpningsområde\r\n\r\n"
        "1 § Denna lag innehåller bestämmelser om tillämpningsområdet.\r\n\r\n"
        "Ansvarsbestämmelser m. m.\r\n\r\n2 § Den som bryter mot lagen döms till böter.\r\n"
    )
    (doc,) = parse_riksdagen_sfs(_with_text(text))
    assert [p.eid for p in doc.provisions if p.kind == "section"] == ["section-1", "section-2"]
    assert _one(doc, "section-2").heading == "Ansvarsbestämmelser m. m."
    assert _one(doc, "section-1").context_heading == "1 kap. Innehåll och tillämpningsområde"


def test_old_statutes_print_a_full_stop_after_the_section_sign() -> None:
    doc = _doc("sfs-1959-82")
    assert [p.eid for p in doc.provisions][:4] == [
        "section-1",
        "section-1-paragraph-1",
        "section-2",
        "section-2-paragraph-1",
    ]
    assert _one(doc, "section-1").text.startswith("Bestämmelserna i denna kungörelse skola äga tillämpning")
    assert _cite(doc, "section-1-paragraph-1").startswith("1 § första stycket kungörelsen (1959:82) om skattefrihet")


def test_an_annex_is_its_own_provision_not_part_of_the_transitional_provisions() -> None:
    doc = _doc(
        "sfs-2013-125"
    )  # an agreement approved by Act: the agreement is the annex, printed after the transitional provisions
    annex = _one(doc, "annex-1")
    assert (
        annex.top_kind == "annex"
        and annex.heading == "Bilaga"
        and annex.text.startswith("AGREEMENT BETWEEN THE GOVERNMENT OF SWEDEN")
    )
    assert len(annex.text) > 30000
    transitional = [p for p in doc.provisions if p.top_kind == "transitional"]
    assert transitional and all("AGREEMENT BETWEEN" not in p.text for p in transitional)
    assert _cite(doc, "annex-1").startswith(
        "bilagan till lagen (2013:125) om avtal mellan Sverige och Antigua och Barbuda"
    )
    assert _one(doc, "transitional-2013-125-point-2").text.startswith("Denna lag ska tillämpas för beskattningsår")


def test_a_repealed_statute_or_one_without_text_is_skipped_not_an_error() -> None:
    with pytest.raises(ParseSkipped, match="repealed"):
        parse_riksdagen_sfs(_read("sfs-2005-676"))
    empty = json.loads(_read("sfs-2014-1308"))
    empty["dokumentstatus"]["dokument"]["text"] = ""  # e.g. sfs-2013-984: no text and not flagged as repealed
    with pytest.raises(ParseSkipped, match="no text"):
        parse_riksdagen_sfs(json.dumps(empty).encode())


def test_rejects_unsupported_documents() -> None:
    raw = json.loads(_read(AVTL))
    repealed = json.loads(json.dumps(raw))
    repealed["dokumentstatus"]["dokumentuppgift"]["uppgift"].append(
        {"kod": "upphavd", "namn": "upphavd", "text": "2018-07-01 00:00:00"}
    )
    with pytest.raises(ParseSkipped, match="repealed"):  # skipped, not an error: the whole list is ingested
        parse_riksdagen_sfs(json.dumps(repealed).encode())
    other = json.loads(json.dumps(raw))
    other["dokumentstatus"]["dokument"]["subtyp"] = "sfsr"
    with pytest.raises(SfsParseError, match="sfs/sfst"):
        parse_riksdagen_sfs(json.dumps(other).encode())
    with pytest.raises(SfsParseError, match="not JSON"):
        parse_riksdagen_sfs(b"<html/>")
    with pytest.raises(SfsParseError, match="not a Riksdag document"):
        parse_riksdagen_sfs(b"{}")
    assert resolve_uri("https://data.riksdagen.se/dokument/sfs-1974-152.json") == ("/se/sfs/1974/152", None)


# --------------------------------------------------------------------------- connector


def _manifest(tmp_path: Path, works: list[dict[str, Any]], **overrides: Any) -> Path:
    manifest = {
        "schema_version": 1,
        "source_id": "se-riksdagen-sfs",
        "base_url": "https://data.riksdagen.se/dokument",
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 5,
        "works": works,
        **overrides,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True))
    return path


def test_connector_reads_the_cache_offline(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "data.riksdagen.se" / "dokument"
    cache.mkdir(parents=True)
    for name in (RF, DSL):
        (cache / f"{name}.json").write_bytes(_read(name))
        (cache / f"{name}.json.meta.json").write_bytes((FIXTURES / f"{name}.json.meta.json").read_bytes())
    path = _manifest(tmp_path, [{"sfs": "1974:152"}, {"sfs": "2018:218"}])
    docs = list(RiksdagenSfsConnector(path, "se-riksdagen-sfs", tmp_path / "cache", offline=True).fetch())
    assert [d.external_id for d in docs] == ["riksdagen:sfs-1974-152", "riksdagen:sfs-2018-218"]
    assert docs[0].uri == "https://data.riksdagen.se/dokument/sfs-1974-152.json"
    assert docs[0].content == _read(RF) and docs[0].media_type == "application/json"
    meta = json.loads((FIXTURES / f"{RF}.json.meta.json").read_text())
    assert docs[0].retrieved_at.isoformat() == meta["retrieved_at"]
    missing = _manifest(tmp_path, [{"sfs": "1962:700"}])
    with pytest.raises(FetchError, match="offline and not cached"):
        list(RiksdagenSfsConnector(missing, "se-riksdagen-sfs", tmp_path / "cache", offline=True).fetch())


def test_connector_reports_a_missing_statute_and_goes_on(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "data.riksdagen.se" / "dokument"
    cache.mkdir(parents=True)
    (cache / "sfs-1999-1.json").write_bytes(b"")  # a 404 recorded by the shared fetcher
    (cache / "sfs-1999-1.json.meta.json").write_text(
        json.dumps({"url": "https://data.riksdagen.se/dokument/sfs-1999-1.json", "status": 404})
    )
    (cache / f"{DSL}.json").write_bytes(_read(DSL))
    (cache / f"{DSL}.json.meta.json").write_bytes((FIXTURES / f"{DSL}.json.meta.json").read_bytes())
    path = _manifest(tmp_path, [{"sfs": "1999:1", "title": "Gone"}, {"sfs": "2018:218", "title": "Dataskyddslagen"}])
    connector = RiksdagenSfsConnector(path, "se-riksdagen-sfs", tmp_path / "cache", offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["riksdagen:sfs-2018-218"]
    assert len(connector.fetch_errors) == 1 and "sfs-1999-1" in connector.fetch_errors[0]
    twice = _manifest(tmp_path, [{"sfs": "1982:80"}, {"sfs": "1982:80"}])
    with pytest.raises(ManifestError, match="listed twice"):
        list(RiksdagenSfsConnector(twice, "se-riksdagen-sfs", tmp_path / "cache", offline=True).fetch())


def test_committed_manifest_lists_the_publishers_statutes_in_force(catalog: Catalog) -> None:
    path = REPO_ROOT / catalog.sources.get("se-riksdagen-sfs").access.endpoint  # type: ignore[operator]
    manifest = RiksdagenSfsConnector(path, "se-riksdagen-sfs", Path("/nonexistent"))._manifest()
    works = manifest["works"]
    assert [w["sfs"] for w in works[:8]] == [
        "1974:152",
        "1962:700",
        "1915:218",
        "1958:637",
        "1982:80",
        "2017:900",
        "2018:218",
        "1942:740",
    ]
    assert len(works) > 5000 and all(w["title"] for w in works)
    assert len({w["sfs"] for w in works}) == len(works)


def test_connector_validates_the_manifest(tmp_path: Path) -> None:
    assert document_id("1982:80") == "sfs-1982-80"
    with pytest.raises(ManifestError, match="user_agent"):
        list(
            RiksdagenSfsConnector(
                _manifest(tmp_path, [], user_agent="python-urllib"), "se-riksdagen-sfs", tmp_path
            ).fetch()
        )
    with pytest.raises(ManifestError, match="invalid SFS number"):
        list(RiksdagenSfsConnector(_manifest(tmp_path, [{"sfs": "../x"}]), "se-riksdagen-sfs", tmp_path).fetch())
    with pytest.raises(ManifestError, match="base_url"):
        list(
            RiksdagenSfsConnector(
                _manifest(tmp_path, [], base_url="https://example.org/dokument"), "se-riksdagen-sfs", tmp_path
            ).fetch()
        )


# --------------------------------------------------------------------------- PostgreSQL

# The six pilot statutes, then three from the whole collection: no numbered section, "1 §." style, and an annex.
FIXTURE_ORDER = [RF, AVTL, DSL, LAS, AB, BRB, "sfs-2014-1308", "sfs-1959-82", "sfs-2013-125"]


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for name in FIXTURE_ORDER:
            meta = json.loads((FIXTURES / f"{name}.json.meta.json").read_text())
            yield RawDocument(
                f"riksdagen:{name}",
                meta["url"],
                "application/json",
                _read(name),
                datetime.fromisoformat(meta["retrieved_at"]).astimezone(UTC),
                {},
            )


@pytest.fixture(scope="module")
def se_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "se-riksdagen-sfs", actor="pytest", connector=_Fixtures()
        )
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="SE", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_search_citations_and_limitations(session: Session, catalog: Catalog, se_ingested: IngestionReport) -> None:
    assert se_ingested.errors == [] and se_ingested.conflicts == []
    assert se_ingested.documents_new == len(FIXTURE_ORDER) and se_ingested.instruments_created == len(FIXTURE_ORDER)
    res = _search(session, catalog, q="all offentlig makt utgår från folket", query_language="sv")
    hit = res["results"][0]
    assert hit["citation"] == "1 kap. 1 § första stycket regeringsformen"
    assert hit["text"] == "All offentlig makt i Sverige utgår från folket."
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"REVISED_TEXT", "PROFILE_UNVERIFIED", "SOURCE_NOT_AUTHENTIC", "ATTESTED_ONLY_AT"} <= codes
    assert "NOT_AUTHENTIC_TEXT" not in codes  # REVISED_TEXT already says so
    assert hit["source"]["attribution"].startswith("Källa: Sveriges riksdag")
    jamkning = _search(session, catalog, q="avtalsvillkor jämkas oskäligt", query_language="sv")
    assert jamkning["results"][0]["citation"] == "36 § första stycket avtalslagen"
    mord = _search(session, catalog, q="berövar annan livet döms för mord", query_language="sv")
    assert mord["results"][0]["citation"] == "3 kap. 1 § första stycket brottsbalken"
    # Statutes of the whole collection: one without numbered sections, an old "1 §." one and one with an annex.
    index = _search(session, catalog, q="uppräkningsfaktorer preliminära kommunalskattemedel 2015", query_language="sv")
    assert index["results"][0]["citation"].startswith("första stycket förordningen (2014:1308) om uppräkningsfaktorer")
    old = _search(session, catalog, q="skattefrihet anslag lågkonjunkturfond kungörelse", query_language="sv")
    assert old["results"][0]["citation"].startswith("1 § första stycket kungörelsen (1959:82) om skattefrihet")
    annex = _search(
        session, catalog, q="Antigua och Barbuda utbyte av upplysningar i skatteärenden", query_language="sv"
    )
    cites = [r["citation"] for r in annex["results"]]
    assert any(c.startswith("bilagan till lagen (2013:125) om avtal mellan Sverige och Antigua") for c in cites), cites
    # A provision with a pending new wording carries the outstanding change.
    dsl = _search(session, catalog, q="Säkerhetspolisens behandling av personuppgifter", query_language="sv")
    top = next(r for r in dsl["results"] if r["citation"].startswith("1 kap. 3 §"))
    assert "OUTSTANDING_CHANGES" in {lim["code"] for lim in top["limitations"]}


@pytest.mark.db
def test_point_in_time_follows_the_source(session: Session, catalog: Catalog, se_ingested: IngestionReport) -> None:
    uri = "/se/sfs/2018/218/~chapter-1-section-3"
    now = provision_at(session, catalog, uri, date(2026, 10, 14))
    then = provision_at(session, catalog, uri, date(2027, 2, 1))
    assert now["status_on_date"] == "in_force_date_unverified" and "2026:1575" not in now["version"]["text"]
    assert then["status_on_date"] == "in_force" and then["version"]["valid_from"] == "2027-01-01"
    assert "lagen (2026:1575)" in then["version"]["text"]
    assert now["provision"]["citation"].startswith("1 kap. 3 § lagen (2018:218)")
    point = "/se/sfs/2018/218/~chapter-1-section-3-paragraph-1-point-4"
    assert provision_at(session, catalog, point, date(2026, 10, 14))["status_on_date"] == "not_yet_in_force"
    assert provision_at(session, catalog, point, date(2025, 1, 1))["status_on_date"] == "history_not_indexed"
    # Brottsbalken 32 kap. 1 §: the wording of 2026:1605 applies on 2027-01-01 only.
    brb = "/se/sfs/1962/700/~chapter-32-section-1"
    day = provision_at(session, catalog, brb, date(2027, 1, 1))
    after = provision_at(session, catalog, brb, date(2027, 1, 2))
    assert day["version"]["valid_to"] == "2027-01-02" and day["version"]["valid_to_basis"] == "explicit"
    assert after["version"]["valid_from"] == "2027-01-02"
    # A chapter replaced on a date still to be set: the new wording is prospective.
    chapter = provision_at(session, catalog, "/se/sfs/1962/700/~chapter-27-section-1", date(2026, 10, 14))
    assert chapter["status_on_date"] == "in_force_date_unverified"
    assert "villkorlig dom" in chapter["version"]["text"]
    assert {t["status"] for t in chapter["timeline"]} == {"in_force", "prospective"}


@pytest.mark.db
def test_integrity_of_ingested_documents(
    session: Session, catalog: Catalog, store: LocalObjectStore, se_ingested: IngestionReport
) -> None:
    docs = session.scalars(select(m.SourceDocument).where(m.SourceDocument.source_id == "se-riksdagen-sfs")).all()
    assert len(docs) == len(FIXTURE_ORDER)
    results = [verify_source_document(session, catalog, store, doc.id) for doc in docs]
    assert all(r["result"] == "pass" for r in results), [r["problems"][:3] for r in results]
    assert all(r["checked_units"] > 0 for r in results)
