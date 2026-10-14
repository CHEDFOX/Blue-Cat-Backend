"""e-Gov 法令検索 parser, fetcher and Japanese search behaviour (real e-Gov API v2 responses, PDL1.0)."""

from __future__ import annotations

import gzip
import json
import shutil
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport, Ingestor
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.jurisdictions.modules.jp import number
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.egov_laws import EgovLawsConnector, FetchError, revisions_to_fetch
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.parsers.egov_law import EgovParseError, parse_egov, resolve_uri
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "egov_laws" / "cache"
API = FIX / "laws.e-gov.go.jp" / "api2"
CONSTITUTION = "law_data/321CONSTITUTION_19470503_000000000000000.xml"
CIVIL_CODE = "law_data/129AC0000000089_20260624_508AC0000000045.xml"
PENAL_CODE = [
    "law_data/140AC0000000045_20160623_428AC0000000054.xml",
    "law_data/140AC0000000045_20170713_429AC0000000072.xml",
    "law_data/140AC0000000045_20230713_505AC0000000066.xml",
    "law_data/140AC0000000045_20260521_507AC0000000039.xml",
]
APPI = "law_data/415AC0000000057_20261001_507AC0000000070.xml"
LAWS = {
    "321CONSTITUTION": [CONSTITUTION],
    "129AC0000000089": [CIVIL_CODE],
    "140AC0000000045": PENAL_CODE,
    "415AC0000000057": [APPI],
}
PENAL_HISTORY = ["2016-06-23", "2017-07-13", "2023-07-13"]


def _read(rel: str) -> bytes:
    """Fixtures are the API responses, unmodified, stored gzip-compressed."""
    return gzip.decompress((API / f"{rel}.gz").read_bytes())


def _doc(rel: str):  # type: ignore[no-untyped-def]
    docs = parse_egov(_read(rel))
    assert len(docs) == 1
    return docs[0]


def _by_eid(doc) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {p.eid: p for p in doc.provisions}


def _cite(doc, eid: str) -> str:  # type: ignore[no-untyped-def]
    p = _by_eid(doc)[eid]
    return get_module("jp").provision_citation(
        ProvisionCitationInput(
            doc.short_title, doc.instrument_type, "JP", p.top_kind, p.num_path, doc.language, ext=doc.ext, eid=p.eid
        )
    )


# --------------------------------------------------------------------------- parser


def test_constitution_structure_and_citations() -> None:
    doc = _doc(CONSTITUTION)
    assert (doc.instrument_type, doc.work_uri, doc.language) == (
        "constitution",
        "/jp/constitution/321CONSTITUTION",
        "ja",
    )
    assert doc.expression_uri == "/jp/constitution/321CONSTITUTION/jpn@1947-05-03"
    assert (doc.work_date, doc.version_date) == (date(1946, 11, 3), date(1947, 5, 3))  # promulgated / in force
    assert doc.text_origin == "official_conversion" and not doc.authoritative  # never amended; the Gazette is authentic
    assert doc.validity_basis == "revision_snapshot"
    assert doc.ext == {
        "law_id": "321CONSTITUTION",
        "law_num": "昭和二十一年憲法",
        "law_type": "Constitution",
        "abbrev": None,
    }
    p = _by_eid(doc)
    assert (
        p["article-9-paragraph-2"].text
        == "前項の目的を達するため、陸海空軍その他の戦力は、これを保持しない。国の交戦権は、これを認めない。"
    )
    assert p["article-9"].text.count("\n") == 1 and not p["article-9"].is_leaf  # two paragraphs, none numbered in print
    assert p["preamble"].text.startswith("日本国民は、正当に選挙された国会における代表者を通じて行動し")
    assert _cite(doc, "article-9-paragraph-2") == "日本国憲法第9条第2項"
    assert _cite(doc, "article-9") == "日本国憲法第9条"
    assert _cite(doc, "preamble") == "日本国憲法前文"


