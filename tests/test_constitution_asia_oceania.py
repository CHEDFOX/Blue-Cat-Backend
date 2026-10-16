"""Deep constitutions sweep, Asia and Oceania: parser hints, committed sources and manifests, recorded refusals.

Fixtures are real: the Philippine and Fijian pages are the text lines of the publishers' PDFs (``pdftotext``) written
back into a small PDF by ``tests/constitution_pdf.py``; the Kazakh page is the publisher's HTML (the ``article``
element, cut after article 12); the Mongolian and Turkmen fixtures are the real text lines of the page and the PDF,
one paragraph per line.
"""

from __future__ import annotations

import html
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from constitution_pdf import make_pdf
from sqlalchemy.orm import Session
from test_constitution_text import _gz, _pages, bundle, parse, prov, text_bundle

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSERS, IngestionReport, Ingestor, build_connector
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.constitution_docs import ConstitutionDocsConnector
from ulis.sources.normalized import ParseSkipped
from ulis.sources.parsers.constitution_text import find_candidates, parse_constitution
from ulis.sources.registry import LicenceNotApproved, licence_gate
from ulis.storage.object_store import LocalObjectStore

FIX = REPO_ROOT / "tests" / "fixtures" / "constitution_text"
ACTIVE = [
    "kz-akorda-constitution",
    "mn-legalinfo-constitution",
    "tm-minjust-constitution",
    "ph-dbm-constitution",
    "vu-ombudsman-constitution",
    "ws-samoagovt-constitution",
    "mh-rmicourts-constitution",
    "fj-parliament-constitution",
    "bt-dlgdm-constitution",
    "tl-parlamento-constitution",
]
PLANNED = [
    "sg-eld-constitution",
    "in-legislative-constitution",
    "kr-ccourt-library-constitution",
    "kh-mfaic-constitution",
    "mv-mvlaw-constitution",
]
REJECTED = ["nu-gov-constitution", "pg-parliament-constitution"]


def hints(source_id: str, slug: str) -> dict[str, Any]:
    manifest = yaml.safe_load((REPO_ROOT / "config" / "manifests" / f"{source_id}.yaml").read_text(encoding="utf-8"))
    return next(d for d in manifest["documents"] if d["slug"] == slug)["structure"]


def lines(name: str) -> list[str]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def line_bundle(name: str, source_id: str, slug: str, **kw: Any) -> bytes:
    """A page with one paragraph per real text line, read with the manifest's hints of the document."""
    body = "<html><body>" + "".join(f"<p>{html.escape(x)}</p>" for x in lines(name)) + "</body></html>"
    return bundle(body.encode("utf-8"), "html", structure=hints(source_id, slug), **kw)


def cite(eid: str, kind: str, path: list[str], cc: str, title: str, language: str = "en") -> str:
    return get_module("constitution").provision_citation(
        ProvisionCitationInput(title, "constitution", cc, kind, path, language, eid=eid)
    )


# --------------------------------------------------------------------------- sections that restart in every article


def ph_doc() -> Any:
    return parse(
        bundle(
            make_pdf(_pages("ph-en-pages.json")),
            "pdf",
            cc="PH",
            structure=hints("ph-dbm-constitution", "ph-en"),
            title="The 1987 Constitution of the Republic of the Philippines",
            citation_name="Philippines Constitution",
        )
    )


def test_philippine_sections_are_numbered_within_their_article() -> None:
    doc = ph_doc()
    eids = [p.eid for p in doc.provisions if p.parent_eid is None]
    assert eids[0] == "preamble"
    assert "sec-ii_1" in eids and "sec-ii_28" in eids and "sec-iii_1" in eids and "sec-iii_2" in eids
    first = prov(doc, "sec-ii_1")
    assert first.text.startswith("The Philippines is a democratic and republican State.")
    assert first.context_heading and first.context_heading.startswith("ARTICLE II Declaration of Principles")
    assert prov(doc, "sec-iii_2").text.startswith(
        "The right of the people to be secure in their persons, houses, papers"
    )
    assert prov(doc, "sec-iii_2").context_heading and "Bill of Rights" in prov(doc, "sec-iii_2").context_heading
    # the second section of Article III is not mistaken for a continuation of Article II (the numbers restart)
    assert not any(p.eid == "sec-ii_29" for p in doc.provisions)
    assert doc.provisions[0].text.startswith("We, the sovereign Filipino people")


