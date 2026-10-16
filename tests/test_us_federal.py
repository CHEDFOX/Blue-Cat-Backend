"""Real US federal documents (GPO USLM fixtures) through ingestion, search and verification (PostgreSQL)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

pytestmark = pytest.mark.db


@pytest.fixture
def us(session: Session, us_ingested: IngestionReport) -> Session:
    return session


def run(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="US", **kw), today=date(2026, 10, 14))


def codes(item: dict[str, Any]) -> set[str]:
    return {lim["code"] for lim in item["limitations"]}


def test_ingestion_of_real_documents(us: Session, us_ingested: IngestionReport) -> None:
    assert us_ingested.errors == [] and us_ingested.conflicts == []
    assert us_ingested.documents_new == 8
    rows = us.execute(
        select(m.Instrument.instrument_type, func.count())
        .where(m.Instrument.jurisdiction_id == "US")
        .group_by(m.Instrument.instrument_type)
    )
    by_type = {kind: count for kind, count in rows}
    # 3 public laws + 1 compilation (of a fourth Act); 5 CFR parts; 1 joint resolution; 1 simple resolution
    assert by_type == {"act": 4, "regulation": 5, "bill": 1, "resolution": 1}
    assert us_ingested.citation_links_resolved > 0


def test_public_law_citation_and_presumed_effective_date(us: Session, catalog: Catalog) -> None:
    hit = next(
        h
        for h in run(us, catalog, q="women STEM disciplines", as_of=date(2018, 1, 1))["results"]
        if h["document"]["uri"] == "/us/pl/115/6"
    )
    assert hit["citation"].startswith("Promoting Women in Entrepreneurship Act, Pub. L. No. 115-6, § 2")
    assert hit["citation"].endswith("131 Stat. 11 (2017)")
    assert (
        hit["authority"]["category"] == "applicability_unverified"
    )  # public law as enacted, no later version recorded: whether it still applies is unknown
    assert {"EFFECTIVE_DATE_PRESUMED", "NOT_AUTHENTICATED", "PROFILE_UNVERIFIED"} <= codes(hit)
    before = run(us, catalog, q="women STEM disciplines", as_of=date(2016, 1, 1))
    assert all(h["document"]["uri"] != "/us/pl/115/6" for h in before["results"])


def test_regulation_attested_only_at_edition_date(us: Session, catalog: Catalog) -> None:
    res = run(us, catalog, q="licensed importer records of firearms", as_of=date(2017, 6, 1), limit=5)
    hit = next(h for h in res["results"] if h["document"]["type"] == "regulation")
    assert hit["citation"].startswith("27 C.F.R. § 478.") and hit["citation"].endswith("(2017)")
    assert hit["authority"]["category"] == "applicability_unverified"
    assert {"ATTESTED_ONLY_AT", "AS_OF_AFTER_CURRENCY"} <= codes(hit)
    assert hit["version"]["currency_date"] == "2017-04-01"


def test_bill_stages_are_proposed_and_successive(us: Session, catalog: Catalog) -> None:
    uri = "/us/bill/116/hjres/37/~sec_1"
    reported = provision_at(us, catalog, uri, date(2019, 2, 1))
    later = provision_at(us, catalog, uri, date(2019, 3, 1))
    assert reported["status_on_date"] == later["status_on_date"] == "proposed"
    assert [t["valid_from"] for t in reported["timeline"]] == ["2019-01-30", "2019-02-13"]
    assert reported["timeline"][0]["valid_to"] == "2019-02-13"
    assert "PROPOSED_NOT_LAW" in codes(reported) and "LATER_STAGE_EXISTS" in codes(reported)
    assert "VERSION_DATE_APPROXIMATE" in codes(later)
    assert reported["version"]["authority"]["category"] == "proposed"
    hidden = run(us, catalog, q="war powers hostilities", as_of=date(2019, 3, 1))
    assert all(h["document"]["type"] not in ("bill", "resolution") for h in hidden["results"])


def test_simple_resolution_is_not_law(us: Session, catalog: Catalog) -> None:
    detail = provision_at(us, catalog, "/us/bill/116/hres/1000/~sec_unnumbered_1", date(2021, 1, 1))
    assert detail["version"]["authority"]["category"] == "non_binding_resolution"
    assert "RESOLUTION_NOT_LAW" in codes(detail)


def test_compilation_is_flagged_unofficial(us: Session, catalog: Catalog) -> None:
    hit = next(
        h
        for h in run(us, catalog, q="anadromous fish", as_of=date(2020, 1, 1), limit=10)["results"]
        if h["document"]["uri"] == "/us/pl/89/304"
    )
    assert {"COMPILATION_NOT_OFFICIAL", "ATTESTED_ONLY_AT"} <= codes(hit)
    assert hit["version"]["consolidation"] == "compilation"


def test_long_quoted_section_does_not_dominate_ranking(us: Session, catalog: Catalog) -> None:
    res = run(us, catalog, q="anadromous fish", as_of=date(2020, 1, 1), limit=3)
    assert res["results"][0]["document"]["uri"] == "/us/pl/89/304"


def test_cfr_cross_references_resolve_to_provisions(us: Session) -> None:
    resolved = us.scalar(
        select(func.count())
        .select_from(m.CitationLink)
        .where(m.CitationLink.to_uri.like("/us/cfr/t27/s478.%"), m.CitationLink.to_provision_id.is_not(None))
    )
    unresolved_usc = us.scalar(
        select(func.count())
        .select_from(m.CitationLink)
        .where(m.CitationLink.to_uri.like("/us/usc/%"), m.CitationLink.to_instrument_id.is_(None))
    )
    assert resolved > 0
    assert unresolved_usc > 0  # US Code not ingested: kept as unresolved targets, never guessed


def test_us_source_documents_pass_integrity(us: Session, catalog: Catalog, store: LocalObjectStore) -> None:
    ids = us.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == "us-gpo-uslm-samples")).all()
    assert len(ids) == 8
    for doc_id in ids:
        result = verify_source_document(us, catalog, store, doc_id)
        assert result["result"] == "pass", result


def test_query_language_prevents_false_friend_expansion(us: Session, catalog: Catalog) -> None:
    # French "conservation" means retention; English "conservation" does not.
    any_language = run(us, catalog, q="anadromous fish conservation", as_of=date(2020, 1, 1))
    english = run(us, catalog, q="anadromous fish conservation", as_of=date(2020, 1, 1), query_language="en")
    assert "data-retention-limit" in {e["concept"] for e in any_language["query_expansions"]}
    assert english["query_expansions"] == []
    assert all(h["score"]["matched_via"] == "query" for h in english["results"])
