"""Irish Statute Book: parser, connector, citations and search behaviour (Acts as enacted, real fixtures)."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
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
from ulis.ingestion.pipeline import IngestionReport, Ingestor, build_connector
from ulis.ingestion.pipeline import resolve_uri as pipeline_resolve_uri
from ulis.jurisdictions.modules import ProvisionCitationInput, get_module
from ulis.retrieval.lookup import provision_at
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.irish_statute_book import IrishStatuteBookConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError
from ulis.sources.normalized import ParsedDocument, ParsedProvision
from ulis.sources.parsers import irish_statute_book as ib
from ulis.sources.parsers.irish_statute_book import IrishParseError, parse_irish_statute_book, resolve_uri
from ulis.sources.registry import LicenceNotApproved, licence_gate
from ulis.storage.object_store import LocalObjectStore
from ulis.verification.integrity import verify_source_document

FIX = REPO_ROOT / "tests" / "fixtures" / "irish_statute_book"
META = json.loads((FIX / "meta.json").read_text())
SOURCE = "ie-irish-statute-book"
# Full files as published, except the Companies Act 2014, an excerpt (see the fixtures README).
NAMES = ("1977-10", "1997-26", "2001-50", "2007-19", "2014-38-excerpt", "2018-7", "1938-16")


def _xml(name: str) -> bytes:
    return gzip.decompress((FIX / f"{name}.xml.gz").read_bytes())


def _doc(name: str) -> ParsedDocument:
    docs = parse_irish_statute_book(_xml(name))
    assert len(docs) == 1
    return docs[0]


def _p(doc: ParsedDocument, eid: str) -> ParsedProvision:
    return {p.eid: p for p in doc.provisions}[eid]


def _cite(doc: ParsedDocument, eid: str) -> str:
    p = _p(doc, eid)
    return get_module("ie").provision_citation(
        ProvisionCitationInput(
            doc.short_title or doc.title,
            doc.instrument_type,
            "IE",
            p.top_kind,
            p.num_path,
            "en",
            ext=doc.ext,
            eid=p.eid,
        )
    )


def _act(body: str = "", year: str = "1999", date_: str = "19990101", doctype: str = "", extra: str = "") -> bytes:
    """A minimal Act document (synthetic; used only for edge cases absent from the fixtures)."""
    return (
        f'<?xml version="1.0"?>{doctype}<act><metadata><title>TEST ACT, {year}</title><number>1</number>'
        f"<year>{year}</year><dateofenactment>{date_}</dateofenactment></metadata>{extra}<body>{body}</body></act>"
    ).encode()


SECT = (
    '<sect id="SEC1"><number>1</number><title><p>Test.</p></title><p class="2 0 0 left 0 1"><b>1.</b> Text.</p></sect>'
)


# --------------------------------------------------------------------------- metadata, dates, origin


def test_act_metadata_dates_and_origin() -> None:
    nfo = _doc("1997-26")
    assert nfo.work_uri == "/ie/act/1997/26" and nfo.expression_uri == "/ie/act/1997/26/en@1997-05-19"
    assert nfo.title == "Non-Fatal Offences against the Person Act 1997 (Number 26 of 1997, as enacted)"
    assert nfo.short_title == "Non-Fatal Offences against the Person Act 1997"  # comma before the year dropped
    assert nfo.ext["short_title_as_enacted"] == "Non-Fatal Offences against the Person Act, 1997"
    assert nfo.work_date == nfo.version_date == date(1997, 5, 19) and nfo.currency_date is None
    # An official conversion by the publisher, not the authenticated text; as enacted; dated from enactment.
    assert (nfo.text_origin, nfo.authoritative, nfo.consolidation_kind) == ("official_conversion", False, "as_enacted")
    assert nfo.validity_basis == "enactment_date_presumed" and nfo.language == "en" and nfo.extent == ["IE"]
    assert [(e.event_type, e.date) for e in nfo.events] == [("enactment", date(1997, 5, 19))]
    assert all(
        p.status == "in_force" and p.valid_from == date(1997, 5, 19) and p.valid_to is None for p in nfo.provisions
    )
    assert all(p.extent == ["IE"] for p in nfo.provisions)
    dpa = _doc("2018-7")
    assert dpa.short_title == "Data Protection Act 2018" and dpa.number == "7" and dpa.work_date == date(2018, 5, 24)
    assert dpa.ext == {
        "act_year": 2018,
        "act_number": 7,
        "commencement_basis": "by_order",
        "short_title_as_enacted": "Data Protection Act 2018",
    }
    get_module("ie").validate_ext(dpa.ext)


def test_missing_amendments_and_commencement_are_reported_as_outstanding() -> None:
    uda = _doc("1977-10")  # "shall come into operation on such day as the Minister may appoint by order"
    assert uda.ext["commencement_basis"] == "by_order"
    labels = [c["affected_label"] for c in uda.outstanding_changes]
    assert labels == ["Later amendments, repeals and revocations", "Commencement"]
    assert all(c["affects_eid"] is None and c["by_uri"] is None for c in uda.outstanding_changes)
    assert "lower bound" in uda.outstanding_changes[1]["description"]
    assert (
        _doc("1997-26").ext["commencement_basis"] == "relative_to_passing"
    )  # "3 months after the date of its passing"
    assert _doc("2001-50").ext["commencement_basis"] == "by_order_and_passing"


# --------------------------------------------------------------------------- exact text and structure


@pytest.mark.parametrize("name", NAMES)
def test_every_section_keeps_the_printed_text_in_order(name: str) -> None:
    """No text is lost, invented or reordered: a section's composite text is its printed lines."""
    content = _xml(name)
    doc = parse_irish_statute_book(content)[0]
    by_eid = {p.eid: p for p in doc.provisions}
    root = ib.parse_xml(content)
    checked = 0
    for sect in root.iter("sect"):
        num = (sect.findtext("number") or "").strip().rstrip(".")
        lines = []
        for child in sect:
            if child.tag in ("p", "pc"):
                lines.append(ib.inline_text(child))
            elif child.tag == "table":
                lines.extend(ib._table_lines(child))
        printed = re.sub(r"^\s*" + re.escape(num) + r"\s*\.\s*—?", "", "".join(lines))
        assert re.sub(r"\s+", "", printed) == re.sub(r"\s+", "", by_eid[f"section-{ib.slug(num)}"].text), num
        checked += 1
    assert checked >= 5


