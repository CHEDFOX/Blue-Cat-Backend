"""United States Code (OLRC USLM XML): parser, citations, connector, PostgreSQL.

Fixtures are three whole titles exactly as downloaded from uscode.house.gov on 2026-10-14 at release point 119-120
(see tests/fixtures/uscode_house/README.md): title 1 and title 17 (positive law) and title 27 (not positive law);
and, at release point 119-121 (2026-10-18), the appendix to title 18 (the Federal Rules of Criminal Procedure, the
Classified Information Procedures Act, the Interstate Agreement on Detainers), the eliminated appendix to title 50 and
the download page itself.
"""

from __future__ import annotations

import gzip
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
from ulis.sources.connectors.uscode_house import (
    UscodeHouseConnector,
    parse_download_page,
    release_date_of,
    title_url,
)
from ulis.sources.normalized import ParsedDocument, ParseSkipped
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
# the appendices, at release point 119-121 (the connector's metadata for them is APPENDIX_META)
SHA_APPENDIX = {
    18: "c3e6999fc93eca07078993d0eac3664e1409bf4305e9b82c2fb83c43d346e64c",
    50: "af28e101684678db9dc727e45a967a48e5414a312090c97f9aa83f32726309ed",
}
APPENDIX_META = {
    "release_point": "119-121",
    "release_date": "2026-10-09",
    "current_through": "Public Law 119-121 (10/09/2026)",
    "appendix": True,
}
HOUSE = "http://xml.house.gov/schemas/uslm/1.0"


def _zip(n: int) -> bytes:
    return (FIX / f"xml_usc{n:02d}@119-120.zip").read_bytes()


def _zip_appendix(n: int) -> bytes:
    return (FIX / f"xml_usc{n:02d}a@119-121.zip").read_bytes()


def _doc(n: int) -> ParsedDocument:
    docs = parse_uslm(_zip(n))
    assert len(docs) == 1
    return docs[0]


def _appendix(n: int) -> dict[str, ParsedDocument]:
    """The instruments of the appendix to title ``n``, by work URI, parsed with the connector's metadata."""
    return {d.work_uri: d for d in parse_uslm(_zip_appendix(n), {**APPENDIX_META, "title": n})}


def _by_eid(doc: ParsedDocument) -> dict[str, Any]:
    return {p.eid: p for p in doc.provisions}


def _xml(n: int) -> str:
    with zipfile.ZipFile(io.BytesIO(_zip(n))) as archive:
        return archive.read(f"usc{n:02d}.xml").decode("utf-8")


def test_fixtures_are_the_downloads() -> None:
    for n, sha in SHA.items():
        assert hashlib.sha256(_zip(n)).hexdigest() == sha
    for n, sha in SHA_APPENDIX.items():
        assert hashlib.sha256(_zip_appendix(n)).hexdigest() == sha


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


# ---------------------------------------------------------------- parser: the release point's date


def test_release_point_date_comes_from_the_manifest_through_the_connector() -> None:
    """A new release point needs no code change: the manifest's date (from the download page) dates the text."""
    xml = _xml(1).replace("Online@119-120", "Online@119-999").encode()
    stated = {
        "release_point": "119-999",
        "release_date": "2026-12-31",
        "current_through": "Public Law 119-999 (12/31/2026)",
    }
    (d,) = parse_uslm(xml, {"title": 1, "appendix": False, **stated})
    assert d.version_date == d.currency_date == date(2026, 12, 31)
    assert d.expression_uri == "/us/usc/t1/en@2026-12-31/release-point-119-999"
    assert d.ext["release_point"] == "119-999" and d.ext["compiled_through"] == "Public Law 119-999"
    assert "release point 119-999" in d.title
    # the date may also be given as a date object (the connector passes ISO text; yaml may give a date)
    (again,) = parse_uslm(xml, {"release_point": "119-999", "release_date": date(2026, 12, 31)})
    assert again.version_date == date(2026, 12, 31)


def test_recorded_release_points_are_a_fallback_and_a_consistency_check() -> None:
    assert {"119-120": date(2026, 10, 7), "119-121": date(2026, 10, 9)} == uslm.USC_RELEASE_POINT_DATES
    # no metadata (fixtures, older stored documents): the table dates the text
    assert _doc(1).version_date == date(2026, 10, 7)
    # metadata that agrees with the table passes; a different date for a recorded release point is refused
    (d,) = parse_uslm(_zip(1), {"release_point": "119-120", "release_date": "2026-10-07"})
    assert d.version_date == date(2026, 10, 7)
    with pytest.raises(UslmParseError, match="dated 2026-10-08 by the manifest but 2026-10-07"):
        parse_uslm(_zip(1), {"release_point": "119-120", "release_date": "2026-10-08"})
    with pytest.raises(UslmParseError, match="archive is release point 119-120 but the manifest says 119-121"):
        parse_uslm(_zip(1), {"release_point": "119-121", "release_date": "2026-10-09"})
    with pytest.raises(UslmParseError, match="is not a date"):
        parse_uslm(_zip(1), {"release_point": "119-120", "release_date": "soon"})
    # what the manifest says about the archive must match the file: title number, main title or appendix
    with pytest.raises(UslmParseError, match="manifest lists title 2"):
        parse_uslm(_zip(1), {"title": 2})
    with pytest.raises(UslmParseError, match="manifest lists an appendix"):
        parse_uslm(_zip(1), {"title": 1, "appendix": True})
    with pytest.raises(UslmParseError, match="manifest lists a main title"):
        parse_uslm(
            _zip_appendix(18),
            {"title": 18, "appendix": False, **{k: v for k, v in APPENDIX_META.items() if k != "appendix"}},
        )


