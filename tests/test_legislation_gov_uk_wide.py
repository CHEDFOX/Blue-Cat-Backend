"""The whole-collection legislation.gov.uk source: regnal-year Acts, robust fetching, the committed manifest.

Real documents (Open Government Licence v3.0): the Clerk of the Council Act 1859 (`ukpga/Vict/22-23/1`) and the Burial
Act 1859 (`ukpga/Vict/22/1`) are both "1859 c. 1" - calendar year and chapter alone do not identify them.
"""

from __future__ import annotations

import gzip
import io
import re
import shutil
import urllib.error
from datetime import UTC, date, datetime
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors import legislation_gov_uk as conn_mod
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.legislation_gov_uk import FetchError, LegislationGovUkConnector, Refused
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.normalized import ParseSkipped
from ulis.sources.parsers.legislation_gov_uk import parse_legislation_gov_uk, resolve_uri
from ulis.storage.object_store import LocalObjectStore

FIX = REPO_ROOT / "tests" / "fixtures" / "legislation_gov_uk" / "cache" / "legislation.gov.uk" / "ukpga" / "Vict"
CLERK = FIX / "22-23" / "1"
BURIAL = FIX / "22" / "1"
CHEVENING = FIX.parent / "Eliz2" / "7-8" / "49"  # Chevening Estate Act 1959: a deed with repeated paragraph numbers
MANIFEST = REPO_ROOT / "config" / "manifests" / "gb-legislation-gov-uk.yaml"
MANIFEST_SECONDARY = REPO_ROOT / "config" / "manifests" / "gb-legislation-gov-uk-secondary.yaml"
MANIFEST_MADE = REPO_ROOT / "config" / "manifests" / "gb-legislation-gov-uk-secondary-made.yaml"


def _read(directory: Path) -> bytes:
    return gzip.decompress((directory / "current.akn.gz").read_bytes())


# --------------------------------------------------------------------------- parser


def test_regnal_year_acts_with_the_same_chapter_have_distinct_identities() -> None:
    clerk = parse_legislation_gov_uk(_read(CLERK))[0]
    burial = parse_legislation_gov_uk(_read(BURIAL))[0]
    assert clerk.work_uri == "/gb/ukpga/Vict/22-23/1" and burial.work_uri == "/gb/ukpga/Vict/22/1"
    assert clerk.work_uri != burial.work_uri
    # both are "1859 c. 1" on the site; the calendar year and number stay in the extension data
    assert (clerk.ext["year"], clerk.ext["number"]) == (1859, "1") == (burial.ext["year"], burial.ext["number"])
    assert clerk.work_title == "Clerk of the Council Act 1859" and burial.work_title == "Burial Act 1859"
    assert clerk.version_date == date(1991, 2, 1) and burial.version_date == date(1996, 4, 1)
    assert clerk.work_date == date(1859, 7, 21) and burial.work_date == date(1859, 3, 25)  # enactment (royal assent)
    assert [p.eid for p in burial.provisions] == ["section-1", "section-2"]
    assert burial.provisions[1].text.startswith("This Act shall be read together with the Burial Act 1857")


def test_regnal_year_uris_resolve_to_the_same_work_uris() -> None:
    assert resolve_uri("http://www.legislation.gov.uk/id/ukpga/Vict/22/1/section/2") == (
        "/gb/ukpga/Vict/22/1",
        "section-2",
    )
    assert resolve_uri("https://www.legislation.gov.uk/ukpga/Vict/22-23/1/1991-02-01") == (
        "/gb/ukpga/Vict/22-23/1",
        None,
    )
    assert resolve_uri("http://www.legislation.gov.uk/id/ukpga/1998/42/section/4/5") == (
        "/gb/ukpga/1998/42",
        "section-4-5",
    )
    assert resolve_uri("http://www.legislation.gov.uk/id/ukpga/Vict/not-a-number/1") is None


