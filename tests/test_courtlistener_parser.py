"""CourtListener bulk parser: text extraction, units, citations, the bundle to ParsedDocument mapping.

Logic tests use short synthetic markup in the layouts the publisher's columns have (Harvard XML with page-number,
Lawbox/Resource HTML with star-pagination, plain text); the real-case tests are in test_courtlistener_real_cases.py.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from ulis.sources.normalized import ParseSkipped
from ulis.sources.parsers import courtlistener_bulk as p

HARVARD = (
    '<opinion type="majority">\n<author id="a1">Smith, J.</author>\n'
    '<p id="b1">First &amp; foremost <em>the rule</em> applies.<footnotemark>1</footnotemark> '
    'More text <page-number citation-index="1" label="102">*102</page-number>continues here.</p>\n'
    '<headnotes id="h1">1. Editorial headnote that is not the opinion.</headnotes>\n'
    '<attorneys id="at">Counsel for appellant.</attorneys>\n'
    '<blockquote id="q1"><p id="b2">Quoted text.</p></blockquote>\n'
    '<p id="b3">Next <page-number citation-index="2" label="999">*999</page-number>parallel page. '
    '<page-number citation-index="1" label="103">*103</page-number>Last words.</p>\n'
    '<footnote label="1"><p id="f1">. A note on the first point.</p></footnote>\n'
    '<img src="data:image/png;base64,AAAA" />\n</opinion>'
)


def texts(atoms: list[p.Atom]) -> list[str]:
    return [a.value for a in atoms if a.kind == "p"]


def test_harvard_xml_gives_paragraphs_and_page_breaks_without_editorial_head_matter() -> None:
    atoms = p.extract("xml_harvard", HARVARD)
    assert [(a.kind, a.value) for a in atoms] == [
        ("p", "Smith, J."),
        ("p", "First & foremost the rule applies. More text"),
        ("page", "102"),
        ("p", "continues here."),
        ("p", "Quoted text."),
        ("p", "Next"),  # the parallel page break (citation-index 2) is ignored, text goes on
        ("p", "parallel page."),
        ("page", "103"),
        ("p", "Last words."),
        ("p", "1. A note on the first point."),
    ]
    joined = " ".join(texts(atoms))
    assert "headnote" not in joined and "Counsel" not in joined and "base64" not in joined and "*102" not in joined


def test_text_before_the_first_page_break_is_on_the_previous_page() -> None:
    atoms = p.extract("xml_harvard", HARVARD)
    units = p.page_units(atoms)
    assert [label for label, _ in units] == ["101", "102", "103"]
    assert units[0][1] == "Smith, J.\n\nFirst & foremost the rule applies. More text"
    assert units[1][1].startswith("continues here.\n\nQuoted text.")
    assert units[2][1] == "Last words.\n\n1. A note on the first point."


def test_an_opinion_without_page_breaks_gives_blocks_with_no_page() -> None:
    atoms = p.extract_plain("One.\n\nTwo.")
    units = p.page_units(atoms)
    assert units == [(None, "One.\n\nTwo.")]


def test_star_pagination_spans_of_the_html_collections() -> None:
    html = (
        "<center><b>945 P.2d 52 (1997)</b></center><p>STATE v. REAMS</p>"
        '<p><span class="star-pagination">*53</span> Counsel list.</p>'
        '<p>Opinion text <span class="star-pagination" label="54" citation-index="1">*54</span>on page fifty-four.</p>'
        '<p>Other scheme <span class="star-pagination" label="7" citation-index="2">*7</span>ignored.</p>'
    )
    atoms = p.extract("html_lawbox", html)
    assert [(a.kind, a.value) for a in atoms] == [
        ("p", "945 P.2d 52 (1997)"),
        ("p", "STATE v. REAMS"),
        ("page", "53"),
        ("p", "Counsel list."),
        ("p", "Opinion text"),
        ("page", "54"),
        ("p", "on page fifty-four."),
        ("p", "Other scheme"),
        ("p", "ignored."),
    ]


def test_html_blocks_tables_scripts_and_entities() -> None:
    html = (
        "<html><head><title>x</title><script>var a = 1;</script></head><body><nav>menu</nav>"
        "<div><h2>HEADING</h2><p>Para&nbsp;one &#167; 12 &mdash; end.<br>Same paragraph.</p>"
        "<table><tr><td>cell a</td><td>cell b</td></tr></table>"
        "<pre>line one\nline two\n\nnew para</pre></div></body></html>"
    )
    assert texts(p.extract("html", html)) == [
        "HEADING",
        "Para one § 12 — end. Same paragraph.",
        "cell a cell b",
        "line one line two",
        "new para",
    ]


def test_plain_text_is_unwrapped_into_paragraphs() -> None:
    raw = "IN THE COURT\r\n   OF APPEALS\r\n\r\n   The first paragraph\r\n   wraps lines.\r\n\f\r\nSecond."
    assert texts(p.extract("plain_text", raw)) == [
        "IN THE COURT OF APPEALS",
        "The first paragraph wraps lines.",
        "Second.",
    ]


def test_pack_makes_blocks_of_whole_paragraphs_and_cuts_only_enormous_ones() -> None:
    paragraphs = [f"Paragraph {i}. " + "word " * 120 for i in range(10)]  # about 620 characters each
    blocks = p.pack(paragraphs)
    assert "\n\n".join(blocks).split("\n\n") == paragraphs  # nothing lost, nothing cut inside
    assert all(len(b) >= p.MIN_UNIT for b in blocks[:-1]) and 2 <= len(blocks) <= 5
    huge = ("Sentence number one is here. " * 600).strip()
    pieces = p.pack([huge])
    assert len(pieces) > 1 and all(len(x) <= p.MAX_UNIT for x in pieces)
    assert " ".join(pieces) == huge


def test_citations_are_deduplicated_and_ranked() -> None:
    raw = [
        {"volume": "93", "reporter": "S. Ct.", "page": "705", "type": 1},
        {"volume": "410", "reporter": "U.S.", "page": "113", "type": 1},
        {"volume": "410", "reporter": "U.S.", "page": "113", "type": 1},
        {"volume": "2019", "reporter": "WL", "page": "55", "type": 7},
        {"volume": "2019", "reporter": "ND", "page": "15", "type": 8},
        {"volume": "", "reporter": "", "page": "1", "type": 1},
    ]
    cites = p.clean_citations(raw)
    assert [c.text for c in cites] == ["93 S. Ct. 705", "410 U.S. 113", "2019 WL 55", "2019 ND 15"]
    best = p.primary_citation(cites)
    assert best is not None and best.text == "2019 ND 15"  # a neutral citation first, then the federal reporters
    assert p.primary_citation(cites[:3]).text == "410 U.S. 113"  # type: ignore[union-attr]  # the official reports first
    assert p.primary_citation([]) is None


def test_database_citations_are_never_reporter_citations_and_the_official_reports_come_first() -> None:
    raw = [
        {"volume": "93", "reporter": "S. Ct.", "page": "705", "type": 1},
        {"volume": "35", "reporter": "L. Ed. 2d", "page": "147", "type": 1},
        {"volume": "410", "reporter": "U.S.", "page": "113", "type": 1},
        {"volume": "1999", "reporter": "N.Y. App. Div. LEXIS", "page": "55", "type": 2},
    ]
    cites = p.clean_citations(raw)
    assert [c.type for c in cites] == [1, 1, 1, p.LEXIS]
    best = p.primary_citation(cites)
    assert best is not None and best.text == "410 U.S. 113"
    only_database = p.clean_citations([raw[3], {"volume": "2019", "reporter": "WL", "page": "5", "type": 7}])
    assert p.primary_citation(only_database).text == "2019 WL 5"  # type: ignore[union-attr]


def bundle(**overrides: Any) -> bytes:
    b: dict[str, Any] = {
        "schema": p.BUNDLE_SCHEMA,
        "continuation": False,
        "court": {"id": "wash", "full_name": "Washington Supreme Court", "citation_string": "Wash."},
        "docket": {"id": 5, "docket_number": "No. 12345-6", "case_name": "Doe v. Roe"},
        "cluster": {
            "id": 777,
            "case_name": "Doe v. Roe",
            "case_name_full": "",
            "date_filed": "1970-03-04",
            "date_filed_is_approximate": False,
            "source": "U",
            "precedential_status": "Published",
            "judges": "Smith, J.",
            "disposition": "Affirmed",
            "syllabus": "",
            "scdb_id": "",
            "filepath_json_harvard": "",
        },
        "citations": [{"volume": "99", "reporter": "Wash. 2d", "page": "100", "type": 2}],
        "opinions": [
            {
                "id": 1,
                "type": "020lead",
                "author_str": "Smith",
                "joined_by_str": "",
                "per_curiam": False,
                "sha1": "",
                "download_url": "",
                "extracted_by_ocr": True,
                "text_column": "xml_harvard",
                "text": HARVARD,
            }
        ],
    }
    for key, value in overrides.items():
        section, _, field = key.partition("__")
        if field:
            b[section][field] = value
        else:
            b[key] = value
    return json.dumps(b).encode()


def test_a_case_whose_first_break_fits_its_citation_is_paged_and_cited_with_pinpoints() -> None:
    doc = p.parse(bundle(citations=[{"volume": "99", "reporter": "Wash. 2d", "page": "101", "type": 2}]))[0]
    assert doc.work_uri == "/us/case/cl/777" and doc.expression_uri == "/us/case/cl/777/eng@1970-03-04"
    assert doc.judgment is not None and doc.judgment.neutral_citation == "99 Wash. 2d 101"
    assert doc.judgment.court_id == "US-WA-SC" and doc.judgment.docket == "No. 12345-6"
    assert [(x.num, x.part) for x in doc.paragraphs] == [
        ("101", "Lead opinion (Smith)"),
        ("102", "Lead opinion (Smith)"),
        ("103", "Lead opinion (Smith)"),
    ]
    assert doc.ext["unit_scheme"] == "page" and doc.ext["page_citation"] == "99 Wash. 2d 101"
    assert (
        doc.judgment.neutral_citation == doc.ext["page_citation"]
    )  # one citation here: the page citation is the one cited
    assert (
        doc.ext["citation_kind"] == "reporter" and doc.ext["text_columns"] == ["xml_harvard"] and doc.ext["ocr"] is True
    )
    assert doc.authoritative is False and doc.text_origin == "unofficial_compilation"
    assert doc.version_date.isoformat() == "1970-03-04" and doc.jurisdiction_id == "US"
    assert len({x.eid for x in doc.paragraphs}) == 3


def test_a_page_scheme_that_does_not_fit_the_citation_falls_back_to_blocks() -> None:
    doc = p.parse(bundle(citations=[{"volume": "99", "reporter": "Wash. 2d", "page": "500", "type": 2}]))[0]
    assert doc.ext["unit_scheme"] == "block" and "page_citation" not in doc.ext
    assert [x.num for x in doc.paragraphs] == ["1"]
    assert "*102" not in doc.paragraphs[0].text
    assert doc.judgment is not None and doc.judgment.neutral_citation == "99 Wash. 2d 500"


def test_no_citation_means_the_docket_number_and_no_reporter_citation() -> None:
    doc = p.parse(bundle(citations=[]))[0]
    assert doc.judgment is not None and doc.judgment.neutral_citation is None
    assert doc.ext["citation_kind"] == "docket" and "citations" not in doc.ext
    assert doc.number == "No. 12345-6"


def test_syllabus_and_several_opinions_become_labelled_parts() -> None:
    dissent = {
        "id": 2,
        "type": "040dissent",
        "author_str": "",
        "joined_by_str": "",
        "per_curiam": False,
        "sha1": "",
        "download_url": "https://example.test/x.pdf",
        "extracted_by_ocr": False,
        "text_column": "plain_text",
        "text": "I dissent.\n\nFor these reasons.",
    }
    b = json.loads(bundle(cluster__syllabus="<p>The syllabus.</p>", citations=[]))
    b["opinions"].append(dissent)
    doc = p.parse(json.dumps(b).encode())[0]
    assert [x.part for x in doc.paragraphs][0] == "Syllabus"
    assert [x.part for x in doc.paragraphs][-1] == "Dissenting opinion"
    assert doc.paragraphs[0].text == "The syllabus."
    assert doc.ext["text_columns"] == ["xml_harvard", "plain_text"] and doc.ext["opinion_ids"] == [1, 2]
    assert doc.ext["download_urls"] == ["https://example.test/x.pdf"]
    assert [x.sort_order for x in doc.paragraphs] == list(range(1, len(doc.paragraphs) + 1))


def test_part_labels_fit_their_column_and_keep_a_long_author_name_in_ext() -> None:
    b = json.loads(bundle(citations=[]))
    b["opinions"][0]["type"] = "035concurrenceinpart"
    b["opinions"][0]["author_str"] = "Chief Justice Alexander Hamilton Jefferson"
    doc = p.parse(json.dumps(b).encode())[0]
    assert {x.part for x in doc.paragraphs} == {"Opinion concurring in part"}
    assert doc.ext["authors"] == ["Opinion concurring in part: Chief Justice Alexander Hamilton Jefferson"]
    assert all(len(x.part) <= p.PART_MAX and len(x.num or "") <= p.NUM_MAX for x in doc.paragraphs)


def test_continuation_documents_have_their_own_expression_and_no_syllabus() -> None:
    doc = p.parse(bundle(continuation=True))[0]
    assert doc.expression_uri == "/us/case/cl/777/eng@1970-03-04+1" and doc.ext["continuation"] is True


def test_refusals_and_skips() -> None:
    with pytest.raises(ValueError, match="not modelled"):
        p.parse(bundle(court__id="no-such-court"))
    with pytest.raises(ValueError, match="not accepted"):
        b = json.loads(bundle())
        b["opinions"][0]["text_column"] = "html_anon_2020"
        p.parse(json.dumps(b).encode())
    with pytest.raises(ValueError, match="not a ulis-us-courtlistener/1 bundle"):
        p.parse(b'{"schema": "other"}')
    with pytest.raises(ParseSkipped, match="no case name"):
        p.parse(bundle(cluster__case_name="", docket__case_name=""))
    b = json.loads(bundle())
    b["opinions"][0]["text"] = "<p></p>"
    with pytest.raises(ParseSkipped, match="no opinion text"):
        p.parse(json.dumps(b).encode())
