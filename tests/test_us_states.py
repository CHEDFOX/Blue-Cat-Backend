"""United States state law: Washington (RCW), Texas, Oregon (ORS) and Utah - parsers, citations, connectors,
state-versus-federal scoping on PostgreSQL, and the states recorded as planned or rejected.

Fixtures are real files retrieved on 2026-10-14 (tests/fixtures/us_states/README.md): whole chapter pages / files for
Washington 9A.32, Oregon 12 and 285A and Utah title 3, real members of the Texas Penal Code and Code of Criminal
Procedure zips, and an excerpt of the Utah title 76 XML (bytes of chapter 76-2 and part 76-5-2 as received).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import zipfile
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.orm import Session, sessionmaker

from ulis.catalog import Catalog
from ulis.ingestion.pipeline import CONNECTORS, PARSERS, IngestionReport, Ingestor
from ulis.jurisdictions.modules import get_module
from ulis.jurisdictions.modules.base import ProvisionCitationInput
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.connectors.us_or_ors import OrOrsConnector
from ulis.sources.connectors.us_or_ors import chapter_url as or_url
from ulis.sources.connectors.us_state_http import ManifestError
from ulis.sources.connectors.us_tx_statutes import TxStatutesConnector, zip_url
from ulis.sources.connectors.us_ut_code import UtCodeConnector, parse_index_version, parse_title_versions, title_url
from ulis.sources.connectors.us_wa_rcw import WaRcwConnector
from ulis.sources.connectors.us_wa_rcw import chapter_url as wa_url
from ulis.sources.normalized import ParsedDocument
from ulis.sources.parsers.us_or_ors import parse_or_ors
from ulis.sources.parsers.us_state_common import StateParseError
from ulis.sources.parsers.us_tx_statutes import CODES, parse_tx_statutes
from ulis.sources.parsers.us_tx_statutes import resolve_uri as tx_resolve
from ulis.sources.parsers.us_ut_code import parse_ut_code
from ulis.sources.parsers.us_wa_rcw import parse_wa_rcw
from ulis.sources.parsers.us_wa_rcw import resolve_uri as wa_resolve
from ulis.sources.registry import LicenceNotApproved, licence_gate
from ulis.sources.us_state_bundle import BundleError, make_bundle, read_bundle
from ulis.storage.object_store import LocalObjectStore

FIX = REPO_ROOT / "tests" / "fixtures" / "us_states"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
RETRIEVED = "2026-10-14T15:30:00+00:00"
META = {"url": "https://example.invalid/page", "retrieved_at": RETRIEVED, "content_type": "text/html"}


def _gz(name: str) -> bytes:
    return gzip.decompress((FIX / name).read_bytes())


def _bundle(state: str, key: str, body: bytes, content_type: str = "text/html") -> bytes:
    return make_bundle(state, key, body, {**META, "content_type": content_type})


def _wa() -> ParsedDocument:
    return parse_wa_rcw(_bundle("WA", "9A.32", _gz("wa_rcw_9A.32.html.gz")))[0]


def _tx(code: str) -> ParsedDocument:
    name = {"PE": "tx_PE_excerpt.htm.zip", "CR": "tx_CR_excerpt.htm.zip"}[code]
    return parse_tx_statutes(_bundle("TX", code, (FIX / name).read_bytes(), "application/zip"))[0]


def _or(chapter: str) -> ParsedDocument:
    return parse_or_ors(_bundle("OR", chapter, _gz(f"or_ors{chapter.zfill(3)}.html.gz")))[0]


def _ut(title: str, name: str) -> ParsedDocument:
    return parse_ut_code(_bundle("UT", title, _gz(name), "text/xml"))[0]


def _by(doc: ParsedDocument) -> dict[str, Any]:
    return {p.eid: p for p in doc.provisions}


# ---------------------------------------------------------------- the envelope


def test_bundle_keeps_the_exact_bytes_and_the_retrieval_time() -> None:
    body = _gz("wa_rcw_9A.32.html.gz")
    b = read_bundle(_bundle("WA", "9A.32", body), "WA")
    assert b.body_bytes == body and b.key == "9A.32" and b.retrieved_on == date(2026, 10, 14)
    # Bytes that are not UTF-8 (Oregon's Windows-1252 files) travel as base64 and come back identical.
    raw = "Chapter 12 — “x”".encode("cp1252")
    assert read_bundle(make_bundle("OR", "12", raw, META), "OR").body_bytes == raw
    with pytest.raises(BundleError, match="state"):
        read_bundle(_bundle("WA", "9A.32", body), "TX")
    with pytest.raises(BundleError, match="not a"):
        read_bundle(b"<html/>", "WA")


# ---------------------------------------------------------------- Washington


def test_rcw_chapter_structure_dates_and_text() -> None:
    d = _wa()
    assert (d.work_uri, d.expression_uri) == ("/us-wa/rcw/9A.32", "/us-wa/rcw/9A.32/en@2026-10-14/rcw-online")
    assert (d.jurisdiction_id, d.extent, d.instrument_type, d.number) == ("US-WA", ["US-WA"], "act", "9A.32")
    assert d.title == "Revised Code of Washington, Chapter 9A.32 RCW - HOMICIDE (Title 9A RCW)"
    assert d.ext == {"rcw_title": "9A", "rcw_chapter": "9A.32"} and d.ext_module == "us_wa"
    assert d.validity_basis == "attested_at_edition" and d.version_date == d.currency_date == date(2026, 10, 14)
    assert d.authoritative is False and d.language == "en"
    assert [p.num_path[0] for p in d.provisions] == [
        "9A.32.010", "9A.32.020", "9A.32.030", "9A.32.040", "9A.32.050", "9A.32.055", "9A.32.060", "9A.32.070",
    ]  # fmt: skip
    p = _by(d)["sec-9A.32.030"]
    assert p.heading == "Murder in the first degree."
    assert p.text.startswith(
        "(1) A person is guilty of murder in the first degree when:\n(a) With a premeditated intent"
    )
    assert p.text.endswith("(2) Murder in the first degree is a class A felony.")
    assert p.status == "in_force" and p.valid_from is None and p.is_leaf and p.extent == ["US-WA"]
    assert p.context_heading == "Chapter 9A.32 RCW - HOMICIDE"
    # The credit and the Code Reviser's notes are notes, not law text.
    assert (p.note or "").startswith("Source credit: [ 1990 c 200 s 1; 1975-'76 2nd ex.s. c 38 s 3;")
    assert "Notes: Effective date" in (p.note or "") and "Source credit" not in p.text


def test_rcw_cross_references_become_citation_links() -> None:
    p = _by(_wa())["sec-9A.32.020"]
    assert [(r.href, r.relation, r.text) for r in p.refs] == [("/us-wa/rcw/46.61/~sec-46.61.520", "cites", "46.61.520")]
    assert wa_resolve("https://app.leg.wa.gov/RCW/default.aspx?cite=46.61.520") == ("/us-wa/rcw/46.61", "sec-46.61.520")
    assert wa_resolve("http://app.leg.wa.gov/RCW/default.aspx?cite=10.95") == ("/us-wa/rcw/10.95", None)
    assert wa_resolve("https://example.com/RCW/default.aspx?cite=10.95") is None


def test_rcw_parser_refuses_the_wrong_page() -> None:
    with pytest.raises(StateParseError, match="not the page of RCW chapter 9A.04"):
        parse_wa_rcw(_bundle("WA", "9A.04", _gz("wa_rcw_9A.32.html.gz")))


# ---------------------------------------------------------------- Texas


def test_penal_code_homicide_chapter_and_citation_data() -> None:
    d = _tx("PE")
    assert (d.work_uri, d.title, d.number) == ("/us-tx/statutes/PE", "Texas Penal Code", "PE")
    assert d.ext == {"code": "PE", "code_name": "Penal Code"} and d.jurisdiction_id == "US-TX"
    p = _by(d)["sec-19.02"]
    assert p.kind == "section" and p.num_path == ["19.02"] and p.heading == "MURDER."
    assert p.text.startswith('(a) In this section:\n(1) "Adequate cause" means cause that would commonly produce')
    assert p.context_heading == "TITLE 5. OFFENSES AGAINST THE PERSON; CHAPTER 19. CRIMINAL HOMICIDE"
    assert (p.note or "").startswith("Source credit: Acts 1973, 63rd Leg., p. 883, ch. 399, Sec. 1, eff. Jan. 1, 1974.")
    assert "Acts 1973" not in p.text
    assert [r.href for r in p.refs][:2] == ["/us-tx/statutes/HS/~sec-481.1022", "/us-tx/statutes/HS/~sec-481.1123"]


def test_conflicting_amendments_keep_every_text_and_say_so() -> None:
    """Section 32.56 is printed four times (four Acts added a section with that number)."""
    d = _tx("PE")
    by = _by(d)
    eids = [e for e in by if e.startswith("sec-32.56")]
    assert eids == ["sec-32.56", "sec-32.56-v2", "sec-32.56-v3", "sec-32.56-v4"]
    assert len({by[e].heading for e in eids}) == 4  # four different sections
    assert "Ambiguous pinpoint" not in (by["sec-32.56"].note or "")
    assert all("Ambiguous pinpoint" in (by[e].note or "") for e in eids[1:])
    # The Council's notes that precede a section describe that section.
    assert (by["sec-32.56-v2"].note or "").startswith(
        "Source note: Text of section as added by Acts 2025, 89th Leg., R.S., Ch. 814 (S.B. 1281), Sec. 3"
    )


def test_stale_zip_members_are_not_ingested_and_upper_case_members_are() -> None:
    pe = _tx("PE")
    assert not any(p.num_path[0].startswith("22.") for p in pe.provisions)  # pe.22_old.htm is a stale copy
    cr = _tx("CR")
    articles = [p.num_path[0] for p in cr.provisions]
    assert any(a.startswith("102.") for a in articles)  # CR.102.htm is chapter 102 in capitals
    assert not any(a.startswith("17.") for a in articles)  # cr.17-old.htm
    assert {p.kind for p in cr.provisions} == {"article"} and cr.ext["code"] == "CR"


def test_a_section_repealed_in_the_past_is_recorded_without_text() -> None:
    p = _by(_tx("PE"))["sec-46.035"]
    assert p.status == "repealed" and p.text == "" and not p.indexable
    assert "Repealed by Acts 2023, 88th Leg., R.S., Ch. 768 (H.B. 4595), Sec. 17.004, eff. September 1, 2023." in (
        p.note or ""
    )


def test_a_repeal_that_takes_effect_later_leaves_the_text_in_force_until_then() -> None:
    """Articles 5.01-5.xx of the Code of Criminal Procedure are repealed 'eff. April 1, 2027': still law today."""
    cr = _tx("CR")
    p = _by(cr)["art-5.01"]
    assert p.status == "in_force" and p.valid_to == date(2027, 4, 1) and p.valid_from is None
    assert p.text.startswith("(a) Family violence is a serious danger and threat to society")
    assert "Repeal takes effect on 2027-04-01 (stated by the source); the text applies until then." in (p.note or "")
    assert "Source note: Text of article effective until April 01, 2027" in (p.note or "")
    assert not any(q.status == "repealed" and q.num_path[0].startswith("5.") for q in cr.provisions)


def test_council_notes_and_cross_references() -> None:
    cr = _by(_tx("CR"))
    assert cr["art-2A.001"].text.startswith("The following are peace officers:")
    assert tx_resolve("https://statutes.capitol.texas.gov/GetStatute.aspx?Code=GV&Value=30.00010") == (
        "/us-tx/statutes/GV",
        "sec-30.00010",
    )
    assert tx_resolve("https://statutes.capitol.texas.gov/Docs/CR/htm/CR.38.htm#38.23") == (
        "/us-tx/statutes/CR",
        "art-38.23",
    )
    assert tx_resolve("https://statutes.capitol.texas.gov/GetStatute.aspx?Code=ZZ&Value=1") is None


def test_texas_parser_refusals() -> None:
    with pytest.raises(StateParseError, match="not a Texas code"):
        parse_tx_statutes(_bundle("TX", "ZZ", (FIX / "tx_PE_excerpt.htm.zip").read_bytes(), "application/zip"))
    with pytest.raises(StateParseError, match="not a readable zip"):
        parse_tx_statutes(_bundle("TX", "PE", b"not a zip", "application/zip"))
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", "x")
    with pytest.raises(StateParseError, match="no chapter files"):
        parse_tx_statutes(_bundle("TX", "PE", empty.getvalue(), "application/zip"))


# ---------------------------------------------------------------- Oregon


def test_ors_chapter_structure_and_repealed_sections() -> None:
    d = _or("12")
    assert (d.work_uri, d.number) == ("/us-or/ors/12", "12")
    assert d.title == "Oregon Revised Statutes, Chapter 12 - Limitations of Actions and Suits (2025 Edition)"
    assert d.ext == {"ors_chapter": "12", "edition_year": 2025} and d.text_origin == "official_conversion"
    assert d.expression_uri == "/us-or/ors/12/en@2026-10-14/ors-2025-edition"
    by = _by(d)
    p = by["sec-12.010"]
    assert p.heading == "Time of commencing actions." and p.text.startswith("Actions shall only be commenced")
    assert p.note == "Source credit: [Amended by 1979 c.284 §43]" and "[" not in p.text
    for eid in ("sec-12.030", "sec-12.133", "sec-12.260"):
        assert by[eid].status == "repealed" and by[eid].text == "" and not by[eid].indexable
    assert sum(p.status == "repealed" for p in d.provisions) == 3
    assert not any("OFFENSES" in p.text for p in d.provisions)


def test_ors_group_headings_are_not_law_text_and_notices_are_outstanding_changes() -> None:
    d = _or("285A")
    assert len(d.outstanding_changes) == 2
    assert d.outstanding_changes[0]["description"].startswith("New sections of law were added by legislative action")
    assert d.ext["ors_chapter"] == "285A" and d.work_uri == "/us-or/ors/285A"
    assert all(p.text == "" for p in d.provisions if p.status == "repealed")
    assert not any(p.text.isupper() for p in d.provisions if p.text)


def test_ors_headings_forms_and_notes_on_a_minimal_file() -> None:
    """Modelled on the structure of the real files (Word HTML): not a copy of any chapter."""

    def p(*runs: str) -> str:
        return "<p class=MsoNormal>" + "".join(runs) + "</p>\n"

    def b(text: str) -> str:
        return f"<b><span>{text}</span></b>"

    def t(text: str) -> str:
        return f"<span>{text}</span>"

    html = (
        "<html><head><title>Oregon Revised Statutes</title></head><body>"
        + p(t("Chapter 3 — Circuit Courts"))
        + p(t("2025 EDITION"))
        + p(t("3.010 Court established"))  # table of contents line (not bold)
        + p(b("3.010 Court established."), t(" The court is established. [1971 c.1 §1]"))
        + p(b("3.020"), t(" [Repealed by 1981 c.215 §8]"))
        + p(t("FAMILY LAW"))
        + p(t("(Family Court Departments)"))
        + p(b("3.030 Form of oath."), t(" The oath shall read:"))
        + p(t("OATH OF OFFICE"))
        + p(t("I, ____, swear. [1971 c.1 §2]"))
        + p(t("Note: Section 9, chapter 1, Oregon Laws 2025, provides: Sec. 9. This section is repealed."))
        + p(t("Sec. 10. A later uncodified section."))
        + "</body></html>"
    )
    d = parse_or_ors(_bundle("OR", "3", html.encode("cp1252")))[0]
    by = _by(d)
    assert list(by) == ["sec-3.010", "sec-3.020", "sec-3.030"]
    assert (
        by["sec-3.010"].text == "The court is established." and by["sec-3.010"].note == "Source credit: [1971 c.1 §1]"
    )
    # The two heading lines belong to no section: 3.020 stays an empty repealed placeholder.
    assert by["sec-3.020"].status == "repealed" and by["sec-3.020"].text == ""
    # An upper-case line inside a section is text; the Note paragraphs (and what follows) are the note.
    assert by["sec-3.030"].text == "The oath shall read:\nOATH OF OFFICE\nI, ____, swear."
    assert (by["sec-3.030"].note or "").startswith(
        "Source credit: [1971 c.1 §2] Source note: Note: Section 9, chapter 1"
    )
    assert "A later uncodified section." in (by["sec-3.030"].note or "")


def test_ors_parser_refuses_the_wrong_chapter() -> None:
    with pytest.raises(StateParseError, match="not the file of ORS chapter 13"):
        parse_or_ors(_bundle("OR", "13", _gz("or_ors012.html.gz")))


# ---------------------------------------------------------------- Utah


def test_utah_title_structure_text_labels_and_history() -> None:
    d = _ut("3", "ut_title3.xml.gz")
    assert (d.work_uri, d.number, d.title) == (
        "/us-ut/code/3",
        "3",
        "Utah Code, Title 3 - Uniform Agricultural Cooperative Association Act",
    )
    assert d.ext == {"title": "3", "title_name": "Uniform Agricultural Cooperative Association Act"}
    p = _by(d)["sec-3-1-1"]
    assert p.heading == "Declaration of policy." and p.num_path == ["3-1-1"]
    assert p.text.startswith("It is the declared policy of this state, as one means of improving")
    assert p.note == "Source history: Amended by Chapter 378, 2010 General Session"
    assert p.context_heading == "Chapter 3-1. General Provisions Relating to Agricultural Cooperative Associations"
    assert d.validity_basis == "attested_at_edition" and all(x.valid_from is None for x in d.provisions)


def test_utah_subsection_labels_come_from_the_number_attribute() -> None:
    d = _ut("76", "ut_title76_excerpt.xml.gz")
    p = _by(d)["sec-76-5-203"]
    assert p.heading.startswith("Murder -- Penalties")  # type: ignore[union-attr]
    lines = p.text.split("\n")
    assert lines[0] == '(1)(a) As used in this section, "predicate offense" means:'
    assert lines[1] == "(i) a clandestine drug lab violation under Section 76-18-506;"
    assert [r.href for r in p.refs][0] == "/us-ut/code/76/~sec-76-18-506"
    assert d.ext["compiled_effective"] == "2026-07-01"


def test_utah_future_version_has_the_dates_the_source_states() -> None:
    d = _ut("76", "ut_title76_excerpt.xml.gz")
    versions = [p for p in d.provisions if p.eid == "sec-76-2-306"]
    assert len(versions) == 2
    current, future = versions
    assert (current.valid_from, current.valid_to) == (None, date(2027, 1, 1))
    assert (future.valid_from, future.valid_to) == (date(2027, 1, 1), None)
    assert "his unawareness is immaterial" in current.text and "the actor's unawareness is immaterial" in future.text
    assert "Version ends 2027-01-01 (stated by the source)." in (current.note or "")
    assert "Amended by Chapter 445, 2026 General Session" in (future.note or "")
    assert not any("Ambiguous pinpoint" in (p.note or "") for p in d.provisions)  # dated versions are versions


def test_utah_markup_on_a_minimal_title() -> None:
    """Modelled on the structure of the real files: labels that carry down, tables, encoded characters, references."""
    xml = (
        '<title number="9"><effdate>7/1/2026</effdate><catchline>Test Title</catchline>'
        '<chapter number="9-1"><catchline>First</catchline>'
        '<section number="9-1-101"><contingent type="CE"/><histories><history>Enacted by Chapter '
        '<modchap sess="2026GS">7</modchap>, 2026 General Session</history></histories>'
        "<catchline>Rules.</catchline><tab/>In this section:"
        '<subsection number="9-1-101(1)"><subsection number="9-1-101(1)(a)">Text under <xref depth="3" '
        'refnumber="9-2-5.1">9-2-5.1</xref> and <xref depth="2" refnumber="9-3-1">Part 1</xref>:<eol/>'
        "<tbl><row><cell/><cell>Year</cell><cell>Rate</cell></row><row><cell/><cell>First</cell><cell>80%</cell></row></tbl>"
        '</subsection><subsection number="9-1-101(1)(b)">See Utah Code Ann. <char set="4" char="6"/>'
        "59-2-103.</subsection></subsection></section></chapter></title>"
    )
    d = parse_ut_code(_bundle("UT", "9", xml.encode(), "text/xml"))[0]
    (p,) = d.provisions
    assert p.text.split("\n") == [
        "In this section:",
        "(1)(a) Text under 9-2-5.1 and Part 1:",
        "Year Rate",
        "First 80%",
        "(b) See Utah Code Ann. [char 4/6]59-2-103.",
    ]
    assert [r.href for r in p.refs] == ["/us-ut/code/9/~sec-9-2-5.1", "/us-ut/code/9"]  # a part is not a section
    assert (p.note or "").startswith("Source history: Enacted by Chapter 7, 2026 General Session")
    assert "Contingent effectiveness" in (p.note or "") and "(type CE)" in (p.note or "")


def test_utah_oddities_found_in_the_full_code() -> None:
    """The XML writes section numbers of title 26B in lower case and has xref depths such as "null"."""
    xml = (
        '<title number="26B"><catchline>Health</catchline><chapter number="26B-2"><catchline>X</catchline>'
        '<section number="26b-2-902"><catchline>Cross.</catchline>See <xref depth="null" refnumber="26B-2-901">Section '
        '26B-2-901</xref> and <xref depth="41-1a-124" refnumber="41-1a-124">41-1a-124</xref>.</section>'
        "</chapter></title>"
    )
    d = parse_ut_code(_bundle("UT", "26B", xml.encode(), "text/xml"))[0]
    (p,) = d.provisions
    assert p.num_path == ["26b-2-902"] and p.eid == "sec-26b-2-902" and d.work_uri == "/us-ut/code/26B"
    assert [r.href for r in p.refs] == ["/us-ut/code/26B/~sec-26B-2-901", "/us-ut/code/41/~sec-41-1a-124"]


def test_utah_parser_refuses_the_wrong_title() -> None:
    with pytest.raises(StateParseError, match="not the XML of Utah Code title 4"):
        parse_ut_code(_bundle("UT", "4", _gz("ut_title3.xml.gz"), "text/xml"))
    with pytest.raises(StateParseError, match="not readable XML"):
        parse_ut_code(_bundle("UT", "3", b"<title number='3'>", "text/xml"))


# ---------------------------------------------------------------- citations


def _cite(module: str, **kw: Any) -> str:
    base: dict[str, Any] = dict(
        instrument_title="x", instrument_type="act", jurisdiction_id="US", top_kind="section", language="en"
    )
    base.update(kw)
    return get_module(module).provision_citation(ProvisionCitationInput(**base))


def test_citations_follow_each_states_convention() -> None:
    assert _cite("us_wa", num_path=["9A.32.030"], ext={"rcw_chapter": "9A.32"}) == "RCW 9A.32.030"
    assert _cite("us_wa", num_path=["9A.32.030", "1", "(a)"]) == "RCW 9A.32.030(1)(a)"
    assert _cite("us_wa", num_path=[], ext={"rcw_chapter": "9A.32"}) == "RCW ch. 9A.32"
    assert _cite("us_tx", num_path=["19.02"], ext={"code": "PE"}) == "Tex. Penal Code § 19.02"
    assert _cite("us_tx", num_path=["552.001"], ext={"code": "GV"}) == "Tex. Gov't Code § 552.001"
    assert (
        _cite("us_tx", num_path=["38.23"], top_kind="article", ext={"code": "CR"}) == "Tex. Code Crim. Proc. art. 38.23"
    )
    assert _cite("us_tx", num_path=["1"], ext={"code": "XX", "code_name": "Odd Code"}) == "Tex. Odd Code § 1"
    assert _cite("us_or", num_path=["163.115", "1", "a"], ext={"ors_chapter": "163"}) == "ORS 163.115(1)(a)"
    assert _cite("us_ut", num_path=["76-5-203", "(2)"], ext={"title": "76"}) == "Utah Code § 76-5-203(2)"
    assert set(CODES) == set(__import__("ulis.jurisdictions.modules.us_tx", fromlist=["CITATIONS"]).CITATIONS)


# ---------------------------------------------------------------- connectors


def _seed(cache: Path, url: str, data: bytes | None, content_type: str = "text/html", key: str | None = None) -> None:
    host = url.split("/")[2]
    http = PoliteHttp(cache, UA, allowed_hosts={host}, offline=True)
    path = http.cache_path(url, key)
    meta_path = path.with_name(path.name + ".meta.json")
    if data is None:
        PoliteHttp._store(path, meta_path, b"", {"url": url, "status": 404})
    else:
        meta = {"url": url, "final_url": url, "status": 200, "content_type": content_type, "retrieved_at": RETRIEVED}
        PoliteHttp._store(path, meta_path, data, meta)


def _robots(cache: Path, host: str, text: str | None) -> None:
    _seed(cache, f"https://{host}/robots.txt", text.encode() if text is not None else None, "text/plain", "robots.txt")


def _manifest(tmp_path: Path, source: str, **lists: Any) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 1, "source_id": source, "user_agent": UA, **lists}))
    return path


def _sleeps() -> tuple[list[float], Any]:
    log: list[float] = []
    return log, log.append


def test_washington_connector_offline_from_a_seeded_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _robots(cache, "app.leg.wa.gov", None)
    page = _gz("wa_rcw_9A.32.html.gz")
    _seed(cache, wa_url("9A.32"), page)
    _seed(cache, wa_url("9A.99"), None)
    _seed(cache, wa_url("9A.04"), b"<html>an error page</html>")
    manifest = _manifest(tmp_path, "us-wa-rcw", chapters=["9A.32", "9A.99", "9A.04"])
    connector = WaRcwConnector(manifest, "us-wa-rcw", cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["us-wa-rcw:9A.32"] and docs[0].media_type == "application/json"
    assert docs[0].retrieved_at == datetime(2026, 10, 14, 15, 30, tzinfo=UTC)
    assert read_bundle(docs[0].content, "WA").body_bytes == page  # exactly as received
    assert hashlib.sha256(page).hexdigest() in docs[0].content.decode()
    assert len(parse_wa_rcw(docs[0].content)[0].provisions) == 8
    assert any("9A.99" in e and "not found (404)" in e for e in connector.fetch_errors)
    assert any("9A.04" in e and "not the page of chapter 9A.04" in e for e in connector.fetch_errors)


def test_connector_refuses_what_robots_txt_disallows_and_works_offline_only_from_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _robots(cache, "app.leg.wa.gov", "User-agent: *\nDisallow: /rcw/\n")
    _seed(cache, wa_url("9A.32"), _gz("wa_rcw_9A.32.html.gz"))
    connector = WaRcwConnector(_manifest(tmp_path, "us-wa-rcw", chapters=["9A.32"]), "us-wa-rcw", cache, offline=True)
    with pytest.raises(FetchError, match="disallowed by robots.txt"):
        list(connector.fetch())
    empty = WaRcwConnector(
        _manifest(tmp_path, "us-wa-rcw", chapters=["9A.32"]), "us-wa-rcw", tmp_path / "nothing", offline=True
    )
    with pytest.raises(FetchError, match="offline and not cached"):
        list(empty.fetch())


def test_manifest_values_are_validated(tmp_path: Path) -> None:
    for bad in (["9A.32/../x"], [], ["9A.32", "9A.32"]):
        with pytest.raises(ManifestError):
            WaRcwConnector(_manifest(tmp_path, "us-wa-rcw", chapters=bad), "us-wa-rcw", tmp_path)._manifest()
    with pytest.raises(ManifestError, match="mismatch"):
        WaRcwConnector(_manifest(tmp_path, "other", chapters=["9A.32"]), "us-wa-rcw", tmp_path)._manifest()
    with pytest.raises(ManifestError):
        wa_url("9A.32&x=1")
    with pytest.raises(ManifestError):
        zip_url("../PE")
    with pytest.raises(ManifestError):
        or_url("16 3")


def test_texas_connector_wraps_the_zip_exactly(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _robots(cache, "tcss.legis.texas.gov", None)
    data = (FIX / "tx_PE_excerpt.htm.zip").read_bytes()
    _seed(cache, zip_url("PE"), data, "application/x-zip-compressed")
    _seed(cache, zip_url("AG"), b"<html>not a zip</html>")
    manifest = _manifest(
        tmp_path, "us-tx-statutes", codes=[{"code": "PE", "name": "Penal Code"}, {"code": "AG", "name": "x"}]
    )
    connector = TxStatutesConnector(manifest, "us-tx-statutes", cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["us-tx-statutes:PE"]
    assert read_bundle(docs[0].content, "TX").body_bytes == data
    assert connector.fetch_errors and "not a zip archive" in connector.fetch_errors[0]


def test_oregon_missing_candidates_are_not_errors(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _robots(cache, "www.oregonlegislature.gov", "User-agent: MS Search 6.0 Robot\nDisallow:\n")
    _seed(cache, or_url("12"), _gz("or_ors012.html.gz"))
    _seed(cache, or_url("13"), None)
    assert or_url("285A").endswith("/bills_laws/ors/ors285A.html") and or_url("12").endswith("/ors/ors012.html")
    connector = OrOrsConnector(
        _manifest(tmp_path, "us-or-ors", chapters=["12", "13"]), "us-or-ors", cache, offline=True
    )
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["us-or-ors:12"]
    assert connector.fetch_errors == [] and len(connector.not_found) == 1 and "ors013.html" in connector.not_found[0]


def test_utah_connector_finds_the_current_file_names_and_waits_ten_seconds(tmp_path: Path) -> None:
    code_html, index_html = _gz("ut_code.html.gz").decode(), _gz("ut_code_index.html.gz").decode()
    assert parse_index_version(code_html) == "C_1800010118000101"
    versions = parse_title_versions(index_html)
    assert len(versions) == 96 and versions["3"] == "C3_1800010118000101" and versions["76"].startswith("C76_")
    assert title_url("3", versions["3"]) == "https://le.utah.gov/xcode/Title3/C3_1800010118000101.xml"
    cache = tmp_path / "cache"
    robots = "User-agent: *\nCrawl-delay: 10\nDisallow: /templates/\nDisallow: /asp/\n"
    _robots(cache, "le.utah.gov", robots)
    _seed(cache, "https://le.utah.gov/xcode/code.html", code_html.encode())
    _seed(cache, "https://le.utah.gov/xcode/C_1800010118000101.html", index_html.encode())
    xml = _gz("ut_title3.xml.gz")
    _seed(cache, title_url("3", versions["3"]), xml, "text/xml")
    manifest = _manifest(tmp_path, "us-ut-code", titles=["3", "99"])
    connector = UtCodeConnector(manifest, "us-ut-code", cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["us-ut-code:3"] and read_bundle(docs[0].content, "UT").body_bytes == xml
    assert connector.http is not None and connector.http.crawl_delay == 10
    assert connector.fetch_errors == ["us-ut-code:99: title not listed in the current index"]


@pytest.mark.parametrize(
    ("source", "cls", "key", "count"),
    [
        ("us-wa-rcw", WaRcwConnector, "chapters", 2785),
        ("us-tx-statutes", TxStatutesConnector, "codes", 30),
        ("us-or-ors", OrOrsConnector, "chapters", 689),
        ("us-ut-code", UtCodeConnector, "titles", 96),
    ],
)
def test_committed_manifests_pass_the_connectors_own_validation(source: str, cls: Any, key: str, count: int) -> None:
    path = REPO_ROOT / "config" / "manifests" / f"{source}.yaml"
    manifest = cls(path, source, Path("/nonexistent"))._manifest()
    assert len(manifest[key]) == count


def test_committed_manifest_contents() -> None:
    wa = yaml.safe_load((REPO_ROOT / "config/manifests/us-wa-rcw.yaml").read_text())["chapters"]
    assert {"1.04", "9A.32", "28A.150", "43.21C", "46.61", "91.08"} <= set(wa) and len(set(wa)) == len(wa)
    tx = yaml.safe_load((REPO_ROOT / "config/manifests/us-tx-statutes.yaml").read_text())["codes"]
    assert {c["code"]: c["name"] for c in tx} == CODES  # the manifest and the parser name the same codes
    ors = yaml.safe_load((REPO_ROOT / "config/manifests/us-or-ors.yaml").read_text())["chapters"]
    assert {"1", "12", "163", "285A", "838"} <= set(ors)
    ut = yaml.safe_load((REPO_ROOT / "config/manifests/us-ut-code.yaml").read_text())["titles"]
    assert {"3", "76", "78B", "81"} <= set(ut)


# ---------------------------------------------------------------- catalog


STATES = {"US-WA": "us-wa-rcw", "US-TX": "us-tx-statutes", "US-OR": "us-or-ors", "US-UT": "us-ut-code"}


def test_state_profiles_are_children_of_the_united_states(catalog: Catalog) -> None:
    reg = catalog.jurisdictions
    for state, source in STATES.items():
        profile = reg.get(state)
        assert profile.parent == "US" and profile.kind == "constituent_unit" and not profile.fictional
        assert profile.verification.status == "draft_unverified" and profile.coverage.corpus_status == "pilot"
        assert profile.publication.official_sources == [source]
        assert reg.chain(state) == [state, "US", "INT"] and reg.sovereign(state) == "US"
        assert profile.citation.module == f"us_{state[3:].lower()}" and profile.legal_system.structure == "constituent"
        assert all(c.appeals_to == ["US-SC"] for c in profile.courts)
        assert catalog.sources.get(source).connector in CONNECTORS and catalog.sources.get(source).parser in PARSERS
        licence_gate(catalog.sources.get(source))
    assert set(STATES) <= set(reg.descendants("US"))
    # A state's law extends to that state only; federal law extends to every state.
    assert reg.extent_covers(["US-WA"], "US-WA") and not reg.extent_covers(["US-WA"], "US-TX")
    assert not reg.extent_covers(["US-WA"], "US") and reg.extent_covers(["US"], "US-WA")


PLANNED = {
    "us-ca-leginfo": ("planned", "Cloudflare"),
    "us-ny-laws": ("planned", "API key"),
    "us-co-crs": ("planned", "LexisNexis"),
    "us-il-ilcs": ("planned", "TLS"),
    "us-fl-statutes": ("planned", "Disallow: /employees/"),
    "us-oh-orc": ("rejected", "Disallow: /"),
    "us-mi-mcl": ("planned", "403"),
    "us-mn-statutes": ("planned", "Disallow"),
    "us-va-code": ("planned", "Copyright Commonwealth of Virginia"),
    "us-pa-consolidated": ("planned", "UserWay"),
}


@pytest.mark.parametrize("source_id", sorted(PLANNED))
def test_other_states_are_recorded_with_findings_and_the_licence_gate_refuses_them(
    catalog: Catalog, source_id: str
) -> None:
    status, evidence = PLANNED[source_id]
    source = catalog.sources.get(source_id)
    assert source.status == status and source.connector is None and source.parser is None
    assert evidence.lower() in (source.licence.reviewed_by or "").lower().replace("(c) ", "").replace("(c)", "")
    assert source.licence.reviewed_on == date(2026, 10, 14) and "Next step" in (source.licence.notes or "")
    assert source.licence.review_status in ("pending_review", "rejected")
    assert source.licence.automated_processing in ("unknown", "not_allowed")
    assert source_id not in CONNECTORS
    with pytest.raises(LicenceNotApproved):
        licence_gate(source)


# ---------------------------------------------------------------- PostgreSQL: state versus federal scope


class _Docs:
    def __init__(self, items: list[tuple[str, str, bytes, str]]):
        self.items = items

    def fetch(self) -> Iterator[RawDocument]:
        for external_id, uri, content, media_type in self.items:
            yield RawDocument(external_id, uri, media_type, content, datetime(2026, 10, 14, 15, 30, tzinfo=UTC), {})


def _batch(source: str) -> _Docs:
    wa = _bundle("WA", "9A.32", _gz("wa_rcw_9A.32.html.gz"))
    tx = _bundle("TX", "PE", (FIX / "tx_PE_excerpt.htm.zip").read_bytes(), "application/zip")
    ors = _bundle("OR", "12", _gz("or_ors012.html.gz"))
    ut = _bundle("UT", "76", _gz("ut_title76_excerpt.xml.gz"), "text/xml")
    return _Docs(
        {
            "us-wa-rcw": [("us-wa-rcw:9A.32", wa_url("9A.32"), wa, "application/json")],
            "us-tx-statutes": [("us-tx-statutes:PE", zip_url("PE"), tx, "application/json")],
            "us-or-ors": [("us-or-ors:12", or_url("12"), ors, "application/json")],
            "us-ut-code": [("us-ut-code:76", title_url("76", "C76_2026050620260701"), ut, "application/json")],
        }[source]
    )


class _Federal:
    def fetch(self) -> Iterator[RawDocument]:
        yield RawDocument(
            "uscode-house:usc17@119-120",
            "https://uscode.house.gov/x",
            "application/zip",
            (REPO_ROOT / "tests/fixtures/uscode_house/xml_usc17@119-120.zip").read_bytes(),
            datetime(2026, 10, 14, 15, 30, tzinfo=UTC),
            {},
        )


@pytest.fixture(scope="module")
def states(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> Iterator[tuple[Session, dict[str, IngestionReport]]]:
    """The four states and one federal title ingested in one transaction that is never committed."""
    session = session_factory()
    reports = {
        s: Ingestor(session, catalog, store).ingest_source(s, actor="pytest", connector=_batch(s))
        for s in STATES.values()
    }
    reports["us-uscode-house"] = Ingestor(session, catalog, store).ingest_source(
        "us-uscode-house", actor="pytest", connector=_Federal()
    )
    session.flush()
    try:
        yield session, reports
    finally:
        session.rollback()
        session.close()


def _find(session: Session, catalog: Catalog, jurisdiction: str, q: str, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(q=q, jurisdiction=jurisdiction, **kw), today=date(2026, 10, 14))


def _docs(res: dict[str, Any]) -> set[str]:
    return {h["document"]["uri"] for h in res["results"]}


@pytest.mark.db
def test_the_four_states_ingest_cleanly(states: tuple[Session, dict[str, IngestionReport]]) -> None:
    _, reports = states
    for source, report in reports.items():
        assert report.errors == [] and report.conflicts == [] and report.documents_new == 1, source
    assert reports["us-wa-rcw"].provision_versions_created == 8
    assert reports["us-ut-code"].provision_versions_created == 44  # 43 sections, sec-76-2-306 in two versions
    assert reports["us-tx-statutes"].citation_links_created > 0


@pytest.mark.db
@pytest.mark.parametrize(
    ("state", "q", "uri", "citation"),
    [
        (
            "US-WA",
            "murder in the first degree premeditated intent cause the death of another person",
            "/us-wa/rcw/9A.32",
            "RCW 9A.32.030",
        ),
        (
            "US-TX",
            "adequate cause sudden passion arising out of provocation murder affirmative defense",
            "/us-tx/statutes/PE",
            "Tex. Penal Code § 19.02",
        ),
        ("US-OR", "actions shall only be commenced within the periods prescribed", "/us-or/ors/12", "ORS 12.010"),
        (
            "US-UT",
            "voluntary intoxication is not a defense to a criminal charge",
            "/us-ut/code/76",
            "Utah Code § 76-2-306",
        ),
    ],
)
def test_search_finds_the_provision_with_its_citation_and_limitations(
    states: tuple[Session, dict[str, IngestionReport]], catalog: Catalog, state: str, q: str, uri: str, citation: str
) -> None:
    session, _ = states
    hit = next(h for h in _find(session, catalog, state, q)["results"] if h["document"]["uri"] == uri)
    assert hit["citation"] == citation
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {"PROFILE_UNVERIFIED", "ATTESTED_ONLY_AT"} <= codes
    assert hit["version"]["currency_date"] == "2026-10-14"


@pytest.mark.db
def test_state_law_applies_only_inside_the_state_and_federal_law_applies_in_every_state(
    states: tuple[Session, dict[str, IngestionReport]], catalog: Catalog
) -> None:
    session, _ = states
    murder = "murder in the first degree person causes the death of another"
    assert {u for u in _docs(_find(session, catalog, "US-WA", murder)) if u.startswith("/us-")} == {"/us-wa/rcw/9A.32"}
    assert {u for u in _docs(_find(session, catalog, "US-TX", murder)) if u.startswith("/us-")} == {
        "/us-tx/statutes/PE"
    }
    # The federal profile does not see any state's law.
    assert not any(
        u.startswith(("/us-wa", "/us-tx", "/us-or", "/us-ut")) for u in _docs(_find(session, catalog, "US", murder))
    )
    # Federal law (17 U.S.C. 107) is found from inside a state through the parent chain, and from no other state's law.
    fair = "fair use of a copyrighted work criticism comment news reporting teaching scholarship research"
    for state in ("US-WA", "US-TX", "US-OR", "US-UT"):
        hits = [h for h in _find(session, catalog, state, fair)["results"] if h["document"]["uri"] == "/us/usc/t17"]
        assert hits and hits[0]["citation"] == "17 U.S.C. § 107", state
    # A state's law is foreign to another state: found only when foreign law is asked for.
    assert "/us-or/ors/12" not in _docs(
        _find(session, catalog, "US-WA", "actions shall only be commenced within the periods prescribed")
    )
    foreign = _find(
        session, catalog, "US-WA", "actions shall only be commenced within the periods prescribed", include_foreign=True
    )
    assert "/us-or/ors/12" in _docs(foreign)


@pytest.mark.db
def test_utah_future_version_follows_the_dates_the_source_states(
    states: tuple[Session, dict[str, IngestionReport]], catalog: Catalog
) -> None:
    session, _ = states
    now = provision_at(session, catalog, "/us-ut/code/76/~sec-76-2-306", date(2026, 10, 14))
    assert (
        now["status_on_date"] == "in_force_date_unverified" and now["provision"]["citation"] == "Utah Code § 76-2-306"
    )
    assert "his unawareness is immaterial" in now["version"]["text"]
    later = provision_at(session, catalog, "/us-ut/code/76/~sec-76-2-306", date(2027, 2, 1))
    assert later["status_on_date"] == "in_force" and later["version"]["valid_from"] == "2027-01-01"
    assert "the actor's unawareness is immaterial" in later["version"]["text"]
    # Search as of today shows the wording in force today only.
    res = _find(session, catalog, "US-UT", "voluntary intoxication is not a defense to a criminal charge")
    texts = [
        h["text"]
        for h in res["results"]
        if h["document"]["uri"] == "/us-ut/code/76" and h["score"]["matched_via"] == "query"
    ]
    assert texts and all("his unawareness" in t for t in texts)


@pytest.mark.db
def test_repealed_oregon_section_is_recorded_without_text(
    states: tuple[Session, dict[str, IngestionReport]], catalog: Catalog
) -> None:
    session, _ = states
    res = provision_at(session, catalog, "/us-or/ors/12/~sec-12.030", date(2026, 10, 14))
    assert res["provision"]["citation"] == "ORS 12.030"
    # An undated repealed section has no version "on a date" (the source states none): it is on the timeline only.
    assert res["version"] is None and res["status_on_date"] == "no_version"
    assert [(v["status"], v["text"]) for v in res["timeline"]] == [("repealed", "")]
    assert res["timeline"][0]["note"] == "Source credit: [Repealed by 1973 c.731 §2]"