def test_civil_code_article_captions_and_ranges() -> None:
    doc = _doc(CIVIL_CODE)
    p = _by_eid(doc)
    tort = p["article-709"]
    # A single-paragraph article is one provision; its caption (見出し) is the heading, not text.
    assert tort.heading == "（不法行為による損害賠償）"
    assert tort.text == (
        "故意又は過失によって他人の権利又は法律上保護される利益を侵害した者は、"
        "これによって生じた損害を賠償する責任を負う。"
    )
    assert "article-709-paragraph-1" not in p
    assert _cite(doc, "article-709") == "民法第709条"
    deleted = p["article-38:84"]  # 第三十八条から第八十四条まで　削除
    assert (deleted.status, deleted.text, deleted.note) == ("repealed", "", "削除")
    assert _cite(doc, "article-38:84") == "民法第38条から第84条まで"
    assert doc.ext["law_num"] == "明治二十九年法律第八十九号" and doc.number == "明治二十九年法律第八十九号"
    # Supplementary provisions of amending laws belong to those laws and are not indexed.
    assert not any(e.startswith("suppl-") for e in p)


def test_penal_code_versions_ruby_and_repeal() -> None:
    old, *_, current = (_doc(rel) for rel in PENAL_CODE)
    p_old, p_now = _by_eid(old), _by_eid(current)
    # <Ruby>姦<Rt>かん</Rt></Ruby>: the reading (furigana) is dropped, the base character kept.
    assert p_old["article-177"].heading == "（強姦）"
    assert p_old["article-177"].text.startswith("暴行又は脅迫を用いて十三歳以上の女子を姦淫した者は、強姦の罪とし")
    assert old.version_date == date(2016, 6, 23) and old.text_origin == "official_revised"
    assert old.events[0].affecting_uri == "https://laws.e-gov.go.jp/law/428AC0000000054"
    assert p_now["article-177"].heading == "（不同意性交等）"
    assert p_now["article-199"].text == "人を殺した者は、死刑又は無期若しくは五年以上の拘禁刑に処する。"
    assert (p_now["article-178"].status, p_now["article-178"].note) == ("repealed", "削除")
    assert "article-178-paragraph-1" in p_old and "article-178-paragraph-1" not in p_now
    assert _cite(current, "article-199") == "刑法第199条"
    assert _cite(current, "article-27_2") == "刑法第27条の2"


def test_appi_items_and_citations() -> None:
    doc = _doc(APPI)
    p = _by_eid(doc)
    item = p["article-27-paragraph-1-item-2"]
    assert (
        item.text
        == "人の生命、身体又は財産の保護のために必要がある場合であって、本人の同意を得ることが困難であるとき。"
    )
    chapeau = p["article-27-paragraph-1"]
    assert chapeau.has_own_content and "\n二　人の生命、身体又は財産の保護" in chapeau.text  # items as printed
    assert _cite(doc, "article-27-paragraph-1-item-2") == "個人情報の保護に関する法律第27条第1項第2号"
    assert _cite(doc, "suppl-article-1") == "個人情報の保護に関する法律附則第1条"
    assert p["appdx-table-1"].heading == "別表第一（第二条関係）"
    assert _cite(doc, "appdx-table-1") == "個人情報の保護に関する法律別表第一"


def test_revision_list_is_a_change_report_of_unenforced_amendments() -> None:
    (report,) = parse_egov(_read("law_revisions/415AC0000000057.xml"))
    assert report.consolidation_kind == "change_report" and report.provisions == []
    assert report.expression_uri == "/jp/act/415AC0000000057/jpn@2026-10-01"  # the CurrentEnforced revision
    changes = report.outstanding_changes
    assert len(changes) == 6 and all(c["affects_eid"] is None for c in changes)
    fixed = next(c for c in changes if c["law_revision_id"] == "415AC0000000057_20261102_508AC0000000062")
    assert fixed["effective_date"] == "2026-11-02" and "to take effect on 2026-11-02" in fixed["description"]
    pending = next(c for c in changes if c["law_revision_id"] == "415AC0000000057_20280716_508AC0000000056")
    # A placeholder ordering date ("not necessarily the actual date") is never presented as the date.
    assert pending["effective_date"] is None and "not yet fixed" in pending["description"]
    assert pending["by_title"] == "個人情報の保護に関する法律等の一部を改正する法律（令和八年法律第五十六号）"
    assert {e.event_type for e in report.events} == {"amendment", "promulgation"}