def test_release_point_without_a_recorded_date_is_refused() -> None:
    xml = _xml(1).replace("Online@119-120", "Online@119-999")
    with pytest.raises(UslmParseError, match="no recorded date"):
        parse_uslm(xml.encode())
    with pytest.raises(UslmParseError, match="no recorded date"):
        parse_uslm(xml.encode(), {"release_point": "119-999"})  # the manifest names it but does not date it


# ---------------------------------------------------------------- parser: the appendices


def test_appendix_to_title_18_is_split_into_its_rules_and_acts() -> None:
    docs = parse_uslm(_zip_appendix(18), {**APPENDIX_META, "title": 18})
    assert [(d.instrument_type, d.work_uri) for d in docs] == [
        ("act", "/us/usc/t18a/unlawful-possession-or-receipt-of-firearms"),
        ("act", "/us/usc/t18a/pl/91/538"),
        ("act", "/us/usc/t18a/pl/96/456"),
        ("court_rules", "/us/usc/t18a/courtRules/Crim"),
    ]
    crim = docs[3]
    assert crim.title == (
        "Federal Rules of Criminal Procedure (Appendix to Title 18, United States Code; court rules, not enacted as "
        "part of the title; release point 119-121)"
    )
    assert crim.work_title == "Federal Rules of Criminal Procedure (Appendix to Title 18, United States Code)"
    assert crim.short_title == "Federal Rules of Criminal Procedure" and crim.number is None
    assert crim.expression_uri == "/us/usc/t18a/courtRules/Crim/en@2026-10-09/release-point-119-121"
    assert crim.version_date == crim.currency_date == date(2026, 10, 9) and crim.work_date is None
    assert crim.validity_basis == "attested_at_edition" and crim.consolidation_kind == "us_code_release_point"
    assert crim.text_origin == "official_consolidation" and crim.authoritative is False
    assert crim.ext == {
        "usc_title": 18,
        "usc_appendix": True,
        "appendix_unit": "court_rules",
        "appendix_heading": "FEDERAL RULES OF CRIMINAL PROCEDURE",
        "positive_law": False,
        "release_point": "119-121",
        "compiled_through": "Public Law 119-121",
        "court_rules": "Crim",
    }
    cipa = docs[2]
    assert cipa.title.startswith(
        "Classified Information Procedures Act (Appendix to Title 18, United States Code; Act "
    )
    assert "not enacted as part of the title; release point 119-121)" in cipa.title
    assert cipa.ext == {
        "usc_title": 18,
        "usc_appendix": True,
        "appendix_unit": "act",
        "appendix_heading": "CLASSIFIED INFORMATION PROCEDURES ACT",
        "positive_law": False,
        "release_point": "119-121",
        "compiled_through": "Public Law 119-121",
        "congress": 96,
        "number": "456",
        "public_private": "public",
    }
    assert UsModule().validate_ext(cipa.ext) == cipa.ext and UsModule().validate_ext(crim.ext) == crim.ext
    assert all(p.status == "in_force" and p.valid_from is None for d in docs[1:] for p in d.provisions)


def test_criminal_rules_are_provisions_with_exact_text_and_their_credits_as_notes() -> None:
    by = _by_eid(_appendix(18)["/us/usc/t18a/courtRules/Crim"])
    rule = by["rule_11"]
    assert (rule.kind, rule.top_kind, rule.num_path, rule.heading) == ("courtRule", "courtRule", ["11"], "Pleas")
    assert rule.text.startswith("(a) Entering a Plea. (1) In General. A defendant may plead not guilty, guilty, or")
    assert (rule.note or "").startswith("Source credit: (As amended Feb. 28, 1966, eff. July 1, 1966;")
    assert [(r.href, r.relation) for r in rule.refs][:2] == [
        ("/us/pl/94/64/s3/5", "source_act"),
        ("/us/stat/89/371", "source_act"),
    ]
    leaf = by["rule_11__subsec_b__para_1__subpara_A"]
    assert leaf.text == (
        "the government’s right, in a prosecution for perjury or false statement, to use against the defendant any "
        "statement that the defendant gives under oath;"
    )
    assert (leaf.num_path, leaf.top_kind, leaf.context_heading, leaf.is_leaf) == (
        ["11", "b", "1", "A"],
        "courtRule",
        "Pleas",
        True,
    )
    assert leaf.parent_eid == "rule_11__subsec_b__para_1" and by["rule_11__subsec_b"].heading == (
        "Considering and Accepting a Guilty or Nolo Contendere Plea."
    )
    assert "rule_32.1" in by and by["rule_32.1"].num_path == ["32.1"]
    # reserved (19, 39) and transferred (22) rules are pointers, not law text; no rule is repealed here
    assert not {"rule_19", "rule_22", "rule_39"} & set(by)
    assert sum(1 for e in by if e.startswith("rule_") and "__" not in e) == 74
    assert "Footnote: See References in Text note below." in (by["rule_1__subsec_b__para_12"].note or "")
    assert all("Source credit" not in p.text and "Committee Notes" not in p.text for p in by.values())


