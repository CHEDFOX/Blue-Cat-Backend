"""API hardening: key matching, rate limits, body limits, CORS, headers, access log, /ready, production checks."""

from __future__ import annotations

import ipaddress
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from ulis.api import security
from ulis.api.app import create_app
from ulis.api.deps import AppState, Principal, require_api_key
from ulis.api.security import (
    ConfigurationError,
    RateSpec,
    SecurityConfig,
    SecurityRuntime,
    TokenBucketLimiter,
    check_research_limits,
    client_ip,
    enforce_production,
    install_security,
    key_fingerprint,
    match_api_key,
    production_problems,
)
from ulis.catalog import Catalog
from ulis.db import make_engine, make_session_factory
from ulis.ingestion.pipeline import IngestionReport
from ulis.settings import Settings, load_settings
from ulis.storage.object_store import LocalObjectStore

KEY = "k1-0123456789abcdef0123456789"
KEY2 = "k2-abcdef0123456789abcdef0123"
STRONG_DB = "postgresql+psycopg://ulis:7f3a9c1e5b2d4f60aa11@db:5432/ulis"
UNREACHABLE_DB = "postgresql+psycopg://ulis:ulis@127.0.0.1:1/none"

# --------------------------------------------------------------------------- keys


def test_match_api_key_compares_every_key_and_rejects_the_rest() -> None:
    keys = [KEY, KEY2, "third-key-0123456789abcdef"]
    assert match_api_key(KEY2, keys) == KEY2
    assert match_api_key("third-key-0123456789abcdef", keys) == "third-key-0123456789abcdef"
    for bad in (None, "", "wrong", KEY[:-1], KEY + "x", KEY.upper(), "kéy"):
        assert match_api_key(bad, keys) is None
    assert match_api_key(KEY, []) is None


def test_fingerprint_never_contains_the_key() -> None:
    fp = key_fingerprint(KEY)
    assert fp.startswith("key:") and len(fp) == 16 and KEY not in fp and fp == key_fingerprint(KEY)
    assert Principal(fp).id == fp


# --------------------------------------------------------------------------- rate specs and buckets


def test_rate_spec_parsing() -> None:
    assert RateSpec.parse("120/min") == RateSpec(120, 60)
    assert RateSpec.parse("20/hour") == RateSpec(20, 3600)
    assert RateSpec.parse(" 5/S ") == RateSpec(5, 1)
    assert RateSpec.parse("300/day").rate == pytest.approx(300 / 86400)  # type: ignore[union-attr]
    for off in ("off", "OFF", "", "0", "unlimited", None):
        assert RateSpec.parse(off) is None
    for bad in ("fast", "10", "10/fortnight", "-5/min", "x/min", "0/min/"):
        with pytest.raises(ValueError, match="invalid rate limit"):
            RateSpec.parse(bad)


def test_token_bucket_burst_refill_and_retry_after() -> None:
    now = [1000.0]
    limiter = TokenBucketLimiter(clock=lambda: now[0])
    spec = RateSpec(3, 60)  # one token every 20 s
    assert [limiter.acquire("k", "a", spec) for _ in range(3)] == [0, 0, 0]
    assert limiter.acquire("k", "a", spec) == 20  # empty: wait for one token
    assert limiter.acquire("k", "b", spec) == 0  # other callers have their own bucket
    assert limiter.acquire("other", "a", spec) == 0  # and other buckets are independent
    now[0] += 20
    assert limiter.acquire("k", "a", spec) == 0
    assert limiter.acquire("k", "a", spec) == 20
    now[0] += 10_000  # never more than the capacity
    assert [limiter.acquire("k", "a", spec) for _ in range(4)] == [0, 0, 0, 20]