def test_unenforced_revision_text_is_refused() -> None:
    raw = _read(APPI).replace(b"<current_revision_status>CurrentEnforced<", b"<current_revision_status>UnEnforced<")
    with pytest.raises(EgovParseError, match="only enforced revisions"):
        parse_egov(raw)
    with pytest.raises(EgovParseError, match="unsupported root"):
        parse_egov(b"<DataRoot/>")


def test_module_numbers_and_resolve_uri() -> None:
    assert number("27_2", "条") == "第27条の2"
    assert number("3_2", "号") == "第3号の2"
    assert number("2:3", "号") == "第2号及び第3号"
    assert number("73:76", "条") == "第73条から第76条まで"
    assert resolve_uri("https://laws.e-gov.go.jp/law/129AC0000000089") == ("/jp/act/129AC0000000089", None)
    assert resolve_uri("https://laws.e-gov.go.jp/law/321CONSTITUTION") == ("/jp/constitution/321CONSTITUTION", None)
    assert resolve_uri("https://example.org/law/129AC0000000089") is None


# --------------------------------------------------------------------------- connector


def _cache_copy(dest: Path) -> Path:
    """Copy the fixtures into the connector's cache layout, decompressed."""
    shutil.copytree(FIX, dest)
    for gz in dest.rglob("*.xml.gz"):
        gz.with_suffix("").write_bytes(gzip.decompress(gz.read_bytes()))
        gz.unlink()
    return dest


def _manifest(tmp_path: Path, laws: list[dict[str, Any]], **overrides: Any) -> Path:
    manifest = {
        "schema_version": 1,
        "source_id": "jp-egov-laws",
        "base_url": "https://laws.e-gov.go.jp/api/2",
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 5,
        "laws": laws,
        **overrides,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True))
    return path


def test_revision_choice_one_per_enforcement_date() -> None:
    listing = _read("law_revisions/140AC0000000045.xml")
    chosen = revisions_to_fetch(listing, history=True)
    dates = [r.enforcement_date for r in chosen]
    assert dates == sorted(set(dates)) and dates[0] == "2016-06-23" and dates[-1] == "2026-05-21"
    # Two amendments took effect on 2017-07-13; the first listed is the law of that day (both applied).
    assert next(r for r in chosen if r.enforcement_date == "2017-07-13").law_revision_id.endswith("429AC0000000072")
    assert [r.status for r in revisions_to_fetch(listing, history=False)] == ["CurrentEnforced"]


def test_connector_offline_from_cache(tmp_path: Path) -> None:
    cache = _cache_copy(tmp_path / "cache")
    laws = [
        {"law_id": "321CONSTITUTION", "history": False},
        {"law_id": "140AC0000000045", "history": True, "history_dates": PENAL_HISTORY},
    ]
    docs = list(EgovLawsConnector(_manifest(tmp_path, laws), "jp-egov-laws", cache, offline=True).fetch())
    assert [d.external_id for d in docs] == [
        "e-gov:321CONSTITUTION_19470503_000000000000000",
        "e-gov:321CONSTITUTION:law_revisions",
        "e-gov:140AC0000000045_20160623_428AC0000000054",
        "e-gov:140AC0000000045_20170713_429AC0000000072",
        "e-gov:140AC0000000045_20230713_505AC0000000066",
        "e-gov:140AC0000000045_20260521_507AC0000000039",
        "e-gov:140AC0000000045:law_revisions",  # after the texts it reports on
    ]
    assert (
        docs[2].uri
        == "https://laws.e-gov.go.jp/api/2/law_data/140AC0000000045_20160623_428AC0000000054?response_format=xml"
    )
    meta = json.loads((cache / "laws.e-gov.go.jp/api2/law_revisions/140AC0000000045.xml.meta.json").read_text())
    assert docs[-1].retrieved_at.isoformat() == meta["retrieved_at"]