def test_philippine_citations_name_the_article_and_the_section() -> None:
    title = "Philippines Constitution"
    assert cite("sec-iii_2", "section", ["sec:iii_2"], "PH", title) == "Philippines Constitution art. III, s. 2"
    assert cite("sec-ix_c_2", "section", ["sec:ix_c_2"], "PH", title) == "Philippines Constitution art. IX-C, s. 2"
    assert (
        cite("sec-ii_12-par2", "section", ["sec:ii_12", "par:2"], "PH", title)
        == "Philippines Constitution art. II, s. 12(2)"
    )
    # unscoped documents keep their form
    assert cite("sec-33", "section", ["sec:33"], "NG", "Nigeria Constitution") == "Nigeria Constitution s. 33"


def test_scope_and_part_headings_are_recognised_apart_from_the_sections() -> None:
    text = [
        "ARTICLE IX",
        "Constitutional Commissions",
        "A. Common Provisions",
        "SECTION 1. First section of part A, with enough words.",
        "SECTION 2. Second section of part A, with enough words.",
        "B. The Civil Service Commission",
        "SECTION 1. First section of part B, with enough words.",
        "SECTION 2. Second section of part B, with enough words.",
        "SECTION 3. Third section of part B, with enough words.",
    ]
    import re

    found = find_candidates(
        text,
        "keyword",
        ["SECTION"],
        [],
        None,
        re.compile(r"^ARTICLE (?P<scope>[IVXLC]+)$"),
        re.compile(r"^(?P<part>[A-D])\. "),
    )
    assert [(c.scope, c.base, c.key) for c in found][:3] == [
        ("IX-A", 1, "ix-a_1"),
        ("IX-A", 2, "ix-a_2"),
        ("IX-B", 1, "ix-b_1"),
    ]
    assert found[0].scope_rank < found[2].scope_rank


def test_marshallese_sections_read_with_the_page_layout() -> None:
    doc = parse(
        bundle(
            make_pdf(_pages("mh-en-pages.json")),
            "pdf",
            cc="MH",
            structure=hints("mh-rmicourts-constitution", "mh-en"),
            title="Constitution of the Republic of the Marshall Islands",
            citation_name="Marshall Islands Constitution",
        )
    )
    tops = [p.eid for p in doc.provisions if p.parent_eid is None]
    assert tops[0] == "preamble" and tops[1:5] == ["sec-i_1", "sec-i_2", "sec-i_3", "sec-i_4"]
    assert "sec-ii_1" in tops and "sec-ii_4" in tops
    assert prov(doc, "sec-i_1").heading == "This Constitution to be Supreme Law"
    assert prov(doc, "sec-i_1-par1").text.startswith("This Constitution shall be the supreme law of the Republic")
    assert prov(doc, "sec-ii_1").context_heading == "ARTICLE II - BILL OF RIGHTS"
    # the running heads and page numbers of the PDF are not text
    assert not any("Page" in p.text.splitlines()[-1] for p in doc.provisions if p.text)
    assert cite("sec-ii_1-par2", "section", ["sec:ii_1", "par:2"], "MH", "Marshall Islands Constitution") == (
        "Marshall Islands Constitution art. II, s. 1(2)"
    )


# ----------------------------------------------------------------- "N.-(1)" headings and headings above the number


