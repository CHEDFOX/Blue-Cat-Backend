"""e-Gov bulk download (all_xml.zip): the connector's bulk mode and the parser's bare <Law> and list inputs.

Fixtures (tests/fixtures/egov_laws_bulk, see its README): a small archive holding real members of e-Gov's archive
of 2026-10-17 and its list lines for those laws, unmodified, and the entries of the API list of laws in force for
the same laws; API responses for the same revisions are in tests/fixtures/egov_laws.
"""

from __future__ import annotations

import dataclasses
import email.message
import gzip
import hashlib
import json
import shutil
import urllib.error
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import METADATA_PARSERS, IngestionReport, Ingestor
from ulis.ops.loading import LoadOptions, select_sources
from ulis.ops.sources import STATIC_PINS, manifest_info
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.egov_laws import BULK_URL, EgovLawsConnector, FetchError
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.normalized import ParsedDocument
from ulis.sources.parsers.egov_law import EgovParseError, kanji_date, parse_egov
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

BULK_FIX = REPO_ROOT / "tests" / "fixtures" / "egov_laws_bulk" / "cache"
API_FIX = REPO_ROOT / "tests" / "fixtures" / "egov_laws" / "cache"
API = API_FIX / "laws.e-gov.go.jp" / "api2"
ARCHIVE = BULK_FIX / "laws.e-gov.go.jp" / "bulkdownload" / "all_xml.zip"
# Laws whose revision in force is in both fixture sets (the bulk archive and the API's law_data responses).
BOTH = {
    "321CONSTITUTION": "321CONSTITUTION_19470503_000000000000000",
    "415AC0000000057": "415AC0000000057_20261001_507AC0000000070",
    "353CO0000000167": "353CO0000000167_20260521_508CO0000000149",
    "405M50000400033": "405M50000400033_20260401_508M60000400006",
    "322CO0000000052": "322CO0000000052_19530801_328AC0000000161",  # typed "Act,CabinetOrder" by the list
    "320IO0000000730": "320IO0000000730_19451229_000000000000000",  # typed "Act,ImperialOrder" by the list
    "322M40010B72001": "322M40010B72001_19560521_331M50000040033",  # law number longer than 40 characters
    "205M10000200030": "205M10000200030_19431115_000000000000000",  # no amending law, but amendment_type 3
    "322R00000001001": "322R00000001001_19470503_000000000000000",  # a Rule
    "504AC0000000068": "504AC0000000068_20250601_000000000000000",  # amendment_law_id 000000000000000
    "141AC0000000029": "141AC0000000029_20250601_504AC0000000068",  # the list's abbreviation adds 「抄」
}
STAGED = "508AC0000000061"  # 防災庁設置法: in force 2026-07-17, two further stages to come (one date not fixed)
PREVIOUS_ONLY = "428M60000040069"  # e-Gov marks its revision in force PreviousEnforced and none CurrentEnforced
ONLY_BULK = [STAGED, PREVIOUS_ONLY, "205M10000200030", "322R00000001001", "504AC0000000068", "141AC0000000029"]


def _api(rel: str) -> bytes:
    return gzip.decompress((API / f"{rel}.gz").read_bytes())


def _cache(dest: Path) -> Path:
    """Both fixture caches in the connector's cache layout, decompressed."""
    shutil.copytree(API_FIX, dest)
    shutil.copytree(BULK_FIX, dest, dirs_exist_ok=True)
    for gz in dest.rglob("*.xml.gz"):
        gz.with_suffix("").write_bytes(gzip.decompress(gz.read_bytes()))
        gz.unlink()
    return dest


def _pin() -> dict[str, Any]:
    data = ARCHIVE.read_bytes()
    return {"url": BULK_URL, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _manifest(tmp_path: Path, laws: list[dict[str, Any]], **overrides: Any) -> Path:
    manifest = {
        "schema_version": 1,
        "source_id": "jp-egov-laws",
        "base_url": "https://laws.e-gov.go.jp/api/2",
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 5,
        "mode": "bulk",
        "bulk": _pin(),
        "laws": laws,
        **overrides,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True))
    return path


def _connector(tmp_path: Path, laws: list[str], **overrides: Any) -> EgovLawsConnector:
    tmp_path.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(tmp_path, [{"law_id": law, "history": False} for law in laws], **overrides)
    return EgovLawsConnector(manifest, "jp-egov-laws", _cache(tmp_path / "cache"), offline=True)