def test_page_breaks_degree_signs_and_line_end_hyphens_in_older_acts() -> None:
    # Older Acts carry <page number="456"/>, <degree/> and <eolhyphen/> (seen in Acts 1980/14, 1992/20, 1995/14, 2005/22
    # and 2014/18): the page number is not text, the other two are characters.
    body = SECT.replace(
        "Text.",
        'Under Article 34.5.4<degree/> of the Constitution, after <page number="456"/>the regulation, Bord Altra'
        "<ifada/><eolhyphen/>Tr<efada/>idliachta.",
    )
    (doc,) = parse_irish_statute_book(_act(body))
    texts = [p.text for p in doc.provisions if "Article" in p.text]
    assert texts and all(
        "Under Article 34.5.4° of the Constitution, after the regulation, Bord Altraí-Tréidliachta." in t for t in texts
    )


def test_chapters_of_a_schedule_part_are_provisions_with_a_citation() -> None:
    # Acts 1991/6 and 2024/7 have <chapter> inside a Schedule (or a Part of one).
    schedule = (
        '<backmatter><schedule id="SCHED1"><title><p>SCHEDULE 1</p></title>'
        '<part id="P1"><title><p>PART 1</p></title>'
        '<chapter id="C1"><title><p>CHAPTER I</p></title><p>Text of the first chapter.</p></chapter>'
        '<chapter id="C2"><title><p>CHAPTER II</p></title><p>Text of the second chapter.</p></chapter>'
        "</part></schedule></backmatter>"
    )
    (doc,) = parse_irish_statute_book(_act(SECT, extra=schedule))
    chapter = _p(doc, "schedule-1-part-1-chapter-ii")
    assert chapter.num_path == ["1", "part:1", "chapter:II"] and "second chapter" in chapter.text
    module = get_module("ie")
    c = ProvisionCitationInput(
        doc.short_title or doc.title,
        "act",
        "IE",
        chapter.top_kind,
        chapter.num_path,
        "en",
        ext=doc.ext,
        eid=chapter.eid,
    )
    assert module.provision_citation(c).endswith(", Sch 1, Pt 1, Ch II")


def test_text_is_exact_and_special_characters_are_resolved() -> None:
    nfo = _doc("1997-26")
    assert _p(nfo, "section-2-1").text == (
        "A person shall be guilty of the offence of assault who, without lawful excuse, intentionally or recklessly—\n"
        "(a) directly or indirectly applies force to or causes an impact on the body of another, or\n"
        "(b) causes another to believe on reasonable grounds that he or she is likely immediately to be subjected "
        "to any such force or impact,\nwithout the consent of the other."
    )
    assert _p(nfo, "section-2-4").text.endswith(
        "a fine not exceeding £1,500 or to imprisonment for a term not exceeding 6 months or to both."
    )
    assert "Garda Síochána" in _p(nfo, "section-7-2").text  # <ifada/> <afada/>
    assert (
        _p(nfo, "section-1-1-def-harm").text
        == "“harm” means harm to body or mind and includes pain and unconsciousness;"
    )
    cpa = _doc("2007-19")  # pretty-printed quotation marks: <odq/>\nconsumer\n<cdq/>
    assert _p(cpa, "section-2-1-def-consumer").text.startswith("“consumer” means a natural person")
    assert "person’s trade" in _p(cpa, "section-2-1-def-consumer").text  # <csq/>
    for doc in (nfo, cpa, _doc("2018-7")):
        for p in doc.provisions:
            assert "<" not in p.text and "\u00a0" not in p.text and "  " not in p.text
            assert not re.search(r"&\w+;|&#", p.text)  # no raw entities (a lone "&" is real text: "24 & 25 Vict.")