def test_acts_reprinted_in_the_appendix_keep_their_sections_and_placeholders() -> None:
    docs = _appendix(18)
    cipa = _by_eid(docs["/us/usc/t18a/pl/96/456"])
    s4 = cipa["sec_4"]
    assert s4.heading == "Discovery of classified information by defendants"
    assert s4.text.startswith(
        "The court, upon a sufficient showing, may authorize the United States to delete specified"
    )
    assert s4.note == "Source credit: (Pub. L. 96–456, § 4, Oct. 15, 1980, 94 Stat. 2025.)"
    assert [(r.href, r.relation) for r in s4.refs] == [
        ("/us/pl/96/456/s4", "source_act"),
        ("/us/stat/94/2025", "source_act"),
    ]
    assert cipa["sec_1__subsec_a"].text.startswith(
        "“Classified information”, as used in this Act, means any information"
    )
    iad = docs["/us/usc/t18a/pl/91/538"]
    assert iad.short_title == "Interstate Agreement on Detainers" and iad.ext["congress"] == 91
    (gone,) = docs["/us/usc/t18a/unlawful-possession-or-receipt-of-firearms"].provisions
    assert (gone.eid, gone.status, gone.text, gone.num_path) == ("sec_1201_to_1203", "repealed", "", ["1201 to 1203"])
    assert (gone.note or "").startswith(
        "Status in source: repealed. The Code prints: Repealed. Pub. L. 99–308, § 104(b)"
    )


def test_an_eliminated_appendix_is_skipped_not_an_error() -> None:
    with pytest.raises(ParseSkipped, match="appendix to title 50 holds no law text: .*has been eliminated"):
        parse_uslm(_zip_appendix(50), {**APPENDIX_META, "title": 50})


def _appendix_xml(units: str, number: int = 28) -> bytes:
    return (
        f'<uscDoc xmlns="{HOUSE}" xmlns:dc="http://purl.org/dc/elements/1.1/" identifier="/us/usc/t{number}a">'
        f"<meta><dc:title>Title {number} Appendix</dc:title><dc:type>USCTitleAppendix</dc:type>"
        f"<docNumber>{number}a</docNumber><docPublicationName>Online@119-121</docPublicationName>"
        '<property role="is-positive-law">no</property></meta>'
        f'<appendix identifier="/us/usc/t{number}a"><num value="{number}a">Title {number}—APPENDIX</num>'
        f"<heading>X</heading>{units}</appendix></uscDoc>"
    ).encode()


def _rule(identifier: str, num: str, heading: str, body: str, status: str = "") -> str:
    attr = f' status="{status}"' if status else ""
    return (
        f'<courtRule{attr} identifier="{identifier}"><num value="{num}">Rule {num}.</num><heading> {heading}</heading>'
        f"{body}</courtRule>"
    )


SYNTHETIC_28A = _appendix_xml(
    # the civil rules: a title of rules, a level of supplemental rules with its own numbering
    '<courtRules identifier="/us/usc/t28a/courtRules/Civil"><heading>FEDERAL RULES OF CIVIL PROCEDURE</heading>'
    '<sourceCredit>(As amended ...)</sourceCredit><title identifier="/us/usc/t28a/courtRules/Civil/tIII">'
    "<num>TITLE III.</num><heading>PLEADINGS AND MOTIONS</heading>"
    + _rule(
        "/us/usc/t28a/courtRules/Civil/rule12",
        "12",
        "Defenses and Objections",
        '<subsection identifier="/us/usc/t28a/courtRules/Civil/rule12/b"><num value="b">(b)</num>'
        "<heading> How to Present Defenses.</heading><chapeau> Every defense to a claim for relief in any pleading "
        "must be asserted in the responsive pleading if one is required. But a party may assert the following "
        "defenses by motion:</chapeau>"
        '<paragraph identifier="/us/usc/t28a/courtRules/Civil/rule12/b/6"><num value="6">(6)</num>'
        "<content> failure to state a claim upon which relief can be granted; and</content></paragraph>"
        "</subsection><sourceCredit>(As amended Dec. 27, 1946, eff. Mar. 19, 1948.)</sourceCredit>"
        '<notes type="uscNote"><note topic="miscellaneous"><heading>Notes of Advisory Committee on Rules—1937</heading>'
        "<p>Note to Subdivision (a).</p></note></notes>",
    )
    + "</title><level><heading>SUPPLEMENTAL RULES FOR ADMIRALTY OR MARITIME CLAIMS AND ASSET FORFEITURE ACTIONS"
    "</heading>"
    + _rule(
        "/us/usc/t28a/courtRules/Civil/ruleC",
        "C",
        "In Rem Actions: Special Provisions",
        '<paragraph identifier="/us/usc/t28a/courtRules/Civil/ruleC/3"><num value="3">(3)</num>'
        "<heading> Judicial Authorization and Process.</heading><content> Arrest Warrant.</content></paragraph>",
    )
    + "</level></courtRules>"
    # the appellate rules: an abrogated rule, an appendix of forms with a superseded form
    '<courtRules identifier="/us/usc/t28a/courtRules/App"><heading>FEDERAL RULES OF APPELLATE PROCEDURE</heading>'
    + _rule("/us/usc/t28a/courtRules/App/rule3", "3", "Appeal as of Right—How Taken", "<content> An appeal.</content>")
    + '<courtRule status="unknown" identifier="/us/usc/t28a/courtRules/App/rule3.1"><num value="3.1">[Rule 3.1.</num>'
    "<heading> Appeal from a Judgment of a Magistrate Judge in a Civil Case] (Abrogated Apr. 24, 1998, eff. Dec. 1, "
    '1998)</heading><notes type="uscNote"><note><p>Rule 3.1 has been abrogated.</p></note></notes></courtRule>'
    '<level role="forms"><heading>APPENDIX OF FORMS</heading>'
    '<level role="form"><num value="Form 1">[Form 1</num><heading>. Superseded by Forms 1A and 1B]</heading>'
    "<content><p>To reflect changes to Rule 3, Form 1 is replaced by Forms 1A and 1B.</p></content></level>"
    '<level role="form" identifier="/us/usc/t28a/courtRules/App/1A"><num value="1A">Form 1A</num>'
    "<heading>. Notice of Appeal to a Court of Appeals From a Judgment of a District Court</heading>"
    "<content><p>United States District Court for the ____ District of ______</p></content></level></level>"
    '<level role="appendix"><heading>APPENDIX</heading><content><img src="file:///x.eps"/></content></level>'
    "</courtRules>"
    # a rule set the file gives no identifier (the bankruptcy rules, title 11): recognised by its heading
    "<courtRules><heading>FEDERAL RULES OF BANKRUPTCY PROCEDURE</heading>"
    + _rule("/us/usc/rule3001", "3001", "Proof of Claim", "<content> A proof of claim must conform.</content>")
    + "</courtRules>"
    # notes only: where an Act's text went (no instrument)
    "<level><heading>JUDICIAL PERSONNEL FINANCIAL DISCLOSURE REQUIREMENTS</heading>"
    '<notes type="uscNote"><note><p>[Title III of Pub. L. 95–521 was repealed.]</p></note></notes></level>'
    # an Act without an identifier, and the reorganization plans
    '<compiledAct><heading>ETHICS IN GOVERNMENT ACT OF 1978</heading><section status="repealed"><num value="112">'
    "[§ 112.</num><heading> Repealed. Pub. L. 101–280, § 3(10)(A), May 4, 1990, 104 Stat. 157]</heading>"
    '<notes type="uscNote"><note><p>Section related to penalties.</p></note></notes></section></compiledAct>'
    '<reorganizationPlans identifier="/us/usc/t28a/reorgPlans"><heading>REORGANIZATION PLANS</heading>'
    '<reorganizationPlan identifier="/us/usc/t28a/reorgPlan/1970/3"><heading>REORGANIZATION PLAN NO. 3 OF 1970'
    "</heading><content><p>Eff. Dec. 2, 1970, 35 F.R. 15623, 84 Stat. 2086</p><p>Section 1. Establishment of Agency"
    "</p></content></reorganizationPlan></reorganizationPlans>"
)


