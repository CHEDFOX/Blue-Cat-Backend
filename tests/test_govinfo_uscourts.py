"""US courts of appeals opinions from GovInfo (USCOURTS): parser, connector, citations, ingestion.

Fixtures (tests/fixtures/govinfo_uscourts/, see its README): two real packages retrieved from www.govinfo.gov on
2026-10-14: Kolenc v. HHS (Federal Circuit, 2024, a PDF with a text layer) and DeRosa v. Lawrence (First Circuit,
2001, a scanned PDF without text layer), and the First Circuit's 2001 sitemap. The robots.txt of the host is the
one kept in tests/fixtures/govinfo_usreports/.
"""

from __future__ import annotations

import base64
import json
import shutil
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSER_VERSIONS, PARSERS, IngestionReport, Ingestor
from ulis.jurisdictions.modules import get_module
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.govinfo import HOST, ROBOTS_URL
from ulis.sources.connectors.govinfo_uscourts import UsCourtsGovInfoConnector, mods_url, pdf_url, sitemap_url
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers.govinfo_uscourts import (
    BUNDLE_SCHEMA,
    COURTS,
    UsCourtsParseError,
    bundle_bytes,
    parse_govinfo_uscourts,
)
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore

SOURCE = "us-govinfo-uscourts"
FIX = REPO_ROOT / "tests" / "fixtures" / "govinfo_uscourts"
UA = yaml.safe_load((REPO_ROOT / "config/manifests/us-uscode-house.yaml").read_text())["user_agent"]
MANIFEST = REPO_ROOT / "config" / "manifests" / f"{SOURCE}.yaml"
KOLENC, DEROSA = "USCOURTS-ca13-23-01808", "USCOURTS-ca1-00-01824"
needs_pdftotext = pytest.mark.skipif(
    shutil.which("pdftotext") is None, reason="pdftotext (poppler-utils) is not installed"
)


def _mods(package: str) -> str:
    return (FIX / f"{package}.mods.xml").read_text(encoding="utf-8")


def _pdf(package: str) -> bytes:
    return (FIX / f"{package}-0.pdf").read_bytes()


def _bundle(package: str, **override: Any) -> bytes:
    body = {
        "schema": BUNDLE_SCHEMA,
        "package": package,
        "granule": f"{package}-0",
        "mods": _mods(package),
        "pdf_base64": base64.b64encode(_pdf(package)).decode("ascii"),
    }
    return bundle_bytes({**body, **override})


def _doc() -> ParsedDocument:
    (doc,) = parse_govinfo_uscourts(_bundle(KOLENC))
    return doc


# --------------------------------------------------------------------------- parser


@needs_pdftotext
def test_metadata_comes_from_the_mods() -> None:
    doc = _doc()
    meta = doc.judgment
    assert meta is not None
    assert (meta.court_id, meta.case_name, meta.neutral_citation) == ("US-CAFC", "Kolenc v. HHS", None)
    assert meta.decision_date == date(2024, 1, 3) and meta.docket == "23-1808"
    assert doc.work_uri == "/us/case/govinfo/USCOURTS-ca13-23-01808-0"
    assert doc.expression_uri == "/us/case/govinfo/USCOURTS-ca13-23-01808-0/eng@2024-01-04"
    assert doc.doc_kind == "judgment" and doc.language == "en" and doc.number == "23-1808"
    assert doc.authoritative is False and doc.text_origin == "official_conversion"
    ext = doc.ext
    assert ext["govinfo_package"] == KOLENC and ext["court_code"] == "ca13" and ext["case_number"] == "23-01808"
    assert ext["pdf_pages"] == 7 and ext["docket_text"].startswith("OPINION filed for the court by Lourie")
    assert "Nonprecedential Per Curiam Opinion" in ext["docket_text"]  # stored as given, not interpreted
    assert {"name": "Department of Health and Human Services", "role": "Respondent"} in ext["parties"]
    assert not any("overrul" in str(v).lower() for v in ext.values())


@needs_pdftotext
def test_units_are_pdf_pages_with_the_text_layer() -> None:
    doc = _doc()
    assert [p.num for p in doc.paragraphs] == ["1", "2", "3", "4", "5", "6", "7"]
    assert [p.eid for p in doc.paragraphs][:2] == ["p1", "p2"] and {p.part for p in doc.paragraphs} == {"opinion"}
    first, second = doc.paragraphs[0].text, doc.paragraphs[1].text
    assert "NOTE: This disposition is nonprecedential." in first  # the court's own marking is part of the text
    assert "United States Court of Appeals\nfor the Federal Circuit" in first
    assert "ANDREW M. KOLENC," in first and "Decided: January 3, 2024" in first
    assert "PER CURIAM." in second and "Andrew Kolenc appeals from a final decision of the" in second
    assert all(p.text == p.text.strip() and "\n\n\n" not in p.text and p.refs == [] for p in doc.paragraphs)


