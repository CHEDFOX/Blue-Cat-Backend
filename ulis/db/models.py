"""Jurisdiction-independent core schema.

Design rules (see ``docs/core-schema.md``):

* Works and versions are separate (FRBR): an ``Instrument`` is the abstract law, an
  ``InstrumentVersion`` is one language expression valid for a period.  The same applies to
  provisions and judgments.
* Original source bytes are kept immutable in object storage (``SourceDocument``); extracted
  text, machine output and translations live in separate tables and always point back to it.
* Validity uses half-open intervals ``[valid_from, valid_to)``; ``NULL`` means unknown/open.
* Jurisdiction-specific data goes into ``ext`` JSONB validated by the jurisdiction module,
  never into new core columns whose meaning would differ between legal systems.
* ``Passage`` is a derived, rebuildable retrieval index - not a source of truth.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, REGCONFIG, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ulis.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


SHA256_CHECK = "char_length({col}) = 64"

# --------------------------------------------------------------------------- jurisdictions


class Jurisdiction(Base):
    __tablename__ = "jurisdictions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("jurisdictions.id"))
    fictional: Mapped[bool] = mapped_column(Boolean)
    profile_version: Mapped[str] = mapped_column(String(20))
    profile_sha256: Mapped[str] = mapped_column(String(64))
    verification_status: Mapped[str] = mapped_column(String(32))
    corpus_status: Mapped[str] = mapped_column(String(32))
    official_languages: Mapped[list[str]] = mapped_column(ARRAY(String(16)))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JurisdictionProfileVersion(Base):
    """Every profile version ever loaded, kept for audit and reproducibility."""

    __tablename__ = "jurisdiction_profile_versions"
    __table_args__ = (UniqueConstraint("jurisdiction_id", "profile_version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    jurisdiction_id: Mapped[str] = mapped_column(ForeignKey("jurisdictions.id"))
    profile_version: Mapped[str] = mapped_column(String(20))
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    loaded_at: Mapped[datetime] = created_at()


class Court(Base):
    __tablename__ = "courts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    jurisdiction_id: Mapped[str] = mapped_column(ForeignKey("jurisdictions.id"), index=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32))
    rank: Mapped[int] = mapped_column(Integer)
    appeals_to: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    binds_self: Mapped[bool] = mapped_column(Boolean, default=False)


# --------------------------------------------------------------------------- sources


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    publisher: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40))
    authority: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    licence_id: Mapped[str] = mapped_column(String(80))
    licence_review_status: Mapped[str] = mapped_column(String(32))
    descriptor: Mapped[dict[str, Any]] = mapped_column(JSONB)
    descriptor_sha256: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SourceDocument(Base):
    """An immutable retrieval of raw bytes from a source ("source version")."""

    __tablename__ = "source_documents"
    __table_args__ = (
        UniqueConstraint("source_id", "sha256"),
        CheckConstraint(SHA256_CHECK.format(col="sha256"), name="sha256_len"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    external_id: Mapped[str] = mapped_column(Text)
    uri: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(100))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = created_at()
    licence_id: Mapped[str] = mapped_column(String(80))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    actor: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32))
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    errors: Mapped[list[Any]] = mapped_column(JSONB, default=list)


# --------------------------------------------------------------------------- legislation & treaties


class Instrument(Base):
    """A legal instrument as an abstract work (constitution, Act, code, bill, treaty, ...)."""

    __tablename__ = "instruments"

    id: Mapped[uuid.UUID] = uuid_pk()
    uri: Mapped[str] = mapped_column(Text, unique=True)
    jurisdiction_id: Mapped[str] = mapped_column(ForeignKey("jurisdictions.id"), index=True)
    instrument_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(Text)
    short_title: Mapped[str | None] = mapped_column(Text)
    number: Mapped[str | None] = mapped_column(String(40))
    work_date: Mapped[date | None] = mapped_column(Date)
    # Proposed instruments (bills, drafts) are never law, whatever their text says.
    is_proposed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Territorial extent as jurisdiction ids; empty = the whole issuing jurisdiction.
    extent: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    ext: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created_at()

    versions: Mapped[list[InstrumentVersion]] = relationship(back_populates="instrument")


class InstrumentVersion(Base):
    """One language expression of an instrument at a point in time."""

    __tablename__ = "instrument_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instruments.id"), index=True)
    uri: Mapped[str] = mapped_column(Text, unique=True)
    language: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    # Short title in this expression's language (citations of a French version use the French title).
    short_title: Mapped[str | None] = mapped_column(Text)
    # True when this language version is itself legally authentic (not a translation).
    authoritative: Mapped[bool] = mapped_column(Boolean)
    text_origin: Mapped[str] = mapped_column(String(32))
    consolidation_kind: Mapped[str] = mapped_column(String(32))
    version_date: Mapped[date] = mapped_column(Date)
    # The date up to which the publisher asserts this consolidation reflects known changes.
    currency_date: Mapped[date | None] = mapped_column(Date)
    outstanding_changes: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    # How validity dates were obtained: source_periods | enactment_date_presumed |
    # attested_at_edition | proposal_stage. Drives legal-status limitations.
    validity_basis: Mapped[str] = mapped_column(String(32), default="source_periods", server_default="source_periods")
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_documents.id"))
    created_at: Mapped[datetime] = created_at()

    instrument: Mapped[Instrument] = relationship(back_populates="versions")


class Provision(Base):
    """A citable unit of an instrument (section, article, subsection, ...) as a work."""

    __tablename__ = "provisions"
    __table_args__ = (UniqueConstraint("instrument_id", "eid"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instruments.id"), index=True)
    eid: Mapped[str] = mapped_column(String(200))
    uri: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    # Numbers from the top-level provision down, e.g. ["12", "(1)"].
    num_path: Mapped[list[str]] = mapped_column(ARRAY(Text))
    top_kind: Mapped[str] = mapped_column(String(32))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("provisions.id"))
    sort_order: Mapped[int] = mapped_column(Integer)
    extent: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    # Retrieval unit: a leaf, or a container that carries its own text (e.g. a chapeau).
    index_unit: Mapped[bool] = mapped_column(Boolean, default=True)


class ProvisionVersion(Base):
    """The exact text of a provision in one language for one validity period."""

    __tablename__ = "provision_versions"
    __table_args__ = (
        Index(
            "uq_provision_versions_period",
            "provision_id",
            "language",
            text("coalesce(valid_from, '-infinity'::date)"),
            "status",
            unique=True,
        ),
        CheckConstraint("valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from", name="interval"),
        CheckConstraint("status IN ('in_force', 'prospective', 'repealed', 'proposed')", name="status"),
        CheckConstraint("valid_to_basis IN ('explicit', 'derived', 'open')", name="valid_to_basis"),
        CheckConstraint("valid_from_basis IN ('source', 'observed')", name="valid_from_basis"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    provision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("provisions.id"), index=True)
    instrument_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instrument_versions.id"))
    language: Mapped[str] = mapped_column(String(16))
    heading: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    valid_to_basis: Mapped[str] = mapped_column(String(16), default="open")
    # "observed": the source restated this wording without moving its start date, so it is dated
    # from the first point-in-time version in which it appears (see the ingestion pipeline).
    valid_from_basis: Mapped[str] = mapped_column(String(16), default="source", server_default="source")
    note: Mapped[str | None] = mapped_column(Text)
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_documents.id"))
    source_locator: Mapped[str] = mapped_column(Text)


class ProvisionVersionSource(Base):
    """Every source document that attests a provision version (provenance, corroboration)."""

    __tablename__ = "provision_version_sources"

    provision_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("provision_versions.id"), primary_key=True)
    instrument_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instrument_versions.id"), primary_key=True)
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_documents.id"))
    source_locator: Mapped[str] = mapped_column(Text)


class LifecycleEvent(Base):
    """Enactment, commencement, amendment, repeal, supersession, entry into force, ..."""

    __tablename__ = "lifecycle_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instruments.id"), index=True)
    provision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("provisions.id"))
    event_type: Mapped[str] = mapped_column(String(32))
    event_date: Mapped[date] = mapped_column(Date)
    affecting_uri: Mapped[str | None] = mapped_column(Text)
    affecting_instrument_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("instruments.id"))
    description: Mapped[str | None] = mapped_column(Text)
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_documents.id"))
    source_locator: Mapped[str | None] = mapped_column(Text)


class TreatyParty(Base):
    """Treaty-specific extension: the status of a treaty for one party."""

    __tablename__ = "treaty_parties"
    __table_args__ = (UniqueConstraint("treaty_id", "party_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    treaty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instruments.id"), index=True)
    party_id: Mapped[str] = mapped_column(ForeignKey("jurisdictions.id"))
    signed_on: Mapped[date | None] = mapped_column(Date)
    consent_type: Mapped[str | None] = mapped_column(String(32))
    consented_on: Mapped[date | None] = mapped_column(Date)
    entry_into_force_on: Mapped[date | None] = mapped_column(Date)
    withdrawn_on: Mapped[date | None] = mapped_column(Date)
    reservations: Mapped[str | None] = mapped_column(Text)
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_documents.id"))


# --------------------------------------------------------------------------- case law


class Judgment(Base):
    __tablename__ = "judgments"

    id: Mapped[uuid.UUID] = uuid_pk()
    uri: Mapped[str] = mapped_column(Text, unique=True)
    jurisdiction_id: Mapped[str] = mapped_column(ForeignKey("jurisdictions.id"), index=True)
    court_id: Mapped[str] = mapped_column(ForeignKey("courts.id"))
    case_name: Mapped[str] = mapped_column(Text)
    neutral_citation: Mapped[str | None] = mapped_column(Text, index=True)
    decision_date: Mapped[date] = mapped_column(Date)
    docket: Mapped[str | None] = mapped_column(Text)
    ext: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = created_at()


class JudgmentVersion(Base):
    __tablename__ = "judgment_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    judgment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("judgments.id"), index=True)
    uri: Mapped[str] = mapped_column(Text, unique=True)
    language: Mapped[str] = mapped_column(String(16))
    authoritative: Mapped[bool] = mapped_column(Boolean)
    text_origin: Mapped[str] = mapped_column(String(32))
    version_date: Mapped[date] = mapped_column(Date)
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_documents.id"))


class JudgmentParagraph(Base):
    __tablename__ = "judgment_paragraphs"
    __table_args__ = (UniqueConstraint("judgment_version_id", "eid"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    judgment_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("judgment_versions.id"), index=True)
    eid: Mapped[str] = mapped_column(String(100))
    num: Mapped[str | None] = mapped_column(String(20))
    part: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(String(64))
    sort_order: Mapped[int] = mapped_column(Integer)


# --------------------------------------------------------------------------- citations & concepts


class CitationLink(Base):
    """A citation relationship as it appears in a source (who cites what, and how)."""

    __tablename__ = "citation_links"

    id: Mapped[uuid.UUID] = uuid_pk()
    from_kind: Mapped[str] = mapped_column(String(32))
    from_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    from_work_uri: Mapped[str] = mapped_column(Text)
    to_uri: Mapped[str] = mapped_column(Text)
    to_instrument_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("instruments.id"), index=True)
    to_provision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("provisions.id"), index=True)
    to_judgment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("judgments.id"), index=True)
    relation: Mapped[str] = mapped_column(String(32))
    citation_text: Mapped[str] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(32))
    verification_status: Mapped[str] = mapped_column(String(32), default="unverified")
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_documents.id"))


class LegalConcept(Base):
    __tablename__ = "legal_concepts"

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(120), unique=True)
    scope_jurisdiction_id: Mapped[str | None] = mapped_column(ForeignKey("jurisdictions.id"))
    description: Mapped[str] = mapped_column(Text)


class ConceptLabel(Base):
    __tablename__ = "concept_labels"
    __table_args__ = (UniqueConstraint("concept_id", "language", "label"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    concept_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("legal_concepts.id"), index=True)
    language: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(Text)
    search_config: Mapped[str] = mapped_column(REGCONFIG)


class LegalIssue(Base):
    """A legal question in a jurisdiction and procedural stage (Phase 4 research workflow)."""

    __tablename__ = "legal_issues"

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(160), unique=True)
    jurisdiction_id: Mapped[str | None] = mapped_column(ForeignKey("jurisdictions.id"))
    procedural_stage: Mapped[str | None] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(Text)


class Proposition(Base):
    """An extracted fact, holding, inference or argument - never stored without its kind."""

    __tablename__ = "propositions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('established_fact', 'allegation', 'disputed_fact', 'legal_provision', 'holding', "
            "'inference', 'argument', 'contrary_interpretation', 'open_question')",
            name="kind",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    jurisdiction_id: Mapped[str | None] = mapped_column(ForeignKey("jurisdictions.id"))
    issue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("legal_issues.id"))
    produced_by: Mapped[str] = mapped_column(String(80))  # e.g. "human:<id>", "model:<provider>/<model>"
    verification_status: Mapped[str] = mapped_column(String(32), default="unverified")
    created_at: Mapped[datetime] = created_at()


class PropositionSupport(Base):
    """Links a proposition to the exact passage that supports (or contradicts) it."""

    __tablename__ = "proposition_support"
    __table_args__ = (
        CheckConstraint("(provision_version_id IS NOT NULL) <> (judgment_paragraph_id IS NOT NULL)", name="one_target"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    proposition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("propositions.id"), index=True)
    provision_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("provision_versions.id"))
    judgment_paragraph_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("judgment_paragraphs.id"))
    stance: Mapped[str] = mapped_column(String(16), default="supports")
    quote: Mapped[str | None] = mapped_column(Text)
    quote_verified: Mapped[bool] = mapped_column(Boolean, default=False)


class Translation(Base):
    """A translation, always kept apart from the original-language text."""

    __tablename__ = "translations"
    __table_args__ = (CheckConstraint("kind IN ('official_translation', 'unofficial_human', 'machine')", name="kind"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    subject_kind: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    source_language: Mapped[str] = mapped_column(String(16))
    target_language: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(String(32), default="unverified")
    created_at: Mapped[datetime] = created_at()


class VerificationRecord(Base):
    __tablename__ = "verification_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    subject_kind: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(Text, index=True)
    check: Mapped[str] = mapped_column(String(48))
    result: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    checker: Mapped[str] = mapped_column(String(80))
    checked_at: Mapped[datetime] = created_at()


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    at: Mapped[datetime] = created_at()
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


# --------------------------------------------------------------------------- retrieval index


class Passage(Base):
    """Derived retrieval unit. Can be dropped and rebuilt from canonical tables at any time."""

    __tablename__ = "passages"
    __table_args__ = (
        Index("ix_passages_tsv", "tsv", postgresql_using="gin"),
        Index("ix_passages_validity", "valid_from", "valid_to"),
        CheckConstraint("(provision_version_id IS NOT NULL) <> (judgment_paragraph_id IS NOT NULL)", name="one_target"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(32))
    provision_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("provision_versions.id", ondelete="CASCADE"), unique=True
    )
    judgment_paragraph_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("judgment_paragraphs.id", ondelete="CASCADE"), unique=True
    )
    instrument_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("instruments.id"))
    judgment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("judgments.id"))
    jurisdiction_id: Mapped[str] = mapped_column(ForeignKey("jurisdictions.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(40))
    language: Mapped[str] = mapped_column(String(16))
    search_config: Mapped[str] = mapped_column(REGCONFIG)
    context: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text)
    # Search forms of context/text when they differ (CJK bigrams, see ulis.languages); the
    # exact text above is what results show and cite.
    search_context: Mapped[str | None] = mapped_column(Text)
    search_text: Mapped[str | None] = mapped_column(Text)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector(search_config, coalesce(search_context, context, '')), 'B') || "
            "setweight(to_tsvector(search_config, coalesce(search_text, text)), 'A')",
            persisted=True,
        ),
    )
    status: Mapped[str] = mapped_column(String(16))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    extent: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    authoritative: Mapped[bool] = mapped_column(Boolean)
    is_proposed: Mapped[bool] = mapped_column(Boolean, default=False)
    index_version: Mapped[int] = mapped_column(Integer, default=1)


# --------------------------------------------------------------------------- research assistant


class ResearchJob(Base):
    """One run of the research assistant (``POST /v1/research``).

    Never holds the question or the case text: only a digest of the request, the model's own
    structured result and progress messages. Rows are deleted at ``expires_at``.
    """

    __tablename__ = "research_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'refused')", name="research_job_status"
        ),
        Index("ix_research_jobs_expires_at", "expires_at"),
        Index("ix_research_jobs_principal_created", "principal", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    status: Mapped[str] = mapped_column(String(16))
    principal: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    request_sha256: Mapped[str] = mapped_column(String(64))
    progress: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
