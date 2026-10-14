"""Evaluation of the research assistant against a question set (``eval/questions.yaml``).

Pure functions only: loading and validating the question file, matching expected citations against
the authorities an answer cites, and summarising. ``scripts/eval_research.py`` runs the pipeline with
a real key and feeds the results here.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ulis.assistant.schemas import INSUFFICIENT, ResearchResult
from ulis.catalog import Catalog


class EvalQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str = Field(min_length=10, max_length=4000)
    jurisdiction: str
    as_of: date | None = None
    depth: Literal["standard", "deep"] = "standard"
    # Citation fragments (case, spacing and dash-insensitive) that an authority cited by the answer should contain.
    expected_citations: list[str] = Field(default_factory=list)
    # A control: the corpus is known not to cover this, so the answer must say "insufficient authority indexed".
    expect_insufficient: bool = False
    notes: str = ""

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", value):
            raise ValueError("id must be a lower-case slug")
        return value


class EvalFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    questions: list[EvalQuestion]


def load_questions(path: Path, catalog: Catalog | None = None) -> list[EvalQuestion]:
    data = EvalFile.model_validate(yaml.safe_load(path.read_text("utf-8")))
    ids = [q.id for q in data.questions]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate question ids")
    for q in data.questions:
        if catalog is not None and q.jurisdiction not in catalog.jurisdictions:
            raise ValueError(f"{q.id}: unknown jurisdiction {q.jurisdiction}")
        if q.expect_insufficient == bool(q.expected_citations):
            raise ValueError(f"{q.id}: give expected_citations, or expect_insufficient for a control (not both)")
    return data.questions


_DASHES = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"})


def normalise_citation(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).translate(_DASHES).casefold()
    return re.sub(r"\s+", " ", value).strip()


def score(question: EvalQuestion, result: ResearchResult) -> dict[str, Any]:
    cited = [normalise_citation(a.citation) for a in result.authorities]
    found = [e for e in question.expected_citations if any(normalise_citation(e) in c for c in cited)]
    insufficient = [i.issue_id for i in result.guidance.issues if i.conclusion == INSUFFICIENT]
    recall = len(found) / len(question.expected_citations) if question.expected_citations else None
    row: dict[str, Any] = {
        "id": question.id,
        "jurisdiction": question.jurisdiction,
        "expected": question.expected_citations,
        "found": found,
        "missing": [e for e in question.expected_citations if e not in found],
        "recall": recall,
        "cited_authorities": [a.citation for a in result.authorities],
        "issues": len(result.guidance.issues),
        "insufficient_issues": insufficient,
        "all_issues_insufficient": bool(result.guidance.issues) and len(insufficient) == len(result.guidance.issues),
        "removed_claims": result.removed_claims,
        "all_verified": all(a.verified for a in result.authorities),
        "searches": result.usage.searches,
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "coverage_notes": len(result.coverage_notes),
    }
    row["passed"] = row["all_issues_insufficient"] if question.expect_insufficient else recall == 1.0
    return row


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if r.get("recall") is not None]
    controls = [r for r in rows if r.get("recall") is None and "passed" in r]
    return {
        "questions": len(rows),
        "completed": sum(1 for r in rows if "error" not in r),
        "failed": [r["id"] for r in rows if "error" in r],
        "mean_recall": round(mean(r["recall"] for r in scored), 3) if scored else None,
        "fully_answered": sum(1 for r in scored if r["recall"] == 1.0),
        "scored": len(scored),
        "controls_passed": sum(1 for r in controls if r["passed"]),
        "controls": len(controls),
        "removed_claims": sum(r.get("removed_claims", 0) for r in rows),
        "unverified_authorities": sum(1 for r in rows if r.get("all_verified") is False),
        "searches": sum(r.get("searches", 0) for r in rows),
        "input_tokens": sum(r.get("input_tokens", 0) for r in rows),
        "output_tokens": sum(r.get("output_tokens", 0) for r in rows),
    }