def test_supplemental_rules_forms_abrogated_rules_and_unnamed_units(monkeypatch: pytest.MonkeyPatch) -> None:
    """Modelled on usc28a.xml and usc11a.xml (see tests/fixtures/uscode_house/README.md for the real file)."""
    docs = {d.work_uri: d for d in parse_uslm(SYNTHETIC_28A, {**APPENDIX_META, "title": 28})}
    assert list(docs) == [
        "/us/usc/t28a/courtRules/Civil",
        "/us/usc/t28a/courtRules/Civil/supplemental-rules-for-admiralty-or-maritime-claims-and-asset-forfeiture-actions",
        "/us/usc/t28a/courtRules/App",
        "/us/usc/t28a/federal-rules-of-bankruptcy-procedure",
        "/us/usc/t28a/ethics-in-government-act-of-1978",
        "/us/usc/t28a/reorgPlans",
    ]
    civil = _by_eid(docs["/us/usc/t28a/courtRules/Civil"])
    assert list(civil) == [
        "rule_12",
        "rule_12__subsec_b",
        "rule_12__subsec_b__para_6",
    ]  # the supplemental rules are apart
    assert civil["rule_12__subsec_b__para_6"].text == "failure to state a claim upon which relief can be granted; and"
    assert civil["rule_12__subsec_b"].text.startswith("Every defense to a claim for relief")
    assert civil["rule_12"].note == "Source credit: (As amended Dec. 27, 1946, eff. Mar. 19, 1948.)"
    supp = docs[
        "/us/usc/t28a/courtRules/Civil/supplemental-rules-for-admiralty-or-maritime-claims-and-asset-forfeiture-actions"
    ]
    assert supp.ext["court_rules"] == "Civil" and supp.ext["supplemental_rules"] is True
    assert supp.short_title == "Supplemental Rules for Admiralty or Maritime Claims and Asset Forfeiture Actions"
    assert [p.eid for p in supp.provisions] == ["rule_C", "rule_C__para_3"]
    app = _by_eid(docs["/us/usc/t28a/courtRules/App"])
    assert [p.eid for p in app.values()] == ["rule_3", "rule_3.1", "form_1", "form_1A"]
    assert app["rule_3.1"].status == "repealed" and app["rule_3.1"].text == ""
    assert (app["rule_3.1"].note or "").startswith(
        "Status in source: repealed. The Code prints: Appeal from a Judgment of a Magistrate Judge in a Civil Case] "
        "(Abrogated Apr. 24, 1998, eff. Dec. 1, 1998)"
    )
    assert app["form_1"].status == "repealed" and (app["form_1"].note or "").startswith(
        "Status in source: superseded. The Code prints: To reflect changes to Rule 3, Form 1 is replaced"
    )
    assert (app["form_1A"].kind, app["form_1A"].num_path, app["form_1A"].status) == ("form", ["1A"], "in_force")
    assert app["form_1A"].text == "United States District Court for the ____ District of ______"
    bankr = docs["/us/usc/t28a/federal-rules-of-bankruptcy-procedure"]
    assert bankr.ext["court_rules"] == "Bankr" and [p.eid for p in bankr.provisions] == ["rule_3001"]
    eiga = docs["/us/usc/t28a/ethics-in-government-act-of-1978"]
    assert eiga.instrument_type == "act" and eiga.provisions[0].status == "repealed" and "congress" not in eiga.ext
    plans = docs["/us/usc/t28a/reorgPlans"]
    (plan,) = plans.provisions
    assert (plans.instrument_type, plan.eid, plan.num_path, plan.kind) == (
        "reorganization_plan",
        "plan_1970_3",
        ["1970/3"],
        "reorganizationPlan",
    )
    assert plan.heading == "REORGANIZATION PLAN NO. 3 OF 1970" and plan.text.endswith(
        "Section 1. Establishment of Agency"
    )
    for d in docs.values():
        assert d.ext["usc_appendix"] is True and d.ext["usc_title"] == 28 and UsModule().validate_ext(d.ext) == d.ext
    # an appendix whose units all hold notes only is skipped; a unit without a heading is refused
    with pytest.raises(ParseSkipped, match="holds no law text"):
        parse_uslm(_appendix_xml("<level><heading>GONE</heading><notes><note><p>Moved.</p></note></notes></level>"))
    with pytest.raises(UslmParseError, match="no heading"):
        parse_uslm(
            _appendix_xml('<compiledAct><section><num value="1">§ 1.</num><content>x</content></section></compiledAct>')
        )
    with pytest.raises(UslmParseError, match="is not /us/usc/t28a"):
        parse_uslm(
            SYNTHETIC_28A.replace(
                b'<appendix identifier="/us/usc/t28a">', b'<appendix identifier="/us/usc/t28a">', 1
            ).replace(b'identifier="/us/usc/t28a"><meta>', b'identifier="/us/usc/t29a"><meta>', 1)
        )