def _bulk(tmp_path: Path, laws: list[str]) -> dict[tuple[str, str], tuple[RawDocument, ParsedDocument]]:
    out = {}
    for raw in _connector(tmp_path, laws).fetch():
        (doc,) = parse_egov(raw.content, dict(raw.metadata))
        out[(raw.metadata["law_id"], raw.metadata["egov_bulk"])] = (raw, doc)
    return out


def _events(doc: ParsedDocument) -> list[tuple[Any, ...]]:
    return [(e.event_type, e.date, e.affecting_uri, e.description) for e in doc.events]


# --------------------------------------------------------------------------- parser: same documents as the API


def test_bulk_texts_parse_to_the_documents_the_api_gives(tmp_path: Path) -> None:
    bulk = _bulk(tmp_path, list(BOTH))
    for law_id, revision_id in BOTH.items():
        raw, doc = bulk[(law_id, "law")]
        (api,) = parse_egov(_api(f"law_data/{revision_id}.xml"))
        assert raw.metadata["law_revision_id"] == revision_id
        # Identical but for where the revision data was read (the response's revision_info / the stored metadata).
        assert dataclasses.replace(doc, events=[]) == dataclasses.replace(api, events=[]), law_id
        assert _events(doc) == _events(api)
        assert doc.events[0].locator == f"metadata/revision_info/law_revision_id[.='{revision_id}']"
    # The file's own type and abbreviations, as the text endpoint gives them.
    assert bulk[("322CO0000000052", "law")][1].ext["law_type"] == "Act"
    assert bulk[("141AC0000000029", "law")][1].ext["abbrev"] == "旧刑法施行法"
    assert bulk[("322M40010B72001", "law")][1].number is None  # longer than the 40-character column


def test_bulk_change_report_matches_the_revision_list(tmp_path: Path) -> None:
    bulk = _bulk(tmp_path, ["415AC0000000057", "504AC0000000068", "205M10000200030", "322R00000001001"])
    for law_id in ("415AC0000000057", "504AC0000000068", "205M10000200030", "322R00000001001"):
        _, report = bulk[(law_id, "law_list")]
        (api,) = parse_egov(_api(f"law_revisions/{law_id}.xml"))
        assert report.outstanding_changes == api.outstanding_changes, law_id
        same = dataclasses.replace(report, events=[], outstanding_changes=[])
        assert same == dataclasses.replace(api, events=[], outstanding_changes=[]), law_id
        # The archive lists the revision in force, not the earlier ones: its events are a subset of the list's.
        assert set(_events(report)) <= set(_events(api)) and {"promulgation"} <= {e.event_type for e in report.events}
    appi = bulk[("415AC0000000057", "law_list")][1]
    assert appi.expression_uri == "/jp/act/415AC0000000057/jpn@2026-10-01" and len(appi.outstanding_changes) == 6
    placeholder = next(c for c in appi.outstanding_changes if c["law_revision_id"].startswith("415AC0000000057_2028"))
    assert placeholder["effective_date"] is None and "not yet fixed" in str(placeholder["description"])


def test_pending_revisions_are_reported_never_loaded(tmp_path: Path) -> None:
    names = zipfile.ZipFile(ARCHIVE).namelist()
    pending = [f"{STAGED}_20261102_000000000000000", f"{STAGED}_20280716_000000000000000"]
    assert all(f"{r}/{r}.xml" in names for r in pending)  # their texts are in the archive...
    connector = _connector(tmp_path, [STAGED])
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [f"e-gov:{STAGED}_20260717_000000000000000", f"e-gov:{STAGED}:law_list"]
    assert not any(r in d.uri for d in docs for r in pending)  # ...and never yielded
    (text,) = parse_egov(docs[0].content, dict(docs[0].metadata))
    assert text.version_date == date(2026, 7, 17) and text.text_origin == "official_conversion"
    (report,) = parse_egov(docs[1].content, dict(docs[1].metadata))
    fixed, open_ = report.outstanding_changes
    assert (fixed["law_revision_id"], fixed["effective_date"]) == (pending[0], "2026-11-02")
    assert "promulgated 2026-07-17" in str(fixed["description"]) and fixed["by_uri"] is None
    assert open_["law_revision_id"] == pending[1] and open_["effective_date"] is None
    assert "「公布の日から起算して二年を超えない範囲内において政令で定める日」" in str(open_["description"])


