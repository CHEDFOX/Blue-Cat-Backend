"""Judgments of the courts of Northern Ireland from judiciaryni.uk: parser, connector, manifest, search.

The fixtures in ``tests/fixtures/ni_judiciary`` are real: the decision page (gzip-compressed HTML) and the judgment
PDF of
``[2013] NICA 1`` (The Queen v Daniel Curran, a sentence appeal) and ``[2026] NIKB 53`` (McGowan and Collins v
Ministry of
Defence (No 2)), retrieved 2026-10-16 from ``https://www.judiciaryni.uk``.
"""

from __future__ import annotations

import base64
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
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.ni_judiciary import ROBOTS_URL, NiJudiciaryConnector, decision_url
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers import ni_judiciary
from ulis.sources.parsers.ni_judiciary import BUNDLE_SCHEMA, NiJudiciaryParseError, bundle_bytes, parse_ni_judiciary
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore

SOURCE = "gb-ni-judiciary-judgments"
HOST = "www.judiciaryni.uk"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
FIX = REPO_ROOT / "tests" / "fixtures" / "ni_judiciary"
CURRAN, MCGOWAN = "2013-nica-1", "2026-nikb-53"
PDF_PATH = {
    CURRAN: "/files/judiciaryni/decisions/Queen%20v%20Daniel%20Curran.pdf",
    MCGOWAN: "/files/judiciaryni/2026-09/ROO13116Final%20-%20Approved.pdf",
}


def _page(slug: str) -> bytes:
    return gzip.decompress((FIX / f"{slug}.html.gz").read_bytes())


def _pdf(slug: str) -> bytes:
    return (FIX / f"{slug}.pdf").read_bytes()


def _bundle(slug: str) -> bytes:
    return bundle_bytes(
        {
            "schema": BUNDLE_SCHEMA,
            "decision_page_url": decision_url(slug),
            "decision_page": _page(slug).decode("utf-8"),
            "pdf_url": f"https://{HOST}{PDF_PATH[slug]}",
            "pdf_base64": base64.b64encode(_pdf(slug)).decode(),
        }
    )


def _doc(slug: str) -> ParsedDocument:
    docs = parse_ni_judiciary(_bundle(slug))
    assert len(docs) == 1
    return docs[0]


# --------------------------------------------------------------------------- parser


def test_court_of_appeal_decision() -> None:
    doc = _doc(CURRAN)
    meta = doc.judgment
    assert meta is not None
    assert (meta.court_id, meta.neutral_citation, meta.docket) == ("GB-NICA", "[2013] NICA 1", CURRAN)
    assert meta.case_name == "Queen v Daniel Curran" and meta.decision_date == date(2013, 1, 11)
    assert doc.work_uri == "/gb/nica/2013/1" and doc.expression_uri == "/gb/nica/2013/1/eng@2013-01-11"
    assert (doc.jurisdiction_id, doc.extent, doc.authoritative, doc.text_origin) == (
        "GB-NIR",
        ["GB-NIR"],
        False,
        "official_conversion",
    )
    assert doc.ext == {"case_id": CURRAN, "justices": ["Morgan LCJ"]}
    numbered = [p for p in doc.paragraphs if p.part == "considerations"]
    assert [p.num for p in numbered] == [str(i) for i in range(1, 21)]
    assert numbered[0].text.startswith(
        "This is an appeal against a sentence imposed on a plea of guilty by Judge Smyth QC"
    )
    assert "Count 5 – 1 year consecutive (different victim)" in numbered[0].text  # one paragraph, lines joined
    assert {"Background", "Consideration"} <= {p.text for p in doc.paragraphs if p.part == "heading"}
    assert all("\n" not in p.text for p in doc.paragraphs) and len({p.eid for p in doc.paragraphs}) == len(
        doc.paragraphs
    )
    assert doc.paragraphs[0].part == "text"  # the cover of the judgment, before paragraph 1


