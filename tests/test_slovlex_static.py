"""Slovakia: Slov-lex static time versions (HTML) - parser, citations, connector, PostgreSQL."""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport, Ingestor, build_connector, resolve_uri
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.connectors.slovlex_static import SlovLexStaticConnector, history_versions, select_versions
from ulis.sources.normalized import ParsedDocument, ParsedProvision
from ulis.sources.parsers.slovlex_static import SlovLexParseError, parse_slovlex_static
from ulis.sources.registry import LicenceNotApproved, licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = Path(__file__).parent / "fixtures" / "sk-slovlex-static"
REPO = Path(__file__).parents[1]
SOURCE = "sk-slovlex-static"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
USTAVA = "/sk/zz/1992/460"
OU = "/sk/zz/2018/18"
# Oldest first within each work (whole-act snapshots).
INGESTED = [
    "ustava-19921001",
    "ustava-20010701",
    "ustava-20251101",
    "ochrana-udajov-20180525",
    "ochrana-udajov-20260818",
    "novela-340-2015-20160101",
]


def _raw(name: str) -> bytes:
    return gzip.decompress((FIX / f"{name}.html.gz").read_bytes())


def _doc(name: str) -> ParsedDocument:
    docs = parse_slovlex_static(_raw(name))
    assert len(docs) == 1
    return docs[0]


