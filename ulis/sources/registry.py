"""Source descriptors: what a source covers, how it is accessed and what its licence permits.

Every source the platform knows about - including sources it is *not yet allowed to
ingest* - is described by a YAML descriptor in ``config/sources``.  The ingestion
pipeline consults :func:`licence_gate` before touching a source, so public
accessibility never substitutes for an approved licence review.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Permission = Literal["allowed", "allowed_with_conditions", "not_allowed", "unknown"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LicenceTerms(_Strict):
    id: str
    name: str
    url: str | None = None
    # Only "approved" / "not_required" allow ingestion.
    review_status: Literal["approved", "not_required", "pending_review", "rejected"]
    reviewed_by: str | None = None
    reviewed_on: date | None = None
    redistribution: Permission
    bulk_download: Permission
    # Automated processing, including indexing, embedding and model-assisted analysis.
    automated_processing: Permission
    commercial_use: Permission
    attribution_required: bool | None = None
    attribution_text: str | None = None
    conditions: list[str] = Field(default_factory=list)
    notes: str | None = None


class AccessMethod(_Strict):
    method: Literal["api", "bulk_download", "local_manifest", "permitted_web_retrieval", "licensed_feed"]
    endpoint: str | None = None
    authentication: str | None = None


class SourceDescriptor(_Strict):
    schema_version: Literal[1]
    id: str
    name: str
    publisher: str
    kind: Literal[
        "official_legislation_portal",
        "official_court_database",
        "official_treaty_repository",
        "official_gazette",
        "international_organisation",
        "open_data_repository",
        "licensed_commercial",
        "project_authored",
    ]
    # How much legal weight the *published text* carries.
    authority: Literal[
        "official_authentic", "official_non_authentic", "authoritative_secondary", "unofficial", "fictional"
    ]
    jurisdictions: list[str]
    document_kinds: list[str]
    languages: list[str]
    coverage: str
    access: AccessMethod
    update_frequency: str
    rate_limits: str | None = None
    licence: LicenceTerms
    provenance: str
    known_limitations: list[str] = Field(default_factory=list)
    status: Literal["active", "planned", "suspended", "rejected"]
    connector: str | None = None
    parser: str | None = None
    # False: superseded editions, historical enactments or other material that is not the law actively serving today.
    # `load-all` leaves such a source out unless asked (`--include-history`, or the source named with `--only`).
    load_by_default: bool = True


class LicenceNotApproved(PermissionError):
    pass


def commercial_deployment() -> bool:
    """``ULIS_COMMERCIAL_USE=1`` declares that this deployment is, or will be, offered commercially."""
    return os.environ.get("ULIS_COMMERCIAL_USE", "").strip().lower() in ("1", "true", "yes")


def licence_gate(source: SourceDescriptor, *, commercial: bool | None = None) -> None:
    """Raise unless the source may be ingested and processed automatically.

    In a commercial deployment (``commercial=True``, or ``ULIS_COMMERCIAL_USE=1`` when not given) the licence must also
    permit commercial use; sources whose commercial use is unknown or not permitted are refused until the publisher
    has answered. The closed early-access test is not commercial.
    """
    problems: list[str] = []
    if commercial is None:
        commercial = commercial_deployment()
    if commercial and source.licence.commercial_use not in ("allowed", "allowed_with_conditions"):
        problems.append(
            f"commercial deployment: licence does not permit commercial use ({source.licence.commercial_use!r})"
        )
    if source.status != "active":
        problems.append(f"source status is {source.status!r}")
    if source.licence.review_status not in ("approved", "not_required"):
        problems.append(f"licence review status is {source.licence.review_status!r}")
    for attr in ("automated_processing", "bulk_download"):
        if getattr(source.licence, attr) not in ("allowed", "allowed_with_conditions"):
            problems.append(f"licence does not permit {attr} ({getattr(source.licence, attr)!r})")
    if not source.connector or not source.parser:
        problems.append("no connector/parser configured")
    if problems:
        raise LicenceNotApproved(f"source {source.id!r} may not be ingested: " + "; ".join(problems))


class SourceRegistry:
    def __init__(self, sources: dict[str, SourceDescriptor]):
        self.sources = sources

    @classmethod
    def from_directory(cls, directory: Path) -> SourceRegistry:
        sources: dict[str, SourceDescriptor] = {}
        for path in sorted(directory.glob("*.yaml")):
            with path.open("r", encoding="utf-8") as fh:
                descriptor = SourceDescriptor.model_validate(yaml.safe_load(fh))
            if descriptor.id in sources:
                raise ValueError(f"{path.name}: duplicate source id {descriptor.id}")
            sources[descriptor.id] = descriptor
        return cls(sources)

    def get(self, source_id: str) -> SourceDescriptor:
        try:
            return self.sources[source_id]
        except KeyError as exc:
            raise KeyError(f"unknown source {source_id!r}") from exc

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.sources.values())
