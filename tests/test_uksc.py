"""UK Supreme Court judgments from the Court's own website (HTML): parser, citations, connector, search.

The fixtures in ``tests/fixtures/uksc`` are real pages of
``https://www.supremecourt.uk/cases/judgments/uksc-<year>-<number>`` (gzip-compressed, retrieved 2026-10-14).
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
from ulis.ingestion.pipeline import IngestionReport, Ingestor, build_connector, resolve_uri
from ulis.jurisdictions.modules import JudgmentCitationInput, get_module
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.connectors.uksc import ROBOTS_URL, UkscConnector, judgment_url
from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers.uksc_html import UkscParseError, parse_uksc
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

SOURCE = "gb-uksc-judgments"
HOST = "www.supremecourt.uk"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
FIX = REPO_ROOT / "tests" / "fixtures" / "uksc"
ABBEY, FORTHWELL, DM = "2022_0124", "2025_0081", "2026_0080"
ALL = [ABBEY, FORTHWELL, DM]


def _id(name: str) -> str:
    year, number = name.split("_")
    return f"UKSC/{year}/{number}"


def _raw(name: str) -> bytes:
    return gzip.decompress((FIX / f"uksc_{name}.html.gz").read_bytes())


def _doc(name: str) -> ParsedDocument:
    docs = parse_uksc(_raw(name))
    assert len(docs) == 1
    return docs[0]


def _para(doc: ParsedDocument, eid: str) -> Any:
    return {p.eid: p for p in doc.paragraphs}[eid]


# --------------------------------------------------------------------------- parser


def test_metadata() -> None:
    doc = _doc(ABBEY)
    meta = doc.judgment
    assert meta is not None
    assert doc.work_uri == "/gb/uksc/2024/23" and doc.expression_uri == "/gb/uksc/2024/23/eng@2024-07-09"
    assert (meta.court_id, meta.neutral_citation, meta.docket) == ("GB-UKSC", "[2024] UKSC 23", "UKSC/2022/0124")
    assert meta.case_name == "Abbey Healthcare (Mill Hill) Ltd v Augusta 2008 LLP (formerly Simply Construct (UK) LLP)"
    assert meta.decision_date == date(2024, 7, 9) and doc.version_date == date(2024, 7, 9)
    assert (doc.language, doc.authoritative, doc.jurisdiction_id) == ("en", False, "GB")
    assert doc.ext["appellants"] == ["Simply Construct (UK) LLP"]
    assert doc.ext["respondents"] == ["Abbey Healthcare (Mill Hill) Ltd"]
    assert doc.ext["judgment_appealed"] == "[2022] EWCA Civ 823"
    assert doc.ext["justices"] == ["Lord Briggs", "Lord Hamblen", "Lady Rose", "Lord Richards", "Lady Simler"]
    assert (doc.ext["hearing_start"], doc.ext["hearing_end"]) == ("29 April 2024", "29 April 2024")
    dm = _doc(DM)
    assert dm.judgment is not None and dm.judgment.case_name == "In the Petition of DM No 2"
    assert dm.ext["interveners"][0].startswith("Reunite International Child Abduction Centre")
    assert dm.judgment.neutral_citation == "[2026] UKSC 32" and dm.judgment.decision_date == date(2026, 9, 9)


def test_numbered_paragraphs_headings_and_unnumbered_text() -> None:
    doc = _doc(ABBEY)
    numbered = [p for p in doc.paragraphs if p.num]
    assert [p.num for p in numbered] == [str(i) for i in range(1, 86)]  # 85 paragraphs, one run
    assert all(p.part == "considerations" for p in numbered)
    assert _para(doc, "para-1").text.startswith("Under the Housing Grants, Construction and Regeneration Act 1996")
    assert _para(doc, "para-85").text == "I would therefore allow the appeal."
    assert _para(doc, "h-1").part == "heading" and _para(doc, "h-1").text.startswith("LORD HAMBLEN (with whom")
    assert _para(doc, "h-2").text == "Introduction"
    # A numbered clause quoted in paragraph 47 does not restart the numbering, and quoted paragraphs stay unnumbered.
    assert _para(doc, "para-47").text.startswith("The collateral warranty provided to the beneficiary")
    quoted = [p for p in doc.paragraphs if p.part == "text" and p.text.startswith("31. On the other hand")]
    assert len(quoted) == 1 and quoted[0].num is None
    assert [p.sort_order for p in doc.paragraphs] == list(range(len(doc.paragraphs)))
    assert len({p.eid for p in doc.paragraphs}) == len(doc.paragraphs)
    assert all("  " not in p.text and "\xa0" not in p.text for p in doc.paragraphs)
    assert {p.part for p in doc.paragraphs} >= {"heading", "considerations", "text", "item"}
    assert not any(p.text == "Judgment details" for p in doc.paragraphs)  # the page's own heading is not text


def test_links_to_legislation_are_the_courts_hyperlinks_and_court_citations_are_patterns() -> None:
    doc = _doc(ABBEY)
    first = [(r.href, r.text, r.relation, r.extraction_method) for r in _para(doc, "para-1").refs]
    assert first == [
        (
            "http://www.legislation.gov.uk/id/ukpga/1996/53",
            "Housing Grants, Construction and Regeneration Act 1996",
            "references",
            "source_markup",
        )
    ]
    hrefs = {r.href for p in doc.paragraphs for r in p.refs}
    assert "http://www.legislation.gov.uk/id/ukpga/1996/53/section/104/1" in hrefs
    assert not any("nationalarchives" in h or "bailii" in h for h in hrefs)  # copies elsewhere are not citations
    # The pipeline resolves such a link to the work and the provision the legislation connector uses.
    assert resolve_uri("http://www.legislation.gov.uk/id/ukpga/1996/53/section/104/1") == (
        "/gb/ukpga/1996/53",
        "section-104-1",
    )
    cites = [r for p in _doc(FORTHWELL).paragraphs for r in p.refs]
    assert [(r.href, r.text, r.relation, r.extraction_method) for r in cites] == [
        ("/gb/uksc/2017/32", "[2017] UKSC 32", "cites", "pattern")
    ]
    own = [r for p in _doc(FORTHWELL).paragraphs for r in p.refs if r.href == "/gb/uksc/2026/33"]
    assert own == []  # the judgment's own citation is not a link


def test_parser_refuses_what_it_cannot_read_safely() -> None:
    good = _raw(FORTHWELL).decode("utf-8")
    cases = [
        (b"\xff\xfe", "not UTF-8"),
        (b"<html></html>", "no case id"),
        (good.replace('id="case-title"', 'id="x"').encode(), "no case title"),
        (good.replace('id="neutral-citation-number"', 'id="x"').encode(), "neutral citation"),
        (good.replace("17 September 2026", "sometime").encode(), "judgment date"),
        (good.replace('id="judgment-details"', 'id="x"').encode(), "judgment-details"),
    ]
    for content, message in cases:
        with pytest.raises(UkscParseError, match=message):
            parse_uksc(content)
    start = good.rfind('id="judgment-details"')
    no_text = good[: good.find(">", good.find("</h", start)) + 1] + "<footer></footer>"
    with pytest.raises(ParseSkipped, match="no judgment text"):
        parse_uksc(no_text.encode())
    stripped = (
        good[: good.find(">", good.find("</h", start)) + 1] + "<p>Only an unnumbered statement.</p><footer></footer>"
    )
    with pytest.raises(UkscParseError, match="no numbered paragraphs"):
        parse_uksc(stripped.encode())


# --------------------------------------------------------------------------- citations, profile, source


def _citation(doc: ParsedDocument, paragraph: str | None) -> str:
    assert doc.judgment is not None
    return get_module("gb").judgment_citation(
        JudgmentCitationInput(
            doc.judgment.case_name,
            doc.judgment.neutral_citation,
            doc.judgment.decision_date,
            doc.judgment.docket,
            doc.judgment.court_id,
            "Supreme Court of the United Kingdom",
            paragraph,
            "en",
            "GB",
            doc.ext,
        )
    )


def test_oscola_style_citations() -> None:
    assert _citation(_doc(ABBEY), "47") == (
        "Abbey Healthcare (Mill Hill) Ltd v Augusta 2008 LLP (formerly Simply Construct (UK) LLP) [2024] UKSC 23 [47]"
    )
    assert _citation(_doc(DM), None) == "In the Petition of DM No 2 [2026] UKSC 32"
    gb = get_module("gb")
    assert gb.validate_ext(_doc(DM).ext)["case_id"] == "UKSC/2026/0080"
    with pytest.raises(ValidationError):
        gb.validate_ext({"case_id": "UKSC/2026/0080", "unknown": 1})
    assert gb.validate_ext({"leg_type": "ukpga", "year": 1998, "number": "42"})["year"] == 1998  # legislation unchanged


def test_profile_source_and_manifest(catalog: Catalog, tmp_path: Path) -> None:
    reg = catalog.jurisdictions
    assert reg.court_jurisdiction("GB-UKSC") == "GB" and reg.get("GB").profile_version == "0.3.0"
    assert "gb-uksc-judgments" in reg.get("GB").publication.official_sources
    source = catalog.sources.get(SOURCE)
    licence_gate(source)
    assert source.licence.id == "OGL-UK-3.0" and source.licence.attribution_required is True
    assert (source.licence.attribution_text or "").startswith("Contains public sector information licensed under")
    assert isinstance(build_connector(source), UkscConnector)
    manifest = UkscConnector(REPO_ROOT / source.access.endpoint, SOURCE, tmp_path)._manifest()
    cases = manifest["cases"]
    assert len(cases) == len(set(cases)) == 126 and cases == sorted(cases)  # one page per judgment (156 pages)
    assert cases[0] == "UKSC/2021/0098" and cases[-1] == "UKSC/2026/0080" and ABBEY.replace("_", "/") not in cases
    assert _id(ABBEY) in cases and _id(FORTHWELL) in cases and _id(DM) in cases
    assert manifest["crawl_delay_seconds"] >= 5 and "(" in manifest["user_agent"]
    # The sources that could not be used are recorded, not ingested.
    for planned in ("gb-find-case-law", "ie-courts-judgments", "ca-scc-decisions", "au-hca-judgments"):
        assert catalog.sources.get(planned).status == "planned"


# --------------------------------------------------------------------------- connector


def _cache_page(cache: Path, name: str, content: bytes, content_type: str = "text/html; charset=utf-8") -> None:
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    url = judgment_url(_id(name))
    path = http.cache_path(url, "judgments/" + url.rsplit("/", 1)[1] + ".html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    meta = {"url": url, "final_url": url, "status": 200, "content_type": content_type}
    path.with_name(path.name + ".meta.json").write_text(
        json.dumps({**meta, "retrieved_at": "2026-10-14T21:00:00+00:00"})
    )


def _cache_robots(cache: Path, robots: str | None) -> None:
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    path = http.cache_path(ROBOTS_URL, "robots.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((robots or "").encode())
    path.with_name(path.name + ".meta.json").write_text(
        json.dumps({"url": ROBOTS_URL, "status": 404 if robots is None else 200})
    )


def _manifest(tmp_path: Path, cases: list[str], **extra: Any) -> Path:
    body = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": f"https://{HOST}",
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "cases": cases,
        **extra,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


def test_connector_offline_from_cache_yields_exact_pages(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_robots(cache, "User-agent: *\nDisallow: /contact-us/book-room-hearing\n")
    for name in (ABBEY, DM):
        _cache_page(cache, name, _raw(name))
    cases = [_id(ABBEY), _id(DM)]
    connector = UkscConnector(_manifest(tmp_path, cases), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [f"uksc:{c}" for c in cases]
    assert docs[0].content == _raw(ABBEY)  # exact bytes as received
    assert docs[0].uri == "https://www.supremecourt.uk/cases/judgments/uksc-2022-0124"
    assert docs[0].media_type == "text/html" and docs[0].retrieved_at.year == 2026
    assert connector.fetch_errors == []
    with pytest.raises(FetchError, match="offline and not cached"):
        list(UkscConnector(_manifest(tmp_path, [_id(FORTHWELL)]), SOURCE, cache, offline=True).fetch())


def test_connector_reports_pages_that_are_not_the_listed_judgment_and_goes_on(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_robots(cache, None)
    _cache_page(cache, ABBEY, _raw(DM))  # the page of another case
    _cache_page(cache, DM, b"{}", content_type="application/json")
    _cache_page(cache, FORTHWELL, _raw(FORTHWELL))
    connector = UkscConnector(_manifest(tmp_path, [_id(ABBEY), _id(DM), _id(FORTHWELL)]), SOURCE, cache, offline=True)
    assert [d.external_id for d in connector.fetch()] == [f"uksc:{_id(FORTHWELL)}"]
    assert len(connector.fetch_errors) == 2
    assert "is not the judgment page of UKSC/2022/0124" in connector.fetch_errors[0]
    assert "expected HTML" in connector.fetch_errors[1]


def test_connector_skips_a_second_page_of_the_same_judgment(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_robots(cache, None)
    _cache_page(cache, ABBEY, _raw(ABBEY))
    joined = _raw(ABBEY).replace(
        b"UKSC/2022/0124", b"UKSC/2022/0125"
    )  # a joined appeal: another case id, same judgment
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    url = judgment_url("UKSC/2022/0125")
    page = http.cache_path(url, "judgments/uksc-2022-0125.html")
    page.write_bytes(joined)
    meta = {
        "url": url,
        "final_url": url,
        "status": 200,
        "content_type": "text/html",
        "retrieved_at": "2026-10-14T21:00:00+00:00",
    }
    page.with_name(page.name + ".meta.json").write_text(json.dumps(meta))
    connector = UkscConnector(_manifest(tmp_path, ["UKSC/2022/0124", "UKSC/2022/0125"]), SOURCE, cache, offline=True)
    assert [d.external_id for d in connector.fetch()] == ["uksc:UKSC/2022/0124"]
    assert connector.fetch_errors == [f"{url}: the same judgment ([2024] UKSC 23) as UKSC/2022/0124; skipped"]


def test_connector_refuses_bad_manifests_and_obeys_robots_txt(tmp_path: Path) -> None:
    for cases, extra, message in (
        (["UKSC/22/124"], {}, "invalid case id"),
        (["UKSC/2022/0124/../x"], {}, "invalid case id"),
        ([_id(ABBEY), _id(ABBEY)], {}, "listed twice"),
        ([], {}, "no cases"),
        ([_id(ABBEY)], {"base_url": "http://www.supremecourt.uk"}, "base_url"),
        ([_id(ABBEY)], {"user_agent": "python-urllib"}, "user_agent"),
        ([_id(ABBEY)], {"crawl_delay_seconds": 1}, "at least 5"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(UkscConnector(_manifest(tmp_path, cases, **extra), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="source_id"):
        list(UkscConnector(_manifest(tmp_path, [_id(ABBEY)], source_id="other"), SOURCE, tmp_path).fetch())
    blocked = tmp_path / "blocked"
    _cache_robots(blocked, "User-agent: *\nDisallow: /cases/\n")
    _cache_page(blocked, ABBEY, _raw(ABBEY))
    with pytest.raises(FetchError, match="disallowed by"):
        list(UkscConnector(_manifest(tmp_path, [_id(ABBEY)]), SOURCE, blocked, offline=True).fetch())


# --------------------------------------------------------------------------- PostgreSQL


class _Judgments:
    def fetch(self) -> Iterator[RawDocument]:
        for name in ALL:
            yield RawDocument(
                f"uksc:{_id(name)}",
                judgment_url(_id(name)),
                "text/html",
                _raw(name),
                datetime(2026, 10, 14, 21, 0, tzinfo=UTC),
                {},
            )


@pytest.fixture(scope="module")
def judgments(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Judgments())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="GB", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_ingest_report(judgments: IngestionReport) -> None:
    assert judgments.errors == [] and judgments.conflicts == []
    assert judgments.documents_new == 3 and judgments.judgments_created == 3
    expected = sum(len(_doc(n).paragraphs) for n in ALL)
    assert judgments.paragraphs_created == judgments.passages_indexed == expected
    links = sum(len(p.refs) for n in ALL for p in _doc(n).paragraphs)
    assert judgments.instruments_created == 0 and judgments.citation_links_created == links


@pytest.mark.db
def test_search_returns_the_numbered_paragraph_with_the_oscola_citation(
    session: Session, catalog: Catalog, judgments: IngestionReport
) -> None:
    res = _search(
        session,
        catalog,
        q="collateral warranty construction contract adjudication carrying out of construction operations",
    )
    hit = res["results"][0]
    assert hit["kind"] == "judgment_paragraph"
    assert hit["citation"].startswith("Abbey Healthcare (Mill Hill) Ltd v Augusta 2008 LLP") and hit[
        "citation"
    ].endswith("[2024] UKSC 23 [3]")
    assert hit["unit"]["heading"] == "considerations" and hit["unit"]["eid"] == "para-3"
    assert (hit["document"]["court_id"], hit["document"]["decision_date"]) == ("GB-UKSC", "2024-07-09")
    assert hit["authority"]["category"] in ("binding_precedent", "precedent_vertical", "persuasive_precedent")
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"AUTHORITY", "SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED"} <= codes
    assert hit["source"]["attribution"].startswith("Contains public sector information licensed under")
    assert hit["version"]["authoritative"] is False and hit["version"]["language"] == "en"


@pytest.mark.db
def test_links_and_reingest(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, judgments: IngestionReport
) -> None:
    with session_factory() as session:
        rows = {
            j.neutral_citation: j for j in session.scalars(select(m.Judgment).where(m.Judgment.court_id == "GB-UKSC"))
        }
        assert {"[2024] UKSC 23", "[2026] UKSC 33", "[2026] UKSC 32"} <= set(rows)
        abbey = rows["[2024] UKSC 23"]
        assert (abbey.docket, abbey.decision_date) == ("UKSC/2022/0124", date(2024, 7, 9))
        links = session.scalars(select(m.CitationLink).where(m.CitationLink.from_work_uri == abbey.uri)).all()
        assert {lk.to_uri for lk in links} >= {"http://www.legislation.gov.uk/id/ukpga/1996/53"}
        # The Housing Grants Act 1996 is not ingested by any test, so its links stay open.
        assert all(lk.to_instrument_id is None for lk in links if "ukpga/1996/53" in lk.to_uri)
        pattern = session.scalars(
            select(m.CitationLink).where(
                m.CitationLink.extraction_method == "pattern", m.CitationLink.to_uri.like("/gb/uksc/%")
            )
        ).all()
        assert pattern and all(lk.relation == "cites" for lk in pattern)
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Judgments())
        assert again.documents_unchanged == 3 and again.judgments_created == 0 and again.paragraphs_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == 3 and all(r["result"] == "pass" for r in results)
        session.rollback()
