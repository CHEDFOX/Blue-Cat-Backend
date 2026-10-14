"""Justice Laws XML parser and Canadian search behaviour (real federal Acts, OGL - Canada)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.parsers.justice_canada import parse_justice_canada, resolve_uri
from ulis.storage.object_store import LocalObjectStore

FIX = REPO_ROOT / "tests" / "fixtures" / "justice_canada"
EN = FIX / "eng" / "acts" / "A-1.xml"
FR = FIX / "fra" / "lois" / "A-1.xml"


def _doc(path):  # type: ignore[no-untyped-def]
    docs = parse_justice_canada(path.read_bytes())
    assert len(docs) == 1
    return docs[0]


def _cite(doc, eid: str) -> str:  # type: ignore[no-untyped-def]
    p = {x.eid: x for x in doc.provisions}[eid]
    return get_module("ca").provision_citation(
        ProvisionCitationInput(doc.title, "act", "CA", p.top_kind, p.num_path, doc.language, ext=doc.ext, eid=p.eid)
    )


def test_english_consolidation() -> None:
    doc = _doc(EN)
    assert (doc.jurisdiction_id, doc.work_uri, doc.language) == ("CA", "/ca/act/a-1", "en")
    assert doc.text_origin == "official_consolidation" and doc.authoritative
    assert doc.ext["statute_series"] == "RSC"  # from the historical notes, not assumed
    p = {x.eid: x for x in doc.provisions}
    assert p["section-4-1-a"].text == "a Canadian citizen, or"
    # The subsection's own words were amended in 2019; its paragraph (a) was not.
    assert p["section-4-1"].valid_from == date(2019, 6, 21) and p["section-4-1-a"].valid_from == date(2007, 9, 1)
    assert p["section-16.31"].status == "repealed" and p["section-16.31"].text == ""
    assert "Repealed" in (p["section-16.31"].note or "")
    assert "section-3-def-business-day-a" in p
    assert not any(e.startswith("schedule-related") for e in p)  # other Acts' provisions are not indexed
    assert doc.outstanding_changes and all(c["affects_eid"] is None for c in doc.outstanding_changes)
    assert _cite(doc, "section-4-1-a") == "Access to Information Act, RSC 1985, c A-1, s 4(1)(a)"
    assert (
        _cite(doc, "section-3-def-business-day-a")
        == "Access to Information Act, RSC 1985, c A-1, s 3 “business day”(a)"
    )
    assert _cite(doc, "schedule-i") == "Access to Information Act, RSC 1985, c A-1, Sch I"


def test_french_version_shares_identifiers() -> None:
    en, fr = _doc(EN), _doc(FR)
    assert fr.language == "fr" and fr.work_uri == en.work_uri and fr.expression_uri.endswith("/fra@2026-06-14")
    shared = {p.eid for p in en.provisions} & {p.eid for p in fr.provisions}
    assert {"section-4-1-a", "section-3-def-business-day", "schedule-ii"} <= shared
    assert _cite(fr, "section-4-1-a") == "Loi sur l’accès à l’information, LRC 1985, c A-1, art 4(1)a)"
    assert _cite(fr, "section-3-def-business-day-a").endswith("art 3 « jour ouvrable » a)")


def test_resolve_uri() -> None:
    assert resolve_uri("https://laws-lois.justice.gc.ca/eng/acts/I-2.5/") == ("/ca/act/i-2.5", None)
    assert resolve_uri("https://example.org/eng/acts/I-2.5/") is None


# --------------------------------------------------------------------------- PostgreSQL


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        now = datetime(2026, 10, 14, tzinfo=UTC)
        for lang, path in (("eng", EN), ("fra", FR)):
            yield RawDocument(
                f"justice-canada:{lang}:A-1", f"file://{path.name}", "application/xml", path.read_bytes(), now, {}
            )


@pytest.fixture(scope="module")
def ca_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "ca-justice-laws-xml", actor="pytest", connector=_Fixtures()
        )
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="CA", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_bilingual_search_and_limitations(session: Session, catalog: Catalog, ca_ingested: IngestionReport) -> None:
    assert ca_ingested.errors == [] and ca_ingested.conflicts == []
    res = _search(
        session, catalog, q="right to be given access to any record", as_of=date(2026, 7, 1), query_language="en"
    )
    hit = next(h for h in res["results"] if h["citation"].endswith("s 4(1)"))
    assert hit["citation"] == "Access to Information Act, RSC 1985, c A-1, s 4(1)"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"OFFICIAL_CONSOLIDATION_REPRODUCED", "PROFILE_UNVERIFIED", "EQUALLY_AUTHENTIC_VERSIONS"} <= codes
    assert hit["source"]["attribution"].startswith("Contains information licensed under the Open Government Licence")
    fr = _search(session, catalog, q="droit d’accès aux documents", as_of=date(2026, 7, 1), query_language="fr")
    first = fr["results"][0]
    assert first["citation"] == "Loi sur l’accès à l’information, LRC 1985, c A-1, art 4(1)"
    assert first["version"]["language"] == "fr"


@pytest.mark.db
def test_earlier_wording_is_a_history_gap_not_absence(
    session: Session, catalog: Catalog, ca_ingested: IngestionReport
) -> None:
    now = provision_at(session, catalog, "/ca/act/a-1/~section-4-1", date(2020, 1, 1), language="en")
    assert now["status_on_date"] == "in_force"
    before = provision_at(session, catalog, "/ca/act/a-1/~section-4-1", date(2010, 1, 1), language="en")
    # The 2019 wording replaced an earlier one that this consolidation does not contain.
    assert before["status_on_date"] == "history_not_indexed"
