"""legislation.gov.uk parser, fetcher and UK search behaviour (real UK documents, OGL v3.0)."""

from __future__ import annotations

import gzip
import json
import re
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.legislation_gov_uk import FetchError, LegislationGovUkConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.parsers.legislation_gov_uk import (
    affected_eid,
    extent_ids,
    parse_legislation_gov_uk,
    resolve_uri,
)
from ulis.storage.object_store import LocalObjectStore

FIX = REPO_ROOT / "tests" / "fixtures" / "legislation_gov_uk"
THEFT = FIX / "cache" / "legislation.gov.uk" / "ukpga" / "1968" / "60"
HRA = FIX / "cache" / "legislation.gov.uk" / "ukpga" / "1998" / "42"


def _read(path: Path) -> bytes:
    """Fixtures are stored gzip-compressed (unmodified responses)."""
    return gzip.decompress(path.with_name(path.name + ".gz").read_bytes())


def _cache_copy(dest: Path) -> Path:
    """Copy the fixture cache and decompress it into the connector's cache layout."""
    shutil.copytree(FIX / "cache", dest)
    for gz in dest.rglob("*.akn.gz"):
        gz.with_suffix("").write_bytes(gzip.decompress(gz.read_bytes()))
        gz.unlink()
    return dest


def _one(path: Path):  # type: ignore[no-untyped-def]
    docs = parse_legislation_gov_uk(_read(path))
    assert len(docs) == 1
    return docs[0]


def _by_eid(doc) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {p.eid: p for p in doc.provisions}


def test_extent_and_identifier_mapping() -> None:
    assert extent_ids("E+W+S+N.I.") == ["GB"]
    assert extent_ids("E+W") == ["GB-EAW"]
    assert extent_ids("E+W+S") == ["GB-EAW", "GB-SCT"]
    assert extent_ids("S") == ["GB-SCT"]
    assert affected_eid("s. 3(2)") == "section-3-2"
    assert affected_eid("Sch. 1 para. 4") == "schedule-1-paragraph-4"
    assert affected_eid("reg. 2(1)") == "regulation-2-1"
    assert affected_eid("Act") is None
    assert resolve_uri("http://www.legislation.gov.uk/id/ukpga/1998/42/section/4/5") == (
        "/gb/ukpga/1998/42",
        "section-4-5",
    )
    assert resolve_uri("http://www.legislation.gov.uk/ukpga/1968/60/2007-01-15") == ("/gb/ukpga/1968/60", None)
    assert resolve_uri("https://example.org/ukpga/1968/60") is None


def test_current_human_rights_act() -> None:
    doc = _one(HRA / "current.akn")
    assert (doc.jurisdiction_id, doc.instrument_type, doc.work_uri) == ("GB", "act", "/gb/ukpga/1998/42")
    assert doc.text_origin == "official_revised" and doc.validity_basis == "source_periods"
    p = _by_eid(doc)
    s4_5_a = p["section-4-5-a"]
    assert s4_5_a.text == "the Supreme Court;" and s4_5_a.extent == ["GB"]
    assert s4_5_a.valid_from is not None
    assert "substituted" in (s4_5_a.note or "")  # editorial commentary kept as a note, not as text
    module = get_module("gb")
    cite = module.provision_citation(
        ProvisionCitationInput(
            "Human Rights Act 1998", "act", "GB", s4_5_a.top_kind, s4_5_a.num_path, "en", ext=doc.ext, eid=s4_5_a.eid
        )
    )
    assert cite == "Human Rights Act 1998, s 4(5)(a)"
    assert doc.outstanding_changes and any(c["affects_eid"] for c in doc.outstanding_changes)


def test_theft_act_extent_repeals_and_schedules() -> None:
    doc = _one(THEFT / "current.akn")
    p = _by_eid(doc)
    assert p["section-1-1"].extent == ["GB-EAW"]
    assert p["section-15"].status == "repealed" and p["section-15"].valid_from == date(2007, 1, 15)
    assert p["section-15"].text == ""
    sch = p["schedule-1-paragraph-1"]
    cite = get_module("gb").provision_citation(
        ProvisionCitationInput(
            "Theft Act 1968", "act", "GB", sch.top_kind, sch.num_path, "en", ext=doc.ext, eid=sch.eid
        )
    )
    assert cite == "Theft Act 1968, sch 1, para 1"


def test_point_in_time_version_before_repeal() -> None:
    earlier = sorted(THEFT / f.name.removesuffix(".gz") for f in THEFT.glob("*.akn.gz") if f.name != "current.akn.gz")
    assert earlier, "fixture needs an earlier point-in-time version"
    doc = _one(earlier[0])
    s15 = _by_eid(doc)["section-15"]
    assert doc.version_date < date(2007, 1, 15)
    assert s15.status == "in_force" and "deception" in s15.text
    # "period-to-2007-01-15" has no start: it runs from the 1991-02-01 basedate of the revised data
    assert (s15.valid_from, s15.valid_to) == (date(1991, 2, 1), date(2007, 1, 15))


