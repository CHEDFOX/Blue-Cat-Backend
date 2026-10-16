"""Judicial Committee of the Privy Council judgments from jcpc.uk (HTML and PDF): parsers, sources, connectors, search.

The fixtures in ``tests/fixtures/jcpc`` are real: the judgment page of ``[2024] UKPC 11`` (Great Lakes Reinsurance v RAV
Bahamas, ``https://jcpc.uk/cases/judgments/jcpc-2023-0044``) and the case page and judgment PDF of ``[2016] UKPC 7``
(Ruddock v The Queen, the Jamaican appeal heard with R v Jogee), retrieved 2026-10-16.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport, Ingestor, build_connector
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.jcpc import JcpcConnector, JcpcPdfConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.connectors.uksc import judgment_url
from ulis.sources.connectors.uksc_pdf import case_url
from ulis.sources.parsers.uksc_html import UkscParseError, parse_uksc
from ulis.sources.parsers.uksc_pdf import BUNDLE_SCHEMA, bundle_bytes, parse_uksc_pdf
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore

HOST = "jcpc.uk"
HTML_SOURCE, PDF_SOURCE = "gb-jcpc-judgments", "gb-jcpc-judgments-pdf"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
FIX = REPO_ROOT / "tests" / "fixtures" / "jcpc"
GREAT_LAKES, RUDDOCK = "JCPC/2023/0044", "JCPC/2015/0020"


def _great_lakes() -> bytes:
    return gzip.decompress((FIX / "jcpc_2023_0044.html.gz").read_bytes())


def _ruddock_page() -> bytes:
    return gzip.decompress((FIX / "jcpc_2015_0020.html.gz").read_bytes())


def _ruddock_pdf() -> bytes:
    return (FIX / "jcpc_2015_0020_judgment.pdf").read_bytes()


def _ruddock_bundle() -> bytes:
    return bundle_bytes(
        {
            "schema": BUNDLE_SCHEMA,
            "case_page_url": case_url(RUDDOCK, host=HOST, prefix="JCPC"),
            "case_page": _ruddock_page().decode("utf-8"),
            "pdf_url": f"https://{HOST}/uploads/jcpc_2015_0020_judgment_x.pdf",
            "pdf_base64": base64.b64encode(_ruddock_pdf()).decode(),
        }
    )


# --------------------------------------------------------------------------- parsers


def test_html_judgment_is_a_privy_council_work_with_its_own_series() -> None:
    doc = parse_uksc(_great_lakes())[0]
    meta = doc.judgment
    assert meta is not None
    assert (meta.court_id, meta.neutral_citation, meta.docket) == ("GB-JCPC", "[2024] UKPC 11", GREAT_LAKES)
    assert doc.work_uri == "/gb/ukpc/2024/11" and doc.expression_uri == "/gb/ukpc/2024/11/eng@2024-05-21"
    assert meta.case_name.startswith("Great Lakes Reinsurance (UK) PLC (as Subrogee of Modrono's Bimini Place Ltd)")
    assert "&#x27;" not in meta.case_name  # the page's character reference is decoded
    assert meta.decision_date == date(2024, 5, 21) and doc.jurisdiction_id == "GB" and doc.authoritative is False
    assert doc.ext["justices"] == ["Lord Briggs", "Lord Hamblen", "Lord Leggatt", "Lord Burrows", "Lord Stephens"]
    numbered = [p for p in doc.paragraphs if p.part == "considerations"]
    assert len(numbered) >= 40 and numbered[-1].num == "43"
    # Citations of both courts in the text are pattern links to the work of that court.
    links = {
        (r.href, r.text, r.extraction_method)
        for p in doc.paragraphs
        for r in p.refs
        if r.extraction_method == "pattern"
    }
    assert ("/gb/uksc/2018/4", "[2018] UKSC 4", "pattern") in links
    assert ("/gb/ukpc/2020/29", "[2020] UKPC 29", "pattern") in links
    assert not any(r.text == "[2024] UKPC 11" for p in doc.paragraphs for r in p.refs)  # not a link to itself


def test_a_court_with_the_wrong_neutral_citation_series_is_refused() -> None:
    page = _great_lakes().decode("utf-8")
    with pytest.raises(UkscParseError, match="neutral citation"):
        parse_uksc(page.replace("[2024] UKPC 11", "[2024] UKSC 11").encode())
    with pytest.raises(UkscParseError, match="no case id"):
        parse_uksc(page.replace("JCPC/2023/0044", "XXXX/2023/0044").encode())


def test_pdf_judgment_is_read_from_the_bundle() -> None:
    doc = parse_uksc_pdf(_ruddock_bundle())[0]
    meta = doc.judgment
    assert meta is not None
    assert (meta.court_id, meta.neutral_citation, meta.docket) == ("GB-JCPC", "[2016] UKPC 7", RUDDOCK)
    assert meta.case_name == "Ruddock (Appellant) v The Queen (Respondent) (Jamaica)" and meta.decision_date == date(
        2016, 2, 18
    )
    assert doc.work_uri == "/gb/ukpc/2016/7" and doc.text_origin == "official_conversion"
    assert doc.ext["judgment_appealed"].startswith("[2013] EWCA Crim 1433")
    numbered = [p for p in doc.paragraphs if p.part == "considerations"]
    assert [p.num for p in numbered] == [str(i) for i in range(1, 121)]
    assert numbered[0].text.startswith("In the language of the criminal law a person who assists or encourages another")
    headings = [p.text for p in doc.paragraphs if p.part == "heading"]
    assert headings[0].startswith("LORD HUGHES AND LORD TOULSON: (with whom Lord Neuberger")
    assert {"History", "Australia", "Analysis"} <= set(headings)


# --------------------------------------------------------------------------- sources and manifests


def test_sources_profile_and_committed_manifests(catalog: Catalog, tmp_path: Path) -> None:
    reg = catalog.jurisdictions
    assert reg.court_jurisdiction("GB-JCPC") == "GB"
    assert {"gb-jcpc-judgments", "gb-jcpc-judgments-pdf"} <= set(reg.get("GB").publication.official_sources)
    html = catalog.sources.get(HTML_SOURCE)
    pdf = catalog.sources.get(PDF_SOURCE)
    for source, cls in ((html, JcpcConnector), (pdf, JcpcPdfConnector)):
        licence_gate(source)
        assert source.licence.id == "OGL-UK-3.0" and source.licence.attribution_required is True
        assert (source.licence.attribution_text or "").startswith("Contains public sector information licensed under")
        assert "jcpc.uk" in (source.licence.attribution_text or "")
        assert isinstance(build_connector(source), cls)
    html_cases = JcpcConnector(REPO_ROOT / html.access.endpoint, HTML_SOURCE, tmp_path)._manifest()["cases"]  # noqa: SLF001
    pdf_cases = JcpcPdfConnector(REPO_ROOT / pdf.access.endpoint, PDF_SOURCE, tmp_path)._manifest()["cases"]  # noqa: SLF001
    assert len(html_cases) > 130 and len(pdf_cases) > 800 and GREAT_LAKES in html_cases and RUDDOCK in pdf_cases
    assert set(html_cases).isdisjoint(pdf_cases)  # each judgment is in one source only
    assert html_cases == sorted(html_cases) and pdf_cases == sorted(pdf_cases)
    assert all(c.startswith("JCPC/") for c in [*html_cases, *pdf_cases])
    # The Supreme Court's manifests are not mixed up with the Committee's.
    with pytest.raises(ManifestError, match="invalid case id"):
        judgment_url("UKSC/2022/0124", host=HOST, prefix="JCPC")


# --------------------------------------------------------------------------- connectors


def _cache(cache: Path, url: str, key: str, content: bytes, content_type: str) -> None:
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    path = http.cache_path(url, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    meta = {"url": url, "final_url": url, "status": 200, "content_type": content_type}
    path.with_name(path.name + ".meta.json").write_text(
        json.dumps({**meta, "retrieved_at": "2026-10-16T08:00:00+00:00"})
    )


def _manifest(tmp_path: Path, source_id: str, cases: list[str], **extra: Any) -> Path:
    body = {
        "schema_version": 1,
        "source_id": source_id,
        "base_url": f"https://{HOST}",
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "cases": cases,
        **extra,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


def test_html_connector_reads_the_privy_council_host(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    url = judgment_url(GREAT_LAKES, host=HOST, prefix="JCPC")
    assert url == "https://jcpc.uk/cases/judgments/jcpc-2023-0044"
    _cache(
        cache,
        f"https://{HOST}/robots.txt",
        "robots.txt",
        b"User-agent: *\nDisallow: /contact-us/book-room-hearing\n",
        "text/plain",
    )
    _cache(cache, url, "judgments/jcpc-2023-0044.html", _great_lakes(), "text/html; charset=utf-8")
    connector = JcpcConnector(_manifest(tmp_path, HTML_SOURCE, [GREAT_LAKES]), HTML_SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [f"jcpc:{GREAT_LAKES}"] and docs[0].content == _great_lakes()
    assert docs[0].uri == url and connector.fetch_errors == []
    # A Supreme Court id, or the Supreme Court's host, is not accepted for this source.
    with pytest.raises(ManifestError, match="invalid case id"):
        list(
            JcpcConnector(
                _manifest(tmp_path, HTML_SOURCE, ["UKSC/2022/0124"]), HTML_SOURCE, cache, offline=True
            ).fetch()
        )
    with pytest.raises(ManifestError, match="base_url"):
        list(
            JcpcConnector(
                _manifest(tmp_path, HTML_SOURCE, [GREAT_LAKES], base_url="https://www.supremecourt.uk"),
                HTML_SOURCE,
                cache,
                offline=True,
            ).fetch()
        )


def test_pdf_connector_bundles_page_and_pdf_and_obeys_robots(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    url = case_url(RUDDOCK, host=HOST, prefix="JCPC")
    link = f"https://{HOST}/uploads/jcpc_2015_0020_judgment_975322ee6a.pdf"
    assert re.search(re.escape(link).encode(), _ruddock_page())  # the fixture page links the PDF
    _cache(
        cache,
        f"https://{HOST}/robots.txt",
        "robots.txt",
        b"User-agent: *\nDisallow: /contact-us/book-room-hearing\n",
        "text/plain",
    )
    _cache(cache, url, "cases/jcpc-2015-0020.html", _ruddock_page(), "text/html; charset=utf-8")
    _cache(cache, link, "uploads/jcpc_2015_0020_judgment_975322ee6a.pdf", _ruddock_pdf(), "application/pdf")
    connector = JcpcPdfConnector(_manifest(tmp_path, PDF_SOURCE, [RUDDOCK]), PDF_SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [f"jcpc-pdf:{RUDDOCK}"]
    bundle = json.loads(docs[0].content)
    assert base64.b64decode(bundle["pdf_base64"]) == _ruddock_pdf() and bundle["case_page"].encode() == _ruddock_page()
    assert parse_uksc_pdf(docs[0].content)[0].judgment.neutral_citation == "[2016] UKPC 7"  # type: ignore[union-attr]
    blocked = tmp_path / "blocked"
    _cache(blocked, f"https://{HOST}/robots.txt", "robots.txt", b"User-agent: *\nDisallow: /cases/\n", "text/plain")
    _cache(blocked, url, "cases/jcpc-2015-0020.html", _ruddock_page(), "text/html; charset=utf-8")
    with pytest.raises(FetchError, match="disallowed by https://jcpc.uk/robots.txt"):
        list(JcpcPdfConnector(_manifest(tmp_path, PDF_SOURCE, [RUDDOCK]), PDF_SOURCE, blocked, offline=True).fetch())


# --------------------------------------------------------------------------- PostgreSQL


class _Judgments:
    def fetch(self) -> Iterator[RawDocument]:
        yield RawDocument(
            f"jcpc:{GREAT_LAKES}",
            judgment_url(GREAT_LAKES, host=HOST, prefix="JCPC"),
            "text/html",
            _great_lakes(),
            datetime(2026, 10, 16, 8, 0, tzinfo=UTC),
            {},
        )


class _PdfJudgments:
    def fetch(self) -> Iterator[RawDocument]:
        yield RawDocument(
            f"jcpc-pdf:{RUDDOCK}",
            f"https://{HOST}/uploads/jcpc_2015_0020_judgment_x.pdf",
            "application/json",
            _ruddock_bundle(),
            datetime(2026, 10, 16, 8, 0, tzinfo=UTC),
            {},
        )


@pytest.fixture(scope="module")
def judgments(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> tuple[IngestionReport, IngestionReport]:
    with session_factory() as session:
        html = Ingestor(session, catalog, store).ingest_source(HTML_SOURCE, actor="pytest", connector=_Judgments())
        pdf = Ingestor(session, catalog, store).ingest_source(PDF_SOURCE, actor="pytest", connector=_PdfJudgments())
        session.commit()
    return html, pdf


@pytest.mark.db
def test_ingest_reports(judgments: tuple[IngestionReport, IngestionReport]) -> None:
    for report in judgments:
        assert report.errors == [] and report.conflicts == []
        assert report.documents_new == 1 and report.judgments_created == 1
    assert judgments[0].paragraphs_created == len(parse_uksc(_great_lakes())[0].paragraphs)
    assert judgments[1].paragraphs_created == len(parse_uksc_pdf(_ruddock_bundle())[0].paragraphs)


@pytest.mark.db
def test_search_returns_the_privy_council_paragraph_with_its_citation_and_no_binding_rating(
    session: Session, catalog: Catalog, judgments: tuple[IngestionReport, IngestionReport]
) -> None:
    res = search(
        session,
        catalog,
        SearchRequest(jurisdiction="GB", q="accessory is guilty of the same offence as the principal"),
        today=date(2026, 10, 16),
    )
    hit = next(r for r in res["results"] if r["kind"] == "judgment_paragraph" and "Ruddock" in r["citation"])
    assert hit["citation"].startswith("Ruddock (Appellant) v The Queen (Respondent) (Jamaica) [2016] UKPC 7 [")
    assert (hit["document"]["court_id"], hit["document"]["decision_date"]) == ("GB-JCPC", "2016-02-18")
    assert hit["authority"]["category"] not in ("binding_precedent", "precedent_vertical")
    assert {"AUTHORITY", "SOURCE_NOT_AUTHENTIC"} <= {lim["code"] for lim in hit["limitations"]}
    assert hit["source"]["attribution"].startswith("Contains public sector information licensed under")
