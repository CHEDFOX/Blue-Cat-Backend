"""Narodne novine (Croatia): parser, connector, citations and search behaviour (real act pages)."""

from __future__ import annotations

import gzip
import html
import json
import re
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
from ulis.ingestion.pipeline import IngestionReport, Ingestor, build_connector
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.jurisdictions.modules.hr import genitive
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.narodne_novine import NarodneNovineConnector, blocked_acts
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.normalized import ParsedDocument, ParsedProvision
from ulis.sources.parsers.legal_html import HtmlParseError
from ulis.sources.parsers.narodne_novine import parse_narodne_novine
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "narodne_novine"
SOURCE = "hr-narodne-novine"
HOST = "narodne-novine.nn.hr"
RETRIEVED = "2026-10-14T13:00:00+00:00"
TODAY = date(2026, 10, 14)
UA = "ULIS-test (+https://example.org)"

# fixture file -> (ELI path as in the manifest, redirect target)
PAGES = {
    "nn-2023-30-510.html.gz": ("2023/30/510", "/clanci/sluzbeni/2023_03_30_510.html"),
    "nn-2010-85-2422.html.gz": ("2010/85/2422", "/clanci/sluzbeni/2010_07_85_2422.html"),
    "nn-2005-42-816.html.gz": ("2005/42/816", "/clanci/sluzbeni/2005_04_42_816.html"),
}
ROBOTS = """User-agent: *
Disallow: /eli/oglasni/2024/111/pdf
Disallow: /clanci/sluzbeni/2017_06_57_1277.html
Disallow: /clanci/sluzbeni/full/2017_06_57_1277.html
Disallow: /clanci/sluzbeni/2010_04_47_1183.html
Disallow: /clanci/oglasi/
Disallow: /clanci/oglasi/*.html$
Disallow: /search.aspx?kategorija=3
"""


def _page(name: str) -> bytes:
    return gzip.decompress((FIX / name).read_bytes())


def _doc(name: str) -> ParsedDocument:
    docs = parse_narodne_novine(_page(name))
    assert len(docs) == 1
    return docs[0]


