"""Shared polite fetcher: identification, host allow-list, cache, offline mode, refusals."""

from __future__ import annotations

import io
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from ulis.sources.connectors import polite_http
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp


class _Response(io.BytesIO):
    status = 200
    headers: dict[str, str] = {"Content-Type": "text/xml"}

    def geturl(self) -> str:
        return "https://example.org/a"

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: Any) -> None:
        pass


def test_requires_identification(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="user_agent"):
        PoliteHttp(tmp_path, "python-urllib", allowed_hosts={"example.org"})


def test_fetches_once_then_serves_from_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_urlopen(request: Any, timeout: float) -> _Response:
        calls.append(request.get_header("User-agent"))
        return _Response(b"<law/>")

    monkeypatch.setattr(polite_http.urllib.request, "urlopen", fake_urlopen)
    http = PoliteHttp(tmp_path, "ULIS-test (+https://example.org)", allowed_hosts={"example.org"}, sleep=lambda s: None)
    assert http.get("https://example.org/a?x=1")[0] == b"<law/>"  # type: ignore[index]
    assert http.get("https://example.org/a?x=1")[0] == b"<law/>"  # type: ignore[index]
    assert calls == ["ULIS-test (+https://example.org)"]
    offline = PoliteHttp(tmp_path, "ULIS-test (+https://example.org)", allowed_hosts={"example.org"}, offline=True)
    assert offline.get("https://example.org/a?x=1") is not None
    with pytest.raises(FetchError, match="offline"):
        offline.get("https://example.org/b")


def test_refuses_other_hosts_plain_http_and_respects_refusals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    http = PoliteHttp(tmp_path, "ULIS-test (+https://example.org)", allowed_hosts={"example.org"}, sleep=lambda s: None)
    with pytest.raises(FetchError, match="only https"):
        http.get("http://example.org/a")
    with pytest.raises(FetchError, match="only https"):
        http.get("https://elsewhere.org/a")

    def forbidden(request: Any, timeout: float) -> None:
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(polite_http.urllib.request, "urlopen", forbidden)
    with pytest.raises(FetchError, match="403"):
        http.get("https://example.org/c")
    assert http.requests_made == 1  # a refusal is not retried


def test_a_dropped_connection_is_retried_like_a_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that closes the connection with no response (http.client.RemoteDisconnected is no URLError and no
    TimeoutError) is waited out and asked again, so one hiccup does not end a run of thousands of requests."""
    import http.client

    attempts: list[int] = []

    def flaky(request: Any, timeout: float) -> _Response:
        attempts.append(1)
        if len(attempts) < 3:
            raise http.client.RemoteDisconnected("Remote end closed connection without response")
        return _Response(b"<law/>")

    monkeypatch.setattr(polite_http.urllib.request, "urlopen", flaky)
    pauses: list[float] = []
    http_ = PoliteHttp(tmp_path, "ULIS-test (+https://example.org)", allowed_hosts={"example.org"}, sleep=pauses.append)
    got = http_.get("https://example.org/a")
    assert got is not None and got[0] == b"<law/>" and len(attempts) == 3
    assert pauses and max(pauses) >= 2 * http_.crawl_delay  # backed off between the attempts

    def always(request: Any, timeout: float) -> None:
        raise http.client.RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr(polite_http.urllib.request, "urlopen", always)
    with pytest.raises(FetchError, match="closed connection"):
        PoliteHttp(
            tmp_path / "other", "ULIS-test (+https://example.org)", allowed_hosts={"example.org"}, sleep=lambda s: None
        ).get("https://example.org/b")