def test_paragraphs_sharing_an_eid_are_separate_provisions() -> None:
    """The deed in the Schedule of the Chevening Estate Act 1959 numbers its paragraphs several times over, so the site
    gives them the same eId; with the same dates they were read as two wordings of one provision, which the database
    refuses (a version ending on the day it starts)."""
    doc = parse_legislation_gov_uk(_read(CHEVENING))[0]
    eids = [p.eid for p in doc.provisions]
    assert doc.work_uri == "/gb/ukpga/Eliz2/7-8/49" and len(eids) == len(set(eids))
    first, second = (p for p in doc.provisions if p.eid in ("schedule-paragraph-1", "schedule-paragraph-1-dup2"))
    assert first.text != second.text and first.valid_from == second.valid_from
    assert first.text.startswith("The said Earl Stanhope") and second.text.startswith("In this Deed")


def test_a_document_with_no_provisions_is_skipped_not_an_error() -> None:
    empty = re.sub(rb"<body>.*</body>", b"<body/>", _read(CLERK), flags=re.S)
    with pytest.raises(ParseSkipped, match="no provisions"):
        parse_legislation_gov_uk(empty)


# --------------------------------------------------------------------------- connector


class _Response:
    def __init__(self, url: str, body: bytes):
        self.url, self.body, self.status = url, body, 200

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body

    def geturl(self) -> str:
        return self.url


def _stub_site(monkeypatch: pytest.MonkeyPatch, routes: dict[str, bytes | int]) -> list[str]:
    """Replace urlopen: a URL maps to its body or to an HTTP status code. Returns the list of URLs requested."""
    requested: list[str] = []

    def urlopen(request: Any, timeout: float = 0) -> _Response:
        url = request.full_url
        requested.append(url)
        answer = routes.get(url, 404)
        if isinstance(answer, int):
            raise urllib.error.HTTPError(url, answer, "stub", Message(), io.BytesIO(b""))
        return _Response(url, answer)

    monkeypatch.setattr(conn_mod.urllib.request, "urlopen", urlopen)
    return requested


