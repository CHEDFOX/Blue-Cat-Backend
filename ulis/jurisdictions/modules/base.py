"""Jurisdiction modules: code-level extension points for jurisdiction-specific behaviour.

The core platform never hard-codes a citation style, extension field or precedent nuance.
Each jurisdiction profile names a module (``citation.module``); the module:

* validates the jurisdiction-specific ``ext`` data attached to instruments and judgments,
* validates the profile's own ``extensions`` block,
* formats citations and pinpoints in the jurisdiction's convention (and language),
* may refine authority classification (e.g. civil-law treatment of case law).

Modules are versioned so that formatted output can be traced to the code that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict


class NoExtension(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class ProvisionCitationInput:
    instrument_title: str
    instrument_type: str
    jurisdiction_id: str
    top_kind: str
    num_path: list[str]
    language: str
    ext: dict[str, Any] = field(default_factory=dict)
    profile_extensions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JudgmentCitationInput:
    case_name: str
    neutral_citation: str | None
    decision_date: date
    docket: str | None
    court_id: str
    court_name: str
    paragraph: str | None
    language: str
    jurisdiction_id: str


PIN_PREFIX = {
    "section": "s",
    "article": "art",
    "rule": "r",
    "clause": "cl",
    "paragraph": "para",
    "regulation": "reg",
}


def subdivision(num: str) -> str:
    n = num.strip().rstrip(".")
    return n if n.startswith("(") else f"({n})"


class JurisdictionModule:
    id: ClassVar[str] = "default"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = NoExtension
    profile_extension_model: ClassVar[type[BaseModel]] = NoExtension

    def validate_ext(self, ext: dict[str, Any]) -> dict[str, Any]:
        return self.ext_model.model_validate(ext).model_dump(exclude_none=True)

    def validate_profile_extensions(self, extensions: dict[str, Any]) -> None:
        self.profile_extension_model.model_validate(extensions.get(self.id, {}))

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        if not c.num_path:
            return ""
        prefix = PIN_PREFIX.get(c.top_kind, c.top_kind)
        return f"{prefix} {c.num_path[0]}" + "".join(subdivision(n) for n in c.num_path[1:] if n)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        return f"{c.instrument_title}, {self.pinpoint(c)}"

    def judgment_citation(self, c: JudgmentCitationInput) -> str:
        reference = c.neutral_citation or f"({c.court_name}, {c.decision_date.isoformat()})"
        pin = f", para {c.paragraph}" if c.paragraph else ""
        return f"{c.case_name} {reference}{pin}"
