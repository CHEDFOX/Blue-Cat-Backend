"""GovInfo bulk CFR annual editions and Federal Register final rules: parsers, connector, history, PostgreSQL.

Fixtures are real data cut byte for byte out of files downloaded from https://www.govinfo.gov/bulkdata/ on 2026-10-15
(see tests/fixtures/govinfo_cfr/README.md and tests/fixtures/govinfo_fr/README.md).
"""

from __future__ import annotations

import gzip
import hashlib
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
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import CONNECTORS, PARSERS, IngestionReport, Ingestor
from ulis.jurisdictions.modules.base import ProvisionCitationInput
from ulis.jurisdictions.modules.us import UsModule, is_cfr_edition_date
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.ecfr import TitleSplit, title_url
from ulis.sources.connectors.govinfo_bulk import GovInfoCfrFrConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.normalized import ParsedDocument, ParseSkipped
from ulis.sources.parsers import cfr_annual, ecfr, federal_register
from ulis.sources.parsers.cfr_annual import CfrParseError, parse_cfr_volume
from ulis.sources.parsers.federal_register import FederalRegisterParseError, parse_issue
from ulis.sources.parsers.govinfo_cfr_fr import parse_govinfo_cfr_fr
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

SOURCE = "us-govinfo-bulk-cfr-fr"
CFR = REPO_ROOT / "tests" / "fixtures" / "govinfo_cfr"
FR = REPO_ROOT / "tests" / "fixtures" / "govinfo_fr"
ECFR_TITLE1 = REPO_ROOT / "tests" / "fixtures" / "ecfr" / "ECFR-title1.xml.gz"
ECFR_TITLE12 = REPO_ROOT / "tests" / "fixtures" / "ecfr" / "ECFR-title12-excerpt.xml.gz"


def _cfr(name: str) -> bytes:
    return (CFR / name).read_bytes()


def _by_part(name: str) -> dict[str, ParsedDocument]:
    return {d.work_uri: d for d in parse_cfr_volume(_cfr(name))}


def _eids(doc: ParsedDocument) -> dict[str, Any]:
    return {p.eid: p for p in doc.provisions}


# ---------------------------------------------------------------------------------------------- CFR parser


def test_an_edition_is_dated_by_its_revision_date_and_keeps_the_parts_identity() -> None:
    docs = _by_part("CFR-2025-title12-excerpt.xml")
    assert sorted(docs) == ["/us/cfr/t12/p1083", "/us/cfr/t12/p214", "/us/cfr/t12/p302"]
    part = docs["/us/cfr/t12/p302"]
    stamp = hashlib.sha256(_cfr("CFR-2025-title12-excerpt.xml")).hexdigest()[:8]
    assert part.expression_uri == f"/us/cfr/t12/p302/en@2025-01-01/cfr-{stamp}"
    assert (part.version_date, part.currency_date, part.work_date) == (date(2025, 1, 1), date(2025, 1, 1), None)
    assert part.title == "12 CFR Part 302: Use of Supervisory Guidance (CFR annual edition revised as of 2025-01-01)"
    assert (part.validity_basis, part.consolidation_kind, part.text_origin) == (
        "attested_at_edition",
        "cfr_annual_edition",
        "official_conversion",
    )
    assert part.authoritative is False and part.instrument_type == "regulation" and part.number == "302"
    assert part.ext["cfr_title"] == 12 and part.ext["cfr_part"] == "302" and part.ext["cfr_chapter"] == "III"
    assert part.ext["agency"] == "Federal Deposit Insurance Corporation"
    assert part.ext["cfr_authority"] == "5 U.S.C. 552; 12 U.S.C. 1818, 1819(a) (Seventh and Tenth), 1831p-1."
    assert part.ext["volume_amended_on"] == "2025-01-01"
    assert all(p.valid_from is None and p.status == "in_force" for p in part.provisions)


