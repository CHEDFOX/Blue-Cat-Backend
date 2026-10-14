"""Research pipeline with scripted FakeLLM conversations over the fictional sample corpus (PostgreSQL)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

import pytest
from assistant_support import (
    CONFIG,
    S12_1,
    S12_1_TEXT_NOW,
    draft_reply,
    good_draft,
    happy_script,
    notes_reply,
    run_pipeline,
    understanding_reply,
    with_output_tokens,
)
from sqlalchemy.orm import Session, sessionmaker

from ulis.assistant.llm import FakeLLM, LLMError, LLMResponse, text_response, tool_use_response
from ulis.assistant.pipeline import PipelineError, Refused
from ulis.assistant.tools import tool_specs
from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport

pytestmark = pytest.mark.db


@pytest.fixture
def sf(session_factory: sessionmaker[Session], ingested: IngestionReport) -> sessionmaker[Session]:
    return session_factory


def test_understands_searches_cites_and_verifies(sf: sessionmaker[Session], catalog: Catalog) -> None:
    llm = FakeLLM(happy_script())
    progress: list[tuple[str, str]] = []
    result = run_pipeline(sf, catalog, llm, progress=progress)

    # understanding: defaults to the requested jurisdiction, flags coverage
    u = result.understanding
    assert [(j.id, j.covered) for j in u.jurisdictions] == [("XA", True)]
    assert u.jurisdictions[0].reason == "Specified in the request."
    assert [i.id for i in u.issues] == ["I1"] and u.as_of == "2024-01-01"

    # searching: the real provision was retrieved and registered, and is the only thing cited
    assert result.usage.searches == 1
    guidance = result.guidance.issues[0]
    assert guidance.issue_id == "I1" and guidance.reasoning
    cited = {i for r in guidance.reasoning for i in r.authority_ids}
    assert cited == {a.id for a in result.authorities}
    (authority,) = result.authorities
    assert authority.citation == S12_1 and authority.kind == "provision" and authority.verified
    assert authority.text == S12_1_TEXT_NOW  # database text, as of 2024
    assert authority.text_sha256 == hashlib.sha256(authority.text.encode()).hexdigest()
    assert authority.valid_from == "2022-01-01" and authority.valid_to is None
    assert authority.source.name and authority.limitations
    assert any(lim.code == "FICTIONAL_DATA" for lim in authority.limitations)

    # verifying: nothing removed, but a critical limitation (fictional data) caps confidence
    assert result.removed_claims == 0 and result.verification.quotes_checked == 1
    assert guidance.confidence == "medium"
    assert any("Limitation of" in u for u in guidance.uncertainties)
    assert result.guidance.summary.startswith("Section 12(1)")
    assert result.guidance.next_steps[-1].startswith("Verify every cited authority")

    # coverage notes and mandatory disclaimer
    assert any("fictional test data" in n for n in result.coverage_notes)
    assert "not legal advice" in result.disclaimer
    assert {"NOT_LEGAL_ADVICE", "AI_DRAFTED"} <= {lim.code for lim in result.limitations}

    # progress covers all four stages in order
    stages = [s for s, _ in progress]
    assert [s for i, s in enumerate(stages) if i == 0 or s != stages[i - 1]] == [
        "understanding",
        "searching",
        "drafting",
        "verifying",
    ]
    assert llm.remaining == 0
    assert result.usage.model == "fake-model" and result.usage.input_tokens > 0


def test_point_in_time_search_returns_the_version_in_force(sf: sessionmaker[Session], catalog: Catalog) -> None:
    script = happy_script()
    script[1] = tool_use_response(
        ("search_law", {"query": "retain personal data", "jurisdiction": "XA", "as_of": "2020-06-01"})
    )
    script[2] = tool_use_response(
        ("get_provision", {"uri": "/akn/xa/act/2015/12/~sec_12__subsec_1", "as_of": "2020-06-01"}), start=10
    )
    script[4] = draft_reply(
        lambda auth, _r: {
            "summary": "",
            "issues": [
                {
                    "issue_id": "I1",
                    "conclusion": "Seven years then applied.",
                    "confidence": "medium",
                    "reasoning": [
                        {"text": 'It said "seven years".', "authority_ids": [auth[S12_1]["id"]]},
                    ],
                    "uncertainties": [],
                }
            ],
            "next_steps": [],
        }
    )
    from datetime import date

    result = run_pipeline(sf, catalog, FakeLLM(script), as_of=date(2020, 6, 1))
    (authority,) = result.authorities
    assert "seven years" in authority.text and authority.valid_to == "2022-01-01"
    assert result.removed_claims == 0


def test_model_can_infer_jurisdiction_and_date_and_says_why(sf: sessionmaker[Session], catalog: Catalog) -> None:
    llm = FakeLLM([understanding_reply(jurisdictions=("DE", "NOT-A-PROFILE", "XA"), as_of="2021-03-04")])
    result = run_pipeline(sf, catalog, llm, jurisdiction=None, as_of=None)
    # an unknown profile id and a fictional jurisdiction (not offered for a real request) are dropped
    assert [j.id for j in result.understanding.jurisdictions] == ["DE"]
    prompt = str(llm.requests[0].messages[0]["content"])
    assert "DE = Germany" in prompt and "XA = " not in prompt
    assert result.understanding.as_of == "2021-03-04"
    assert any("inferred by the model" in n and "governed by the law of DE" in n for n in result.coverage_notes)
    assert result.understanding.jurisdictions[0].reason == "The matter is governed by the law of DE."


def test_nothing_relevant_retrieved_skips_drafting_and_says_so(sf: sessionmaker[Session], catalog: Catalog) -> None:
    llm = FakeLLM(
        [
            understanding_reply(),
            text_response({"notes": "", "relevant_authority_ids": [], "gaps": ["Nothing relevant found."]}),
        ]
    )
    result = run_pipeline(sf, catalog, llm)
    assert "XA = " in str(llm.requests[0].messages[0]["content"])  # fictional jurisdictions are offered when asked for
    assert llm.remaining == 0 and [r.stage for r in llm.requests] == ["understanding", "searching"]
    issue = result.guidance.issues[0]
    assert issue.conclusion == "insufficient authority indexed" and issue.confidence == "low"
    assert "Nothing relevant found." in issue.uncertainties and result.authorities == []
    assert "No indexed authority supports guidance" in result.guidance.summary


def test_uncovered_or_unidentified_jurisdictions_are_stated_not_papered_over(
    sf: sessionmaker[Session], catalog: Catalog
) -> None:
    llm = FakeLLM([understanding_reply(jurisdictions=("DE",))])
    result = run_pipeline(sf, catalog, llm, jurisdiction="DE")
    assert result.understanding.jurisdictions[0].covered is False
    assert result.usage.searches == 0 and result.authorities == []
    assert any("no legal materials are indexed" in n for n in result.coverage_notes)
    assert result.guidance.issues[0].conclusion == "insufficient authority indexed"
    assert "nothing was searched" in " ".join(result.guidance.issues[0].uncertainties)

    none = run_pipeline(sf, catalog, FakeLLM([understanding_reply(jurisdictions=())]), jurisdiction=None)
    assert none.understanding.jurisdictions == []
    assert any("No governing jurisdiction could be identified" in n for n in none.coverage_notes)


def test_no_issue_found_gives_a_clear_summary(sf: sessionmaker[Session], catalog: Catalog) -> None:
    result = run_pipeline(sf, catalog, FakeLLM([understanding_reply(issues=())]))
    assert result.guidance.issues == [] and "restate" in result.guidance.summary


def test_search_cap_is_enforced_and_reported(sf: sessionmaker[Session], catalog: Catalog) -> None:
    config = replace(CONFIG, max_searches_standard=2)
    searches = [
        ("search_law", {"query": f"personal data {n}", "jurisdiction": "XA", "as_of": "2024-01-01"}) for n in range(5)
    ]
    llm = FakeLLM(
        [
            understanding_reply(),
            tool_use_response(*searches),
            text_response({"notes": "x", "relevant_authority_ids": [], "gaps": []}),
        ]
    )
    result = run_pipeline(sf, catalog, llm, config=config)
    assert result.usage.searches == 2
    tool_results = llm.requests[2].messages[-1]["content"]
    assert [bool(b.get("is_error")) for b in tool_results] == [False, False, True, True, True]
    assert "search budget" in json.loads(tool_results[2]["content"])["error"]
    assert any(lim.code == "RESEARCH_BUDGET_REACHED" for lim in result.limitations)


def test_deep_depth_uses_the_larger_search_cap(sf: sessionmaker[Session], catalog: Catalog) -> None:
    config = replace(CONFIG, max_searches_standard=1, max_searches_deep=3)
    searches = [
        ("search_law", {"query": f"personal data {n}", "jurisdiction": "XA", "as_of": "2024-01-01"}) for n in range(5)
    ]
    llm = FakeLLM(
        [
            understanding_reply(),
            tool_use_response(*searches),
            text_response({"notes": "", "relevant_authority_ids": []}),
        ]
    )
    result = run_pipeline(sf, catalog, llm, config=config, depth="deep")
    assert result.usage.searches == 3


def test_tool_misuse_is_answered_with_errors_not_crashes(sf: sessionmaker[Session], catalog: Catalog) -> None:
    bad_calls = [
        ("delete_everything", {}),
        ("search_law", {"query": "x", "jurisdiction": "ZZ", "as_of": "2024-01-01"}),
        ("search_law", {"query": "x", "jurisdiction": "XA", "as_of": "next tuesday"}),
        ("get_provision", {"uri": "/akn/xa/act/nope", "as_of": "2024-01-01"}),
        ("coverage", {"jurisdiction": "XA", "extra": 1}),
    ]
    llm = FakeLLM(
        [
            understanding_reply(),
            tool_use_response(*bad_calls),
            text_response({"notes": "", "relevant_authority_ids": []}),
        ]
    )
    result = run_pipeline(sf, catalog, llm)
    blocks = llm.requests[2].messages[-1]["content"]
    assert [bool(b.get("is_error")) for b in blocks] == [True, True, True, True, False]
    assert result.usage.searches == 0  # invalid searches do not consume the budget


def test_unparseable_model_output_is_repaired_once_then_fails(sf: sessionmaker[Session], catalog: Catalog) -> None:
    ok = FakeLLM(["I could not do that as JSON.", understanding_reply(issues=())])
    assert run_pipeline(sf, catalog, ok).guidance.issues == []
    assert "could not be used" in str(ok.requests[1].messages[-1]["content"])
    bad = FakeLLM(["nope", "still nope"])
    with pytest.raises(PipelineError) as err:
        run_pipeline(sf, catalog, bad)
    assert err.value.code == "model_output_invalid"


def test_refusal_and_provider_errors_surface_as_distinct_outcomes(sf: sessionmaker[Session], catalog: Catalog) -> None:
    refusal = LLMResponse("", [], "refusal", text_response("").usage, [], "fake-model")
    with pytest.raises(Refused):
        run_pipeline(sf, catalog, FakeLLM([refusal]))
    with pytest.raises(LLMError) as err:
        run_pipeline(sf, catalog, FakeLLM([]))
    assert err.value.code == "fake_script_exhausted"


def test_output_token_budget_stops_further_model_calls(sf: sessionmaker[Session], catalog: Catalog) -> None:
    config = replace(CONFIG, max_output_tokens=4000)
    llm = FakeLLM([with_output_tokens(text_response(understanding_reply()), 2000)])
    result = run_pipeline(sf, catalog, llm, config=config)
    assert result.usage.searches == 0 and llm.remaining == 0
    assert any(lim.code == "RESEARCH_BUDGET_REACHED" for lim in result.limitations)
    assert result.usage.output_tokens == 2000
    assert all(r.max_tokens <= 4000 for r in llm.requests)


def test_drafting_only_sees_extracted_facts_and_delimited_untrusted_text(
    sf: sessionmaker[Session], catalog: Catalog
) -> None:
    secret = "ZEBRA-CLIENT-7731"
    llm = FakeLLM(happy_script())
    run_pipeline(sf, catalog, llm, case_text=f"Client file {secret}: the controller keeps backups.")
    first = str(llm.requests[0].messages[0]["content"])
    assert secret in first and "<untrusted_case_file id=" in first and "</untrusted_case_file id=" in first
    for later in llm.requests[1:]:
        assert secret not in json.dumps(later.messages, ensure_ascii=False)
        assert secret not in later.system
    assert all(r.max_tokens > 0 for r in llm.requests)
    assert llm.requests[0].json_schema is not None and llm.requests[-1].json_schema is not None
    assert llm.requests[1].tools == tool_specs(catalog) and llm.requests[0].tools == []


def test_confidence_and_conclusion_are_capped_by_verification(sf: sessionmaker[Session], catalog: Catalog) -> None:
    def draft(auth: dict[str, Any], _r: Any) -> dict[str, Any]:
        d = good_draft(auth)
        d["issues"][0]["reasoning"].append(
            {"text": "Unsupported legal claim without any citation.", "authority_ids": []}
        )
        return d

    script = happy_script()
    script[4] = draft_reply(draft)
    result = run_pipeline(sf, catalog, FakeLLM(script))
    assert result.removed_claims == 1
    issue = result.guidance.issues[0]
    assert issue.confidence == "medium" and len(issue.reasoning) == 1
    assert any("removed because they could not be verified" in u for u in issue.uncertainties)
    assert any(lim.code == "UNVERIFIED_CLAIMS_REMOVED" for lim in result.limitations)
    assert "removed" in result.guidance.summary


def test_pipeline_uses_notes_to_select_authorities(sf: sessionmaker[Session], catalog: Catalog) -> None:
    script = happy_script()
    script[3] = notes_reply(S12_1)
    llm = FakeLLM(script)
    run_pipeline(sf, catalog, llm)
    shown = json.loads(str(llm.requests[-1].messages[0]["content"]).split("JSON):\n", 1)[1].split("\n", 1)[0])
    assert [a["citation"] for a in shown] == [S12_1]