def test_connector_requires_cache_offline_and_identification(tmp_path: Path) -> None:
    laws = [{"law_id": "140AC0000000045"}]
    with pytest.raises(FetchError, match="offline and not cached"):
        list(EgovLawsConnector(_manifest(tmp_path, laws), "jp-egov-laws", tmp_path / "empty", offline=True).fetch())
    with pytest.raises(ManifestError, match="user_agent"):
        list(EgovLawsConnector(_manifest(tmp_path, laws, user_agent="python-urllib"), "jp-egov-laws", tmp_path).fetch())
    with pytest.raises(ManifestError, match="base_url"):
        bad = _manifest(tmp_path, laws, base_url="http://laws.e-gov.go.jp/api/2")
        list(EgovLawsConnector(bad, "jp-egov-laws", tmp_path).fetch())
    with pytest.raises(ManifestError, match="law_id"):
        list(EgovLawsConnector(_manifest(tmp_path, [{"law_id": "../x"}]), "jp-egov-laws", tmp_path).fetch())


# --------------------------------------------------------------------------- PostgreSQL


class _Fixtures:
    """In-memory connector: every law's revision texts oldest first, then its revision list."""

    def fetch(self) -> Iterator[RawDocument]:
        for law_id, texts in LAWS.items():
            for rel in [*texts, f"law_revisions/{law_id}.xml"]:
                meta = json.loads((API / f"{rel}.meta.json").read_text())
                yield RawDocument(
                    f"e-gov:{Path(rel).stem}",
                    meta["url"],
                    "application/xml",
                    _read(rel),
                    datetime.fromisoformat(meta["retrieved_at"]),
                    {"law_id": law_id},
                )


class _Docs:
    def __init__(self, docs: list[RawDocument]):
        self.docs = docs

    def fetch(self) -> Iterator[RawDocument]:
        yield from self.docs


