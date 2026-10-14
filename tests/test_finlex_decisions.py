"""Decisions of the Data Protection Ombudsman from Finlex open data: parser, citations, connector, search.

The fixtures in ``tests/fixtures/finlex_decisions`` are real ``main.xml`` members of Finlex's package
``data-protection-ombudsman-decision.zip`` (gzip-compressed, retrieved 2026-10-14).
"""

from __future__ import annotations

import gzip
import io
import json
import re
import zipfile
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
from ulis.sources.connectors.finlex_decisions import ROBOTS_URL, FinlexDecisionsConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers.finlex_decisions import FinlexDecisionParseError, parse_finlex_decision
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

SOURCE = "fi-finlex-dpo-decisions"
HOST = "www.finlex.fi"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
FIX = REPO_ROOT / "tests" / "fixtures" / "finlex_decisions"
BASE = "akn/fi/judgment/data-protection-ombudsman-decision"
ARCHIVE = "files/open-data/archives/data-protection-ombudsman-decision.zip"
ARCHIVE_URL = f"https://{HOST}/{ARCHIVE}"
FILES = ["2005_188_fin", "2005_188_swe", "2019_481_fin", "2020_584_fin", "2025_2469_fin"]


def _raw(name: str) -> bytes:
    return gzip.decompress((FIX / f"{name}.xml.gz").read_bytes())


def _doc(name: str) -> ParsedDocument:
    docs = parse_finlex_decision(_raw(name))
    assert len(docs) == 1
    return docs[0]


def _member(name: str) -> str:
    year, number, lang = name.split("_")
    return f"{BASE}/{year}/{number}/{lang}@/main.xml"


def _para(doc: ParsedDocument, eid: str) -> Any:
    return {p.eid: p for p in doc.paragraphs}[eid]


# --------------------------------------------------------------------------- parser


def test_metadata_title_and_languages() -> None:
    fin, swe = _doc("2005_188_fin"), _doc("2005_188_swe")
    assert fin.work_uri == swe.work_uri == f"/{BASE}/2005/188"
    assert fin.expression_uri == f"/{BASE}/2005/188/fin@2026-05-19"
    assert swe.expression_uri == f"/{BASE}/2005/188/swe@2026-05-19"
    assert (fin.language, swe.language) == ("fi", "sv")
    assert fin.doc_kind == "judgment" and fin.jurisdiction_id == "FI" and fin.authoritative is False
    meta = fin.judgment
    assert meta is not None
    assert (meta.court_id, meta.docket, meta.neutral_citation) == ("FI-TSV", "1164/41/2005", None)
    assert meta.decision_date == date(2005, 7, 26) and fin.version_date == date(2026, 5, 19)
    assert meta.case_name == "Rekisteröidyn tarkastusoikeus internetin kautta"
    assert swe.judgment is not None and swe.judgment.case_name == "Den registrerades rätt till insyn via nätet"
    assert fin.ext["legal_basis"] == "Henkilötietolain mukainen päätös" and fin.ext["keywords"] == [
        "tarkastusoikeus",
        "internet",
    ]
    assert fin.ext["subtype"] == "data-protection-ombudsman-decision" and fin.ext["number"] == "188"
    assert _doc("2019_481_fin").ext["legal_basis"] == "EU:n yleisen tietosuoja-asetuksen mukainen päätös"


def test_paragraphs_are_the_text_in_order_without_numbers() -> None:
    doc = _doc("2005_188_fin")
    assert [p.part for p in doc.paragraphs] == ["headnote"] + ["text"] * 6
    assert all(p.num is None for p in doc.paragraphs)
    assert [p.sort_order for p in doc.paragraphs] == list(range(7))
    first = _para(doc, "p-1")
    # <b>ASIA</b><br/>...: the heading line is kept with its line break, whitespace is collapsed.
    assert first.text.startswith("ASIA\nVoidaanko rekisteröidyn tarkastusoikeus")
    assert "  " not in first.text and "\n " not in first.text


def test_introduction_headings_items_and_text_parts() -> None:
    intro = _doc("2020_584_fin")
    assert [p.part for p in intro.paragraphs[:3]] == ["headnote", "introduction", "introduction"]
    assert _para(intro, "i-1").text == "ASIA"
    listed = _doc("2025_2469_fin")
    parts = {p.part for p in listed.paragraphs}
    assert {"headnote", "heading", "text", "item"} == parts
    assert _para(listed, "li-1").text.startswith("Article 47(1) of the EU General Data Protection Regulation")
    assert _para(listed, "h-2").text == "Summary of the facts"
    eids = [p.eid for p in listed.paragraphs]
    assert len(eids) == len(set(eids))


