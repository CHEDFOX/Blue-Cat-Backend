"""Shared helpers for the research-assistant tests: scripted FakeLLM conversations over the fictional corpus."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ulis.assistant.config import AssistantConfig
from ulis.assistant.llm import FakeLLM, LLMRequest, LLMResponse, Usage, text_response, tool_use_response
from ulis.assistant.pipeline import ResearchInput, ResearchPipeline
from ulis.assistant.schemas import ResearchResult
from ulis.catalog import Catalog

TODAY = date(2026, 10, 14)
S12_1 = "Personal Data Act 2015 (XA) s 12(1)"
S12_1_URI = "/akn/xa/act/2015/12/~sec_12__subsec_1"
S12_1_TEXT_NOW = (
    "A controller shall not retain personal data for longer than five years after the purpose for which it was "
    "collected has been fulfilled."
)
CONFIG = AssistantConfig(api_key="test-key", max_searches_standard=6, max_searches_deep=10)


def understanding_reply(
    *,
    jurisdictions: tuple[str, ...] = ("XA",),
    issues: tuple[str, ...] = ("Retention period for personal data",),
    as_of: str | None = None,
) -> dict[str, Any]:
    return {
        "summary": "A controller asks how long it may keep personal data.",
        "facts": ["The controller keeps backups of personal data.", "The purpose of collection has been fulfilled."],
        "as_of": as_of,
        "jurisdictions": [{"id": j, "reason": f"The matter is governed by the law of {j}."} for j in jurisdictions],
        "issues": [
            {"title": title, "area_of_law": "Data protection", "question": f"What does the law provide on: {title}?"}
            for title in issues
        ],
    }


def citations_seen(request: LLMRequest) -> dict[str, str]:
    """citation -> authority id for everything the tools returned so far in this conversation."""
    found: dict[str, str] = {}
    for message in request.messages:
        content = message["content"]
        if message["role"] != "user" or not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result" and not block.get("is_error"):
                data = json.loads(block["content"])
                for hit in data.get("results", []):
                    found[hit["citation"]] = hit["authority_id"]
                if "authority_id" in data:
                    found[data["citation"]] = data["authority_id"]
    return found


def prompt_authorities(request: LLMRequest) -> dict[str, dict[str, Any]]:
    """citation -> authority (as shown to the drafting call)."""
    text = request.messages[0]["content"]
    assert isinstance(text, str)
    marker = "Authorities (the only citable sources; JSON):\n"
    payload = text.split(marker, 1)[1].split("\n", 1)[0]
    return {a["citation"]: a for a in json.loads(payload)}


def notes_reply(*citations: str) -> Callable[[LLMRequest], LLMResponse]:
    def make(request: LLMRequest) -> LLMResponse:
        seen = citations_seen(request)
        ids = [seen[c] for c in citations]
        return text_response({"notes": "Found the governing provision.", "relevant_authority_ids": ids, "gaps": []})

    return make


def draft_reply(
    build: Callable[[dict[str, dict[str, Any]], LLMRequest], dict[str, Any]],
) -> Callable[[LLMRequest], LLMResponse]:
    def make(request: LLMRequest) -> LLMResponse:
        return text_response(build(prompt_authorities(request), request))

    return make


def good_draft(authorities: dict[str, dict[str, Any]], issue_id: str = "I1") -> dict[str, Any]:
    a = authorities[S12_1]["id"]
    return {
        "summary": "Section 12(1) limits how long personal data may be kept.",
        "issues": [
            {
                "issue_id": issue_id,
                "conclusion": "A controller may keep personal data for five years after the purpose is fulfilled.",
                "confidence": "high",
                "reasoning": [
                    {
                        "text": f'Section 12(1) provides that a controller "shall not retain personal data for longer '
                        f'than five years" ({a}).',
                        "authority_ids": [a],
                    }
                ],
                "uncertainties": ["Backups may fall outside the word 'retain'."],
            }
        ],
        "next_steps": ["Check whether the data is also required to be kept under another Act."],
    }


def happy_script(*, depth_issues: tuple[str, ...] = ("Retention period for personal data",)) -> list[Any]:
    """understanding -> one search + provision read -> notes -> good draft, for a single issue."""
    return [
        understanding_reply(issues=depth_issues),
        tool_use_response(
            ("search_law", {"query": "retain personal data", "jurisdiction": "XA", "as_of": "2024-01-01"}),
            ("coverage", {"jurisdiction": "XA"}),
        ),
        tool_use_response(("get_provision", {"uri": S12_1_URI, "as_of": "2024-01-01"}), start=10),
        notes_reply(S12_1),
        draft_reply(lambda auth, _req: good_draft(auth)),
    ]


def run_pipeline(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    llm: FakeLLM,
    *,
    question: str = "How long may a controller keep personal data?",
    case_text: str | None = None,
    jurisdiction: str | None = "XA",
    as_of: date | None = date(2024, 1, 1),
    depth: str = "standard",
    config: AssistantConfig = CONFIG,
    progress: list[tuple[str, str]] | None = None,
) -> ResearchResult:
    pipeline = ResearchPipeline(
        llm=llm,
        session_factory=session_factory,
        catalog=catalog,
        config=config,
        report=(lambda stage, message: progress.append((stage, message))) if progress is not None else None,
        today=TODAY,
    )
    return pipeline.run(
        ResearchInput(question=question, case_text=case_text, jurisdiction=jurisdiction, as_of=as_of, depth=depth)  # type: ignore[arg-type]
    )


def with_output_tokens(response: LLMResponse, tokens: int) -> LLMResponse:
    return replace(response, usage=Usage(100, tokens))