@pytest.fixture(scope="module")
def jp_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source("jp-egov-laws", actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="JP", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_ingestion_records_snapshots_and_change_reports(
    session: Session, catalog: Catalog, store: LocalObjectStore, jp_ingested: IngestionReport
) -> None:
    assert jp_ingested.errors == [] and jp_ingested.conflicts == []
    assert (jp_ingested.instruments_created, jp_ingested.instrument_versions_created) == (4, 7)
    # An unchanged wording continues one version across the four Penal Code snapshots.
    pv = session.scalars(
        select(m.ProvisionVersion)
        .join(m.Provision)
        .where(m.Provision.uri == "/jp/act/140AC0000000045/~article-1-paragraph-1")
    ).one()
    attested = session.scalar(
        select(func.count())
        .select_from(m.ProvisionVersionSource)
        .where(m.ProvisionVersionSource.provision_version_id == pv.id)
    )
    assert (pv.valid_from, pv.valid_to, attested) == (date(2016, 6, 23), None, 4)
    appi = session.scalar(
        select(m.InstrumentVersion).where(m.InstrumentVersion.uri == "/jp/act/415AC0000000057/jpn@2026-10-01")
    )
    assert appi is not None and len(appi.outstanding_changes) == 6
    assert appi.currency_date == date(2026, 10, 14)  # the revision list, retrieved that day, attests it as current
    # The revision list records no text; the amendments it reports are re-derived from it on verification.
    listing = session.scalars(
        select(m.SourceDocument).where(m.SourceDocument.external_id == "e-gov:415AC0000000057")
    ).one()
    result = verify_source_document(session, catalog, store, listing.id)
    assert result["result"] == "pass" and result["checked_units"] > 0


@pytest.mark.db
def test_point_in_time_follows_enforcement_dates(
    session: Session, catalog: Catalog, jp_ingested: IngestionReport
) -> None:
    uri = "/jp/act/140AC0000000045/~article-177"
    assert provision_at(session, catalog, uri, date(1900, 1, 1))["status_on_date"] == "not_yet_in_force"  # before 1907
    # Promulgated 1907, but e-Gov history starts with the revision in force on 2016-06-23.
    assert provision_at(session, catalog, uri, date(2010, 1, 1))["status_on_date"] == "history_not_indexed"
    v2017 = provision_at(session, catalog, uri, date(2017, 7, 12))
    assert v2017["status_on_date"] == "in_force" and v2017["version"]["heading"] == "（強姦）"
    v2020 = provision_at(session, catalog, uri, date(2017, 7, 13))  # the amendment's enforcement date itself
    assert v2020["version"]["heading"] == "（強制性交等）" and v2020["version"]["valid_from"] == "2017-07-13"
    assert v2020["version"]["valid_to"] == "2023-07-13" and v2020["version"]["valid_to_basis"] == "derived"
    assert v2020["provision"]["citation"] == "刑法第177条"
    v2024 = provision_at(session, catalog, uri, date(2024, 1, 1))
    assert v2024["version"]["heading"] == "（不同意性交等）" and v2024["version"]["valid_from"] == "2023-07-13"


@pytest.mark.db
def test_deleted_and_removed_provisions(session: Session, catalog: Catalog, jp_ingested: IngestionReport) -> None:
    art = "/jp/act/140AC0000000045/~article-178"
    assert provision_at(session, catalog, art, date(2020, 1, 1))["status_on_date"] == "in_force"
    after = provision_at(session, catalog, art, date(2024, 1, 1))
    assert after["status_on_date"] == "repealed" and after["version"]["note"] == "削除"
    assert "REPEALED" in {lim["code"] for lim in after["limitations"]}
    # Article 178's paragraphs are no longer in the text from 2023-07-13: their versions end there.
    para = "/jp/act/140AC0000000045/~article-178-paragraph-2"
    before = provision_at(session, catalog, para, date(2020, 1, 1))
    assert before["status_on_date"] == "in_force" and before["version"]["valid_to"] == "2023-07-13"
    assert before["version"]["valid_to_basis"] == "explicit"
    assert provision_at(session, catalog, para, date(2024, 1, 1))["status_on_date"] == "no_version"


@pytest.mark.db
def test_japanese_search_finds_provisions_with_citations_and_limitations(
    session: Session, catalog: Catalog, jp_ingested: IngestionReport
) -> None:
    res = _search(session, catalog, q="故意又は過失によって他人の権利", as_of=date(2026, 10, 1), query_language="ja")
    hit = res["results"][0]
    assert hit["citation"] == "民法第709条"
    assert hit["text"].startswith("故意又は過失によって他人の権利又は法律上保護される利益を侵害した者は")
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"REVISED_TEXT", "SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED", "OUTSTANDING_CHANGES_INSTRUMENT"} <= codes
    assert hit["source"]["attribution"].startswith("出典：e-Gov法令検索（デジタル庁）")
    appi = _search(
        session, catalog, q="個人データを第三者に提供", as_of=date(2026, 10, 14), query_language="ja", limit=20
    )
    assert "個人情報の保護に関する法律第27条第1項" in {h["citation"] for h in appi["results"]}
    item = _search(
        session, catalog, q="人の生命、身体又は財産の保護のために必要がある場合", as_of=date(2026, 10, 14), limit=50
    )
    assert "個人情報の保護に関する法律第27条第1項第2号" in {h["citation"] for h in item["results"]}


@pytest.mark.db
def test_historical_search_returns_the_wording_then_in_force(
    session: Session, catalog: Catalog, jp_ingested: IngestionReport
) -> None:
    res = _search(session, catalog, q="強制性交等の罪", as_of=date(2020, 1, 1), query_language="ja")
    hit = next(h for h in res["results"] if h["citation"] == "刑法第177条")
    assert "五年以上の有期懲役" in hit["text"] and hit["version"]["valid_from"] == "2017-07-13"
    now = _search(session, catalog, q="強制性交等の罪", as_of=date(2026, 10, 1), query_language="ja")
    assert not any(h["citation"] == "刑法第177条" for h in now["results"])  # that wording no longer applies


@pytest.mark.db
def test_snapshots_must_be_recorded_oldest_first(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, jp_ingested: IngestionReport
) -> None:
    older = _read(PENAL_CODE[0]).replace(
        b"<amendment_enforcement_date>2016-06-23<", b"<amendment_enforcement_date>2015-01-01<"
    )
    raw = RawDocument("test:older", "https://example.org/older", "application/xml", older, datetime.now(UTC), {})
    with session_factory() as s:
        report = Ingestor(s, catalog, store).ingest_source("jp-egov-laws", actor="pytest", connector=_Docs([raw]))
        s.rollback()
    assert len(report.errors) == 1 and "oldest first" in report.errors[0]