def test_fijian_gazette_headings_with_a_dash_and_clauses() -> None:
    doc = parse(
        bundle(
            make_pdf(_pages("fj-en-pages.json")),
            "pdf",
            cc="FJ",
            structure=hints("fj-parliament-constitution", "fj-en"),
            title="Constitution of the Republic of Fiji",
            citation_name="Fiji Constitution",
        )
    )
    assert prov(doc, "preamble").text.startswith("RECOGNISING the indigenous people or the iTaukei")
    assert prov(doc, "sec-1").text.startswith(
        "The Republic of Fiji is a sovereign democratic State founded on the values of"
    )
    two = prov(doc, "sec-2")
    assert two.is_leaf is False  # the clauses (1) ... (6) are its paragraphs
    assert prov(doc, "sec-2-par1").text == "This Constitution is the supreme law of the State."
    assert prov(doc, "sec-2-par5").text.startswith("This Constitution cannot be abrogated or suspended by any person")
    assert prov(doc, "sec-3").context_heading and prov(doc, "sec-3").context_heading.startswith("CHAPTER 1")
    assert cite("sec-2-par1", "section", ["sec:2", "par:1"], "FJ", "Fiji Constitution") == "Fiji Constitution s. 2(1)"


# --------------------------------------------------------------------------- ordinals spelled in words


def test_mongolian_articles_headed_by_ordinal_words_are_numbered_in_order() -> None:
    doc = parse(
        line_bundle(
            "mn-mn-lines.json",
            "mn-legalinfo-constitution",
            "mn-mn",
            cc="MN",
            language="mn",
            title="Монгол Улсын Үндсэн хууль (1992)",
            citation_name="Mongolia Constitution",
        )
    )
    arts = [p for p in doc.provisions if p.kind == "article"]
    assert [p.eid for p in arts][:3] == ["art-1", "art-2", "art-3"] and len(arts) >= 12
    assert arts[0].text.startswith("1.Монгол Улс бол тусгаар тогтносон, бүрэн эрхт, Бүгд найрамдах улс мөн.")
    assert arts[0].context_heading and arts[0].context_heading.startswith("НЭГДҮГЭЭР БҮЛЭГ")
    assert prov(doc, "preamble").heading == "Монголын ард түмэн бид:"
    # "Арван хоёрдугаар зүйл." (the twelfth) is the twelfth heading
    assert prov(doc, "art-12").text.startswith("1.Монгол Улсын тусгаар тогтнол, бүрэн эрхт байдлын бэлгэ тэмдэг")
    assert cite("art-12", "article", ["art:12"], "MN", "Mongolia Constitution", "mn") == "Mongolia Constitution art. 12"


def test_sequence_style_needs_its_heading_pattern() -> None:
    assert find_candidates(["Нэгдүгээр зүйл."], "sequence", [], [], None) == []


# ----------------------------------------------------------------- Turkmen (number first, ordinal suffix) and Kazakh


def test_turkmen_articles_with_ordinal_suffixes() -> None:
    doc = parse(
        line_bundle(
            "tm-tk-lines.json",
            "tm-minjust-constitution",
            "tm-tk",
            cc="TM",
            language="tk",
            title="Türkmenistanyň Konstitusiýasy",
            citation_name="Turkmenistan Constitution",
        )
    )
    arts = [p for p in doc.provisions if p.kind == "article"]
    assert [p.eid for p in arts][:3] == ["art-1", "art-2", "art-3"] and len(arts) >= 5
    assert arts[0].text.startswith("Türkmenistan demokratik, hukuk we dünýewi döwlet bolup,")
    assert arts[0].context_heading and arts[0].context_heading.startswith("I BÖLÜM.")
    assert prov(doc, "preamble").heading == "Biz, Türkmenistanyň halky,"


