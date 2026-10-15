"""The whole-collection Federal Register source: batched listings, robust fetching, off-peak hours, the manifest.

The Register's API is replaced by a stub that answers from the real API responses kept as fixtures
(tests/fixtures/au_federal_register); nothing here touches the network.
"""

from __future__ import annotations

import gzip
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.au_federal_register import FederalRegisterConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

API = REPO_ROOT / "tests" / "fixtures" / "au_federal_register" / "cache" / "api.prod.legislation.gov.au"
MANIFEST = REPO_ROOT / "config" / "manifests" / "au-federal-register.yaml"
AIA, PRIVACY = "C1901A00002", "C2004A03712"
NOW = "2026-10-15T09:00:00+00:00"


def _json(path: Path) -> Any:
    return json.loads(gzip.decompress(path.with_name(path.name + ".gz").read_bytes()))


def _stub(monkeypatch: pytest.MonkeyPatch, fail: Callable[[str], str | None] | None = None) -> list[str]:
    """Replace ``PoliteHttp.get`` with answers built from the fixtures. Returns the URLs requested.

    A title without fixtures gets a synthetic latest version and EPUB-less document row copied from the Acts
    Interpretation Act, so that many titles can be listed; ``fail(url)`` returns an error text to raise for a URL.
    """
    requested: list[str] = []
    aia_version = next(v for v in _json(API / AIA / "versions-p0.json")["value"] if v["isLatest"])
    aia_doc = next(
        d for d in _json(API / AIA / "documents-p0.json")["value"] if d["registerId"] == aia_version["registerId"]
    )

    def versions_of(title: str) -> list[dict[str, Any]]:
        if (API / title / "versions-p0.json.gz").exists():
            return _json(API / title / "versions-p0.json")["value"]
        return [{**aia_version, "titleId": title, "registerId": f"C2026C{title[-5:]}"}]

    def documents_of(title: str) -> list[dict[str, Any]]:
        if (API / title / "documents-p0.json.gz").exists():
            return _json(API / title / "documents-p0.json")["value"]
        return [{**aia_doc, "titleId": title, "registerId": f"C2026C{title[-5:]}"}]

    def get(self: PoliteHttp, url: str, cache_key: str | None = None, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
        requested.append(url)
        if fail and (error := fail(url)):
            raise FetchError(error)
        meta = {"url": url, "final_url": url, "status": 200, "retrieved_at": NOW}
        if m := re.search(r"/Versions\?\$filter=titleId%20in%20\(([^)]*)\)%20and%20isLatest%20eq%20true", url):
            titles = re.findall(r"C\d{4}[A-Z]\d{5}", m.group(1))
            rows = [v for t in titles for v in versions_of(t) if v["isLatest"]]
            return json.dumps({"value": rows}).encode(), meta
        if m := re.search(r"/Documents\?\$filter=registerId%20in%20\(([^)]*)\)%20and", url):
            wanted = set(re.findall(r"C\d{4}C\d{5}", m.group(1)))
            rows = [d for t in (AIA, PRIVACY) for d in documents_of(t) if d["registerId"] in wanted]
            matched = {d["registerId"] for d in rows}
            for r in sorted(wanted - matched):  # a synthetic title: its register id ends with the title's number
                rows += [d for d in documents_of(f"C2026A{r[-5:]}") if d["registerId"] == r]
            return json.dumps({"value": rows}).encode(), meta
        if m := re.search(r"/(Versions|Documents)\?\$filter=titleId%20eq%20'(C\d{4}[A-Z]\d{5})'", url):
            rows = versions_of(m.group(2)) if m.group(1) == "Versions" else documents_of(m.group(2))
            return json.dumps({"value": rows}).encode(), meta
        if m := re.search(r"/documents\(titleid='(C\d{4}[A-Z]\d{5})',start=([\d-]+),", url):
            path = API / m.group(1) / f"{m.group(2)}_r0.json"
            if path.with_name(path.name + ".gz").exists():
                return gzip.decompress(path.with_name(path.name + ".gz").read_bytes()), meta
            return b'{"synthetic": true}', meta
        raise AssertionError(f"unexpected request {url}")

    monkeypatch.setattr(PoliteHttp, "get", get)
    return requested


def _connector(tmp_path: Path, works: list[dict[str, Any]], **extra: Any) -> FederalRegisterConnector:
    manifest = {
        "schema_version": 1,
        "source_id": "au-federal-register",
        "api_base": "https://api.prod.legislation.gov.au/v1",
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 10,
        "works": works,
        **extra,
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return FederalRegisterConnector(path, "au-federal-register", tmp_path / "cache", sleep=lambda s: None)


def test_batched_listings_cost_one_request_per_title(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requested = _stub(monkeypatch)
    conn = _connector(tmp_path, [{"title_id": AIA, "history": False}, {"title_id": PRIVACY}], batch_listings=True)
    docs = list(conn.fetch())
    assert [d.external_id for d in docs] == ["frl:C1901A00002@2026-03-28#r0", "frl:C2004A03712@2026-06-04#r0"]
    assert docs[1].metadata["register_id"] == "C2026C00227" and docs[1].metadata["end"] == "2026-12-10"
    # two listings for both titles, then one document each: 4 requests, not the 6 of per-title listings
    assert len(requested) == 4
    assert f"Versions?$filter=titleId%20in%20('{AIA}','{PRIVACY}')%20and%20isLatest%20eq%20true" in requested[0]
    assert "Documents?$filter=registerId%20in%20(" in requested[1]
    assert requested[2].startswith("https://api.prod.legislation.gov.au/v1/documents(titleid='C1901A00002'")
    assert conn.fetch_errors == []


def test_titles_are_listed_in_batches_of_forty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requested = _stub(monkeypatch)
    ids = [f"C2026A{n:05d}" for n in range(1, 83)]
    conn = _connector(tmp_path, [{"title_id": t, "history": False} for t in ids], batch_listings=True)
    docs = list(conn.fetch())
    assert len(docs) == 82
    listings = [u for u in requested if "/Versions?" in u or "/Documents?" in u]
    assert len(listings) == 6  # three batches (40, 40, 2) of two listings each
    assert listings[0].count("'C2026A") == 40 and listings[4].count("'C2026A") == 2


def test_history_titles_are_listed_per_title_even_in_batch_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested = _stub(monkeypatch)
    conn = _connector(
        tmp_path, [{"title_id": AIA, "history": True, "history_since": "2026-01-01"}], batch_listings=True
    )
    list(conn.fetch())
    assert "titleId%20eq%20'C1901A00002'" in requested[0] and "titleId%20eq%20'C1901A00002'" in requested[1]


def test_a_title_without_a_registered_version_is_reported_and_the_run_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(PoliteHttp, "get", lambda self, url, cache_key=None, headers=None: (b'{"value": []}', {}))
    conn = _connector(tmp_path, [{"title_id": AIA, "history": False}, {"title_id": PRIVACY}], batch_listings=True)
    assert list(conn.fetch()) == []
    assert conn.fetch_errors == [
        "frl:C1901A00002: no registered version listed (is the title in force?)",
        "frl:C2004A03712: no registered version listed (is the title in force?)",
    ]


def test_one_failing_document_is_recorded_a_refusal_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_aia(url: str) -> str | None:
        return f"{url}: HTTP 500 (not retried)" if "titleid='C1901A00002'" in url else None

    _stub(monkeypatch, fail_aia)
    conn = _connector(tmp_path, [{"title_id": AIA, "history": False}, {"title_id": PRIVACY}], batch_listings=True)
    docs = list(conn.fetch())
    assert [d.metadata["title_id"] for d in docs] == [PRIVACY]
    assert (
        len(conn.fetch_errors) == 1
        and conn.fetch_errors[0].startswith("frl:C1901A00002:")
        and "HTTP 500" in conn.fetch_errors[0]
    )

    _stub(
        monkeypatch,
        lambda url: f"{url}: HTTP 403 (not retried: a refusal is respected)" if "documents(" in url else None,
    )
    conn = _connector(tmp_path / "r", [{"title_id": AIA, "history": False}, {"title_id": PRIVACY}], batch_listings=True)
    with pytest.raises(FetchError, match="HTTP 403"):
        list(conn.fetch())
    assert conn.fetch_errors == []

    _stub(monkeypatch, lambda url: f"{url}: HTTP 500" if "documents(" in url else None)
    ids = [f"C2026A{n:05d}" for n in range(1, 9)]
    conn = _connector(tmp_path / "f", [{"title_id": t, "history": False} for t in ids], batch_listings=True)
    with pytest.raises(FetchError, match="stopping after 5 failures in a row"):
        list(conn.fetch())
    assert len(conn.fetch_errors) == 5


def test_listing_continues_with_skip_because_the_next_link_drops_the_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def get(self: PoliteHttp, url: str, cache_key: str | None = None, headers: Any = None):  # type: ignore[no-untyped-def]
        seen.append(url)
        meta = {"url": url, "status": 200, "retrieved_at": NOW}
        if url.endswith("&$skip=2"):
            return json.dumps({"value": [{"n": 3}]}).encode(), meta
        link = "https://api.prod.legislation.gov.au/v1/Versions?$select=titleId&$orderby=start&$skip=2"  # no $filter
        return json.dumps({"value": [{"n": 1}, {"n": 2}], "@odata.nextLink": link}).encode(), meta

    monkeypatch.setattr(PoliteHttp, "get", get)
    conn = _connector(tmp_path, [{"title_id": AIA}])
    manifest = conn._manifest()  # noqa: SLF001
    http = conn._http(manifest)  # noqa: SLF001
    rows = conn.versions(manifest, http, AIA)
    assert [r["n"] for r in rows] == [1, 2, 3]
    assert len(seen) == 2 and seen[0].endswith("&$orderby=start") and seen[1] == seen[0] + "&$skip=2"
    assert all("titleId%20eq%20'C1901A00002'" in u for u in seen)  # the filter is on every page


def test_requests_wait_for_the_end_of_the_australian_working_day(tmp_path: Path) -> None:
    sleeps: list[float] = []
    clock = [datetime(2026, 10, 15, 2, 0, tzinfo=UTC)]  # 12:00 at UTC+10

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] = datetime(2026, 10, 15, 10, 0, 30, tzinfo=UTC)  # 20:00:30 at UTC+10

    window = {"utc_offset": 10, "start": 8, "end": 20}
    conn = _connector(tmp_path, [{"title_id": AIA}], avoid_hours=window)
    conn._sleep = sleep  # noqa: SLF001
    conn._now = lambda: clock[0]  # noqa: SLF001
    manifest = conn._manifest()  # noqa: SLF001
    conn._wait_off_peak(manifest)  # noqa: SLF001
    assert sleeps == [8 * 3600.0]  # until 20:00 local time
    conn._wait_off_peak(manifest)  # noqa: SLF001 - now outside the window: no wait
    assert sleeps == [8 * 3600.0]
    clock[0] = datetime(2026, 10, 14, 23, 30, tzinfo=UTC)  # 09:30 the next morning at UTC+10
    conn._wait_off_peak(manifest)  # noqa: SLF001
    assert sleeps[-1] == 10.5 * 3600
    clock[0] = datetime(2026, 10, 15, 11, 0, tzinfo=UTC)  # 21:00 at UTC+10
    conn._wait_off_peak(manifest)  # noqa: SLF001
    assert len(sleeps) == 2
    # without the option nothing waits
    plain = _connector(tmp_path / "p", [{"title_id": AIA}])
    plain._now = lambda: datetime(2026, 10, 15, 2, 0, tzinfo=UTC)  # noqa: SLF001
    plain._wait_off_peak(plain._manifest())  # noqa: SLF001


def test_manifest_checks(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="listed twice"):
        _connector(tmp_path, [{"title_id": AIA}, {"title_id": AIA}])._manifest()  # noqa: SLF001
    for bad in (
        {"utc_offset": 10, "start": 20, "end": 8},
        {"start": 8, "end": 20},
        {"utc_offset": "10", "start": 8, "end": 20},
    ):
        with pytest.raises(ManifestError, match="avoid_hours"):
            _connector(tmp_path / "x", [{"title_id": AIA}], avoid_hours=bad)._manifest()  # noqa: SLF001


def test_committed_manifest_is_every_principal_act_in_force() -> None:
    """The generated manifest (scripts/build_frl_manifest.py) passes the connector's own validation."""
    conn = FederalRegisterConnector(MANIFEST, "au-federal-register", Path("/nonexistent"))
    manifest = conn._manifest()  # noqa: SLF001 - the connector's own validation of the committed manifest
    works = manifest["works"]
    assert len(works) > 700 and all("history" in w for w in works)
    assert manifest["batch_listings"] is True and manifest["avoid_hours"] == {"utc_offset": 10, "start": 8, "end": 20}
    flags = {w["title_id"]: w["history"] for w in works}
    assert [t for t, h in flags.items() if h] == ["C2004Q00685", "C1901A00002", "C2004A00553", "C2004A02944"]
    for sample in ("C1914A00012", PRIVACY, "C2004A00275", "C2004A04868"):
        assert flags[sample] is False
    assert not any(w["title_id"].startswith("F") for w in works)  # no legislative instruments
    assert manifest["crawl_delay_seconds"] == 10
