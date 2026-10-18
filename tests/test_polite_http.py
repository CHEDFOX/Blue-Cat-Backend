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


# --------------------------------------------------------------------------- the delay rule


def test_robots_crawl_delay_reads_the_group_for_ulis_else_star() -> None:
    from ulis.sources.connectors.polite_http import robots_crawl_delay

    ua = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
    assert robots_crawl_delay("User-agent: *\nCrawl-delay: 5\nDisallow: */data.pdf\n", ua) == 5.0
    assert robots_crawl_delay("User-agent: *\nDisallow:\n", ua) is None
    assert robots_crawl_delay("", ua) is None
    assert robots_crawl_delay("<!doctype html><title>app</title>", ua) is None  # an SPA shell, not a robots file
    # Groups for other agents (www.riksdagen.se names three Claude crawlers) do not apply to ULIS.
    riksdagen = "sitemap:https://x/s.xml\nuser-agent:Claudebot\ncrawl-delay:1\nuser-agent:Claude-User\ncrawl-delay:1\n"
    assert robots_crawl_delay(riksdagen, ua) is None
    # A group naming ULIS wins over "*"; agents listed together share the group.
    both = "User-agent: *\nCrawl-delay: 2\n\nUser-agent: Other\nUser-agent: ULIS-research-bot\nCrawl-delay: 10\n"
    assert robots_crawl_delay(both, ua) == 10.0
    assert robots_crawl_delay("User-agent: *\nCrawl-delay: ten\n", ua) is None


def test_manifest_crawl_delay_allows_one_second_and_refuses_less() -> None:
    from ulis.sources.connectors.local_manifest import ManifestError
    from ulis.sources.connectors.polite_http import MIN_CRAWL_DELAY, manifest_crawl_delay

    assert MIN_CRAWL_DELAY == 1.0
    assert manifest_crawl_delay({}) == 5.0  # no value in the manifest: the old default
    assert manifest_crawl_delay({"crawl_delay_seconds": 1}) == 1.0
    assert manifest_crawl_delay({"crawl_delay_seconds": 10}) == 10.0
    for bad in (0.5, 0, -1, float("nan")):
        with pytest.raises(ManifestError, match="at least 1"):
            manifest_crawl_delay({"crawl_delay_seconds": bad})
    with pytest.raises(ManifestError, match="number"):
        manifest_crawl_delay({"crawl_delay_seconds": "fast"})
    # A connector may hold a publisher's stricter rule; it can never go under the project minimum.
    with pytest.raises(ManifestError, match="at least 5"):
        manifest_crawl_delay({"crawl_delay_seconds": 1}, minimum=5.0)
    with pytest.raises(ManifestError, match="at least 1"):
        manifest_crawl_delay({"crawl_delay_seconds": 0.5}, minimum=0.1)


class _Site:
    """A fake publisher: robots.txt (or 404) and a small document per path; records requests and pauses."""

    def __init__(self, robots: bytes | None):
        self.robots = robots
        self.requested: list[str] = []
        self.sleeps: list[float] = []

    def urlopen(self, request: Any, timeout: float) -> _Response:
        url = request.full_url
        self.requested.append(url)
        if url.endswith("/robots.txt"):
            if self.robots is None:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
            return _Response(self.robots)
        return _Response(b"<law/>")


def _client(tmp_path: Path, site: _Site, delay: float, **kwargs: Any) -> PoliteHttp:
    return PoliteHttp(
        tmp_path,
        "ULIS-research-bot/0.1 (+https://example.org)",
        allowed_hosts={"example.org"},
        crawl_delay=delay,
        sleep=site.sleeps.append,
        honour_robots_delay=True,
        **kwargs,
    )


def test_a_robots_crawl_delay_wins_over_a_shorter_manifest_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = _Site(b"User-agent: *\nCrawl-delay: 7\n")
    monkeypatch.setattr(polite_http.urllib.request, "urlopen", site.urlopen)
    http = _client(tmp_path, site, 1.0)
    http.get("https://example.org/a")
    http.get("https://example.org/b")
    # robots.txt is read once, before the first document; every pause after it is the publisher's 7 s, not 1 s.
    assert site.requested == ["https://example.org/robots.txt", "https://example.org/a", "https://example.org/b"]
    assert http.delay_for("example.org") == 7.0 and http.crawl_delay == 1.0
    assert len(site.sleeps) == 2 and all(6.5 < s <= 7.0 for s in site.sleeps)


def test_without_a_crawl_delay_the_manifest_delay_applies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for robots in (None, b"User-agent: *\nDisallow:\n"):  # no robots.txt (404), or one with no Crawl-delay
        site = _Site(robots)
        monkeypatch.setattr(polite_http.urllib.request, "urlopen", site.urlopen)
        cache = tmp_path / ("none" if robots is None else "empty")
        http = _client(cache, site, 1.0)
        http.get("https://example.org/a")
        http.get("https://example.org/b")
        assert http.delay_for("example.org") == 1.0
        assert len(site.sleeps) == 2 and all(0.5 < s <= 1.0 for s in site.sleeps)
    # A manifest delay longer than robots.txt's keeps its own: the delay is the larger of the two.
    site = _Site(b"User-agent: *\nCrawl-delay: 2\n")
    monkeypatch.setattr(polite_http.urllib.request, "urlopen", site.urlopen)
    http = _client(tmp_path / "longer", site, 5.0)
    assert http.load_robots_delay("example.org") == 2.0 and http.delay_for("example.org") == 5.0


def test_robots_is_read_afresh_each_run_and_not_at_all_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    site = _Site(b"User-agent: *\nCrawl-delay: 3\n")
    monkeypatch.setattr(polite_http.urllib.request, "urlopen", site.urlopen)
    _client(tmp_path, site, 1.0).get("https://example.org/a")
    # The publisher raises its Crawl-delay; the next run (a new client) sees it although robots.txt is cached.
    site.robots = b"User-agent: *\nCrawl-delay: 9\n"
    run2 = _client(tmp_path, site, 1.0)
    run2.get("https://example.org/b")
    assert site.requested.count("https://example.org/robots.txt") == 2 and run2.delay_for("example.org") == 9.0
    # Offline, from the cache: no request at all, robots.txt included.
    offline = _client(tmp_path, site, 1.0, offline=True)
    before = len(site.requested)
    assert offline.get("https://example.org/a") is not None and len(site.requested) == before


def test_a_refused_robots_txt_stops_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(request: Any, timeout: float) -> None:
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(polite_http.urllib.request, "urlopen", forbidden)
    http = _client(tmp_path, _Site(None), 1.0)
    with pytest.raises(FetchError, match="403"):
        http.get("https://example.org/a")
    assert http.requests_made == 1  # robots.txt only; the document was never asked for
