"""Ingestion pipeline against PostgreSQL."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.catalog_sync import ProfileVersionConflict, sync_catalog
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.registry import LicenceNotApproved
from ulis.storage.object_store import LocalObjectStore

pytestmark = pytest.mark.db


def test_sample_corpus_ingested(ingested: IngestionReport) -> None:
    assert ingested.errors == [] and ingested.conflicts == []
    assert ingested.documents_new == 14
    assert ingested.instruments_created == 7
    assert ingested.instrument_versions_created == 11
    assert ingested.judgments_created == 3
    assert ingested.paragraphs_created == 16
    assert ingested.citation_links_created == ingested.citation_links_resolved == 14
    # Unchanged provisions repeated across consolidations are corroborated, not duplicated.
    assert ingested.provision_versions_corroborated > 0
    assert ingested.passages_indexed > 0


def test_reingestion_is_idempotent(session: Session, catalog: Catalog, store: LocalObjectStore) -> None:
    before = session.scalar(select(func.count()).select_from(m.ProvisionVersion))
    report = Ingestor(session, catalog, store).ingest_source("ulis-fictional-sample", actor="pytest")
    assert (report.documents_new, report.documents_unchanged) == (0, 14)
    assert session.scalar(select(func.count()).select_from(m.ProvisionVersion)) == before


def test_derived_validity_periods(session: Session) -> None:
    rows = session.execute(
        select(
            m.ProvisionVersion.status,
            m.ProvisionVersion.valid_from,
            m.ProvisionVersion.valid_to,
            m.ProvisionVersion.valid_to_basis,
        )
        .join(m.Provision)
        .where(m.Provision.uri == "/akn/xa/act/2015/12/~sec_20")
        .order_by(m.ProvisionVersion.valid_from)
    ).all()
    assert [tuple(r) for r in rows] == [
        ("in_force", date(2015, 7, 1), date(2023, 3, 1), "derived"),
        ("repealed", date(2023, 3, 1), None, "open"),
    ]


def test_every_stored_text_traces_to_raw_source(session: Session, store: LocalObjectStore) -> None:
    missing = session.scalar(
        select(func.count())
        .select_from(m.ProvisionVersion)
        .outerjoin(m.ProvisionVersionSource, m.ProvisionVersionSource.provision_version_id == m.ProvisionVersion.id)
        .where(m.ProvisionVersionSource.provision_version_id.is_(None))
    )
    assert missing == 0
    for doc in session.scalars(select(m.SourceDocument)):
        assert store.exists(doc.storage_key)


def test_licence_gate_blocks_planned_sources(session: Session, catalog: Catalog, store: LocalObjectStore) -> None:
    with pytest.raises(LicenceNotApproved):
        Ingestor(session, catalog, store).ingest_source("in-india-code", actor="pytest")
    with pytest.raises(LicenceNotApproved):
        Ingestor(session, catalog, store).ingest_source("gb-find-case-law", actor="pytest")


class _ListConnector:
    def __init__(self, docs: list[RawDocument]):
        self.docs = docs

    def fetch(self) -> Iterator[RawDocument]:
        return iter(self.docs)


def _raw(external_id: str, content: bytes) -> RawDocument:
    return RawDocument(external_id, None, "application/akn+xml", content, datetime.now(UTC))


def test_conflicting_text_is_recorded_not_overwritten(
    session: Session, catalog: Catalog, store: LocalObjectStore
) -> None:
    original = (REPO_ROOT / "sample_corpus/xa/act-2015-12.eng@2023-03-01.xml").read_bytes()
    # Same provision, same period, different words, published under a new expression URI.
    altered = original.replace(b"within thirty days", b"within ten days").replace(b"eng@2023-03-01", b"eng@2023-03-02")
    report = Ingestor(session, catalog, store).ingest_source(
        "ulis-fictional-sample", actor="pytest", connector=_ListConnector([_raw("altered", altered)])
    )
    assert report.documents_new == 1
    # Both the subsection and its containing section (whose combined text includes it) conflict.
    assert sorted(c.split(" ")[0] for c in report.conflicts) == [
        "/akn/xa/act/2015/12/~sec_13",
        "/akn/xa/act/2015/12/~sec_13__subsec_2",
    ]
    texts = session.scalars(
        select(m.ProvisionVersion.text)
        .join(m.Provision)
        .where(m.Provision.uri == "/akn/xa/act/2015/12/~sec_13__subsec_2")
    ).all()
    assert texts == ["A controller shall comply with a requirement under subsection (1) within thirty days."]
    failed = session.scalar(
        select(func.count())
        .select_from(m.VerificationRecord)
        .where(m.VerificationRecord.check == "version_text_consistency", m.VerificationRecord.result == "fail")
    )
    assert failed == 2


def test_invalid_documents_are_rejected_and_run_continues(
    session: Session, catalog: Catalog, store: LocalObjectStore
) -> None:
    good = (REPO_ROOT / "sample_corpus/xa/bill-2026-9.eng.xml").read_bytes()
    unknown_jurisdiction = good.replace(
        b"<ulis:jurisdiction>XA</ulis:jurisdiction>", b"<ulis:jurisdiction>QQ</ulis:jurisdiction>"
    ).replace(b"2026/9", b"2026/99")
    wrong_type = good.replace(
        b"<ulis:instrumentType>bill</ulis:instrumentType>", b"<ulis:instrumentType>decree</ulis:instrumentType>"
    ).replace(b"2026/9", b"2026/98")
    report = Ingestor(session, catalog, store).ingest_source(
        "ulis-fictional-sample",
        actor="pytest",
        connector=_ListConnector(
            [_raw("bad-1", unknown_jurisdiction), _raw("bad-2", b"<x/>"), _raw("bad-3", wrong_type)]
        ),
    )
    assert report.documents_new == 0 and len(report.errors) == 3
    assert "unknown jurisdiction QQ" in report.errors[0]
    assert "not declared for XA" in report.errors[2]
    assert session.scalar(select(m.Instrument).where(m.Instrument.uri == "/akn/xa/bill/2026/99")) is None


def test_profile_change_requires_version_bump(session: Session, tmp_path: Path) -> None:
    for sub in ("jurisdictions", "sources", "concepts"):
        shutil.copytree(REPO_ROOT / "config" / sub, tmp_path / sub)
    xa = tmp_path / "jurisdictions/xa.yaml"
    xa.write_text(xa.read_text().replace("Parliament of Aurelia", "Parliament of the Federal Republic"))
    with pytest.raises(ProfileVersionConflict, match="XA 1.0.1 changed without a version bump"):
        sync_catalog(session, Catalog.load(tmp_path))
    xa.write_text(xa.read_text().replace("profile_version: 1.0.1", "profile_version: 1.0.2"))
    sync_catalog(session, Catalog.load(tmp_path))
    versions = session.scalars(
        select(m.JurisdictionProfileVersion.profile_version).where(m.JurisdictionProfileVersion.jurisdiction_id == "XA")
    ).all()
    assert {"1.0.1", "1.0.2"} <= set(versions)
