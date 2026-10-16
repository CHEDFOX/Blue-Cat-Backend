"""East and Southern Africa constitutions through the real pipeline: fixtures, the committed source descriptors."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker
from test_constitution_east_south import (
    _json,
    bundle_for,
    columns_pdf,
    fixture_pdf,
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


def _rwanda() -> list[tuple[str, bytes]]:
    cols = _json("rw-columns.json")
    sheets = [
        ((842, 595), [(90, cols["rw"][i : i + 40]), (316, cols["en"][i : i + 40]), (536, cols["fr"][i : i + 40])])
        for i in range(0, max(len(v) for v in cols.values()), 40)
    ]
    pdf = columns_pdf([((595, 842), [(40, ["Official Gazette n° Special of 04/08/2023"])]), *sheets])
    return [(slug, bundle_for("rw-minijust-constitution", slug, pdf)) for slug in ("rw-rw", "rw-en", "rw-fr")]


SOURCES = {
    "rw-minijust-constitution": _rwanda(),
    "so-senate-constitution": [("so-so", bundle_for("so-senate-constitution", "so-so", fixture_pdf("so-pages.json")))],
    "mg-assemblee-constitution": [
        ("mg-mg", bundle_for("mg-assemblee-constitution", "mg-mg", fixture_pdf("mg-mg-pages.json"), drop=("first_page",))),
        (
            "mg-fr",
            bundle_for(
                "mg-assemblee-constitution", "mg-fr", fixture_pdf("mg-fr-pages.json"), drop=("last_page", "tail_regex")
            ),
        ),
    ],
    "mu-assembly-constitution": [("mu-en", bundle_for("mu-assembly-constitution", "mu-en", fixture_pdf("mu-pages.json")))],
    "ao-tribunal-constitucional-constitution": [
        ("ao-pt", bundle_for("ao-tribunal-constitucional-constitution", "ao-pt", fixture_pdf("ao-pages.json")))
    ],
    "sc-ecs-constitution": [
        ("sc-en", bundle_for("sc-ecs-constitution", "sc-en", fixture_pdf("sc-pages.json"), drop=("last_page",)))
    ],
    "ls-gov-constitution": [("ls-en", bundle_for("ls-gov-constitution", "ls-en", fixture_pdf("ls-pages.json")))],
    "zw-youth-constitution": [("zw-en", bundle_for("zw-youth-constitution", "zw-en", fixture_pdf("zw-pages.json")))],
}


@pytest.fixture(scope="module")
def east_south_ingested(
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
def test_every_constitution_is_ingested_without_errors(east_south_ingested: dict[str, IngestionReport]) -> None:
    for source_id, report in east_south_ingested.items():
        assert report.errors == [] and report.conflicts == [], source_id
        assert report.documents_new == len(SOURCES[source_id]) and report.instruments_created == 1, source_id
        assert report.passages_indexed >= 8, source_id


@pytest.mark.db
def test_search_finds_the_article_with_its_citation_in_each_language(
    session: Session, catalog: Catalog, east_south_ingested: dict[str, IngestionReport]
) -> None:
    en = _search(session, catalog, "RW", "Suffrage is universal and equal for all Rwandans")
    assert en["results"][0]["citation"].startswith("Rwanda Constitution art. 2")
    rw = _search(session, catalog, "RW", "Ubutegetsi bwose bukomoka ku Banyarwanda")
    assert rw["results"][0]["citation"].startswith("Rwanda Constitution art. 1")
    assert rw["results"][0]["jurisdiction"]["id"] == "RW"
    so = _search(session, catalog, "SO", "Soomaaliya waa Jamhuuriyad federaal ah")
    hit = so["results"][0]
    assert hit["citation"].startswith("Somalia Constitution art. 1")
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"ATTESTED_ONLY_AT", "PROFILE_UNVERIFIED", "NOT_AUTHENTIC_TEXT"} <= codes
    assert "Constitution of Somalia" in hit["source"]["attribution"]
    mu = _search(session, catalog, "MU", "supreme law of Mauritius")
    assert mu["results"][0]["citation"].startswith("Mauritius Constitution s. 2")
    ao = _search(session, catalog, "AO", "Estado Democrático de Direito")
    assert ao["results"][0]["citation"].startswith("Angola Constitution art. 2")
    ls = _search(session, catalog, "LS", "Lesotho shall be a sovereign democratic kingdom")
    assert ls["results"][0]["citation"].startswith("Lesotho Constitution s. 1")
    zw = _search(session, catalog, "ZW", "This Constitution is the supreme law of Zimbabwe")
    assert zw["results"][0]["citation"].startswith("Zimbabwe Constitution s. 2")
    mg = _search(session, catalog, "MG", "Le peuple Malagasy constitue une Nation")
    assert mg["results"][0]["citation"].startswith("Madagascar Constitution art. 1")


@pytest.mark.db
def test_the_edition_date_is_the_only_date_the_text_is_attested_at(
    session: Session, catalog: Catalog, east_south_ingested: dict[str, IngestionReport]
) -> None:
    got = provision_at(session, catalog, "/sc/constitution/~art-1", TODAY)
    assert got["provision"]["citation"] == "Seychelles Constitution art. 1"
    assert got["version"]["text"].startswith("Seychelles is a sovereign democratic Republic")
    assert got["status_on_date"] == "in_force_date_unverified"
    ls = provision_at(session, catalog, "/ls/constitution/~sec-2", TODAY)
    assert ls["status_on_date"] == "in_force_date_unverified"  # attested at 2 April 1993 only
    attested = next(lim for lim in ls["limitations"] if lim["code"] == "ATTESTED_ONLY_AT")
    assert "1993-04-02" in attested["message"]
    ao = provision_at(session, catalog, "/ao/constitution/~art-1", TODAY)
    assert ao["version"]["text"].startswith("Angola é uma República soberana e independente")


@pytest.mark.db
def test_reingest_is_idempotent_and_stored_texts_verify(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    east_south_ingested: dict[str, IngestionReport],
) -> None:
    from sqlalchemy import select

    from ulis.db import models as m

    with session_factory() as session:
        total = 0
        for source_id, documents in SOURCES.items():
            again = Ingestor(session, catalog, store).ingest_source(
                source_id, actor="pytest", connector=_Documents(source_id, documents)
            )
            assert again.documents_unchanged == len(documents) and again.provision_versions_created == 0, source_id
            assert again.errors == [] and again.conflicts == [], source_id
            total += len(documents)
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id.in_(list(SOURCES)))).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == total and all(r["result"] == "pass" for r in results)
        session.rollback()