def _p(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return next(p for p in doc.provisions if p.eid == eid)


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _p(doc, eid)
    return get_module("sk").provision_citation(
        ProvisionCitationInput(
            doc.title,
            doc.instrument_type,
            "SK",
            p.top_kind,
            p.num_path,
            doc.language,
            doc.ext,
            version_date=doc.version_date,
        )
    )


# --------------------------------------------------------------------------- parser


def test_not_an_act_page_or_promulgated_text_is_rejected() -> None:
    with pytest.raises(SlovLexParseError, match="not a Slov-lex act page"):
        parse_slovlex_static(b"<html><body><p>nothing</p></body></html>")
    promulgated = _raw("ustava-20251101").replace(b'data-from="2025-11-01"', b'data-from=""')
    with pytest.raises(SlovLexParseError, match="no effective date"):
        parse_slovlex_static(promulgated)


def test_constitution_structure_dates_and_origin() -> None:
    doc = _doc("ustava-20251101")
    assert doc.instrument_type == "constitution" and doc.title == "Ústava Slovenskej republiky"
    assert doc.number == "460/1992 Zb." and doc.work_date == date(1992, 9, 1)
    assert doc.work_uri == USTAVA and doc.expression_uri == f"{USTAVA}/slk@2025-11-01"
    assert doc.language == "sk" and doc.authoritative is False
    assert doc.text_origin == "official_consolidation" and doc.validity_basis == "revision_snapshot"
    assert doc.version_date == date(2025, 11, 1) and doc.ext == {"year": 1992, "number": "460", "collection": "Zb."}
    pre = _p(doc, "preamble")
    assert pre.top_kind == "preamble" and pre.text.startswith("PREAMBULA\nMy, národ slovenský,")
    c2 = _p(doc, "ustavnyclanok-2.odsek-1")
    assert c2.num_path == ["art:2", "sub:1"] and c2.parent_eid == "ustavnyclanok-2" and c2.is_leaf
    assert c2.text.startswith("Štátna moc pochádza od občanov, ktorí ju vykonávajú")
    assert c2.text.endswith("prostredníctvom svojich volených zástupcov alebo priamo.")
    assert _p(doc, "ustavnyclanok-2").is_leaf is False
    assert "ODDIEL" in (c2.context_heading or "").upper() or c2.context_heading
    assert all(p.valid_from == date(2025, 11, 1) and p.valid_to is None for p in doc.provisions)
    assert all(p.status == "in_force" and p.extent == ["SK"] for p in doc.provisions)
    assert len({p.eid for p in doc.provisions}) == len(doc.provisions)


def test_the_constitution_across_versions() -> None:
    old, mid = _doc("ustava-19921001"), _doc("ustava-20010701")
    # A version that ended before the next one: the period runs to the day after its last day.
    assert (old.version_date, _p(old, "ustavnyclanok-25.odsek-1").valid_to) == (date(1992, 10, 1), date(1998, 8, 5))
    assert _p(mid, "ustavnyclanok-25.odsek-1").valid_to == date(2002, 1, 1)
    assert _p(old, "ustavnyclanok-25.odsek-1").text == "Obrana Slovenskej republiky je vecou cti každého občana."
    assert _p(mid, "ustavnyclanok-25.odsek-1").text.startswith("Obrana Slovenskej republiky je povinnosťou a vecou cti")
    # Article 1 had a single unnumbered paragraph in 1992; later it is split into numbered paragraphs.
    assert _p(old, "ustavnyclanok-1").is_leaf and _p(old, "ustavnyclanok-1").text.startswith(
        "Slovenská republika je zvrchovaný"
    )
    assert _p(mid, "ustavnyclanok-1").text == "" and _p(mid, "ustavnyclanok-1.odsek-1").num_path == ["art:1", "sub:1"]


def test_act_with_parts_footnotes_references_and_amending_articles() -> None:
    doc = _doc("ochrana-udajov-20260818")
    assert doc.instrument_type == "act" and doc.number == "18/2018 Z. z." and doc.work_date == date(2017, 11, 29)
    assert doc.title.startswith("Zákon o ochrane osobných údajov a o zmene a doplnení niektorých zákonov")
    # The article that holds the act itself is not part of the citation path.
    p1 = _p(doc, "paragraf-1")
    assert p1.num_path == ["sec:1"] and p1.heading == "Predmet úpravy" and p1.text == "Tento zákon upravuje"
    a = _p(doc, "paragraf-1.odsek-1.pismeno-a")
    assert a.num_path == ["sec:1", "pt:a"] and a.parent_eid == "paragraf-1"
    assert a.text.startswith("ochranu práv fyzických osôb pred neoprávneným spracúvaním")
    # Footnote markers stay in the text as superscripts; the footnote text is the provision's note.
    assert "²⁶)" in _p(doc, "paragraf-79.odsek-3").text
    assert (_p(doc, "paragraf-79.odsek-3").note or "").startswith("26)")
    assert (_p(doc, "paragraf-2").note or "").startswith("1) § 57 ods. 2 zákona č. 351/2011 Z. z.")
    # References within the act and to other acts.
    hrefs = {r.href for r in _p(doc, "paragraf-79.odsek-4").refs}
    assert f"{OU}/~paragraf-45.odsek-5" in hrefs
    assert any(r.href == "/sk/zz/2011/351" for p in doc.provisions for r in p.refs)
    # Amending articles (Čl. II ...) are provisions with numbered points.
    art = next(p for p in doc.provisions if p.num_path[:1] == ["art:II"])
    assert art.kind == "article" and any(p.num_path[:2] == ["art:II", "item:1"] for p in doc.provisions)
    assert resolve_uri(f"{OU}/~paragraf-45.odsek-5") == (OU, "paragraf-45.odsek-5")


def test_amending_act_and_quoted_text() -> None:
    doc = _doc("novela-340-2015-20160101")
    assert doc.number == "340/2015 Z. z." and doc.version_date == date(2016, 1, 1)
    b3 = _p(doc, "predpis.clanok-1.bod-3")
    assert b3.num_path == ["art:I", "item:3"]
    assert b3.text.startswith("V § 21c ods. 1 písm. c) sa na konci čiarka nahrádza bodkočiarkou")
    # The quoted new wording of another act (and its footnote) belongs to the amending point.
    assert "udelila súhlas" in b3.text and "²³)" in b3.text and "Poznámka pod čiarou k odkazu 23 znie:" in b3.text
    assert doc.events[0].event_type == "version_change"


def test_repealed_marker_in_text_makes_a_repealed_provision() -> None:
    page = (
        b'<h1>1/2000 Z. z.</h1><div class="ucinnost_header" data-from="2020-01-01" data-to="">x</div>'
        b'<div class="predpis Skupina " id="predpis"><div id="predpis.typ" class="predpisTyp">Z\xc3\x81KON</div>'
        b'<div class="predpisNadpis NADPIS" id="predpis.nadpis">o veciach</div>'
        b'<div class="paragraf Skupina " id="paragraf-1"><div class="paragrafOznacenie">\xc2\xa7 1</div>'
        b'<div class="text" id="paragraf-1.text">Zru\xc5\xa1en\xc3\xbd</div></div></div>'
    )
    p = parse_slovlex_static(page)[0].provisions[0]
    assert p.status == "repealed" and p.text == "" and p.note == "Zrušený" and p.num_path == ["sec:1"]


def test_a_code_named_by_its_title_is_an_act() -> None:
    page = (
        b'<h1>40/1964 Zb.</h1><div class="ucinnost_header" data-from="2020-01-01" data-to="2020-12-31">x</div>'
        b'<div class="predpis Skupina " id="predpis">'
        b'<div id="predpis.typ" class="predpisTyp">OB\xc4\x8cIANSKY Z\xc3\x81KONN\xc3\x8dK</div>'
        b'<div class="paragraf Skupina " id="paragraf-420"><div class="paragrafOznacenie">\xc2\xa7 420</div>'
        b'<div class="odsek Skupina " id="paragraf-420.odsek-1"><div class="odsekOznacenie">(1)</div>'
        b'<div class="text">Ka\xc5\xbed\xc3\xbd zodpoved\xc3\xa1 za \xc5\xa1kodu.</div></div></div></div>'
    )
    doc = parse_slovlex_static(page)[0]
    assert (doc.instrument_type, doc.title, doc.number) == ("act", "Občiansky zákonník", "40/1964 Zb.")
    assert doc.provisions[1].valid_to == date(2021, 1, 1) and doc.work_uri == "/sk/zz/1964/40"


# --------------------------------------------------------------------------- citations


def _c(path: list[str], year: int, number: str, collection: str, title: str, itype: str = "act") -> str:
    return get_module("sk").provision_citation(
        ProvisionCitationInput(
            title,
            itype,
            "SK",
            "section",
            path,
            "sk",
            {"year": year, "number": number, "collection": collection},
        )
    )


def test_citations() -> None:
    assert _c(["sec:420"], 1964, "40", "Zb.", "Zákon Občiansky zákonník") == "§ 420 Občianskeho zákonníka"
    assert _c(["sec:144", "sub:1"], 2005, "300", "Z. z.", "Zákon Trestný zákon") == "§ 144 ods. 1 Trestného zákona"
    assert (
        _c(["sec:13", "sub:1", "pt:a"], 2001, "311", "Z. z.", "Zákon Zákonník práce")
        == "§ 13 ods. 1 písm. a) Zákonníka práce"
    )
    assert (
        _c(["art:2", "sub:1"], 1992, "460", "Zb.", "Ústava", "constitution")
        == "čl. 2 ods. 1 Ústavy Slovenskej republiky"
    )
    assert (
        _c(["sec:5", "sub:2", "pt:a", "item:1"], 2018, "18", "Z. z.", "Zákon o ochrane osobných údajov")
        == "§ 5 ods. 2 písm. a) bod 1 zákona č. 18/2018 Z. z."
    )
    assert (
        _c(["art:II", "item:1"], 2015, "340", "Z. z.", "Zákon ktorým sa mení")
        == "čl. II bod 1 zákona č. 340/2015 Z. z."
    )
    assert (
        _c(["sec:3"], 2020, "50", "Z. z.", "Nariadenie vlády Slovenskej republiky o veciach")
        == "§ 3 nariadenia vlády č. 50/2020 Z. z."
    )
    assert (
        _c(["preamble:preambula"], 1992, "460", "Zb.", "Ústava", "constitution")
        == "preambula Ústavy Slovenskej republiky"
    )
    doc = _doc("ustava-20251101")
    assert _cite(doc, "ustavnyclanok-2.odsek-1") == "čl. 2 ods. 1 Ústavy Slovenskej republiky"
    assert (
        _cite(_doc("ochrana-udajov-20260818"), "paragraf-1.odsek-1.pismeno-a") == "§ 1 písm. a) zákona č. 18/2018 Z. z."
    )


# --------------------------------------------------------------------------- connector


def _put(dest: Path, key: str, data: bytes, status: int = 200) -> None:
    file = dest / "static.slov-lex.sk" / key if not key.startswith("www:") else dest / "www.slov-lex.sk" / key[4:]
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(data)
    file.with_name(file.name + ".meta.json").write_text(
        json.dumps(
            {
                "url": "https://x",
                "final_url": "https://x",
                "status": status,
                "retrieved_at": "2026-10-14T10:55:00+00:00",
            }
        )
    )


def _cache(dest: Path, *, robots: str = "User-agent: *\nDisallow:\n") -> None:
    _put(dest, "www:robots.txt", robots.encode())
    _put(dest, "static/SK/ZZ/1992/460/history.html", _raw("history-1992-460"))
    for name in ("ustava-19921001", "ustava-20010701", "ustava-20251101"):
        _put(dest, f"static/SK/ZZ/1992/460/{name.split('-')[1]}.html", _raw(name))
    _put(dest, "static/SK/ZZ/2018/18/history.html", _raw("history-2018-18"))
    for name in ("ochrana-udajov-20180525", "ochrana-udajov-20260818"):
        _put(dest, f"static/SK/ZZ/2018/18/{name.split('-')[2]}.html", _raw(name))


def _manifest(tmp_path: Path, laws: list[dict[str, Any]], **extra: Any) -> Path:
    body = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": "https://static.slov-lex.sk",
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "as_of": "2026-10-14",
        "laws": laws,
        **extra,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(body, allow_unicode=True))
    return path