def test_statutes_named_with_their_number_are_linked_by_pattern() -> None:
    gdpr = _doc("2019_481_fin")
    linked = [p for p in gdpr.paragraphs if p.refs]
    assert [(r.href, r.text, r.relation, r.extraction_method) for p in linked for r in p.refs] == [
        ("/fi/act/2018/1050", "(1050/2018)", "references", "pattern")
    ]
    old = _doc("2005_188_fin")
    assert [r.href for p in old.paragraphs for r in p.refs] == ["/fi/act/1999/523"]
    # Diary numbers and the GDPR's own number are not statute numbers.
    for name in FILES:
        for p in _doc(name).paragraphs:
            assert all(r.href.count("/") == 4 for r in p.refs)


def test_parser_refuses_what_it_cannot_read_safely() -> None:
    good = _raw("2019_481_fin")
    cases = [
        (b"<html>", "not well-formed"),
        (b"<!DOCTYPE x [<!ENTITY a 'b'>]><akomaNtoso/>", "not well-formed or unsafe"),
        (
            good.replace(b"data-protection-ombudsman-decision", b"chancellor-of-justice-decision"),
            "unsupported decision",
        ),
        (good.replace(b'language="fin"', b'language="eng"'), "language"),
        (re.sub(rb"<docTitle>.*?</docTitle>", b"<docTitle></docTitle>", good, flags=re.S), "docTitle"),
        (good.replace(b'name="dateIssued"', b'name="other"'), "dateIssued"),
    ]
    for content, message in cases:
        with pytest.raises(FinlexDecisionParseError, match=message):
            parse_finlex_decision(content)
    attached = good.replace(b"<judgmentBody>", b'<judgmentBody><decision><componentRef src="main.pdf"/></decision>')
    with pytest.raises(FinlexDecisionParseError, match="attached document"):
        parse_finlex_decision(attached)
    empty = (
        good[: good.index(b"<judgmentBody>")]
        + b"<judgmentBody><decision><p/></decision></judgmentBody></judgment></akomaNtoso>"
    )
    with pytest.raises(ParseSkipped, match="no text"):
        parse_finlex_decision(empty)


# --------------------------------------------------------------------------- citations, profile, source


def _citation(doc: ParsedDocument, paragraph: str | None = None) -> str:
    assert doc.judgment is not None
    return get_module("fi").judgment_citation(
        JudgmentCitationInput(
            doc.judgment.case_name,
            doc.judgment.neutral_citation,
            doc.judgment.decision_date,
            doc.judgment.docket,
            doc.judgment.court_id,
            "Tietosuojavaltuutettu / Dataombudsmannen (Data Protection Ombudsman)",
            paragraph,
            doc.language,
            "FI",
        )
    )


def test_decision_citations_in_both_languages() -> None:
    assert _citation(_doc("2005_188_fin")) == "Tietosuojavaltuutettu, päätös 26.7.2005, dnro 1164/41/2005"
    assert _citation(_doc("2005_188_swe")) == "Dataombudsmannen, beslut 26.7.2005, dnr 1164/41/2005"
    assert _citation(_doc("2025_2469_fin")).endswith("14.4.2025, dnro TSV/4995/2023")
    assert _citation(_doc("2005_188_fin"), "3").endswith("kohta 3")
    fi = get_module("fi")
    assert fi.validate_ext(_doc("2019_481_fin").ext)["diary_number"] == "28/523/2018"
    with pytest.raises(ValidationError):
        fi.validate_ext({"subtype": "x", "year": 2020, "number": "1", "unknown": 1})
    # Statute ext is unchanged.
    assert fi.validate_ext({"statute_number": "731/1999", "year": 1999, "number": "731"})["year"] == 1999


def test_profile_has_the_ombudsman_as_a_body_that_is_not_a_court(catalog: Catalog) -> None:
    reg = catalog.jurisdictions
    assert reg.court_jurisdiction("FI-TSV") == "FI"
    body = reg.courts()["FI-TSV"]
    assert body.kind == "administrative_body" and body.appeals_to == []
    assert reg.get("FI").profile_version == "0.2.0"
    assert reg.courts()["FI-KKO"].kind == "court"


def test_source_is_approved_and_uses_this_connector(catalog: Catalog) -> None:
    source = catalog.sources.get(SOURCE)
    licence_gate(source)
    assert source.licence.id == "CC-BY-4.0" and source.licence.attribution_required is True
    assert "Finlex open data" in (source.licence.attribution_text or "")
    assert source.languages == ["fi", "sv"] and source.document_kinds == ["judgment"]
    assert isinstance(build_connector(source), FinlexDecisionsConnector)
    manifest = yaml.safe_load((REPO_ROOT / source.access.endpoint).read_text())
    assert manifest["archive"] == ARCHIVE and manifest["languages"] == ["fin", "swe"]
    assert manifest["years"] is None and manifest["limit"] is None  # the committed manifest is the whole package
    assert manifest["crawl_delay_seconds"] >= 5 and "(" in manifest["user_agent"]
    # The sources that could not be used are recorded, not ingested.
    for planned in ("fi-kko-precedents", "ca-scc-decisions", "ie-courts-judgments", "dk-domsdatabasen"):
        assert catalog.sources.get(planned).status == "planned"