def test_subsections_paragraphs_and_the_roman_letter_ambiguity() -> None:
    dpa = _doc("2018-7")
    leaf = _p(dpa, "section-60-3-a-i")
    assert (leaf.kind, leaf.num_path, leaf.parent_eid) == (
        "subparagraph",
        ["60", "(3)", "(a)", "(i)"],
        "section-60-3-a",
    )
    assert leaf.text.startswith("to safeguard cabinet confidentiality")
    para = _p(dpa, "section-60-3-a")
    assert para.kind == "paragraph" and para.has_own_content and not para.is_leaf
    assert para.text.startswith("the restrictions are necessary and proportionate—\n(i) to safeguard")
    # "(a) (i) ..." on one printed line: paragraph (a) without text of its own, holding sub-paragraphs (i), (ii)
    both = _p(dpa, "section-60-5-a")
    assert not both.has_own_content and not both.indexable
    assert both.text.startswith("(i) if the application of those rights") and both.text.count("\n") == 1
    assert _p(dpa, "section-60-5-a-ii").kind == "subparagraph"
    # (i) is the roman numeral under a paragraph, but the letter after (h) at the same indent
    assert _p(dpa, "section-60-7-i").kind == "paragraph"
    uda = _doc("1977-10")
    assert _p(uda, "section-2-1-i").kind == "paragraph"
    assert _p(uda, "section-2-3-a-ii-i").kind == "clause" and _p(uda, "section-2-3-a-ii-i").num_path[-1] == "(I)"
    tfa = _doc("2001-50")
    assert _p(tfa, "section-24-def-instrument-i").kind == "paragraph"


def test_closing_words_keep_their_place() -> None:
    nfo = _doc("1997-26")
    c = _p(nfo, "section-6-5-c")  # "(c) ... (i) ..., (ii) ..., shall be guilty of an offence." belongs to (c)
    assert c.text.splitlines()[0].startswith("A person who in committing")
    assert c.text.splitlines()[-1] == "shall be guilty of an offence."
    dpa = _doc("2018-7")  # text between paragraphs (c) and (i): "require that individual to-" ends subsection (1)
    s4 = _p(dpa, "section-4-1").text.splitlines()
    assert s4.index("require that individual to—") < s4.index(
        next(line for line in s4 if line.startswith("(i) make a request"))
    )


def test_definitions_are_provisions_with_their_own_pinpoint() -> None:
    nfo = _doc("1997-26")
    d = _p(nfo, "section-1-1-def-harm")
    assert (d.kind, d.num_path, d.parent_eid, d.is_leaf) == (
        "definition",
        ["1", "(1)", "def:harm"],
        "section-1-1",
        True,
    )
    s1 = _p(nfo, "section-1-1")
    assert s1.has_own_content and s1.text.startswith("In this Act—\n“contaminated blood” means")
    assert s1.text.index("“harm”") < s1.text.index("“property”")  # printed order kept
    assert _cite(nfo, "section-1-1-def-harm") == (
        "Non-Fatal Offences against the Person Act 1997, s 1(1) (definition of “harm”)"
    )


def test_quoted_amending_text_is_not_treated_as_structure() -> None:
    dpa = _doc("2018-7")
    s = _p(dpa, "section-192")  # substitutes a paragraph "(b) ... (i) ... (ii) ..." in another Act
    assert s.is_leaf and "“(b) Nothing in paragraph (a)" in s.text and "\n(iii) may act on behalf" in s.text
    assert not [p for p in dpa.provisions if p.parent_eid == "section-192"]


def test_footnotes_are_notes_not_text() -> None:
    dpa = _doc("2018-7")
    lt = _p(dpa, "long-title")
    assert lt.note == "[1] OJ No. L 119, 4.5.2016, p.1 | [2] OJ No. L 119, 4.5.2016, p.89"
    assert lt.text.startswith("An Act to establish a body to be known as An Coimisiún um Chosaint Sonraí")
    assert "OJ No." not in lt.text and lt.kind == "long_title" and lt.top_kind == "long_title"
    assert _p(dpa, "section-2-1-def-data-protection-regulation").note == "[3] OJ No. L 119, 4.5.2016, p.1"


def test_parts_and_chapters_are_context_not_citation_levels() -> None:
    dpa = _doc("2018-7")
    assert _p(dpa, "section-60").context_heading == (
        "PART 3 Data Protection Regulation / Chapter 3 Rights, and restrictions of rights, of data subject "
        "and restrictions on obligations of controllers"
    )
    assert _p(dpa, "section-1").context_heading == "PART 1 Preliminary and General"
    assert _p(dpa, "section-60").heading.startswith("Restrictions on obligations of controllers")
    assert _p(_doc("2001-50"), "section-4-1").context_heading == "PART 2 Theft and Related Offences"
    ca = _doc("2014-38-excerpt")
    assert _p(ca, "section-228-1-a").context_heading.startswith(
        "PART 5 DUTIES OF DIRECTORS AND OTHER OFFICERS / CHAPTER 2"
    )
    assert not any(p.kind in ("part", "chapter") for p in ca.provisions)