def test_token_bucket_cost_is_capped_penalty_and_memory_bound() -> None:
    now = [0.0]
    limiter = TokenBucketLimiter(clock=lambda: now[0], max_entries=3)
    spec = RateSpec(10, 10)
    assert limiter.acquire("k", "a", spec, cost=1000) == 0  # a cost above the capacity is capped, never impossible
    limiter.penalize("k", "p", spec, cost=1000)  # penalties are bounded by the capacity ...
    assert limiter.acquire("k", "p", spec) > 0
    now[0] += 25  # ... so a penalised caller is free again after at most two windows
    assert limiter.acquire("k", "p", spec) == 0
    for ident in "bcdef":
        limiter.acquire("k", ident, spec)
    assert len(limiter._buckets) == 3


# --------------------------------------------------------------------------- client address


def _scope(peer: str, xff: str | None = None) -> dict[str, Any]:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return {"type": "http", "client": (peer, 1234), "headers": headers}


def test_client_ip_trusts_forwarded_for_only_from_trusted_proxies() -> None:
    private = (ipaddress.ip_network("172.16.0.0/12"),)
    # No trusted proxies: the header is ignored (anyone could forge it).
    assert client_ip(_scope("203.0.113.9", "1.2.3.4"), ()) == "203.0.113.9"
    # An untrusted peer cannot make its header count either.
    assert client_ip(_scope("203.0.113.9", "1.2.3.4"), private) == "203.0.113.9"
    # Through a trusted proxy the right-most untrusted hop is the client; forged left entries are ignored.
    assert client_ip(_scope("172.18.0.5", "9.9.9.9, 198.51.100.7"), private) == "198.51.100.7"
    assert client_ip(_scope("172.18.0.5", "198.51.100.7, 172.18.0.2"), private) == "198.51.100.7"
    assert client_ip(_scope("172.18.0.5"), private) == "172.18.0.5"
    assert client_ip(_scope("172.18.0.5", "not-an-ip"), private) == "172.18.0.5"
    # IPv6 callers are limited per /64.
    assert client_ip(_scope("2001:db8:1:2:aaaa::1"), ()) == client_ip(_scope("2001:db8:1:2:bbbb::2"), ())


# --------------------------------------------------------------------------- configuration