# --------------------------------------------------------------------------- connector


def _zip(names: list[str], extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name in names:
            z.writestr(_member(name), _raw(name))
        for member, content in (extra or {}).items():
            z.writestr(member, content)
    return buffer.getvalue()


def _write_cache(cache: Path, archive: bytes, robots: str | None = None) -> None:
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    meta = {"status": 200, "content_type": "application/zip", "retrieved_at": "2026-10-14T19:50:00+00:00"}
    path = http.cache_path(ARCHIVE_URL)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(archive)
    path.with_name(path.name + ".meta.json").write_text(
        json.dumps({**meta, "url": ARCHIVE_URL, "final_url": ARCHIVE_URL})
    )
    robots_path = http.cache_path(ROBOTS_URL, "robots.txt")
    robots_path.write_bytes((robots or "").encode())
    status = {"status": 404} if robots is None else {**meta, "status": 200, "content_type": "text/plain"}
    robots_path.with_name(robots_path.name + ".meta.json").write_text(json.dumps({"url": ROBOTS_URL, **status}))


def _manifest(tmp_path: Path, **extra: Any) -> Path:
    body = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": f"https://{HOST}",
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "archive": ARCHIVE,
        "languages": ["fin", "swe"],
        "years": None,
        "limit": None,
        **extra,
    }
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


def _connector(tmp_path: Path, cache: Path, **extra: Any) -> FinlexDecisionsConnector:
    return FinlexDecisionsConnector(_manifest(tmp_path, **extra), SOURCE, cache, offline=True)