def test_sections_paragraphs_appendices_and_footnotes_follow_the_ecfr_parser() -> None:
    part = _by_part("CFR-2025-title12-excerpt.xml")["/us/cfr/t12/p302"]
    by = _eids(part)
    assert [p.eid for p in part.provisions if p.kind == "section"] == ["sec_302.1", "sec_302.2", "sec_302.3"]
    assert by["sec_302.1"].heading == "Purpose."
    appendix = by["app_A"]
    assert (
        appendix.kind == "appendix"
        and appendix.heading == "Appendix A to Part 302—Statement Clarifying the Role of Supervisory Guidance"
    )
    assert appendix.note and appendix.note.startswith("Footnote: 1 Government agencies issue regulations")
    penalties = _by_part("CFR-2025-title12-excerpt.xml")["/us/cfr/t12/p1083"]
    sec = _eids(penalties)["sec_1083.1"]
    assert sec.note == "Source credit: [89 FR 1789, Jan. 11, 2024]"
    assert " | " in sec.text  # a table is flattened to one line per row, cells joined by " | "
    para = _eids(_by_part("CFR-2025-title12-excerpt.xml")["/us/cfr/t12/p214"])["sec_214.3__para_b"]
    assert para.num_path == ["214.3", "b"] and para.text.startswith(
        "The Board of Governors of the Federal Reserve System reserves"
    )


def test_text_is_the_same_as_the_ecfrs_for_what_the_editions_share() -> None:
    """The edition file is indented by GPO's pretty-printer, the eCFR file is not: after the indentation is removed the
    two sources give the same identifiers and the same words for every provision both hold."""
    annual = _by_part("CFR-2025-title12-excerpt.xml")["/us/cfr/t12/p302"]
    data = gzip.decompress(ECFR_TITLE12.read_bytes())
    stamp = datetime(2026, 10, 6, 21, 51, 17, tzinfo=UTC)
    current = next(
        ecfr.parse_ecfr(env)[0]
        for number, env in TitleSplit(data, 12, title_url(12), stamp, "0" * 64).parts()
        if number == "302"
    )
    a, c = _eids(annual), _eids(current)
    shared = sorted(set(a) & set(c))
    assert shared == ["app_A", "sec_302.1", "sec_302.2", "sec_302.3"]
    assert [eid for eid in shared if a[eid].text != c[eid].text] == []
    assert {"sec_302.100"} <= set(c) - set(a)  # added after the edition


def test_indentation_is_removed_and_every_other_space_is_kept() -> None:
    pad = b" " * 24
    xml = (
        b"<CFRDOC><FMTR><TITLEPG><TITLENUM>Title 9</TITLENUM>"
        b"<REVISED>Revised as of January 1, 2020</REVISED></TITLEPG></FMTR>"
        b'<TITLE><CHAPTER><TOC><TOCHD><HD SOURCE="HED">CHAPTER I\xe2\x80\x94TEST AGENCY</HD></TOCHD></TOC>'
        b'<PART><HD SOURCE="HED">PART 5\xe2\x80\x94TEST</HD>'
        b"<SECTION><SECTNO>\xc2\xa7\xe2\x80\x895.1</SECTNO><SUBJECT>Scope.</SUBJECT>\n"
        + pad
        + b"<P>\n"
        + pad
        + b"    (a) \n"
        + pad
        + b'    <E T="03">Caption.</E>\n'
        + pad
        + b"     Pursuant to 15 U.S.C.78a \n"
        + pad
        + b'    <E T="03">et seq.</E>\n'
        + pad
        + b"    ). Eight \n"
        + pad
        + b"    <FR>1/2</FR>\n"
        + pad
        + b"     inches, and 1\n"
        + pad
        + b"    <FR>1/2</FR>\n"
        + pad
        + b"     more.\n"
        + pad
        + b"</P>\n"
        b"</SECTION></PART></CHAPTER></TITLE></CFRDOC>"
    )
    (doc,) = parse_cfr_volume(xml)
    para = next(p for p in doc.provisions if p.eid == "sec_5.1__para_a")
    # the spaces kept beside a line break are the source text's own (after "(a)", before "Pursuant", "78a ", "Eight ");
    # the one before ")" does not exist in it; a stacked fraction after a whole number reads "1 1/2"
    assert para.text == "Caption. Pursuant to 15 U.S.C.78a et seq.). Eight 1/2 inches, and 1 1/2 more."
    assert doc.provisions[0].text.startswith("(a) Caption. Pursuant")


