"""United States Code (OLRC USLM XML, release point 119-120): parser, citations, connector, PostgreSQL.

Fixtures are three whole titles exactly as downloaded from uscode.house.gov on 2026-10-14 (see
tests/fixtures/uscode_house/README.md): title 1 and title 17 (positive law) and title 27 (not positive law).
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
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
from ulis.ingestion.pipeline import CONNECTORS, IngestionReport, Ingestor, resolve_uri
from ulis.jurisdictions.modules.base import ProvisionCitationInput
from ulis.jurisdictions.modules.us import UsModule
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.connectors.uscode_house import UscodeHouseConnector, parse_download_page, title_url
from ulis.sources.normalized import ParsedDocument
from ulis.sources.parsers import uslm
from ulis.sources.parsers.uslm import UslmParseError, parse_uslm, resolve_identifier
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

SOURCE = "us-uscode-house"
FIX = REPO_ROOT / "tests" / "fixtures" / "uscode_house"
SHA = {
    1: "6d7d505defd730c112e1adda3f332139e30777ca0138300c0f6d9f34a8ce9ed5",
    17: "aa970d45710822aef07460f1be02fbca9382bf1212929ff8d75b359dbb0f3e6c",
    27: "e47d81f74685659955939a813cb2308eaac4e1450e2f0dbf5cdf50ce0083b01b",
}
HOUSE = "http://xml.house.gov/schemas/uslm/1.0"


def _zip(n: int) -> bytes:
    return (FIX / f"xml_usc{n:02d}@119-120.zip").read_bytes()


def _doc(n: int) -> ParsedDocument:
    docs = parse_uslm(_zip(n))
    assert len(docs) == 1
    return docs[0]


def _by_eid(doc: ParsedDocument) -> dict[str, Any]:
    return {p.eid: p for p in doc.provisions}


def _xml(n: int) -> str:
    with zipfile.ZipFile(io.BytesIO(_zip(n))) as archive:
        return archive.read(f"usc{n:02d}.xml").decode("utf-8")


def test_fixtures_are_the_downloads() -> None:
    for n, sha in SHA.items():
        assert hashlib.sha256(_zip(n)).hexdigest() == sha


# ---------------------------------------------------------------- parser: positive-law titles


def test_title_one_is_a_positive_law_title_attested_at_the_release_point() -> None:
    d = _doc(1)
    assert (d.work_uri, d.expression_uri) == ("/us/usc/t1", "/us/usc/t1/en@2026-10-07/release-point-119-120")
    assert (d.instrument_type, d.number, d.short_title) == ("act", "1", "1 U.S.C.")
    assert d.title == ("General Provisions (Title 1, United States Code; positive-law title; release point 119-120)")
    assert d.validity_basis == "attested_at_edition" and d.consolidation_kind == "us_code_release_point"
    assert d.version_date == d.currency_date == date(2026, 10, 7) and d.work_date is None
    assert d.text_origin == "official_consolidation" and d.authoritative is False
    assert d.ext == {
        "usc_title": 1,
        "positive_law": True,
        "release_point": "119-120",
        "compiled_through": "Public Law 119-120",
    }
    assert all(p.status == "in_force" and p.valid_from is None and p.valid_to is None for p in d.provisions)


def test_section_text_matches_the_gpo_edition_and_source_credit_is_a_note() -> None:
    p = _by_eid(_doc(1))["sec_107"]
    # The same wording as the HTML of GovInfo's USCODE-2024-title1 (an independent copy of the 2024 edition).
    assert p.text == (
        "Enrolled bills and resolutions of either House of Congress shall be printed on parchment or paper of "
        "suitable quality as shall be determined by the Joint Committee on Printing."
    )
    assert p.heading == "Parchment or paper for printing enrolled bills or resolutions"
    assert p.note == "Source credit: (July 30, 1947, ch. 388, 61 Stat. 635.)"
    assert [(r.href, r.relation) for r in p.refs] == [
        ("/us/act/1947-07-30/ch388", "source_act"),
        ("/us/stat/61/635", "source_act"),
    ]
    assert "Stat." not in p.text


def test_copyright_title_structure_and_fair_use() -> None:
    d = _doc(17)
    by = _by_eid(d)
    s107 = by["sec_107"]
    assert s107.heading == "Limitations on exclusive rights: Fair use"
    assert s107.text.startswith("Notwithstanding the provisions of sections 106 and 106A, the fair use")
    assert s107.text.endswith("made upon consideration of all the above factors.")  # the continuation
    assert not s107.is_leaf and s107.has_own_content  # the chapeau is the section's own text
    para = by["sec_107__para_4"]
    assert para.num_path == ["107", "4"] and para.parent_eid == "sec_107" and para.is_leaf
    assert para.text == "the effect of the use upon the potential market for or value of the copyrighted work."
    assert para.context_heading == "Limitations on exclusive rights: Fair use"
    assert [r.href for r in s107.refs][:3] == ["/us/pl/94/553/tI/s101", "/us/stat/90/2546", "/us/pl/101/650/tVI/s607"]
    assert {r.relation for r in s107.refs} == {"source_act"}
    assert "Source credit: (Pub. L. 94–553, title I, § 101, Oct. 19, 1976, 90 Stat. 2546;" in (s107.note or "")


def test_repealed_entries_are_recorded_and_pointers_are_not_provisions() -> None:
    d = _doc(17)
    by = _by_eid(d)
    s509 = by["sec_509"]
    assert s509.status == "repealed" and s509.text == "" and not s509.indexable
    assert s509.heading is not None and s509.heading.startswith("Repealed. Pub. L. 110–403")
    assert (s509.note or "").startswith("Status in source: repealed. The Code prints: Repealed. Pub. L. 110–403")
    assert by["sec_114__subsec_i"].status == "repealed" and by["sec_114__subsec_i"].text == ""
    # "[§ 116A. Renumbered § 116]" is a pointer, not law text: 116 exists, 116A does not.
    assert "sec_116" in by and "sec_116A" not in by
    assert sum(p.status == "repealed" for p in d.provisions) == 3


def test_footnote_marks_do_not_enter_the_text_and_footnotes_become_notes() -> None:
    d = _doc(17)
    carriers = [p for p in d.provisions if "concessionnaire" in p.text and p.is_leaf]
    assert carriers
    p = carriers[0]
    assert "concessionnaire," in p.text and "concessionnaire,1" not in p.text and "concessionnaire, 1" not in p.text
    assert "Footnote: So in original. Probably should be “concessionaire”." in (p.note or "")


def test_non_positive_law_title_is_marked_and_placeholders_are_skipped() -> None:
    d = _doc(27)
    assert d.ext["positive_law"] is False and d.ext["usc_title"] == 27
    assert d.title == (
        "Intoxicating Liquors (Title 27, United States Code; not enacted as positive law - "
        "prima facie evidence only, 1 U.S.C. 204; release point 119-120)"
    )
    by = _by_eid(d)
    # omitted (43a, 43b ...) and transferred (63a-63d) entries carry no law text and are not provisions
    assert not any(e.startswith(("sec_43a", "sec_63a")) for e in by)
    assert any(p.status == "repealed" for p in d.provisions)
    assert by["sec_1_to_5"].status == "repealed" and by["sec_1_to_5"].num_path == ["1 to 5"]


def test_repeated_section_numbers_and_footnote_marks_in_numbers() -> None:
    """Modelled on 5 U.S.C. 3598, which the Code prints twice, each with a footnote mark in its number."""

    def section(extra: str, heading: str, note: str) -> str:
        return (
            f'<section identifier="/us/usc/t5/s3598"><num value="3598">§ 3598.'
            f'<ref class="footnoteRef" idref="fn1">1</ref><note type="footnote" id="fn1"><num>1</num> {note}</note>'
            f"</num><heading> {heading}</heading><content>{extra}</content></section>"
        )

    xml = (
        f'<uscDoc xmlns="{HOUSE}" xmlns:dc="http://purl.org/dc/elements/1.1/" identifier="/us/usc/t5">'
        "<meta><dc:title>Title 5</dc:title><dc:type>USCTitle</dc:type><docNumber>5</docNumber>"
        "<docPublicationName>Online@119-120</docPublicationName>"
        '<property role="is-positive-law">yes</property></meta>'
        '<main><title identifier="/us/usc/t5"><num value="5">Title 5—</num><heading>GOVERNMENT ORGANIZATION'
        " AND EMPLOYEES</heading>"
        + section("First text.", "First", "Another section 3598 is set out after this section.")
        + section("Second text.", "Second", "Another section 3598 is set out preceding this section.")
        + "</title></main></uscDoc>"
    )
    d = parse_uslm(xml.encode())[0]
    assert d.work_title == "Government Organization and Employees (Title 5, United States Code)"
    assert [p.eid for p in d.provisions] == ["sec_3598", "sec_3598_dup2"]  # not "sec_3598.1"
    assert d.provisions[0].num_path == ["3598"] and d.provisions[1].num_path == ["3598"]
    assert "Footnote: Another section 3598 is set out after this section." in (d.provisions[0].note or "")
    assert "Ambiguous pinpoint" in (d.provisions[1].note or "")


# ---------------------------------------------------------------- parser: refusals


def test_release_point_without_a_recorded_date_is_refused() -> None:
    xml = _xml(1).replace("Online@119-120", "Online@119-999")
    with pytest.raises(UslmParseError, match="no recorded date"):
        parse_uslm(xml.encode())


def test_appendices_wrong_archives_and_oversized_members_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UslmParseError, match="main title"):
        parse_uslm(_xml(1).replace("<dc:type>USCTitle</dc:type>", "<dc:type>USCTitleAppendix</dc:type>").encode())
    two = io.BytesIO()
    with zipfile.ZipFile(two, "w") as archive:
        archive.writestr("usc01.xml", _xml(1))
        archive.writestr("other.xml", "<x/>")
    with pytest.raises(UslmParseError, match="single usc"):
        parse_uslm(two.getvalue())
    with pytest.raises(UslmParseError, match="not a readable zip"):
        parse_uslm(b"PK\x03\x04 not really a zip")
    monkeypatch.setattr(uslm, "MAX_XML_BYTES", 1000)
    with pytest.raises(UslmParseError, match="larger than"):
        parse_uslm(_zip(1))
    bad = _xml(1).replace('identifier="/us/usc/t1"', 'identifier="/us/usc/t2"', 1)
    with pytest.raises(UslmParseError, match="identifier"):
        parse_uslm(bad.encode())
    dtd = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><uscDoc>&e;</uscDoc>'
    with pytest.raises(UslmParseError):
        parse_uslm(dtd.encode())


def test_existing_document_types_are_unchanged_by_the_code_extensions() -> None:
    """The Code-only rules (status attributes, source credits, footnote marks) do not touch other USLM types."""
    cfr = parse_uslm((REPO_ROOT / "tests" / "fixtures" / "uslm" / "CFR-2018-title27-vol3-uslm.xml").read_bytes())
    assert cfr and all(p.status == "in_force" for d in cfr for p in d.provisions)
    assert all(d.ext.get("usc_title") is None for d in cfr)


# ---------------------------------------------------------------- identifiers and citations


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("/us/usc/t18/s1030", ("/us/usc/t18", "sec_1030")),
        ("/us/usc/t18/s1030/a/2", ("/us/usc/t18", "sec_1030")),  # below the section: the section
        ("/us/usc/t17/s106A", ("/us/usc/t17", "sec_106A")),
        ("/us/usc/t1/ch1", ("/us/usc/t1", None)),
        ("/us/usc/t1", ("/us/usc/t1", None)),
    ],
)
def test_code_identifiers_resolve_to_title_and_section(uri: str, expected: tuple[str, str | None]) -> None:
    assert resolve_identifier(uri) == expected
    assert resolve_uri(uri) == expected


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _by_eid(doc)[eid]
    return UsModule().provision_citation(
        ProvisionCitationInput(
            instrument_title=doc.work_title,
            instrument_type=doc.instrument_type,
            jurisdiction_id="US",
            top_kind=p.top_kind,
            num_path=p.num_path,
            language="en",
            ext=doc.ext,
            work_date=doc.work_date,
            version_date=doc.version_date,
            eid=eid,
        )
    )


def test_code_citations_follow_the_bluebook_form() -> None:
    d17, d1 = _doc(17), _doc(1)
    assert _cite(d17, "sec_107") == "17 U.S.C. § 107"
    assert _cite(d17, "sec_107__para_4") == "17 U.S.C. § 107(4)"
    assert _cite(d17, "sec_106A") == "17 U.S.C. § 106A"
    assert _cite(d1, "sec_204__subsec_a") == "1 U.S.C. § 204(a)"
    # A section of another title, built the way the parser records it: 18 U.S.C. § 1030(a)(2)(C).
    ext = {"usc_title": 18, "positive_law": True, "release_point": "119-120"}
    c = ProvisionCitationInput("Crimes", "act", "US", "section", ["1030", "a", "2", "C"], "en", ext=ext)
    assert UsModule().provision_citation(c) == "18 U.S.C. § 1030(a)(2)(C)"
    assert UsModule().provision_citation(
        ProvisionCitationInput("Crimes", "act", "US", "section", [], "en", ext=ext)
    ) == ("18 U.S.C.")
    assert UsModule().validate_ext(ext) == ext


# ---------------------------------------------------------------- connector


ROBOTS = "User-agent: Slurp\nDisallow: /\n"


def _manifest(tmp_path: Path, numbers: list[int], **extra: Any) -> Path:
    manifest = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": "https://uscode.house.gov",
        "user_agent": "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)",
        "release_point": {"id": "119-120"},
        "titles": [{"number": n, **({"sha256": SHA[n]} if n in SHA else {})} for n in numbers],
    }
    manifest.update(extra)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return path


def _cache(tmp_path: Path, titles: list[int], robots: str = ROBOTS) -> Path:
    """A PoliteHttp cache as a previous online run leaves it, built from the fixtures."""
    cache = tmp_path / "cache"
    host = cache / "uscode.house.gov"
    host.mkdir(parents=True)
    (host / "robots.txt").write_text(robots)
    (host / "robots.txt.meta.json").write_text(
        json.dumps({"url": "https://uscode.house.gov/robots.txt", "status": 200})
    )
    for n in titles:
        url = title_url("119-120", n)
        target = host / url.removeprefix("https://uscode.house.gov/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_zip(n))
        meta = {"url": url, "final_url": url, "status": 200, "content_type": "application/x-zip-compressed"}
        target.with_name(target.name + ".meta.json").write_text(
            json.dumps({**meta, "retrieved_at": "2026-10-14T15:30:00+00:00"})
        )
    return cache


def test_title_urls_are_the_olrc_release_point_downloads() -> None:
    assert title_url("119-120", 17) == (
        "https://uscode.house.gov/download/releasepoints/us/pl/119/120/xml_usc17@119-120.zip"
    )
    for release, number in (("119", 1), ("119-120", 0), ("119-120", 55), ("../x", 1)):
        with pytest.raises(ManifestError):
            title_url(release, number)


def test_connector_offline_from_cache_yields_the_archives_as_received(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, [1, 17])
    connector = UscodeHouseConnector(manifest, SOURCE, _cache(tmp_path, [1, 17]), offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["uscode-house:usc01@119-120", "uscode-house:usc17@119-120"]
    assert [hashlib.sha256(d.content).hexdigest() for d in docs] == [SHA[1], SHA[17]]
    assert docs[0].media_type == "application/zip" and docs[0].retrieved_at.tzinfo is not None
    assert docs[1].uri == title_url("119-120", 17) and docs[1].metadata["title"] == 17
    assert connector.http is not None and connector.http.requests_made == 0


def test_connector_refuses_what_robots_txt_or_the_manifest_forbid(tmp_path: Path) -> None:
    uncached = UscodeHouseConnector(_manifest(tmp_path, [1, 17]), SOURCE, _cache(tmp_path, [1]), offline=True)
    with pytest.raises(FetchError, match="offline and not cached"):
        list(uncached.fetch())
    disallowed = _cache(tmp_path / "d", [1], robots="User-agent: *\nDisallow: /download/\n")
    with pytest.raises(FetchError, match="disallowed by"):
        list(UscodeHouseConnector(_manifest(tmp_path, [1]), SOURCE, disallowed, offline=True).fetch())
    pinned = _manifest(tmp_path, [1], titles=[{"number": 1, "sha256": "0" * 64}])
    with pytest.raises(ManifestError, match="sha256"):
        list(UscodeHouseConnector(pinned, SOURCE, _cache(tmp_path / "p", [1]), offline=True).fetch())
    for bad in (
        {"source_id": "other"},
        {"base_url": "https://example.org"},
        {"user_agent": "bot"},
        {"release_point": {"id": "x"}},
        {"titles": []},
        {"titles": [{"number": 1}, {"number": 1}]},
        {"titles": [{"number": 99}]},
    ):
        with pytest.raises(ManifestError):
            list(UscodeHouseConnector(_manifest(tmp_path, [1], **bad), SOURCE, tmp_path, offline=True).fetch())


def test_connector_refuses_a_response_that_is_not_a_zip(tmp_path: Path) -> None:
    cache = _cache(tmp_path, [1])
    (cache / "uscode.house.gov" / "download/releasepoints/us/pl/119/120/xml_usc01@119-120.zip").write_bytes(b"<html/>")
    with pytest.raises(FetchError, match="not a zip archive"):
        list(UscodeHouseConnector(_manifest(tmp_path, [1]), SOURCE, cache, offline=True).fetch())


def test_source_is_active_licence_approved_and_registered(catalog: Catalog) -> None:
    source = catalog.sources.get(SOURCE)
    assert source.status == "active" and source.licence.review_status == "approved"
    assert (source.connector, source.parser) == ("uscode_house", "uslm") and "uscode_house" in CONNECTORS
    assert source.authority == "official_non_authentic" and source.jurisdictions == ["US"]
    real = yaml.safe_load((REPO_ROOT / source.access.endpoint).read_text())
    assert real["base_url"] == "https://uscode.house.gov" and real["release_point"]["id"] == "119-120"
    assert real["release_point"]["current_through"] == "Public Law 119-120 (10/07/2026)"
    assert real["release_point"]["id"] in uslm.USC_RELEASE_POINT_DATES
    UscodeHouseConnector(REPO_ROOT / source.access.endpoint, SOURCE, Path("unused"))._manifest()


def test_committed_manifest_lists_every_main_title_with_its_pin_and_legal_status() -> None:
    """The whole open scope: titles 1-52 and 54 (53 is reserved), 27 of them positive law, every archive pinned."""
    real = yaml.safe_load((REPO_ROOT / "config" / "manifests" / "us-uscode-house.yaml").read_text())
    titles = real["titles"]
    assert [t["number"] for t in titles] == [*range(1, 53), 54]
    assert all(len(t["sha256"]) == 64 and set(t["sha256"]) <= set("0123456789abcdef") for t in titles)
    assert len({t["sha256"] for t in titles}) == 53 and all(t["name"] for t in titles)
    positive = [t["number"] for t in titles if t["positive_law"]]
    assert positive == [
        1,
        3,
        4,
        5,
        9,
        10,
        11,
        13,
        14,
        17,
        18,
        23,
        28,
        31,
        32,
        35,
        36,
        37,
        38,
        39,
        40,
        41,
        44,
        46,
        49,
        51,
        54,
    ]
    assert {26, 42} <= {t["number"] for t in titles if not t["positive_law"]}
    by_number = {t["number"]: t for t in titles}
    assert by_number[1]["sha256"] == SHA[1] and by_number[17]["sha256"] == SHA[17]
    assert by_number[27]["sha256"] == SHA[27]
    # the status in the manifest is the status the XML states, for every fixture
    for n in (1, 17, 27):
        assert _doc(n).ext["positive_law"] is by_number[n]["positive_law"]


def test_download_page_is_read_as_the_olrc_prints_it() -> None:
    """An excerpt of the real page: the asterisk marks positive law, the changed-title class and a style
    attribute do not hide a title, and the reserved title 53 and the appendix rows are not main titles."""
    page = parse_download_page((FIX / "download_page_excerpt.html").read_text(encoding="utf-8"))
    assert (page.release_point, page.current_through) == ("119-120", "Public Law 119-120 (10/07/2026)")
    assert page.titles == [
        {"number": 1, "name": "General Provisions", "positive_law": True},
        {"number": 2, "name": "The Congress", "positive_law": False},
        {"number": 5, "name": "Government Organization and Employees", "positive_law": True},
        {"number": 31, "name": "Money and Finance", "positive_law": True},
        {"number": 54, "name": "National Park Service and Related Programs", "positive_law": True},
    ]
    with pytest.raises(ManifestError, match="currency"):
        parse_download_page("<html></html>")


def test_a_missing_archive_is_reported_and_the_other_titles_are_still_fetched(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, [1, 17])
    cache = _cache(tmp_path, [17])
    url = title_url("119-120", 1)
    gone = cache / "uscode.house.gov" / url.removeprefix("https://uscode.house.gov/")
    gone.parent.mkdir(parents=True, exist_ok=True)
    gone.write_bytes(b"")
    gone.with_name(gone.name + ".meta.json").write_text(json.dumps({"url": url, "status": 404}))
    connector = UscodeHouseConnector(manifest, SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["uscode-house:usc17@119-120"]
    assert len(connector.fetch_errors) == 1 and "not found (404)" in connector.fetch_errors[0]


# ---------------------------------------------------------------- PostgreSQL


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for n in (1, 17, 27):
            yield RawDocument(
                f"uscode-house:usc{n:02d}@119-120",
                title_url("119-120", n),
                "application/zip",
                _zip(n),
                datetime(2026, 10, 14, 15, 30, tzinfo=UTC),
                {"title": n},
            )


@pytest.fixture(scope="module")
def usc(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> Iterator[tuple[Session, IngestionReport]]:
    """The three fixtures ingested in one transaction that is never committed, so the shared test database (and
    the US counts other tests make) is untouched."""
    session = session_factory()
    report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
    session.flush()
    try:
        yield session, report
    finally:
        session.rollback()
        session.close()


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="US", **kw), today=date(2026, 10, 14))


def _codes(item: dict[str, Any]) -> set[str]:
    return {lim["code"] for lim in item["limitations"]}


@pytest.mark.db
def test_ingestion_of_three_titles(usc: tuple[Session, IngestionReport]) -> None:
    session, report = usc
    assert report.errors == [] and report.conflicts == [] and report.documents_new == 3
    rows = session.execute(select(m.Instrument.uri, m.Instrument.ext).where(m.Instrument.uri.like("/us/usc/%")))
    assert {uri: ext["usc_title"] for uri, ext in rows} == {"/us/usc/t1": 1, "/us/usc/t17": 17, "/us/usc/t27": 27}
    assert report.citation_links_resolved > 0


@pytest.mark.db
def test_search_finds_fair_use_with_its_citation_and_limitations(
    usc: tuple[Session, IngestionReport], catalog: Catalog
) -> None:
    session, _ = usc
    res = _search(session, catalog, q="fair use of a copyrighted work criticism comment news reporting teaching")
    hit = next(h for h in res["results"] if h["document"]["uri"] == "/us/usc/t17")
    assert hit["citation"] == "17 U.S.C. § 107"
    assert hit["text"].startswith("Notwithstanding the provisions of sections 106 and 106A, the fair use")
    assert hit["version"]["consolidation"] == "us_code_release_point"
    assert hit["version"]["currency_date"] == "2026-10-07"
    assert {"ATTESTED_ONLY_AT", "AS_OF_AFTER_CURRENCY", "OFFICIAL_CONSOLIDATION_REPRODUCED", "PROFILE_UNVERIFIED"} <= (
        _codes(hit)
    )
    assert "REPEALED" not in _codes(hit)


@pytest.mark.db
def test_a_paragraph_is_cited_to_its_pinpoint(usc: tuple[Session, IngestionReport], catalog: Catalog) -> None:
    session, _ = usc
    res = _search(
        session, catalog, q="effect of the use upon the potential market for or value of the copyrighted work"
    )
    cites = [h["citation"] for h in res["results"] if h["document"]["uri"] == "/us/usc/t17"]
    assert "17 U.S.C. § 107(4)" in cites


@pytest.mark.db
def test_point_in_time_lookup_follows_the_source(usc: tuple[Session, IngestionReport], catalog: Catalog) -> None:
    """The source attests the text only as of its release date: no start date is invented for any day."""
    session, _ = usc
    current = provision_at(session, catalog, "/us/usc/t17/~sec_107", date(2026, 10, 14))
    assert current["status_on_date"] == "in_force_date_unverified"
    assert current["provision"]["citation"] == "17 U.S.C. § 107"
    assert current["version"]["valid_from"] is None and current["instrument"]["title"].startswith("Copyrights")
    assert {"ATTESTED_ONLY_AT", "AS_OF_AFTER_CURRENCY"} <= _codes(current)
    attested = next(lim for lim in current["limitations"] if lim["code"] == "ATTESTED_ONLY_AT")
    assert "in force on 2026-10-07 (us code release point)" in attested["message"]
    earlier = provision_at(session, catalog, "/us/usc/t17/~sec_107", date(2020, 1, 1))
    assert earlier["status_on_date"] == "in_force_date_unverified"
    assert "ATTESTED_ONLY_AT" in _codes(earlier) and "AS_OF_AFTER_CURRENCY" not in _codes(earlier)


@pytest.mark.db
def test_repealed_section_is_recorded_but_never_presented_as_in_force(
    usc: tuple[Session, IngestionReport], catalog: Catalog
) -> None:
    """The Code gives no repeal date, and the shared resolver selects repealed versions only by date, so the
    lookup reports no applicable version; the timeline still shows the repealed entry and the Code's own words."""
    session, _ = usc
    detail = provision_at(session, catalog, "/us/usc/t17/~sec_509", date(2026, 10, 14))
    assert detail["status_on_date"] == "no_version" and detail["version"] is None
    (entry,) = detail["timeline"]
    assert entry["status"] == "repealed" and entry["text"] == "" and entry["valid_from"] is None
    assert entry["note"].startswith("Status in source: repealed. The Code prints: Repealed. Pub. L. 110–403")
    session_hits = _search(session, catalog, q="seizure and forfeiture of copies", limit=20)["results"]
    assert all(h["provision"]["eid"] != "sec_509" for h in session_hits)