def test_schedules_and_their_parts() -> None:
    dpa = _doc("2018-7")
    s1 = _p(dpa, "schedule-1")
    assert (s1.kind, s1.num_path, s1.heading, s1.is_leaf) == ("schedule", ["1"], "Statutory Instruments Revoked", True)
    assert "Item (1) | S.I. No. and Year (2) | Short Title (3) | Extent of Revocation (4)" in s1.text
    assert "1. | S.I. No. 347 of 1988 | Data Protection (Fees) Regulations 1988 | The whole Regulations" in s1.text
    nfo = _doc("1997-26")  # the only Schedule, printed without a number
    only = _p(nfo, "schedule")
    assert only.heading == "Enactments Repealed" and only.num_path == [""] and only.note == "Section 31."
    cpa = _doc("2007-19")
    assert [p.eid for p in cpa.provisions if p.kind == "schedule_part"] == [
        "schedule-1-part-1",
        "schedule-1-part-2",
        "schedule-3-part-1",
        "schedule-3-part-2",
    ]
    part = _p(cpa, "schedule-1-part-1")
    assert part.num_path == ["1", "part:1"] and part.parent_eid == "schedule-1" and part.top_kind == "schedule"
    assert not _p(cpa, "schedule-1").is_leaf
    tfa = _doc("2001-50")  # a Convention reproduced in a Schedule, as numbered articles
    assert _p(tfa, "schedule-2").text.startswith("Text in the English language of the Convention")
    assert "Article 1" in _p(tfa, "schedule-2").text
    assert _p(_doc("2014-38-excerpt"), "schedule-5").text.startswith("Section 142.\n1. A company that is")


def test_internal_references_become_links_to_sections() -> None:
    nfo = _doc("1997-26")
    refs = _p(nfo, "section-1-2").refs  # "For the purposes of sections 17, 18 and 19 ..."
    assert [r.href for r in refs] == [f"/ie/act/1997/26/~section-{n}" for n in (17, 18, 19)]
    assert all(r.relation == "cites" for r in refs)
    cpa = _doc("2007-19")
    assert "/ie/act/2007/19/~schedule-2" in [r.href for r in _p(cpa, "section-1-2").refs]


def test_every_provision_is_well_formed() -> None:
    for name in NAMES:
        doc = _doc(name)
        eids = [p.eid for p in doc.provisions]
        assert len(eids) == len(set(eids)), name
        index = {p.eid: i for i, p in enumerate(doc.provisions)}
        for i, p in enumerate(doc.provisions):
            assert p.sort_order == i and p.text.strip(), (name, p.eid)
            assert p.parent_eid is None or index[p.parent_eid] < i  # parents come first
            assert p.is_leaf == (not any(q.parent_eid == p.eid for q in doc.provisions))


# --------------------------------------------------------------------------- citations


def test_citations_in_the_irish_convention() -> None:
    dpa = _doc("2018-7")
    assert _cite(dpa, "section-60-3-a") == "Data Protection Act 2018, s 60(3)(a)"
    assert _cite(dpa, "section-60-3-a-i") == "Data Protection Act 2018, s 60(3)(a)(i)"
    assert _cite(dpa, "section-60") == "Data Protection Act 2018, s 60"
    assert _cite(dpa, "schedule-1") == "Data Protection Act 2018, Sch 1"
    assert _cite(dpa, "long-title") == "Data Protection Act 2018, long title"
    assert _cite(dpa, "section-2-1-def-commission") == (
        "Data Protection Act 2018, s 2(1) (definition of \u201cCommission\u201d)"
    )
    assert _cite(_doc("1997-26"), "schedule") == "Non-Fatal Offences against the Person Act 1997, Sch"
    assert _cite(_doc("2001-50"), "section-4-1") == "Criminal Justice (Theft and Fraud Offences) Act 2001, s 4(1)"
    assert _cite(_doc("2007-19"), "schedule-1-part-1") == "Consumer Protection Act 2007, Sch 1, Pt 1"
    assert _cite(_doc("2014-38-excerpt"), "section-228-1-a") == "Companies Act 2014, s 228(1)(a)"
    assert _cite(_doc("1977-10"), "section-2-3-a-ii-i") == "Unfair Dismissals Act 1977, s 2(3)(a)(ii)(I)"


def test_module_pinpoint_cases() -> None:
    mod = get_module("ie")

    def cite(path: list[str], top: str = "section", title: str = "Test Act 2000") -> str:
        return mod.provision_citation(ProvisionCitationInput(title, "act", "IE", top, path, "en"))

    assert cite(["12A", "(1A)", "(aa)", "(iv)", "(II)", "(B)"]) == "Test Act 2000, s 12A(1A)(aa)(iv)(II)(B)"
    assert cite(["3", "(2)", "def:data subject"]) == "Test Act 2000, s 3(2) (definition of “data subject”)"
    assert cite(["2", "part:3", "(1)"], "schedule") == "Test Act 2000, Sch 2, Pt 3(1)"
    assert cite(["long title"], "long_title") == "Test Act 2000, long title"
    assert cite([]) == "Test Act 2000"
    with pytest.raises(Exception):  # noqa: B017, PT011 - strict ext model
        mod.validate_ext({"act_year": 2000, "act_number": 1, "unexpected": True})


# --------------------------------------------------------------------------- XML safety and loud failures


