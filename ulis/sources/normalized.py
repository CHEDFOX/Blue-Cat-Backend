"""Normalised document model produced by every parser.

Parsers for different source formats (Akoma Ntoso, CLML, USLM, court HTML, ...) all
emit these structures, so the ingestion pipeline never depends on a source format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal


@dataclass(frozen=True)
class ParsedRef:
    href: str
    text: str
    relation: str
    # "source_markup": the source itself marks the reference (link, identifier); "pattern": found by a
    # strict text pattern that maps to exactly one target (see the parser that sets it).
    extraction_method: str = "source_markup"


@dataclass
class ParsedProvision:
    eid: str
    kind: str
    num_path: list[str]
    top_kind: str
    heading: str | None
    text: str
    parent_eid: str | None
    sort_order: int
    status: Literal["in_force", "prospective", "repealed", "proposed"]
    valid_from: date | None
    valid_to: date | None
    extent: list[str]
    note: str | None
    refs: list[ParsedRef]
    is_leaf: bool
    has_own_content: bool
    # Heading of the enclosing top-level provision (useful context for leaf passages).
    context_heading: str | None = None
    # False when the source's composite text for this container never applied as a whole (its
    # children's periods do not overlap): the provision is recorded, but not this version.
    record_version: bool = True

    @property
    def indexable(self) -> bool:
        """Leaves are indexed; containers only when they carry their own text (e.g. a chapeau)."""
        return (
            self.record_version
            and self.status != "repealed"
            and bool(self.text)
            and (self.is_leaf or self.has_own_content)
        )


@dataclass(frozen=True)
class ParsedEvent:
    event_type: str
    date: date
    affecting_uri: str | None
    description: str | None
    locator: str


@dataclass(frozen=True)
class ParsedTreatyParty:
    party: str
    signed_on: date | None
    consent_type: str | None
    consented_on: date | None
    entry_into_force_on: date | None
    withdrawn_on: date | None = None
    reservations: str | None = None


@dataclass
class ParsedParagraph:
    eid: str
    num: str | None
    part: str
    text: str
    sort_order: int
    refs: list[ParsedRef]


@dataclass(frozen=True)
class ParsedJudgmentMeta:
    court_id: str
    case_name: str
    neutral_citation: str | None
    decision_date: date
    docket: str | None


@dataclass
class ParsedDocument:
    doc_kind: Literal["legislation", "judgment"]
    instrument_type: str
    jurisdiction_id: str
    work_uri: str
    expression_uri: str
    work_title: str
    title: str
    short_title: str | None
    number: str | None
    work_date: date | None
    language: str
    authoritative: bool
    text_origin: str
    consolidation_kind: str
    version_date: date
    currency_date: date | None
    extent: list[str] = field(default_factory=list)
    ext_module: str | None = None
    ext: dict[str, Any] = field(default_factory=dict)
    outstanding_changes: list[dict[str, Any]] = field(default_factory=list)
    events: list[ParsedEvent] = field(default_factory=list)
    provisions: list[ParsedProvision] = field(default_factory=list)
    treaty_parties: list[ParsedTreatyParty] = field(default_factory=list)
    judgment: ParsedJudgmentMeta | None = None
    paragraphs: list[ParsedParagraph] = field(default_factory=list)
    stage: str | None = None
    # How provision validity dates were obtained: source_periods (stated by the source),
    # enactment_date_presumed, attested_at_edition (known in force only at the edition date),
    # proposal_stage (text of a bill at a legislative stage).
    validity_basis: str = "source_periods"