def _connector(cache: Path, tmp_path: Path, works: list[dict[str, Any]], **kw: Any) -> LegislationGovUkConnector:
    manifest = {
        "schema_version": 1,
        "source_id": "gb-legislation-gov-uk",
        "base_url": "https://www.legislation.gov.uk",
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 5,
        "works": works,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return LegislationGovUkConnector(path, "gb-legislation-gov-uk", cache, **kw)


def test_connector_uses_cache_offline_and_yields_history(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_copy(cache)
    works = [{"path": "ukpga/1968/60", "history": True, "history_dates": ["2001-10-01"]}]
    docs = list(_connector(cache, tmp_path, works, offline=True).fetch())
    versions = [d.metadata["point_in_time"] for d in docs]
    assert versions == sorted(versions) and len(versions) == len(set(versions)) >= 2
    assert all(d.uri.startswith("https://www.legislation.gov.uk/ukpga/1968/60") for d in docs)
    meta = json.loads((cache / "legislation.gov.uk/ukpga/1968/60/current.json").read_text())
    assert docs[-1].retrieved_at.isoformat() == meta["retrieved_at"]


def test_current_only_manifest_fetches_the_current_text_alone(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_copy(cache)
    works = [{"path": "ukpga/1968/60", "history": True, "history_dates": ["2001-10-01"]}]
    path = tmp_path / "manifest.yaml"
    manifest = yaml.safe_load(_connector(cache, tmp_path, works, offline=True).manifest_path.read_text())
    path.write_text(yaml.safe_dump({**manifest, "current_only": True}))
    conn = LegislationGovUkConnector(path, "gb-legislation-gov-uk", cache, offline=True)
    docs = list(conn.fetch())
    assert len(docs) == 1 and conn._manifest()["works"][0]["history"] is False  # noqa: SLF001
    for name in ("gb-legislation-gov-uk", "gb-legislation-gov-uk-secondary"):  # the go-live manifests
        committed = yaml.safe_load((REPO_ROOT / "config" / "manifests" / f"{name}.yaml").read_text())
        assert committed["current_only"] is True


def test_connector_requires_identification_and_https(tmp_path: Path) -> None:
    conn = _connector(tmp_path, tmp_path, [{"path": "ukpga/1968/60"}], offline=True)
    with pytest.raises(FetchError, match="offline and not cached"):
        list(conn.fetch())
    path = tmp_path / "manifest.yaml"
    manifest = yaml.safe_load(path.read_text())
    manifest["user_agent"] = "python-urllib"
    path.write_text(yaml.safe_dump(manifest))
    with pytest.raises(ManifestError, match="user_agent"):
        list(LegislationGovUkConnector(path, "gb-legislation-gov-uk", tmp_path).fetch())


# --------------------------------------------------------------------------- PostgreSQL


@pytest.fixture(scope="module")
def uk_ingested(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    store: LocalObjectStore,
    ingested: IngestionReport,
    tmp_path_factory: pytest.TempPathFactory,
) -> IngestionReport:
    tmp = tmp_path_factory.mktemp("uk")
    cache = tmp / "cache"
    _cache_copy(cache)
    connector = _connector(
        cache,
        tmp,
        [
            {"path": "ukpga/1968/60", "history": True, "history_dates": ["2001-10-01"]},
            {"path": "ukpga/1998/42", "history": False},
        ],
        offline=True,
    )
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "gb-legislation-gov-uk", actor="pytest", connector=connector
        )
        session.commit()
    return report


@pytest.mark.db
def test_uk_ingestion_and_history(session: Session, catalog: Catalog, uk_ingested: IngestionReport) -> None:
    assert uk_ingested.errors == [] and uk_ingested.conflicts == []
    before = provision_at(session, catalog, "/gb/ukpga/1968/60/~section-15", date(2006, 6, 1))
    after = provision_at(session, catalog, "/gb/ukpga/1968/60/~section-15", date(2008, 1, 1))
    assert before["status_on_date"] == "in_force" and "deception" in before["version"]["text"]
    assert after["status_on_date"] == "repealed"
    assert before["provision"]["citation"] == "Theft Act 1968, s 15"


@pytest.mark.db
def test_uk_extent_drives_jurisdiction_filter(session: Session, catalog: Catalog, uk_ingested: IngestionReport) -> None:
    def cites(jurisdiction: str) -> set[str]:
        res = search(
            session,
            catalog,
            SearchRequest(
                q="dishonestly appropriates property",
                jurisdiction=jurisdiction,
                as_of=date(2020, 1, 1),
                query_language="en",
            ),
            today=date(2026, 10, 14),
        )
        return {h["citation"] for h in res["results"]}

    assert "Theft Act 1968, s 1(1)" in cites("GB-ENG")
    assert "Theft Act 1968, s 1(1)" in cites("GB-WLS")
    assert not any(c.startswith("Theft Act 1968") for c in cites("GB-SCT"))  # does not extend to Scotland


@pytest.mark.db
def test_uk_limitations(session: Session, catalog: Catalog, uk_ingested: IngestionReport) -> None:
    res = search(
        session,
        catalog,
        SearchRequest(
            q="declaration of incompatibility", jurisdiction="GB-ENG", as_of=date(2026, 10, 1), query_language="en"
        ),
        today=date(2026, 10, 14),
    )
    hit = next(h for h in res["results"] if h["document"]["uri"] == "/gb/ukpga/1998/42")
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"REVISED_TEXT", "PROFILE_UNVERIFIED"} <= codes
    assert "NOT_AUTHENTIC_TEXT" not in codes  # English is the authentic language; REVISED_TEXT covers it
    assert hit["source"]["attribution"].startswith("Contains public sector information")


class _Docs:
    def __init__(self, docs: list[RawDocument]):
        self.docs = docs

    def fetch(self):  # type: ignore[no-untyped-def]
        yield from self.docs


@pytest.mark.db
def test_restated_wording_is_dated_from_when_it_was_first_seen(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> None:
    """legislation.gov.uk sometimes amends a wording without moving its start date. The new
    wording is then dated from the first point-in-time version that shows it, and flagged."""
    original = _read(THEFT / "2001-10-01.akn").replace(b"ukpga/1968/60", b"ukpga/1968/960")
    later = re.sub(
        rb'(<FRBRExpression>.*?<FRBRdate date=")2001-10-01', rb"\g<1>2001-11-15", original, count=1, flags=re.S
    ).replace(
        b"dishonestly appropriates property belonging to another", b"dishonestly appropriates property of another"
    )
    now = datetime(2026, 10, 14, tzinfo=UTC)
    docs = [
        RawDocument(f"test:theft-960@{d}", f"https://example.org/{d}", "application/akn+xml", c, now, {})
        for d, c in (("2001-10-01", original), ("2001-11-15", later))
    ]
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(
            "gb-legislation-gov-uk", actor="pytest", connector=_Docs(docs)
        )
        session.commit()
    assert report.errors == [] and report.conflicts == [] and report.provision_versions_restated >= 1
    with session_factory() as session:
        before = provision_at(session, catalog, "/gb/ukpga/1968/960/~section-1-1", date(2001, 11, 1))
        after = provision_at(session, catalog, "/gb/ukpga/1968/960/~section-1-1", date(2001, 11, 20))
    assert "belonging to another" in before["version"]["text"]
    assert "property of another" in after["version"]["text"]
    assert after["version"]["valid_from"] == "2001-11-15" and after["version"]["valid_from_basis"] == "observed"
    assert "START_DATE_OBSERVED" in {lim["code"] for lim in after["limitations"]}


@pytest.mark.db
def test_history_loaded_after_the_current_text_gives_the_same_answers(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> None:
    """The go-live load fetches the current text of each work only; the earlier versions are added by a later run.
    The late arrival of the older versions must neither conflict with the current text nor change its answers."""
    now = datetime(2026, 10, 14, tzinfo=UTC)
    versions = FIX / "versions"

    def doc(day: str) -> RawDocument:
        raw = gzip.decompress((versions / f"ukpga_1990_26_{day}.akn.gz").read_bytes())
        return RawDocument(
            f"test:ukpga-1990-26@{day}", f"https://example.org/{day}", "application/akn+xml", raw, now, {}
        )

    reports = []
    for docs in ([doc("2007-09-01")], [doc("1991-02-01"), doc("2007-09-01")]):
        with session_factory() as session:
            reports.append(
                Ingestor(session, catalog, store).ingest_source(
                    "gb-legislation-gov-uk", actor="pytest", connector=_Docs(docs)
                )
            )
            session.commit()
    first, second = reports
    assert first.errors == [] and second.errors == []
    assert first.conflicts == [] and second.conflicts == [], second.conflicts
    with session_factory() as session:
        old = provision_at(session, catalog, "/gb/ukpga/1990/26/~section-1", date(1995, 1, 1))
        new = provision_at(session, catalog, "/gb/ukpga/1990/26/~section-1", date(2010, 1, 1))
    assert old["version"]["valid_from"] == "1991-02-01" and new["version"]["valid_from"] == "2007-09-01"
    assert old["version"]["text"] != new["version"]["text"]
