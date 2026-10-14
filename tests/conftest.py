from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.cli import migrate
from ulis.db import make_engine, make_session_factory
from ulis.ingestion.catalog_sync import sync_catalog
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.settings import REPO_ROOT
from ulis.storage.object_store import LocalObjectStore

SAMPLE_SOURCE = "ulis-fictional-sample"


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    return Catalog.load(REPO_ROOT / "config")


@pytest.fixture(scope="session")
def db_url() -> str:
    url = os.environ.get("ULIS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("ULIS_TEST_DATABASE_URL not set; skipping PostgreSQL integration tests")
    return url


@pytest.fixture(scope="session")
def engine(db_url: str) -> Iterator[Engine]:
    eng = make_engine(db_url)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    migrate(db_url)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


@pytest.fixture(scope="session")
def store_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("objects")


@pytest.fixture(scope="session")
def store(store_dir: Path) -> LocalObjectStore:
    return LocalObjectStore(store_dir)


@pytest.fixture(scope="session")
def ingested(session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore) -> IngestionReport:
    with session_factory() as session:
        sync_catalog(session, catalog, actor="pytest")
        report = Ingestor(session, catalog, store).ingest_source(SAMPLE_SOURCE, actor="pytest")
        session.commit()
    return report


@pytest.fixture
def session(session_factory: sessionmaker[Session], ingested: IngestionReport) -> Iterator[Session]:
    """A session whose changes are rolled back after each test."""
    s = session_factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


US_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "uslm"


@pytest.fixture(scope="session")
def us_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    """Real US federal documents (GPO USLM fixtures), ingested through the licence-gated pipeline."""
    from ulis.sources.connectors.local_manifest import LocalManifestConnector

    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "us-gpo-uslm-samples",
            actor="pytest",
            connector=LocalManifestConnector(US_FIXTURES / "MANIFEST.yaml", "us-gpo-uslm-samples"),
        )
        session.commit()
    return report
