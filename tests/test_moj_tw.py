"""全國法規資料庫 (Taiwan) parser, fetcher and Chinese search behaviour (real law.moj.gov.tw pages, OGDL 1.0)."""

from __future__ import annotations

import gzip
import io
import json
import shutil
import ssl
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport, Ingestor, build_connector
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.jurisdictions.modules.tw import number
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors import polite_http
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import FetchError, MojLawsConnector, robots_allows, tls_context
from ulis.sources.connectors.polite_http import PoliteHttp
from ulis.sources.parsers.moj_law import MojParseError, not_in_force, numeral, parse_moj_law, roc_date
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

SOURCE = "tw-moj-laws"
FIX = REPO_ROOT / "tests" / "fixtures" / "moj_laws" / "cache"
HOST = FIX / "law.moj.gov.tw"
PCODES = ["A0000001", "A0000002", "B0000001", "C0000001", "I0050021", "N0030001"]
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
# The 2023 and 2025 amendments of the Personal Data Protection Act, not yet in force (生效狀態 memo).
PDPA_PENDING = {
    "1-1", "1-2", "12", "18", "20-1", "21", "21-1", "21-2", "21-3", "21-4", "21-5", "22", "23",
    "24", "25", "26", "27", "41", "47", "48", "49", "51-1", "52", "53", "53-1", "55",
}  # fmt: skip


def _page(pcode: str) -> bytes:
    """Fixtures are the pages as received, stored gzip-compressed."""
    return gzip.decompress((HOST / "LawClass" / "LawAll" / f"{pcode}.html.gz").read_bytes())


def _doc(pcode: str):  # type: ignore[no-untyped-def]
    docs = parse_moj_law(_page(pcode))
    assert len(docs) == 1
    return docs[0]


def _by_eid(doc) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {p.eid: p for p in doc.provisions}


def _cite(doc, eid: str) -> str:  # type: ignore[no-untyped-def]
    p = _by_eid(doc)[eid]
    return get_module("tw").provision_citation(
        ProvisionCitationInput(
            doc.short_title, doc.instrument_type, "TW", p.top_kind, p.num_path, doc.language, ext=doc.ext, eid=p.eid
        )
    )


# --------------------------------------------------------------------------- parser


def test_constitution_preamble_items_and_citations() -> None:
    doc = _doc("A0000001")
    assert (doc.instrument_type, doc.work_uri, doc.language) == ("constitution", "/tw/constitution/A0000001", "zh-Hant")
    assert doc.expression_uri == "/tw/constitution/A0000001/zho@2026-10-02"
    # Never amended: the page states 公布日期 (promulgation) and no 修正日期.
    assert (doc.work_date, doc.version_date, doc.currency_date) == (
        date(1947, 1, 1),
        date(2026, 10, 2),
        date(2026, 10, 2),
    )
    assert (doc.text_origin, doc.consolidation_kind, doc.authoritative) == ("official_conversion", "as_made", False)
    assert doc.validity_basis == "attested_at_edition" and doc.ext == {"pcode": "A0000001", "category": "憲法"}
    p = _by_eid(doc)
    assert p["preamble"].text.startswith("中華民國國民大會受全體國民之付託，依據孫中山先生創立中華民國之遺教")
    assert all(v.valid_from is None and v.status == "in_force" for v in doc.provisions)
    # Items of the Constitution are labelled "一  " (no 、); the label is not part of the item's text.
    assert p["article-107"].text.startswith("左列事項，由中央立法並執行之：\n一  外交。\n二  國防與國防軍事。")
    assert p["article-107-item-1"].text == "外交。" and p["article-107"].has_own_content
    # ... or with an ideographic space (U+3000), kept as printed in the article's text.
    assert p["article-26"].text.startswith("國民大會以左列代表組織之：\n一" + chr(0x3000) + "每縣市及其同等區域")
    assert p["article-26-item-1"].text.startswith("每縣市及其同等區域各選出代表一人")
    assert [e for e in p if e.startswith("article-8-paragraph-")] == [f"article-8-paragraph-{i}" for i in range(1, 5)]
    assert p["article-171"].text == "法律與憲法牴觸者無效。\n法律與憲法有無牴觸發生疑義時，由司法院解釋之。"
    assert _cite(doc, "article-8-paragraph-2") == "憲法第8條第2項"
    assert _cite(doc, "article-107-item-1") == "憲法第107條第1款"
    assert _cite(doc, "preamble") == "憲法前言"
    assert p["article-107-item-1"].context_heading == "第 十 章 中央與地方之權限"


