"""Constitutions of Europe, the Caucasus and the Middle East (the deep sweep, docs/constitutions-europe-me-deep.md).

The fixtures in ``tests/fixtures/constitution_eme`` are real lines of the documents the publishers serve (the first
articles of each text, plus the tail of the text where the manifest names a ``tail_regex``), saved after the manifest's
own HTML or PDF selection; the parser tests feed them to the parser with the structure hints of the committed
manifests. ``expectations.json`` is the golden record of what the parser makes of them; the hand-written assertions
below name the parts that matter.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker
from test_constitution_text import parse, text_bundle

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport, Ingestor, build_connector
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.constitution_docs import ConstitutionDocsConnector
from ulis.sources.normalized import ParsedDocument
from ulis.sources.registry import LicenceNotApproved, licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "constitution_eme"
RETRIEVED = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
TODAY = date(2026, 10, 16)

ACTIVE = [
    "be-senate-constitution",
    "md-presedinte-constitution",
    "lt-seimas-constitution",
    "mk-sobranie-constitution",
    "ba-ustavni-sud-constitution",
    "hu-magyar-kozlony-constitution",
    "va-governatorato-constitution",
    "az-president-constitution",
    "kz-akorda-constitution",
    "ad-consell-general-constitution",
    "mt-legislation-constitution",
    "ge-constitutional-court-constitution",
    "mc-legimonaco-constitution",
    "is-stjornarradid-constitution",
    "ro-senat-constitution",
    "xk-gjk-constitution",
    "bg-president-constitution",
    "ye-nic-constitution",
    "ie-isb-constitution",
    "ps-tscc-constitution",
]
# an explicit licence statement on the publisher's own site (the others are AMBER: official text, no stated restriction)
GREEN = {"md-presedinte-constitution", "bg-president-constitution", "ie-isb-constitution"}
PLANNED = {
    "pt-parlamento-constitution": "Reserva de propriedade",
    "rs-srbija-constitution": "Без прерада",
    "tr-tbmm-constitution": "OAI-SearchBot",
    "no-lovdata-constitution": "HTTP 405",
    "ua-rada-constitution": "Creative Commons Attribution 4.0",
    "cy-law-office-constitution": "Disallow: /",
    "ae-cabinet-constitution": "HTTP 403",
}
REJECTED = {
    "jo-cco-constitution": "إعادة نشر أجزاء بسيطة من المعلومات",
    "kw-kna-constitution": "أو نقل أي جزء من محتوياتها",
}


def fixture(slug: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIX / f"{slug}.json").read_text(encoding="utf-8"))
    return data


def parse_fixture(slug: str) -> ParsedDocument:
    data = fixture(slug)
    hints = dict(data["hints"])
    if data["media"] == "pdf":
        hints.setdefault("heading", "text")  # the default of a PDF, which the paragraphs of a test page do not have
    return parse(
        text_bundle(
            data["lines"],
            cc=data["jurisdiction"],
            language=data["language"],
            structure=hints,
            title=data["title"],
            citation_name=data["citation_name"],
        )
    )


def summary(doc: ParsedDocument) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    for p in doc.provisions:
        kinds[p.kind] = kinds.get(p.kind, 0) + 1
    first = next((p for p in doc.provisions if p.eid.startswith("art-") and "-par" not in p.eid), doc.provisions[0])
    return {
        "eids": [p.eid for p in doc.provisions if p.parent_eid is None],
        "kinds": kinds,
        "first_text": first.text[:60],
        "last": doc.provisions[-1].eid,
    }


SLUGS = sorted(p.stem for p in FIX.glob("*.json") if p.stem != "expectations")


def cite(module: str, eid: str, title: str, *, country: str = "BE", language: str = "en") -> str:
    kind = "preamble" if eid == "preamble" else "block" if eid.startswith("blk-") else "article"
    num = [] if eid == "preamble" else [eid.split("-", 1)[1]]
    return get_module(module).provision_citation(
        ProvisionCitationInput(
            instrument_title=title,
            instrument_type="constitution",
            jurisdiction_id=country,
            top_kind=kind,
            num_path=num,
            language=language,
            eid=eid,
        )
    )


# --------------------------------------------------------------------------- committed sources and manifests


@pytest.mark.parametrize("source_id", ACTIVE)
def test_committed_manifest_loads_and_matches_its_source(catalog: Catalog, source_id: str) -> None:
    source = catalog.sources.get(source_id)
    assert (
        source.status == "active" and source.connector == "constitution_docs" and source.parser == "constitution_text"
    )
    assert source.document_kinds == ["constitution"] and source.licence.review_status == "approved"
    licence_gate(source, commercial=False)
    assert source.licence.attribution_required and "{retrieved_date}" in (source.licence.attribution_text or "")
    assert source.licence.reviewed_on in (date(2026, 10, 15), date(2026, 10, 16))
    assert "not counsel" in (source.licence.reviewed_by or "") and "robots.txt" in (source.licence.reviewed_by or "")
    assert isinstance(build_connector(source), ConstitutionDocsConnector)
    manifest = ConstitutionDocsConnector(
        REPO_ROOT / source.access.endpoint, source_id, Path("/nonexistent")
    )._manifest()
    assert "ULIS-research-bot" in manifest["user_agent"] and manifest["crawl_delay_seconds"] >= 5
    countries = {d["jurisdiction"] for d in manifest["documents"]}
    assert countries == set(source.jurisdictions)
    assert {d["language"] for d in manifest["documents"]} == set(source.languages)
    for country in countries:
        profile = catalog.jurisdictions.get(country)
        assert profile.verification.status == "draft_unverified" and "constitution" in profile.instrument_types
        assert profile.citation.module == ("ie" if country == "IE" else "constitution")
        assert source_id in profile.publication.official_sources
        if country != "IE":  # Ireland's profile was written for its Acts and gained the Constitution
            assert profile.instrument_types == ["constitution"]
            assert "Only the constitution is loaded" in profile.coverage.known_gaps[0]
    for d in manifest["documents"]:
        for event in d.get("events", []):
            date.fromisoformat(str(event["date"]))
        assert d.get("authoritative") is False


def test_the_licence_gate_lets_a_commercial_deployment_use_only_the_explicit_licences(catalog: Catalog) -> None:
    for source_id in ACTIVE:
        source = catalog.sources.get(source_id)
        if source_id in GREEN:
            assert source.licence.commercial_use == "allowed_with_conditions"
            licence_gate(source, commercial=True)
        else:
            assert source.licence.commercial_use == "unknown"
            with pytest.raises(LicenceNotApproved, match="commercial"):
                licence_gate(source, commercial=True)


@pytest.mark.parametrize("source_id", sorted(PLANNED) + sorted(REJECTED))
def test_blocked_publishers_are_recorded_with_their_words_and_refused_by_the_gate(
    catalog: Catalog, source_id: str
) -> None:
    source = catalog.sources.get(source_id)
    assert source.status == ("rejected" if source_id in REJECTED else "planned")
    assert source.licence.reviewed_on in (date(2026, 10, 15), date(2026, 10, 16))
    assert source.connector is None and source.parser is None and source.document_kinds == ["constitution"]
    words = {**PLANNED, **REJECTED}[source_id]
    assert words in (source.licence.reviewed_by or ""), source_id
    with pytest.raises(LicenceNotApproved):
        licence_gate(source)


def test_the_rejected_publishers_permit_no_automated_use(catalog: Catalog) -> None:
    for source_id in REJECTED:
        licence = catalog.sources.get(source_id).licence
        assert licence.automated_processing == "not_allowed" and licence.bulk_download == "not_allowed"


def test_the_first_sweep_records_the_deep_sweep_promoted_are_now_active(catalog: Catalog) -> None:
    for source_id in (
        "be-senate-constitution",
        "lt-seimas-constitution",
        "ro-senat-constitution",
        "xk-gjk-constitution",
    ):
        assert catalog.sources.get(source_id).status == "active"
    # still planned: the publisher of the first sweep that stays blocked
    assert catalog.sources.get("be-dekamer-constitution").status == "planned"


# --------------------------------------------------------------------------- the parser on the real lines


@pytest.mark.parametrize("slug", SLUGS)
def test_every_fixture_parses_as_the_golden_record_says(slug: str) -> None:
    expected = json.loads((FIX / "expectations.json").read_text(encoding="utf-8"))
    assert summary(parse_fixture(slug)) == expected[slug]


def test_fixtures_are_small_and_cover_every_source() -> None:
    assert sum(p.stat().st_size for p in FIX.iterdir()) < 400_000
    manifests = {}
    for source_id in ACTIVE:
        text = (REPO_ROOT / "config" / "manifests" / f"{source_id}.yaml").read_text(encoding="utf-8")
        manifests[source_id] = text
    for source_id in ACTIVE:
        assert any(fixture(s)["source"] == source_id for s in SLUGS), source_id


def test_belgium_reads_art_1er_and_keeps_the_revision_list_as_blocks() -> None:
    for slug, first in (("be-fr", "La Belgique est un État fédéral"), ("be-nl", "België is een federale Staat")):
        doc = parse_fixture(slug)
        assert [p.eid for p in doc.provisions if p.parent_eid is None][:3] == ["art-1", "art-2", "art-3"]
        assert doc.provisions[0].text.startswith(first)
        assert doc.provisions[-1].eid.startswith("blk-")  # the list of the revisions after the text
    assert cite("constitution", "art-7bis", "Belgium Constitution", language="fr") == "Belgium Constitution art. 7bis"


def test_the_preamble_is_stored_where_the_manifest_names_it() -> None:
    for slug, start in (
        ("md-ro", "NOI, reprezentanții"),
        ("lt-lt", "– prieš daugelį amžių"),
        ("az-en", "The people of Azerbaijan"),
        ("ge-en", "We, the citizens of Georgia"),
        ("bg-bg", "Ние, народните представители"),
        ("va-it", "Stato della Città del Vaticano che"),
        ("mc-fr", "Considérant que les institutions"),
        ("ad-ca", "Conscient de la necessitat"),
    ):
        doc = parse_fixture(slug)
        assert doc.provisions[0].eid == "preamble", slug
        text = (doc.provisions[0].heading or "") + " " + doc.provisions[0].text
        assert start in text, slug


def test_a_whole_sentence_marker_is_the_text_of_the_preamble_not_a_heading() -> None:
    doc = parse_fixture("ge-en")
    pre = doc.provisions[0]
    assert pre.heading is None and pre.text.startswith("We, the citizens of Georgia")
    assert "Constitutional Law of Georgia No 1324" in (pre.note or "")  # the amendment note stays a note


def test_text_on_the_heading_line_keeps_its_closing_punctuation() -> None:
    bg = parse_fixture("bg-bg")
    art1 = next(p for p in bg.provisions if p.eid == "art-1")
    assert art1.text.split("\n")[0] == "(1) България е република с парламентарно управление."
    assert "(Изм." not in art1.text  # notes of amendments are not in the first article


def test_a_footnote_mark_after_the_article_number_is_not_part_of_it() -> None:
    mc = parse_fixture("mc-fr")
    assert [p.eid for p in mc.provisions if p.parent_eid is None][:4] == ["preamble", "art-1", "art-2", "art-3"]


def test_blocks_are_cited_as_text_blocks() -> None:
    for slug in ("hu-hu", "ba-sr", "ps-en"):
        doc = parse_fixture(slug)
        assert doc.provisions and all(p.eid.startswith("blk-") for p in doc.provisions), slug
    assert cite("constitution", "blk-2", "Hungary Fundamental Law", country="HU", language="hu") == (
        "Hungary Fundamental Law, text block 2"
    )


def test_lettered_articles_and_other_scripts() -> None:
    ie = parse_fixture("ie-en")
    assert ie.provisions[0].eid == "preamble" and any(p.eid == "art-1" for p in ie.provisions)
    assert cite("constitution", "art-5a", "Malta Constitution", country="MT") == "Malta Constitution art. 5A"
    assert cite("constitution", "art-7ter", "Belgium Constitution", language="fr") == "Belgium Constitution art. 7ter"
    # Ireland keeps its own module for the Acts; it cites the Constitution like the generic module and needs no Act year
    assert cite("ie", "art-28a", "Ireland Constitution", country="IE") == "Ireland Constitution art. 28A"
    assert cite("ie", "preamble", "Ireland Constitution", country="IE") == "Ireland Constitution, preamble"
    assert get_module("ie").validate_ext({}) == {}
    with pytest.raises(ValueError, match="act_year"):
        get_module("ie").validate_ext({"act_number": 3})
    ye = parse_fixture("ye-ar")
    assert ye.provisions[0].eid == "art-1" and ye.provisions[0].text.startswith("الجمهورية اليمنية دولة عربية")


def test_iceland_reads_the_number_and_the_word_gr_as_the_heading() -> None:
    doc = parse_fixture("is-is")
    assert doc.provisions[0].text == "Ísland er lýðveldi með þingbundinni stjórn."  # not "gr.\nÍsland ..."


# --------------------------------------------------------------------------- the real pipeline


class _Documents:
    def __init__(self, source_id: str, slugs: list[str]):
        self.source_id = source_id
        self.slugs = slugs

    def fetch(self) -> Iterator[RawDocument]:
        for slug in self.slugs:
            data = fixture(slug)
            hints = dict(data["hints"])
            if data["media"] == "pdf":
                hints.setdefault("heading", "text")
            content = text_bundle(
                data["lines"],
                cc=data["jurisdiction"],
                language=data["language"],
                structure=hints,
                title=data["title"],
                citation_name=data["citation_name"],
                edition_date="2025-06-20",
            )
            yield RawDocument(
                f"constitution:{slug}", f"https://example.org/{slug}", "application/json", content, RETRIEVED, {}
            )


DB_SOURCES = {
    "be-senate-constitution": ["be-fr", "be-nl"],
    "az-president-constitution": ["az-en"],
    "va-governatorato-constitution": ["va-it"],
    "ge-constitutional-court-constitution": ["ge-en"],
    "xk-gjk-constitution": ["xk-sq"],
    "bg-president-constitution": ["bg-bg"],
}


@pytest.fixture(scope="module")
def eme_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> dict[str, IngestionReport]:
    reports = {}
    with session_factory() as session:
        for source_id, slugs in DB_SOURCES.items():
            reports[source_id] = Ingestor(session, catalog, store).ingest_source(
                source_id, actor="pytest", connector=_Documents(source_id, slugs)
            )
        session.commit()
    return reports


def _search(session: Session, catalog: Catalog, jurisdiction: str, q: str) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction=jurisdiction, q=q), today=TODAY)


@pytest.mark.db
def test_the_fixtures_are_ingested_without_errors(eme_ingested: dict[str, IngestionReport]) -> None:
    for source_id, report in eme_ingested.items():
        assert report.errors == [] and report.conflicts == [], source_id
        assert report.documents_new == len(DB_SOURCES[source_id]), source_id
        assert report.passages_indexed > 5, source_id


@pytest.mark.db
def test_search_finds_a_known_article_with_its_citation_and_the_limitations(
    session: Session, catalog: Catalog, eme_ingested: dict[str, IngestionReport]
) -> None:
    res = _search(
        session, catalog, "BE", "La Belgique est un État fédéral qui se compose des communautés et des régions"
    )
    hit = res["results"][0]
    assert hit["citation"] == "Belgium Constitution art. 1" and hit["jurisdiction"]["id"] == "BE"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"ATTESTED_ONLY_AT", "PROFILE_UNVERIFIED", "NOT_AUTHENTIC_TEXT"} <= codes
    assert "Belgian Senate" in hit["source"]["attribution"]
    nl = _search(
        session, catalog, "BE", "België is een federale Staat, samengesteld uit de gemeenschappen en de gewesten"
    )
    assert nl["results"][0]["citation"] == "Belgium Constitution art. 1"
    az = _search(session, catalog, "AZ", "sole source of state power in the Republic of Azerbaijan")
    assert az["results"][0]["citation"] == "Azerbaijan Constitution art. 1"
    va = _search(
        session, catalog, "VA", "Sommo Pontefice Sovrano dello Stato della Città del Vaticano pienezza della potestà"
    )
    assert va["results"][0]["citation"] == "Vatican City State Fundamental Law art. 1"
    bg = _search(session, catalog, "BG", "България е република с парламентарно управление")
    assert bg["results"][0]["citation"].startswith("Bulgaria Constitution art. 1")
    assert all(h["jurisdiction"]["id"] == "BG" for h in bg["results"])


@pytest.mark.db
def test_the_licence_of_the_explicit_source_reaches_the_result(
    session: Session, catalog: Catalog, eme_ingested: dict[str, IngestionReport]
) -> None:
    bg = _search(session, catalog, "BG", "България е република с парламентарно управление")
    attribution = bg["results"][0]["source"]["attribution"]
    assert "CC BY 2.5" in attribution and "2026-10-16" in attribution  # {retrieved_date} is filled at serve time


@pytest.mark.db
def test_lookup_by_uri_returns_the_exact_text_attested_at_the_edition(
    session: Session, catalog: Catalog, eme_ingested: dict[str, IngestionReport]
) -> None:
    got = provision_at(session, catalog, "/ge/constitution/~art-1", TODAY)
    assert got["provision"]["citation"] == "Georgia Constitution art. 1"
    assert got["version"]["text"].startswith("Georgia is an independent, unified and indivisible state")
    assert got["status_on_date"] == "in_force_date_unverified"
    assert got["version"]["valid_from"] is None or got["version"]["valid_from"] <= TODAY.isoformat()


@pytest.mark.db
def test_reingest_is_idempotent_and_stored_texts_verify(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    eme_ingested: dict[str, IngestionReport],
) -> None:
    from sqlalchemy import select

    from ulis.db import models as m

    with session_factory() as session:
        total = 0
        for source_id, slugs in DB_SOURCES.items():
            again = Ingestor(session, catalog, store).ingest_source(
                source_id, actor="pytest", connector=_Documents(source_id, slugs)
            )
            assert again.documents_unchanged == len(slugs) and again.provision_versions_created == 0, source_id
            assert again.errors == [] and again.conflicts == [], source_id
            total += len(slugs)
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id.in_(list(DB_SOURCES)))).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == total and all(r["result"] == "pass" for r in results)
        session.rollback()
