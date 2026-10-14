"""API hardening for a single-VPS deployment.

Everything here is plain ASGI/stdlib and is wired into the application by one call,
:func:`install_security`.  It provides

* constant-time API-key matching over several keys (:func:`match_api_key`);
* in-process token-bucket rate limiting per API key and per client IP, with stricter limits for
  ``/v1/research*`` and a global daily cap (:class:`TokenBucketLimiter`, :func:`check_research_limits`);
* request body size limits (413) and CORS restricted to configured origins (default: none);
* security headers and structured JSON access logs that never contain query strings, bodies or keys;
* ``/health`` (liveness, in ``app.py``) and ``/ready`` (database reachable, migrations at head, catalog loaded);
* a ``ULIS_ENV=production`` start-up check that refuses unsafe configuration.

Limits are kept in process memory: with N uvicorn workers every worker enforces them separately, so the
effective ceiling is up to N times the configured value.  They are an abuse and cost brake, not billing.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
import sys
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.engine import make_url
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ulis.settings import REPO_ROOT, Settings

PRODUCTION = "production"
RESEARCH_PREFIX = "/v1/research"
DOC_PATHS = frozenset({"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})
ACCESS_LOGGER_NAME = "ulis.access"

# Placeholders and defaults that must never reach production.
WEAK_SECRETS = frozenset(
    {
        "",
        "ulis",
        "password",
        "postgres",
        "changeme",
        "change-me",
        "change_me",
        "secret",
        "test",
        "test-key",
        "dev",
        "development",
        "example",
        "replace-me",
        "your-api-key",
        "admin",
    }
)
MIN_API_KEY_LENGTH = 24
MIN_DB_PASSWORD_LENGTH = 12


class ConfigurationError(RuntimeError):
    """The deployment configuration is unsafe or malformed: the API refuses to start."""


# --------------------------------------------------------------------------- API keys


def extract_api_key(headers: Mapping[str, str]) -> str | None:
    """The key a caller supplied: ``X-API-Key`` or ``Authorization: Bearer``."""
    supplied = headers.get("x-api-key")
    if not supplied:
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
    return supplied or None


def key_fingerprint(key: str) -> str:
    """A stable identifier for a key that is safe to log and store (never the key itself)."""
    return "key:" + hashlib.sha256(key.encode()).hexdigest()[:12]


def match_api_key(supplied: str | None, keys: Iterable[str]) -> str | None:
    """Return the configured key equal to ``supplied``, or None.

    Both sides are hashed first, so the comparison always runs on equal-length digests, and every
    configured key is compared (no early exit): the time taken reveals neither the length of a key nor
    which key matched.
    """
    if not supplied:
        return None
    digest = hashlib.sha256(supplied.encode()).digest()
    matched: str | None = None
    for key in keys:
        if hmac.compare_digest(digest, hashlib.sha256(key.encode()).digest()):
            matched = key
    return matched


# --------------------------------------------------------------------------- rate limiting

_PERIODS = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "m": 60,
    "min": 60,
    "minute": 60,
    "h": 3600,
    "hour": 3600,
    "d": 86400,
    "day": 86400,
}
_OFF = frozenset({"", "0", "off", "none", "unlimited", "disabled"})


@dataclass(frozen=True)
class RateSpec:
    """``capacity`` requests per ``period`` seconds; a full bucket allows a burst of ``capacity``."""

    capacity: float
    period: float

    @property
    def rate(self) -> float:
        return self.capacity / self.period

    @classmethod
    def parse(cls, value: str | None) -> RateSpec | None:
        """``"120/min"``, ``"20/hour"``, ``"5/s"``; ``off`` (or empty, 0) disables the limit."""
        text_value = (value or "").strip().lower()
        if text_value in _OFF:
            return None
        count, sep, unit = text_value.partition("/")
        try:
            capacity = float(int(count))
            period = _PERIODS[unit.strip()] if sep else 0
        except (ValueError, KeyError):
            period = 0
            capacity = 0.0
        if capacity <= 0 or period <= 0:
            raise ValueError(f"invalid rate limit {value!r}: use e.g. '120/min', '20/hour', '300/day' or 'off'")
        return cls(capacity, float(period))


class TokenBucketLimiter:
    """Thread-safe in-process token buckets keyed by (name, identifier), bounded in memory."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, max_entries: int = 50_000):
        self._clock = clock
        self._max = max_entries
        self._buckets: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _state(self, name: str, ident: str, spec: RateSpec) -> list[float]:
        now = self._clock()
        bucket = (name, ident)
        state = self._buckets.get(bucket)
        if state is None:
            state = [spec.capacity, now]
            self._buckets[bucket] = state
            if len(self._buckets) > self._max:
                self._buckets.popitem(last=False)  # forget the least recently seen caller
        else:
            state[0] = min(spec.capacity, state[0] + (now - state[1]) * spec.rate)
            state[1] = now
            self._buckets.move_to_end(bucket)
        return state

    def acquire(self, name: str, ident: str, spec: RateSpec, cost: float = 1.0) -> int:
        """Take ``cost`` tokens. Returns 0 when allowed, else the seconds to wait (>= 1)."""
        cost = min(cost, spec.capacity)
        with self._lock:
            state = self._state(name, ident, spec)
            if state[0] >= cost:
                state[0] -= cost
                return 0
            return max(1, math.ceil((cost - state[0]) / spec.rate))

    def penalize(self, name: str, ident: str, spec: RateSpec, cost: float) -> None:
        """Take tokens unconditionally (down to ``-capacity``), e.g. after a failed authentication."""
        with self._lock:
            state = self._state(name, ident, spec)
            state[0] = max(-spec.capacity, state[0] - cost)