def test_publishers_bare_doctype_is_accepted_and_nothing_else() -> None:
    raw = _xml("2018-7")
    assert b'<!DOCTYPE act SYSTEM "legislation.dtd">' in raw[:200]  # the DTD is never fetched
    assert parse_irish_statute_book(raw)[0].work_uri == "/ie/act/2018/7"
    assert parse_irish_statute_book(_act(SECT, doctype='<!DOCTYPE act SYSTEM "legislation.dtd">'))[0].short_title
    for doctype in (
        '<!DOCTYPE act SYSTEM "legislation.dtd" [<!ENTITY e "x">]>',
        '<!DOCTYPE act [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>',
        '<!DOCTYPE act [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>',
        '<!DOCTYPE act SYSTEM "http://example.org/other.dtd">',
    ):
        with pytest.raises(IrishParseError, match="unsafe|well-formed"):
            parse_irish_statute_book(_act(SECT, doctype=doctype))


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"<html><body/></html>", "unsupported root"),
        (b"<act><metadata/>", "well-formed"),
        (b"<act><metadata><year>1999</year><number>1</number></metadata></act>", "missing metadata or body"),
        (_act(SECT, date_="1999-01-01"), "invalid dateofenactment"),
        (_act(SECT, date_="19991301"), "invalid dateofenactment"),
        (_act(SECT, date_="20010101"), "not in the Act year"),
        (_act("<p>no sections</p>"), "no sections"),
        (_act(SECT.replace("<number>1</number>", "<number></number>")), "without number"),
        (_act(SECT.replace("</sect>", "<surprise/></sect>")), "unsupported element <surprise>"),
        (_act(SECT.replace("Text.", "Text.<mystery/>")), "unsupported empty element <mystery/>"),
        (_act(SECT.replace("Text.", 'Text.<unicode ch="zz"/>')), "bad <unicode"),
        (_act(SECT.replace("<number>1</number>", "<number>1</number><year>x</year>")), "unsupported element"),
    ],
)
def test_malformed_or_unexpected_documents_fail_loudly(content: bytes, message: str) -> None:
    with pytest.raises(IrishParseError, match=message):
        parse_irish_statute_book(content)


def test_minimal_act_and_numbering_variants() -> None:
    body = (
        '<sect id="SEC1"><number>1.</number><title><p>Short title and commencement.</p></title>'
        '<p class="-3 11 0 left 1 0"><b>1.</b> (1) This Act may be cited as the Test Act, 1999.</p>'
        '<p class="-3 11 0 left 1 0">(2) This Act shall come into operation on the passing of this Act.</p></sect>'
        '<sect id="SEC2"><number>2</number><title><p>Other.</p></title>'
        '<p class="2 0 0 left 0 1"><b>2</b>.<emdash/> (1)  Spaced   text<emdash/></p>'
        '<p class="-5 9 0 left 0 1">(<i>a</i>) first,</p><p class="-5 9 0 left 0 1">(<i>b</i>) second.</p></sect>'
    )
    doc = parse_irish_statute_book(_act(body))[0]
    assert doc.short_title == "Test Act 1999" and doc.ext["commencement_basis"] == "relative_to_passing"
    assert {p.eid: p.text for p in doc.provisions}["section-2-1"] == "Spaced text—\n(a) first,\n(b) second."
    assert [p.eid for p in doc.provisions] == [
        "section-1",
        "section-1-1",
        "section-1-2",
        "section-2",
        "section-2-1",
        "section-2-1-a",
        "section-2-1-b",
    ]
    # without a "may be cited as" section the title is the metadata title, comma dropped
    assert parse_irish_statute_book(_act(SECT.replace("Test.", "Other."), year="2005", date_="20050301"))[
        0
    ].title.startswith("TEST ACT 2005")


def test_resolve_uri() -> None:
    assert resolve_uri("https://www.irishstatutebook.ie/eli/2018/act/7/enacted/en/html") == ("/ie/act/2018/7", None)
    assert resolve_uri("http://www.irishstatutebook.ie/eli/1997/act/26/enacted/en/xml") == ("/ie/act/1997/26", None)
    assert resolve_uri("https://www.irishstatutebook.ie/eli/2018/act/7") == ("/ie/act/2018/7", None)
    assert resolve_uri("https://www.irishstatutebook.ie/eli/2018/si/7/made/en/html") is None
    assert resolve_uri("https://example.org/eli/2018/act/7/enacted/en/xml") is None
    assert pipeline_resolve_uri("https://www.irishstatutebook.ie/eli/2018/act/7/enacted/en/html") == (
        "/ie/act/2018/7",
        None,
    )
    assert pipeline_resolve_uri("/ie/act/2018/7/~section-60") == ("/ie/act/2018/7", "section-60")


# --------------------------------------------------------------------------- connector


def _cache(tmp_path: Path, names: tuple[str, ...] = ("1977-10", "1997-26")) -> Path:
    cache = tmp_path / "cache"
    for name in names:
        year, number = name.split("-")[0], name.split("-")[1].split("-")[0]
        target = cache / "www.irishstatutebook.ie" / "eli" / year / "act" / number / "enacted" / "en" / "xml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_xml(name))
        info = META[name]
        target.with_name("xml.meta.json").write_text(
            json.dumps(
                {
                    "url": info["url"],
                    "final_url": info["final_url"],
                    "status": 200,
                    "content_type": "application/xml",
                    "retrieved_at": info["retrieved_at"],
                }
            )
        )
    return cache