def test_the_older_layout_without_a_title_number_on_the_title_page_is_read() -> None:
    part = _by_part("CFR-2001-title1-part2.xml")["/us/cfr/t1/p2"]
    assert part.version_date == date(2000, 4, 1)  # the file's own revision date; it sits in the folder of 2001
    assert part.ext["cfr_title"] == 1 and part.ext["cfr_chapter"] == "I" and "volume_amended_on" not in part.ext
    assert [p.eid for p in part.provisions if p.kind == "section"][:2] == ["sec_2.1", "sec_2.2"]
    assert is_cfr_edition_date(part.version_date) and not is_cfr_edition_date(date(2026, 10, 6))


def test_volumes_that_cannot_be_read_are_refused_or_skipped_not_guessed() -> None:
    with pytest.raises(CfrParseError, match="not a CFR volume"):
        parse_cfr_volume(b"<FEDREG/>")
    with pytest.raises(CfrParseError, match="not well-formed"):
        parse_cfr_volume(b"<CFRDOC>")
    with pytest.raises(CfrParseError, match="no title number"):
        parse_cfr_volume(b"<CFRDOC><FMTR/></CFRDOC>")
    with pytest.raises(CfrParseError, match="no revision date"):
        parse_cfr_volume(b"<CFRDOC><FMTR><TITLEPG><TITLENUM>Title 3</TITLENUM></TITLEPG></FMTR></CFRDOC>")
    only_reserved = (
        b"<CFRDOC><FMTR><TITLEPG><TITLENUM>Title 3</TITLENUM><REVISED>Revised as of January 1, 2020</REVISED>"
        b'</TITLEPG></FMTR><TITLE><CHAPTER><PART><HD SOURCE="HED">PART 1\xe2\x80\x94X</HD>'
        b"<RESERVED>[Reserved]</RESERVED></PART></CHAPTER></TITLE></CFRDOC>"
    )
    with pytest.raises(ParseSkipped):
        parse_cfr_volume(only_reserved)
    with pytest.raises(ValueError, match="neither"):
        parse_govinfo_cfr_fr(b"<html/>")
    assert (
        PARSERS["govinfo_cfr_fr"] is parse_govinfo_cfr_fr
        and cfr_annual.PARSER_VERSION
        and federal_register.PARSER_VERSION
    )


# ---------------------------------------------------------------------------------------------- FR parser


def _issue() -> list[ParsedDocument]:
    return parse_issue((FR / "FR-2026-10-14-excerpt.xml").read_bytes())


def test_final_rules_are_identified_by_volume_and_first_page() -> None:
    docs = {d.work_uri: d for d in _issue()}
    assert sorted(docs) == ["/us/fr/91/65100", "/us/fr/91/65107", "/us/fr/91/65109"]
    rule = docs["/us/fr/91/65100"]
    assert rule.title == (
        "Airworthiness Directives; Bombardier, Inc., Airplanes "
        "(Department of Transportation; Final rule, 91 FR 65100, 2026-10-14)"
    )
    assert (rule.number, rule.ext["fr_number"], rule.ext["fr_volume"], rule.ext["fr_page"]) == (
        "2026-21010",
        "2026-21010",
        91,
        65100,
    )
    assert rule.ext["agency"] == "Department of Transportation" and rule.ext["fr_action"] == "Final rule."
    assert rule.expression_uri == "/us/fr/91/65100/en@2026-10-14"
    assert (rule.work_date, rule.version_date, rule.currency_date) == (date(2026, 10, 14), date(2026, 10, 14), None)
    assert rule.instrument_type == "regulation" and rule.consolidation_kind == "federal_register_rule"
    assert (rule.validity_basis, rule.text_origin, rule.authoritative) == (
        "source_periods",
        "official_conversion",
        False,
    )
    assert [e.event_type for e in rule.events] == ["publication"]
    assert all(p.valid_from is None for p in rule.provisions)  # the effective date is prose in DATES: not extracted