# ---------------------------------------------------------------- parser: refusals


def test_appendices_wrong_archives_and_oversized_members_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UslmParseError, match="title number not found"):
        parse_uslm(_xml(1).replace("<dc:type>USCTitle</dc:type>", "<dc:type>USCTitleAppendix</dc:type>").encode())
    with pytest.raises(UslmParseError, match="not a title or appendix"):
        parse_uslm(_xml(1).replace("<dc:type>USCTitle</dc:type>", "<dc:type>USCTable</dc:type>").encode())
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
        # the appendices: a rule set, a rule (below the rule: the rule), an Act's section, a reorganization plan
        ("/us/usc/t28a/courtRules/Civil", ("/us/usc/t28a/courtRules/Civil", None)),
        ("/us/usc/t28a/courtRules/Civil/rule12/b/6", ("/us/usc/t28a/courtRules/Civil", "rule_12")),
        ("/us/usc/t18a/courtRules/Crim/rule32.1", ("/us/usc/t18a/courtRules/Crim", "rule_32.1")),
        ("/us/usc/t18a/pl/96/456/s4", ("/us/usc/t18a/pl/96/456", "sec_4")),
        ("/us/usc/t18a/pl/96/456", ("/us/usc/t18a/pl/96/456", None)),
        ("/us/usc/t5a/reorgPlan/1939/I", ("/us/usc/t5a/reorgPlans", "plan_1939_I")),
        ("/us/usc/t5a/reorgPlans", ("/us/usc/t5a/reorgPlans", None)),
    ],
)
def test_code_identifiers_resolve_to_title_and_section(uri: str, expected: tuple[str, str | None]) -> None:
    assert resolve_identifier(uri) == expected
    assert resolve_uri(uri) == expected