def test_connector_offline_from_cache_yields_exact_members_oldest_first(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _write_cache(cache, _zip(FILES[::-1], {f"{BASE}/2005/188/fin@/media/x.pdf": b"%PDF", "other/readme.txt": b"x"}))
    docs = list(_connector(tmp_path, cache).fetch())
    assert [d.external_id for d in docs] == [
        "finlex-dpo:2005/188/fin",
        "finlex-dpo:2005/188/swe",
        "finlex-dpo:2019/481/fin",
        "finlex-dpo:2020/584/fin",
        "finlex-dpo:2025/2469/fin",
    ]
    assert docs[0].content == _raw("2005_188_fin")  # exact bytes as stored in the package
    assert docs[0].uri == f"{ARCHIVE_URL}#{_member('2005_188_fin')}" and docs[0].media_type == "application/xml"
    assert docs[0].retrieved_at.year == 2026 and docs[0].metadata["member"] == _member("2005_188_fin")
    # Slices: by language, year and count.
    assert [d.external_id for d in _connector(tmp_path, cache, languages=["swe"]).fetch()] == [
        "finlex-dpo:2005/188/swe"
    ]
    assert len(list(_connector(tmp_path, cache, years=[2019, 2020]).fetch())) == 2
    assert [d.external_id for d in _connector(tmp_path, cache, limit=1).fetch()] == ["finlex-dpo:2005/188/fin"]


def test_connector_refuses_bad_manifests(tmp_path: Path) -> None:
    for extra, message in (
        ({"base_url": "http://www.finlex.fi"}, "base_url"),
        ({"user_agent": "python-urllib"}, "user_agent"),
        ({"crawl_delay_seconds": 1}, "at least 5"),
        ({"archive": "files/open-data/archives/statute-consolidated.zip"}, "archive must be"),
        ({"archive": "files/open-data/archives/../x.zip"}, "archive must be"),
        ({"languages": []}, "languages"),
        ({"languages": ["eng"]}, "languages"),
        ({"years": "2020"}, "years"),
        ({"limit": "5"}, "limit"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(_connector(tmp_path, tmp_path, **extra).fetch())
    with pytest.raises(ManifestError, match="source_id"):
        list(_connector(tmp_path, tmp_path, source_id="other").fetch())


def test_connector_obeys_robots_size_limit_and_checks_each_member(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    _write_cache(blocked, _zip(FILES[:1]), robots="User-agent: *\nDisallow: /files/\n")
    with pytest.raises(FetchError, match="disallowed by"):
        list(_connector(tmp_path, blocked).fetch())
    allowed = tmp_path / "allowed"
    _write_cache(allowed, _zip(FILES[:1]), robots="User-agent: *\nDisallow: /_next/image\n")
    assert len(list(_connector(tmp_path, allowed).fetch())) == 1
    with pytest.raises(FetchError, match="exceeds max_archive_bytes"):
        list(_connector(tmp_path, allowed, max_archive_bytes=100).fetch())
    notzip = tmp_path / "notzip"
    _write_cache(notzip, b"<html>error</html>")
    with pytest.raises(FetchError, match="not a zip"):
        list(_connector(tmp_path, notzip).fetch())
    # A malformed member and a member that is another document are skipped and reported, the run goes on.
    mixed = tmp_path / "mixed"
    swapped = _raw("2019_481_fin")
    _write_cache(
        mixed,
        _zip(
            ["2005_188_fin"],
            {
                f"{BASE}/2010/5/fin@/main.xml": b"<html>Fel",
                f"{BASE}/2011/6/fin@/main.xml": swapped,  # the document of 2019/481
            },
        ),
    )
    connector = _connector(tmp_path, mixed)
    assert [d.external_id for d in connector.fetch()] == ["finlex-dpo:2005/188/fin"]
    assert len(connector.fetch_errors) == 2
    assert "not well-formed" in connector.fetch_errors[0] and "is not /akn/fi/judgment" in connector.fetch_errors[1]


# --------------------------------------------------------------------------- PostgreSQL


class _Decisions:
    def fetch(self) -> Iterator[RawDocument]:
        for name in FILES:
            yield RawDocument(
                f"finlex-dpo:{name}",
                f"{ARCHIVE_URL}#{_member(name)}",
                "application/xml",
                _raw(name),
                datetime(2026, 10, 14, 19, 0, tzinfo=UTC),
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


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="FI", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_ingest_report(decisions: IngestionReport) -> None:
    assert decisions.errors == [] and decisions.conflicts == []
    assert decisions.documents_new == 5 and decisions.judgments_created == 4  # 2005/188 has two expressions
    expected = sum(len(_doc(n).paragraphs) for n in FILES)
    assert decisions.paragraphs_created == decisions.passages_indexed == expected
    assert decisions.instruments_created == 0 and decisions.citation_links_created == 6


@pytest.mark.db
def test_search_finds_the_decision_with_citation_and_no_binding_authority(
    session: Session, catalog: Catalog, decisions: IngestionReport
) -> None:
    res = _search(session, catalog, q="jäljennösten saaminen sosiaalihuollon asiakastiedoista")
    hit = res["results"][0]
    assert hit["kind"] == "judgment_paragraph"
    assert (
        hit["text"] == "Jäljennösten saaminen sosiaalihuollon asiakastiedoista"
    )  # the title (headnote) of the decision
    assert hit["unit"]["eid"] == "t-1" and hit["unit"]["heading"] == "headnote"
    assert hit["citation"] == "Tietosuojavaltuutettu, päätös 8.8.2019, dnro 28/523/2018"
    assert hit["document"]["court_id"] == "FI-TSV" and hit["document"]["decision_date"] == "2019-08-08"
    assert hit["authority"]["category"] == "persuasive_precedent" and hit["authority"]["label"] == "Persuasive only"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"AUTHORITY", "SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED"} <= codes
    assert hit["source"]["attribution"].startswith("Source: Finlex open data")
    assert hit["version"]["authoritative"] is False

    swedish = _search(session, catalog, q="registrerades rätt till insyn via nätet")["results"][0]
    assert swedish["citation"] == "Dataombudsmannen, beslut 26.7.2005, dnr 1164/41/2005"
    assert swedish["version"]["language"] == "sv"


@pytest.mark.db
def test_judgments_links_and_reingest(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, decisions: IngestionReport
) -> None:
    with session_factory() as session:
        rows = {j.uri: j for j in session.scalars(select(m.Judgment).where(m.Judgment.court_id == "FI-TSV"))}
        parallel = rows[f"/{BASE}/2005/188"]
        versions = session.scalars(select(m.JudgmentVersion).where(m.JudgmentVersion.judgment_id == parallel.id)).all()
        assert sorted(v.language for v in versions) == ["fi", "sv"]
        assert (parallel.docket, parallel.neutral_citation, parallel.decision_date) == (
            "1164/41/2005",
            None,
            date(2005, 7, 26),
        )
        links = session.scalars(select(m.CitationLink).where(m.CitationLink.from_work_uri.like(f"/{BASE}/%"))).all()
        assert len(links) == 6 and {lk.to_uri for lk in links} == {"/fi/act/1999/523", "/fi/act/2018/1050"}
        assert all(lk.extraction_method == "pattern" and lk.relation == "references" for lk in links)
        assert all(lk.to_instrument_id is None for lk in links)  # the Acts are not ingested in this test
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Decisions())
        assert again.documents_unchanged == 5 and again.judgments_created == 0 and again.paragraphs_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == 5 and all(r["result"] == "pass" for r in results)
        session.rollback()