def test_preamble_is_marked_as_explanation_and_regulatory_text_is_kept_apart() -> None:
    rule = {d.work_uri: d for d in _issue()}["/us/fr/91/65100"]
    by = _eids(rule)
    assert by["summary"].text.startswith(
        "The FAA is adopting a new airworthiness directive (AD) for certain Bombardier"
    )
    assert by["dates"].text.startswith("This AD is effective November 18, 2026.")
    assert by["summary"].note == federal_register.PREAMBLE_NOTE and by["regtext"].note == federal_register.REGTEXT_NOTE
    assert by["regtext"].kind == "regulatory_text" and by["regtext"].heading == "Regulatory text: 14 CFR part 39"
    # the heading between the two amendments belongs to the second one
    assert by["regtext_2"].text.startswith("§ 39.13 [Amended]\n2. The FAA amends § 39.13 by adding the following new")
    assert by["signature"].text.startswith("Issued on September 25, 2026.")
    kinds = {p.kind for p in rule.provisions}
    assert kinds == {"preamble", "regulatory_text"} and all(not p.num_path for p in rule.provisions)
    assert [p.heading for p in rule.provisions if p.eid.startswith("supplementary")][:3] == [
        "Background",
        "Discussion of Final Airworthiness Directive",
        "Comments",
    ]


def test_a_rule_with_run_in_headings_and_a_table_is_read_and_footnotes_are_collected() -> None:
    docs = {d.work_uri: d for d in _issue()}
    food = docs["/us/fr/91/65109"]
    assert [p.eid for p in food.provisions if p.eid.startswith("preamble_text")] == ["preamble_text", "preamble_text_2"]
    assert food.ext["fr_action"].lower().startswith("final amendment")
    assert docs["/us/fr/91/65107"].provisions[1].text == "Effective date 0901 UTC, December 24, 2026."


def test_two_rules_on_one_page_and_what_cannot_be_identified() -> None:
    def rule(page: str, number: str) -> str:
        return (
            f'<PRTPAGE P="{page}"/><RULE><PREAMB><AGENCY TYPE="F">AGENCY ONE</AGENCY><CFR>9 CFR Part 1</CFR>'
            f'<SUBJECT>Subject {number}</SUBJECT><ACT><HD SOURCE="HED">ACTION:</HD><P>Final rule.</P></ACT>'
            f'<SUM><HD SOURCE="HED">SUMMARY:</HD><P>Summary {number}.</P></SUM></PREAMB>'
            f'<SUPLINF><HD SOURCE="HED">SUPPLEMENTARY INFORMATION:</HD><P>Text {number}.</P></SUPLINF>'
            f"<FRDOC>[FR Doc. {number} Filed 10-13-26; 8:45 am]</FRDOC></RULE>"
        )

    head = "<VOL>91</VOL><NO>1</NO><DATE>Wednesday, October 14, 2026</DATE><UNITNAME>Rules</UNITNAME>"
    nameless = "<RULE><PREAMB><SUBJECT>No doc number</SUBJECT></PREAMB></RULE>"
    xml = f"<FEDREG>{head}<RULES>{rule('100', '2026-1')}{nameless}{rule('100', '2026-2')}</RULES></FEDREG>"
    docs = parse_issue(xml.encode())
    assert [d.work_uri for d in docs] == ["/us/fr/91/100", "/us/fr/91/100-2"]
    assert docs[1].expression_uri == "/us/fr/91/100-2/en@2026-10-14" and docs[1].number == "2026-2"
    with pytest.raises(ParseSkipped):
        parse_issue(f"<FEDREG>{head}<NOTICES><NOTICE/></NOTICES></FEDREG>".encode())
    with pytest.raises(FederalRegisterParseError, match="no volume or date"):
        parse_issue(b"<FEDREG><NO>1</NO></FEDREG>")
    with pytest.raises(FederalRegisterParseError, match="not a Federal Register issue"):
        parse_issue(b"<CFRDOC/>")
    assert parse_govinfo_cfr_fr(xml.encode())[0].work_uri == "/us/fr/91/100"