@pytest.mark.db
def test_documents_say_whether_the_title_is_positive_law(
    usc: tuple[Session, IngestionReport], catalog: Catalog
) -> None:
    session, _ = usc
    one = _search(session, catalog, q="Codes and Supplements as evidence of the laws of United States prima facie")
    hit = next(h for h in one["results"] if h["document"]["uri"] == "/us/usc/t1")
    assert hit["citation"] == "1 U.S.C. § 204" and "positive-law title" in hit["document"]["title"]
    liquor = _search(session, catalog, q="intoxicating liquor")
    hit27 = next(h for h in liquor["results"] if h["document"]["uri"] == "/us/usc/t27")
    assert hit27["citation"].startswith("27 U.S.C. § ")
    assert "not enacted as positive law - prima facie evidence only, 1 U.S.C. 204" in hit27["document"]["title"]
    # the legal status is also a limitation on the result itself, and only on non-positive-law titles
    assert "NOT_POSITIVE_LAW" in _codes(hit27) and "NOT_POSITIVE_LAW" not in _codes(hit)
    message = next(lim for lim in hit27["limitations"] if lim["code"] == "NOT_POSITIVE_LAW")["message"]
    assert "only prima facie evidence of the law (1 U.S.C. 204)" in message and "Statutes at Large prevails" in message
    detail = provision_at(session, catalog, hit27["unit"]["uri"], date(2026, 10, 14))
    assert "NOT_POSITIVE_LAW" in _codes(detail)


@pytest.mark.db
def test_code_cross_references_resolve_to_sections(usc: tuple[Session, IngestionReport]) -> None:
    session, _ = usc
    row = session.execute(
        select(m.CitationLink.to_uri, m.Provision.eid)
        .join(m.Provision, m.CitationLink.to_provision_id == m.Provision.id)
        .where(m.CitationLink.to_uri.like("/us/usc/t17/s%"))
        .limit(1)
    ).first()
    assert row is not None and row[1].startswith("sec_")


@pytest.mark.db
def test_stored_texts_rederive_from_the_downloaded_archives(
    usc: tuple[Session, IngestionReport], catalog: Catalog, store: LocalObjectStore
) -> None:
    session, _ = usc
    docs = session.scalars(select(m.SourceDocument).where(m.SourceDocument.source_id == SOURCE)).all()
    assert len(docs) == 3
    for doc in docs:
        result = verify_source_document(session, catalog, store, doc.id, checker="pytest")
        assert result["result"] == "pass" and result["checked_units"] > 0, result["problems"][:3]