@dataclass(frozen=True)
class Limited:
    scope: str  # ip | key | research_key | research_ip | research_global
    retry_after: int


# --------------------------------------------------------------------------- configuration


def _parse_networks(value: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    nets = []
    for part in value.split(","):
        part = part.strip()
        if part:
            try:
                nets.append(ipaddress.ip_network(part, strict=False))
            except ValueError as exc:
                raise ConfigurationError(f"ULIS_TRUSTED_PROXIES: {part!r} is not an IP address or network") from exc
    return tuple(nets)


def _int_env(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer number of bytes, got {raw!r}") from exc
    if value < 1:
        raise ConfigurationError(f"{name} must be positive")
    return value


def _rate_env(environ: Mapping[str, str], name: str, default: str) -> RateSpec | None:
    try:
        return RateSpec.parse(environ.get(name, default))
    except ValueError as exc:
        raise ConfigurationError(f"{name}: {exc}") from exc


@dataclass(frozen=True)
class SecurityConfig:
    environment: str = "development"
    cors_origins: tuple[str, ...] = ()
    trusted_proxies: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()
    max_body_bytes: int = 262_144
    max_body_bytes_research: int = 1_048_576
    rate_key: RateSpec | None = RateSpec(600, 60)
    rate_ip: RateSpec | None = RateSpec(300, 60)
    rate_research_key: RateSpec | None = RateSpec(60, 3600)
    rate_research_ip: RateSpec | None = RateSpec(60, 3600)
    rate_research_global: RateSpec | None = RateSpec(300, 86400)
    # Extra tokens taken from the IP bucket for every 401 (slows key guessing).
    auth_failure_cost: float = 5.0
    access_log: bool = True
    enable_docs: bool = True

    @classmethod
    def from_env(cls, environment: str, environ: Mapping[str, str] | None = None) -> SecurityConfig:
        env = os.environ if environ is None else environ
        origins = tuple(o.strip().rstrip("/") for o in env.get("ULIS_CORS_ORIGINS", "").split(",") if o.strip())
        for origin in origins:
            if origin != "*" and not origin.startswith(("http://", "https://")):
                raise ConfigurationError(f"ULIS_CORS_ORIGINS: {origin!r} must be '*' or start with http:// or https://")
        docs_default = "0" if environment == PRODUCTION else "1"
        return cls(
            environment=environment,
            cors_origins=origins,
            trusted_proxies=_parse_networks(env.get("ULIS_TRUSTED_PROXIES", "")),
            max_body_bytes=_int_env(env, "ULIS_MAX_BODY_BYTES", 262_144),
            max_body_bytes_research=_int_env(env, "ULIS_MAX_BODY_BYTES_RESEARCH", 1_048_576),
            rate_key=_rate_env(env, "ULIS_RATE_LIMIT_KEY", "600/min"),
            rate_ip=_rate_env(env, "ULIS_RATE_LIMIT_IP", "300/min"),
            rate_research_key=_rate_env(env, "ULIS_RATE_LIMIT_RESEARCH_KEY", "60/hour"),
            rate_research_ip=_rate_env(env, "ULIS_RATE_LIMIT_RESEARCH_IP", "60/hour"),
            rate_research_global=_rate_env(env, "ULIS_RATE_LIMIT_RESEARCH_GLOBAL", "300/day"),
            access_log=env.get("ULIS_ACCESS_LOG", "1").strip().lower() not in {"0", "false", "off", "no"},
            enable_docs=env.get("ULIS_ENABLE_DOCS", docs_default).strip().lower() in {"1", "true", "on", "yes"},
        )


def production_problems(settings: Settings, config: SecurityConfig) -> list[str]:
    """Everything that makes this configuration unsafe for ``ULIS_ENV=production``."""
    problems: list[str] = []
    if not settings.api_keys:
        problems.append("ULIS_API_KEYS is empty: set at least one API key")
    for key in settings.api_keys:
        if key.lower() in WEAK_SECRETS or len(key) < MIN_API_KEY_LENGTH or len(set(key)) < 6:
            problems.append(
                f"an API key ({key_fingerprint(key)}) is a placeholder or shorter than {MIN_API_KEY_LENGTH} "
                "characters: generate keys with `openssl rand -hex 32`"
            )
    try:
        password = make_url(settings.database_url).password
    except Exception:  # sqlalchemy raises ArgumentError; never echo the URL (it holds the password)
        problems.append("ULIS_DATABASE_URL is not a valid database URL")
    else:
        if password is None or password.lower() in WEAK_SECRETS or len(password) < MIN_DB_PASSWORD_LENGTH:
            problems.append(
                "the database password is missing, a default or shorter than "
                f"{MIN_DB_PASSWORD_LENGTH} characters (set POSTGRES_PASSWORD to a random value)"
            )
    if "*" in config.cors_origins:
        problems.append("ULIS_CORS_ORIGINS must list explicit origins, not '*'")
    return problems


def enforce_production(settings: Settings, config: SecurityConfig) -> None:
    if settings.environment != PRODUCTION:
        return
    problems = production_problems(settings, config)
    if problems:
        raise ConfigurationError("refusing to start with ULIS_ENV=production: " + "; ".join(problems))


# --------------------------------------------------------------------------- client address


def _in_networks(address: str, networks: Iterable[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in net for net in networks)


def _normalise_ip(address: str) -> str:
    """IPv6 callers are limited per /64 so that rotating addresses inside one block do not evade limits."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address[:64]
    if isinstance(ip, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address) + "/64"
    return str(ip)


def client_ip(scope: Scope, trusted: Iterable[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> str:
    """The caller's address. ``X-Forwarded-For`` is honoured only when the connecting peer is a trusted proxy,
    and then the right-most address that is not itself a trusted proxy is used (earlier entries are forgeable)."""
    nets = tuple(trusted)
    client = scope.get("client")
    peer = client[0] if client else "unknown"
    if not nets or not _in_networks(peer, nets):
        return _normalise_ip(peer)
    forwarded = Headers(scope=scope).get("x-forwarded-for", "")
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    for hop in reversed(hops):
        if not _in_networks(hop, nets):
            try:
                ipaddress.ip_address(hop)
            except ValueError:
                break
            return _normalise_ip(hop)
    return _normalise_ip(peer)


# --------------------------------------------------------------------------- runtime


def _default_sink(line: str) -> None:
    logging.getLogger(ACCESS_LOGGER_NAME).info(line)


def _ensure_access_logger() -> None:
    logger = logging.getLogger(ACCESS_LOGGER_NAME)
    if not any(h.get_name() == "ulis-access" for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.set_name("ulis-access")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class _StripQueryFilter(logging.Filter):
    """Safety net: if uvicorn's own access log is left on, drop the query string from the request line."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (args[0], args[1], args[2].split("?", 1)[0], args[3], args[4])
        return True


@dataclass
class SecurityRuntime:
    config: SecurityConfig
    api_keys: frozenset[str]
    requires_api_key: bool
    limiter: TokenBucketLimiter = field(default_factory=TokenBucketLimiter)
    log_sink: Callable[[str], None] = _default_sink

    def identify(self, headers: Mapping[str, str]) -> str | None:
        """The caller's principal id, or None when no valid key was supplied."""
        if not self.requires_api_key:
            return "anonymous-dev"
        key = match_api_key(extract_api_key(headers), self.api_keys)
        return key_fingerprint(key) if key else None

    def _first_exceeded(
        self, buckets: Iterable[tuple[str, str | None, RateSpec | None]], cost: float
    ) -> Limited | None:
        for name, ident, spec in buckets:
            if ident and spec is not None and (wait := self.limiter.acquire(name, ident, spec, cost)):
                return Limited(name, wait)
        return None

    def check_general(self, principal: str | None, ip: str) -> Limited | None:
        cfg = self.config
        return self._first_exceeded((("ip", ip, cfg.rate_ip), ("key", principal, cfg.rate_key)), 1.0)

    def charge_research(self, principal: str | None, ip: str, cost: float = 1.0) -> Limited | None:
        cfg = self.config
        buckets = (
            ("research_key", principal, cfg.rate_research_key),
            ("research_ip", ip, cfg.rate_research_ip),
            ("research_global", "all", cfg.rate_research_global),
        )
        return self._first_exceeded(buckets, cost)

    def log(self, event: str, level: str = "info", **fields: Any) -> None:
        record = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "level": level, "event": event, **fields}
        self.log_sink(json.dumps(record, separators=(",", ":"), default=str))


def check_research_limits(request: Request, cost: int = 1) -> None:
    """Hook for the research endpoints: raise HTTP 429 (with ``Retry-After``) when the caller's research
    allowance, the per-IP allowance or the global daily cap is used up.

    ``POST /v1/research*`` is already charged one unit by the middleware, so a plain call is a no-op;
    pass a larger ``cost`` (e.g. 3 for ``depth="deep"``) to charge the difference.  Without
    :func:`install_security` there are no limits and the call does nothing.
    """
    runtime: SecurityRuntime | None = getattr(request.app.state, "security", None)
    if runtime is None:
        return
    state = request.scope.setdefault("state", {})
    extra = cost - int(state.get("research_charged", 0))
    if extra <= 0:
        return
    principal = state.get("principal_id") or runtime.identify(request.headers)
    if principal is None:  # unauthenticated: the route's auth dependency answers 401; never spend shared allowances
        return
    ip = state.get("client_ip") or client_ip(request.scope, runtime.config.trusted_proxies)
    limited = runtime.charge_research(principal, ip, float(extra))
    state["research_charged"] = cost
    if limited is not None:
        state["limited_by"] = limited.scope
        raise HTTPException(429, "research rate limit exceeded", headers={"Retry-After": str(limited.retry_after)})


# --------------------------------------------------------------------------- middleware (pure ASGI)


def _is_research(path: str) -> bool:
    return path.startswith(RESEARCH_PREFIX)


async def _reject(scope: Scope, receive: Receive, send: Send, status: int, detail: str, **headers: str) -> None:
    await JSONResponse({"detail": detail}, status_code=status, headers=headers)(scope, receive, send)


class AccessLogMiddleware:
    """Outermost: request id, client address, one structured JSON line per request.

    The line holds method, path (never the query string), status, timings, sizes, the caller's key
    fingerprint and address.  Request and response bodies, query strings and credentials are never read.
    """

    def __init__(self, app: ASGIApp, runtime: SecurityRuntime):
        self.app = app
        self.runtime = runtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rt = self.runtime
        started = time.perf_counter()
        headers = Headers(scope=scope)
        inbound = headers.get("x-request-id", "")
        valid_id = 8 <= len(inbound) <= 64 and inbound.isascii() and inbound.replace("-", "").replace("_", "").isalnum()
        request_id = inbound if valid_id else uuid.uuid4().hex
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["client_ip"] = client_ip(scope, rt.config.trusted_proxies)
        status = 500
        sent = 0
        error: str | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status, sent
            if message["type"] == "http.response.start":
                status = message["status"]
                kept = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                kept.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": kept}
            elif message["type"] == "http.response.body":
                sent += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except asyncio.CancelledError:
            status, error = 499, "client_closed"
            raise
        except Exception as exc:
            status, error = 500, type(exc).__name__
            raise
        finally:
            # Successful liveness probes (the container healthcheck, every 30 s) would only add noise.
            quiet = scope["path"] == "/health" and status == 200
            if rt.config.access_log and not quiet:
                length = headers.get("content-length", "")
                record: dict[str, Any] = {
                    "request_id": request_id,
                    "method": scope["method"],
                    "path": scope["path"][:300],
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "client_ip": state["client_ip"],
                    "principal": state.get("principal_id"),
                    "bytes_in": int(length) if length.isdigit() else None,
                    "bytes_out": sent,
                    "user_agent": headers.get("user-agent", "")[:120] or None,
                }
                if state.get("limited_by"):
                    record["limited_by"] = state["limited_by"]
                if error:
                    record["error"] = error
                rt.log("access", "error" if status >= 500 else "warning" if status >= 400 else "info", **record)


class SecurityHeadersMiddleware:
    """Adds response headers that are not already set by the application."""

    API_HEADERS = (
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"cache-control", b"no-store"),
        (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
        (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    )

    def __init__(self, app: ASGIApp, runtime: SecurityRuntime):
        self.app = app
        self.hsts = runtime.config.environment == PRODUCTION

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_docs = scope["path"] in DOC_PATHS  # the interactive docs need scripts: no CSP there

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                extra = list(self.API_HEADERS)
                if self.hsts:
                    extra.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                for name, value in extra:
                    if name not in present and not (is_docs and name == b"content-security-policy"):
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


class LimitsMiddleware:
    """Rate limits (per IP, per key, research) and request body size limits."""

    def __init__(self, app: ASGIApp, runtime: SecurityRuntime):
        self.app = app
        self.runtime = runtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rt = self.runtime
        cfg = rt.config
        state = scope.setdefault("state", {})
        path, method = scope["path"], scope["method"]
        headers = Headers(scope=scope)
        ip = state.get("client_ip") or client_ip(scope, cfg.trusted_proxies)

        if path != "/health":  # liveness probes are never limited
            principal = rt.identify(headers)
            state["principal_id"] = principal
            limited = rt.check_general(principal, ip)
            # Only authenticated callers spend the research allowance, so anonymous traffic cannot
            # exhaust the shared daily cap.
            if limited is None and principal and method == "POST" and _is_research(path):
                limited = rt.charge_research(principal, ip)
                state["research_charged"] = 1
            if limited is not None:
                state["limited_by"] = limited.scope
                await _reject(
                    scope, receive, send, 429, "rate limit exceeded", **{"Retry-After": str(limited.retry_after)}
                )
                return

        max_bytes = cfg.max_body_bytes_research if _is_research(path) else cfg.max_body_bytes
        declared = headers.get("content-length")
        if declared is not None:
            if not declared.isdigit():
                await _reject(scope, receive, send, 400, "invalid Content-Length")
                return
            if int(declared) > max_bytes:
                state["limited_by"] = "body_size"
                await _reject(scope, receive, send, 413, f"request body too large (limit {max_bytes} bytes)")
                return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:  # chunked uploads without Content-Length
                    state["limited_by"] = "body_size"
                    raise HTTPException(413, f"request body too large (limit {max_bytes} bytes)")
            return message

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] == 401 and cfg.rate_ip is not None:
                rt.limiter.penalize("ip", ip, cfg.rate_ip, cfg.auth_failure_cost)
            await send(message)

        await self.app(scope, limited_receive, send_wrapper)


# --------------------------------------------------------------------------- readiness


@lru_cache(maxsize=1)
def migration_heads() -> frozenset[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return frozenset(ScriptDirectory.from_config(cfg).get_heads())


def readiness(app: FastAPI) -> tuple[bool, dict[str, Any]]:
    """Is this instance able to serve? Database reachable, migrations at head, catalog loaded."""
    st = app.state.ulis
    checks: dict[str, Any] = {}
    try:
        with st.session_factory() as session:
            session.execute(text("SET LOCAL statement_timeout = 3000"))
            session.execute(text("SELECT 1"))
            checks["database"] = {"ok": True}
            try:
                current = set(session.execute(text("SELECT version_num FROM alembic_version")).scalars())
                heads = migration_heads()
                checks["migrations"] = {"ok": current == heads, "current": sorted(current), "head": sorted(heads)}
            except Exception:
                session.rollback()
                checks["migrations"] = {"ok": False, "error": "alembic_version unreadable: run `ulis.cli migrate`"}
            try:
                session.execute(text("SET LOCAL statement_timeout = 3000"))
                in_db_j = session.execute(text("SELECT count(*) FROM jurisdictions")).scalar_one()
                in_db_s = session.execute(text("SELECT count(*) FROM sources")).scalar_one()
                want_j, want_s = len(st.catalog.jurisdictions.profiles), len(st.catalog.sources.sources)
                checks["catalog"] = {
                    "ok": want_j > 0 and want_s > 0 and in_db_j >= want_j and in_db_s >= want_s,
                    "jurisdictions": in_db_j,
                    "sources": in_db_s,
                    "hint": "run `ulis.cli sync-catalog` if this is not ok",
                }
            except Exception:
                session.rollback()
                checks["catalog"] = {"ok": False, "error": "catalog tables unreadable: run `ulis.cli migrate`"}
    except Exception:
        checks["database"] = {"ok": False, "error": "unreachable"}
    return all(c.get("ok") for c in checks.values()) and "database" in checks, checks


# --------------------------------------------------------------------------- wiring


def install_security(app: FastAPI, settings: Settings, config: SecurityConfig | None = None) -> SecurityRuntime:
    """Add limits, headers, CORS, access logging and ``/ready`` to ``app``; enforce production rules."""
    config = config or SecurityConfig.from_env(settings.environment)
    enforce_production(settings, config)
    _ensure_access_logger()
    uvicorn_access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _StripQueryFilter) for f in uvicorn_access.filters):
        uvicorn_access.addFilter(_StripQueryFilter())

    runtime = SecurityRuntime(config=config, api_keys=settings.api_keys, requires_api_key=settings.requires_api_key)
    app.state.security = runtime
    if settings.environment == PRODUCTION and not config.trusted_proxies:
        runtime.log(
            "startup_warning",
            "warning",
            message="ULIS_TRUSTED_PROXIES is empty: behind a reverse proxy every caller shares the proxy's address "
            "and per-IP limits do not work",
        )
    if not config.enable_docs:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) not in DOC_PATHS]

    @app.get("/ready", include_in_schema=False)
    def ready() -> JSONResponse:
        ok, checks = readiness(app)
        return JSONResponse(
            {"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503
        )

    # Innermost first; the last one added is outermost.
    app.add_middleware(LimitsMiddleware, runtime=runtime)
    app.add_middleware(SecurityHeadersMiddleware, runtime=runtime)
    if config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.cors_origins),
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["X-API-Key", "Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["Retry-After", "X-Request-ID"],
            allow_credentials=False,
            max_age=600,
        )
    app.add_middleware(AccessLogMiddleware, runtime=runtime)
    return runtime