def test_pages_agree_with_the_issues_own_table_of_contents_on_a_real_issue() -> None:
    """The fixture is an excerpt, so its contents are synthesised from the page marks of the whole issue, checked
    once by hand against the issue's CNTNTS (10 of 10 rules agreed); here the pages are the ones recorded."""
    assert {d.ext["fr_number"]: d.ext["fr_page"] for d in _issue()} == {
        "2026-21010": 65100,
        "2026-20974": 65107,
        "2026-20979": 65109,
    }


# ---------------------------------------------------------------------------------------------- citations


def _cite(ext: dict[str, Any], eid: str, work_date: date | None, version_date: date | None, num_path: list[str]) -> str:
    return UsModule().provision_citation(
        ProvisionCitationInput(
            "x",
            "regulation",
            "US",
            "section",
            num_path,
            "en",
            ext=ext,
            work_date=work_date,
            version_date=version_date,
            eid=eid,
        )
    )


def test_citations_of_editions_ecfr_files_and_federal_register_rules() -> None:
    ext = {"cfr_title": 12, "cfr_part": "302"}
    assert _cite(ext, "sec_302.1", None, date(2025, 1, 1), ["302.1", "b"]) == "12 C.F.R. § 302.1(b) (2025)"
    # a version of the same part that is a file of the eCFR (dated by the day GPO made it) has no year
    assert _cite(ext, "sec_302.1", None, date(2026, 10, 6), ["302.1", "b"]) == "12 CFR 302.1(b)"
    assert _cite({**ext, "cfr_compilation": "ecfr"}, "sec_302.1", None, date(2025, 1, 1), ["302.1"]) == "12 CFR 302.1"
    fr = {"fr_volume": 91, "fr_page": 65100}
    assert (
        _cite(fr, "summary", date(2026, 10, 14), date(2026, 10, 14), [])
        == "91 Fed. Reg. 65,100 (Oct. 14, 2026), summary"
    )
    assert (
        _cite(fr, "regtext_2", date(2026, 10, 14), None, []) == "91 Fed. Reg. 65,100 (Oct. 14, 2026), regulatory text"
    )
    assert _cite(fr, "supplementary_information_3", date(2026, 10, 14), None, []) == (
        "91 Fed. Reg. 65,100 (Oct. 14, 2026), supplementary information"
    )


# ---------------------------------------------------------------------------------------------- connector

ROBOTS = "User-agent: *\nDisallow: /app/search/*\n"
SITE = "https://www.govinfo.gov"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"


def _store(cache: Path, path: str, data: bytes) -> None:
    target = cache / "www.govinfo.gov" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    meta = {"url": f"{SITE}/{path}", "final_url": f"{SITE}/{path}", "status": 200, "content_type": "application/zip"}
    target.with_name(target.name + ".meta.json").write_text(
        json.dumps({**meta, "retrieved_at": "2026-10-15T08:00:00+00:00"})
    )


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def _connector(tmp_path: Path, entries: list[dict[str, Any]], cache: Path) -> GovInfoCfrFrConnector:
    path = tmp_path / "manifest.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "source_id": SOURCE,
                "base_url": SITE,
                "user_agent": UA,
                "crawl_delay_seconds": 5,
                "files": entries,
            }
        )
    )
    return GovInfoCfrFrConnector(path, SOURCE, cache, offline=True)