def test_a_bare_law_file_needs_consistent_metadata(tmp_path: Path) -> None:
    raw, _ = _bulk(tmp_path, ["322R00000001001"])[("322R00000001001", "law")]
    with pytest.raises(EgovParseError, match="needs the connector's metadata"):
        parse_egov(raw.content)
    meta = dict(raw.metadata)
    with pytest.raises(EgovParseError, match="the list says"):
        parse_egov(raw.content, {**meta, "law_info": {**meta["law_info"], "law_num": "昭和二十二年規則第二号"}})
    with pytest.raises(EgovParseError, match="the file is revision"):
        parse_egov(raw.content, {**meta, "law_revision_id": "322R00000001001_20300101_000000000000000"})
    unenforced = {**meta["revision_info"], "current_revision_status": "UnEnforced"}
    with pytest.raises(EgovParseError, match="only enforced revisions"):
        parse_egov(raw.content, {**meta, "revision_info": unenforced})
    with pytest.raises(EgovParseError, match="unexpected bulk list columns"):
        parse_egov(b"a,b\n1,2\n", {**meta, "egov_bulk": "law_list"})
    assert METADATA_PARSERS["egov_law_xml"] is parse_egov


def test_list_dates_in_the_japanese_era() -> None:
    assert kanji_date("令和八年三月三十一日") == date(2026, 3, 31)
    assert kanji_date("令和元年十二月十三日") == date(2019, 12, 13)
    assert kanji_date("平成十五年五月三十日") == date(2003, 5, 30)
    assert kanji_date("明治五年十一月九日") == date(1872, 11, 9)  # as e-Gov writes it in ISO form
    assert kanji_date("") is None
    for bad in ("令和八年", "令和八年十三月一日", "2026-03-31"):
        with pytest.raises(EgovParseError):
            kanji_date(bad)


# --------------------------------------------------------------------------- connector


