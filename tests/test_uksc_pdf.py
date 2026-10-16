"""UK Supreme Court judgments published as PDF (2009 to 2020): parser, connector, committed manifest, search.

The fixtures in ``tests/fixtures/uksc_pdf`` are real: the Court's case pages (gzip-compressed HTML) and the judgment
PDFs of
``[2012] UKSC 61`` (Imperial Tobacco Limited v The Lord Advocate) and ``[2009] UKSC 5`` (In re B (A Child)), retrieved
2026-10-16
from ``https://supremecourt.uk``.
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
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.connectors.uksc_pdf import ROBOTS_URL, UkscPdfConnector, case_url
from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers import uksc_pdf
from ulis.sources.parsers.uksc_pdf import BUNDLE_SCHEMA, UkscPdfParseError, bundle_bytes, parse_uksc_pdf
from ulis.sources.registry import licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

SOURCE = "gb-uksc-judgments-pdf"
HOST = "supremecourt.uk"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
FIX = REPO_ROOT / "tests" / "fixtures" / "uksc_pdf"
TOBACCO, INREB = "2012_0066", "2009_0019"
ALL = [TOBACCO, INREB]


def _id(name: str) -> str:
    year, number = name.split("_")
    return f"UKSC/{year}/{number}"


def _page(name: str) -> bytes:
    return gzip.decompress((FIX / f"uksc_{name}.html.gz").read_bytes())


def _pdf(name: str) -> bytes:
    return (FIX / f"uksc_{name}_judgment.pdf").read_bytes()


def _bundle(name: str) -> bytes:
    return bundle_bytes(
        {
            "schema": BUNDLE_SCHEMA,
            "case_page_url": case_url(_id(name)),
            "case_page": _page(name).decode("utf-8"),
            "pdf_url": f"https://{HOST}/uploads/uksc_{name}_judgment_x.pdf",
            "pdf_base64": base64.b64encode(_pdf(name)).decode(),
        }
    )


def _doc(name: str) -> ParsedDocument:
    docs = parse_uksc_pdf(_bundle(name))
    assert len(docs) == 1
    return docs[0]


# --------------------------------------------------------------------------- parser


def test_metadata_comes_from_the_case_page_and_the_first_pdf_page() -> None:
    doc = _doc(TOBACCO)
    meta = doc.judgment
    assert meta is not None
    assert meta.court_id == "GB-UKSC" and meta.neutral_citation == "[2012] UKSC 61" and meta.docket == "UKSC/2012/0066"
    assert meta.case_name == "Imperial Tobacco Limited (Appellant) v The Lord Advocate (Respondent) (Scotland)"
    assert meta.decision_date == date(2012, 12, 12) and doc.version_date == date(2012, 12, 12)
    assert doc.work_uri == "/gb/uksc/2012/61" and doc.expression_uri == "/gb/uksc/2012/61/eng@2012-12-12"
    assert doc.authoritative is False and doc.text_origin == "official_conversion" and doc.jurisdiction_id == "GB"
    assert doc.ext["judgment_appealed"] == "[2012] CSIH 0009"
    assert doc.ext["justices"] == ["Lord Hope", "Lord Walker", "Lady Hale", "Lord Kerr", "Lord Sumption"]
    other = _doc(INREB)
    assert other.judgment is not None and other.judgment.neutral_citation == "[2009] UKSC 5"
    assert other.work_uri == "/gb/uksc/2009/5" and other.ext["judgment_appealed"] == "[2009] EWCA Civ 545"


def test_numbered_paragraphs_headings_and_front_matter() -> None:
    doc = _doc(TOBACCO)
    numbered = [p for p in doc.paragraphs if p.part == "considerations"]
    assert [p.num for p in numbered] == [str(i) for i in range(1, 47)]  # 1 to 46, continuous
    assert numbered[0].eid == "para-1" and numbered[-1].eid == "para-46"
    first = numbered[0].text
    assert first.startswith(
        "The question in this appeal is whether sections 1 and 9 of the Tobacco and Primary Medical Services"
    )
    assert "\n" not in first and "  " not in first  # lines joined into one paragraph
    assert numbered[-1].text.endswith("affirm the First Division’s interlocutor.")  # the page footer "Page 17" is gone
    assert not any(p.text.startswith("Page ") and p.text[5:].isdigit() for p in doc.paragraphs)
    headings = [p.text for p in doc.paragraphs if p.part == "heading"]
    assert headings[0] == "LORD HOPE (with whom Lord Walker, Lady Hale, Lord Kerr and Lord Sumption agree)"
    assert {"Background", "The interpretation issue", "Conclusion"} <= set(headings)
    front = [p for p in doc.paragraphs if p.part == "text"]
    assert front[0].text == "Michaelmas Term [2012] UKSC 61 On appeal from: [2012] CSIH 0009"
    assert doc.paragraphs[0].part == "text" and all(p.num is None for p in doc.paragraphs if p.part != "considerations")
    # Headings stay in reading order, between the paragraphs they separate.
    order = [(p.part, p.num or p.text) for p in doc.paragraphs]
    assert order.index(("heading", "Conclusion")) == order.index(("considerations", "46")) - 1
    assert [p.sort_order for p in doc.paragraphs] == list(range(len(doc.paragraphs)))
    assert len({p.eid for p in doc.paragraphs}) == len(doc.paragraphs)


def test_an_opinion_header_and_a_name_in_capitals_are_headings() -> None:
    doc = _doc(INREB)
    assert [p.text for p in doc.paragraphs if p.part == "heading"][0] == "LORD KERR"
    assert [p.num for p in doc.paragraphs if p.part == "considerations"][-1] == "43"
    assert any(p.text.startswith("Appellant (GB)") for p in doc.paragraphs)  # counsel, before paragraph 1


def test_a_judgment_that_cites_no_other_one_has_no_pattern_links() -> None:
    assert [r for p in _doc(INREB).paragraphs for r in p.refs] == []


def test_parser_refuses_what_it_cannot_read(monkeypatch: pytest.MonkeyPatch) -> None:
    good = json.loads(_bundle(TOBACCO))
    for change, message in (
        ({"schema": "other"}, "unexpected bundle schema"),
        ({"pdf_base64": "not base64!"}, "incomplete bundle"),
        ({"pdf_base64": base64.b64encode(b"%PDF-1.4 not really").decode()}, "cannot be read"),
        ({"case_page": good["case_page"].replace("UKSC/2012/0066", "UKSC/12/66")}, "no case id"),
        ({"case_page": good["case_page"].replace("[2012] UKSC 61", "[2012] EWCA 61")}, "neutral citation"),
        ({"case_page": good["case_page"].replace('id="judgment-date"', 'id="x"')}, "judgment date"),
    ):
        with pytest.raises(UkscPdfParseError, match=message):
            parse_uksc_pdf(bundle_bytes({**good, **change}))
    with pytest.raises(UkscPdfParseError, match="not a ULIS bundle"):
        parse_uksc_pdf(b"<html></html>")
    # A PDF without a text layer (a scan) is skipped, not an error; a page of text with no numbered paragraph is
    # an error.
    monkeypatch.setattr(uksc_pdf, "pdf_pages", lambda _pdf: ["", "Page 2"])
    with pytest.raises(ParseSkipped, match="no text layer"):
        parse_uksc_pdf(_bundle(TOBACCO))
    monkeypatch.setattr(uksc_pdf, "pdf_pages", lambda _pdf: ["The text of a judgment. " * 40])
    with pytest.raises(UkscPdfParseError, match="no numbered paragraphs"):
        parse_uksc_pdf(_bundle(TOBACCO))


def test_numbers_must_continue_the_sequence_or_restart_before_a_2(monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        "Cover page text of the judgment, long enough to count as text.\n\n"
        "LORD ONE\n1.\nFirst paragraph of the first opinion that mentions [2010] UKSC 3 and goes on for some words.\n"
        "5.\nA quoted clause numbered 5 does not continue the sequence.\n2.\nSecond paragraph.\nPage 3\n"
        "LORD TWO\n1.\nFirst paragraph of the second opinion.\n2.\nSecond paragraph of the second opinion, which is\n"
        "wrapped over two lines with a compound non-\nresident word.\n"
    )
    monkeypatch.setattr(uksc_pdf, "pdf_pages", lambda _pdf: [body + "x" * 400])
    doc = parse_uksc_pdf(_bundle(TOBACCO))[0]
    got = [(p.part, p.num, p.eid) for p in doc.paragraphs if p.part == "considerations"]
    assert got == [
        ("considerations", "1", "para-1"),
        ("considerations", "2", "para-2"),
        ("considerations", "1", "para-1-2"),
        ("considerations", "2", "para-2-2"),
    ]
    first = next(p for p in doc.paragraphs if p.eid == "para-1")
    assert "5. A quoted clause numbered 5" in first.text
    assert [(r.href, r.text, r.extraction_method) for r in first.refs] == [
        ("/gb/uksc/2010/3", "[2010] UKSC 3", "pattern")
    ]
    second = next(p for p in doc.paragraphs if p.eid == "para-2-2")
    assert "compound non-resident word." in second.text  # a hyphen at the end of a line is joined without a space
    assert [p.text for p in doc.paragraphs if p.part == "heading"] == ["LORD ONE", "LORD TWO"]


# --------------------------------------------------------------------------- source, manifest


def test_source_licence_and_committed_manifest(catalog: Catalog, tmp_path: Path) -> None:
    source = catalog.sources.get(SOURCE)
    licence_gate(source)
    assert source.licence.id == "OGL-UK-3.0" and source.licence.attribution_required is True
    assert (source.licence.attribution_text or "").startswith("Contains public sector information licensed under")
    assert isinstance(build_connector(source), UkscPdfConnector)
    manifest = UkscPdfConnector(REPO_ROOT / source.access.endpoint, SOURCE, tmp_path)._manifest()  # noqa: SLF001
    cases = manifest["cases"]
    assert len(cases) == len(set(cases)) == len(cases) and cases == sorted(cases) and len(cases) > 1300
    assert cases[0].startswith("UKSC/2009/") and _id(TOBACCO) in cases and _id(INREB) in cases
    # The judgments that the Court publishes as HTML (the other source) are not repeated here.
    html = yaml.safe_load((REPO_ROOT / "config" / "manifests" / "gb-uksc-judgments.yaml").read_text())["cases"]
    assert set(html).isdisjoint(cases)
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


def _cache_case(cache: Path, name: str, page: bytes | None = None, pdf: bytes | None = None) -> str:
    """Cache a case page and its PDF the way PoliteHttp keeps them; returns the PDF URL."""
    page = page if page is not None else _page(name)
    link = f"https://{HOST}/uploads/uksc_{name}_judgment_abc123.pdf"
    _cache(cache, case_url(_id(name)), f"cases/uksc-{name.replace('_', '-')}.html", page, "text/html; charset=utf-8")
    if pdf is not None:
        _cache(cache, link, "uploads/" + link.rsplit("/", 1)[1], pdf, "application/pdf")
    return link


def _cache_robots(cache: Path, robots: str | None) -> None:
    http = PoliteHttp(cache, UA, allowed_hosts={HOST})
    path = http.cache_path(ROBOTS_URL, "robots.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((robots or "").encode())
    path.with_name(path.name + ".meta.json").write_text(
        json.dumps({"url": ROBOTS_URL, "status": 404 if robots is None else 200})
    )


def _with_pdf_link(page: bytes, name: str) -> bytes:
    """The fixture page links its real PDF (a hashed name); the cache holds it under a name of our choosing."""
    import re

    return re.sub(
        rb'(id="judgment-pdf"[^>]*href=")[^"]+(")',
        rb"\1https://supremecourt.uk/uploads/uksc_" + name.encode() + rb"_judgment_abc123.pdf\2",
        page,
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


def test_connector_offline_yields_page_and_pdf_exactly_as_received(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_robots(cache, "User-agent: *\nDisallow: /contact-us/book-room-hearing\n")
    for name in ALL:
        _cache_case(cache, name, _with_pdf_link(_page(name), name), _pdf(name))
    connector = UkscPdfConnector(_manifest(tmp_path, [_id(INREB), _id(TOBACCO)]), SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [f"uksc-pdf:{_id(INREB)}", f"uksc-pdf:{_id(TOBACCO)}"]
    bundle = json.loads(docs[0].content)
    assert bundle["schema"] == BUNDLE_SCHEMA and base64.b64decode(bundle["pdf_base64"]) == _pdf(INREB)
    assert bundle["case_page"].encode() == _with_pdf_link(_page(INREB), INREB)  # exact page text
    assert docs[0].media_type == "application/json" and docs[0].uri.endswith("uksc_2009_0019_judgment_abc123.pdf")
    assert docs[0].retrieved_at.year == 2026 and connector.fetch_errors == [] and connector.not_yet_judged == []
    # The bundle is what the parser reads.
    assert parse_uksc_pdf(docs[1].content)[0].judgment.neutral_citation == "[2012] UKSC 61"  # type: ignore[union-attr]
    with pytest.raises(FetchError, match="offline and not cached"):
        list(UkscPdfConnector(_manifest(tmp_path, ["UKSC/2013/0001"]), SOURCE, cache, offline=True).fetch())


def test_connector_skips_appeals_without_judgment_joined_appeals_and_bad_pages(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache_robots(cache, None)
    pending = (
        _page(TOBACCO)
        .replace(b'id="neutral-citation-number"', b'id="neutral-citation-x"')
        .replace(b"UKSC/2012/0066", b"UKSC/2012/0001")
    )
    _cache_case(cache, "2012_0001", pending)
    _cache_case(cache, TOBACCO, _with_pdf_link(_page(TOBACCO), TOBACCO), _pdf(TOBACCO))
    joined = _with_pdf_link(_page(TOBACCO), TOBACCO).replace(b"UKSC/2012/0066", b"UKSC/2012/0067")
    _cache_case(cache, "2012_0067", joined)
    _cache_case(cache, "2012_0068", b"{}")  # not the case page
    other = _with_pdf_link(_page(INREB), "2012_0069").replace(b"UKSC/2009/0019", b"UKSC/2012/0069")
    _cache_case(cache, "2012_0069", other, b"<html>not a pdf</html>")
    cases = [_id(n) for n in ("2012_0001", TOBACCO, "2012_0067", "2012_0068", "2012_0069")]
    connector = UkscPdfConnector(_manifest(tmp_path, cases), SOURCE, cache, offline=True)
    assert [d.external_id for d in connector.fetch()] == [f"uksc-pdf:{_id(TOBACCO)}"]
    assert connector.not_yet_judged == ["UKSC/2012/0001"]
    assert len(connector.fetch_errors) == 3
    assert "the same judgment ([2012] UKSC 61) as UKSC/2012/0066" in connector.fetch_errors[0]
    assert "not the case page of UKSC/2012/0068" in connector.fetch_errors[1]
    assert "not a PDF" in connector.fetch_errors[2]


def test_connector_refuses_bad_manifests_and_obeys_robots_txt(tmp_path: Path) -> None:
    for cases, extra, message in (
        (["UKSC/22/124"], {}, "invalid case id"),
        ([_id(TOBACCO), _id(TOBACCO)], {}, "listed twice"),
        ([], {}, "no cases"),
        ([_id(TOBACCO)], {"base_url": "http://supremecourt.uk"}, "base_url"),
        ([_id(TOBACCO)], {"user_agent": "python-urllib"}, "user_agent"),
        ([_id(TOBACCO)], {"crawl_delay_seconds": 1}, "at least 5"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(UkscPdfConnector(_manifest(tmp_path, cases, **extra), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="source_id"):
        list(UkscPdfConnector(_manifest(tmp_path, [_id(TOBACCO)], source_id="other"), SOURCE, tmp_path).fetch())
    blocked = tmp_path / "blocked"
    _cache_robots(blocked, "User-agent: *\nDisallow: /cases/\n")
    _cache_case(blocked, TOBACCO, _with_pdf_link(_page(TOBACCO), TOBACCO), _pdf(TOBACCO))
    with pytest.raises(FetchError, match="disallowed by"):
        list(UkscPdfConnector(_manifest(tmp_path, [_id(TOBACCO)]), SOURCE, blocked, offline=True).fetch())
    pdfs_blocked = tmp_path / "pdfs-blocked"
    _cache_robots(pdfs_blocked, "User-agent: *\nDisallow: /uploads/\n")
    _cache_case(pdfs_blocked, TOBACCO, _with_pdf_link(_page(TOBACCO), TOBACCO), _pdf(TOBACCO))
    with pytest.raises(FetchError, match="uploads"):
        list(UkscPdfConnector(_manifest(tmp_path, [_id(TOBACCO)]), SOURCE, pdfs_blocked, offline=True).fetch())


# --------------------------------------------------------------------------- PostgreSQL


class _Judgments:
    def fetch(self) -> Iterator[RawDocument]:
        for name in ALL:
            yield RawDocument(
                f"uksc-pdf:{_id(name)}",
                f"https://{HOST}/uploads/uksc_{name}_judgment_x.pdf",
                "application/json",
                _bundle(name),
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
    assert judgments.paragraphs_created == judgments.passages_indexed == sum(len(_doc(n).paragraphs) for n in ALL)


@pytest.mark.db
def test_search_returns_the_numbered_paragraph_with_the_oscola_citation(
    session: Session, catalog: Catalog, judgments: IngestionReport
) -> None:
    res = search(
        session,
        catalog,
        SearchRequest(
            jurisdiction="GB",
            q="provision is outside that competence so far as it relates to reserved matters",
        ),
        today=date(2026, 10, 16),
    )
    hit = next(r for r in res["results"] if r["kind"] == "judgment_paragraph" and "Imperial Tobacco" in r["citation"])
    assert hit["citation"].startswith(
        "Imperial Tobacco Limited (Appellant) v The Lord Advocate (Respondent) (Scotland) [2012] UKSC 61 ["
    )
    assert hit["unit"]["heading"] == "considerations" and hit["unit"]["eid"].startswith("para-")
    assert (hit["document"]["court_id"], hit["document"]["decision_date"]) == ("GB-UKSC", "2012-12-12")
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"AUTHORITY", "SOURCE_NOT_AUTHENTIC", "PROFILE_UNVERIFIED"} <= codes
    assert hit["source"]["attribution"].startswith("Contains public sector information licensed under")
    assert hit["version"]["authoritative"] is False


@pytest.mark.db
def test_reingest_and_verification(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, judgments: IngestionReport
) -> None:
    from sqlalchemy import select

    from ulis.db import models as m

    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Judgments())
        assert again.documents_unchanged == 2 and again.judgments_created == 0 and again.paragraphs_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == 2 and all(r["result"] == "pass" for r in results)
        session.rollback()