def _connector(
    tmp_path: Path, paths: list[str], sleeps: list[float] | None = None, delay: int = 1
) -> LegislationGovUkConnector:
    manifest = {
        "schema_version": 1,
        "source_id": "gb-legislation-gov-uk",
        "base_url": "https://www.legislation.gov.uk",
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": delay,
        "works": [{"path": p, "history": False} for p in paths],
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    record = sleeps if sleeps is not None else []
    return LegislationGovUkConnector(path, "gb-legislation-gov-uk", tmp_path / "cache", sleep=record.append)


SITE = "https://www.legislation.gov.uk"


def test_connector_reads_regnal_year_paths_and_records_missing_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes: dict[str, bytes | int] = {
        f"{SITE}/robots.txt": b"User-agent: *\nCrawl-delay: 7\nDisallow: */data.pdf\nDisallow: /defralex\n",
        f"{SITE}/ukpga/Vict/22/1/data.akn": _read(BURIAL),
        f"{SITE}/ukpga/Vict/22-23/1/data.akn": _read(CLERK),
    }
    requested = _stub_site(monkeypatch, routes)
    sleeps: list[float] = []
    conn = _connector(tmp_path, ["ukpga/Vict/22/1", "ukpga/2099/1", "ukpga/Vict/22-23/1"], sleeps, delay=1)
    docs = list(conn.fetch())
    assert [d.metadata["path"] for d in docs] == ["ukpga/Vict/22/1", "ukpga/Vict/22-23/1"]
    assert docs[0].external_id.startswith("legislation.gov.uk:ukpga/Vict/22/1@1996-04-01")
    assert conn.fetch_errors == ["ukpga/2099/1: not found (404)"]  # the run went on after the missing work
    assert requested[0] == f"{SITE}/robots.txt" and len(requested) == 4
    # robots.txt's Crawl-delay (7 s) wins over the manifest's 1 s
    assert sleeps and all(6.5 < s <= 7 for s in sleeps)
    # a second run is served from the cache
    again = list(_connector(tmp_path, ["ukpga/Vict/22/1"]).fetch())
    assert len(again) == 1 and len(requested) == 4


def test_connector_respects_robots_txt_and_stops_on_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    routes: dict[str, bytes | int] = {
        f"{SITE}/robots.txt": b"User-agent: *\nCrawl-delay: 5\nDisallow: /ukpga/Vict/22/\n",
        f"{SITE}/ukpga/Vict/22-23/1/data.akn": _read(CLERK),
    }
    requested = _stub_site(monkeypatch, routes)
    with pytest.raises(Refused, match="disallowed by robots.txt"):
        list(_connector(tmp_path / "a", ["ukpga/Vict/22/1"]).fetch())
    assert f"{SITE}/ukpga/Vict/22/1/data.akn" not in requested

    routes[f"{SITE}/ukpga/Vict/22-23/1/data.akn"] = 403
    conn = _connector(tmp_path / "b", ["ukpga/Vict/22-23/1", "ukpga/Vict/22-23/2"])
    with pytest.raises(Refused, match="HTTP 403"):
        list(conn.fetch())
    assert conn.fetch_errors == []  # a refusal is not recorded and skipped: it stops the run


def test_connector_gives_up_after_repeated_failures_but_survives_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = f"{SITE}/ukpga/Vict/22/1/data.akn"
    routes: dict[str, bytes | int] = {f"{SITE}/robots.txt": b"User-agent: *\n", good: _read(BURIAL)}
    bad = [f"ukpga/Vict/22-23/{n}" for n in range(2, 8)]
    for p in bad:
        routes[f"{SITE}/{p}/data.akn"] = 500
    _stub_site(monkeypatch, routes)
    conn = _connector(tmp_path, ["ukpga/Vict/22-23/2", "ukpga/Vict/22/1"])
    assert len(list(conn.fetch())) == 1 and len(conn.fetch_errors) == 1 and "HTTP 500" in conn.fetch_errors[0]
    conn = _connector(tmp_path / "x", bad)
    with pytest.raises(FetchError, match="stopping after 5 failures in a row"):
        list(conn.fetch())
    assert len(conn.fetch_errors) == 5


def test_manifest_validation(tmp_path: Path) -> None:
    for bad in ("ukpga/1998", "ukpga/Eliz2/42", "ukpga/Eliz2/10-11/x", "../ukpga/1998/42", "ukpga/1998/42/"):
        with pytest.raises(ManifestError, match="invalid work path"):
            _connector(tmp_path, [bad])._manifest()  # noqa: SLF001
    with pytest.raises(ManifestError, match="listed twice"):
        _connector(tmp_path, ["ukpga/1998/42", "ukpga/1998/42"])._manifest()  # noqa: SLF001
    for ok in (
        "ukpga/1998/42",
        "ukpga/Eliz2/10-11/58",
        "ukpga/Geo6/12-13-14/103",
        "ukpga/Geo6and1Eliz2/15-16/29",
        "asc/2026/11",
    ):
        _connector(tmp_path, [ok])._manifest()  # noqa: SLF001


def _by_type(works: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for w in works:
        counts[w["path"].split("/")[0]] = counts.get(w["path"].split("/")[0], 0) + 1
    return counts


def _listed_works(manifest_path: Path) -> list[dict[str, Any]]:
    """The committed manifest's own entries (history/version flags as written), before the go-live ``current_only``
    switch, which the connector applies on top (every work's history off, one request each)."""
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    default_history = bool(raw.get("history_default", False))
    return [LegislationGovUkConnector._work(entry, default_history) for entry in raw["works"]]  # noqa: SLF001


def test_committed_primary_manifest_is_every_act_and_order_the_site_holds_as_xml() -> None:
    """Generated by scripts/build_legislation_gov_uk_manifest.py: passes the connector's own validation."""
    conn = LegislationGovUkConnector(MANIFEST, "gb-legislation-gov-uk", Path("/nonexistent"))
    validated = conn._manifest()  # noqa: SLF001 - the connector's own validation of the committed manifest
    assert validated["current_only"] is True and all(w["history"] is False for w in validated["works"])  # go-live
    works = _listed_works(MANIFEST)  # the listing itself: which works have a history to fetch on request
    assert len(works) == len(validated["works"])
    by_type = _by_type(works)
    assert len(works) > 7000 and by_type["ukpga"] > 4000 and by_type["asp"] > 400 and by_type["ukla"] > 150
    for kind in ("anaw", "asc", "mwa", "nia", "nisi", "apni", "ukcm", "aep", "aosp", "apgb", "aip", "mnia"):
        assert by_type.get(kind, 0) > 1, kind
    assert not {"uksi", "ssi", "wsi", "nisr"} & set(by_type)  # secondary legislation is the other source
    assert all("history" in w for w in works)  # explicit for every work: a missing flag must not mean "fetch history"
    revised = [w for w in works if w["history"]]
    single = [w for w in works if w.get("version")]
    assert len(revised) > 6000 and len(single) > 900 and len(revised) + len(single) == len(works)
    assert all(w["version"] in ("enacted", "made") and w["history"] is False for w in single)  # Orders in Council: made
    # Repealed Acts are in, with their history; the five Acts of the original sample keep theirs.
    assert any(re.search(r"\(\s*repealed", w.get("title", ""), re.I) and w["history"] for w in works)
    paths = {w["path"] for w in works}
    assert {"ukpga/1998/42", "ukpga/1978/30", "ukpga/1968/60", "ukpga/2018/12", "ukpga/2015/15"} <= paths
    assert {"asp/2003/2", "mwa/2011/1", "anaw/2016/1"} <= paths
    assert all(w["history"] for w in works if w["path"] in ("ukpga/1998/42", "asp/2003/2"))
    assert any(p.startswith("ukpga/Eliz2/") for p in paths)  # pre-1963 Acts use the regnal-year path
    assert "ukpga/Vict/22/1" in paths and "ukpga/Vict/22-23/1" in paths
    assert "aosp/1540/SessMar14/37" in paths  # the older parliaments' unusual session forms


def test_committed_secondary_manifests_split_revised_and_never_revised_instruments() -> None:
    revised = LegislationGovUkConnector(MANIFEST_SECONDARY, "gb-legislation-gov-uk-secondary", Path("/nonexistent"))
    manifest = revised._manifest()  # noqa: SLF001
    assert manifest["current_only"] is True and all(w["history"] is False for w in manifest["works"])  # go-live
    works = _listed_works(MANIFEST_SECONDARY)
    by_type = _by_type(works)
    assert manifest["history_default"] is True and len(works) > 17000 and len(works) == len(manifest["works"])
    assert by_type["uksi"] > 9000 and by_type["ssi"] > 2000 and by_type["wsi"] > 1500 and by_type["nisr"] > 4000
    assert set(by_type) == {"uksi", "ssi", "wsi", "nisr", "ukci"}
    assert all(w["history"] is True and not w.get("version") for w in works)  # every one has its versions fetched
    assert "uksi/2018/480" in {w["path"] for w in works}  # the instrument of the original sample, now with the rest
    made = LegislationGovUkConnector(MANIFEST_MADE, "gb-legislation-gov-uk-secondary-made", Path("/nonexistent"))
    made_works = made._manifest()["works"]  # noqa: SLF001
    made_by_type = _by_type(made_works)
    assert (
        len(made_works) > 65000 and made_by_type["uksi"] > 50000 and set(made_by_type) == {"uksi", "ssi", "wsi", "nisr"}
    )
    assert all(w["version"] == "made" and w["history"] is False for w in made_works)
    # An instrument is in one source only, and secondary legislation is not mixed with Acts.
    assert {w["path"] for w in works}.isdisjoint(w["path"] for w in made_works)
    assert not any(w["path"].startswith(("ukpga/", "asp/", "nisi/")) for w in [*works, *made_works])


# --------------------------------------------------------------------------- PostgreSQL


class _Docs:
    def __init__(self, docs: list[RawDocument]):
        self.docs = docs

    def fetch(self):  # type: ignore[no-untyped-def]
        yield from self.docs


@pytest.fixture(scope="module")
def regnal_ingested(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    ingested: IngestionReport,
    tmp_path_factory: pytest.TempPathFactory,
) -> IngestionReport:
    cache = tmp_path_factory.mktemp("uk-regnal") / "cache"
    shutil.copytree(FIX.parent.parent.parent, cache)
    for gz in cache.rglob("*.akn.gz"):
        gz.with_suffix("").write_bytes(gzip.decompress(gz.read_bytes()))
        gz.unlink()
    manifest = tmp_path_factory.mktemp("uk-regnal-manifest") / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "source_id": "gb-legislation-gov-uk",
                "base_url": "https://www.legislation.gov.uk",
                "user_agent": "ULIS-test (+https://example.org)",
                "works": [
                    {"path": "ukpga/Vict/22-23/1", "history": False},
                    {"path": "ukpga/Vict/22/1"},
                    {"path": "ukpga/Eliz2/7-8/49"},
                ],
            }
        )
    )
    connector = LegislationGovUkConnector(manifest, "gb-legislation-gov-uk", cache, offline=True)
    docs = list(connector.fetch())
    assert len(docs) == 3
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "gb-legislation-gov-uk", actor="pytest", connector=_Docs(docs)
        )
        session.commit()
    return report