@needs_pdftotext
def test_a_scanned_pdf_without_text_layer_is_skipped_not_guessed() -> None:
    with pytest.raises(ParseSkipped, match="no text layer"):
        parse_govinfo_uscourts(_bundle(DEROSA))


@needs_pdftotext
def test_parser_refuses_what_it_cannot_read_safely() -> None:
    with pytest.raises(UsCourtsParseError, match="not a ULIS bundle"):
        parse_govinfo_uscourts(b"%PDF-1.4")
    with pytest.raises(UsCourtsParseError, match="schema"):
        parse_govinfo_uscourts(_bundle(KOLENC, schema="other/1"))
    with pytest.raises(UsCourtsParseError, match="not that of package"):
        parse_govinfo_uscourts(_bundle(KOLENC, mods=_mods(DEROSA)))
    with pytest.raises(UsCourtsParseError, match="lists no granule"):
        parse_govinfo_uscourts(_bundle(KOLENC, granule=f"{KOLENC}-7"))
    with pytest.raises(UsCourtsParseError, match="cannot be read safely"):
        parse_govinfo_uscourts(_bundle(KOLENC, mods="<!DOCTYPE x [<!ENTITY a 'b'>]><x/>"))
    with pytest.raises(UsCourtsParseError, match="not a PDF"):
        parse_govinfo_uscourts(_bundle(KOLENC, pdf_base64=base64.b64encode(b"<html>error</html>").decode()))
    with pytest.raises(UsCourtsParseError, match="no valid PDF"):
        parse_govinfo_uscourts(_bundle(KOLENC, pdf_base64="***"))
    other_court = _mods(KOLENC).replace("<courtCode>ca13</courtCode>", "<courtCode>cand</courtCode>")
    with pytest.raises(ParseSkipped, match="not a court of appeals"):
        parse_govinfo_uscourts(_bundle(KOLENC, mods=other_court))


def test_every_court_of_appeals_code_maps_to_a_court_of_the_profile(catalog: Catalog) -> None:
    ids = {c.id for c in catalog.jurisdictions.get("US").courts}
    assert set(COURTS.values()) <= ids and len(COURTS) == 13 and COURTS["ca13"] == "US-CAFC"


# --------------------------------------------------------------------------- citations


def test_slip_opinion_citation_has_docket_court_and_date_but_no_pinpoint(catalog: Catalog) -> None:
    us = get_module("us")
    from ulis.jurisdictions.modules import JudgmentCitationInput

    def cite(court: str, paragraph: str | None) -> str:
        return us.judgment_citation(
            JudgmentCitationInput(
                case_name="Kolenc v. HHS",
                neutral_citation=None,
                decision_date=date(2024, 1, 3),
                docket="23-1808",
                court_id=court,
                court_name="x",
                paragraph=paragraph,
                language="en",
                jurisdiction_id="US",
            )
        )

    assert cite("US-CAFC", None) == cite("US-CAFC", "2") == "Kolenc v. HHS, No. 23-1808 (Fed. Cir. Jan. 3, 2024)"
    assert cite("US-CADC", None) == "Kolenc v. HHS, No. 23-1808 (D.C. Cir. Jan. 3, 2024)"
    assert cite("US-CA2", None) == "Kolenc v. HHS, No. 23-1808 (2d Cir. Jan. 3, 2024)"
    assert cite("US-CA9", None).endswith("(9th Cir. Jan. 3, 2024)")


# --------------------------------------------------------------------------- source and connector


def test_source_is_approved_registered_and_documented(catalog: Catalog) -> None:
    source = catalog.sources.get(SOURCE)
    licence_gate(source)
    assert source.status == "active" and source.document_kinds == ["judgment"] and source.jurisdictions == ["US"]
    assert source.licence.id == "US-PD-17USC105" and source.licence.review_status == "approved"
    assert source.connector in CONNECTORS and source.parser in PARSERS and source.parser in PARSER_VERSIONS
    assert any("api.data.gov" in c for c in source.licence.conditions) and any(
        "pdftotext" in c for c in source.licence.conditions
    )


