"""POST /v1/research and GET /v1/research/{id}: contract, limits, privacy and the job lifecycle (PostgreSQL)."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pytest
from assistant_support import CONFIG, S12_1, happy_script, understanding_reply
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from ulis.api.app import create_app
from ulis.assistant.config import AssistantConfig
from ulis.assistant.jobs import ResearchService, request_digest
from ulis.assistant.llm import FakeLLM, LLMError, LLMResponse, Usage, text_response
from ulis.assistant.pipeline import ResearchInput
from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport
from ulis.settings import load_settings

pytestmark = pytest.mark.db
KEY = "test-key-0123456789"
H = {"X-API-Key": KEY}
KEY2 = "other-key-0123456789"
PRINCIPAL = "key:" + hashlib.sha256(KEY.encode()).hexdigest()[:12]
QUESTION = "How long may a controller keep personal data?"
Q_MARK, C_MARK = "QMARK-4417", "CMARK-9082"


# --------------------------------------------------------------------------- helpers


class Harness:
    def __init__(self, client: TestClient, service: ResearchService) -> None:
        self.client, self.service = client, service

    def post(self, body: Any = None, *, headers: dict[str, str] | None = None, **fields: Any) -> Any:
        payload = (
            body if body is not None else {"question": QUESTION, "jurisdiction": "XA", "as_of": "2024-01-01", **fields}
        )
        return self.client.post("/v1/research", json=payload, headers=H if headers is None else headers)

    def finish(self, response: Any, timeout: float = 30.0) -> dict[str, Any]:
        assert response.status_code == 202, response.text
        job_id = uuid.UUID(response.json()["id"])
        self.service.wait(job_id, timeout)
        got = self.client.get(f"/v1/research/{job_id}", headers=H)
        assert got.status_code == 200, got.text
        return got.json()  # type: ignore[no-any-return]


@pytest.fixture
def make(
    db_url: str,
    catalog: Catalog,
    store_dir: Path,
    ingested: IngestionReport,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., Harness]]:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    open_clients: list[TestClient] = []

    def build(
        llm: Callable[[str], Any] | None = None, config: AssistantConfig | None = None, *, keep: bool = False
    ) -> Harness:
        settings = replace(
            load_settings(),
            database_url=db_url,
            object_store_dir=store_dir,
            environment="test",
            api_keys=frozenset({KEY, KEY2}),
        )
        app = create_app(settings, catalog)
        service: ResearchService = app.state.research
        service.config = config or CONFIG
        service.llm_factory = llm
        client = TestClient(app)
        client.__enter__()
        open_clients.append(client)
        return Harness(client, service)

    yield build
    for client in open_clients:
        client.__exit__(None, None, None)


@pytest.fixture(autouse=True)
def clean_jobs(session_factory: sessionmaker[Session], ingested: IngestionReport) -> Iterator[None]:
    with session_factory() as s:
        s.execute(text("DELETE FROM research_jobs"))
        s.commit()
    yield


def fixed(llm_script: list[Any]) -> Callable[[str], FakeLLM]:
    return lambda depth: FakeLLM(list(llm_script))


# --------------------------------------------------------------------------- contract mirror


class _C(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CProgress(_C):
    at: datetime
    stage: Literal["understanding", "searching", "drafting", "verifying"]
    message: str


class CJurisdiction(_C):
    id: str
    reason: str
    covered: bool


class CIssue(_C):
    id: str
    title: str
    area_of_law: str
    question: str


class CUnderstanding(_C):
    summary: str
    facts: list[str]
    as_of: str | None
    jurisdictions: list[CJurisdiction]
    issues: list[CIssue]


class CReasoning(_C):
    text: str
    authority_ids: list[str]


class CGuidanceIssue(_C):
    issue_id: str
    conclusion: str
    confidence: Literal["high", "medium", "low"]
    reasoning: list[CReasoning]
    uncertainties: list[str]


class CGuidance(_C):
    summary: str
    issues: list[CGuidanceIssue]
    next_steps: list[str]


class CLimitation(_C):
    code: str
    severity: str
    message: str


class CSource(_C):
    name: str
    url: str | None
    attribution: str | None


class CAuthority(_C):
    id: str
    citation: str
    uri: str
    jurisdiction: str
    kind: Literal["provision", "judgment_paragraph"]
    text: str
    text_sha256: str
    valid_from: str | None
    valid_to: str | None
    authority_label: str
    limitations: list[CLimitation]
    source: CSource
    verified: bool


class CUsage(_C):
    searches: int
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int  # additive to the contract


class CResult(_C):
    understanding: CUnderstanding
    guidance: CGuidance
    authorities: list[CAuthority]
    coverage_notes: list[str]
    removed_claims: int
    limitations: list[CLimitation]
    disclaimer: str
    usage: CUsage
    verification: dict[str, Any]  # additive to the contract


class CError(_C):
    code: str
    message: str


class CJob(_C):
    id: uuid.UUID
    status: Literal["queued", "running", "completed", "failed", "refused"]
    created_at: datetime
    expires_at: datetime
    progress: list[CProgress]
    result: CResult | None
    error: CError | None


# --------------------------------------------------------------------------- availability, auth, validation


def test_503_without_api_key_and_the_rest_of_the_api_keeps_working(make: Callable[..., Harness]) -> None:
    h = make(llm=None)
    r = h.post()
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]
    assert h.client.get("/v1/jurisdictions", headers=H).status_code == 200
    assert h.client.get("/v1/search", params={"q": "personal data", "jurisdiction": "XA"}, headers=H).status_code == 200
    assert h.client.get(f"/v1/research/{uuid.uuid4()}", headers=H).status_code == 404


def test_both_routes_require_the_api_key(make: Callable[..., Harness]) -> None:
    h = make(fixed([]))
    assert h.post(headers={}).status_code == 401
    assert h.post(headers={"X-API-Key": "wrong"}).status_code == 401
    assert h.client.get(f"/v1/research/{uuid.uuid4()}").status_code == 401
    bearer = h.client.get(f"/v1/research/{uuid.uuid4()}", headers={"Authorization": f"Bearer {KEY}"})
    assert bearer.status_code == 404  # authenticated, simply unknown


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"question": ""}, 422),
        ({"question": "   \n\t "}, 422),
        ({"jurisdiction": "XA"}, 422),
        ({"question": QUESTION, "jurisdiction": "ZZ"}, 422),
        ({"question": QUESTION, "depth": "extreme"}, 422),
        ({"question": QUESTION, "as_of": "01/02/2024"}, 422),
        ({"question": QUESTION, "as_of": "2024-13-45"}, 422),
        ({"question": QUESTION, "unexpected": True}, 422),
        ({"question": 12}, 422),
        ({"question": QUESTION, "case_text": ["x"]}, 422),
        ([1, 2, 3], 422),
        ({"question": "q" * 4001}, 413),
        ({"question": QUESTION, "case_text": "c" * 60001}, 413),
    ],
)
def test_invalid_input_is_rejected_with_the_documented_status(
    make: Callable[..., Harness], body: Any, status: int
) -> None:
    h = make(fixed([]))
    assert h.post(body).status_code == status


def test_malformed_json_is_400_and_an_oversized_body_413(make: Callable[..., Harness]) -> None:
    h = make(fixed([]))
    bad = h.client.post("/v1/research", content=b'{"question": ', headers={**H, "Content-Type": "application/json"})
    assert bad.status_code == 400
    huge = h.client.post(
        "/v1/research",
        content=b'{"question": "' + b"x" * 1_100_000 + b'"}',
        headers={**H, "Content-Type": "application/json"},
    )
    assert huge.status_code == 413


def test_validation_errors_never_echo_the_request_text(make: Callable[..., Harness]) -> None:
    h = make(fixed([]))
    r = h.post({"question": f"{Q_MARK} confidential", "case_text": f"{C_MARK} file", "depth": "extreme"})
    assert r.status_code == 422
    assert Q_MARK not in r.text and C_MARK not in r.text
    assert h.post({"question": QUESTION, "jurisdiction": f"{Q_MARK}"}).status_code == 422


def test_maximum_sizes_are_accepted_and_jurisdiction_is_case_insensitive(make: Callable[..., Harness]) -> None:
    h = make(fixed([understanding_reply(issues=())]))
    body = {"question": "q" * 4000, "case_text": "c" * 60000, "jurisdiction": " xa "}
    job = h.finish(h.post(body))
    assert job["status"] == "completed"
    assert job["result"]["understanding"]["jurisdictions"][0]["id"] == "XA"


# --------------------------------------------------------------------------- lifecycle


def test_job_lifecycle_queued_running_completed(make: Callable[..., Harness]) -> None:
    started, release = threading.Event(), threading.Event()

    def gate(_request: Any) -> LLMResponse:
        started.set()
        assert release.wait(20)
        return text_response(understanding_reply())

    script = happy_script()
    script[0] = gate
    h = make(fixed(script))
    r = h.post()
    assert r.status_code == 202 and set(r.json()) == {"id", "status"} and r.json()["status"] == "queued"
    job_id = r.json()["id"]
    assert r.headers["location"] == f"/v1/research/{job_id}"
    assert started.wait(20)
    running = h.client.get(f"/v1/research/{job_id}", headers=H).json()
    assert running["status"] == "running" and running["result"] is None and running["error"] is None
    assert running["progress"][0]["stage"] == "understanding"
    release.set()
    done = h.finish(r)
    assert done["status"] == "completed" and done["error"] is None
    stages = [p["stage"] for p in done["progress"]]
    assert stages[0] == "understanding" and stages[-1] == "verifying" and "drafting" in stages
    created, expires = datetime.fromisoformat(done["created_at"]), datetime.fromisoformat(done["expires_at"])
    assert abs((expires - created) - timedelta(hours=24)) < timedelta(seconds=5)


def test_depth_selects_the_model_factory_argument(make: Callable[..., Harness]) -> None:
    depths: list[str] = []

    def factory(depth: str) -> FakeLLM:
        depths.append(depth)
        return FakeLLM(happy_script(), model=f"model-{depth}")

    h = make(factory, replace(CONFIG, max_searches_deep=10))
    std = h.finish(h.post())
    deep = h.finish(h.post(depth="deep"))
    assert depths == ["standard", "deep"]
    assert std["result"]["usage"]["model"] == "model-standard" and deep["result"]["usage"]["model"] == "model-deep"


def test_expired_jobs_are_404_and_purged(make: Callable[..., Harness], session_factory: sessionmaker[Session]) -> None:
    h = make(fixed(happy_script()))
    job = h.finish(h.post())
    jid = job["id"]
    with session_factory() as s:
        s.execute(text("UPDATE research_jobs SET expires_at = now() - interval '1 minute' WHERE id = :i"), {"i": jid})
        s.commit()
    assert h.client.get(f"/v1/research/{jid}", headers=H).status_code == 404
    with session_factory() as s:
        assert s.execute(text("SELECT count(*) FROM research_jobs WHERE id = :i"), {"i": jid}).scalar() == 0


def test_expired_rows_are_purged_on_new_job_and_on_startup(
    make: Callable[..., Harness], session_factory: sessionmaker[Session]
) -> None:
    def seed(n: int) -> list[str]:
        ids = []
        with session_factory() as s:
            for _ in range(n):
                jid = str(uuid.uuid4())
                ids.append(jid)
                s.execute(
                    text(
                        "INSERT INTO research_jobs (id, status, principal, created_at, expires_at, request_sha256, "
                        "progress)"
                        " VALUES (:i, 'completed', 'p', now() - interval '2 days', now() - interval '1 day', :h, '[]')"
                    ),
                    {"i": jid, "h": "0" * 64},
                )
            s.commit()
        return ids

    def count(ids: list[str]) -> int:
        with session_factory() as s:
            return int(
                s.execute(
                    text("SELECT count(*) FROM research_jobs WHERE id = ANY(CAST(:i AS uuid[]))"), {"i": ids}
                ).scalar()
                or 0
            )

    h = make(fixed(happy_script()))
    old = seed(2)
    assert count(old) == 2
    h.finish(h.post())  # creating a job purges
    assert count(old) == 0
    more = seed(3)
    make(fixed([]))  # a new process starting up purges
    assert count(more) == 0


def test_jobs_left_running_by_a_dead_process_are_failed_on_startup(
    make: Callable[..., Harness], session_factory: sessionmaker[Session]
) -> None:
    ids = {status: str(uuid.uuid4()) for status in ("running", "queued", "completed")}
    with session_factory() as s:
        for status, jid in ids.items():
            s.execute(
                text(
                    "INSERT INTO research_jobs (id, status, principal, created_at, expires_at, request_sha256, "
                    "progress)"
                    " VALUES (:i, :s, :p, now(), now() + interval '1 day', :h, '[]')"
                ),
                {"i": jid, "s": status, "p": PRINCIPAL, "h": "0" * 64},
            )
        s.commit()
    h = make(fixed([]))
    for status in ("running", "queued"):
        job = h.client.get(f"/v1/research/{ids[status]}", headers=H).json()
        assert job["status"] == "failed" and job["error"]["code"] == "interrupted"
    assert h.client.get(f"/v1/research/{ids['completed']}", headers=H).json()["status"] == "completed"


def test_a_job_can_only_be_read_by_the_key_that_created_it(make: Callable[..., Harness]) -> None:
    h = make(fixed(happy_script()))
    response = h.post()
    h.finish(response)
    url = f"/v1/research/{response.json()['id']}"
    assert h.client.get(url, headers={"X-API-Key": KEY2}).status_code == 404
    assert h.client.get(url, headers=H).status_code == 200


def test_openapi_documents_the_research_routes(make: Callable[..., Harness]) -> None:
    schema = make(fixed([])).client.get("/openapi.json").json()
    post = schema["paths"]["/v1/research"]["post"]
    body = post["requestBody"]["content"]["application/json"]["schema"]
    assert set(body["properties"]) == {"question", "case_text", "jurisdiction", "as_of", "depth"}
    assert {"202", "400", "413", "422", "429", "503"} <= set(post["responses"])
    assert "/v1/research/{job_id}" in schema["paths"]


def test_unknown_and_malformed_ids_are_404(make: Callable[..., Harness]) -> None:
    h = make(fixed([]))
    assert h.client.get(f"/v1/research/{uuid.uuid4()}", headers=H).status_code == 404
    assert h.client.get("/v1/research/not-a-uuid", headers=H).status_code == 404


# --------------------------------------------------------------------------- failure outcomes


def test_provider_errors_refusals_and_bugs_end_in_the_documented_states(
    make: Callable[..., Harness], caplog: pytest.LogCaptureFixture
) -> None:
    def boom(_r: Any) -> LLMResponse:
        raise LLMError("llm_rate_limited", "The language-model provider is rate limiting requests.", retryable=True)

    def bug(_r: Any) -> LLMResponse:
        raise ValueError(f"{Q_MARK} leaked in an exception message")

    refusal = LLMResponse("", [], "refusal", Usage(1, 1), [], "fake-model")
    caplog.set_level(logging.DEBUG)
    h = make(lambda depth: FakeLLM([boom]))
    failed = h.finish(h.post())
    assert failed["status"] == "failed" and failed["error"]["code"] == "llm_rate_limited" and failed["result"] is None

    h.service.llm_factory = lambda depth: FakeLLM([refusal])
    refused = h.finish(h.post())
    assert refused["status"] == "refused" and refused["error"]["code"] == "refused"

    h.service.llm_factory = lambda depth: FakeLLM([bug])
    crashed = h.finish(h.post(question=f"{Q_MARK} question"))
    assert crashed["status"] == "failed" and crashed["error"]["code"] == "internal_error"
    assert Q_MARK not in json.dumps(crashed)
    assert not any(Q_MARK in rec.getMessage() for rec in caplog.records)


# --------------------------------------------------------------------------- limits and hooks


def test_concurrency_and_hourly_limits_return_429(make: Callable[..., Harness]) -> None:
    started, release = threading.Event(), threading.Event()

    def gate(_r: Any) -> LLMResponse:
        started.set()
        assert release.wait(20)
        return text_response(understanding_reply(issues=()))

    h = make(lambda depth: FakeLLM([gate]), replace(CONFIG, max_active_per_principal=1, rate_per_hour=3))
    first = h.post()
    assert first.status_code == 202 and started.wait(20)
    second = h.post()
    assert second.status_code == 429 and int(second.headers["retry-after"]) > 0
    release.set()
    h.finish(first)
    h.service.llm_factory = lambda depth: FakeLLM([understanding_reply(issues=())])
    assert h.finish(h.post())["status"] == "completed"
    assert h.finish(h.post())["status"] == "completed"
    limited = h.post()  # fourth in the hour (the rejected one never became a job)
    assert limited.status_code == 429 and "Hourly" in limited.json()["detail"]


def test_shared_research_limiter_is_charged_and_can_refuse(
    make: Callable[..., Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    costs: list[int] = []

    def limiter(request: Any, cost: int = 1) -> None:
        costs.append(cost)
        raise HTTPException(429, "shared limiter says no", headers={"Retry-After": "7"})

    monkeypatch.setattr("ulis.api.security.check_research_limits", limiter)
    h = make(fixed(happy_script()))
    r = h.post()
    assert r.status_code == 429 and r.headers["retry-after"] == "7" and costs == [1]
    r = h.post(depth="deep")
    assert r.status_code == 429 and costs == [1, 3]  # a deep run costs three units


# --------------------------------------------------------------------------- privacy


def test_request_text_is_never_stored_audited_or_logged(
    make: Callable[..., Harness], session_factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    h = make(fixed(happy_script()))
    question, case_text = f"{Q_MARK} {QUESTION}", f"{C_MARK} The controller keeps backups of client files."
    body = {"question": question, "case_text": case_text, "jurisdiction": "XA", "as_of": "2024-01-01"}
    job = h.finish(h.post(body))
    assert job["status"] == "completed"
    with session_factory() as s:
        rows = (
            s.execute(text("SELECT to_jsonb(r)::text FROM research_jobs r WHERE id = :i"), {"i": job["id"]})
            .scalars()
            .all()
        )
        audits = (
            s.execute(
                text("SELECT to_jsonb(a)::text FROM audit_events a WHERE details->>'job_id' = :i ORDER BY at"),
                {"i": job["id"]},
            )
            .scalars()
            .all()
        )
        stored_digest = s.execute(
            text("SELECT request_sha256 FROM research_jobs WHERE id = :i"), {"i": job["id"]}
        ).scalar()
    assert len(rows) == 1 and len(audits) == 2
    for blob in [*rows, *audits, json.dumps(job)]:
        assert Q_MARK not in blob and C_MARK not in blob and "client files" not in blob
    expected = request_digest(
        ResearchInput(question=question, case_text=case_text, jurisdiction="XA", as_of=datetime(2024, 1, 1).date())
    )
    assert (
        stored_digest
        == expected
        == hashlib.sha256(
            json.dumps(
                {
                    "question": question,
                    "case_text": case_text,
                    "jurisdiction": "XA",
                    "as_of": "2024-01-01",
                    "depth": "standard",
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    )
    assert [json.loads(a)["action"] for a in audits] == ["research_created", "research_finished"]
    assert json.loads(audits[1])["details"]["status"] == "completed"
    assert not any(Q_MARK in r.getMessage() or C_MARK in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- contract


def test_completed_job_matches_the_documented_contract(
    make: Callable[..., Harness], session_factory: sessionmaker[Session]
) -> None:
    h = make(fixed(happy_script()))
    raw = h.finish(h.post())
    job = CJob.model_validate(raw)  # raises on any missing, mistyped or undeclared field
    assert job.status == "completed" and job.result is not None and job.error is None
    result = job.result
    ids = {a.id for a in result.authorities}
    assert ids and all(set(r.authority_ids) <= ids for i in result.guidance.issues for r in i.reasoning)
    assert {i.issue_id for i in result.guidance.issues} == {i.id for i in result.understanding.issues}
    with session_factory() as s:
        for a in result.authorities:
            stored = s.execute(
                text(
                    "SELECT pv.text, pv.text_sha256 FROM provision_versions pv "
                    "JOIN provisions p ON p.id = pv.provision_id WHERE p.uri = :u AND pv.text = :t"
                ),
                {"u": a.uri, "t": a.text},
            ).one()
            assert (
                stored.text == a.text
                and stored.text_sha256 == a.text_sha256 == hashlib.sha256(a.text.encode()).hexdigest()
            )
            assert a.verified and a.citation == S12_1
    assert result.disclaimer and "not legal advice" in result.disclaimer


def test_failed_and_refused_jobs_match_the_contract_too(make: Callable[..., Harness]) -> None:
    h = make(lambda depth: FakeLLM([]))
    job = CJob.model_validate(h.finish(h.post()))
    assert job.status == "failed" and job.result is None and job.error is not None