@pytest.mark.db
def test_same_chapter_acts_are_two_instruments(
    session: Session, catalog: Catalog, regnal_ingested: IngestionReport
) -> None:
    assert regnal_ingested.errors == [] and regnal_ingested.conflicts == []
    assert regnal_ingested.instruments_created == 3
    burial = provision_at(session, catalog, "/gb/ukpga/Vict/22/1/~section-1", date(2026, 10, 1))
    clerk = provision_at(session, catalog, "/gb/ukpga/Vict/22-23/1/~section-1", date(2026, 10, 1))
    assert burial["provision"]["citation"] == "Burial Act 1859, s 1"
    assert clerk["provision"]["citation"] == "Clerk of the Council Act 1859, s 1"
    assert "Principal Secretaries of State" in burial["version"]["text"]
    assert "Privy Council" in clerk["version"]["text"]


@pytest.mark.db
def test_search_finds_a_regnal_year_act_with_its_citation(
    session: Session, catalog: Catalog, regnal_ingested: IngestionReport
) -> None:
    res = search(
        session,
        catalog,
        SearchRequest(
            q="vaults or place of burial Order in Council obstruct misdemeanor",
            jurisdiction="GB",
            as_of=date(2026, 10, 1),
            query_language="en",
        ),
        today=date(2026, 10, 14),
    )
    hit = next(h for h in res["results"] if h["document"]["uri"] == "/gb/ukpga/Vict/22/1")
    assert hit["citation"].startswith("Burial Act 1859, s ")
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"REVISED_TEXT", "PROFILE_UNVERIFIED"} <= codes
    assert hit["source"]["attribution"].startswith("Contains public sector information")