def test_additional_articles() -> None:
    doc = _doc("A0000002")
    p = _by_eid(doc)
    assert doc.instrument_type == "constitution" and doc.text_origin == "official_revised"
    assert doc.work_date is None  # amended: the original promulgation is in the history page, not this one
    assert doc.ext["amended_on"] == "2005-06-10"
    assert p["article-1-paragraph-2"].text == "憲法第二十五條至第三十四條及第一百三十五條之規定，停止適用。"
    assert _cite(doc, "article-1-paragraph-2") == "憲法增修條文第1條第2項"
    assert _cite(doc, "preamble") == "憲法增修條文前言"
    assert [e for e, v in p.items() if v.kind == "article"] == [f"article-{i}" for i in range(1, 13)]


def test_civil_code_paragraphs_deleted_and_not_yet_in_force() -> None:
    doc = _doc("B0000001")
    p = _by_eid(doc)
    tort = p["article-184-paragraph-1"]
    assert tort.text == (
        "因故意或過失，不法侵害他人之權利者，負損害賠償責任。故意以背於善良風俗之方法，加損害於他人者亦同。"
    )
    assert (tort.status, tort.valid_from) == ("in_force", None)
    assert tort.context_heading == "第 二 編 債 第 一 章 通則 第 一 節 債之發生 第 五 款 侵權行為"
    assert _cite(doc, "article-184-paragraph-1") == "民法第184條第1項"
    assert _cite(doc, "article-184") == "民法第184條"
    assert (p["article-219"].status, p["article-219"].text, p["article-219"].note) == ("repealed", "", "（刪除）")
    # 生效狀態: art 166-1 (added 1999, date to be fixed) and art 1223 (amended 2026, six months after
    # promulgation) are shown in a wording not yet in force.
    assert {e for e, v in p.items() if v.status == "prospective" and v.kind == "article"} == {
        "article-166_1",
        "article-1223",
    }
    assert p["article-1223-item-4"].status == "prospective" and "自公布六個月後施行" in (p["article-1223"].note or "")
    assert _cite(doc, "article-166_1-paragraph-2") == "民法第166條之1第2項"
    assert doc.ext["amended_on"] == "2026-08-17"
    assert [(e.event_type, e.date) for e in doc.events] == [
        ("amendment", date(2026, 8, 17)),
        ("version_change", date(2026, 10, 2)),
    ]


def test_criminal_code_conventional_title_and_inserted_articles() -> None:
    doc = _doc("C0000001")
    p = _by_eid(doc)
    assert doc.title == "中華民國刑法" and doc.instrument_type == "act"
    assert p["article-271-paragraph-1"].text == "殺人者，處死刑、無期徒刑或十年以上有期徒刑。"
    assert _cite(doc, "article-271-paragraph-1") == "刑法第271條第1項"
    # A single-line article whose line is indented like an item but has no label stays one provision.
    assert p["article-227_1"].text == "十八歲以下之人犯前條之罪者，減輕或免除其刑。" and p["article-227_1"].is_leaf
    assert _cite(doc, "article-227_1") == "刑法第227條之1"
    assert "本條文有附件" in (p["article-185_3"].note or "")  # attachments are files, not ingested


def test_pdpa_items_and_amendments_not_in_force() -> None:
    doc = _doc("I0050021")
    p = _by_eid(doc)
    item = p["article-6-paragraph-1-item-2"]
    assert item.text == "公務機關執行法定職務或非公務機關履行法定義務必要範圍內，且事前或事後有適當安全維護措施。"
    assert item.status == "in_force"
    chapeau = p["article-6-paragraph-1"]
    assert chapeau.has_own_content and "\n二、公務機關執行法定職務" in chapeau.text  # items as printed
    assert _cite(doc, "article-6-paragraph-1-item-2") == "個人資料保護法第6條第1項第2款"
    assert _cite(doc, "article-2-item-1") == "個人資料保護法第2條第1款"  # single-paragraph article
    assert _cite(doc, "article-21_1") == "個人資料保護法第21條之1"
    pending = {v.num_path[0] for v in doc.provisions if v.kind == "article" and v.status == "prospective"}
    assert pending == PDPA_PENDING  # ranges 22～26, 47～49 and 21-1～21-5 expanded in document order
    assert all(v.status == "prospective" for v in doc.provisions if v.num_path and v.num_path[0] in PDPA_PENDING)
    deleted = p["article-27"]  # deletion not yet in force: the article still applies in its old wording
    assert (deleted.status, deleted.text) == ("prospective", "") and deleted.note.startswith("（刪除） 一百十四年")
    assert p["article-12-paragraph-1"].note.startswith("一百十四年十一月十一日修正第 1-1、12、18")
    assert "一百十二年五月三十一日增訂之第 1-1 條" in p["article-1_1"].note  # named by both memo items