def test_an_appendix_identifier_is_not_the_main_title() -> None:
    assert resolve_identifier("/us/usc/t28a") is None and resolve_identifier("/us/usc/t28a/unknown/x") is None


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _by_eid(doc)[eid]
    return UsModule().provision_citation(
        ProvisionCitationInput(
            instrument_title=doc.short_title or doc.work_title,  # as retrieval passes it (details.provision_citation)
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


def _appendix_cite(
    title: int, unit: str, path: list[str], eid: str, *, key: str | None = None, supp: bool = False, name: str = "X"
) -> tuple[str, str]:
    ext: dict[str, Any] = {"usc_title": title, "usc_appendix": True, "appendix_unit": unit, "positive_law": False}
    if key:
        ext["court_rules"] = key
    if supp:
        ext["supplemental_rules"] = True
    kind = {"court_rules": "court_rules", "act": "act", "reorganization_plans": "reorganization_plan"}[unit]
    c = ProvisionCitationInput(name, kind, "US", "courtRule", path, "en", ext=ext, eid=eid)
    return UsModule().provision_citation(c), UsModule().pinpoint(c)


def test_appendix_material_is_cited_in_bluebook_form() -> None:
    cite = _appendix_cite
    assert cite(28, "court_rules", ["12", "b", "6"], "rule_12__subsec_b__para_6", key="Civil") == (
        "Fed. R. Civ. P. 12(b)(6)",
        "Rule 12(b)(6)",
    )
    assert cite(18, "court_rules", ["11", "b", "1", "A"], "rule_11__subsec_b__para_1__subpara_A", key="Crim")[0] == (
        "Fed. R. Crim. P. 11(b)(1)(A)"
    )
    assert (
        cite(28, "court_rules", ["4", "a", "1"], "rule_4__subsec_a__para_1", key="App")[0] == "Fed. R. App. P. 4(a)(1)"
    )
    assert cite(28, "court_rules", ["702"], "rule_702", key="Evid")[0] == "Fed. R. Evid. 702"
    assert cite(11, "court_rules", ["3001", "c"], "rule_3001__subsec_c", key="Bankr")[0] == "Fed. R. Bankr. P. 3001(c)"
    assert cite(28, "court_rules", ["C", "3"], "rule_C__para_3", key="Civil", supp=True)[0] == (
        "Fed. R. Civ. P. Supp. R. C(3)"
    )
    assert cite(28, "court_rules", ["1A"], "form_1A", key="App") == ("Fed. R. App. P. Form 1A", "Form 1A")
    assert cite(28, "court_rules", [], "", key="App") == ("Fed. R. App. P.", "")
    # a rule set this module has no abbreviation for is named as the appendix prints it
    assert cite(28, "court_rules", ["3"], "rule_3", name="Rules of the Court of Federal Claims") == (
        "28 U.S.C. app., Rules of the Court of Federal Claims, Rule 3",
        "Rule 3",
    )
    assert cite(18, "act", ["4"], "sec_4", name="Classified Information Procedures Act") == (
        "18 U.S.C. app. § 4 (Classified Information Procedures Act)",
        "§ 4",
    )
    assert cite(18, "act", ["1", "a"], "sec_1__subsec_a", name="Classified Information Procedures Act")[0] == (
        "18 U.S.C. app. § 1(a) (Classified Information Procedures Act)"
    )
    assert cite(18, "act", [], "", name="Classified Information Procedures Act")[0] == (
        "18 U.S.C. app. (Classified Information Procedures Act)"
    )
    assert cite(5, "reorganization_plans", ["1970/3"], "plan_1970_3", name="Reorganization Plans") == (
        "Reorg. Plan No. 3 of 1970, 5 U.S.C. app.",
        "Reorg. Plan No. 3 of 1970",
    )
    assert (
        cite(5, "reorganization_plans", [], "", name="Reorganization Plans")[0]
        == "5 U.S.C. app. (Reorganization Plans)"
    )
    # built the way the parser records them, from the fixture
    crim = _appendix(18)["/us/usc/t18a/courtRules/Crim"]
    assert _cite(crim, "rule_11__subsec_b__para_1__subpara_A") == "Fed. R. Crim. P. 11(b)(1)(A)"
    assert _cite(crim, "rule_32.1") == "Fed. R. Crim. P. 32.1"
    assert _cite(_appendix(18)["/us/usc/t18a/pl/96/456"], "sec_4") == (
        "18 U.S.C. app. § 4 (Classified Information Procedures Act)"
    )


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


def _cache(tmp_path: Path, titles: list[int], robots: str = ROBOTS, appendices: list[int] = []) -> Path:  # noqa: B006
    """A PoliteHttp cache as a previous online run leaves it, built from the fixtures (titles at release point 119-120,
    appendices at 119-121)."""
    cache = tmp_path / "cache"
    host = cache / "uscode.house.gov"
    host.mkdir(parents=True)
    (host / "robots.txt").write_text(robots)
    (host / "robots.txt.meta.json").write_text(
        json.dumps({"url": "https://uscode.house.gov/robots.txt", "status": 200})
    )
    archives = [(title_url("119-120", n), _zip(n)) for n in titles]
    archives += [(title_url("119-121", n, appendix=True), _zip_appendix(n)) for n in appendices]
    for url, data in archives:
        target = host / url.removeprefix("https://uscode.house.gov/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        meta = {"url": url, "final_url": url, "status": 200, "content_type": "application/x-zip-compressed"}
        target.with_name(target.name + ".meta.json").write_text(
            json.dumps({**meta, "retrieved_at": "2026-10-14T15:30:00+00:00"})
        )
    return cache


def test_title_urls_are_the_olrc_release_point_downloads() -> None:
    assert title_url("119-120", 17) == (
        "https://uscode.house.gov/download/releasepoints/us/pl/119/120/xml_usc17@119-120.zip"
    )
    assert title_url("119-121", 5, appendix=True) == (
        "https://uscode.house.gov/download/releasepoints/us/pl/119/121/xml_usc05a@119-121.zip"
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
    assert docs[1].metadata["appendix"] is False and docs[1].metadata["release_point"] == "119-120"
    assert "release_date" not in docs[1].metadata  # the manifest did not state one: the parser falls back
    assert connector.http is not None and connector.http.requests_made == 0


def test_connector_yields_the_appendices_with_the_release_point_and_its_date(tmp_path: Path) -> None:
    release = {"id": "119-121", "current_through": "Public Law 119-121 (10/09/2026)", "date": "2026-10-09"}
    manifest = _manifest(
        tmp_path,
        [],
        release_point=release,
        titles=[{"number": 1}],
        appendices=[{"number": 18, "sha256": SHA_APPENDIX[18]}, {"number": 50, "name": "Appendix to Title 50"}],
    )
    cache = _cache(tmp_path, [], appendices=[18, 50])
    # title 1 at 119-121 is not cached here: it is reported, the appendices are still yielded
    url = title_url("119-121", 1)
    gone = cache / "uscode.house.gov" / url.removeprefix("https://uscode.house.gov/")
    gone.parent.mkdir(parents=True, exist_ok=True)
    gone.write_bytes(b"")
    gone.with_name(gone.name + ".meta.json").write_text(json.dumps({"url": url, "status": 404}))
    connector = UscodeHouseConnector(manifest, SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["uscode-house:usc18a@119-121", "uscode-house:usc50a@119-121"]
    assert [hashlib.sha256(d.content).hexdigest() for d in docs] == [SHA_APPENDIX[18], SHA_APPENDIX[50]]
    assert docs[0].uri == title_url("119-121", 18, appendix=True)
    assert {k: v for k, v in docs[0].metadata.items() if k != "cached_fetch"} == {
        "title": 18,
        "appendix": True,
        "release_point": "119-121",
        "release_date": "2026-10-09",
        "current_through": "Public Law 119-121 (10/09/2026)",
    }
    assert len(connector.fetch_errors) == 1 and "usc01@119-121.zip: not found (404)" in connector.fetch_errors[0]
    # the date may be written unquoted (yaml gives a date object); it must agree with current_through
    (tmp_path / "d").mkdir()
    (tmp_path / "b").mkdir()
    dated = _manifest(tmp_path / "d", [1], release_point={"id": "119-120", "date": date(2026, 10, 7)})
    assert UscodeHouseConnector.release_metadata(UscodeHouseConnector(dated, SOURCE, tmp_path)._manifest()) == {
        "release_point": "119-120",
        "release_date": "2026-10-07",
    }
    for bad in (
        {"release_point": {**release, "date": "2026-10-10"}},
        {"release_point": {**release, "date": "yesterday"}},
        {"release_point": {"id": "119-121", "current_through": "Public Law 119-121", "date": "2026-10-09"}},
        {"appendices": [{"number": 18}, {"number": 18}]},
        {"appendices": [{"number": 0}]},
        {"appendices": {"number": 18}},
    ):
        with pytest.raises(ManifestError):
            list(UscodeHouseConnector(_manifest(tmp_path / "b", [1], **bad), SOURCE, tmp_path, offline=True).fetch())
    assert release_date_of("Public Law 119-121 (10/09/2026)") == date(2026, 10, 9)
    with pytest.raises(ManifestError):
        release_date_of("Public Law 119-121 (13/40/2026)")


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
    assert source.document_kinds == ["act", "court_rules", "reorganization_plan"]
    assert {"court_rules", "reorganization_plan"} <= set(catalog.jurisdictions.get("US").instrument_types)
    real = yaml.safe_load((REPO_ROOT / source.access.endpoint).read_text())
    assert real["base_url"] == "https://uscode.house.gov" and real["release_point"]["id"] == "119-121"
    assert real["release_point"]["current_through"] == "Public Law 119-121 (10/09/2026)"
    assert str(real["release_point"]["date"]) == "2026-10-09"  # the date the parser uses; no code change per release
    assert uslm.USC_RELEASE_POINT_DATES.get("119-121") == date(2026, 10, 9)  # the consistency table agrees
    manifest = UscodeHouseConnector(REPO_ROOT / source.access.endpoint, SOURCE, Path("unused"))._manifest()
    assert UscodeHouseConnector.release_metadata(manifest) == {
        "release_point": "119-121",
        "release_date": "2026-10-09",
        "current_through": "Public Law 119-121 (10/09/2026)",
    }


def test_committed_manifest_lists_every_main_title_with_its_pin_and_legal_status() -> None:
    """The whole open scope: titles 1-52 and 54 (53 is reserved), 27 of them positive law, the five appendices,
    every archive pinned."""
    real = yaml.safe_load((REPO_ROOT / "config" / "manifests" / "us-uscode-house.yaml").read_text())
    titles = real["titles"]
    assert [t["number"] for t in titles] == [*range(1, 53), 54]
    assert all(len(t["sha256"]) == 64 and set(t["sha256"]) <= set("0123456789abcdef") for t in titles)
    assert len({t["sha256"] for t in titles}) == 53 and all(t["name"] for t in titles)
    appendices = real["appendices"]
    assert [a["number"] for a in appendices] == [5, 11, 18, 28, 50]
    assert all(len(a["sha256"]) == 64 and a["name"] == f"Appendix to Title {a['number']}" for a in appendices)
    assert {a["number"]: a["sha256"] for a in appendices if a["number"] in SHA_APPENDIX} == SHA_APPENDIX
    assert len({t["sha256"] for t in titles} | {a["sha256"] for a in appendices}) == 58
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
    # the title fixtures are the 119-120 archives; the OLRC regenerates every file at a release point, so the pins
    # differ, but the status in the manifest is the status the XML states, for every fixture
    for n in (1, 17, 27):
        assert by_number[n]["sha256"] != SHA[n] and _doc(n).ext["positive_law"] is by_number[n]["positive_law"]


def test_download_page_is_read_as_the_olrc_prints_it() -> None:
    """An excerpt of the real page: the asterisk marks positive law, the changed-title class and a style
    attribute do not hide a title, and the reserved title 53 and the appendix rows are not main titles."""
    page = parse_download_page((FIX / "download_page_excerpt.html").read_text(encoding="utf-8"))
    assert (page.release_point, page.current_through) == ("119-120", "Public Law 119-120 (10/07/2026)")
    assert page.release_date == date(2026, 10, 7)
    assert page.titles == [
        {"number": 1, "name": "General Provisions", "positive_law": True},
        {"number": 2, "name": "The Congress", "positive_law": False},
        {"number": 5, "name": "Government Organization and Employees", "positive_law": True},
        {"number": 31, "name": "Money and Finance", "positive_law": True},
        {"number": 54, "name": "National Park Service and Related Programs", "positive_law": True},
    ]
    assert page.appendices == [{"number": 5, "name": "Appendix to Title 5"}]
    with pytest.raises(ManifestError, match="currency"):
        parse_download_page("<html></html>")


def test_the_whole_download_page_at_release_point_119_121_lists_58_archives() -> None:
    """The page as received on 2026-10-18 (gzip, nothing edited): what the committed manifest was built from."""
    with gzip.open(FIX / "download.html@119-121.gz") as handle:
        page = parse_download_page(handle.read().decode("utf-8"))
    assert (page.release_point, page.current_through) == ("119-121", "Public Law 119-121 (10/09/2026)")
    assert page.release_date == date(2026, 10, 9)
    assert [t["number"] for t in page.titles] == [*range(1, 53), 54]
    assert sum(t["positive_law"] for t in page.titles) == 27
    assert [a["number"] for a in page.appendices] == [5, 11, 18, 28, 50]
    assert page.titles[27] == {"number": 28, "name": "Judiciary and Judicial Procedure", "positive_law": True}
    real = yaml.safe_load((REPO_ROOT / "config" / "manifests" / "us-uscode-house.yaml").read_text())
    assert [(t["number"], t["name"], t["positive_law"]) for t in real["titles"]] == [
        (t["number"], t["name"], t["positive_law"]) for t in page.titles
    ]
    assert [a["number"] for a in real["appendices"]] == [a["number"] for a in page.appendices]


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


class _AppendixFixtures:
    """What the connector yields for the appendices to titles 18 and 50 at release point 119-121."""

    def fetch(self) -> Iterator[RawDocument]:
        for n in (18, 50):
            yield RawDocument(
                f"uscode-house:usc{n:02d}a@119-121",
                title_url("119-121", n, appendix=True),
                "application/zip",
                _zip_appendix(n),
                datetime(2026, 10, 18, 1, 8, tzinfo=UTC),
                {"title": n, **APPENDIX_META},
            )


@pytest.fixture(scope="module")
def appendix(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> Iterator[tuple[Session, IngestionReport]]:
    session = session_factory()
    report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_AppendixFixtures())
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


@pytest.mark.db
def test_ingestion_of_an_appendix_creates_one_instrument_per_unit_and_skips_the_eliminated_one(
    appendix: tuple[Session, IngestionReport],
) -> None:
    session, report = appendix
    assert report.errors == [] and report.conflicts == []
    assert (report.documents_seen, report.documents_new, report.documents_skipped) == (2, 1, 1)
    assert len(report.skipped) == 1 and report.skipped[0].startswith(
        "uscode-house:usc50a@119-121: the appendix to title 50 holds no law text"
    )
    assert report.instruments_created == 4
    rows = session.execute(
        select(m.Instrument.uri, m.Instrument.instrument_type, m.Instrument.ext).where(
            m.Instrument.uri.like("/us/usc/t18a%")
        )
    ).all()
    assert {uri: kind for uri, kind, _ in rows} == {
        "/us/usc/t18a/unlawful-possession-or-receipt-of-firearms": "act",
        "/us/usc/t18a/pl/91/538": "act",
        "/us/usc/t18a/pl/96/456": "act",
        "/us/usc/t18a/courtRules/Crim": "court_rules",
    }
    assert all(ext["usc_appendix"] is True and ext["release_point"] == "119-121" for _, _, ext in rows)
    stored = session.scalar(
        select(m.SourceDocument).where(m.SourceDocument.external_id == "uscode-house:usc18a@119-121")
    )
    assert stored is not None and stored.metadata_["release_date"] == "2026-10-09"


@pytest.mark.db
def test_search_finds_a_criminal_rule_and_an_appendix_act_with_bluebook_citations(
    appendix: tuple[Session, IngestionReport], catalog: Catalog
) -> None:
    session, _ = appendix
    res = _search(
        session, catalog, q="prosecution for perjury or false statement statement the defendant gives under oath"
    )
    hit = next(h for h in res["results"] if h["document"]["uri"] == "/us/usc/t18a/courtRules/Crim")
    assert hit["citation"] == "Fed. R. Crim. P. 11(b)(1)(A)" and hit["document"]["type"] == "court_rules"
    assert hit["text"].startswith("the government’s right, in a prosecution for perjury or false statement")
    assert (
        hit["version"]["currency_date"] == "2026-10-09" and hit["version"]["consolidation"] == "us_code_release_point"
    )
    assert {"USC_APPENDIX", "ATTESTED_ONLY_AT", "OFFICIAL_CONSOLIDATION_REPRODUCED", "PROFILE_UNVERIFIED"} <= _codes(
        hit
    )
    assert "NOT_POSITIVE_LAW" not in _codes(hit)  # the appendix limitation says what applies, not the title's
    message = next(lim for lim in hit["limitations"] if lim["code"] == "USC_APPENDIX")["message"]
    assert "has not been enacted as part of the title" in message and "28 U.S.C. 2072-2075" in message
    cipa = _search(session, catalog, q="discovery of classified information by defendants delete specified items")
    hit = next(h for h in cipa["results"] if h["document"]["uri"] == "/us/usc/t18a/pl/96/456")
    assert hit["citation"] == "18 U.S.C. app. § 4 (Classified Information Procedures Act)"
    assert hit["document"]["type"] == "act" and "USC_APPENDIX" in _codes(hit)
    assert hit["text"].startswith("The court, upon a sufficient showing, may authorize the United States to delete")


@pytest.mark.db
def test_a_rule_is_looked_up_by_its_eid_and_dated_only_at_the_release_point(
    appendix: tuple[Session, IngestionReport], catalog: Catalog
) -> None:
    session, _ = appendix
    current = provision_at(session, catalog, "/us/usc/t18a/courtRules/Crim/~rule_11", date(2026, 10, 18))
    assert current["status_on_date"] == "in_force_date_unverified"
    assert current["provision"]["citation"] == "Fed. R. Crim. P. 11"
    assert current["version"]["valid_from"] is None and current["instrument"]["title"].startswith(
        "Federal Rules of Criminal Procedure (Appendix to Title 18"
    )
    assert {"ATTESTED_ONLY_AT", "AS_OF_AFTER_CURRENCY", "USC_APPENDIX"} <= _codes(current)
    attested = next(lim for lim in current["limitations"] if lim["code"] == "ATTESTED_ONLY_AT")
    assert "in force on 2026-10-09 (us code release point)" in attested["message"]
    # the Act's source credit links to the public law, which is not loaded here: recorded, unresolved
    link = session.execute(
        select(m.CitationLink.to_uri, m.CitationLink.to_instrument_id).where(
            m.CitationLink.to_uri == "/us/pl/96/456/s4"
        )
    ).first()
    assert link is not None and link[1] is None


@pytest.mark.db
def test_appendix_texts_rederive_from_the_downloaded_archive(
    appendix: tuple[Session, IngestionReport], catalog: Catalog, store: LocalObjectStore
) -> None:
    session, _ = appendix
    doc = session.scalar(select(m.SourceDocument).where(m.SourceDocument.external_id == "uscode-house:usc18a@119-121"))
    assert doc is not None
    result = verify_source_document(session, catalog, store, doc.id, checker="pytest")
    assert result["result"] == "pass" and result["checked_units"] == 1236, result["problems"][:3]
