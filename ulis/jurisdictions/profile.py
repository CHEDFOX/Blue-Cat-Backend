"""Schema for versioned jurisdiction profiles.

A profile is the explicit, reviewable description of how one jurisdiction works:
its place in the jurisdiction tree, languages, institutions, courts, precedent
rules, publication practice and known coverage gaps.  Profiles live as YAML files
in ``config/jurisdictions`` and are versioned independently of the code.

The schema intentionally captures only what the core platform needs to reason
generically.  Anything whose legal meaning is jurisdiction-specific belongs in the
profile's ``extensions`` block, validated by the jurisdiction module named in
``citation.module`` (see ``ulis.jurisdictions.modules``).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JURISDICTION_ID_RE = re.compile(r"^[A-Z]{2,3}(-[A-Z0-9]{1,8})*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

JurisdictionKind = Literal[
    "international_legal_order",
    "regional_organisation",
    "sovereign_state",
    "constituent_unit",
    "local",
    "other",
]

LegalFamily = Literal["common_law", "civil_law", "customary", "religious", "mixed", "international", "sui_generis"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VerificationInfo(_Strict):
    # fictional_test_data: invented for testing, never real law.
    # draft_unverified:    written from research notes; must be reviewed by a qualified lawyer.
    # reviewed:            reviewed and signed off by a named reviewer.
    status: Literal["fictional_test_data", "draft_unverified", "reviewed"]
    reviewed_by: str | None = None
    reviewed_on: date | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _reviewed_needs_reviewer(self) -> VerificationInfo:
        if self.status == "reviewed" and not (self.reviewed_by and self.reviewed_on):
            raise ValueError("verification.status 'reviewed' requires reviewed_by and reviewed_on")
        return self


class LegalSystem(_Strict):
    families: list[LegalFamily]
    structure: Literal[
        "unitary",
        "federal",
        "quasi_federal",
        "confederal",
        "constituent",
        "international_organisation",
        "international_legal_order",
    ]
    # How treaties obtain domestic legal effect.
    treaty_reception: Literal["monist", "dualist", "mixed", "not_applicable", "unknown"]
    notes: str | None = None


class LegislativeInstitution(_Strict):
    id: str
    name: str
    instrument_types: list[str]


class Court(_Strict):
    id: str
    name: str
    kind: Literal["court", "tribunal", "administrative_body"] = "court"
    # 1 = apex of its own hierarchy. Ranks are only comparable within one profile.
    rank: int = Field(ge=1)
    # Courts to which appeals lie. May reference courts defined in an ancestor profile.
    appeals_to: list[str] = Field(default_factory=list)
    # Whether the court is bound by its own previous decisions.
    binds_self: bool = False


class PrecedentRules(_Strict):
    doctrine: Literal["stare_decisis", "jurisprudence_constante", "no_formal_precedent", "not_applicable"]
    # Decisions bind every court whose appeals lie (directly or indirectly) to the deciding court.
    vertical_binding: bool
    notes: str | None = None


class CitationConventions(_Strict):
    module: str = "default"
    notes: str | None = None
    examples: list[str] = Field(default_factory=list)


class PublicationPractice(_Strict):
    official_sources: list[str] = Field(default_factory=list)
    commencement: str | None = None
    amendment: str | None = None
    repeal: str | None = None


class Membership(_Strict):
    organisation: str
    from_date: date | None = None
    to_date: date | None = None


class Coverage(_Strict):
    corpus_status: Literal["none", "fictional_sample", "pilot", "partial", "broad"]
    known_gaps: list[str] = Field(default_factory=list)


class JurisdictionProfile(_Strict):
    schema_version: Literal[1]
    id: str
    profile_version: str
    name: str
    names: dict[str, str] = Field(default_factory=dict)
    kind: JurisdictionKind
    parent: str | None
    fictional: bool
    verification: VerificationInfo
    official_languages: list[str]
    legal_system: LegalSystem
    legislative_institutions: list[LegislativeInstitution] = Field(default_factory=list)
    instrument_types: list[str]
    # Instrument types from highest to lowest normative rank.
    norm_hierarchy: list[str] = Field(default_factory=list)
    courts: list[Court] = Field(default_factory=list)
    precedent: PrecedentRules
    citation: CitationConventions = Field(default_factory=CitationConventions)
    publication: PublicationPractice = Field(default_factory=PublicationPractice)
    memberships: list[Membership] = Field(default_factory=list)
    coverage: Coverage
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not JURISDICTION_ID_RE.match(value):
            raise ValueError(f"invalid jurisdiction id {value!r}")
        return value

    @field_validator("profile_version")
    @classmethod
    def _semver(cls, value: str) -> str:
        if not SEMVER_RE.match(value):
            raise ValueError("profile_version must be MAJOR.MINOR.PATCH")
        return value

    @model_validator(mode="after")
    def _consistency(self) -> JurisdictionProfile:
        if self.fictional != (self.verification.status == "fictional_test_data"):
            raise ValueError("fictional profiles (and only those) must use verification.status fictional_test_data")
        if self.fictional and self.coverage.corpus_status not in ("none", "fictional_sample"):
            raise ValueError("a fictional jurisdiction can only have a fictional_sample corpus")
        unknown = [t for t in self.norm_hierarchy if t not in self.instrument_types]
        if unknown:
            raise ValueError(f"norm_hierarchy references undeclared instrument types: {unknown}")
        court_ids = [c.id for c in self.courts]
        if len(court_ids) != len(set(court_ids)):
            raise ValueError("duplicate court ids")
        return self

    def sha256(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def court(self, court_id: str) -> Court | None:
        return next((c for c in self.courts if c.id == court_id), None)