def test_labor_standards_subitems() -> None:
    doc = _doc("N0030001")
    p = _by_eid(doc)
    assert p["article-59-item-4-subitem-1"].text == "配偶及子女。"
    assert p["article-59-item-4"].text.endswith(
        "\n（一）配偶及子女。\n（二）父母。\n（三）祖父母。\n（四）孫子女。\n（五）兄弟姐妹。"
    )
    assert _cite(doc, "article-59-item-4-subitem-1") == "勞動基準法第59條第4款第1目"
    assert sum(v.kind == "subitem" for v in doc.provisions) == 5


def test_values_and_memo_parsing() -> None:
    assert [numeral(n) for n in ("一", "十", "十一", "二十", "九十九", "一百零一", "一百十")] == [
        1, 10, 11, 20, 99, 101, 110,
    ]  # fmt: skip
    assert roc_date("民國 115 年 10 月 02 日") == date(2026, 10, 2)
    assert number("21_1", "條") == "第21條之1"
    order = ["1", "1-1", "2", "3", "3-1", "4", "5"]
    assert set(not_in_force(["修正第 2～4 條條文，施行日期，由行政院定之。"], order)) == {"2", "3", "3-1", "4"}
    with pytest.raises(MojParseError, match="no article named"):
        not_in_force(["本法施行日期，由行政院定之。"], order)
    with pytest.raises(MojParseError, match="cannot resolve the range"):
        not_in_force(["修正第 4～2 條條文"], order)


def test_parser_refuses_what_it_cannot_date_or_read() -> None:
    page = _page("I0050021")
    with pytest.raises(MojParseError, match="not a law full-text page"):
        parse_moj_law(b"<html><body>Service unavailable</body></html>")
    repealed = page.replace("<th>修正日期：</th>".encode(), "<th>廢止日期：</th>".encode())
    with pytest.raises(MojParseError, match="repealed laws"):
        parse_moj_law(repealed)
    # A 生效狀態 memo that names no article: refuse rather than present unknown text as in force.
    vague = page.replace("修正第&nbsp;1-1、12、18".encode(), "修正本法".encode()).replace(
        "增訂之第&nbsp;1-1&nbsp;&nbsp;條條文".encode(), "增訂條文".encode()
    )
    with pytest.raises(MojParseError, match="no article named"):
        parse_moj_law(vague)
    no_cutoff = page.replace("法規整編資料截止日".encode(), "更新日".encode())
    with pytest.raises(MojParseError, match="cut-off"):
        parse_moj_law(no_cutoff)


# --------------------------------------------------------------------------- connector


def test_robots_rules() -> None:
    robots = gzip.decompress((HOST / "robots.txt.gz").read_bytes()).decode()
    allowed = "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=B0000001"
    assert robots_allows(robots, UA, allowed)
    for url in (
        "https://law.moj.gov.tw/api/ch/law/json",
        "https://law.moj.gov.tw/LawClass/LawHistory.aspx?pcode=B0000001",
        "https://law.moj.gov.tw/ENG/LawClass/LawAll.aspx?pcode=B0000001",
        "https://law.moj.gov.tw/",
    ):
        assert not robots_allows(robots, UA, url), url
    own_group = "User-agent: ULIS-research-bot\nDisallow: /LawClass/\n\nUser-agent: *\nAllow: /\n"
    assert not robots_allows(own_group, UA, allowed)
    assert robots_allows("User-agent: *\nDisallow:\n", UA, allowed)
    assert robots_allows("User-agent: *\nDisallow: /*.aspx$\n", UA, "https://law.moj.gov.tw/a.aspx?x=1")


def _cache_copy(dest: Path) -> Path:
    """Copy the fixtures into the connector's cache layout, decompressed."""
    shutil.copytree(FIX, dest)
    for gz in dest.rglob("*.gz"):
        gz.with_suffix("").write_bytes(gzip.decompress(gz.read_bytes()))
        gz.unlink()
    return dest