def test_kazakh_page_is_read_from_its_article_element_only() -> None:
    doc = parse(
        bundle(
            _gz("kz-ru.html.gz"),
            "html",
            cc="KZ",
            language="ru",
            structure=hints("kz-akorda-constitution", "kz-ru"),
            title="Конституция Республики Казахстан",
            citation_name="Kazakhstan Constitution",
        )
    )
    eids = [p.eid for p in doc.provisions if p.parent_eid is None]
    assert eids[:3] == ["preamble", "art-1", "art-2"] and "art-12" in eids
    assert prov(doc, "preamble").heading == "Мы, единый народ Казахстана,"
    assert prov(doc, "art-1").text.startswith(
        "Республика Казахстан – демократическое, светское, правовое и социальное государство"
    )
    assert prov(doc, "art-1").context_heading == "Раздел I Основы конституционного строя"
    # nothing of the site's menus or of the "back" link is stored
    assert not any(p.text == "Президент" or "Вернуться назад" in p.text for p in doc.provisions)
    assert (
        cite("art-2-par1", "article", ["art:2", "par:1"], "KZ", "Kazakhstan Constitution", "ru")
        == "Kazakhstan Constitution art. 2(1)"
    )


def test_a_page_of_plain_paragraphs_still_parses_like_before() -> None:
    doc = parse(
        text_bundle(
            [f"Article {n}" if k == 0 else f"Words of article {n}, long enough." for n in range(1, 8) for k in (0, 1)]
        )
    )
    assert [p.eid for p in doc.provisions] == [f"art-{n}" for n in range(1, 8)]


# --------------------------------------------------------------------------- committed sources and manifests


@pytest.mark.parametrize("source_id", ACTIVE)
def test_committed_source_manifest_and_profile_agree(catalog: Catalog, source_id: str) -> None:
    source = catalog.sources.get(source_id)
    assert (
        source.status == "active" and source.connector == "constitution_docs" and source.parser == "constitution_text"
    )
    assert source.document_kinds == ["constitution"] and source.licence.review_status == "approved"
    licence_gate(source, commercial=False)
    assert source.licence.attribution_required and "{retrieved_date}" in (source.licence.attribution_text or "")
    assert source.licence.reviewed_on == date(2026, 10, 16) and "not counsel" in (source.licence.reviewed_by or "")
    assert "robots.txt" in (source.licence.reviewed_by or "") and source.licence.id.endswith("-official-text-no-terms")
    assert source.licence.commercial_use == "unknown"
    with pytest.raises(LicenceNotApproved, match="commercial"):
        licence_gate(source, commercial=True)
    assert isinstance(build_connector(source), ConstitutionDocsConnector)
    manifest = ConstitutionDocsConnector(
        REPO_ROOT / source.access.endpoint, source_id, Path("/nonexistent")
    )._manifest()
    assert "ULIS-research-bot" in manifest["user_agent"] and manifest["crawl_delay_seconds"] >= 5
    countries = {d["jurisdiction"] for d in manifest["documents"]}
    assert countries == set(source.jurisdictions)
    assert {d["language"] for d in manifest["documents"]} == set(source.languages)
    for d in manifest["documents"]:
        assert d.get("authoritative") is False
        for event in d.get("events", []):
            date.fromisoformat(str(event["date"]))
    for country in countries:
        profile = catalog.jurisdictions.get(country)
        assert profile.verification.status == "draft_unverified" and "constitution" in profile.instrument_types
        assert profile.citation.module == "constitution" and source_id in profile.publication.official_sources
        if country != "BT":  # Bhutan's profile also covers the Acts of its second source
            assert profile.instrument_types == ["constitution"]
            assert "Only the constitution is loaded" in profile.coverage.known_gaps[0]


@pytest.mark.parametrize("source_id", PLANNED + REJECTED)
def test_refused_publisher_is_recorded_and_the_licence_gate_refuses_it(catalog: Catalog, source_id: str) -> None:
    source = catalog.sources.get(source_id)
    assert source.status == ("rejected" if source_id in REJECTED else "planned")
    assert source.licence.review_status in ("pending_review", "rejected")
    assert source.licence.reviewed_on == date(2026, 10, 16)
    assert "robots.txt" in (source.licence.reviewed_by or "")
    assert source.document_kinds == ["constitution"]
    assert source.connector is None and source.parser is None and source.licence.attribution_text is None
    with pytest.raises(LicenceNotApproved):
        licence_gate(source)
    assert source_id not in CONNECTORS and not any(source_id in name for name in PARSERS)