@pytest.mark.db
def test_a_run_keeps_autovacuum_off_its_tables(
    engine: Engine,
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    regnal_ingested: IngestionReport,
) -> None:
    """A run is one transaction: autovacuum, which cannot see its rows, would record "no rows" for the tables it
    fills and the planner would then take minutes per Act (measured on a 56-Act slice). The run's ANALYZE holds the
    lock that makes vacuum wait, so a vacuum tried in the middle of a run is refused here by a short lock timeout."""
    now = datetime(2026, 10, 15, tzinfo=UTC)
    docs = [
        RawDocument(f"test:{name}", f"https://example.org/{name}", "application/akn+xml", _read(path), now, {})
        for name, path in (("clerk", CLERK), ("burial", BURIAL))
    ]
    outcome: dict[str, str] = {}

    class _MidRunVacuum:
        fetch_errors: list[str] = []

        def fetch(self):  # type: ignore[no-untyped-def]
            yield docs[0]
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(text("SET lock_timeout = '1s'"))
                try:
                    conn.execute(text("VACUUM provisions"))
                    outcome["vacuum"] = "ran"
                except DBAPIError as exc:
                    outcome["vacuum"] = type(exc.orig).__name__
            yield docs[1]

    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "gb-legislation-gov-uk", actor="pytest", connector=_MidRunVacuum()
        )
        session.commit()
    assert outcome == {"vacuum": "LockNotAvailable"}
    assert report.errors == [] and report.documents_seen == 2