def test_history_page_and_version_policies() -> None:
    rows = history_versions(_raw("history-1992-460").decode())
    assert (
        len(rows) == 28 and rows[0] == (date(1992, 10, 1), date(1998, 8, 4)) and rows[-1] == (date(2025, 11, 1), None)
    )
    as_of = date(2026, 10, 14)
    assert len(select_versions(rows, "all", as_of)) == 28
    assert select_versions(rows, "current", as_of) == [rows[-1]]
    assert select_versions(rows, "all", date(2000, 1, 1))[-1][0] == date(1999, 1, 27)
    assert select_versions(rows, "current", date(2000, 1, 1)) == [rows[2]]
    since = select_versions(rows, {"since": "2024-06-01"}, as_of)
    assert [v[0] for v in since] == [date(2023, 7, 1), date(2025, 1, 1), date(2025, 11, 1)][-len(since) :]
    with pytest.raises(ManifestError):
        select_versions(rows, "latest", as_of)


def test_connector_offline_from_cache_yields_versions_oldest_first(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache(cache)
    manifest = _manifest(
        tmp_path, [{"act": "1992/460", "versions": {"since": "2025-01-01"}}, {"act": "2018/18", "versions": "current"}]
    )
    conn = SlovLexStaticConnector(manifest, SOURCE, cache, offline=True)
    # Only cached pages exist: the 2025-01-01 version of the Constitution was not cached, so the run stops there.
    with pytest.raises(FetchError, match="offline and not cached"):
        list(conn.fetch())
    _put(
        cache,
        "static/SK/ZZ/1992/460/20250101.html",
        _raw("ustava-20251101").replace(b'data-from="2025-11-01"', b'data-from="2025-01-01"'),
    )
    docs = list(SlovLexStaticConnector(manifest, SOURCE, cache, offline=True).fetch())
    assert [d.external_id for d in docs] == [
        "slovlex:1992/460:20250101",
        "slovlex:1992/460:20251101",
        "slovlex:2018/18:20260818",
    ]
    assert docs[0].media_type == "text/html" and docs[1].content == _raw("ustava-20251101")
    assert docs[1].metadata["version_from"] == "2025-11-01"
    assert docs[1].retrieved_at == datetime(2026, 10, 14, 10, 55, tzinfo=UTC)


def test_connector_refuses_bad_manifests_robots_and_foreign_pages(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache(cache)
    law = {"act": "2018/18", "versions": "current"}
    for bad in (
        {"schema_version": 2},
        {"source_id": "other"},
        {"base_url": "https://evil.example"},
        {"user_agent": "bot"},
        {"crawl_delay_seconds": 1},
        {"as_of": "tomorrow"},
        {"laws": []},
        {"laws": [{"act": "18/2018"}]},
        {"laws": [law, law]},
        {"laws": [{"act": "2018/18", "versions": "latest"}]},
    ):
        manifest = _manifest(tmp_path, [law])
        body = yaml.safe_load(manifest.read_text())
        body.update(bad)
        manifest.write_text(yaml.safe_dump(body))
        with pytest.raises(ManifestError):
            list(SlovLexStaticConnector(manifest, SOURCE, cache, offline=True).fetch())
    manifest = _manifest(tmp_path, [law])
    # robots.txt disallowing the static paths stops the run.
    blocked = tmp_path / "blocked"
    _cache(blocked, robots="User-agent: *\nDisallow: /static/\n")
    with pytest.raises(FetchError, match="disallowed by robots.txt"):
        list(SlovLexStaticConnector(manifest, SOURCE, blocked, offline=True).fetch())
    # A page that is not the version it should be is not ingested.
    _put(cache, "static/SK/ZZ/2018/18/20260818.html", b"<html>login</html>")
    with pytest.raises(FetchError, match="not the time version starting 2026-08-18"):
        list(SlovLexStaticConnector(manifest, SOURCE, cache, offline=True).fetch())
    # A missing history page is a recorded error, not a stop.
    _put(cache, "static/SK/ZZ/2018/18/history.html", b"", status=404)
    conn = SlovLexStaticConnector(manifest, SOURCE, cache, offline=True)
    assert list(conn.fetch()) == [] and "history page not found" in conn.fetch_errors[0]


def test_committed_manifest_loads_and_source_uses_this_connector(catalog: Catalog, tmp_path: Path) -> None:
    path = REPO / "config/manifests/sk-slovlex-static.yaml"
    SlovLexStaticConnector(path, SOURCE, tmp_path, offline=True)._manifest()  # noqa: SLF001
    manifest = yaml.safe_load(path.read_text())
    acts = [law["act"] for law in manifest["laws"]]
    assert acts[0] == "1992/460" and "2001/311" in acts and "1964/40" in acts and len(acts) == len(set(acts)) >= 30
    assert isinstance(build_connector(catalog.sources.get(SOURCE)), SlovLexStaticConnector)
    source = catalog.sources.get(SOURCE)
    assert source.authority == "official_non_authentic" and source.licence.review_status == "approved"
    # Suspended after the licence review: pages are noindex, nofollow and no terms could be read.
    assert source.status == "suspended"
    with pytest.raises(LicenceNotApproved):
        licence_gate(source)


# --------------------------------------------------------------------------- PostgreSQL


@pytest.fixture(scope="module", autouse=True)
def _fixtures_bypass_the_suspension() -> Iterator[None]:
    """The source is suspended (planned), so the real gate refuses it; the pipeline tests below exercise the parser
    and connector on fixtures with the pipeline's gate lifted, while the test above proves the real gate refuses it."""
    patch = pytest.MonkeyPatch()
    patch.setattr("ulis.ingestion.pipeline.licence_gate", lambda source: None)
    yield
    patch.undo()


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for name in INGESTED:
            yield RawDocument(
                f"slovlex:{name}",
                f"https://static.slov-lex.sk/static/{name}.html",
                "text/html",
                _raw(name),
                datetime(2026, 10, 14, 10, 55, tzinfo=UTC),
                {},
            )


@pytest.fixture(scope="module")
def sk_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="SK", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_search_finds_exact_text_with_citation_and_limitations(
    session: Session, catalog: Catalog, sk_ingested: IngestionReport
) -> None:
    assert sk_ingested.errors == [] and sk_ingested.conflicts == []
    assert sk_ingested.instruments_created == 3 and sk_ingested.instrument_versions_created == len(INGESTED)
    res = _search(
        session, catalog, q="Obrana Slovenskej republiky je povinnosťou a vecou cti občanov", as_of=date(2026, 10, 14)
    )
    hit = res["results"][0]
    assert hit["citation"] == "čl. 25 ods. 1 Ústavy Slovenskej republiky"
    assert hit["text"].startswith("Obrana Slovenskej republiky je povinnosťou a vecou cti občanov.")
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {
        "PROFILE_UNVERIFIED",
        "OFFICIAL_CONSOLIDATION_REPRODUCED",
        "SOURCE_NOT_AUTHENTIC",
        "SNAPSHOT_DATES",
    } <= codes
    assert hit["source"]["attribution"].startswith("Zdroj: Slov-lex")
    ou = _search(
        session, catalog, q="Prevádzkovateľ a sprostredkovateľ je povinný zachovávať mlčanlivosť o osobných údajoch"
    )
    assert ou["results"][0]["citation"] == "§ 79 ods. 1 zákona č. 18/2018 Z. z."


@pytest.mark.db
def test_point_in_time_follows_the_time_versions(
    session: Session, catalog: Catalog, sk_ingested: IngestionReport
) -> None:
    c25 = f"{USTAVA}/~ustavnyclanok-25.odsek-1"
    old = provision_at(session, catalog, c25, date(1995, 1, 1))
    assert (
        old["status_on_date"] == "in_force"
        and old["version"]["text"] == "Obrana Slovenskej republiky je vecou cti každého občana."
    )
    assert (old["version"]["valid_from"], old["version"]["valid_to"]) == ("1992-10-01", "1998-08-05")
    mid = provision_at(session, catalog, c25, date(2001, 8, 1))
    assert mid["version"]["text"].startswith("Obrana Slovenskej republiky je povinnosťou a vecou cti")
    assert (mid["version"]["valid_from"], mid["version"]["valid_to"]) == ("2001-07-01", "2002-01-01")
    now = provision_at(session, catalog, c25, date(2026, 10, 14))
    assert now["version"]["valid_from"] == "2025-11-01" and now["version"]["valid_to"] is None
    assert now["provision"]["citation"] == "čl. 25 ods. 1 Ústavy Slovenskej republiky"
    # Versions between 1998 and 2001 were not ingested, so those dates have no version.
    assert provision_at(session, catalog, c25, date(1999, 6, 1))["version"] is None
    # 18/2018: the versions of 2018-05-25 (until 2019-08-31) and 2026-08-18 are ingested; the years between are not.
    p1 = f"{OU}/~paragraf-1"
    first = provision_at(session, catalog, p1, date(2018, 6, 1))["version"]
    assert (first["valid_from"], first["valid_to"]) == ("2018-05-25", "2019-09-01")
    assert provision_at(session, catalog, p1, date(2020, 1, 1))["version"] is None
    assert provision_at(session, catalog, p1, date(2026, 10, 14))["version"]["valid_from"] == "2026-08-18"


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, sk_ingested: IngestionReport
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        assert again.documents_unchanged == len(INGESTED) and again.provision_versions_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == len(INGESTED) and all(r["result"] == "pass" for r in results)
