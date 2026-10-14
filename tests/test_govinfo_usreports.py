"""United States Reports from GovInfo (Supreme Court of the United States): parser, connector, citations, ingestion.

Fixtures (tests/fixtures/govinfo_usreports/, see its README): real package MODS (other cases' entries removed) and a
contiguous slice of the real text rendition of volumes 410 (Roe v. Wade), 250 (a page shared by two cases) and 586
(modern layout, "fi" ligatures lost by the OCR), retrieved from www.govinfo.gov on 2026-10-14.
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
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSER_VERSIONS, PARSERS, IngestionReport, Ingestor
from ulis.jurisdictions.modules import JudgmentCitationInput, get_module
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.govinfo import HOST, ROBOTS_URL
from ulis.sources.connectors.govinfo_usreports import UsReportsGovInfoConnector, mods_url, text_url
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers.govinfo_usreports import (
    BUNDLE_SCHEMA,
    UsReportsParseError,
    bundle_bytes,
    parse_govinfo_usreports,
    read_mods,
)
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore

SOURCE = "us-govinfo-usreports"
FIX = REPO_ROOT / "tests" / "fixtures" / "govinfo_usreports"
UA = yaml.safe_load((REPO_ROOT / "config/manifests/us-uscode-house.yaml").read_text())["user_agent"]
MANIFEST = REPO_ROOT / "config" / "manifests" / f"{SOURCE}.yaml"


def _text(name: str) -> str:
    return gzip.decompress((FIX / f"{name}.txt.gz").read_bytes()).decode("utf-8")


def _mods(name: str) -> str:
    return (FIX / f"{name}.mods.xml").read_text(encoding="utf-8")


def _bundle(name: str, volume: int, **override: Any) -> bytes:
    body = {"schema": BUNDLE_SCHEMA, "volume": volume, "mods": _mods(name), "text": _text(name)}
    return bundle_bytes({**body, **override})


def _docs(name: str, volume: int) -> dict[int, ParsedDocument]:
    return {d.ext["first_page"]: d for d in parse_govinfo_usreports(_bundle(name, volume))}


ROE = _docs("vol410-roe", 410)
SHARED = _docs("vol250-shared", 250)
STITT = _docs("vol586-stitt", 586)


# --------------------------------------------------------------------------- parser


def test_roe_v_wade_metadata_comes_from_the_package_mods() -> None:
    assert list(ROE) == [113]  # Doe v. Bolton (179) is listed, but where it ends is not in the slice: not guessed
    roe = ROE[113]
    meta = roe.judgment
    assert meta is not None
    assert (meta.case_name, meta.neutral_citation, meta.court_id) == ("Roe v. Wade", "410 U.S. 113", "US-SC")
    assert meta.decision_date == date(1973, 1, 22) and meta.docket is None
    assert roe.work_uri == "/us/case/us/410/113" and roe.expression_uri == "/us/case/us/410/113/eng@2025-07-08"
    assert roe.doc_kind == "judgment" and roe.jurisdiction_id == "US" and roe.language == "en"
    assert roe.authoritative is False and roe.text_origin == "official_conversion"
    assert roe.title == "Roe v. Wade, 410 U.S. 113 (1973)"
    assert roe.ext == {
        "reporter": "U.S.",
        "volume": 410,
        "first_page": 113,
        "govinfo_package": "USREPORTS-410",
        "govinfo_granule": "USREPORTS-410-113",
        "volume_cases_listed": 2,
        "volume_cases_located": 1,
        "court_term": 1972,
    }


def test_units_are_the_printed_pages_with_the_running_head_label_as_part() -> None:
    units = ROE[113].paragraphs
    assert [p.num for p in units] == [str(n) for n in range(113, 179)]  # every page, no gap, none merged
    assert [p.eid for p in units[:2]] == ["p113", "p114"] and [p.sort_order for p in units] == list(range(1, 67))
    by_page = {p.num: p for p in units}
    assert by_page["113"].part == "Syllabus" and by_page["116"].part == "Opinion of the Court"
    assert by_page["178"].part == "Rehnquist, J., dissenting"
    assert by_page["113"].text.startswith(
        "ROE et al. v. WADE, DISTRICT ATTORNEY OF DALLAS COUNTY\nAPPEAL FROM THE UNITED"
    )
    assert (
        "No. 70-18. Argued December 13, 1971—Reargued October 11, 1972—Decided January 22, 1973" in by_page["113"].text
    )
    assert by_page["178"].text.endswith("For all of the foregoing reasons, I respectfully dissent.")
    # the famous passage is on page 153, as the United States Reports print it (410 U.S. at 153)
    assert "This right of privacy, whether it be founded in the Fourteenth Amendment" in by_page["153"].text.replace(
        "\n", " "
    )
    assert "pregnancy" in by_page["153"].text and by_page["153"].part == "Opinion of the Court"


def test_text_is_source_text_without_running_heads() -> None:
    source = _text("vol410-roe").replace("\r\n", "\n")
    for p in ROE[113].paragraphs:
        assert p.text in source  # one contiguous run of the OCR lines, unchanged (including OCR errors and tabs)
        assert "OCTOBER TERM, 1972" not in p.text and "410 U. S.\n" not in p.text
        assert p.text == p.text.strip() and p.refs == []


def test_a_page_shared_by_two_cases_belongs_to_both_and_the_label_is_not_guessed() -> None:
    assert list(SHARED) == [28, 30]  # the last listed case (33) has no known end
    schlitz, coleman = SHARED[28], SHARED[30]
    assert schlitz.judgment is not None and coleman.judgment is not None
    assert schlitz.judgment.case_name == "Schlitz Brewing Co. v. Houston Ice Co."
    assert [p.num for p in schlitz.paragraphs] == ["28", "29", "30"]
    assert schlitz.paragraphs[-1].part == "text"  # page 30's running head says "Syllabus", which is Coleman's
    assert schlitz.paragraphs[-1].text.endswith("Mr. Justice McKenna and Mr. Justice Pitney dissent.")
    assert "Decree affirmed." in schlitz.paragraphs[-1].text
    assert coleman.paragraphs[0].num == "30" and coleman.paragraphs[0].part == "Syllabus"
    assert coleman.paragraphs[0].text.startswith(
        "COLEMAN, SURVIVING ADMINISTRATRIX OF COLEMAN, v. UNITED STATES.\nAPPEAL FROM"
    )
    assert "Decree affirmed." not in coleman.paragraphs[0].text


def test_ocr_errors_of_the_source_are_kept() -> None:
    stitt = STITT[27]
    text = "\n".join(p.text for p in stitt.paragraphs)
    assert "specifc" in text and "defned" in text  # the lost "fi" ligature is the source's, not ours
    assert stitt.judgment is not None and stitt.judgment.neutral_citation == "586 U.S. 27"
    assert stitt.judgment.decision_date == date(2018, 1, 1)
    assert "year-only" in stitt.ext["decision_date_caveat"]
    assert [p.part for p in stitt.paragraphs][:3] == ["Syllabus", "Syllabus", "Opinion of the Court"]


def test_mods_reader_lists_cases_with_first_page_and_citation() -> None:
    volume, changed, cases = read_mods(_mods("vol410-roe"))
    assert (volume, changed) == (410, date(2025, 7, 8))
    assert [(c.page, c.citation, c.decided) for c in cases] == [
        (113, "410 U.S. 113", date(1973, 1, 22)),
        (179, "410 U.S. 179", date(1973, 1, 22)),
    ]
    assert cases[1].title == "Doe v. Bolton, 410 U.S. 179 (1972)"  # the source's own year, not corrected


def test_parser_refuses_or_skips_what_it_cannot_read_safely() -> None:
    with pytest.raises(UsReportsParseError, match="not a ULIS bundle"):
        parse_govinfo_usreports(b"<html>")
    with pytest.raises(UsReportsParseError, match="schema"):
        parse_govinfo_usreports(_bundle("vol410-roe", 410, schema="other/1"))
    with pytest.raises(UsReportsParseError, match="differs from the MODS volume"):
        parse_govinfo_usreports(_bundle("vol410-roe", 411))
    with pytest.raises(UsReportsParseError, match="unexpected root"):
        parse_govinfo_usreports(_bundle("vol410-roe", 410, mods="<other/>"))
    with pytest.raises(UsReportsParseError):
        parse_govinfo_usreports(_bundle("vol410-roe", 410, mods="<!DOCTYPE x [<!ENTITY a 'b'>]><x/>"))
    # a layout whose page headers are not recognised yields nothing, and says so
    with pytest.raises(ParseSkipped, match="no case could be cut out"):
        parse_govinfo_usreports(_bundle("vol410-roe", 410, text="Some text\nwithout page headers\n"))


# --------------------------------------------------------------------------- citations


def test_citations_follow_us_practice(catalog: Catalog) -> None:
    us = get_module("us")

    def cite(**kw: Any) -> str:
        base: dict[str, Any] = {
            "case_name": "Roe v. Wade",
            "neutral_citation": "410 U.S. 113",
            "decision_date": date(1973, 1, 22),
            "docket": None,
            "court_id": "US-SC",
            "court_name": "Supreme Court of the United States",
            "paragraph": None,
            "language": "en",
            "jurisdiction_id": "US",
        }
        return us.judgment_citation(JudgmentCitationInput(**{**base, **kw}))

    assert cite() == "Roe v. Wade, 410 U.S. 113 (1973)"
    assert cite(paragraph="153") == "Roe v. Wade, 410 U.S. 113, 153 (1973)"
    assert cite(paragraph="153-154") == "Roe v. Wade, 410 U.S. 113, 153-154 (1973)"
    assert cite(neutral_citation=None) == "Roe v. Wade, 1973-01-22"
    assert (
        cite(
            case_name="Kolenc v. HHS",
            neutral_citation=None,
            docket="23-1808",
            court_id="US-CAFC",
            decision_date=date(2024, 1, 3),
            paragraph="2",
        )
        == "Kolenc v. HHS, No. 23-1808 (Fed. Cir. Jan. 3, 2024)"
    )
    assert cite(neutral_citation="20 F.4th 100", court_id="US-CA9") == "Roe v. Wade, 20 F.4th 100 (9th Cir. 1973)"
    assert us.judgment_unit_label("153", "Opinion of the Court", ROE[113].ext) == "p. 153"
    assert us.judgment_unit_label("3", "text", {"case_number": "23-01808"}) == "PDF p. 3"
    assert us.judgment_unit_label("3", "text", None) == "para 3"
    assert get_module("nl").judgment_unit_label("3", "considerations", None) == "para 3"  # other countries unchanged


def test_judgment_ext_is_validated(catalog: Catalog) -> None:
    us = get_module("us")
    assert us.validate_ext(ROE[113].ext)["govinfo_package"] == "USREPORTS-410"
    with pytest.raises(ValidationError):
        us.validate_ext({**ROE[113].ext, "overruled_by": "/us/case/us/597/215"})  # treatment is never recorded
    assert us.validate_ext({"usc_title": 18})["usc_title"] == 18  # legislation ext is unchanged


# --------------------------------------------------------------------------- source and connector


def test_source_is_approved_registered_and_documented(catalog: Catalog) -> None:
    source = catalog.sources.get(SOURCE)
    licence_gate(source)
    assert source.status == "active" and source.kind == "official_court_database"
    assert source.authority == "official_non_authentic" and source.document_kinds == ["judgment"]
    assert source.licence.id == "US-PD-17USC105" and source.licence.review_status == "approved"
    assert source.licence.url == "https://www.govinfo.gov/about/policies"
    assert source.connector in CONNECTORS and source.parser in PARSERS and source.parser in PARSER_VERSIONS
    assert "api.data.gov" in (source.licence.conditions[0] + (source.licence.reviewed_by or ""))


def test_recorded_robots_txt_allows_the_paths_the_connector_uses() -> None:
    robots = (FIX / "robots.txt").read_text(encoding="utf-8")
    for url in (mods_url(410), text_url(410), "https://www.govinfo.gov/sitemap/USREPORTS_sitemap_index.xml"):
        assert robots_allows(robots, UA, url), url
    assert not robots_allows(robots, UA, "https://www.govinfo.gov/app/search/q")  # disallowed, never used
    assert "Sitemap: https://www.govinfo.gov/sitemap/USREPORTS_sitemap_index.xml" in robots


def test_committed_manifest_lists_the_whole_collection(tmp_path: Path) -> None:
    connector = UsReportsGovInfoConnector(MANIFEST, SOURCE, tmp_path)
    manifest = connector._manifest()
    volumes = [v["volume"] for v in manifest["volumes"]]
    assert len(volumes) == 584 and volumes[0] == 2 and volumes[-1] == 586 and volumes == sorted(volumes)
    assert 1 not in volumes and 465 not in volumes and 410 in volumes  # GovInfo lists no package for these two
    assert manifest["crawl_delay_seconds"] >= 5 and manifest["base_url"] == "https://www.govinfo.gov"


def _cache(cache: Path, volumes: dict[int, tuple[str, str]], robots: str | None = None) -> None:
    """Pre-fill the PoliteHttp cache the way a real run would, so the connector runs offline."""
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    meta = {"status": 200, "retrieved_at": "2026-10-14T18:00:00+00:00"}

    def put(url: str, key: str, body: bytes, content_type: str) -> None:
        path = http.cache_path(url, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        path.with_name(path.name + ".meta.json").write_text(
            json.dumps({**meta, "url": url, "final_url": url, "content_type": content_type})
        )

    put(
        ROBOTS_URL,
        "robots.txt",
        (robots if robots is not None else (FIX / "robots.txt").read_text()).encode(),
        "text/plain",
    )
    for volume, (mods, text) in volumes.items():
        put(mods_url(volume), f"metadata/USREPORTS-{volume}.mods.xml", mods.encode(), "application/xml")
        put(text_url(volume), f"content/USREPORTS-{volume}.txt", text.encode(), "text/plain")


def _manifest(tmp_path: Path, volumes: list[int], **extra: Any) -> Path:
    body = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": "https://www.govinfo.gov",
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "volumes": [{"volume": v} for v in volumes],
        **extra,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


def test_connector_offline_yields_one_exact_bundle_per_volume(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache(
        cache, {410: (_mods("vol410-roe"), _text("vol410-roe")), 586: (_mods("vol586-stitt"), _text("vol586-stitt"))}
    )
    connector = UsReportsGovInfoConnector(_manifest(tmp_path, [410, 586]), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["govinfo:USREPORTS-410", "govinfo:USREPORTS-586"]
    assert docs[0].uri == text_url(410) and docs[0].media_type == "application/json"
    bundle = json.loads(docs[0].content)
    assert bundle["schema"] == BUNDLE_SCHEMA and bundle["volume"] == 410
    assert bundle["mods"] == _mods("vol410-roe") and bundle["text"] == _text("vol410-roe")  # exact bytes as received
    assert docs[0].retrieved_at.year == 2026 and connector.fetch_errors == []
    assert {d.ext["first_page"] for d in parse_govinfo_usreports(docs[0].content)} == {113}


def test_connector_skips_and_reports_bad_volumes_but_a_refusal_stops(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    other = _mods("vol586-stitt")  # the MODS of another volume
    _cache(cache, {410: (_mods("vol410-roe"), _text("vol410-roe")), 411: (other, "x")})
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    http.cache_path(mods_url(412), "metadata/USREPORTS-412.mods.xml").parent.mkdir(parents=True, exist_ok=True)
    missing = http.cache_path(mods_url(412), "metadata/USREPORTS-412.mods.xml")
    missing.write_bytes(b"")
    missing.with_name(missing.name + ".meta.json").write_text(json.dumps({"url": mods_url(412), "status": 404}))
    connector = UsReportsGovInfoConnector(_manifest(tmp_path, [410, 411, 412]), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["govinfo:USREPORTS-410"]
    assert len(connector.fetch_errors) == 2
    assert "not the package MODS of USREPORTS-411" in connector.fetch_errors[0]
    assert "not found (404)" in connector.fetch_errors[1]
    # a volume that is not cached at all is an error, never a silent gap
    with pytest.raises(FetchError, match="offline and not cached"):
        list(UsReportsGovInfoConnector(_manifest(tmp_path, [413]), SOURCE, cache, offline=True).fetch())


def test_connector_obeys_robots_txt(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache(cache, {410: (_mods("vol410-roe"), _text("vol410-roe"))}, robots="User-agent: *\nDisallow: /metadata/\n")
    with pytest.raises(FetchError, match="disallowed by"):
        list(UsReportsGovInfoConnector(_manifest(tmp_path, [410]), SOURCE, cache, offline=True).fetch())


def test_connector_refuses_bad_manifests(tmp_path: Path) -> None:
    for volumes, extra, message in (
        ([410, 410], {}, "invalid or repeated volume"),
        ([0], {}, "invalid or repeated volume"),
        ([410], {"base_url": "http://www.govinfo.gov"}, "base_url"),
        ([410], {"user_agent": "python-urllib"}, "user_agent"),
        ([410], {"crawl_delay_seconds": 1}, "at least 5"),
        ([], {}, "no volumes"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(UsReportsGovInfoConnector(_manifest(tmp_path, volumes, **extra), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="source_id"):
        list(UsReportsGovInfoConnector(_manifest(tmp_path, [410], source_id="other"), SOURCE, tmp_path).fetch())


# --------------------------------------------------------------------------- PostgreSQL


class _Volumes:
    def fetch(self) -> Iterator[RawDocument]:
        for volume, name in ((250, "vol250-shared"), (410, "vol410-roe"), (586, "vol586-stitt")):
            yield RawDocument(
                f"govinfo:USREPORTS-{volume}",
                text_url(volume),
                "application/json",
                _bundle(name, volume),
                datetime(2026, 10, 14, 18, 0, tzinfo=UTC),
                {"volume": volume},
            )


@pytest.fixture(scope="module")
def us_reports(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Volumes())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="US", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_ingest_report(us_reports: IngestionReport) -> None:
    assert us_reports.errors == [] and us_reports.conflicts == [] and us_reports.skipped == []
    assert us_reports.documents_new == 3 and us_reports.judgments_created == 1 + 2 + 1
    expected = sum(len(d.paragraphs) for docs in (ROE, SHARED, STITT) for d in docs.values())
    assert us_reports.paragraphs_created == us_reports.passages_indexed == expected
    assert us_reports.instruments_created == 0 and us_reports.citation_links_created == 0


@pytest.mark.db
def test_search_finds_roe_with_page_pinpoint_and_binding_force_for_the_circuits(
    session: Session, catalog: Catalog, us_reports: IngestionReport
) -> None:
    q = "Fourteenth Amendment concept of personal liberty restrictions upon state action Ninth Amendment reservation"
    results = _search(session, catalog, q=q)["results"]
    hit = next(r for r in results if r["unit"]["eid"] == "p153")
    assert hit["kind"] == "judgment_paragraph" and hit["citation"] == "Roe v. Wade, 410 U.S. 113, 153 (1973)"
    assert hit["unit"]["label"] == "p. 153" and hit["unit"]["heading"] == "Opinion of the Court"
    assert hit["document"]["court_id"] == "US-SC" and hit["document"]["neutral_citation"] == "410 U.S. 113"
    assert hit["document"]["decision_date"] == "1973-01-22" and hit["version"]["authoritative"] is False
    assert hit["version"]["text_origin"] == "official_conversion"
    assert hit["authority"]["category"] == "precedent_vertical"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"AUTHORITY", "PROFILE_UNVERIFIED"} <= codes
    below = next(
        r for r in _search(session, catalog, q=q, forum_court="US-CA9")["results"] if r["unit"]["eid"] == "p153"
    )
    assert below["citation"] == hit["citation"] and below["authority"]["category"] == "binding_precedent"
    # the volume says nothing about later treatment: nothing is marked overruled
    assert all(r["authority"]["category"] != "overruled_precedent" for r in results)


@pytest.mark.db
def test_search_finds_a_case_from_the_shared_page_volume(
    session: Session, catalog: Catalog, us_reports: IngestionReport
) -> None:
    res = _search(session, catalog, q="War Revenue Act contingent beneficial interest Refunding Act tax erroneously")
    hit = res["results"][0]
    assert hit["citation"].startswith("Coleman") and hit["citation"].endswith("(1919)")