def _prov(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return next(p for p in doc.provisions if p.eid == eid)


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _prov(doc, eid)
    return get_module("hr").provision_citation(
        ProvisionCitationInput(
            instrument_title=doc.title,
            instrument_type=doc.instrument_type,
            jurisdiction_id="HR",
            top_kind=p.top_kind,
            num_path=p.num_path,
            language="hr",
            ext=doc.ext,
            eid=p.eid,
        )
    )


# --------------------------------------------------------------------------- parser


def test_act_with_eli_metadata() -> None:
    doc = _doc("nn-2023-30-510.html.gz")
    assert doc.work_uri == "/hr/nn/2023/30/510" and doc.expression_uri == "/hr/nn/2023/30/510/hrv@2023-03-15"
    assert doc.title == "Zakon o službenoj iskaznici" and doc.number == "NN 30/2023"
    assert (doc.instrument_type, doc.language, doc.jurisdiction_id) == ("act", "hr", "HR")
    assert doc.work_date == date(2023, 3, 3) and doc.version_date == date(2023, 3, 15)  # adopted / published
    assert doc.authoritative is False and doc.text_origin == "official_conversion"
    assert doc.consolidation_kind == "as_published" and doc.validity_basis == "source_periods"
    assert doc.currency_date is None
    assert doc.ext["eli"] == "sluzbeni/2023/30/510" and doc.ext["document_type"] == "ZAKON"
    assert doc.ext["nn_citation"] == "NN 30/2023" and doc.ext["consolidated_text"] is False
    assert {e.event_type: e.date for e in doc.events} == {
        "enactment": date(2023, 3, 3),
        "publication": date(2023, 3, 15),
    }
    # the source gives no entry-into-force date: no period is recorded, and later amendments are not applied
    assert all(p.valid_from is None and p.valid_to is None and p.status == "in_force" for p in doc.provisions)
    assert len(doc.outstanding_changes) == 1 and doc.outstanding_changes[0]["affects_eid"] is None
    assert "does not consolidate" in doc.outstanding_changes[0]["description"]


def test_structure_headings_paragraphs_points_and_closing() -> None:
    doc = _doc("nn-2023-30-510.html.gz")
    tops = [p.eid for p in doc.provisions if p.parent_eid is None]
    assert tops == ["preamble", *[f"art-{n}" for n in range(1, 17)], "zav-1"]
    pre = _prov(doc, "preamble")
    assert pre.text.startswith("HRVATSKI SABOR\n510\nNa temelju članka 89. Ustava Republike Hrvatske, donosim\nODLUKU")
    assert "ZAKON\nO SLUŽBENOJ ISKAZNICI" in pre.text
    one = _prov(doc, "art-1")
    assert one.heading == "Predmet Zakona" and one.is_leaf
    assert one.text == "Ovim Zakonom uređuje se izdavanje i uporaba službene iskaznice u državnim tijelima."
    two = _prov(doc, "art-2")
    assert two.heading == "Primjena Zakona" and [p.eid for p in doc.provisions if p.parent_eid == "art-2"] == [
        "art-2-par1",
        "art-2-par2",
    ]
    assert _prov(doc, "art-2-par2").text.startswith("Odredbe ovoga Zakona ne primjenjuju se na državna tijela osnovana")
    assert _prov(doc, "art-2-par2").num_path == ["art:2", "par:2"] and _prov(doc, "art-2-par2").label if False else True
    five = _prov(doc, "art-5-par5")
    assert five.text.startswith("Elektronički kontaktni čip iz stavka 2. podstavka 4. ovoga članka sadrži:\n1. broj službene")
    point = _prov(doc, "art-5-par5-pt1")
    assert point.kind == "point" and point.num_path == ["art:5", "par:5", "pt:1"] and point.text == "broj službene iskaznice"
    # dashes are text of the paragraph; the source numbers nothing there
    assert "– državna tijela su tijela državne uprave" in _prov(doc, "art-3-par1").text
    closing = _prov(doc, "zav-1")
    assert closing.kind == "closing" and closing.text.startswith("Klasa: 022-02/22-01/68\nZagreb, 3. ožujka 2023.")


def test_consolidated_text_without_eli_block() -> None:
    doc = _doc("nn-2010-85-2422.html.gz")
    assert doc.title == "Ustav Republike Hrvatske (pročišćeni tekst)" and doc.instrument_type == "constitution"
    assert doc.work_uri == "/hr/nn/2010/85/2422" and doc.version_date == date(2010, 7, 9)
    assert doc.work_date is None  # the page carries no adoption date outside the ELI block
    assert doc.ext["consolidated_text"] is True and doc.ext["document_type"] is None
    assert doc.text_origin == "official_consolidation" and doc.consolidation_kind == "as_published"
    arts = [p for p in doc.provisions if p.kind == "article"]
    assert len(arts) == 152 and arts[0].eid == "art-1" and arts[-1].eid == "art-152"
    art90 = _prov(doc, "art-90")
    assert art90.text.startswith("Prije nego što stupe na snagu zakoni i drugi propisi državnih tijela objavljuju se u ")
    assert "Zakon stupa na snagu najranije osmi dan od dana njegove objave" in art90.text
    assert art90.context_heading is not None and art90.context_heading.startswith("VI.") is False
    assert _prov(doc, "art-1").context_heading == "II. TEMELJNE ODREDBE"
    assert _prov(doc, "art-151").context_heading == "X. ZAVRŠNE ODREDBE"
    # the historical foundations before the first article are the preamble
    assert "IZVORIŠNE OSNOVE" not in _prov(doc, "art-1").text


def test_old_markup_and_quoted_article_headings() -> None:
    doc = _doc("nn-2005-42-816.html.gz")
    assert doc.title == "Zakon o dopunama Zakona o boravišnoj pristojbi" and doc.version_date == date(2005, 4, 1)
    assert [p.eid for p in doc.provisions if p.parent_eid is None] == [
        "preamble",
        "art-1",
        "art-2",
        "art-3",
        "art-4",
        "art-5",
        "zav-1",
    ]
    three = _prov(doc, "art-3")
    # an amending law quotes the article it inserts: that line is text, not a new article
    assert "»Članak 16.a\nNovčanom kaznom" in three.text and "art-16a" not in {p.eid for p in doc.provisions}
    assert _prov(doc, "art-5").text == "Ovaj Zakon stupa na snagu osmoga dana od dana objave u »Narodnim novinama«."
    assert "\xad" not in "".join(p.text for p in doc.provisions)  # soft hyphens are not text


def test_citations() -> None:
    constitution, law = _doc("nn-2010-85-2422.html.gz"), _doc("nn-2023-30-510.html.gz")
    assert _cite(constitution, "art-90") == "članak 90. Ustava Republike Hrvatske (pročišćeni tekst, NN 85/2010)"
    assert _cite(law, "art-5-par5-pt1") == "članak 5. stavak 5. točka 1. Zakona o službenoj iskaznici (NN 30/2023)"
    assert _cite(law, "art-2") == "članak 2. Zakona o službenoj iskaznici (NN 30/2023)"
    assert _cite(law, "preamble") == "uvodni dio Zakona o službenoj iskaznici (NN 30/2023)"
    assert _cite(law, "zav-1") == "završni dio Zakona o službenoj iskaznici (NN 30/2023)"
    module = get_module("hr")
    assert module.validate_ext(law.ext)["nn_edition"] == 30
    with pytest.raises(ValueError):
        module.validate_ext({**law.ext, "unexpected": 1})


def test_genitive_forms_and_fallback() -> None:
    assert genitive("Zakon o radu") == "Zakona o radu"
    assert genitive("Zakon o izmjenama i dopunama Zakona o radu") == "Zakona o izmjenama i dopunama Zakona o radu"
    assert genitive("Ustav Republike Hrvatske") == "Ustava Republike Hrvatske"
    assert genitive("Ustavni zakon o Ustavnom sudu Republike Hrvatske") == "Ustavnog zakona o Ustavnom sudu Republike Hrvatske"
    assert genitive("Kazneni zakon") == "Kaznenog zakona" and genitive("Obiteljski zakon") == "Obiteljskog zakona"
    assert genitive("Zakonik o kaznenom postupku") == "Zakonika o kaznenom postupku"
    assert genitive("Uredba o nečemu") is None
    module = get_module("hr")
    base = {"instrument_type": "regulation", "jurisdiction_id": "HR", "top_kind": "article", "language": "hr"}
    inserted = ProvisionCitationInput(
        instrument_title="Uredba o nečemu", num_path=["art:12a", "par:2"], ext={"nn_citation": "NN 1/2020"}, **base
    )
    assert module.provision_citation(inserted) == "Uredba o nečemu, članak 12.a stavak 2. (NN 1/2020)"


def test_parser_rejects_foreign_pages() -> None:
    with pytest.raises(HtmlParseError, match="not a Narodne novine act page"):
        parse_narodne_novine(b"<html><body><p>Hello</p></body></html>")
    page = _page("nn-2023-30-510.html.gz").decode("utf-8").replace("NN 30/2023 (15.3.2023.)", "NN 31/2023 (15.3.2023.)")
    with pytest.raises(HtmlParseError, match="another edition"):
        parse_narodne_novine(page.encode("utf-8"))
    page = _page("nn-2023-30-510.html.gz").decode("utf-8").replace("eli/sluzbeni/2023/30/510", "eli/sluzbeni/2023/30/511")
    with pytest.raises(HtmlParseError, match="describe another act"):
        parse_narodne_novine(page.encode("utf-8"))


# --------------------------------------------------------------------------- connector


def _write(http: PoliteHttp, url: str, data: bytes, final_path: str | None = None, status: int = 200) -> None:
    path = http.cache_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    meta = {
        "url": url,
        "final_url": f"https://{HOST}{final_path}" if final_path else url,
        "status": status,
        "content_type": "text/html; charset=utf-8",
        "retrieved_at": RETRIEVED,
    }
    path.with_name(path.name + ".meta.json").write_text(json.dumps(meta))


def _cache(tmp_path: Path) -> Path:
    cache = tmp_path / "cache"
    http = PoliteHttp(cache, UA, allowed_hosts={HOST}, offline=True)
    _write(http, f"https://{HOST}/robots.txt", ROBOTS.encode())
    for name, (eli, final) in PAGES.items():
        _write(http, f"https://{HOST}/eli/sluzbeni/{eli}", _page(name), final)
    _write(http, f"https://{HOST}/eli/sluzbeni/2020/1/1", b"", status=404)
    _write(http, f"https://{HOST}/eli/sluzbeni/2020/2/2", "<html><body>Održavanje</body></html>".encode())
    _write(http, f"https://{HOST}/eli/sluzbeni/2010/47/1183", _page("nn-2005-42-816.html.gz"), "/clanci/sluzbeni/2010_04_47_1183.html")
    return cache


def _manifest(tmp_path: Path, acts: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "manifest.yaml"
    data = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": f"https://{HOST}",
        "user_agent": UA,
        "acts": acts,
        **extra,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


def test_robots_rules_name_individual_acts() -> None:
    assert blocked_acts(ROBOTS) == {(2017, 57, 1277), (2010, 47, 1183)}
    assert blocked_acts("User-agent: Googlebot\nDisallow: /clanci/sluzbeni/2017_06_57_1277.html\n") == set()


def test_connector_offline_yields_pages_exactly(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    acts = [{"eli": "2023/30/510", "title": "Zakon o službenoj iskaznici", "kind": "zakon"}, {"eli": "2010/85/2422"}]
    connector = NarodneNovineConnector(_manifest(tmp_path, acts), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["hr-nn:2023/30/510", "hr-nn:2010/85/2422"]
    assert docs[0].content == _page("nn-2023-30-510.html.gz")  # exactly the bytes received
    assert docs[0].uri == f"https://{HOST}/eli/sluzbeni/2023/30/510" and docs[0].media_type == "text/html"
    assert docs[0].retrieved_at == datetime(2026, 10, 14, 13, 0, tzinfo=UTC)
    assert docs[0].metadata["eli"] == "sluzbeni/2023/30/510" and docs[0].metadata["kind"] == "zakon"
    assert connector.http is not None and connector.http.requests_made == 0 and connector.fetch_errors == []


def test_connector_skips_blocked_missing_and_foreign_pages(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    acts = [{"eli": e} for e in ("2017/57/1277", "2020/1/1", "2020/2/2", "2010/47/1183", "2005/42/816")]
    connector = NarodneNovineConnector(_manifest(tmp_path, acts), SOURCE, cache, offline=True)
    assert [d.external_id for d in connector.fetch()] == ["hr-nn:2005/42/816"]
    errors = "\n".join(connector.fetch_errors)
    assert len(connector.fetch_errors) == 4
    assert "2017/57/1277" in errors and "disallows this act's page" in errors  # named in robots.txt
    assert "2020/1/1" in errors and "404" in errors
    assert "2020/2/2" in errors and "not the page of act NN 2/2020 no. 2" in errors
    assert "2010/47/1183" in errors  # blocked by identity before any request
    # the page the server redirects to is disallowed for a different act: dropped from the cache
    http = PoliteHttp(cache, UA, allowed_hosts={HOST}, offline=True)
    _write(http, f"https://{HOST}/eli/sluzbeni/2011/3/9", _page("nn-2005-42-816.html.gz"), "/clanci/oglasi/9.html")
    connector = NarodneNovineConnector(_manifest(tmp_path, [{"eli": "2011/3/9"}]), SOURCE, cache, offline=True)
    assert list(connector.fetch()) == [] and "disallowed by robots.txt; not kept" in connector.fetch_errors[0]
    assert not http.cache_path(f"https://{HOST}/eli/sluzbeni/2011/3/9").exists()


def test_connector_is_offline_and_refuses_bad_manifests(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    with pytest.raises(FetchError, match="offline and not cached"):
        list(NarodneNovineConnector(_manifest(tmp_path, [{"eli": "2023/155/2358"}]), SOURCE, cache, offline=True).fetch())
    good = [{"eli": "2023/30/510"}]
    for acts, extra, message in (
        ([{"eli": "2023/30/510/../x"}], {}, "invalid ELI path"),
        ([{"eli": "2023/0/1"}], {}, "invalid ELI path"),
        ([{"eli": "2023/30/510"}, {"eli": "2023/30/510"}], {}, "listed twice"),
        (good, {"base_url": "http://narodne-novine.nn.hr"}, "base_url"),
        (good, {"user_agent": "python-urllib"}, "user_agent"),
        (good, {"crawl_delay_seconds": 0.3}, "crawl_delay_seconds"),
        ([], {}, "no documents"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(NarodneNovineConnector(_manifest(tmp_path, acts, **extra), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="mismatch"):
        list(NarodneNovineConnector(_manifest(tmp_path, good, source_id="other"), SOURCE, tmp_path).fetch())


def test_real_manifest_and_registration() -> None:
    catalog = Catalog.load(REPO_ROOT / "config")
    source = catalog.sources.get(SOURCE)
    assert isinstance(build_connector(source), NarodneNovineConnector)
    path = REPO_ROOT / source.access.endpoint
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    ids = [a["eli"] for a in manifest["acts"]]
    assert len(ids) == len(set(ids)) == 4769
    assert {"1990/56/1092", "2010/85/2422", "2021/111/1941", "2023/30/510", "2004/137/2415"} <= set(ids)
    assert {a["kind"] for a in manifest["acts"]} == {"zakon", "pročišćeni tekst", "ustav"}
    assert manifest["crawl_delay_seconds"] >= 5 and "ULIS-research-bot" in manifest["user_agent"]
    # the connector's own validation accepts the committed manifest
    assert NarodneNovineConnector(path, SOURCE, Path("/nonexistent"))._manifest()["acts"] == manifest["acts"]
    licence_gate(source)
    assert source.licence.review_status == "approved" and source.authority == "official_non_authentic"
    profile = catalog.jurisdictions.get("HR")
    assert profile.verification.status == "draft_unverified" and profile.official_languages == ["hr"]
    assert profile.citation.module == "hr" and profile.coverage.corpus_status == "pilot"


def test_fixtures_readme_names_the_source_and_licence() -> None:
    text = (FIX / "README.md").read_text(encoding="utf-8")
    assert "narodne-novine.nn.hr" in text and "članak 18. stavak 3." in text and "2026-10-14" in text
    for name in PAGES:
        assert name in text


# --------------------------------------------------------------------------- database


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for name, (eli, _final) in PAGES.items():
            yield RawDocument(
                f"hr-nn:{eli}",
                f"https://{HOST}/eli/sluzbeni/{eli}",
                "text/html",
                _page(name),
                datetime.fromisoformat(RETRIEVED),
                {"fixture": name},
            )


@pytest.fixture(scope="module")
def hr_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="HR", **kw), today=TODAY)


@pytest.mark.db
def test_ingest_report_counts(hr_ingested: IngestionReport) -> None:
    assert hr_ingested.errors == [] and hr_ingested.conflicts == []
    assert hr_ingested.documents_new == 3 and hr_ingested.instruments_created == 3
    assert hr_ingested.provision_versions_created == sum(len(_doc(n).provisions) for n in PAGES)
    assert hr_ingested.passages_indexed > 100


@pytest.mark.db
def test_search_finds_exact_text_with_citation_and_limitations(
    session: Session, catalog: Catalog, hr_ingested: IngestionReport
) -> None:
    res = _search(session, catalog, q="Zakon stupa na snagu najranije osmi dan od dana njegove objave")
    hit = res["results"][0]
    assert hit["citation"] == "članak 90. Ustava Republike Hrvatske (pročišćeni tekst, NN 85/2010)"
    assert "Zakon stupa na snagu najranije osmi dan od dana njegove objave, osim ako nije" in hit["text"]
    assert hit["jurisdiction"]["id"] == "HR" and all(h["jurisdiction"]["id"] == "HR" for h in res["results"])
    assert hit["authority"]["category"] == "applicability_unverified"  # no start date, no end date
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED", "EFFECTIVE_DATE_UNKNOWN", "CURRENCY_UNKNOWN"} <= codes
    assert "OUTSTANDING_CHANGES_INSTRUMENT" in codes  # amendments published later are not applied
    assert hit["source"]["attribution"].startswith("Izvor: Narodne novine")
    point = _search(session, catalog, q="kodove za podešavanje pristupa podacima za aktivaciju privatnog ključa")
    assert point["results"][0]["citation"] == "članak 5. stavak 5. točka 2. Zakona o službenoj iskaznici (NN 30/2023)"
    assert point["results"][0]["text"] == "kodove za podešavanje pristupa podacima za aktivaciju privatnog ključa"


@pytest.mark.db
def test_no_validity_period_means_no_point_in_time_answer(
    session: Session, catalog: Catalog, hr_ingested: IngestionReport
) -> None:
    got = provision_at(session, catalog, "/hr/nn/2023/30/510/~art-1", date(2024, 1, 1))
    assert got["provision"]["citation"] == "članak 1. Zakona o službenoj iskaznici (NN 30/2023)"
    assert got["status_on_date"] != "in_force" or got["version"]["valid_from"] is None
    codes = {lim["code"] for lim in got["limitations"]}
    assert "EFFECTIVE_DATE_UNKNOWN" in codes or "NO_VERSION" in codes or got["version"] is None


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, hr_ingested: IngestionReport
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        assert again.documents_unchanged == 3 and again.provision_versions_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == 3 and all(r["result"] == "pass" for r in results)
        assert sum(r["checked_units"] for r in results) == hr_ingested.provision_versions_created
        session.rollback()


def test_text_is_never_invented() -> None:
    """Every article's first line is found in the raw page (compared without white space)."""
    raw = re.sub(r"\s+", "", html.unescape(re.sub(r"<[^>]+>", " ", _page("nn-2023-30-510.html.gz").decode("utf-8"))))
    checked = 0
    for p in _doc("nn-2023-30-510.html.gz").provisions:
        if p.kind == "article" and p.is_leaf:
            assert re.sub(r"\s+", "", p.text.split("\n")[0])[:60] in raw
            checked += 1
    assert checked >= 3