def test_high_court_decision_and_the_court_follows_the_series_of_the_citation() -> None:
    doc = _doc(MCGOWAN)
    assert doc.judgment is not None and doc.judgment.court_id == "GB-NIHC" and doc.work_uri == "/gb/nikb/2026/53"
    assert doc.judgment.neutral_citation == "[2026] NIKB 53" and doc.judgment.decision_date == date(2026, 9, 15)
    assert [p.num for p in doc.paragraphs if p.part == "considerations"][-1] == "36"
    assert {"Decision", "Conclusion"} <= {p.text for p in doc.paragraphs if p.part == "heading"}
    # Other series map to the Crown Court or to "other courts"; the work URI is the series of the citation.
    assert ni_judiciary.COURTS["NICA"] == "GB-NICA" and ni_judiciary.COURTS["NICC"] == "GB-NICC"
    page = json.loads(_bundle(MCGOWAN))
    page["decision_page"] = (
        page["decision_page"].replace("NIKB 53", "NICoroner 5").replace("[2026] NIKB", "[2026] NICoroner")
    )
    other = parse_ni_judiciary(bundle_bytes(page))[0]
    assert (
        other.judgment is not None
        and other.judgment.court_id == "GB-NIOTH"
        and other.work_uri == "/gb/nicoroner/2026/5"
    )


def test_court_citations_in_the_text_are_pattern_links_to_the_work_of_that_court() -> None:
    doc = _doc(CURRAN)
    links = [(r.href, r.text, r.extraction_method) for p in doc.paragraphs for r in p.refs]
    assert ("/gb/nica/2005/33", "[2005] NICA 33", "pattern") in links
    assert not any(text == "[2013] NICA 1" for _, text, _ in links)  # not a link to itself


def test_parser_refuses_or_skips_what_it_cannot_read(monkeypatch: pytest.MonkeyPatch) -> None:
    good = json.loads(_bundle(CURRAN))
    for change, message in (
        ({"schema": "other"}, "unexpected bundle schema"),
        ({"pdf_base64": "not base64!"}, "incomplete bundle"),
        ({"pdf_base64": base64.b64encode(b"%PDF-1.4 not really").decode()}, "cannot be read"),
        ({"decision_page": good["decision_page"].replace("Neutral Citation No.", "Citation")}, "neutral citation"),
        (
            {"decision_page": good["decision_page"].replace("<time", "<span").replace("</time>", "</span>")},
            "date delivered",
        ),
    ):
        with pytest.raises(NiJudiciaryParseError, match=message):
            parse_ni_judiciary(bundle_bytes({**good, **change}))
    with pytest.raises(NiJudiciaryParseError, match="not a ULIS bundle"):
        parse_ni_judiciary(b"<html></html>")
    monkeypatch.setattr(ni_judiciary, "pdf_pages", lambda _pdf: ["", "3"])
    with pytest.raises(ParseSkipped, match="no text layer"):
        parse_ni_judiciary(_bundle(CURRAN))
    # Sentencing remarks and rulings without numbered paragraphs are kept as unnumbered text.
    monkeypatch.setattr(
        ni_judiciary, "pdf_pages", lambda _pdf: ["Sentencing remarks of the judge in the Crown Court. " * 20]
    )
    doc = parse_ni_judiciary(_bundle(CURRAN))[0]
    assert doc.paragraphs and all(p.num is None for p in doc.paragraphs)


# --------------------------------------------------------------------------- source, manifest


def test_source_profile_and_committed_manifest(catalog: Catalog, tmp_path: Path) -> None:
    reg = catalog.jurisdictions
    assert reg.court_jurisdiction("GB-NICA") == "GB-NIR" and reg.court_jurisdiction("GB-NIHC") == "GB-NIR"
    source = catalog.sources.get(SOURCE)
    licence_gate(source)
    assert source.licence.id == "OGL-UK-3.0" and source.licence.attribution_required is True
    assert "Judiciary of Northern Ireland" in (source.licence.attribution_text or "")
    assert isinstance(build_connector(source), NiJudiciaryConnector)
    manifest = NiJudiciaryConnector(REPO_ROOT / source.access.endpoint, SOURCE, tmp_path)._manifest()  # noqa: SLF001
    decisions = manifest["decisions"]
    assert len(decisions) == len(set(decisions)) > 1000
    assert all(d[:4].isdigit() and d.split("-")[1].startswith("ni") for d in decisions)
    assert manifest["crawl_delay_seconds"] >= 5 and "(" in manifest["user_agent"]