def test_bulk_connector_offline(tmp_path: Path) -> None:
    laws = [
        {"law_id": "321CONSTITUTION", "history": False},
        {"law_id": "140AC0000000045", "history": True, "history_dates": ["2016-06-23"]},  # from the API
        {"law_id": PREVIOUS_ONLY, "history": False},
    ]
    cache = _cache(tmp_path / "cache")
    connector = EgovLawsConnector(_manifest(tmp_path, laws), "jp-egov-laws", cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [
        "e-gov:321CONSTITUTION_19470503_000000000000000",
        "e-gov:321CONSTITUTION:law_list",
        "e-gov:140AC0000000045_20160623_428AC0000000054",
        "e-gov:140AC0000000045_20260521_507AC0000000039",
        "e-gov:140AC0000000045:law_revisions",
        f"e-gov:{PREVIOUS_ONLY}_20170401_429M60000040008",
        f"e-gov:{PREVIOUS_ONLY}:law_list",
    ]
    assert connector.fetch_errors == []
    text, report = docs[0], docs[1]
    member = "321CONSTITUTION_19470503_000000000000000/321CONSTITUTION_19470503_000000000000000.xml"
    archive = zipfile.ZipFile(ARCHIVE)
    assert text.uri == f"{BULK_URL}#{member}" and text.content == archive.read(member)  # exact bytes
    meta = json.loads(ARCHIVE.with_name("all_xml.zip.meta.json").read_text())
    provenance = {**_pin(), "retrieved_at": meta["retrieved_at"], "last_modified": None, "member": member}
    assert text.metadata["archive"] == provenance  # the exact bytes: archive digest and member
    assert text.retrieved_at.isoformat() == meta["retrieved_at"] == report.retrieved_at.isoformat()
    lines = archive.read("all_law_list.csv").split(b"\n")
    assert report.content == lines[0] + b"\n" + b"".join(x + b"\n" for x in lines if b",321CONSTITUTION," in x)
    assert report.media_type == "text/csv" and report.uri == f"{BULK_URL}#all_law_list.csv"
    # A law e-Gov marks PreviousEnforced only (no CurrentEnforced), which the API path refuses, loads from the archive.
    (previous,) = parse_egov(docs[-2].content, dict(docs[-2].metadata))
    assert previous.version_date == date(2017, 4, 1) and previous.instrument_type == "ministerial_ordinance"


def test_bulk_disagreements_are_reported_and_the_run_goes_on(tmp_path: Path) -> None:
    connector = _connector(tmp_path, ["129AC0000000089", "322R00000001001", "205M10000200030"])
    page = next((tmp_path / "cache").rglob("bulk-*/offset000000.xml"))
    # The list (read later than the archive, say) names another revision in force for one law.
    page.write_bytes(
        page.read_bytes().replace(
            b"<law_revision_id>205M10000200030_19431115_000000000000000",
            b"<law_revision_id>205M10000200030_20261019_508M60000200001",
        )
    )
    ids = [d.external_id for d in connector.fetch()]
    assert ids == ["e-gov:322R00000001001_19470503_000000000000000", "e-gov:322R00000001001:law_list"]
    first, second = connector.fetch_errors
    assert first.startswith("e-gov:129AC0000000089: not in the bulk archive") and "skipped" in first
    assert "disagree on the revision in force" in second and "205M10000200030_20261019" in second


def test_the_archive_is_pinned_and_the_manifest_checked(tmp_path: Path) -> None:
    laws = ["322R00000001001"]
    wrong = {**_pin(), "sha256": "0" * 64}
    with pytest.raises(FetchError, match="not the one the manifest pins"):
        list(_connector(tmp_path / "a", laws, bulk=wrong).fetch())
    for n, latest in enumerate(({"url": BULK_URL, "pin": "latest"}, {"url": BULK_URL, "bytes": None, "sha256": None})):
        assert len(list(_connector(tmp_path / f"b{n}", laws, bulk=latest).fetch())) == 2  # whatever archive is served
    for bad, message in (
        ({"mode": "zip"}, "mode must be one of"),
        ({"bulk": {"url": "https://example.org/all_xml.zip"}}, "bulk.url"),
        ({"bulk": {**_pin(), "sha256": None}}, "both set"),
        ({"bulk": {"url": BULK_URL, "pin": "exact"}}, "both set"),
        ({"bulk": {**_pin(), "pin": "latest"}}, "leave bulk.bytes and bulk.sha256 out"),
        ({"bulk": {"url": BULK_URL, "pin": "newest"}}, "bulk.pin must be one of"),
    ):
        path = _manifest(tmp_path, [{"law_id": "322R00000001001"}], **bad)
        with pytest.raises(ManifestError, match=message):
            EgovLawsConnector(path, "jp-egov-laws", tmp_path)._manifest()  # noqa: SLF001
    with pytest.raises(FetchError, match="offline and not cached"):
        manifest = _manifest(tmp_path, [{"law_id": "322R00000001001"}])
        list(EgovLawsConnector(manifest, "jp-egov-laws", tmp_path / "empty", offline=True).fetch())


def test_committed_manifest_takes_the_latest_archive_and_is_refreshed(catalog: Catalog, tmp_path: Path) -> None:
    path = REPO_ROOT / "config" / "manifests" / "jp-egov-laws.yaml"
    manifest = EgovLawsConnector(path, "jp-egov-laws", tmp_path)._manifest()  # noqa: SLF001
    assert manifest["mode"] == "bulk" and manifest["bulk"] == {"url": BULK_URL, "pin": "latest"}
    # The plan of load-all counts the requests of a bulk run (archive, list, two laws with history), not one per law.
    info = manifest_info(catalog.sources.get("jp-egov-laws"), REPO_ROOT)
    assert info is not None and info.entries == len(manifest["laws"]) and info.requests == 51
    # Not a static pin: the weekly refresh discards an archive older than its age limit and loads the current one.
    assert "jp-egov-laws" not in STATIC_PINS
    assert "jp-egov-laws" in {i.source.id for i in select_sources(catalog, LoadOptions(refresh=True)).selected}


class _Response:
    def __init__(self, url: str, body: bytes):
        self.url, self.body, self.status = url, body, 200
        self.headers = {"Content-Type": "application/octet-stream"}  # e-Gov sends no ETag or Last-Modified

    def read(self) -> bytes:
        return self.body

    def geturl(self) -> str:
        return self.url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _EGov:
    """Stands in for laws.e-gov.go.jp behind the real PoliteHttp: serves an archive and the list page; counts calls."""

    def __init__(self, archive: bytes):
        self.archive = archive
        self.page = gzip.decompress(next(BULK_FIX.rglob("offset000000.xml.gz")).read_bytes())
        self.requests: list[str] = []

    def __call__(self, request: Any, timeout: float | None = None, **kwargs: Any) -> _Response:
        url = request.full_url
        self.requests.append(url)
        if url == BULK_URL:
            return _Response(url, self.archive)
        if url.startswith("https://laws.e-gov.go.jp/api/2/laws?"):
            return _Response(url, self.page)
        raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=email.message.Message(), fp=None)

    def downloads(self) -> int:
        return self.requests.count(BULK_URL)