def test_connector_yields_every_volume_and_issue_of_a_zip_oldest_first(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _store(cache, "robots.txt", ROBOTS.encode())
    cfr = {"CFR-2012-title1-vol1.xml": _cfr("CFR-2012-title1-part2.xml")}
    _store(cache, "bulkdata/CFR/2012/title-1/CFR-2012-title-1.zip", _zip(cfr))
    issues = {"FR-2026-10-02.xml": b"<FEDREG/>", "FR-2026-10-01.xml": b"<FEDREG/>", "FR-2026-10-14.xml": b"<FEDREG/>"}
    _store(cache, "bulkdata/FR/2026/10/FR-2026-10.zip", _zip(issues))
    entries = [
        {"path": "bulkdata/CFR/2012/title-1/CFR-2012-title-1.zip", "kind": "zip", "year": 2012, "title": 1},
        {"path": "bulkdata/FR/2026/10/FR-2026-10.zip", "kind": "zip", "year": 2026, "month": 10},
    ]
    connector = _connector(tmp_path, entries, cache)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == [
        "govinfo:CFR/2012/title-1/CFR-2012-title-1.zip!CFR-2012-title1-vol1.xml",
        "govinfo:FR/2026/10/FR-2026-10.zip!FR-2026-10-01.xml",
        "govinfo:FR/2026/10/FR-2026-10.zip!FR-2026-10-02.xml",
        "govinfo:FR/2026/10/FR-2026-10.zip!FR-2026-10-14.xml",
    ]
    assert docs[0].content == _cfr("CFR-2012-title1-part2.xml") and docs[0].metadata["year"] == 2012
    assert connector.fetch_errors == []


def test_connector_manifest_is_limited_to_the_cfr_and_federal_register_collections(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    bad = [
        {"path": "bulkdata/STATUTE/1/STATUTE-1.xml", "kind": "xml"},
        {"path": "bulkdata/CFR/2012/title-1/CFR-2013-title-1.zip", "kind": "zip"},
        {"path": "bulkdata/CFR/2012/title-51/CFR-2012-title-51.zip", "kind": "zip"},
        {"path": "bulkdata/FR/2026/10/FR-2026-11.zip", "kind": "zip"},
        {"path": "bulkdata/FR/2026/13/FR-2026-13.zip", "kind": "zip"},
        {"path": "bulkdata/ECFR/title-1/ECFR-title1.xml", "kind": "xml"},
    ]
    for entry in bad:
        with pytest.raises(ManifestError):
            list(_connector(tmp_path, [entry], cache).fetch())


def test_committed_manifest_lists_the_editions_oldest_first_and_five_years_of_the_federal_register() -> None:
    descriptor = yaml.safe_load((REPO_ROOT / "config" / "sources" / f"{SOURCE}.yaml").read_text())
    path = REPO_ROOT / descriptor["access"]["endpoint"]
    manifest = GovInfoCfrFrConnector(path, SOURCE, Path("unused"))._manifest()
    files = manifest["files"]
    cfr = [f for f in files if "/CFR/" in f["path"]]
    fr = [f for f in files if "/FR/" in f["path"]]
    assert len(files) == len(cfr) + len(fr) and len(cfr) > 1300 and len(fr) >= 50
    assert [(f["year"], f["title"]) for f in cfr] == sorted(
        (f["year"], f["title"]) for f in cfr
    )  # oldest edition first
    assert cfr[0]["year"] <= 1996 and cfr[-1]["year"] >= 2026
    assert files.index(cfr[-1]) < files.index(fr[0])
    assert {f["year"] for f in fr} == set(range(fr[0]["year"], fr[0]["year"] + 5)) and fr[-1]["year"] >= 2026
    assert [(f["year"], f["month"]) for f in fr] == sorted((f["year"], f["month"]) for f in fr)


def test_source_is_active_licence_approved_and_registered(catalog: Catalog) -> None:
    source = catalog.sources.get(SOURCE)
    assert source.status == "active" and source.licence.review_status == "approved"
    assert source.licence.reviewed_by.startswith("ULIS project, 2026-10-15, from the publisher's notice")
    assert (source.connector, source.parser) == ("govinfo_bulk_cfr_fr", "govinfo_cfr_fr")
    assert CONNECTORS["govinfo_bulk_cfr_fr"] is GovInfoCfrFrConnector
    assert source.authority == "official_non_authentic" and source.document_kinds == ["regulation"]
    assert "17 U.S.C. 105" in source.licence.name and "CFR-XML_User-Guide" in source_path_text()


def source_path_text() -> str:
    return (REPO_ROOT / "config" / "sources" / f"{SOURCE}.yaml").read_text()


# ---------------------------------------------------------------------------------------------- PostgreSQL


def _raw(name: str, content: bytes, when: datetime) -> RawDocument:
    return RawDocument(f"govinfo:test/{name}", f"{SITE}/bulkdata/{name}", "application/xml", content, when, {})


class _Editions:
    """Part 2 of title 1 as the editions of 2000, 2012 and 2023, oldest first."""

    def __init__(
        self,
        names: tuple[str, ...] = (
            "CFR-2001-title1-part2.xml",
            "CFR-2012-title1-part2.xml",
            "CFR-2023-title1-part2.xml",
        ),
    ):
        self.names = names

    def fetch(self) -> Iterator[RawDocument]:
        for name in self.names:
            yield _raw(name, _cfr(name), datetime(2026, 10, 15, 8, 0, tzinfo=UTC))


class _Ecfr:
    """The eCFR file of title 1 (Last-Modified 2026-08-11), part 2 only."""

    def fetch(self) -> Iterator[RawDocument]:
        stamp = datetime(2026, 8, 11, 20, 55, 31, tzinfo=UTC)
        split = TitleSplit(gzip.decompress(ECFR_TITLE1.read_bytes()), 1, title_url(1), stamp, "0" * 64)
        for number, envelope in split.parts():
            if number == "2":
                yield RawDocument("ecfr:t1:p2", title_url(1), "application/xml", envelope, stamp, {})


class _Issue:
    def fetch(self) -> Iterator[RawDocument]:
        name = "FR-2026-10-14-excerpt.xml"
        yield _raw(name, (FR / name).read_bytes(), datetime(2026, 10, 15, 8, 0, tzinfo=UTC))


@pytest.fixture
def db(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> Iterator[tuple[Session, Ingestor]]:
    session = session_factory()
    try:
        yield session, Ingestor(session, catalog, store)
    finally:
        session.rollback()
        session.close()


def _versions(session: Session, uri: str) -> list[tuple[str, date | None, str]]:
    rows = session.execute(
        select(m.ProvisionVersion.text, m.ProvisionVersion.valid_from, m.ProvisionVersion.valid_from_basis)
        .join(m.Provision)
        .where(m.Provision.uri == uri)
        .order_by(m.ProvisionVersion.valid_from.nulls_first())
    ).all()
    return [(t, d, b) for t, d, b in rows]


@pytest.mark.db
def test_editions_oldest_first_then_the_ecfr_build_the_history_of_a_section(
    db: tuple[Session, Ingestor], catalog: Catalog
) -> None:
    session, ingestor = db
    first = ingestor.ingest_source(SOURCE, actor="pytest", connector=_Editions())
    assert first.errors == [] and first.conflicts == [] and first.documents_new == 3
    assert first.instruments_created == 1 and first.instrument_versions_created == 3
    assert first.provision_versions_restated > 0 and first.provision_versions_corroborated > 0
    current = ingestor.ingest_source("us-ecfr", actor="pytest", connector=_Ecfr())
    assert current.errors == [] and current.conflicts == [] and current.documents_new == 1
    versions = _versions(session, "/us/cfr/t1/p2/~sec_2.5")
    # 2000, 2012 and 2023 differ; the eCFR of 2026 repeats the 2023 wording, so it adds a source, not a version
    assert [(d, b) for _, d, b in versions] == [
        (None, "source"),
        (date(2012, 1, 1), "observed"),
        (date(2023, 1, 1), "observed"),
    ]
    assert len({t for t, _, _ in versions}) == 3
    unchanged = _versions(session, "/us/cfr/t1/p2/~sec_2.1")
    assert len(unchanged) == 1 and unchanged[0][1] is None
    n_versions = session.scalar(
        select(func.count())
        .select_from(m.InstrumentVersion)
        .join(m.Instrument)
        .where(m.Instrument.uri == "/us/cfr/t1/p2")
    )
    assert n_versions == 4

    # a lookup follows the source: 2015 -> the wording of the 2012 edition, with its date caveat
    detail = provision_at(session, catalog, "/us/cfr/t1/p2/~sec_2.5", date(2015, 6, 1))
    assert detail["version"]["valid_from"] == "2012-01-01" and detail["version"]["text"] == versions[1][0]
    assert detail["provision"]["citation"] == "1 C.F.R. § 2.5 (2012)"
    old = provision_at(session, catalog, "/us/cfr/t1/p2/~sec_2.5", date(2005, 6, 1))
    assert old["version"]["text"] == versions[0][0] and old["status_on_date"] == "in_force_date_unverified"
    assert "ATTESTED_ONLY_AT" in {lim["code"] for lim in old["limitations"]}
    now = provision_at(session, catalog, "/us/cfr/t1/p2/~sec_2.5", date(2026, 10, 15))
    assert now["version"]["text"] == versions[2][0]
    # the citation names the latest source that attests the wording: the eCFR repeats the 2023 wording (no year, the
    # eCFR is not an edition) and the editions of 2000, 2012 and 2023 all have section 2.1, then the eCFR again
    assert now["provision"]["citation"] == "1 CFR 2.5"
    assert (
        provision_at(session, catalog, "/us/cfr/t1/p2/~sec_2.1", date(2026, 10, 15))["provision"]["citation"]
        == "1 CFR 2.1"
    )


@pytest.mark.db
def test_the_ecfr_loaded_first_makes_an_older_edition_a_recorded_conflict(
    db: tuple[Session, Ingestor], catalog: Catalog
) -> None:
    """Why the editions are loaded oldest first and before the eCFR: the pipeline never overwrites, and it does not
    put an older wording before a newer one."""
    session, ingestor = db
    assert ingestor.ingest_source("us-ecfr", actor="pytest", connector=_Ecfr()).errors == []
    late = ingestor.ingest_source(SOURCE, actor="pytest", connector=_Editions(("CFR-2001-title1-part2.xml",)))
    assert late.errors == [] and late.provision_versions_restated == 0
    assert any("/us/cfr/t1/p2/~sec_2.3" in c for c in late.conflicts)
    assert len(_versions(session, "/us/cfr/t1/p2/~sec_2.3")) == 1


@pytest.mark.db
def test_final_rules_are_searchable_with_their_citation_and_limitations(
    db: tuple[Session, Ingestor], catalog: Catalog
) -> None:
    session, ingestor = db
    report = ingestor.ingest_source(SOURCE, actor="pytest", connector=_Issue())
    assert (
        report.errors == [] and report.conflicts == [] and report.documents_new == 1 and report.instruments_created == 3
    )
    res = search(
        session,
        catalog,
        SearchRequest(q="new airworthiness directive Bombardier Model airplanes", jurisdiction="US"),
        today=date(2026, 10, 15),
    )
    hit = next(h for h in res["results"] if h["document"]["uri"] == "/us/fr/91/65100")
    assert hit["citation"].startswith("91 Fed. Reg. 65,100 (Oct. 14, 2026)")
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"EFFECTIVE_DATE_UNKNOWN", "NOT_AUTHENTICATED", "VERSION_NOTE"} <= codes
    assert hit["version"]["consolidation"] == "federal_register_rule" and hit["version"]["valid_from"] is None
    detail = provision_at(session, catalog, "/us/fr/91/65100/~dates", date(2026, 10, 15))
    assert detail["version"]["text"].startswith("This AD is effective November 18, 2026.")
    assert detail["provision"]["citation"] == "91 Fed. Reg. 65,100 (Oct. 14, 2026), dates"


@pytest.mark.db
def test_stored_files_rederive_from_the_stored_documents(
    db: tuple[Session, Ingestor], catalog: Catalog, store: LocalObjectStore
) -> None:
    session, ingestor = db
    ingestor.ingest_source(SOURCE, actor="pytest", connector=_Editions(("CFR-2023-title1-part2.xml",)))
    ingestor.ingest_source(SOURCE, actor="pytest", connector=_Issue())
    docs = session.scalars(select(m.SourceDocument).where(m.SourceDocument.source_id == SOURCE)).all()
    assert len(docs) == 2
    for doc in docs:
        result = verify_source_document(session, catalog, store, doc.id, checker="pytest")
        assert result["result"] == "pass" and result["checked_units"] > 0, result["problems"][:3]


def test_fixture_readmes_say_where_the_files_come_from() -> None:
    for folder in (CFR, FR):
        text = (folder / "README.md").read_text()
        assert "govinfo.gov/bulkdata" in text and "17 U.S.C. 105" in text
    listed = set(re.findall(r"`(CFR-[0-9a-z-]+\.xml)`", (CFR / "README.md").read_text()))
    assert listed == {p.name for p in CFR.glob("*.xml")}