# --------------------------------------------------------------------------- connector


def _cache(cache: Path, url: str, key: str, content: bytes, content_type: str) -> None:
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    path = http.cache_path(url, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    meta = {"url": url, "final_url": url, "status": 200, "content_type": content_type}
    path.with_name(path.name + ".meta.json").write_text(
        json.dumps({**meta, "retrieved_at": "2026-10-16T08:00:00+00:00"})
    )


def _cache_decision(cache: Path, slug: str, page: bytes | None = None, pdf: bytes | None = None) -> None:
    _cache(
        cache, decision_url(slug), f"judicial-decisions/{slug}.html", page or _page(slug), "text/html; charset=UTF-8"
    )
    if pdf is not None:
        _cache(cache, f"https://{HOST}{PDF_PATH[slug]}", f"pdf/{slug}.pdf", pdf, "application/pdf")


def _robots(cache: Path, text: str) -> None:
    _cache(cache, ROBOTS_URL, "robots.txt", text.encode(), "text/plain")


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


def test_connector_offline_bundles_page_and_pdf_exactly_as_received(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _robots(cache, "User-agent: *\nDisallow: /search/\nDisallow: /publications/*type\n")
    for slug in (CURRAN, MCGOWAN):
        _cache_decision(cache, slug, pdf=_pdf(slug))
    connector = NiJudiciaryConnector(_manifest(tmp_path, [CURRAN, MCGOWAN]), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [f"ni-judiciary:{CURRAN}", f"ni-judiciary:{MCGOWAN}"]
    bundle = json.loads(docs[0].content)
    assert bundle["schema"] == BUNDLE_SCHEMA and base64.b64decode(bundle["pdf_base64"]) == _pdf(CURRAN)
    assert bundle["decision_page"].encode() == _page(CURRAN)
    assert bundle["pdf_url"] == "https://www.judiciaryni.uk/files/judiciaryni/decisions/Queen%20v%20Daniel%20Curran.pdf"
    assert (
        docs[0].uri == "https://www.judiciaryni.uk/judicial-decisions/2013-nica-1" and docs[0].retrieved_at.year == 2026
    )
    assert connector.fetch_errors == [] and connector.not_judgments == []
    assert parse_ni_judiciary(docs[1].content)[0].judgment.neutral_citation == "[2026] NIKB 53"  # type: ignore[union-attr]
    with pytest.raises(FetchError, match="offline and not cached"):
        list(NiJudiciaryConnector(_manifest(tmp_path, ["2013-nica-2"]), SOURCE, cache, offline=True).fetch())


def test_connector_skips_what_is_not_a_judgment_a_second_page_and_a_bad_pdf(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _robots(cache, "User-agent: *\n")
    _cache_decision(cache, CURRAN, pdf=_pdf(CURRAN))
    no_citation = _page(CURRAN).replace(b"Neutral Citation No.", b"Reference")
    _cache(cache, decision_url("2013-nica-7"), "judicial-decisions/2013-nica-7.html", no_citation, "text/html")
    same = _page(CURRAN)  # another page with the same neutral citation
    _cache(cache, decision_url("2013-nica-8"), "judicial-decisions/2013-nica-8.html", same, "text/html")
    _cache(cache, decision_url("2013-nica-9"), "judicial-decisions/2013-nica-9.html", _page(MCGOWAN), "text/html")
    _cache(cache, f"https://{HOST}{PDF_PATH[MCGOWAN]}", "pdf/2013-nica-9.pdf", b"<html>not a pdf</html>", "text/html")
    connector = NiJudiciaryConnector(
        _manifest(tmp_path, [CURRAN, "2013-nica-7", "2013-nica-8", "2013-nica-9"]), SOURCE, cache, offline=True
    )
    assert [d.external_id for d in connector.fetch()] == [f"ni-judiciary:{CURRAN}"]
    assert connector.not_judgments == ["2013-nica-7"]
    assert len(connector.fetch_errors) == 2
    assert "the same decision ([2013] NICA 1) as 2013-nica-1" in connector.fetch_errors[0]
    assert "not a PDF" in connector.fetch_errors[1]


def test_connector_refuses_bad_manifests_and_obeys_robots_txt(tmp_path: Path) -> None:
    for decisions, extra, message in (
        (["practice-direction-4-2026"], {}, "invalid decision page name"),
        (["2013-nica-1/../x"], {}, "invalid decision page name"),
        ([CURRAN, CURRAN], {}, "listed twice"),
        ([], {}, "no decisions"),
        ([CURRAN], {"base_url": "http://www.judiciaryni.uk"}, "base_url"),
        ([CURRAN], {"user_agent": "python-urllib"}, "user_agent"),
        ([CURRAN], {"crawl_delay_seconds": 1}, "at least 5"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(NiJudiciaryConnector(_manifest(tmp_path, decisions, **extra), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="source_id"):
        list(NiJudiciaryConnector(_manifest(tmp_path, [CURRAN], source_id="other"), SOURCE, tmp_path).fetch())
    blocked = tmp_path / "blocked"
    _robots(blocked, "User-agent: *\nDisallow: /judicial-decisions/\n")
    _cache_decision(blocked, CURRAN, pdf=_pdf(CURRAN))
    with pytest.raises(FetchError, match="disallowed by"):
        list(NiJudiciaryConnector(_manifest(tmp_path, [CURRAN]), SOURCE, blocked, offline=True).fetch())
    files_blocked = tmp_path / "files-blocked"
    _robots(files_blocked, "User-agent: *\nDisallow: /files/\n")
    _cache_decision(files_blocked, CURRAN, pdf=_pdf(CURRAN))
    with pytest.raises(FetchError, match="/files/"):
        list(NiJudiciaryConnector(_manifest(tmp_path, [CURRAN]), SOURCE, files_blocked, offline=True).fetch())


# --------------------------------------------------------------------------- PostgreSQL


class _Judgments:
    def fetch(self) -> Iterator[RawDocument]:
        for slug in (CURRAN, MCGOWAN):
            yield RawDocument(
                f"ni-judiciary:{slug}",
                decision_url(slug),
                "application/json",
                _bundle(slug),
                datetime(2026, 10, 16, 8, 0, tzinfo=UTC),
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


@pytest.mark.db
def test_ingest_report(judgments: IngestionReport) -> None:
    assert judgments.errors == [] and judgments.conflicts == []
    assert judgments.documents_new == 2 and judgments.judgments_created == 2
    assert judgments.paragraphs_created == sum(len(_doc(s).paragraphs) for s in (CURRAN, MCGOWAN))


@pytest.mark.db
def test_search_in_northern_ireland_returns_the_paragraph_with_the_neutral_citation(
    session: Session, catalog: Catalog, judgments: IngestionReport
) -> None:
    res = search(
        session,
        catalog,
        SearchRequest(jurisdiction="GB-NIR", q="indecent assault parish priest seaside cottage Tyrella"),
        today=date(2026, 10, 16),
    )
    hit = next(r for r in res["results"] if r["kind"] == "judgment_paragraph" and "Curran" in r["citation"])
    assert hit["citation"].startswith("Queen v Daniel Curran [2013] NICA 1 [")
    assert hit["unit"]["heading"] == "considerations"
    assert (hit["document"]["court_id"], hit["document"]["decision_date"]) == ("GB-NICA", "2013-01-11")
    assert {"AUTHORITY", "SOURCE_NOT_AUTHENTIC"} <= {lim["code"] for lim in hit["limitations"]}
    assert hit["source"]["attribution"].startswith("Contains public sector information licensed under")
