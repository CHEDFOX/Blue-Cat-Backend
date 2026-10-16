"""HTTP API (FastAPI TestClient against PostgreSQL)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.api.app import create_app
from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport
from ulis.settings import load_settings

pytestmark = pytest.mark.db
KEY = "test-key-0123456789"
H = {"X-API-Key": KEY}


@pytest.fixture(scope="module")
def client(db_url: str, catalog: Catalog, store_dir: Path, ingested: IngestionReport) -> Iterator[TestClient]:
    settings = replace(
        load_settings(), database_url=db_url, object_store_dir=store_dir, environment="test", api_keys=frozenset({KEY})
    )
    with TestClient(create_app(settings, catalog)) as c:
        yield c


def test_refuses_to_start_without_keys_outside_development(db_url: str) -> None:
    with pytest.raises(RuntimeError, match="ULIS_API_KEYS"):
        create_app(replace(load_settings(), database_url=db_url, environment="production", api_keys=frozenset()))


def test_health_is_public_but_data_requires_a_key(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/v1/search", params={"q": "data"}).status_code == 401
    assert client.get("/v1/search", params={"q": "data"}, headers={"X-API-Key": "wrong"}).status_code == 401
    ok = client.get("/v1/jurisdictions", headers={"Authorization": f"Bearer {KEY}"})
    assert ok.status_code == 200
    assert ok.headers["x-content-type-options"] == "nosniff" and ok.headers["cache-control"] == "no-store"


def test_search_endpoint(client: TestClient, session_factory: sessionmaker[Session]) -> None:
    q = "confidential client question about retaining backups"
    r = client.get("/v1/search", params={"q": q, "jurisdiction": "XA", "as_of": "2024-01-01", "limit": 5}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["query"]["jurisdiction"] == "XA" and body["results"]
    assert all(h["limitations"] for h in body["results"])
    with session_factory() as s:
        event = s.scalars(
            select(m.AuditEvent).where(m.AuditEvent.action == "search").order_by(m.AuditEvent.at.desc())
        ).first()
        assert event is not None
        assert event.details["q_sha256"] == hashlib.sha256(q.encode()).hexdigest()
        assert q not in str(event.details)  # the query text itself is never stored


def test_search_validation_errors(client: TestClient) -> None:
    assert client.get("/v1/search", params={"q": "x", "jurisdiction": "ZZ"}, headers=H).status_code == 422
    assert client.get("/v1/search", params={"q": "x", "limit": 500}, headers=H).status_code == 422
    assert client.get("/v1/search", params={"q": "x" * 600}, headers=H).status_code == 422


def test_provision_and_quote_verification(client: TestClient) -> None:
    r = client.get(
        "/v1/provisions", params={"uri": "/akn/xa/act/2015/12/~sec_12__subsec_1", "as_of": "2019-01-01"}, headers=H
    )
    assert r.status_code == 200
    version = r.json()["version"]
    assert "seven years" in version["text"]
    exact = client.post(
        "/v1/verify/quote",
        headers=H,
        json={
            "provision_version_id": version["id"],
            "quote": "shall not retain personal data for longer than seven years",
        },
    )
    assert exact.json()["result"] == "exact"
    wrong = client.post(
        "/v1/verify/quote",
        headers=H,
        json={"provision_version_id": version["id"], "quote": "for longer than five years"},
    )
    assert wrong.json()["result"] == "not_found"
    both = client.post("/v1/verify/quote", headers=H, json={"quote": "x"})
    assert both.status_code == 422
    assert client.get("/v1/provisions", params={"uri": "/akn/none"}, headers=H).status_code == 404


def test_source_document_verification_and_coverage(client: TestClient) -> None:
    docs = client.get("/v1/source-documents", headers=H).json()
    assert len(docs) >= 14
    res = client.post(f"/v1/verify/source-documents/{docs[0]['id']}", headers=H).json()
    assert res["result"] == "pass"
    coverage = {c["jurisdiction"]: c for c in client.get("/v1/coverage", headers=H).json()}
    assert coverage["IN"]["instruments"] == 0 and coverage["IN"]["corpus_status"] == "pilot"
    assert coverage["XA"]["instruments"] == 4 and coverage["XA"]["fictional"] is True


def test_jurisdiction_profile_endpoint(client: TestClient) -> None:
    body = client.get("/v1/jurisdictions/XA-NP", headers=H).json()
    assert body["chain"] == ["XA-NP", "XA", "INT"]
    assert body["profile"]["precedent"]["doctrine"] == "stare_decisis"
    assert len(body["profile_sha256"]) == 64
    assert client.get("/v1/jurisdictions/ZZ", headers=H).status_code == 404