def test_recorded_refusals_quote_the_publishers_words(catalog: Catalog) -> None:
    said = {
        "nu-gov-constitution": "you must not otherwise use, modify, reproduce or distribute",
        "kr-ccourt-library-constitution": "Copyright ©2024 by the Constitutional Court of Korea",
        "kh-mfaic-constitution": "Request Rejected",
        "in-legislative-constitution": "answered HTTP 403",
        "sg-eld-constitution": "Copyright @ 2010 Government of Singapore",
        "mv-mvlaw-constitution": "answered HTTP 401",
        "pg-parliament-constitution": "Content may be reproduced for personal, non-commercial use",
    }
    for source_id, phrase in said.items():
        assert phrase in (catalog.sources.get(source_id).licence.reviewed_by or ""), source_id
    assert catalog.sources.get("nu-gov-constitution").licence.automated_processing == "not_allowed"


def test_kazakhstan_and_turkmenistan_are_active_now_and_the_earlier_planned_records_are_replaced(
    catalog: Catalog,
) -> None:
    for source_id in ("kz-akorda-constitution", "tm-minjust-constitution"):
        assert catalog.sources.get(source_id).status == "active"
    assert catalog.sources.get("kz-akorda-constitution").languages == ["ru", "kk"]


# ------------------------------------------------------------- other laws of a publisher: the Acts of Bhutan


def test_acts_of_bhutan_manifest_and_source(catalog: Catalog) -> None:
    source = catalog.sources.get("bt-judiciary-acts")
    assert source.status == "active" and source.document_kinds == ["act"] and source.jurisdictions == ["BT"]
    licence_gate(source, commercial=False)
    endpoint = REPO_ROOT / source.access.endpoint
    manifest = ConstitutionDocsConnector(endpoint, "bt-judiciary-acts", Path("/nonexistent"))._manifest()
    documents = manifest["documents"]
    assert (
        len(documents) > 70 and manifest["crawl_delay_seconds"] >= 5 and manifest["hosts"] == ["www.judiciary.gov.bt"]
    )
    assert len({d["slug"] for d in documents}) == len(documents)
    assert all(d["instrument_type"] == "act" and d["language"] == "en" and d["url"].endswith(".pdf") for d in documents)
    assert any(d["title"] == "Copyright Act of Bhutan 2001" for d in documents)
    profile = catalog.jurisdictions.get("BT")
    assert profile.instrument_types == ["constitution", "act"]
    assert "bt-judiciary-acts" in profile.publication.official_sources


def test_an_act_is_a_work_of_its_own_with_its_own_title_in_the_citation() -> None:
    doc = parse(
        bundle(
            make_pdf(_pages("bt-copyright-act-pages.json")),
            "pdf",
            cc="BT",
            slug="bt-copyright-act-of-bhutan-2001",
            instrument_type="act",
            structure={"page_numbers": True},
            title="Copyright Act of Bhutan 2001",
            citation_name="Copyright Act of Bhutan 2001",
        )
    )
    assert doc.instrument_type == "act" and doc.work_uri == "/bt/act/copyright-act-of-bhutan-2001"
    assert doc.short_title == "Copyright Act of Bhutan 2001" and doc.consolidation_kind == "act_text"
    assert any(p.eid == "sec-1" for p in doc.provisions)
    title = "Copyright Act of Bhutan 2001"
    assert cite("sec-3", "section", ["sec:3"], "BT", title) == "Copyright Act of Bhutan 2001 s. 3"
    plain = [f"Article {n}" if k == 0 else f"Words of article {n}, long enough." for n in range(1, 8) for k in (0, 1)]
    assert parse(text_bundle(plain)).work_uri == "/xx/constitution"


