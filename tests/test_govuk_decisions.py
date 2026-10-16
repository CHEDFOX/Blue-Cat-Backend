"""Tribunal decisions on GOV.UK (Employment Appeal Tribunal, Upper Tribunal AAC): parser, connector, manifest, search.

The fixtures in ``tests/fixtures/govuk_decisions`` are real responses of ``https://www.gov.uk/api/content/<page>``
(gzip-compressed JSON,
retrieved 2026-10-16): ``[2025] EAT 88`` (Okeze v Cygnet Health Care), ``UKEAT/0421/13/BA`` (Coker v Wandsworth, no
neutral citation),
``[2016] UKUT 296 (AAC)`` (paragraphs numbered without a full stop) and ``[2025] UKUT 70 (AAC)`` (a PDF whose
extraction has no
recognisable paragraph numbers).
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
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport, Ingestor, build_connector
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.govuk_decisions import (
    COLLECTIONS,
    PATH,
    ROBOTS_URL,
    GovUkDecisionsConnector,
    content_url,
)
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers.govuk_decisions import GovUkDecisionParseError, parse_govuk_decision
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore

SOURCE = "gb-govuk-tribunal-decisions"
HOST = "www.gov.uk"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
FIX = REPO_ROOT / "tests" / "fixtures" / "govuk_decisions"
EAT = "employment-appeal-tribunal-decisions/miss-shirley-okeze-v-cygnet-health-care-ltd-2025-eat-88"
OLD_EAT = "employment-appeal-tribunal-decisions/mrs-oluwaseryi-coker-v-wandsworth-borough-council-ukeat-0421-13-ba"
AAC = "administrative-appeals-tribunal-decisions/jm-v-secretary-of-state-for-work-and-pensions-pip-2016-ukut-296-aac"
AAC_PLAIN = (
    "administrative-appeals-tribunal-decisions/wb-v-secretary-of-state-for-work-and-pensions-pip-2025-ukut-070-aac"
)
FILES = {
    EAT: "eat_2025_88.json.gz",
    OLD_EAT: "eat_coker_ukeat_0421_13.json.gz",
    AAC: "ukut_2016_296_aac.json.gz",
    AAC_PLAIN: "ukut_2025_70_aac.json.gz",
}


def _raw(path: str) -> bytes:
    return gzip.decompress((FIX / FILES[path]).read_bytes())


def _doc(path: str) -> ParsedDocument:
    docs = parse_govuk_decision(_raw(path))
    assert len(docs) == 1
    return docs[0]


# --------------------------------------------------------------------------- parser


def test_employment_appeal_tribunal_decision_with_a_neutral_citation() -> None:
    doc = _doc(EAT)
    meta = doc.judgment
    assert meta is not None
    assert (meta.court_id, meta.neutral_citation, meta.docket) == ("GB-EAT", "[2025] EAT 88", "EA-2021-SCO-000119-DT")
    assert meta.case_name == "Miss Shirley Okeze v Cygnet Health Care Ltd"  # the citation is not repeated in the name
    assert meta.decision_date == date(2025, 6, 13) and doc.version_date == date(2025, 6, 13)
    assert doc.work_uri == "/gb/eat/2025/88" and doc.expression_uri == "/gb/eat/2025/88/eng@2025-06-13"
    assert (doc.jurisdiction_id, doc.authoritative, doc.text_origin) == ("GB", False, "official_conversion")
    assert doc.ext == {"case_id": "EA-2021-SCO-000119-DT"}
    numbered = [p for p in doc.paragraphs if p.part == "considerations"]
    assert [p.num for p in numbered] == [str(i) for i in range(1, 67)]
    assert numbered[0].text.startswith(
        "This appeal raises the issue of whether it was appropriate for an Employment Tribunal"
    )
    assert all("\n" not in p.text and "\r" not in p.text and "  " not in p.text for p in doc.paragraphs)
    assert len({p.eid for p in doc.paragraphs}) == len(doc.paragraphs)
    assert doc.paragraphs[0].part == "text" and doc.paragraphs[0].text.startswith(
        "Neutral Citation Number: [2025] EAT 88"
    )


def test_an_older_decision_without_a_neutral_citation_is_a_work_named_after_its_page() -> None:
    doc = _doc(OLD_EAT)
    meta = doc.judgment
    assert meta is not None
    assert meta.neutral_citation is None and meta.docket == "UKEAT/0421/13/BA"
    assert meta.case_name == "Mrs Oluwaseryi Coker v Wandsworth Borough Council: UKEAT/0421/13/BA"
    assert doc.work_uri == "/gb/eat/mrs-oluwaseryi-coker-v-wandsworth-borough-council-ukeat-0421-13-ba"
    assert meta.decision_date == date(2014, 6, 27)
    assert [p.num for p in doc.paragraphs if p.part == "considerations"][-1] == "28"


def test_upper_tribunal_decision_and_paragraphs_numbered_without_a_full_stop() -> None:
    doc = _doc(AAC)
    meta = doc.judgment
    assert meta is not None
    assert (meta.court_id, meta.neutral_citation) == ("GB-UTAAC", "[2016] UKUT 296 (AAC)")
    assert meta.case_name == "JM v Secretary of State for Work and Pensions (PIP)"
    assert doc.work_uri == "/gb/ukut/2016/296"  # the Upper Tribunal numbers its decisions in one series
    numbered = [p for p in doc.paragraphs if p.part == "considerations"]
    assert [p.num for p in numbered[:3]] == ["1", "2", "3"]
    assert numbered[0].text.startswith(
        "I held an oral hearing of this appeal on 10 February 2016 at Field House in London."
    )
    # A line that wraps onto "5 points" or "2 points" inside a sentence is not a paragraph number.
    nums = [int(p.num) for p in numbered if p.num]
    assert nums == sorted(nums) and len(nums) == len(set(nums)) and nums[-1] >= 50


def test_a_decision_without_recognisable_paragraph_numbers_is_kept_as_unnumbered_text() -> None:
    doc = _doc(AAC_PLAIN)
    assert doc.judgment is not None and doc.judgment.neutral_citation == "[2025] UKUT 70 (AAC)"
    assert doc.paragraphs and all(p.num is None and p.part != "considerations" for p in doc.paragraphs)


def test_parser_refuses_or_skips_what_it_cannot_use() -> None:
    good = json.loads(_raw(EAT))
    for change, message in (
        ({"document_type": "news_story"}, "unsupported document type"),
        ({"title": None}, "not a GOV.UK tribunal decision"),
    ):
        with pytest.raises(GovUkDecisionParseError, match=message):
            parse_govuk_decision(json.dumps({**good, **change}).encode())
    with pytest.raises(GovUkDecisionParseError, match="not a GOV.UK tribunal decision"):
        parse_govuk_decision(b"<html></html>")
    no_date = json.loads(_raw(EAT))
    del no_date["details"]["metadata"]["tribunal_decision_decision_date"]
    with pytest.raises(GovUkDecisionParseError, match="not a GOV.UK tribunal decision"):
        parse_govuk_decision(json.dumps(no_date).encode())
    scan = json.loads(_raw(EAT))
    scan["details"]["metadata"]["hidden_indexable_content"] = ""
    with pytest.raises(ParseSkipped, match="no text"):
        parse_govuk_decision(json.dumps(scan).encode())


def test_court_citations_in_the_text_are_pattern_links() -> None:
    page = json.loads(_raw(EAT))
    meta = page["details"]["metadata"]
    meta["hidden_indexable_content"] = (
        "Neutral Citation Number: [2025] EAT 88\n\nIntroduction\n"
        "1.\tThe tribunal applied Poole BC v GN [2019] UKSC 25 and Ruddock v The Queen [2016] UKPC 7 correctly. "
        + "x"
        * 300
    )
    doc = parse_govuk_decision(json.dumps(page).encode())[0]
    first = next(p for p in doc.paragraphs if p.num == "1")
    assert [(r.href, r.text, r.extraction_method) for r in first.refs] == [
        ("/gb/uksc/2019/25", "[2019] UKSC 25", "pattern"),
        ("/gb/ukpc/2016/7", "[2016] UKPC 7", "pattern"),
    ]
    assert "Introduction" in [p.text for p in doc.paragraphs if p.part == "heading"]


# --------------------------------------------------------------------------- source, manifest


def test_source_profile_and_committed_manifest(catalog: Catalog, tmp_path: Path) -> None:
    reg = catalog.jurisdictions
    assert reg.court_jurisdiction("GB-EAT") == "GB" and reg.court_jurisdiction("GB-UTAAC") == "GB"
    assert SOURCE in reg.get("GB").publication.official_sources
    source = catalog.sources.get(SOURCE)
    licence_gate(source)
    assert source.licence.id == "OGL-UK-3.0" and source.licence.attribution_required is True
    assert "GOV.UK" in (source.licence.attribution_text or "")
    assert isinstance(build_connector(source), GovUkDecisionsConnector)
    manifest = GovUkDecisionsConnector(REPO_ROOT / source.access.endpoint, SOURCE, tmp_path)._manifest()  # noqa: SLF001
    decisions = manifest["decisions"]
    assert len(decisions) == len(set(decisions)) > 4700
    by_collection = {c: sum(d.startswith(c + "/") for d in decisions) for c in COLLECTIONS}
    assert (
        by_collection["employment-appeal-tribunal-decisions"] > 2600
        and by_collection["administrative-appeals-tribunal-decisions"] > 2000
    )
    assert all(PATH.match(d) for d in decisions) and EAT in decisions and AAC in decisions
    # The collections of private individuals are not listed.
    assert not any(
        d.startswith(("employment-tribunal-decisions/", "residential-property-tribunal-decisions/")) for d in decisions
    )
    assert manifest["crawl_delay_seconds"] >= 5 and "(" in manifest["user_agent"]


# --------------------------------------------------------------------------- connector


def _cache(
    cache: Path, url: str, key: str, content: bytes, content_type: str = "application/json; charset=utf-8"
) -> None:
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    path = http.cache_path(url, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    meta = {"url": url, "final_url": url, "status": 200, "content_type": content_type}
    path.with_name(path.name + ".meta.json").write_text(
        json.dumps({**meta, "retrieved_at": "2026-10-16T08:00:00+00:00"})
    )


def _cache_robots(cache: Path, robots: str | None) -> None:
    _cache(cache, ROBOTS_URL, "robots.txt", (robots or "").encode(), "text/plain")
    if robots is None:  # a missing robots.txt is a 404
        http = PoliteHttp(cache, UA, allowed_hosts={HOST})
        path = http.cache_path(ROBOTS_URL, "robots.txt")
        path.with_name(path.name + ".meta.json").write_text(json.dumps({"url": ROBOTS_URL, "status": 404}))


def _manifest(tmp_path: Path, decisions: list[str], **extra: Any) -> Path:
    body = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": f"https://{HOST}",
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "decisions": decisions,
        **extra,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


def test_connector_offline_yields_the_responses_exactly_as_received(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_robots(cache, "User-agent: *\nDisallow: /*/print$\nDisallow: /search/all*\n")
    for path in (EAT, AAC):
        _cache(cache, content_url(path), f"api/content/{path}.json", _raw(path))
    connector = GovUkDecisionsConnector(_manifest(tmp_path, [EAT, AAC]), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [f"govuk:{EAT}", f"govuk:{AAC}"]
    assert docs[0].content == _raw(EAT) and docs[0].media_type == "application/json"
    assert docs[0].uri == f"https://www.gov.uk/{EAT}" and docs[0].retrieved_at.year == 2026
    assert connector.fetch_errors == []
    assert parse_govuk_decision(docs[1].content)[0].judgment.neutral_citation == "[2016] UKUT 296 (AAC)"  # type: ignore[union-attr]
    with pytest.raises(FetchError, match="offline and not cached"):
        list(GovUkDecisionsConnector(_manifest(tmp_path, [OLD_EAT]), SOURCE, cache, offline=True).fetch())


def test_connector_reports_pages_that_are_not_the_listed_decision_and_goes_on(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_robots(cache, None)
    _cache(cache, content_url(EAT), f"api/content/{EAT}.json", _raw(AAC))  # the page of another decision
    _cache(cache, content_url(OLD_EAT), f"api/content/{OLD_EAT}.json", b"<html>not json</html>")
    _cache(cache, content_url(AAC), f"api/content/{AAC}.json", _raw(AAC))
    connector = GovUkDecisionsConnector(_manifest(tmp_path, [EAT, OLD_EAT, AAC]), SOURCE, cache, offline=True)
    assert [d.external_id for d in connector.fetch()] == [f"govuk:{AAC}"]
    assert len(connector.fetch_errors) == 2
    assert "not the employment_appeal_tribunal_decision page" in connector.fetch_errors[0]
    assert "not JSON" in connector.fetch_errors[1]


def test_connector_refuses_bad_manifests_and_obeys_robots_txt(tmp_path: Path) -> None:
    for decisions, extra, message in (
        (["employment-tribunal-decisions/some-claimant-v-a-company"], {}, "invalid decision path"),
        (["employment-appeal-tribunal-decisions/../x"], {}, "invalid decision path"),
        ([EAT, EAT], {}, "listed twice"),
        ([], {}, "no decisions"),
        ([EAT], {"base_url": "http://www.gov.uk"}, "base_url"),
        ([EAT], {"user_agent": "python-urllib"}, "user_agent"),
        ([EAT], {"crawl_delay_seconds": 1}, "at least 5"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(GovUkDecisionsConnector(_manifest(tmp_path, decisions, **extra), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="source_id"):
        list(GovUkDecisionsConnector(_manifest(tmp_path, [EAT], source_id="other"), SOURCE, tmp_path).fetch())
    blocked = tmp_path / "blocked"
    _cache_robots(blocked, "User-agent: *\nDisallow: /api/\n")
    _cache(blocked, content_url(EAT), f"api/content/{EAT}.json", _raw(EAT))
    with pytest.raises(FetchError, match="disallowed by"):
        list(GovUkDecisionsConnector(_manifest(tmp_path, [EAT]), SOURCE, blocked, offline=True).fetch())


# --------------------------------------------------------------------------- PostgreSQL


class _Decisions:
    def fetch(self) -> Iterator[RawDocument]:
        for path in (EAT, OLD_EAT, AAC, AAC_PLAIN):
            yield RawDocument(
                f"govuk:{path}",
                f"https://{HOST}/{path}",
                "application/json",
                _raw(path),
                datetime(2026, 10, 16, 8, 0, tzinfo=UTC),
                {},
            )


@pytest.fixture(scope="module")
def decisions(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Decisions())
        session.commit()
    return report


@pytest.mark.db
def test_ingest_report(decisions: IngestionReport) -> None:
    assert decisions.errors == [] and decisions.conflicts == []
    assert decisions.documents_new == 4 and decisions.judgments_created == 4
    expected = sum(len(_doc(p).paragraphs) for p in FILES)
    assert decisions.paragraphs_created == decisions.passages_indexed == expected


@pytest.mark.db
def test_search_returns_the_numbered_paragraph_with_the_citation_of_the_tribunal(
    session: Session, catalog: Catalog, decisions: IngestionReport
) -> None:
    doc = _doc(EAT)
    probe = next(p for p in doc.paragraphs if p.num == "1")
    words = " ".join(probe.text.split()[:12])
    res = search(session, catalog, SearchRequest(jurisdiction="GB", q=words), today=date(2026, 10, 16))
    hit = next(r for r in res["results"] if r["kind"] == "judgment_paragraph" and "Okeze" in r["citation"])
    assert hit["citation"] == "Miss Shirley Okeze v Cygnet Health Care Ltd [2025] EAT 88 [1]"
    assert hit["unit"]["heading"] == "considerations" and hit["unit"]["eid"] == "para-1"
    assert (hit["document"]["court_id"], hit["document"]["decision_date"]) == ("GB-EAT", "2025-06-13")
    assert hit["authority"]["category"] not in ("binding_precedent", "precedent_vertical")
    assert {"AUTHORITY", "SOURCE_NOT_AUTHENTIC"} <= {lim["code"] for lim in hit["limitations"]}
    assert hit["source"]["attribution"].startswith("Contains public sector information licensed under")
    older = search(
        session,
        catalog,
        SearchRequest(jurisdiction="GB", q="remedies hearing Employment Tribunal Wandsworth"),
        today=date(2026, 10, 16),
    )
    old_hit = next(r for r in older["results"] if "Coker" in r["citation"])
    assert old_hit["citation"].startswith(
        "Mrs Oluwaseryi Coker v Wandsworth Borough Council: UKEAT/0421/13/BA (Employment Appeal Tribunal, 2014-06-27)"
    )
