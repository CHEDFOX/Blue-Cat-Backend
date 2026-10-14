"""Shapes exchanged with the model (and their JSON schemas) and the public result contract."""

from __future__ import annotations

import json
import re
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

Confidence = Literal["high", "medium", "low"]
Stage = Literal["understanding", "searching", "drafting", "verifying"]

INSUFFICIENT = "insufficient authority indexed"

# --------------------------------------------------------------------------- model outputs


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class UJurisdiction(_Lenient):
    id: str
    reason: str = ""


class UIssue(_Lenient):
    title: str
    area_of_law: str = ""
    question: str = ""


class UnderstandingOut(_Lenient):
    summary: str
    facts: list[str] = Field(default_factory=list)
    as_of: str | None = None
    jurisdictions: list[UJurisdiction] = Field(default_factory=list)
    issues: list[UIssue] = Field(default_factory=list)


class SearchNotes(_Lenient):
    notes: str = ""
    relevant_authority_ids: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class DReasoning(_Lenient):
    text: str
    authority_ids: list[str] = Field(default_factory=list)


class DIssue(_Lenient):
    issue_id: str
    conclusion: str
    confidence: Confidence = "low"
    reasoning: list[DReasoning] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def _tolerant_confidence(cls, value: Any) -> Any:
        """An unrecognised level is read as the most cautious one rather than failing the whole draft."""
        text = value.strip().lower() if isinstance(value, str) else ""
        return text if text in ("high", "medium", "low") else "low"


class DraftOut(_Lenient):
    summary: str = ""
    issues: list[DIssue] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


M = TypeVar("M", bound=BaseModel)


class ModelOutputError(ValueError):
    """The model's reply was not the JSON we asked for."""


_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def parse_model_json(text: str, model: type[M]) -> M:
    """Lenient JSON extraction (code fences, stray prose) followed by schema validation."""
    candidate = _FENCE.sub("", text.strip())
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise ModelOutputError("the reply contained no JSON object") from None
        try:
            data = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ModelOutputError(f"the reply was not valid JSON ({exc.msg})") from None
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise ModelOutputError(f"the JSON did not match the schema at {loc or 'root'}: {first['msg']}") from None


# --------------------------------------------------------------------------- JSON schemas (structured output)


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required if required is not None else list(properties),
        "additionalProperties": False,
    }


def _arr(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


_STR: dict[str, Any] = {"type": "string"}


def understanding_schema(jurisdiction_ids: list[str]) -> dict[str, Any]:
    return _obj(
        {
            "summary": _STR,
            "facts": _arr(_STR),
            "as_of": {"anyOf": [_STR, {"type": "null"}]},
            "jurisdictions": _arr(_obj({"id": {"type": "string", "enum": jurisdiction_ids}, "reason": _STR})),
            "issues": _arr(_obj({"title": _STR, "area_of_law": _STR, "question": _STR})),
        }
    )


def draft_schema() -> dict[str, Any]:
    return _obj(
        {
            "summary": _STR,
            "issues": _arr(
                _obj(
                    {
                        "issue_id": _STR,
                        "conclusion": _STR,
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "reasoning": _arr(_obj({"text": _STR, "authority_ids": _arr(_STR)})),
                        "uncertainties": _arr(_STR),
                    }
                )
            ),
            "next_steps": _arr(_STR),
        }
    )


# --------------------------------------------------------------------------- public result contract


class LimitationOut(BaseModel):
    code: str
    severity: str
    message: str


class JurisdictionOut(BaseModel):
    id: str
    reason: str
    covered: bool


class IssueOut(BaseModel):
    id: str
    title: str
    area_of_law: str
    question: str


class UnderstandingResult(BaseModel):
    summary: str
    facts: list[str]
    as_of: str | None
    jurisdictions: list[JurisdictionOut]
    issues: list[IssueOut]


class ReasoningOut(BaseModel):
    text: str
    authority_ids: list[str]


class GuidanceIssueOut(BaseModel):
    issue_id: str
    conclusion: str
    confidence: Confidence
    reasoning: list[ReasoningOut]
    uncertainties: list[str]


class GuidanceResult(BaseModel):
    summary: str
    issues: list[GuidanceIssueOut]
    next_steps: list[str]


class SourceOut(BaseModel):
    name: str
    url: str | None
    attribution: str | None


class AuthorityOut(BaseModel):
    id: str
    citation: str
    uri: str
    jurisdiction: str
    kind: Literal["provision", "judgment_paragraph"]
    text: str
    text_sha256: str
    valid_from: str | None
    valid_to: str | None
    authority_label: str
    limitations: list[LimitationOut]
    source: SourceOut
    verified: bool


class UsageOut(BaseModel):
    searches: int
    model: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0


class RemovedItem(BaseModel):
    issue_id: str | None
    where: str
    reason: str


class VerificationOut(BaseModel):
    """Additive to the contract: what the verifier did (never contains the removed text itself)."""

    authorities_checked: int
    authorities_verified: int
    quotes_checked: int
    quotes_failed: int
    removed: list[RemovedItem]


class ResearchResult(BaseModel):
    understanding: UnderstandingResult
    guidance: GuidanceResult
    authorities: list[AuthorityOut]
    coverage_notes: list[str]
    removed_claims: int
    limitations: list[LimitationOut]
    disclaimer: str
    usage: UsageOut
    verification: VerificationOut