def _manifest(tmp_path: Path, acts: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "manifest.yaml"
    data = {
        "schema_version": 1,
        "source_id": SOURCE,
        "base_url": "https://www.irishstatutebook.ie",
        "user_agent": "ULIS-test (+https://example.org)",
        "acts": acts,
        **extra,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


def test_connector_offline_from_cache(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    pin = hashlib.sha256(_xml("1977-10")).hexdigest()
    manifest = _manifest(tmp_path, [{"year": 1977, "number": 10, "sha256": pin}, {"year": 1997, "number": 26}])
    connector = IrishStatuteBookConnector(manifest, SOURCE, cache, offline=True)
    docs = list(connector.fetch())
    assert [d.external_id for d in docs] == ["irishstatutebook:act/1977/10", "irishstatutebook:act/1997/26"]
    assert docs[0].content == _xml("1977-10")  # exact bytes as received
    assert docs[0].uri == "https://www.irishstatutebook.ie/eli/1977/act/10/enacted/en/xml"
    assert docs[0].media_type == "application/xml" and docs[0].retrieved_at.year == 2026
    assert connector.http is not None and connector.http.requests_made == 0
    with pytest.raises(FetchError, match="offline and not cached"):
        list(
            IrishStatuteBookConnector(
                _manifest(tmp_path, [{"year": 2018, "number": 7}]), SOURCE, cache, offline=True
            ).fetch()
        )
    with pytest.raises(ManifestError, match="sha256"):
        bad = _manifest(tmp_path, [{"year": 1977, "number": 10, "sha256": "0" * 64}])
        list(IrishStatuteBookConnector(bad, SOURCE, cache, offline=True).fetch())


def test_connector_refuses_bad_manifests_and_non_xml_responses(tmp_path: Path) -> None:
    for acts, extra, message in (
        ([{"year": "1977", "number": 10}], {}, "invalid act reference"),
        ([{"year": 1977, "number": 0}], {}, "invalid act reference"),
        ([{"year": 1800, "number": 1}], {}, "invalid act reference"),
        ([{"year": 1977, "number": 10}], {"base_url": "http://www.irishstatutebook.ie"}, "base_url"),
        ([{"year": 1977, "number": 10}], {"user_agent": "python-urllib"}, "user_agent"),
        ([], {}, "no acts"),
    ):
        with pytest.raises(ManifestError, match=message):
            list(IrishStatuteBookConnector(_manifest(tmp_path, acts, **extra), SOURCE, tmp_path).fetch())
    with pytest.raises(ManifestError, match="mismatch"):
        other = _manifest(tmp_path, [{"year": 1977, "number": 10}], source_id="other")
        list(IrishStatuteBookConnector(other, SOURCE, tmp_path).fetch())
    # A cached HTML page (an error page, a login wall) is never passed on as an Act; nor a redirect to another host.
    cache = _cache(tmp_path, ("1977-10",))
    xml = cache / "www.irishstatutebook.ie" / "eli" / "1977" / "act" / "10" / "enacted" / "en" / "xml"
    xml.write_bytes(b"<!DOCTYPE html><html>Error</html>")
    connector = IrishStatuteBookConnector(
        _manifest(tmp_path, [{"year": 1977, "number": 10}]), SOURCE, cache, offline=True
    )
    assert list(connector.fetch()) == []  # reported, not passed on, and the run would go on with the next Act
    assert len(connector.fetch_errors) == 1 and "not Act XML" in connector.fetch_errors[0]
    with pytest.raises(ManifestError, match="listed twice"):
        twice = _manifest(tmp_path, [{"year": 1977, "number": 10}, {"year": 1977, "number": 10}])
        list(IrishStatuteBookConnector(twice, SOURCE, cache, offline=True).fetch())
    # Some old Acts (e.g. 1938/16) open with the processing instruction <?version xml="1.0"?>: well formed, accepted.
    odd = _act(SECT).replace(b'<?xml version="1.0"?>', b'<?version xml="1.0"?>')
    xml.write_bytes(odd)
    assert [
        d.content
        for d in IrishStatuteBookConnector(
            _manifest(tmp_path, [{"year": 1977, "number": 10}]), SOURCE, cache, offline=True
        ).fetch()
    ] == [odd]
    assert len(parse_irish_statute_book(odd)) == 1
    xml.write_bytes(_xml("1977-10"))
    meta = xml.with_name("xml.meta.json")
    info = json.loads(meta.read_text())
    meta.write_text(json.dumps({**info, "final_url": "https://example.org/act.xml"}))
    with pytest.raises(FetchError, match="redirected away"):
        list(
            IrishStatuteBookConnector(
                _manifest(tmp_path, [{"year": 1977, "number": 10}]), SOURCE, cache, offline=True
            ).fetch()
        )


def test_repository_manifest_and_pins_are_consistent(catalog: Catalog) -> None:
    path = REPO_ROOT / "config" / "manifests" / f"{SOURCE}.yaml"
    manifest = yaml.safe_load(path.read_text())
    acts = manifest["acts"]
    assert [(a["year"], a["number"]) for a in acts[:6]] == [
        (1977, 10),
        (1997, 26),
        (2001, 50),
        (2007, 19),
        (2014, 38),
        (2018, 7),
    ]
    assert all(len(a["sha256"]) == 64 for a in acts[:6]) and not any("sha256" in a for a in acts[6:])
    # the publisher's whole list of Public Acts: 1922 to date, each once, in the order of the year lists
    assert len(acts) > 3500 and len({(a["year"], a["number"]) for a in acts}) == len(acts)
    assert min(a["year"] for a in acts) == 1922 and max(a["year"] for a in acts) >= 2025
    assert acts[6:] == sorted(acts[6:], key=lambda a: (a["year"], a["number"]))
    assert all(a["title"] for a in acts)
    assert (1922, 1) in {(a["year"], a["number"]) for a in acts}
    # the committed manifest passes the connector's own validation
    IrishStatuteBookConnector(path, SOURCE, Path("/nonexistent"))._manifest()
    assert META["1977-10"]["bytes"] == len(_xml("1977-10"))
    assert hashlib.sha256(_xml("1977-10")).hexdigest() == acts[0]["sha256"]  # fixture = the pinned download
    assert hashlib.sha256(_xml("2018-7")).hexdigest() == acts[5]["sha256"]
    assert manifest["user_agent"].startswith("ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)")
    assert isinstance(build_connector(catalog.sources.get(SOURCE)), IrishStatuteBookConnector)


def test_source_and_profile_state_the_licence_and_the_gaps(catalog: Catalog) -> None:
    source = catalog.sources.get(SOURCE)
    licence_gate(source)
    assert source.licence.id == "IE-Oireachtas-Open-Data-PSI" and source.licence.attribution_required
    assert source.authority == "official_non_authentic" and source.languages == ["en"]
    assert source.licence.attribution_text and source.licence.attribution_text.startswith("Acts of the Oireachtas")
    assert "not the official text" in source.licence.attribution_text
    revised = catalog.sources.get("ie-revised-acts-lrc")  # no stated licence: recorded, never fetched
    assert revised.status == "planned" and revised.connector is None
    with pytest.raises(LicenceNotApproved, match="pending_review"):
        licence_gate(revised)
    profile = catalog.jurisdictions.get("IE")
    assert profile.verification.status == "draft_unverified" and profile.coverage.corpus_status == "pilot"
    assert [c.id for c in profile.courts] == ["IE-SC", "IE-CA", "IE-HC"]
    assert profile.legal_system.treaty_reception == "dualist" and profile.legal_system.families == ["common_law"]
    gaps = " ".join(profile.coverage.known_gaps)
    for phrase in ("as enacted", "NOT applied", "commencement", "Constitution", "English text only", "Revised Acts"):
        assert phrase in gaps
    assert "Data Protection Act 2018, s 60(3)(a)" in profile.citation.examples


# --------------------------------------------------------------------------- PostgreSQL


class _Fixtures:
    def fetch(self) -> Iterator[RawDocument]:
        for name in NAMES:
            info = META[name]
            yield RawDocument(
                f"irishstatutebook:fixture/{name}",
                info["final_url"],
                "application/xml",
                _xml(name),
                datetime.fromisoformat(info["retrieved_at"]).astimezone(UTC),
                {"fixture": name},
            )


@pytest.fixture(scope="module")
def ie_ingested(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ingested: IngestionReport
) -> IngestionReport:
    with session_factory() as session:
        report = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        session.commit()
    return report


def _search(session: Session, catalog: Catalog, **kw: Any) -> dict[str, Any]:
    return search(session, catalog, SearchRequest(jurisdiction="IE", **kw), today=date(2026, 10, 14))


@pytest.mark.db
def test_ingest_report_counts(ie_ingested: IngestionReport) -> None:
    assert ie_ingested.errors == [] and ie_ingested.conflicts == []
    assert ie_ingested.documents_new == len(NAMES) and ie_ingested.instruments_created == len(NAMES)
    assert ie_ingested.provision_versions_created == sum(len(_doc(n).provisions) for n in NAMES)
    assert ie_ingested.citation_links_created > 0 and ie_ingested.passages_indexed > 1000


@pytest.mark.db
def test_an_act_of_1938_is_found_as_enacted_and_says_so(
    session: Session, catalog: Catalog, ie_ingested: IngestionReport
) -> None:
    # The whole collection of Public Acts since 1922 is ingested as enacted: an old Act carries the same labels, and the
    # warnings that it is the Act as passed (no later amendment or repeal applied).
    res = _search(session, catalog, q="unlawful to import flour or wheaten meal unless", as_of=date(2026, 1, 1))
    hit = res["results"][0]
    assert hit["citation"].startswith("Agricultural Produce (Cereals) Act 1938, s 9")
    assert hit["text"].startswith("It shall not be lawful for any person to import any flour or wheaten meal")
    assert hit["version"]["valid_from"] == "1938-05-16"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {
        "EFFECTIVE_DATE_PRESUMED",
        "OUTSTANDING_CHANGES_INSTRUMENT",
        "NOT_AUTHENTICATED",
        "SOURCE_NOT_AUTHENTIC",
    } <= codes
    # the document title and the version both say "as enacted"
    assert hit["document"]["title"] == "Agricultural Produce (Cereals) Act 1938 (Number 16 of 1938, as enacted)"
    assert hit["version"]["consolidation"] == "as_enacted" and hit["version"]["text_origin"] == "official_conversion"


@pytest.mark.db
def test_search_finds_exact_text_with_citation_and_limitations(
    session: Session, catalog: Catalog, ie_ingested: IngestionReport
) -> None:
    res = _search(session, catalog, q="to safeguard cabinet confidentiality parliamentary privilege national security")
    hit = res["results"][0]
    assert hit["citation"] == "Data Protection Act 2018, s 60(3)(a)(i)"
    assert hit["text"].startswith("to safeguard cabinet confidentiality, parliamentary privilege, national security")
    assert hit["jurisdiction"]["id"] == "IE" and all(h["jurisdiction"]["id"] == "IE" for h in res["results"])
    assert hit["authority"]["category"] == "binding_legislation"
    codes = {lim["code"] for lim in hit["limitations"]}
    assert {
        "EFFECTIVE_DATE_PRESUMED",
        "NOT_AUTHENTICATED",
        "SOURCE_NOT_AUTHENTIC",
        "OUTSTANDING_CHANGES_INSTRUMENT",
        "CURRENCY_UNKNOWN",
        "PROFILE_UNVERIFIED",
    } <= codes
    presumed = next(lim for lim in hit["limitations"] if lim["code"] == "EFFECTIVE_DATE_PRESUMED")
    assert "2018-05-24" in presumed["message"]
    outstanding = next(lim for lim in hit["limitations"] if lim["code"] == "OUTSTANDING_CHANGES_INSTRUMENT")
    assert (
        "Later amendments, repeals and revocations" in outstanding["message"]
        and "Commencement" in outstanding["message"]
    )
    assert hit["source"]["attribution"].startswith("Acts of the Oireachtas from the electronic Irish Statute Book")
    assert "not the official text of the Act" in hit["source"]["attribution"]
    theft = _search(session, catalog, q="a person is guilty of theft if he or she dishonestly appropriates property")
    assert any(
        h["citation"] == "Criminal Justice (Theft and Fraud Offences) Act 2001, s 4(1)" for h in theft["results"]
    )
    company = _search(
        session, catalog, q="act in good faith in what the director considers to be the interests of the company"
    )
    cites = [h["citation"] for h in company["results"]]
    assert cites[0].startswith("Companies Act 2014, s 228(1)") and "Companies Act 2014, s 228(1)(a)" in cites
    dismissal = _search(session, catalog, q="dismissal of an employee shall be deemed to be an unfair dismissal")
    assert dismissal["results"][0]["citation"] == "Unfair Dismissals Act 1977, s 6(1)"
    assault = _search(session, catalog, q="assault causing harm summary conviction imprisonment 12 months")
    assert assault["results"][0]["citation"].startswith("Non-Fatal Offences against the Person Act 1997, s 3(2)")
    consumer = _search(
        session, catalog, q="a commercial practice is misleading if the trader omits or conceals material information"
    )
    assert consumer["results"][0]["citation"] == "Consumer Protection Act 2007, s 46(1)"


@pytest.mark.db
def test_point_in_time_follows_the_enactment_date(
    session: Session, catalog: Catalog, ie_ingested: IngestionReport
) -> None:
    uri = "/ie/act/2018/7/~section-60-3-a-i"
    before = provision_at(session, catalog, uri, date(2018, 5, 23))
    assert before["status_on_date"] == "not_yet_in_force" and before["version"] is None
    assert "NO_VERSION_ON_DATE" in {lim["code"] for lim in before["limitations"]}
    on_day = provision_at(session, catalog, uri, date(2018, 5, 24))
    assert on_day["status_on_date"] == "in_force" and on_day["version"]["valid_from"] == "2018-05-24"
    assert on_day["provision"]["citation"] == "Data Protection Act 2018, s 60(3)(a)(i)"
    assert on_day["version"]["text"].startswith("to safeguard cabinet confidentiality")
    now = provision_at(session, catalog, uri, date(2026, 10, 14))
    assert now["status_on_date"] == "in_force" and now["version"]["valid_to"] is None
    assert {"EFFECTIVE_DATE_PRESUMED", "OUTSTANDING_CHANGES_INSTRUMENT"} <= {lim["code"] for lim in now["limitations"]}
    nothing = _search(session, catalog, q="to safeguard cabinet confidentiality", as_of=date(2018, 5, 23))
    assert not any("Data Protection Act 2018" in h["citation"] for h in nothing["results"])
    # a section of the 1977 Act is not yet law in 1976
    assert (
        provision_at(session, catalog, "/ie/act/1977/10/~section-6-1", date(1977, 4, 5))["status_on_date"]
        == "not_yet_in_force"
    )
    assert (
        provision_at(session, catalog, "/ie/act/1977/10/~section-6-1", date(1977, 4, 6))["status_on_date"] == "in_force"
    )


@pytest.mark.db
def test_reingest_is_idempotent_and_sources_verify(
    session_factory: sessionmaker[Session], catalog: Catalog, store: LocalObjectStore, ie_ingested: IngestionReport
) -> None:
    with session_factory() as session:
        again = Ingestor(session, catalog, store).ingest_source(SOURCE, actor="pytest", connector=_Fixtures())
        assert again.documents_unchanged == len(NAMES) and again.provision_versions_created == 0
        ids = session.scalars(select(m.SourceDocument.id).where(m.SourceDocument.source_id == SOURCE)).all()
        results = [verify_source_document(session, catalog, store, i, checker="pytest") for i in ids]
        assert len(results) == len(NAMES) and all(r["result"] == "pass" for r in results)
        assert sum(r["checked_units"] for r in results) == ie_ingested.provision_versions_created
        session.rollback()


def test_fixtures_readme_names_the_source_and_licence() -> None:
    text = (FIX / "README.md").read_text()
    assert "irishstatutebook.ie" in text and "Oireachtas (Open Data) PSI Licence" in text
