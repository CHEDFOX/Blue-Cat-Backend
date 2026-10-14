"""Jurisdiction-aware search, exact citations and legal-status limitations (PostgreSQL)."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import pytest
from sqlalchemy.orm import Session

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.retrieval.search import SearchError, SearchRequest, search
from ulis.sources.parsers.akn import parse_akn
from ulis.storage.object_store import LocalObjectStore

pytestmark = pytest.mark.db
TODAY = date(2026, 10, 14)


def run(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(**kw), today=TODAY)


def codes(hit_or_result: dict[str, Any]) -> set[str]:
    return {lim["code"] for lim in hit_or_result["limitations"]}


def by_citation(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {h["citation"]: h for h in result["results"]}


S12_1 = "Personal Data Act 2015 (XA) s 12(1)"


@pytest.mark.parametrize(
    ("as_of", "expected", "valid_to"),
    [
        (date(2020, 6, 1), "seven years", "2022-01-01"),
        (date(2021, 12, 31), "seven years", "2022-01-01"),
        (date(2022, 1, 1), "five years", None),
        (date(2024, 1, 1), "five years", None),
    ],
)
def test_returns_the_version_in_force_on_the_date(
    session: Session, catalog: Catalog, as_of: date, expected: str, valid_to: str | None
) -> None:
    hit = by_citation(run(session, catalog, q="retain personal data", jurisdiction="XA", as_of=as_of))[S12_1]
    assert expected in hit["text"]
    assert hit["version"]["valid_to"] == valid_to
    assert hit["authority"]["category"] == "binding_legislation"
    if valid_to:
        assert "LATER_VERSION_EXISTS" in codes(hit)


def test_jurisdiction_hierarchy_and_territorial_extent(session: Session, catalog: Catalog) -> None:
    provincial = "Health Records Act 2019 (XA-NP) s 6(1)"
    north = by_citation(run(session, catalog, q="keep health record", jurisdiction="XA-NP", as_of=TODAY))
    south = by_citation(run(session, catalog, q="keep health record", jurisdiction="XA-SP", as_of=TODAY))
    federal = run(session, catalog, q="keep health record", jurisdiction="XA", as_of=TODAY)
    assert provincial in north
    assert provincial not in south
    assert provincial not in by_citation(federal)
    assert "SUBNATIONAL_LEGISLATION_EXCLUDED" in codes(federal)


def test_foreign_law_excluded_unless_requested_and_never_binding(session: Session, catalog: Catalog) -> None:
    home = run(session, catalog, q="données conservées", jurisdiction="XA", as_of=TODAY, limit=20)
    assert not [h for h in home["results"] if h["jurisdiction"]["id"] == "XB"]
    # Provincial case law belongs to the same national court system; provincial legislation does not apply.
    assert all(h["kind"] == "judgment_paragraph" for h in home["results"] if h["jurisdiction"]["id"] == "XA-NP")
    wider = run(
        session, catalog, q="données conservées", jurisdiction="XA", as_of=TODAY, include_foreign=True, limit=20
    )
    foreign = [h for h in wider["results"] if h["jurisdiction"]["id"] == "XB"]
    assert foreign and all(h["authority"]["category"] == "foreign" for h in foreign)
    assert "FOREIGN_MATERIAL_INCLUDED" in codes(wider)


def test_bills_are_not_law(session: Session, catalog: Catalog) -> None:
    q = "Personal Data Act 2015 three years"
    default = run(session, catalog, q=q, jurisdiction="XA", as_of=TODAY, limit=20)
    assert all(not h["document"].get("is_proposed") for h in default["results"])
    included = run(session, catalog, q=q, jurisdiction="XA", as_of=TODAY, include_proposed=True, limit=20)
    bill = next(h for h in included["results"] if h["document"]["type"] == "bill")
    assert bill["authority"]["category"] == "proposed"
    assert "PROPOSED_NOT_LAW" in codes(bill)
    before_introduction = run(
        session, catalog, q=q, jurisdiction="XA", as_of=date(2026, 1, 1), include_proposed=True, limit=20
    )
    assert all(h["document"]["type"] != "bill" for h in before_introduction["results"])


def test_prospective_provisions_are_flagged(session: Session, catalog: Catalog) -> None:
    q = "backup copy isolated"
    assert not any(
        "12A" in h["citation"] for h in run(session, catalog, q=q, jurisdiction="XA", as_of=TODAY)["results"]
    )
    res = run(session, catalog, q=q, jurisdiction="XA", as_of=TODAY, include_prospective=True)
    hit = next(h for h in res["results"] if "s 12A" in h["citation"])
    assert hit["authority"]["category"] == "prospective_legislation"
    assert "NOT_IN_FORCE" in codes(hit)


def test_cross_lingual_concept_expansion_and_parallel_versions(session: Session, catalog: Catalog) -> None:
    res = run(session, catalog, q="erasure", jurisdiction="XB", as_of=TODAY)
    assert "QUERY_EXPANDED" in codes(res)
    hit = next(h for h in res["results"] if h["unit"]["eid"] == "art_28__para_3")
    assert hit["score"]["matched_via"] == "concept_expansion"
    assert hit["version"]["language"] in ("fr", "de")
    assert {p["language"] for p in hit["parallel_versions"]} == {"fr", "de"} - {hit["version"]["language"]}
    assert "EQUALLY_AUTHENTIC_VERSIONS" in codes(hit)


def test_french_query_cites_french_text(session: Session, catalog: Catalog) -> None:
    hit = by_citation(run(session, catalog, q="données personnelles conservées", jurisdiction="XB", as_of=TODAY))[
        "art. 28 al. 2 CC-XB"
    ]
    assert hit["version"]["language"] == "fr" and hit["score"]["matched_via"] == "query"
    german = hit["parallel_versions"][0]
    assert german["language"] == "de" and german["citation"] == "Art. 28 Abs. 2 ZGB-XB"


def test_treaty_domestic_effect_follows_profile(session: Session, catalog: Catalog) -> None:
    xa = next(
        h
        for h in run(session, catalog, q="kept no longer than necessary", jurisdiction="XA", as_of=TODAY)["results"]
        if h["document"]["type"] == "treaty"
    )
    assert xa["authority"]["category"] == "treaty_obligation"
    assert "dualist" in xa["authority"]["rationale"] and "Personal Data Act 2015" in xa["authority"]["rationale"]
    xb = next(
        h
        for h in run(session, catalog, q="conservées plus longtemps que nécessaire", jurisdiction="XB", as_of=TODAY)[
            "results"
        ]
        if h["document"]["type"] == "treaty"
    )
    assert "monist" in xb["authority"]["rationale"]
    early = run(session, catalog, q="kept no longer than necessary", jurisdiction="XA", as_of=date(2011, 6, 1))
    assert all(h["document"]["type"] != "treaty" for h in early["results"])


def test_precedent_and_negative_treatment(session: Session, catalog: Catalog) -> None:
    res = run(session, catalog, q="backup tapes retained", jurisdiction="XA-NP", as_of=TODAY, limit=20)
    calder = next(h for h in res["results"] if "XANPHC 15" in h["citation"])
    assert calder["authority"]["category"] == "overruled_precedent"
    assert "NEGATIVE_TREATMENT" in codes(calder)
    with_forum = run(
        session, catalog, q="ordinary meaning of retain", jurisdiction="XA-NP", as_of=TODAY, forum_court="XA-NP-DC"
    )
    minerva = next(h for h in with_forum["results"] if "XASC 7" in h["citation"])
    assert minerva["authority"]["category"] == "binding_precedent"


def test_judicial_treatment_and_outstanding_changes_on_statute(session: Session, catalog: Catalog) -> None:
    hits = by_citation(run(session, catalog, q="erase personal data thirty days", jurisdiction="XA", as_of=TODAY))
    s13_2 = hits["Personal Data Act 2015 (XA) s 13(2)"]
    assert "OUTSTANDING_CHANGES" in codes(s13_2)
    assert "AS_OF_AFTER_CURRENCY" in codes(s13_2)  # 2026-10-14 is after the 2026-09-30 currency date
    s12 = by_citation(run(session, catalog, q="retain personal data", jurisdiction="XA", as_of=TODAY))[S12_1]
    assert {"JUDICIAL_TREATMENT", "TREATMENT_OF_OTHER_VERSION", "PENDING_PROPOSAL", "CITATOR_COVERAGE"} <= codes(s12)
    assert any(t["relation"] == "interprets" and "XASC 7" in t["citing_citation"] for t in s12["treatment"]["judicial"])


def test_unindexed_jurisdiction_reports_no_coverage(session: Session, catalog: Catalog) -> None:
    res = run(session, catalog, q="retention of personal data", jurisdiction="IN")
    assert res["results"] == []
    assert {"NO_CORPUS", "PROFILE_UNVERIFIED"} <= codes(res)
    assert res["coverage"][0]["instruments"] == 0


def test_missing_jurisdiction_asks_for_clarification(session: Session, catalog: Catalog) -> None:
    res = run(session, catalog, q="privacy")
    assert res["clarifications"][0]["field"] == "jurisdiction"
    assert "JURISDICTION_NOT_SPECIFIED" in codes(res) and "DATE_DEFAULTED" in codes(res)
    assert len({h["jurisdiction"]["id"] for h in res["results"]}) > 1


def test_every_hit_is_an_exact_source_citation(session: Session, catalog: Catalog, store: LocalObjectStore) -> None:
    res = run(
        session,
        catalog,
        q="personal data",
        jurisdiction="XA-NP",
        as_of=TODAY,
        limit=25,
        include_proposed=True,
        include_prospective=True,
    )
    assert len(res["results"]) >= 10
    for hit in res["results"]:
        assert hit["citation"] and hit["text"]
        assert hashlib.sha256(hit["text"].encode()).hexdigest() == hit["text_sha256"]
        assert "FICTIONAL_DATA" in codes(hit)
        doc = session.get(m.SourceDocument, hit["source"]["document_id"])
        assert doc is not None and doc.sha256 == hit["source"]["sha256"]
        parsed = parse_akn(store.get(doc.storage_key))
        units = {p.eid: p.text for p in parsed.provisions} | {p.eid: p.text for p in parsed.paragraphs}
        assert units[hit["source"]["locator"]] == hit["text"], hit["citation"]


def test_input_validation(session: Session, catalog: Catalog) -> None:
    for kw in (
        {"q": ""},
        {"q": "x", "jurisdiction": "ZZ"},
        {"q": "x", "limit": 0},
        {"q": "x", "jurisdiction": "XA", "forum_court": "XB-CC"},
        {"q": "x", "languages": ["english!"]},
    ):
        with pytest.raises(SearchError):
            run(session, catalog, **kw)
