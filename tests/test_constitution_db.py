"""Constitutions stored through the real pipeline: fixtures of real pages and PDFs, the real source descriptors."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from constitution_pdf import make_pdf
from sqlalchemy.orm import Session, sessionmaker
from test_constitution_text import (
    CN_HINTS,
    KE_HINTS,
    LK_HINTS,
    ZA_HINTS,
    _gz,
    _pages,
    bundle,
)

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.sources.connectors.base import RawDocument
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

RETRIEVED = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
TODAY = date(2026, 10, 15)


class _Documents:
    """The bundles the constitution connector would yield, built from the fixtures."""

    def __init__(self, source_id: str, documents: list[tuple[str, bytes]]):
        self.source_id = source_id
        self.documents = documents

    def fetch(self) -> Iterator[RawDocument]:
        for slug, content in self.documents:
            yield RawDocument(
                f"constitution:{slug}", f"https://example.org/{slug}", "application/json", content, RETRIEVED, {}
            )


SOURCES = {
    "cn-govcn-constitution": [
        (
            "cn-zh",
            bundle(
                _gz("cn-gov-cn.html.gz"),
                "html",
                cc="CN",
                language="zh",
                structure=CN_HINTS,
                title="中华人民共和国宪法",
                citation_name="中华人民共和国宪法",
                adopted_on="1982-12-04",
                edition_date="2018-03-22",
            ),
        )
    ],
    "za-doj-constitution": [
        (
            "za-en",
            bundle(
                make_pdf(_pages("za-en-pages.json")),
                "pdf",
                cc="ZA",
                structure=ZA_HINTS,
                title="Constitution of the Republic of South Africa, 1996",
                citation_name="South Africa Constitution",
                edition_date="2026-05-25",
            ),
        )
    ],
    "lk-parliament-constitution": [
        (
            "lk-en",
            bundle(
                make_pdf(_pages("lk-en-pages.json")),
                "pdf",
                cc="LK",
                structure=LK_HINTS,
                title="The Constitution of the Democratic Socialist Republic of Sri Lanka",
                citation_name="Sri Lanka Constitution",
            ),
        )
    ],
    "ke-parliament-constitution": [
        (
            "ke-en",
            bundle(
                make_pdf(_pages("ke-en-pages.json")),
                "pdf",
                cc="KE",
                structure=KE_HINTS,
                title="The Constitution of Kenya, 2010",
                citation_name="Kenya Constitution",
            ),
        )
    ],
}


@pytest.fixture(scope="module")
def constitutions_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> dict[str, IngestionReport]:
    reports = {}
    with session_factory() as session:
        for source_id, documents in SOURCES.items():
            reports[source_id] = Ingestor(session, catalog, store).ingest_source(
                source_id, actor="pytest", connector=_Documents(source_id, documents)
            )
        session.commit()
    return reports


def _search(session: Session, catalog: Catalog, jurisdiction: str, q: str) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction=jurisdiction, q=q), today=TODAY)


@pytest.mark.db
def test_every_constitution_is_ingested_without_errors(constitutions_ingested: dict[str, IngestionReport]) -> None:
    for source_id, report in constitutions_ingested.items():
        assert report.errors == [] and report.conflicts == [], source_id
        assert report.documents_new == 1 and report.instruments_created == 1, source_id
        assert report.passages_indexed > 10, source_id


@pytest.mark.db
def test_search_finds_an_article_with_its_citation_and_the_limitations(
    session: Session, catalog: Catalog, constitutions_ingested: dict[str, IngestionReport]
) -> None:
    res = _search(session, catalog, "ZA", "Republic of South Africa is one, sovereign, democratic state")
    hit = res["results"][0]
    assert hit["citation"].startswith("South Africa Constitution s. 1")
    assert hit["jurisdiction"]["id"] == "ZA" and all(h["jurisdiction"]["id"] == "ZA" for h in res["results"])
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"ATTESTED_ONLY_AT", "PROFILE_UNVERIFIED", "NOT_AUTHENTIC_TEXT"} <= codes
    assert "Constitution of the Republic of South Africa" in hit["source"]["attribution"]
    cn = _search(session, catalog, "CN", "中华人民共和国公民在法律面前一律平等")
    assert cn["results"][0]["citation"] == "中华人民共和国宪法第33条第2款" or cn["results"][0]["citation"].startswith(
        "中华人民共和国宪法第33条"
    )
    ke = _search(session, catalog, "KE", "Legislation to establish the ethics and anti-corruption commission")
    assert any(h["citation"].startswith("Kenya Constitution art. 79") for h in ke["results"])


@pytest.mark.db
def test_lookup_by_uri_returns_the_exact_text_attested_at_the_edition(
    session: Session, catalog: Catalog, constitutions_ingested: dict[str, IngestionReport]
) -> None:
    got = provision_at(session, catalog, "/cn/constitution/~art-1", TODAY)
    assert got["provision"]["citation"] == "中华人民共和国宪法第1条"
    assert got["version"]["text"].startswith("中华人民共和国是工人阶级领导的")
    assert got["status_on_date"] == "in_force_date_unverified"


@pytest.mark.db
def test_reingest_is_idempotent_and_stored_texts_verify(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    constitutions_ingested: dict[str, IngestionReport],
) -> None:
    from sqlalchemy import select

    from ulis.db import models as m

    with session_factory() as session:
        for source_id, documents in SOURCES.items():
            again = Ingestor(session, catalog, store).ingest_source(
                source_id, actor="pytest", connector=_Documents(source_id, documents)
            )
            assert again.documents_unchanged == 1 and again.provision_versions_created == 0, source_id
            assert again.errors == [] and again.conflicts == [], source_id
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id.in_(list(SOURCES)))).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == len(SOURCES) and all(r["result"] == "pass" for r in results)
        session.rollback()
