"""The US module's citation and unit labels for CourtListener cases (pure functions, no database)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from ulis.jurisdictions.modules.base import JudgmentCitationInput
from ulis.jurisdictions.modules.us import UsModule


def cite(**kw: Any) -> str:
    base: dict[str, Any] = {
        "case_name": "Doe v. Roe",
        "neutral_citation": None,
        "decision_date": date(1997, 9, 22),
        "docket": None,
        "court_id": "US-WA-SC",
        "court_name": "Washington Supreme Court",
        "paragraph": None,
        "language": "en",
        "jurisdiction_id": "US",
        "ext": {"courtlistener_cluster": 1, "court_citation": "Wash.", "citation_kind": "reporter"},
    }
    base.update(kw)
    return UsModule().judgment_citation(JudgmentCitationInput(**base))


def test_supreme_court_case_is_cited_by_its_reporter_with_the_page_and_no_court() -> None:
    ext = {
        "courtlistener_cluster": 1,
        "citation_kind": "reporter",
        "unit_scheme": "page",
        "page_citation": "410 U.S. 113",
    }
    got = cite(
        case_name="Roe v. Wade",
        neutral_citation="410 U.S. 113",
        court_id="US-SC",
        court_name="Supreme Court",
        decision_date=date(1973, 1, 22),
        paragraph="153",
        ext=ext,
    )
    assert got == "Roe v. Wade, 410 U.S. 113, 153 (1973)"


def test_state_case_has_the_court_and_year_and_a_pinpoint_only_for_the_cited_reporters_pages() -> None:
    state = {"courtlistener_cluster": 1, "court_citation": "Mont.", "citation_kind": "reporter"}
    assert cite(neutral_citation="945 P.2d 52", ext=state) == "Doe v. Roe, 945 P.2d 52 (Mont. 1997)"
    pages = state | {"unit_scheme": "page", "page_citation": "945 P.2d 52"}
    assert cite(neutral_citation="945 P.2d 52", paragraph="53", ext=pages) == "Doe v. Roe, 945 P.2d 52, 53 (Mont. 1997)"
    other = state | {"unit_scheme": "page", "page_citation": "283 Mont. 1"}  # the pages belong to another citation
    assert cite(neutral_citation="945 P.2d 52", paragraph="53", ext=other) == "Doe v. Roe, 945 P.2d 52 (Mont. 1997)"
    blocks = state | {"unit_scheme": "block"}  # a running number of the unit is not a page
    assert cite(neutral_citation="945 P.2d 52", paragraph="2", ext=blocks) == "Doe v. Roe, 945 P.2d 52 (Mont. 1997)"


def test_neutral_citation_stands_alone_and_a_case_without_citation_uses_docket_court_and_date() -> None:
    neutral = {"courtlistener_cluster": 1, "citation_kind": "neutral"}
    assert cite(neutral_citation="2019 ND 15", ext=neutral) == "Doe v. Roe, 2019 ND 15"
    docket = {"courtlistener_cluster": 1, "court_citation": "9th Cir.", "citation_kind": "docket"}
    assert cite(court_id="US-CA9", docket="99-1234", ext=docket) == "Doe v. Roe, No. 99-1234 (9th Cir. Sept. 22, 1997)"
    database = {"courtlistener_cluster": 1, "court_citation": "9th Cir.", "citation_kind": "database"}
    assert cite(court_id="US-CA9", neutral_citation="2019 WL 5", ext=database) == (
        "Doe v. Roe, 2019 WL 5 (9th Cir. Sept. 22, 1997)"
    )
    federal = {"courtlistener_cluster": 1, "citation_kind": "docket"}  # no court string: the federal abbreviation table
    assert cite(court_id="US-CA9", docket="99-1234", ext=federal) == "Doe v. Roe, No. 99-1234 (9th Cir. Sept. 22, 1997)"


@pytest.mark.parametrize(
    ("num", "part", "ext", "label"),
    [
        ("153", "Opinion", {"courtlistener_cluster": 1, "unit_scheme": "page"}, "p. 153"),
        (None, "Dissenting opinion", {"courtlistener_cluster": 1, "unit_scheme": "page"}, "Dissenting opinion"),
        ("syllabus 1", "Syllabus", {"courtlistener_cluster": 1, "unit_scheme": "page"}, "Syllabus"),
        ("3", "Opinion", {"courtlistener_cluster": 1, "unit_scheme": "block"}, "Opinion, text block 3"),
        # the other US sources keep their labels
        ("153", "Opinion of the Court", {"reporter": "U.S."}, "p. 153"),
        ("2", "opinion", {"case_number": "23-1"}, "PDF p. 2"),
    ],
)
def test_unit_labels_never_claim_a_page_or_paragraph_the_source_does_not_have(
    num: str | None, part: str, ext: dict[str, Any], label: str
) -> None:
    assert UsModule().judgment_unit_label(num, part, ext) == label


def test_ext_of_a_courtlistener_case_is_validated_and_unknown_fields_are_refused() -> None:
    module = UsModule()
    ok = module.validate_ext(
        {"courtlistener_cluster": 5, "citations": ["1 U.S. 1"], "unit_scheme": "page", "ocr": True}
    )
    assert ok == {"courtlistener_cluster": 5, "citations": ["1 U.S. 1"], "unit_scheme": "page", "ocr": True}
    with pytest.raises(ValueError):
        module.validate_ext({"courtlistener_cluster": 5, "unknown_field": 1})
