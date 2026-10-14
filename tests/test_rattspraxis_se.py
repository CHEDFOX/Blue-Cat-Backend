"""Swedish case law reports from Domstolsverket's open data (Sök rättspraxis): parser, citations, connector, search.

The fixtures in ``tests/fixtures/rattspraxis`` are real publications (form REFERAT) of
``https://rattspraxis.etjanst.domstol.se/api/v1/publiceringar``, one JSON object each, serialised canonically
(gzip-compressed, retrieved 2026-10-14).
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import IngestionReport, Ingestor, build_connector
from ulis.jurisdictions.modules import JudgmentCitationInput, get_module
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.connectors.rattspraxis_se import (
    ROBOTS_URL,
    RattspraxisConnector,
    page_url,
    publication_bytes,
)
from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers.rattspraxis_se import COURTS, RattspraxisParseError, parse_rattspraxis
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

SOURCE = "se-rattspraxis"
HOST = "rattspraxis.etjanst.domstol.se"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
FIX = REPO_ROOT / "tests" / "fixtures" / "rattspraxis"
HD, HD91, HFD, ADO, MIOD, REGR, HSV = (
    "hd_nja_2026_347",
    "hd_nja_1991_56",
    "hfd_2026_ref_35",
    "ado_2025_62",
    "miod_2026_9",
    "regr_ra_2010_ref_122",
    "hsv_rh_2026_10",
)
ALL = [HD91, REGR, HFD, MIOD, ADO, HSV, HD]


def _raw(name: str) -> bytes:
    return gzip.decompress((FIX / f"{name}.json.gz").read_bytes())


def _item(name: str) -> dict[str, Any]:
    return json.loads(_raw(name))


def _doc(name: str) -> ParsedDocument:
    docs = parse_rattspraxis(_raw(name))
    assert len(docs) == 1
    return docs[0]


def _para(doc: ParsedDocument, eid: str) -> Any:
    return {p.eid: p for p in doc.paragraphs}[eid]


# --------------------------------------------------------------------------- parser


def test_metadata_of_a_supreme_court_report() -> None:
    doc = _doc(HD)
    meta = doc.judgment
    assert meta is not None
    assert doc.work_uri == "/se/rattspraxis/82cd6efc-2d29-4616-8473-f798bd14b82c"
    assert doc.expression_uri == f"{doc.work_uri}/swe@2026-10-02"
    assert (meta.court_id, meta.neutral_citation, meta.docket) == ("SE-HD", "NJA 2026 s. 347", "FT 1040-25")
    assert meta.decision_date == date(2026, 6, 12) and doc.version_date == date(2026, 10, 2)
    assert meta.case_name == "”Bryggan på Sippsö”" and doc.title == meta.case_name
    assert (doc.language, doc.authoritative, doc.jurisdiction_id) == ("sv", False, "SE")
    assert doc.ext["references"] == ["NJA 2026:30", "NJA 2026 s. 347"]  # the page form is the citation
    assert doc.ext["type"] == "PREJUDIKAT" and doc.ext["court_name"] == "Högsta domstolen"
    assert doc.ext["keywords"][:2] == ["Skadestånd", "Europakonventionen"]
    assert {"sfs": "1972:207", "reference": "3 kap. 4 § skadeståndslagen"} in doc.ext["statutes"]
    assert any(c.startswith("”Toftavägen” NJA 2021 s. 820") for c in doc.ext["cited_cases"])


def test_courts_map_to_profile_courts_and_keep_the_actual_court() -> None:
    hfd, regr, hsv, ado, miod = (_doc(n) for n in (HFD, REGR, HSV, ADO, MIOD))
    assert [d.judgment.court_id for d in (hfd, regr, hsv, ado, miod) if d.judgment] == [
        "SE-HFD",
        "SE-HFD",  # Regeringsrätten, the Supreme Administrative Court until 2010
        "SE-HOVR",
        "SE-AD",
        "SE-MIOD",
    ]
    assert regr.ext["court_name"] == "Regeringsrätten" and hsv.ext["court_name"] == "Svea hovrätt"
    assert [d.judgment.neutral_citation for d in (hfd, regr, hsv, ado, miod) if d.judgment] == [
        "HFD 2026 ref. 35",
        "RÅ 2010 ref. 122",
        "RH 2026:10",
        "AD 2025 nr 62",
        "MIG 2026:9",
    ]
    assert ado.ext["labour_court_number"] == "62/25"
    # Every code of the API that has reports maps; every mapped court is in the profile.
    ids = {c.id for c in Catalog.load(REPO_ROOT / "config").jurisdictions.get("SE").courts}
    assert set(COURTS.values()) <= ids


def test_numbered_reasons_carry_num_and_the_lower_court_sections_do_not() -> None:
    doc = _doc(HD)
    numbered = [p for p in doc.paragraphs if p.num]
    assert [p.num for p in numbered] == [str(i) for i in range(1, 25)]
    assert all(p.part == "considerations" for p in numbered)
    first = numbered[0]
    assert first.text.startswith("S.S. ansökte om strandskyddsdispens")  # the number is not part of the text
    assert not first.text.startswith("1.")
    assert _para(doc, "hn-1").part == "headnote" and _para(doc, "hn-1").text.startswith("En tid om cirka ett år")
    lower = [p for p in doc.paragraphs if p.part == "text"]
    assert lower and all(p.num is None for p in lower)
    assert _para(doc, "h-1").text == "Stockholms tingsrätt" and _para(doc, "h-1").part == "heading"
    assert [p.sort_order for p in doc.paragraphs] == list(range(len(doc.paragraphs)))
    assert len({p.eid for p in doc.paragraphs}) == len(doc.paragraphs)


def test_reports_without_numbered_reasons_have_no_numbers() -> None:
    for name in (HD91, REGR, MIOD, HSV, ADO):  # NJA 1991 has a list of four locations: not a run of reasons
        assert all(p.num is None and p.part != "considerations" for p in _doc(name).paragraphs), name
    hfd = _doc(HFD)
    assert [p.num for p in hfd.paragraphs if p.num] == [str(i) for i in range(1, 24)]


def test_text_is_collapsed_and_free_of_soft_hyphens() -> None:
    for name in ALL:
        for p in _doc(name).paragraphs:
            assert "­" not in p.text and "\xa0" not in p.text and "  " not in p.text, (name, p.eid)
    assert any("25 000 kr" in p.text for p in _doc(HD).paragraphs)  # non-breaking spaces became spaces


def test_statute_references_come_from_the_publishers_sfs_numbers() -> None:
    doc = _doc(HD)
    first = doc.paragraphs[0]
    assert [(r.href, r.text, r.relation, r.extraction_method) for r in first.refs] == [
        ("/se/sfs/1972/207", "3 kap. 4 § skadeståndslagen", "references", "source_markup")
    ]  # 'artikel 6.1 i Europakonventionen' has no SFS number and is not linked
    assert all(not p.refs for p in doc.paragraphs[1:])
    assert [r.href for r in _doc(REGR).paragraphs[0].refs] == [
        "/se/sfs/1991/586",
        "/se/sfs/1997/483",
        "/se/sfs/2000/980",
    ]


def test_parser_refuses_what_it_cannot_read_safely() -> None:
    good = _item(HFD)

    def refuse(item: Any, message: str) -> None:
        with pytest.raises(RattspraxisParseError, match=message):
            parse_rattspraxis(json.dumps(item).encode())

    refuse({**good, "id": "not-an-id"}, "unexpected publication id")
    refuse({**good, "publiceringsform": "DOM_ELLER_BESLUT"}, "not supported")
    refuse({**good, "domstol": {"domstolKod": "XYZ"}}, "not mapped")
    refuse({**good, "avgorandedatum": "i går"}, "avgorandedatum")
    refuse({**good, "publiceringstid": None}, "publiceringstid")
    refuse([good], "not a publication object")
    with pytest.raises(RattspraxisParseError, match="not JSON"):
        parse_rattspraxis(b"<html>")
    with pytest.raises(ParseSkipped, match="no text"):
        parse_rattspraxis(json.dumps({**good, "innehall": "", "sammanfattning": ""}).encode())
    # A report with a summary but no body keeps the summary.
    only = parse_rattspraxis(json.dumps({**good, "innehall": ""}).encode())[0]
    assert [p.part for p in only.paragraphs] == ["headnote"]


# --------------------------------------------------------------------------- citations, profile, source


def _citation(doc: ParsedDocument, paragraph: str | None, court_name: str) -> str:
    assert doc.judgment is not None
    return get_module("se").judgment_citation(
        JudgmentCitationInput(
            doc.judgment.case_name,
            doc.judgment.neutral_citation,
            doc.judgment.decision_date,
            doc.judgment.docket,
            doc.judgment.court_id,
            court_name,
            paragraph,
            "sv",
            "SE",
            doc.ext,
        )
    )


def test_report_citations() -> None:
    assert _citation(_doc(HD), "7", "Högsta domstolen (Supreme Court)") == "NJA 2026 s. 347 p. 7"
    assert _citation(_doc(HD), None, "Högsta domstolen (Supreme Court)") == "NJA 2026 s. 347"
    assert _citation(_doc(HFD), "3", "Högsta förvaltningsdomstolen") == "HFD 2026 ref. 35 p. 3"
    assert _citation(_doc(MIOD), None, "Migrationsöverdomstolen") == "MIG 2026:9"
    # Without a report number: the actual court, date and case number.
    bare = _doc(HSV)
    assert bare.judgment is not None
    object.__setattr__(bare.judgment, "neutral_citation", None)
    assert _citation(bare, None, "Hovrätterna (courts of appeal)") == "Svea hovrätt 2026-04-17, mål nr Ö 1309-26"
    se = get_module("se")
    assert se.validate_ext(_doc(HD).ext)["court_code"] == "HDO"
    with pytest.raises(ValidationError):
        se.validate_ext({"court_code": "HDO", "publication_id": "x", "unknown": 1})
    assert se.validate_ext({"sfs_number": "1962:700", "year": 1962, "number": 700, "citation_name": "brottsbalken"})


def test_profile_has_the_special_courts_and_the_source_is_approved(catalog: Catalog) -> None:
    reg = catalog.jurisdictions
    assert reg.get("SE").profile_version == "0.2.0" and "se-rattspraxis" in reg.get("SE").publication.official_sources
    for court_id in ("SE-HD", "SE-HFD", "SE-HOVR", "SE-KR", "SE-AD", "SE-MIOD", "SE-MMOD", "SE-PMOD", "SE-MD"):
        assert reg.court_jurisdiction(court_id) == "SE"
    source = catalog.sources.get(SOURCE)
    licence_gate(source)
    assert source.licence.id == "CC0-1.0" and source.licence.commercial_use == "allowed"
    assert "Domstolsverkets öppna data" in (source.licence.attribution_text or "")
    assert source.languages == ["sv"] and source.document_kinds == ["judgment"]
    assert isinstance(build_connector(source), RattspraxisConnector)


def test_committed_manifest_is_valid_and_covers_every_court_with_reports(tmp_path: Path) -> None:
    source = Catalog.load(REPO_ROOT / "config").sources.get(SOURCE)
    path = REPO_ROOT / source.access.endpoint
    manifest = RattspraxisConnector(path, SOURCE, tmp_path)._manifest()
    # The 20 court codes of the API that had case reports on 2026-10-14 (PBR, RHN and DOV had none).
    assert set(manifest["courts"]) == set(COURTS) and len(manifest["courts"]) == 20
    assert manifest["max_items_per_court"] is None and manifest["ascending"] is True
    assert manifest["page_size"] == 50 and manifest["crawl_delay_seconds"] >= 5 and "(" in manifest["user_agent"]


# --------------------------------------------------------------------------- connector


def _write_page(cache: Path, court: str, page: int, items: list[dict[str, Any]], size: int, asc: bool = True) -> None:
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    url = page_url(court, page, size, ascending=asc)
    key = f"referat/{court}/{'asc' if asc else 'desc'}-{size}-{page:05d}.json"
    path = http.cache_path(url, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(items, ensure_ascii=False).encode())
    meta = {"url": url, "final_url": url, "status": 200, "content_type": "application/json"}
    path.with_name(path.name + ".meta.json").write_text(
        json.dumps({**meta, "retrieved_at": "2026-10-14T20:00:00+00:00"})
    )


def _write_robots(cache: Path, robots: str | None) -> None:
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    path = http.cache_path(ROBOTS_URL, "robots.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((robots or "").encode())
    meta = {"url": ROBOTS_URL, "status": 404 if robots is None else 200}
    path.with_name(path.name + ".meta.json").write_text(json.dumps(meta))


def _manifest(tmp_path: Path, **extra: Any) -> Path:
    body = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": f"https://{HOST}",
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "page_size": 2,
        "forms": ["REFERAT"],
        "ascending": True,
        "max_items_per_court": None,
        "courts": ["HDO", "HFD"],
        **extra,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


def _connector(tmp_path: Path, cache: Path, **extra: Any) -> RattspraxisConnector:
    return RattspraxisConnector(_manifest(tmp_path, **extra), SOURCE, cache, offline=True)


def test_connector_pages_until_a_short_page_and_yields_each_report_canonically(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _write_robots(cache, None)
    hd, hd91, hfd = _item(HD), _item(HD91), _item(HFD)
    _write_page(cache, "HDO", 0, [hd91, hd], 2)
    _write_page(cache, "HDO", 1, [], 2)  # a full first page, then an empty one
    _write_page(cache, "HFD", 0, [hfd], 2)  # short: the only page
    docs = list(_connector(tmp_path, cache).fetch())
    assert [d.external_id for d in docs] == [f"rattspraxis:{i['id']}" for i in (hd91, hd, hfd)]
    assert docs[0].content == publication_bytes(hd91) == _raw(HD91)  # canonical JSON, byte for byte
    assert (
        docs[0].uri == f"https://{HOST}/api/v1/publiceringar/{hd91['id']}" and docs[0].media_type == "application/json"
    )
    assert docs[0].metadata["court"] == "HDO" and docs[0].metadata["page"] == 0 and docs[0].retrieved_at.year == 2026
    connector = _connector(tmp_path, cache)
    list(connector.fetch())
    assert connector.fetch_errors == []
    # Slice: at most one report per court, stopping without asking for the next page.
    assert [d.external_id for d in _connector(tmp_path, cache, max_items_per_court=1).fetch()] == [
        f"rattspraxis:{hd91['id']}",
        f"rattspraxis:{hfd['id']}",
    ]
    with pytest.raises(FetchError, match="offline and not cached"):
        list(_connector(tmp_path, cache, courts=["ADO"]).fetch())


def test_connector_skips_and_reports_wrong_and_repeated_entries_and_refuses_bad_pages(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _write_robots(cache, None)
    hd, hfd = _item(HD), _item(HFD)
    _write_page(cache, "HDO", 0, [hd, hfd], 2)  # a HFD report on the HDO list
    _write_page(cache, "HDO", 1, [hd], 2)  # a repeat of the first page's report
    _write_page(cache, "HFD", 0, [], 2)
    connector = _connector(tmp_path, cache)
    assert [d.external_id for d in connector.fetch()] == [f"rattspraxis:{hd['id']}"]
    assert len(connector.fetch_errors) == 2
    assert "is not a HDO report" in connector.fetch_errors[0] and "seen twice" in connector.fetch_errors[1]
    for content, message in ((b"<html>", "not JSON"), (b'{"fel": 1}', "expected a JSON list")):
        bad = tmp_path / "bad"
        _write_robots(bad, None)
        _write_page(bad, "HDO", 0, [], 2)
        http = PoliteHttp(bad, UA, allowed_hosts={HOST})
        http.cache_path(page_url("HDO", 0, 2, ascending=True), "referat/HDO/asc-2-00000.json").write_bytes(content)
        with pytest.raises(FetchError, match=message):
            list(_connector(tmp_path, bad, courts=["HDO"]).fetch())


def test_connector_refuses_bad_manifests_and_obeys_robots_txt(tmp_path: Path) -> None:
    for extra, message in (
        ({"base_url": "http://rattspraxis.etjanst.domstol.se"}, "base_url"),
        ({"user_agent": "python-urllib"}, "user_agent"),
        ({"crawl_delay_seconds": 1}, "at least 5"),
        ({"page_size": 0}, "page_size"),
        ({"page_size": 500}, "page_size"),
        ({"forms": ["DOM_ELLER_BESLUT"]}, "forms must be"),
        ({"courts": []}, "courts"),
        ({"courts": ["HDO", "HDO"]}, "courts"),
        ({"courts": ["XYZ"]}, "not one the parser maps"),
        ({"max_items_per_court": 0}, "max_items_per_court"),
        ({"ascending": "yes"}, "ascending"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(_connector(tmp_path, tmp_path, **extra).fetch())
    with pytest.raises(ManifestError, match="source_id"):
        list(_connector(tmp_path, tmp_path, source_id="other").fetch())
    blocked = tmp_path / "blocked"
    _write_robots(blocked, "User-agent: *\nDisallow: /api/\n")
    _write_page(blocked, "HDO", 0, [], 2)
    with pytest.raises(FetchError, match="disallowed by"):
        list(_connector(tmp_path, blocked, courts=["HDO"]).fetch())
    allowed = tmp_path / "allowed"
    _write_robots(allowed, "User-agent: *\nDisallow: /sok/\n")
    _write_page(allowed, "HDO", 0, [], 2)
    assert list(_connector(tmp_path, allowed, courts=["HDO"]).fetch()) == []


# --------------------------------------------------------------------------- PostgreSQL


class _Reports:
    def fetch(self) -> Iterator[RawDocument]:
        for name in ALL:
            item = _item(name)
            yield RawDocument(
                f"rattspraxis:{item['id']}",
                f"https://{HOST}/api/v1/publiceringar/{item['id']}",
                "application/json",
                _raw(name),
                datetime(2026, 10, 14, 20, 0, tzinfo=UTC),
                {},
            )


@pytest.fixture(scope="module")
def reports(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Reports())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="SE", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_ingest_report(reports: IngestionReport) -> None:
    assert reports.errors == [] and reports.conflicts == []
    assert reports.documents_new == 7 and reports.judgments_created == 7
    expected = sum(len(_doc(n).paragraphs) for n in ALL)
    assert reports.paragraphs_created == reports.passages_indexed == expected
    links = sum(len(p.refs) for n in ALL for p in _doc(n).paragraphs)
    assert reports.instruments_created == 0 and reports.citation_links_created == links


@pytest.mark.db
def test_search_returns_the_numbered_paragraph_with_the_report_citation(
    session: Session, catalog: Catalog, reports: IngestionReport
) -> None:
    res = _search(
        session, catalog, q="huvudfrågan inaktivitet länsstyrelsens handläggning strandskyddsdispens skälig tid"
    )
    hit = res["results"][0]
    assert hit["kind"] == "judgment_paragraph"
    assert hit["citation"] == "NJA 2026 s. 347 p. 6"
    assert hit["text"].startswith("Huvudfrågan i målet är om en sammanlagd tid på cirka ett år och elva månader")
    assert hit["unit"]["eid"] == "s-6" and hit["unit"]["heading"] == "considerations"
    assert (hit["document"]["court_id"], hit["document"]["decision_date"]) == ("SE-HD", "2026-06-12")
    assert hit["authority"]["category"] == "persuasive_precedent"
    assert (
        "jurisprudence constante" in hit["authority"]["rationale"] or "jurisprudence" in hit["authority"]["rationale"]
    )
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"AUTHORITY", "SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED"} <= codes
    assert hit["source"]["attribution"].startswith("Källa: Domstolsverkets öppna data")
    assert hit["version"]["authoritative"] is False and hit["version"]["language"] == "sv"

    hfd = _search(session, catalog, q="ränteutgifter avdragsgilla näringsverksamhet bestämmelser begränsar")["results"][
        0
    ]
    assert hfd["citation"] == "HFD 2026 ref. 35 p. 1" and hfd["document"]["court_id"] == "SE-HFD"


@pytest.mark.db
def test_judgments_links_and_reingest(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, reports: IngestionReport
) -> None:
    with session_factory() as session:
        rows = {
            j.neutral_citation: j for j in session.scalars(select(m.Judgment).where(m.Judgment.jurisdiction_id == "SE"))
        }
        assert {"NJA 2026 s. 347", "HFD 2026 ref. 35", "RÅ 2010 ref. 122", "AD 2025 nr 62"} <= set(rows)
        hd = rows["NJA 2026 s. 347"]
        assert (hd.court_id, hd.docket, hd.decision_date) == ("SE-HD", "FT 1040-25", date(2026, 6, 12))
        assert hd.ext["publication_id"] == "82cd6efc-2d29-4616-8473-f798bd14b82c"
        links = session.scalars(select(m.CitationLink).where(m.CitationLink.from_work_uri == hd.uri)).all()
        assert [(lk.to_uri, lk.extraction_method, lk.relation) for lk in links] == [
            ("/se/sfs/1972/207", "source_markup", "references")
        ]
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Reports())
        assert again.documents_unchanged == 7 and again.judgments_created == 0 and again.paragraphs_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == 7 and all(r["result"] == "pass" for r in results)
        session.rollback()
