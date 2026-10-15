"""GovInfo bulk statutes (Statutes at Large, public and private laws, compilations): parser, connector, PostgreSQL.

Fixtures are real laws cut byte for byte out of the volumes downloaded from
https://www.govinfo.gov/bulkdata/STATUTE/ on 2026-10-15 (see tests/fixtures/govinfo_statutes/README.md).
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
from ulis.sources.connectors.govinfo_bulk import GovInfoStatutesConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers.uslm import UslmParseError, parse_uslm, resolve_identifier
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

SOURCE = "us-govinfo-bulk-statutes"
FIX = REPO_ROOT / "tests" / "fixtures" / "govinfo_statutes"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"


def _read(vol: int) -> bytes:
    return (FIX / f"STATUTE-{vol}-sample.xml").read_bytes()


def _docs(vol: int) -> dict[str, ParsedDocument]:
    return {d.work_uri: d for d in parse_uslm(_read(vol))}


# ---------------------------------------------------------------------------------------------- parser


def test_modern_law_is_a_public_law_with_its_short_title_and_enactment_date() -> None:
    doc = _docs(138)["/us/pl/118/214"]
    assert (
        doc.title
        == "U.S. Congressman Sam Johnson Memorial VA Clinic Act (Public Law 118-214, 138 Stat. 2805, as enacted)"
    )
    assert (doc.work_title, doc.short_title, doc.number) == (
        "U.S. Congressman Sam Johnson Memorial VA Clinic Act",
        "U.S. Congressman Sam Johnson Memorial VA Clinic Act",
        "118-214",
    )
    assert doc.expression_uri == "/us/pl/118/214/en@2025-01-02/stat-138"
    assert (doc.work_date, doc.version_date, doc.currency_date) == (date(2025, 1, 2), date(2025, 1, 2), None)
    assert doc.validity_basis == "enactment_date_presumed" and doc.consolidation_kind == "as_enacted"
    assert (doc.text_origin, doc.authoritative) == ("official_conversion", False)
    assert doc.ext["statutes_at_large"] == ["138 Stat. 2805"] and doc.ext["public_private"] == "public"
    assert doc.ext["official_title"].startswith("To name the Department of Veterans Affairs community-based")
    sec1, sec2 = doc.provisions[:2]
    assert (sec1.eid, sec1.heading, sec1.valid_from) == ("sec_1", "SHORT TITLE.", date(2025, 1, 2))
    assert sec1.text == "This Act may be cited as the “U.S. Congressman Sam Johnson Memorial VA Clinic Act”."
    assert sec2.text.startswith("The Department of Veterans Affairs community-based outpatient clinic in Plano, Texas")
    assert [e.event_type for e in doc.events] == ["enactment"]


def test_an_act_that_amends_another_act_does_not_take_its_title_from_the_amended_act() -> None:
    doc = _docs(138)["/us/pl/118/250"]
    assert doc.short_title is None and doc.work_title == "Public Law 118-250"
    assert doc.title.startswith(
        "Public Law 118-250 (138 Stat. 2939, as enacted): To amend title 28, United States Code"
    )


def test_laws_from_the_57th_congress_are_found_by_the_number_in_the_margin_note() -> None:
    public = _docs(32)["/us/pl/57/1"]
    assert (public.number, public.ext["chapter"], public.work_date) == ("57-1", "1", date(1901, 12, 14))
    assert public.provisions[0].eid == "sec_unnumbered_1"  # a short act without a numbered section
    assert public.provisions[0].text.startswith("That the Industrial Commission authorized by")
    assert "An Act authorizing" not in public.title  # the quoted title of an earlier Act is not this Act's name
    private = _docs(32)["/us/pvtl/57/944"]
    assert private.ext["public_private"] == "private" and private.title.startswith("Private Law 57-944 (32 Stat. 1449")
    assert resolve_identifier("/us/pvtl/57/944/s1") == ("/us/pvtl/57/944", "sec_1")


def test_a_law_of_the_72nd_volume_is_a_public_law_by_its_citation() -> None:
    doc = _docs(72)["/us/pl/85/317"]
    assert doc.ext["statutes_at_large"] == ["72 Stat. 706"] and doc.version_date == date(1958, 2, 4)
    assert doc.provisions[0].text.startswith("That the President of the United States is authorized and requested")


def test_acts_before_1901_are_named_by_date_and_chapter_and_found_by_their_first_page() -> None:
    docs = _docs(1)
    first = docs["/us/stat/1/23"]
    assert first.title == (
        "Act of June 1, 1789, ch. I (1 Stat. 23, as enacted): "
        "to regulate the Time and Manner of administering certain Oaths."
    )
    assert (first.number, first.ext["chapter"], first.ext["congress"], first.ext["session"]) == ("I", "I", 1, 1)
    assert first.provisions[0].text.startswith(
        "Be it enacted by the Senate and [House of] Representatives of the United States"
    )
    # two acts begin on page 418: the second keeps the page with a counter
    assert {"/us/stat/1/418", "/us/stat/1/418-2"} <= set(docs)
    assert docs["/us/stat/1/418-2"].expression_uri == "/us/stat/1/418-2/en@1795-02-21/stat-1"
    # an act without sections is one unnumbered provision made of its enacting formula and text
    assert [p.eid for p in docs["/us/stat/1/573"].provisions] == ["sec_unnumbered_1"]


def test_margin_notes_and_page_marks_are_not_law_text() -> None:
    text = " ".join(p.text for d in parse_uslm(_read(1)) for p in d.provisions)
    assert "Form of the oath or affirmation to support the Constitution" not in text  # a sidenote of 1 Stat. 23
    assert "oath or affirmation required by the sixth article" in text


def test_volume_without_laws_or_identity_is_refused_or_skipped() -> None:
    empty = (
        b'<statutesAtLarge xmlns="http://schemas.gpo.gov/xml/uslm"><meta><volume>9</volume></meta></statutesAtLarge>'
    )
    with pytest.raises(ParseSkipped):
        parse_uslm(empty)
    with pytest.raises(UslmParseError, match="volume number"):
        parse_uslm(b'<statutesAtLarge xmlns="http://schemas.gpo.gov/xml/uslm"><meta></meta></statutesAtLarge>')


def _synthetic(meta: str, preface: str = "") -> str:
    return (
        f"<component><pLaw><meta>{meta}</meta>{preface}<main><longTitle><officialTitle>To test.</officialTitle>"
        '</longTitle><section><num value="1">SECTION 1.</num><heading>TEST.</heading>'
        "<content>That this is the text.</content></section></main></pLaw></component>"
    )


def test_the_variants_of_older_and_newer_volumes_are_read_not_skipped() -> None:
    """Found by parsing a sample of the 138 volumes: the same citation written in other ways, no Statutes at Large
    citation at all, and a date the converter could not read (0001-01-01), which the running head still states."""
    laws = [
        _synthetic(
            "<citableAs>Public Law 104\u2013263</citableAs><citableAs>110 STAT. 3212</citableAs>"
            "<approvedDate>1996-10-09</approvedDate><congress>104</congress><publicPrivate>public</publicPrivate>"
        ),
        _synthetic(
            '<citableAs>Public Law 352</citableAs><dc:date xmlns:dc="http://purl.org/dc/elements/1.1/">1952-05-21</dc:date>'
            "<congress>82</congress><publicPrivate>public</publicPrivate>"
        ),
        _synthetic(
            "<citableAs>Public Law 109\u2013368</citableAs><citableAs>120 Stat. 2641</citableAs>"
            "<approvedDate>0001-01-01</approvedDate><congress>109</congress><publicPrivate>public</publicPrivate>",
            "<preface><centerRunningHead>PUBLIC LAW 109\u2013368\u2014NOV. 17, 2006</centerRunningHead></preface>",
        ),
        _synthetic(
            '<citableAs>2 Stat. 329.</citableAs><dc:date xmlns:dc="http://purl.org/dc/elements/1.1/">1805-03-02</dc:date>'
            "<congress>8</congress><publicPrivate>public</publicPrivate>"
        ),
        _synthetic("<citableAs>Public Law 99\u20131</citableAs><congress>99</congress>"),  # no date anywhere: skipped
        _synthetic("<approvedDate>1952-01-01</approvedDate><congress>82</congress>"),  # nothing to name it by: skipped
    ]
    xml = (
        '<statutesAtLarge xmlns="http://schemas.gpo.gov/xml/uslm"><meta><volume>9</volume></meta><main><collection>'
        + "".join(laws)
        + "</collection></main></statutesAtLarge>"
    )
    docs = {d.work_uri: d for d in parse_uslm(xml.encode())}
    assert sorted(docs) == ["/us/pl/104/263", "/us/pl/109/368", "/us/pl/82/352", "/us/stat/2/329"]
    assert docs["/us/pl/104/263"].ext["statutes_at_large"] == ["110 Stat. 3212"]  # written 110 STAT. 3212
    assert docs["/us/pl/82/352"].ext["statutes_at_large"] == [] and docs["/us/pl/82/352"].title.startswith(
        "Public Law 82-352 (as enacted)"
    )
    assert docs["/us/pl/109/368"].version_date == date(2006, 11, 17)  # from the running head, not the sentinel
    assert docs["/us/stat/2/329"].ext["statutes_at_large"] == ["2 Stat. 329"]


def test_every_fixture_law_has_a_unique_work_and_expression() -> None:
    for vol in (1, 32, 72, 138):
        docs = parse_uslm(_read(vol))
        assert len({d.work_uri for d in docs}) == len(docs) == len({d.expression_uri for d in docs})


# ---------------------------------------------------------------------------------------------- citations


def _cite(ext: dict[str, Any], title: str, num_path: list[str], work_date: date | None = None) -> str:
    return UsModule().provision_citation(
        ProvisionCitationInput(title, "act", "US", "section", num_path, "en", ext=ext, work_date=work_date)
    )


def test_citations_of_public_private_and_chapter_acts() -> None:
    public = {"congress": 118, "number": "214", "statutes_at_large": ["138 Stat. 2805"], "public_private": "public"}
    assert (
        _cite(public, "U.S. Congressman Sam Johnson Memorial VA Clinic Act", ["2"], date(2025, 1, 2))
        == "U.S. Congressman Sam Johnson Memorial VA Clinic Act, Pub. L. No. 118-214, § 2, 138 Stat. 2805 (2025)"
    )
    assert (
        _cite(public, "Public Law 118-214", ["2"], date(2025, 1, 2))
        == "Pub. L. No. 118-214, § 2, 138 Stat. 2805 (2025)"
    )
    private = {"congress": 57, "number": "944", "statutes_at_large": ["32 Stat. 1449"], "public_private": "private"}
    assert _cite(private, "Private Law 57-944", [""], date(1902, 6, 17)) == "Priv. L. No. 57-944, 32 Stat. 1449 (1902)"
    chapter = {"congress": 1, "chapter": "I", "statutes_at_large": ["1 Stat. 23"], "public_private": "public"}
    assert (
        _cite(chapter, "Act of June 1, 1789, ch. I", ["2"], date(1789, 6, 1))
        == "Act of June 1, 1789, ch. I, § 2, 1 Stat. 23"
    )
    named = {"congress": 1, "chapter": "XX", "statutes_at_large": ["1 Stat. 73"], "public_private": "public"}
    assert (
        _cite(named, "Judiciary Act of 1789", ["9"], date(1789, 9, 24))
        == "Judiciary Act of 1789, Act of Sept. 24, 1789, ch. XX, § 9, 1 Stat. 73"
    )


# ---------------------------------------------------------------------------------------------- connector

ROBOTS = "User-agent: *\nDisallow: /app/search/*\n"
SITE = "https://www.govinfo.gov"


def _store(cache: Path, path: str, data: bytes, status: int = 200) -> None:
    target = cache / "www.govinfo.gov" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    meta = {"url": f"{SITE}/{path}", "final_url": f"{SITE}/{path}", "status": status, "content_type": "text/xml"}
    target.with_name(target.name + ".meta.json").write_text(
        json.dumps({**meta, "retrieved_at": "2026-10-15T08:00:00+00:00"})
    )


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def _manifest(tmp_path: Path, entries: list[dict[str, Any]], **extra: Any) -> Path:
    manifest = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": SITE,
        "user_agent": UA,
        "crawl_delay_seconds": 5,
        "files": entries,
    }
    manifest.update(extra)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return path


def _connector(tmp_path: Path, entries: list[dict[str, Any]], cache: Path, **extra: Any) -> GovInfoStatutesConnector:
    return GovInfoStatutesConnector(_manifest(tmp_path, entries, **extra), SOURCE, cache, offline=True)


def test_connector_yields_volumes_as_received_and_each_xml_member_of_a_zip(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _store(cache, "robots.txt", ROBOTS.encode())
    _store(cache, "bulkdata/STATUTE/72/STATUTE-72.xml", _read(72))
    members = {
        "COMPS-2.xml": b"<b/>",
        "COMPS-10.xml": b"<c/>",
        "COMPS-1.xml": b"<a/>",
        "readme.pdf": b"%PDF",
        "x/y.xml": b"<n/>",
    }
    _store(cache, "bulkdata/COMPS/COMPS.zip", _zip(members))
    files = [
        {"path": "bulkdata/STATUTE/72/STATUTE-72.xml", "kind": "xml", "volume": 72},
        {"path": "bulkdata/COMPS/COMPS.zip", "kind": "zip"},
    ]
    connector = _connector(tmp_path, files, cache)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [
        "govinfo:STATUTE/72/STATUTE-72.xml",
        "govinfo:COMPS/COMPS.zip!COMPS-1.xml",
        "govinfo:COMPS/COMPS.zip!COMPS-2.xml",
        "govinfo:COMPS/COMPS.zip!COMPS-10.xml",
    ]
    assert docs[0].content == _read(72) and docs[0].media_type == "application/xml" and docs[0].metadata["volume"] == 72
    assert [d.content for d in docs[1:]] == [b"<a/>", b"<b/>", b"<c/>"]  # natural order; PDFs and paths skipped
    assert docs[2].uri == f"{SITE}/bulkdata/COMPS/COMPS.zip#COMPS-2.xml" and docs[2].metadata["member"] == "COMPS-2.xml"
    assert docs[1].metadata["archive_sha256"] == hashlib.sha256(_zip(members)).hexdigest()
    assert connector.fetch_errors == [] and connector.client is not None and connector.client.http.requests_made == 0


def test_connector_reports_what_is_missing_and_goes_on(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _store(cache, "robots.txt", ROBOTS.encode())
    _store(cache, "bulkdata/STATUTE/1/STATUTE-1.xml", b"", status=404)
    _store(cache, "bulkdata/STATUTE/72/STATUTE-72.xml", _read(72))
    _store(cache, "bulkdata/STATUTE/32/STATUTE-32.xml", b"<html>blocked</html>")
    _store(cache, "bulkdata/COMPS/COMPS.zip", b"PK\x03\x04 not really a zip")
    files = [{"path": f"bulkdata/STATUTE/{v}/STATUTE-{v}.xml", "kind": "xml"} for v in (1, 2, 32, 72)] + [
        {"path": "bulkdata/COMPS/COMPS.zip", "kind": "zip"}
    ]
    connector = _connector(tmp_path, files, cache)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["govinfo:STATUTE/72/STATUTE-72.xml"]
    errors = " | ".join(connector.fetch_errors)
    assert "STATUTE-1.xml: not found (404)" in errors and "offline and not cached" in errors
    assert "not XML" in errors and "not a readable zip" in errors and len(connector.fetch_errors) == 4


def test_connector_stops_on_a_pin_mismatch_and_on_a_path_robots_txt_forbids(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _store(cache, "robots.txt", ROBOTS.encode())
    _store(cache, "bulkdata/STATUTE/72/STATUTE-72.xml", _read(72))
    files = [{"path": "bulkdata/STATUTE/72/STATUTE-72.xml", "kind": "xml", "sha256": "0" * 64}]
    with pytest.raises(ManifestError, match="sha256"):
        list(_connector(tmp_path, files, cache).fetch())
    ok = [
        {"path": "bulkdata/STATUTE/72/STATUTE-72.xml", "kind": "xml", "sha256": hashlib.sha256(_read(72)).hexdigest()}
    ]
    assert len(list(_connector(tmp_path, ok, cache).fetch())) == 1
    forbidding = tmp_path / "forbidding"
    _store(forbidding, "robots.txt", b"User-agent: *\nDisallow: /bulkdata/STATUTE/\n")
    with pytest.raises(FetchError, match="disallowed by"):
        list(_connector(tmp_path, ok, forbidding).fetch())


def test_manifest_is_checked_against_the_collections_of_this_source(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    bad: list[dict[str, Any]] = [
        {"path": "bulkdata/CFR/2025/title-1/CFR-2025-title-1.zip", "kind": "zip"},  # the other source's collection
        {"path": "bulkdata/STATUTE/1/STATUTE-2.xml", "kind": "xml"},  # folder and file name differ
        {"path": "bulkdata/STATUTE/1/../../x.xml", "kind": "xml"},
        {"path": "bulkdata/STATUTE/1/STATUTE-1.xml", "kind": "zip"},  # kind does not match the name
        {"path": "bulkdata/COMPS/COMPS.zip", "kind": "pdf"},
        {"path": "https://example.org/bulkdata/COMPS/COMPS.zip", "kind": "zip"},
    ]
    for entry in bad:
        with pytest.raises(ManifestError):
            list(_connector(tmp_path, [entry], cache).fetch())
    twice = {"path": "bulkdata/COMPS/COMPS.zip", "kind": "zip"}
    for extra in (
        {"files": [twice, twice]},
        {"files": []},
        {"source_id": "other"},
        {"base_url": "https://example.org"},
        {"user_agent": "bot"},
    ):
        with pytest.raises(ManifestError):
            list(_connector(tmp_path, [twice], cache, **extra).fetch())


def test_committed_manifest_lists_every_volume_the_recent_laws_and_the_compilations() -> None:
    source_path = REPO_ROOT / "config" / "sources" / f"{SOURCE}.yaml"
    descriptor = yaml.safe_load(source_path.read_text())
    manifest_path = REPO_ROOT / descriptor["access"]["endpoint"]
    connector = GovInfoStatutesConnector(manifest_path, SOURCE, Path("unused"))
    manifest = connector._manifest()
    files = manifest["files"]
    volumes = [f["volume"] for f in files if "/STATUTE/" in f["path"]]
    assert volumes == list(range(1, 139))
    plaw = [f["path"] for f in files if "/PLAW/" in f["path"]]
    assert plaw == [
        "bulkdata/PLAW/119/public/PLAW-119-public.zip",
        "bulkdata/PLAW/119/private/PLAW-119-private.zip",
    ]
    assert files[-1] == {"path": "bulkdata/COMPS/COMPS.zip", "kind": "zip"} and len(files) == 141
    assert manifest["crawl_delay_seconds"] >= 5


def test_manifest_script_reads_the_directory_listings_of_the_repository() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_govinfo_manifests", REPO_ROOT / "scripts" / "build_govinfo_manifests.py"
    )
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    class _Client:
        robots = None

        def get(self, url: str, cache_key: str | None = None) -> tuple[bytes, dict[str, Any]] | None:
            assert (
                url == "https://www.govinfo.gov/bulkdata/CFR/2025/" and cache_key == "bulkdata/CFR/2025/_listing.html"
            )
            page = (
                '<tr><td><a href="/bulkdata/CFR/2025/title-12">title-12</a></td></tr>'
                '<tr><td><a href="/bulkdata/CFR/2025/title-1">title-1</a></td></tr>'
                '<tr><td><a href="/bulkdata/CFR/2025/CFR-2025.zip">CFR-2025.zip</a></td></tr>'
                '<tr><td><a href="/bulkdata/CFR/">Parent</a></td></tr>'
                '<tr><td><a href="/bulkdata/CFR/2025/">self</a></td></tr>'
            )
            return page.encode(), {}

    assert script.listing(_Client(), "bulkdata/CFR/2025") == ["CFR-2025.zip", "title-1", "title-12"]


def test_source_is_active_licence_approved_and_registered(catalog: Catalog) -> None:
    source = catalog.sources.get(SOURCE)
    assert source.status == "active" and source.licence.review_status == "approved"
    assert source.licence.reviewed_by.startswith("ULIS project, 2026-10-15, from the publisher's notice")
    assert (source.connector, source.parser) == ("govinfo_bulk_statutes", "uslm")
    assert CONNECTORS["govinfo_bulk_statutes"] is GovInfoStatutesConnector
    assert source.authority == "official_non_authentic" and source.jurisdictions == ["US"]
    assert "17 U.S.C. 105" in source.licence.name and source.licence.url == "https://www.govinfo.gov/about/policies"


def test_links_to_laws_resolve_to_the_work_and_section() -> None:
    assert resolve_uri("/us/pl/118/214/s2") == ("/us/pl/118/214", "sec_2")
    assert resolve_uri("/us/stat/1/23") == ("/us/stat/1/23", None)


# ---------------------------------------------------------------------------------------------- PostgreSQL


class _Volumes:
    def fetch(self) -> Iterator[RawDocument]:
        for vol in (1, 32, 72, 138):
            yield RawDocument(
                f"govinfo:STATUTE/{vol}/STATUTE-{vol}.xml",
                f"{SITE}/bulkdata/STATUTE/{vol}/STATUTE-{vol}.xml",
                "application/xml",
                _read(vol),
                datetime(2026, 10, 15, 8, 0, tzinfo=UTC),
                {"volume": vol},
            )


@pytest.fixture(scope="module")
def stat(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> Iterator[tuple[Session, IngestionReport]]:
    """The four fixture volumes ingested in one transaction that is never committed."""
    session = session_factory()
    report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Volumes())
    session.flush()
    try:
        yield session, report
    finally:
        session.rollback()
        session.close()


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="US", **kw), today=date(2026, 10, 15))


def _codes(item: dict[str, Any]) -> set[str]:
    return {lim["code"] for lim in item["limitations"]}


@pytest.mark.db
def test_volumes_are_ingested_as_instruments_of_the_enacted_laws(stat: tuple[Session, IngestionReport]) -> None:
    session, report = stat
    assert report.errors == [] and report.conflicts == [] and report.documents_new == 4
    uris = set(session.scalars(select(m.Instrument.uri).where(m.Instrument.uri.like("/us/%"))))
    assert {"/us/pl/118/214", "/us/pl/85/317", "/us/pvtl/57/944", "/us/stat/1/23", "/us/stat/1/418-2"} <= uris
    assert report.instruments_created == 10  # volume 1: 4 acts, 32: 3, 72: 1, 138: 2


@pytest.mark.db
def test_search_finds_a_modern_law_with_its_bluebook_citation_and_limitations(
    stat: tuple[Session, IngestionReport], catalog: Catalog
) -> None:
    session, _ = stat
    res = _search(session, catalog, q="community-based outpatient clinic in Plano, Texas shall be known")
    hit = next(h for h in res["results"] if h["document"]["uri"] == "/us/pl/118/214")
    assert (
        hit["citation"]
        == "U.S. Congressman Sam Johnson Memorial VA Clinic Act, Pub. L. No. 118-214, § 2, 138 Stat. 2805 (2025)"
    )
    assert hit["version"]["consolidation"] == "as_enacted" and hit["version"]["valid_from"] == "2025-01-02"
    assert {"EFFECTIVE_DATE_PRESUMED", "OFFICIAL_CONSOLIDATION_REPRODUCED"} - _codes(hit) == {
        "OFFICIAL_CONSOLIDATION_REPRODUCED"
    }
    assert "NOT_AUTHENTICATED" in _codes(hit) and "PROFILE_UNVERIFIED" in _codes(hit)


@pytest.mark.db
def test_search_finds_an_act_of_1789_by_date_and_chapter(
    stat: tuple[Session, IngestionReport], catalog: Catalog
) -> None:
    session, _ = stat
    res = _search(
        session,
        catalog,
        q="members of the several State legislatures at the next sessions thereof",
        include_historical=True,
    )
    hit = next(h for h in res["results"] if h["document"]["uri"] == "/us/stat/1/23")
    assert hit["citation"] == "Act of June 1, 1789, ch. I, § 3, 1 Stat. 23"
    assert hit["version"]["valid_from"] == "1789-06-01"


@pytest.mark.db
def test_point_in_time_lookup_of_an_enacted_law_follows_its_approval_date(
    stat: tuple[Session, IngestionReport], catalog: Catalog
) -> None:
    session, _ = stat
    after = provision_at(session, catalog, "/us/pl/118/214/~sec_2", date(2025, 6, 1))
    assert after["status_on_date"] == "in_force" and after["version"]["valid_from"] == "2025-01-02"
    before = provision_at(session, catalog, "/us/pl/118/214/~sec_2", date(2024, 6, 1))
    assert before["version"] is None  # not law yet


@pytest.mark.db
def test_stored_texts_rederive_from_the_stored_volumes(
    stat: tuple[Session, IngestionReport], catalog: Catalog, store: LocalObjectStore
) -> None:
    session, _ = stat
    docs = session.scalars(select(m.SourceDocument).where(m.SourceDocument.source_id == SOURCE)).all()
    assert len(docs) == 4
    for doc in docs:
        result = verify_source_document(session, catalog, store, doc.id, checker="pytest")
        assert result["result"] == "pass" and result["checked_units"] > 0, result["problems"][:3]