def test_recorded_robots_txt_allows_the_paths_the_connector_uses() -> None:
    robots = (REPO_ROOT / "tests/fixtures/govinfo_usreports/robots.txt").read_text(encoding="utf-8")
    for url in (sitemap_url("ca9", 2024), mods_url(KOLENC), pdf_url(KOLENC, f"{KOLENC}-0")):
        assert robots_allows(robots, UA, url), url
    assert "Sitemap: https://www.govinfo.gov/sitemap/USCOURTS_sitemap_index.xml" in robots


def test_committed_manifest_lists_every_court_of_appeals_year_of_the_collection(tmp_path: Path) -> None:
    manifest = UsCourtsGovInfoConnector(MANIFEST, SOURCE, tmp_path)._manifest()
    entries = manifest["sitemaps"]
    assert len(entries) == 313 and {e["court"] for e in entries} == set(COURTS)
    assert entries[0]["year"] == 2026 and [e["year"] for e in entries] == sorted(
        (e["year"] for e in entries), reverse=True
    )
    assert {"court": "ca13", "year": 2024} in entries and {"court": "caDC", "year": 1993} in entries
    assert manifest["crawl_delay_seconds"] >= 5 and "limit_per_sitemap" not in manifest


def _cache(cache: Path, packages: list[str], robots: str | None = None, sitemap: bool = True) -> None:
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    meta = {"status": 200, "retrieved_at": "2026-10-14T18:00:00+00:00"}

    def put(url: str, key: str, body: bytes, content_type: str) -> None:
        path = http.cache_path(url, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        path.with_name(path.name + ".meta.json").write_text(
            json.dumps({**meta, "url": url, "final_url": url, "content_type": content_type})
        )

    robots_text = (
        robots if robots is not None else (REPO_ROOT / "tests/fixtures/govinfo_usreports/robots.txt").read_text()
    )
    put(ROBOTS_URL, "robots.txt", robots_text.encode(), "text/plain")
    if sitemap:
        put(
            sitemap_url("ca1", 2001),
            "sitemap/USCOURTS_ca1_2001_sitemap.xml",
            (FIX / "USCOURTS_ca1_2001_sitemap.xml").read_bytes(),
            "text/xml",
        )
    for package in packages:
        put(mods_url(package), f"metadata/{package}.mods.xml", _mods(package).encode(), "application/xml")
        put(pdf_url(package, f"{package}-0"), f"content/{package}/{package}-0.pdf", _pdf(package), "application/pdf")


def _manifest(tmp_path: Path, sitemaps: list[dict[str, Any]], **extra: Any) -> Path:
    body = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": "https://www.govinfo.gov",
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "sitemaps": sitemaps,
        **extra,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


def test_connector_offline_yields_one_exact_bundle_per_opinion(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache(cache, [DEROSA])
    manifest = _manifest(tmp_path, [{"court": "ca1", "year": 2001}], limit_per_sitemap=1)
    connector = UsCourtsGovInfoConnector(manifest, SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [f"govinfo:{DEROSA}-0"] and connector.fetch_errors == []
    assert docs[0].uri == pdf_url(DEROSA, f"{DEROSA}-0") and docs[0].media_type == "application/json"
    bundle = json.loads(docs[0].content)
    assert bundle["schema"] == BUNDLE_SCHEMA and bundle["package"] == DEROSA and bundle["granule"] == f"{DEROSA}-0"
    assert bundle["mods"] == _mods(DEROSA) and base64.b64decode(bundle["pdf_base64"]) == _pdf(DEROSA)  # exact bytes
    assert docs[0].retrieved_at.year == 2026
    # the second package of the sitemap is not cached: offline, that is an error and never a silent gap
    with pytest.raises(FetchError, match="offline and not cached"):
        list(
            UsCourtsGovInfoConnector(
                _manifest(tmp_path, [{"court": "ca1", "year": 2001}]), SOURCE, cache, offline=True
            ).fetch()
        )


def test_connector_reports_bad_packages_and_stops_on_robots(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache(cache, [DEROSA])
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    # the MODS of another package in place of the requested one, and a PDF that is an HTML error page
    wrong = http.cache_path(mods_url(DEROSA), f"metadata/{DEROSA}.mods.xml")
    wrong.write_text(_mods(KOLENC))
    connector = UsCourtsGovInfoConnector(
        _manifest(tmp_path, [{"court": "ca1", "year": 2001}], limit_per_sitemap=1), SOURCE, cache, offline=True
    )
    assert list(connector.fetch()) == []
    assert len(connector.fetch_errors) == 1 and f"not the package MODS of {DEROSA}" in connector.fetch_errors[0]
    wrong.write_text(_mods(DEROSA))
    page = http.cache_path(pdf_url(DEROSA, f"{DEROSA}-0"), f"content/{DEROSA}/{DEROSA}-0.pdf")
    page.write_bytes(b"<html>Error</html>")
    connector = UsCourtsGovInfoConnector(
        _manifest(tmp_path, [{"court": "ca1", "year": 2001}], limit_per_sitemap=1), SOURCE, cache, offline=True
    )
    assert list(connector.fetch()) == [] and "not a PDF file" in connector.fetch_errors[0]
    blocked = tmp_path / "blocked"
    _cache(blocked, [DEROSA], robots="User-agent: *\nDisallow: /sitemap/\n")
    with pytest.raises(FetchError, match="disallowed by"):
        list(
            UsCourtsGovInfoConnector(
                _manifest(tmp_path, [{"court": "ca1", "year": 2001}]), SOURCE, blocked, offline=True
            ).fetch()
        )


def test_connector_refuses_bad_manifests(tmp_path: Path) -> None:
    ok = [{"court": "ca1", "year": 2001}]
    for sitemaps, extra, message in (
        ([{"court": "cand", "year": 2020}], {}, "invalid or repeated court/year"),
        ([{"court": "ca1", "year": "2001"}], {}, "invalid or repeated court/year"),
        ([{"court": "ca1", "year": 1800}], {}, "invalid or repeated court/year"),
        (ok + ok, {}, "invalid or repeated court/year"),
        (ok, {"limit_per_sitemap": 0}, "limit_per_sitemap"),
        (ok, {"base_url": "http://www.govinfo.gov"}, "base_url"),
        (ok, {"user_agent": "python-urllib"}, "user_agent"),
        (ok, {"crawl_delay_seconds": 1}, "at least 5"),
        ([], {}, "no sitemaps"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(UsCourtsGovInfoConnector(_manifest(tmp_path, sitemaps, **extra), SOURCE, tmp_path).fetch())


# --------------------------------------------------------------------------- PostgreSQL


class _Opinions:
    def fetch(self) -> Iterator[RawDocument]:
        for package in (KOLENC, DEROSA):
            yield RawDocument(
                f"govinfo:{package}-0",
                pdf_url(package, f"{package}-0"),
                "application/json",
                _bundle(package),
                datetime(2026, 10, 14, 18, 0, tzinfo=UTC),
                {"package": package},
            )


@pytest.fixture(scope="module")
def appeals(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Opinions())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="US", **kw), today=date(2026, 10, 14))


@needs_pdftotext
@pytest.mark.db
def test_ingest_report_counts_the_scan_as_skipped(appeals: IngestionReport) -> None:
    assert appeals.errors == [] and appeals.conflicts == []
    assert appeals.documents_seen == 2 and appeals.documents_new == 1 and appeals.documents_skipped == 1
    assert "no text layer" in appeals.skipped[0] and appeals.skipped[0].startswith(f"govinfo:{DEROSA}-0")
    assert appeals.judgments_created == 1 and appeals.paragraphs_created == appeals.passages_indexed == 7


@needs_pdftotext
@pytest.mark.db
def test_search_finds_the_opinion_with_citation_and_court_dependent_authority(
    session: Session, catalog: Catalog, appeals: IngestionReport
) -> None:
    q = "Consumer Safety Officer removal Merit Systems Protection Board government-owned vehicle"
    hit = _search(session, catalog, q=q)["results"][0]
    assert (
        hit["kind"] == "judgment_paragraph" and hit["citation"] == "Kolenc v. HHS, No. 23-1808 (Fed. Cir. Jan. 3, 2024)"
    )
    assert hit["unit"]["label"] == "PDF p. 2" and hit["unit"]["heading"] == "opinion"
    assert hit["document"]["court_id"] == "US-CAFC" and hit["document"]["neutral_citation"] is None
    assert hit["document"]["decision_date"] == "2024-01-03" and hit["version"]["authoritative"] is False
    # no forum court: the courts that appeal to the Federal Circuit (Federal Claims, International Trade, Veterans
    # Claims, the boards) are modelled since the CourtListener source, so it binds courts below it; a forum court is
    # needed for a definite answer
    assert hit["authority"]["category"] == "precedent_vertical"
    same = _search(session, catalog, q=q, forum_court="US-CAFC")["results"][0]
    other = _search(session, catalog, q=q, forum_court="US-CA9")["results"][0]
    assert same["authority"]["category"] == "binding_precedent"  # a court of appeals binds itself
    assert other["authority"]["category"] == "persuasive_precedent"  # another circuit's opinion is only persuasive