def test_a_pdf_whose_font_maps_letters_to_private_use_code_points_is_skipped() -> None:
    glyphs = "".join(chr(0xF002 + k % 40) for k in range(80))
    with pytest.raises(ParseSkipped, match="private-use"):
        parse_constitution(text_bundle([glyphs, glyphs, glyphs]))


# --------------------------------------------------------------------------- through the pipeline


class _Documents:
    def __init__(self, source_id: str, documents: list[tuple[str, bytes]]):
        self.source_id = source_id
        self.documents = documents

    def fetch(self) -> Any:
        for slug, content in self.documents:
            yield RawDocument(
                f"constitution:{slug}",
                f"https://example.org/{slug}",
                "application/json",
                content,
                datetime(2026, 10, 16, 9, 0, tzinfo=UTC),
                {},
            )


@pytest.fixture(scope="module")
def asia_ingested(
    session_factory: Any, catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> dict[str, Any]:
    ph = bundle(
        make_pdf(_pages("ph-en-pages.json")),
        "pdf",
        cc="PH",
        structure=hints("ph-dbm-constitution", "ph-en"),
        title="The 1987 Constitution of the Republic of the Philippines",
        citation_name="Philippines Constitution",
    )
    fj = bundle(
        make_pdf(_pages("fj-en-pages.json")),
        "pdf",
        cc="FJ",
        structure=hints("fj-parliament-constitution", "fj-en"),
        title="Constitution of the Republic of Fiji",
        citation_name="Fiji Constitution",
        edition_date="2013-09-07",
    )
    mn = line_bundle(
        "mn-mn-lines.json",
        "mn-legalinfo-constitution",
        "mn-mn",
        cc="MN",
        language="mn",
        title="Монгол Улсын Үндсэн хууль (1992)",
        citation_name="Mongolia Constitution",
    )
    reports = {}
    with session_factory() as session:
        for source_id, documents in {
            "ph-dbm-constitution": [("ph-en", ph)],
            "fj-parliament-constitution": [("fj-en", fj)],
            "mn-legalinfo-constitution": [("mn-mn", mn)],
        }.items():
            reports[source_id] = Ingestor(session, catalog, store).ingest_source(
                source_id, actor="pytest", connector=_Documents(source_id, documents)
            )
        session.commit()
    return reports


@pytest.mark.db
def test_asia_oceania_constitutions_are_ingested_and_searchable(
    session: Session, catalog: Catalog, asia_ingested: dict[str, Any]
) -> None:
    for source_id, report in asia_ingested.items():
        assert report.errors == [] and report.conflicts == [], source_id
        assert report.documents_new == 1 and report.passages_indexed > 10, source_id
    today = date(2026, 10, 16)
    ph = search(
        session,
        catalog,
        SearchRequest(
            jurisdiction="PH", q="right of the people to be secure against unreasonable searches and seizures"
        ),
        today=today,
    )
    hit = ph["results"][0]
    assert hit["citation"] == "Philippines Constitution art. III, s. 2"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"ATTESTED_ONLY_AT", "PROFILE_UNVERIFIED", "NOT_AUTHENTIC_TEXT"} <= codes
    assert "Department of Budget and Management" in hit["source"]["attribution"]
    fj = search(
        session,
        catalog,
        SearchRequest(jurisdiction="FJ", q="This Constitution is the supreme law of the State"),
        today=today,
    )
    assert fj["results"][0]["citation"] == "Fiji Constitution s. 2(1)"
    mn = search(
        session, catalog, SearchRequest(jurisdiction="MN", q="Монгол Улс бол тусгаар тогтносон бүрэн эрхт"), today=today
    )
    assert mn["results"][0]["citation"] == "Mongolia Constitution art. 1"