def _live(tmp_path: Path, egov: _EGov, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> list[RawDocument]:
    """One run of the connector with pin: latest against the stand-in (cache in tmp_path/cache)."""
    monkeypatch.setattr("ulis.sources.connectors.polite_http.urllib.request.urlopen", egov)
    laws = [{"law_id": "322R00000001001", "history": False}, {"law_id": STAGED, "history": False}]
    pin = kwargs.pop("bulk", {"url": BULK_URL, "pin": "latest"})
    manifest = _manifest(tmp_path, laws, bulk=pin)
    connector = EgovLawsConnector(manifest, "jp-egov-laws", tmp_path / "cache", sleep=lambda s: None, **kwargs)
    return list(connector.fetch())


def _archive_paths(tmp_path: Path) -> tuple[Path, Path]:
    path = tmp_path / "cache" / "laws.e-gov.go.jp" / "bulkdownload" / "all_xml.zip"
    return path, path.with_name("all_xml.zip.meta.json")


def test_live_archive_is_downloaded_once_recorded_and_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    egov = _EGov(ARCHIVE.read_bytes())
    docs = _live(tmp_path, egov, monkeypatch)
    assert len(docs) == 4 and egov.downloads() == 1
    assert egov.requests == ["https://laws.e-gov.go.jp/robots.txt", BULK_URL, egov.requests[2]]  # + one list page
    pin = _pin()
    path, meta_path = _archive_paths(tmp_path)
    meta = json.loads(meta_path.read_text())
    assert (meta["bytes"], meta["sha256"]) == (pin["bytes"], pin["sha256"]) and meta["last_modified"] is None
    for doc in docs:  # every document names the exact bytes it came from
        assert doc.metadata["archive"]["sha256"] == pin["sha256"] and doc.uri.startswith(f"{BULK_URL}#")
        assert doc.metadata["archive"]["retrieved_at"] == meta["retrieved_at"]
    assert (path.parent.parent / "api2" / "laws" / f"bulk-{pin['sha256'][:16]}" / "offset000000.xml").exists()
    again = _live(tmp_path, egov, monkeypatch)  # a second run reuses the cache: no request at all
    assert [d.content for d in again] == [d.content for d in docs] and len(egov.requests) == 3


def test_a_deleted_or_damaged_archive_is_downloaded_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    egov = _EGov(ARCHIVE.read_bytes())
    _live(tmp_path, egov, monkeypatch)
    path, meta_path = _archive_paths(tmp_path)
    path.unlink()  # e.g. pruned from the cache
    assert len(_live(tmp_path, egov, monkeypatch)) == 4 and egov.downloads() == 2
    meta_path.unlink()
    assert len(_live(tmp_path, egov, monkeypatch)) == 4 and egov.downloads() == 3
    # Cut short after it was recorded: offline the run stops (and keeps the file); online it is downloaded again.
    path.write_bytes(path.read_bytes()[:50_000])
    with pytest.raises(FetchError, match="no longer the file downloaded"):
        _live(tmp_path, egov, monkeypatch, offline=True)
    assert path.stat().st_size == 50_000
    with pytest.raises(FetchError, match="no longer the file downloaded"):
        _live(tmp_path, egov, monkeypatch, bulk=_pin())  # pin: exact never replaces the archive
    assert len(_live(tmp_path, egov, monkeypatch)) == 4 and egov.downloads() == 4
    assert path.read_bytes() == ARCHIVE.read_bytes()


def test_a_new_archive_is_read_with_its_own_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    egov = _EGov(ARCHIVE.read_bytes())
    first = _live(tmp_path, egov, monkeypatch)
    rebuilt = tmp_path / "rebuilt.zip"
    shutil.copy(ARCHIVE, rebuilt)
    with zipfile.ZipFile(rebuilt, "a") as z:
        z.comment = b"rebuilt"  # same laws, other bytes: as when e-Gov rebuilds the archive
    egov.archive = rebuilt.read_bytes()
    _archive_paths(tmp_path)[0].unlink()  # discarded by load-all --refresh once older than its age limit
    lists_before = egov.requests.count(egov.requests[2])
    second = _live(tmp_path, egov, monkeypatch)
    new_sha = hashlib.sha256(egov.archive).hexdigest()
    assert {d.metadata["archive"]["sha256"] for d in second} == {new_sha}  # one run, one archive
    assert {d.metadata["archive"]["sha256"] for d in first} == {_pin()["sha256"]}
    assert egov.downloads() == 2 and egov.requests.count(egov.requests[2]) == lists_before + 1  # list read again
    assert (tmp_path / "cache" / "laws.e-gov.go.jp" / "api2" / "laws" / f"bulk-{new_sha[:16]}").is_dir()
    assert [d.content for d in second] == [d.content for d in first]  # unchanged laws: the same bytes


# --------------------------------------------------------------------------- PostgreSQL


@pytest.fixture(scope="module")
def jp_bulk_ingested(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    ingested: IngestionReport,
    tmp_path_factory: pytest.TempPathFactory,
) -> IngestionReport:
    connector = _connector(tmp_path_factory.mktemp("bulk"), ONLY_BULK)
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source("jp-egov-laws", actor="pytest", connector=connector)
        session.commit()
    return report


@pytest.mark.db
def test_bulk_ingestion_is_searchable_and_verifiable(
    session: Session, catalog: Catalog, store: LocalObjectStore, jp_bulk_ingested: IngestionReport
) -> None:
    assert jp_bulk_ingested.errors == [] and jp_bulk_ingested.conflicts == []
    assert (jp_bulk_ingested.documents_new, jp_bulk_ingested.instruments_created) == (12, 6)
    assert jp_bulk_ingested.instrument_versions_created == 6
    staged = session.scalars(
        select(m.InstrumentVersion).where(m.InstrumentVersion.uri == f"/jp/act/{STAGED}/jpn@2026-07-17")
    ).one()
    assert [c["effective_date"] for c in staged.outstanding_changes] == ["2026-11-02", None]
    assert staged.currency_date == date(2026, 10, 18)  # the archive's retrieval date
    # Every stored document re-derives (texts, and the events the lists report) with the metadata stored with it.
    docs = session.scalars(select(m.SourceDocument).where(m.SourceDocument.external_id.like("e-gov:%"))).all()
    bulk_docs = [d for d in docs if d.metadata_.get("egov_bulk")]
    assert len(bulk_docs) == 12
    assert {d.metadata_["archive"]["sha256"] for d in bulk_docs} == {_pin()["sha256"]}  # the exact archive
    assert all(d.uri.startswith(f"{BULK_URL}#") for d in bulk_docs)
    results = [verify_source_document(session, catalog, store, d.id) for d in bulk_docs]
    assert all(r["result"] == "pass" for r in results) and sum(r["checked_events"] for r in results) > 0
    previous = provision_at(
        session, catalog, f"/jp/ministerial_ordinance/{PREVIOUS_ONLY}/~article-1", date(2026, 10, 18)
    )
    assert previous["status_on_date"] == "in_force"
    # In force from 2026-07-17 are only the law's supplementary provisions; its body follows on later dates.
    query = "令和八年十二月三十一日までの間において政令で定める日から施行する"
    res = search(
        session,
        catalog,
        SearchRequest(jurisdiction="JP", q=query, as_of=date(2026, 10, 18), query_language="ja"),
        today=date(2026, 10, 18),
    )
    hit = next(h for h in res["results"] if h["citation"] == "防災庁設置法附則第1条")
    codes = {lim["code"] for lim in hit["limitations"]}
    # The law as made (official_conversion, not a revised text), with its two stages to come reported.
    assert {"SOURCE_NOT_AUTHENTIC", "OUTSTANDING_CHANGES_INSTRUMENT"} <= codes and "REVISED_TEXT" not in codes