def _manifest(tmp_path: Path, laws: list[dict[str, Any]], **overrides: Any) -> Path:
    manifest = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": "https://law.moj.gov.tw",
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "laws": laws,
        **overrides,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True))
    return path


def test_connector_offline_from_cache(tmp_path: Path) -> None:
    cache = _cache_copy(tmp_path / "cache")
    connector = MojLawsConnector(_manifest(tmp_path, [{"pcode": p} for p in PCODES]), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [f"moj-law:{p}" for p in PCODES]
    assert docs[0].uri == "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0000001"
    assert docs[0].media_type == "text/html" and docs[0].content == _page("A0000001")
    meta = json.loads((cache / "law.moj.gov.tw/LawClass/LawAll/A0000001.html.meta.json").read_text())
    assert docs[0].retrieved_at.isoformat() == meta["retrieved_at"]
    real = MojLawsConnector(REPO_ROOT / "config/manifests/tw-moj-laws.yaml", SOURCE, cache, offline=True)
    assert len(real._manifest()["laws"]) == 8


def test_connector_respects_robots_and_validates_manifest(tmp_path: Path) -> None:
    cache = _cache_copy(tmp_path / "cache")
    (cache / "law.moj.gov.tw/robots.txt").write_text("User-agent: *\nDisallow: /\n")
    with pytest.raises(FetchError, match="disallowed by https://law.moj.gov.tw/robots.txt"):
        list(MojLawsConnector(_manifest(tmp_path, [{"pcode": "B0000001"}]), SOURCE, cache, offline=True).fetch())
    laws = [{"pcode": "B0000001"}]
    with pytest.raises(FetchError, match="offline and not cached"):
        list(MojLawsConnector(_manifest(tmp_path, laws), SOURCE, tmp_path / "empty", offline=True).fetch())
    with pytest.raises(ManifestError, match="user_agent"):
        list(MojLawsConnector(_manifest(tmp_path, laws, user_agent="python-urllib"), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="base_url"):
        list(MojLawsConnector(_manifest(tmp_path, laws, base_url="http://law.moj.gov.tw"), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="pcode"):
        list(MojLawsConnector(_manifest(tmp_path, [{"pcode": "../x"}]), SOURCE, tmp_path).fetch())


def test_tls_context_still_verifies() -> None:
    context = tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert not context.verify_flags & ssl.VERIFY_X509_STRICT
    insecure = ssl.create_default_context()
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE
    with pytest.raises(ValueError, match="must verify"):
        PoliteHttp(Path("."), UA, allowed_hosts={"law.moj.gov.tw"}, ssl_context=insecure)


def test_polite_http_passes_the_tls_context_only_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    class _Response(io.BytesIO):
        status = 200
        headers: dict[str, str] = {"Content-Type": "text/html"}

        def geturl(self) -> str:
            return "https://law.moj.gov.tw/a"

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *exc: Any) -> None:
            pass

    def fake_urlopen(request: Any, timeout: float, **kwargs: Any) -> _Response:
        seen.append(kwargs)
        return _Response(b"<html/>")

    monkeypatch.setattr(polite_http.urllib.request, "urlopen", fake_urlopen)
    context = tls_context()
    for ctx, path in ((context, "a"), (None, "b")):
        http = PoliteHttp(tmp_path, UA, allowed_hosts={"law.moj.gov.tw"}, sleep=lambda s: None, ssl_context=ctx)
        http.get(f"https://law.moj.gov.tw/{path}")
    assert seen == [{"context": context}, {}]


def test_registered_connector(catalog: Catalog) -> None:
    assert isinstance(build_connector(catalog.sources.get(SOURCE)), MojLawsConnector)


# --------------------------------------------------------------------------- PostgreSQL


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for pcode in PCODES:
            meta = json.loads((HOST / "LawClass" / "LawAll" / f"{pcode}.html.meta.json").read_text())
            yield RawDocument(
                f"moj-law:{pcode}",
                meta["final_url"],
                "text/html",
                _page(pcode),
                datetime.fromisoformat(meta["retrieved_at"]),
                {"pcode": pcode},
            )


@pytest.fixture(scope="module")
def tw_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="TW", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_chinese_search_finds_provisions_with_citations_and_limitations(
    session: Session, catalog: Catalog, tw_ingested: IngestionReport
) -> None:
    assert tw_ingested.errors == [] and tw_ingested.conflicts == []
    assert (tw_ingested.instruments_created, tw_ingested.instrument_versions_created) == (6, 6)
    res = _search(
        session, catalog, q="因故意或過失，不法侵害他人之權利", as_of=date(2026, 10, 2), query_language="zh-Hant"
    )
    hit = res["results"][0]
    assert hit["citation"] == "民法第184條第1項"
    assert hit["text"].startswith("因故意或過失，不法侵害他人之權利者，負損害賠償責任。")
    assert hit["authority"]["category"] == "applicability_unverified"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"ATTESTED_ONLY_AT", "REVISED_TEXT", "SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED"} <= codes
    assert "NOT_AUTHENTIC_TEXT" not in codes and "OUTSTANDING_CHANGES_INSTRUMENT" not in codes
    assert hit["source"]["attribution"].startswith("法務部 2026 全國法規資料庫")
    murder = _search(session, catalog, q="殺人者，處死刑、無期徒刑", as_of=date(2026, 10, 2))
    assert murder["results"][0]["citation"] == "刑法第271條第1項"
    # Bigram search: "殺人者" alone also matches e.g. 刑法第332條 (強盜而故意殺人者).
    assert "刑法第271條第1項" in {h["citation"] for h in _search(session, catalog, q="殺人者", limit=20)["results"]}
    # A question made of several unspaced terms: every term need not be in one provision (terms are ORed, ranked).
    multi = _search(session, catalog, q="殺人者 處死刑 賠償", as_of=date(2026, 10, 2), limit=20)
    assert "刑法第271條第1項" in {h["citation"] for h in multi["results"]}
    pdpa = _search(session, catalog, q="法定職務或非公務機關履行法定義務必要範圍內", limit=50)
    assert "個人資料保護法第6條第1項第2款" in {h["citation"] for h in pdpa["results"]}
    sub = _search(session, catalog, q="配偶及子女", limit=50)
    assert "勞動基準法第59條第4款第1目" in {h["citation"] for h in sub["results"]}


@pytest.mark.db
def test_amended_wording_not_in_force_is_prospective(
    session: Session, catalog: Catalog, tw_ingested: IngestionReport
) -> None:
    q = "知悉所保有之個人資料被竊取、竄改、毀損、滅失或洩漏"
    now = _search(session, catalog, q=q, as_of=date(2026, 10, 2), limit=50)
    assert "個人資料保護法第12條第1項" not in {h["citation"] for h in now["results"]}
    later = _search(session, catalog, q=q, as_of=date(2026, 10, 2), include_prospective=True, limit=50)
    hit = next(h for h in later["results"] if h["citation"] == "個人資料保護法第12條第1項")
    assert hit["authority"]["category"] == "prospective_legislation"
    assert "NOT_IN_FORCE" in {lim["code"] for lim in hit["limitations"]}
    pit = provision_at(session, catalog, "/tw/act/I0050021/~article-12-paragraph-1", date(2026, 10, 2))
    assert pit["status_on_date"] == "prospective_only"


@pytest.mark.db
def test_point_in_time_is_attested_only_at_the_cut_off_date(
    session: Session, catalog: Catalog, tw_ingested: IngestionReport
) -> None:
    uri = "/tw/act/B0000001/~article-184-paragraph-1"
    at = provision_at(session, catalog, uri, date(2026, 10, 2))
    assert at["status_on_date"] == "in_force_date_unverified"
    assert at["version"]["currency_date"] == "2026-10-02"
    assert at["provision"]["citation"] == "民法第184條第1項"
    earlier = provision_at(session, catalog, uri, date(1990, 1, 1))
    # Only the current wording is published: 1990 is not "not yet in force", it is unknown.
    assert earlier["status_on_date"] == "in_force_date_unverified"
    assert "ATTESTED_ONLY_AT" in {lim["code"] for lim in earlier["limitations"]}
    deleted = provision_at(session, catalog, "/tw/act/B0000001/~article-219", date(2026, 10, 2))
    assert [(t["status"], t["note"]) for t in deleted["timeline"]] == [("repealed", "（刪除）")]
    res = _search(session, catalog, q="殺人者", as_of=date(1990, 1, 1))
    assert "HISTORY_INCOMPLETE" in {lim["code"] for lim in res["limitations"]}


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, tw_ingested: IngestionReport
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        assert again.documents_unchanged == len(PCODES) and again.provision_versions_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == len(PCODES) and all(r["result"] == "pass" for r in results)
        assert sum(r["checked_units"] for r in results) == tw_ingested.provision_versions_created
        session.rollback()