def test_config_defaults_and_overrides() -> None:
    cfg = SecurityConfig.from_env("development", {})
    assert cfg.cors_origins == () and cfg.enable_docs and cfg.access_log
    assert cfg.rate_research_key == RateSpec(60, 3600) and cfg.rate_research_global == RateSpec(300, 86400)
    prod = SecurityConfig.from_env("production", {})
    assert not prod.enable_docs
    env = {
        "ULIS_CORS_ORIGINS": "https://app.example.org/, https://b.example.org",
        "ULIS_TRUSTED_PROXIES": "10.0.0.0/8, 192.168.1.1",
        "ULIS_MAX_BODY_BYTES": "1000",
        "ULIS_RATE_LIMIT_KEY": "5/s",
        "ULIS_RATE_LIMIT_IP": "off",
        "ULIS_RATE_LIMIT_RESEARCH_KEY": "2/hour",
        "ULIS_ACCESS_LOG": "0",
        "ULIS_ENABLE_DOCS": "1",
    }
    cfg = SecurityConfig.from_env("production", env)
    assert cfg.cors_origins == ("https://app.example.org", "https://b.example.org")
    assert len(cfg.trusted_proxies) == 2 and cfg.max_body_bytes == 1000
    assert cfg.rate_key == RateSpec(5, 1) and cfg.rate_ip is None and cfg.rate_research_key == RateSpec(2, 3600)
    assert not cfg.access_log and cfg.enable_docs


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("ULIS_RATE_LIMIT_KEY", "lots", "ULIS_RATE_LIMIT_KEY"),
        ("ULIS_MAX_BODY_BYTES", "big", "ULIS_MAX_BODY_BYTES"),
        ("ULIS_MAX_BODY_BYTES", "0", "positive"),
        ("ULIS_CORS_ORIGINS", "example.org", "ULIS_CORS_ORIGINS"),
        ("ULIS_TRUSTED_PROXIES", "10.0.0.0/99", "ULIS_TRUSTED_PROXIES"),
    ],
)
def test_malformed_configuration_is_refused(name: str, value: str, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        SecurityConfig.from_env("development", {name: value})


def _settings(**changes: Any) -> Settings:
    base = replace(load_settings(), environment="production", database_url=STRONG_DB, api_keys=frozenset({KEY}))
    return replace(base, **changes)


def test_production_check_accepts_a_sound_configuration() -> None:
    cfg = SecurityConfig(environment="production")
    assert production_problems(_settings(), cfg) == []
    enforce_production(_settings(), cfg)


def test_production_check_lists_every_problem_without_leaking_secrets() -> None:
    weak = _settings(api_keys=frozenset({"changeme", "short"}), database_url="postgresql+psycopg://ulis:ulis@db/ulis")
    problems = production_problems(weak, SecurityConfig(environment="production", cors_origins=("*",)))
    text = " | ".join(problems)
    assert "API key" in text and "database password" in text and "'*'" in text
    assert len(problems) == 4  # two weak keys, the password, the wildcard
    with pytest.raises(ConfigurationError) as exc_info:
        enforce_production(weak, SecurityConfig(environment="production"))
    assert isinstance(exc_info.value, RuntimeError)
    assert "changeme" not in str(exc_info.value) and "ulis:ulis" not in str(exc_info.value)
    no_keys = production_problems(_settings(api_keys=frozenset()), SecurityConfig())
    assert any("ULIS_API_KEYS" in p for p in no_keys)
    assert any(
        "database password" in p
        for p in production_problems(_settings(database_url="postgresql:///ulis"), SecurityConfig())
    )
    # Other environments are not held to the production rules.
    enforce_production(replace(weak, environment="development"), SecurityConfig())


def test_create_app_refuses_unsafe_production_and_starts_when_sound(catalog: Catalog, tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)
    with pytest.raises(ConfigurationError, match="database password"):
        create_app(_settings(database_url="postgresql+psycopg://ulis:ulis@db/ulis"), catalog, store)
    with pytest.raises(RuntimeError, match="ULIS_API_KEYS"):
        create_app(_settings(api_keys=frozenset()), catalog, store)
    app = create_app(_settings(), catalog, store)  # lazy engine: nothing connects
    assert app.state.security.config.environment == "production"


# --------------------------------------------------------------------------- the application under test


class Harness:
    def __init__(self, app: FastAPI, runtime: SecurityRuntime, lines: list[str]):
        self.app, self.runtime, self.lines = app, runtime, lines

    def client(self, peer: str = "testclient") -> TestClient:
        return TestClient(self.app, client=(peer, 50000))

    @property
    def log(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.lines]


@pytest.fixture
def make_app(catalog: Catalog, tmp_path: Path) -> Iterator[Callable[..., Harness]]:
    engines = []

    def factory(
        config: SecurityConfig | None = None,
        environment: str = "test",
        keys: frozenset[str] = frozenset({KEY, KEY2}),
        db_url: str = UNREACHABLE_DB,
    ) -> Harness:
        settings = replace(load_settings(), environment=environment, api_keys=keys, database_url=db_url)
        engine = make_engine(db_url)
        engines.append(engine)
        app = FastAPI()
        app.state.ulis = AppState(settings, catalog, make_session_factory(engine), LocalObjectStore(tmp_path))

        @app.get("/health")
        def health() -> dict[str, str]:
            return {"status": "ok"}

        @app.get("/v1/data")
        def data(_: Annotated[Principal, Depends(require_api_key)]) -> dict[str, bool]:
            return {"ok": True}

        @app.post("/v1/echo")
        async def echo(request: Request) -> dict[str, int]:
            return {"bytes": len(await request.body())}

        @app.post("/v1/research")
        def research(request: Request, _: Annotated[Principal, Depends(require_api_key)]) -> JSONResponse:
            check_research_limits(request)
            return JSONResponse({"id": "job", "status": "queued"}, status_code=202)

        @app.post("/v1/research/deep")
        def deep(request: Request, _: Annotated[Principal, Depends(require_api_key)]) -> JSONResponse:
            check_research_limits(request, cost=3)
            return JSONResponse({"id": "job"}, status_code=202)

        @app.get("/v1/research/{job_id}")
        def poll(job_id: str) -> dict[str, str]:
            return {"id": job_id, "status": "running"}

        config = config or SecurityConfig(environment=environment)
        runtime = install_security(app, settings, config)
        lines: list[str] = []
        runtime.log_sink = lines.append
        return Harness(app, runtime, lines)

    yield factory
    for engine in engines:
        engine.dispose()


H = {"X-API-Key": KEY}

# --------------------------------------------------------------------------- headers, ids, docs


def test_security_headers_and_request_id(make_app: Callable[..., Harness]) -> None:
    h = make_app()
    r = h.client().get("/v1/data", headers=H)
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert "strict-transport-security" not in r.headers  # only in production
    rid = r.headers["x-request-id"]
    assert len(rid) == 32
    assert h.client().get("/health", headers={"X-Request-ID": "abc-12345678"}).headers["x-request-id"] == "abc-12345678"
    assert h.client().get("/health", headers={"X-Request-ID": 'x y"<>'}).headers["x-request-id"] != 'x y"<>'
    prod = make_app(
        environment="production", config=SecurityConfig(environment="production"), db_url=STRONG_DB
    ).client()
    assert "max-age=" in prod.get("/health").headers["strict-transport-security"]


def test_docs_are_removed_in_production_only(make_app: Callable[..., Harness]) -> None:
    dev = make_app().client()
    assert dev.get("/docs").status_code == 200 and "content-security-policy" not in dev.get("/docs").headers
    assert dev.get("/openapi.json").status_code == 200
    prod_config = SecurityConfig.from_env("production", {})
    prod = make_app(environment="production", config=prod_config, db_url=STRONG_DB).client()
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert prod.get(path).status_code == 404
    docs_on = SecurityConfig.from_env("production", {"ULIS_ENABLE_DOCS": "1"})
    assert make_app(environment="production", config=docs_on, db_url=STRONG_DB).client().get("/docs").status_code == 200


# --------------------------------------------------------------------------- authentication and rate limits


def test_multiple_keys_work_and_bad_keys_are_refused(make_app: Callable[..., Harness]) -> None:
    c = make_app().client()
    assert c.get("/v1/data", headers={"X-API-Key": KEY}).status_code == 200
    assert c.get("/v1/data", headers={"Authorization": f"Bearer {KEY2}"}).status_code == 200
    for headers in ({}, {"X-API-Key": "nope"}, {"X-API-Key": KEY[:-1]}, {"Authorization": "Basic abc"}):
        assert c.get("/v1/data", headers=headers).status_code == 401


def test_per_ip_limit_and_health_exemption(make_app: Callable[..., Harness]) -> None:
    h = make_app(config=SecurityConfig(rate_ip=RateSpec(3, 60), rate_key=None))
    c = h.client()
    assert [c.get("/v1/data", headers=H).status_code for _ in range(3)] == [200, 200, 200]
    blocked = c.get("/v1/data", headers=H)
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1
    assert blocked.json() == {"detail": "rate limit exceeded"}
    assert c.get("/health").status_code == 200  # liveness probes are never limited
    assert h.client("198.51.100.1").get("/v1/data", headers=H).status_code == 200  # another IP is unaffected
    assert any(r.get("limited_by") == "ip" and r["status"] == 429 for r in h.log)


def test_per_key_limit_is_independent_per_key(make_app: Callable[..., Harness]) -> None:
    h = make_app(config=SecurityConfig(rate_ip=None, rate_key=RateSpec(2, 60)))
    c = h.client()
    assert [c.get("/v1/data", headers=H).status_code for _ in range(3)] == [200, 200, 429]
    assert c.get("/v1/data", headers={"X-API-Key": KEY2}).status_code == 200
    assert c.get("/v1/data", headers={"X-API-Key": KEY2}).status_code == 200
    assert c.get("/v1/data", headers={"X-API-Key": KEY2}).status_code == 429


def test_failed_authentication_costs_extra_from_the_ip_bucket(make_app: Callable[..., Harness]) -> None:
    h = make_app(config=SecurityConfig(rate_ip=RateSpec(10, 60), rate_key=None, auth_failure_cost=4))
    c = h.client()
    statuses = [c.get("/v1/data", headers={"X-API-Key": "guess"}).status_code for _ in range(4)]
    assert statuses == [401, 401, 429, 429]  # 1 + 4 per failure: the third attempt finds the bucket empty
    assert h.client("198.51.100.2").get("/v1/data", headers=H).status_code == 200


def test_forwarded_for_sets_the_limited_address_only_behind_trusted_proxies(make_app: Callable[..., Harness]) -> None:
    trusted = SecurityConfig(
        rate_ip=RateSpec(1, 60), rate_key=None, trusted_proxies=(ipaddress.ip_network("10.0.0.0/8"),)
    )
    h = make_app(config=trusted)
    proxy = h.client("10.1.1.1")
    assert proxy.get("/v1/data", headers={**H, "X-Forwarded-For": "203.0.113.1"}).status_code == 200
    assert proxy.get("/v1/data", headers={**H, "X-Forwarded-For": "203.0.113.1"}).status_code == 429
    assert proxy.get("/v1/data", headers={**H, "X-Forwarded-For": "203.0.113.2"}).status_code == 200
    # A direct caller cannot choose its bucket by sending the header.
    direct = make_app(config=SecurityConfig(rate_ip=RateSpec(1, 60), rate_key=None)).client("198.51.100.5")
    assert direct.get("/v1/data", headers={**H, "X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert direct.get("/v1/data", headers={**H, "X-Forwarded-For": "2.2.2.2"}).status_code == 429


# --------------------------------------------------------------------------- research limits


def test_research_posts_are_limited_polling_is_not(make_app: Callable[..., Harness]) -> None:
    h = make_app(
        config=SecurityConfig(
            rate_ip=None,
            rate_key=None,
            rate_research_key=RateSpec(2, 3600),
            rate_research_ip=None,
            rate_research_global=None,
        )
    )
    c = h.client()
    assert c.post("/v1/research", json={}, headers=H).status_code == 202
    assert (
        c.post("/v1/research", json={}, headers=H).status_code == 202
    )  # the hook inside the route does not charge twice
    limited = c.post("/v1/research", json={}, headers=H)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    assert [c.get("/v1/research/abc", headers=H).status_code for _ in range(20)] == [200] * 20
    assert c.post("/v1/research", json={}, headers={"X-API-Key": KEY2}).status_code == 202


def test_research_global_cap_cannot_be_exhausted_by_anonymous_callers(make_app: Callable[..., Harness]) -> None:
    cfg = SecurityConfig(
        rate_ip=None,
        rate_key=None,
        rate_research_key=None,
        rate_research_ip=None,
        rate_research_global=RateSpec(2, 86400),
    )
    c = make_app(config=cfg).client()
    assert [c.post("/v1/research", json={}).status_code for _ in range(5)] == [401] * 5  # nothing was charged
    assert c.post("/v1/research", json={}, headers=H).status_code == 202
    assert c.post("/v1/research", json={}, headers={"X-API-Key": KEY2}).status_code == 202
    assert c.post("/v1/research", json={}, headers=H).status_code == 429  # the shared daily cap is used up


def test_check_research_limits_hook_charges_the_extra_cost(make_app: Callable[..., Harness]) -> None:
    h = make_app(
        config=SecurityConfig(
            rate_ip=None,
            rate_key=None,
            rate_research_key=RateSpec(4, 3600),
            rate_research_ip=None,
            rate_research_global=None,
        )
    )
    c = h.client()
    assert c.post("/v1/research/deep", json={}, headers=H).status_code == 202  # middleware 1 + hook 2 = 3 of 4
    assert c.post("/v1/research", json={}, headers=H).status_code == 202  # 4 of 4
    assert c.post("/v1/research", json={}, headers=H).status_code == 429
    assert h.log[-1]["limited_by"] == "research_key"


def test_hook_is_a_no_op_without_security_installed() -> None:
    app = FastAPI()

    @app.post("/v1/research")
    def research(request: Request) -> dict[str, bool]:
        check_research_limits(request, cost=100)
        return {"ok": True}

    assert TestClient(app).post("/v1/research").status_code == 200


# --------------------------------------------------------------------------- body limits


def test_body_size_limits(make_app: Callable[..., Harness]) -> None:
    h = make_app(config=SecurityConfig(max_body_bytes=1000, max_body_bytes_research=5000, rate_ip=None, rate_key=None))
    c = h.client()
    assert c.post("/v1/echo", content=b"x" * 1000, headers=H).json() == {"bytes": 1000}
    too_big = c.post("/v1/echo", content=b"x" * 1001, headers=H)
    assert too_big.status_code == 413 and "too large" in too_big.json()["detail"]
    assert c.post("/v1/research", content=b"x" * 4000, headers=H).status_code == 202  # research allows more
    assert c.post("/v1/research", content=b"x" * 5001, headers=H).status_code == 413

    # No Content-Length (chunked upload): the stream itself is counted.
    def chunks(n: int, size: int) -> Iterator[bytes]:
        for _ in range(n):
            yield b"y" * size

    assert c.post("/v1/echo", content=chunks(4, 200), headers=H).json() == {"bytes": 800}
    streamed = c.post("/v1/echo", content=chunks(20, 200), headers=H)
    assert streamed.status_code == 413
    assert any(r.get("limited_by") == "body_size" for r in h.log)


# --------------------------------------------------------------------------- CORS


def test_cors_default_none_and_configured_origins(make_app: Callable[..., Harness]) -> None:
    default = make_app().client()
    r = default.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    pre = default.options(
        "/v1/data", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in pre.headers

    cfg = SecurityConfig(cors_origins=("https://app.example.org",))
    c = make_app(config=cfg).client()
    ok = c.get("/health", headers={"Origin": "https://app.example.org"})
    assert ok.headers["access-control-allow-origin"] == "https://app.example.org"
    assert "access-control-allow-credentials" not in ok.headers
    assert "access-control-allow-origin" not in c.get("/health", headers={"Origin": "https://evil.example"}).headers
    pre = c.options(
        "/v1/data",
        headers={
            "Origin": "https://app.example.org",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-api-key",
        },
    )
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == "https://app.example.org"
    # Errors raised by the limiter carry the CORS headers too, so a browser can read the 429.
    limited = make_app(
        config=SecurityConfig(cors_origins=("https://app.example.org",), rate_ip=RateSpec(1, 60))
    ).client()
    limited.get("/v1/data", headers=H)
    blocked = limited.get("/v1/data", headers={**H, "Origin": "https://app.example.org"})
    assert blocked.status_code == 429 and blocked.headers["access-control-allow-origin"] == "https://app.example.org"


# --------------------------------------------------------------------------- access log


def test_access_log_is_structured_and_never_holds_secrets(make_app: Callable[..., Harness]) -> None:
    h = make_app()
    c = h.client()
    secret_query = "q=my-confidential-client-matter&api_key=SECRETKEYVALUE"
    c.post(f"/v1/echo?{secret_query}", content=b"BODY-WITH-CASE-FACTS", headers={**H, "User-Agent": "tester/1"})
    c.get("/v1/data?" + secret_query, headers={"X-API-Key": "wrong-secret-key-value"})
    c.get("/missing")
    raw = "\n".join(h.lines)
    for forbidden in (
        "my-confidential",
        "SECRETKEYVALUE",
        "BODY-WITH-CASE-FACTS",
        KEY,
        "wrong-secret-key-value",
        "q=",
        "?",
    ):
        assert forbidden not in raw
    first, second, third = h.log
    assert first["event"] == "access" and first["method"] == "POST" and first["path"] == "/v1/echo"
    assert first["status"] == 200 and first["principal"] == key_fingerprint(KEY) and first["user_agent"] == "tester/1"
    assert first["bytes_in"] == len(b"BODY-WITH-CASE-FACTS") and first["bytes_out"] > 0 and first["duration_ms"] >= 0
    assert len(first["request_id"]) == 32 and first["level"] == "info"
    assert second["status"] == 401 and second["principal"] is None and second["level"] == "warning"
    assert third["status"] == 404 and third["client_ip"] == "testclient"
    assert all(r["ts"].endswith("+00:00") for r in h.log)


def test_access_log_records_server_errors_and_can_be_disabled(make_app: Callable[..., Harness]) -> None:
    h = make_app()

    @h.app.get("/boom")
    def boom() -> None:
        raise ValueError("secret detail must not be logged")

    with pytest.raises(ValueError, match="secret detail"):
        h.client().get("/boom?x=1")
    entry = h.log[-1]
    assert entry["status"] == 500 and entry["level"] == "error" and entry["error"] == "ValueError"
    assert "secret detail" not in h.lines[-1]
    h.lines.clear()
    h.client().get("/health")
    assert h.lines == []  # successful liveness probes are not logged
    quiet = make_app(config=SecurityConfig(access_log=False))
    quiet.client().get("/v1/data", headers=H)
    assert quiet.lines == []


def test_uvicorn_access_filter_drops_query_strings() -> None:
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', None, None)
    record.args = ("1.2.3.4:5", "GET", "/v1/search?q=client+secret", "1.1", 200)
    assert security._StripQueryFilter().filter(record)
    assert record.getMessage() == '1.2.3.4:5 - "GET /v1/search HTTP/1.1" 200'


# --------------------------------------------------------------------------- /ready


def test_ready_reports_unreachable_database_without_details(make_app: Callable[..., Harness]) -> None:
    r = make_app().client().get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready" and body["checks"] == {"database": {"ok": False, "error": "unreachable"}}
    assert "127.0.0.1" not in r.text and "ulis:ulis" not in r.text


@pytest.mark.db
def test_ready_checks_database_migrations_and_catalog(
    make_app: Callable[..., Harness],
    db_url: str,
    ingested: IngestionReport,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = make_app(db_url=db_url).client()
    r = c.get("/ready")
    assert r.status_code == 200, r.text
    checks = r.json()["checks"]
    assert r.json()["status"] == "ready" and all(v["ok"] for v in checks.values())
    assert checks["migrations"]["current"] == checks["migrations"]["head"]
    assert checks["catalog"]["jurisdictions"] > 0 and checks["catalog"]["sources"] > 0
    # Migrations behind the code: not ready.
    monkeypatch.setattr(security, "migration_heads", lambda: frozenset({"9999"}))
    behind = c.get("/ready")
    assert behind.status_code == 503 and behind.json()["checks"]["migrations"]["ok"] is False
    assert behind.json()["checks"]["database"]["ok"] is True
    assert c.get("/health").status_code == 200  # liveness is independent


@pytest.mark.db
def test_the_real_app_exposes_ready_and_keeps_its_contract(
    db_url: str, catalog: Catalog, store_dir: Path, ingested: IngestionReport
) -> None:
    settings = replace(
        load_settings(), database_url=db_url, object_store_dir=store_dir, environment="test", api_keys=frozenset({KEY})
    )
    with TestClient(create_app(settings, catalog)) as c:
        assert c.get("/ready").status_code == 200
        assert c.get("/health").json()["status"] == "ok"
        assert c.get("/v1/jurisdictions").status_code == 401
        assert c.get("/v1/jurisdictions", headers=H).status_code == 200
        assert c.get("/v1/search", params={"q": "x" * 501}, headers=H).status_code == 422
