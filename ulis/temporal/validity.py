"""Point-in-time validity.

Conventions (see ``docs/temporal-model.md``):

* Validity is the half-open interval ``[valid_from, valid_to)``: a version that is replaced
  on 1 January 2022 has ``valid_to = 2022-01-01`` and the new version ``valid_from = 2022-01-01``.
* ``valid_from = None`` means the start is unknown or the version is not in force
  (prospective). Such versions are never selected as "the law on date D".
* A ``valid_to`` that the source did not state is *derived* from the next version's start,
  and flagged as derived, never presented as a published fact.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol


class VersionLike(Protocol):
    @property
    def id(self) -> Any: ...
    @property
    def status(self) -> str: ...
    @property
    def valid_from(self) -> date | None: ...
    @property
    def valid_to(self) -> date | None: ...


@dataclass(frozen=True)
class Span:
    id: Any
    status: str
    valid_from: date | None
    valid_to: date | None
    valid_to_explicit: bool = False


LEGAL_CHAIN = ("in_force", "repealed")


def _dated(versions: Sequence[VersionLike], statuses: tuple[str, ...] = LEGAL_CHAIN) -> list[VersionLike]:
    return sorted(
        (v for v in versions if v.valid_from is not None and v.status in statuses),
        key=lambda v: v.valid_from,  # type: ignore[arg-type, return-value]
    )


def derive_valid_to(
    versions: Sequence[Span], statuses: tuple[str, ...] = LEGAL_CHAIN
) -> dict[Any, tuple[date | None, str]]:
    """Return ``{version_id: (valid_to, basis)}`` for versions of one provision in one language.

    ``basis`` is ``explicit`` (stated by the source), ``derived`` (start of the next version)
    or ``open`` (no later version known).
    """
    out: dict[Any, tuple[date | None, str]] = {}
    dated = _dated(versions, statuses)
    for i, v in enumerate(dated):
        span = v if isinstance(v, Span) else None
        if span is not None and span.valid_to_explicit and span.valid_to is not None:
            out[v.id] = (span.valid_to, "explicit")
        elif (later := next((d.valid_from for d in dated[i + 1 :] if d.valid_from > v.valid_from), None)) is not None:  # type: ignore[operator]
            # The next version that starts later. A version starting the same day (an in-force wording and the repeal of
            # the same provision, or two wordings for different extents) does not end this one on its own start date.
            out[v.id] = (later, "derived")
        else:
            out[v.id] = (None, "open")
    if statuses == LEGAL_CHAIN:
        for v in versions:
            out.setdefault(v.id, (None, "open"))
    return out


def covers(version: VersionLike, as_of: date) -> bool:
    return (
        version.valid_from is not None
        and version.valid_from <= as_of
        and (version.valid_to is None or as_of < version.valid_to)
    )


PointInTimeStatus = Literal[
    "in_force",
    "repealed",
    "not_yet_in_force",
    "prospective_only",
    "no_version",
    "in_force_date_unverified",
    "proposed",
    "history_not_indexed",
]


@dataclass
class PointInTime:
    status: PointInTimeStatus
    version: VersionLike | None
    earlier: list[VersionLike] = field(default_factory=list)
    later: list[VersionLike] = field(default_factory=list)
    prospective: list[VersionLike] = field(default_factory=list)


def resolve_at(
    versions: Sequence[VersionLike],
    as_of: date,
    *,
    history_start: date | None = None,
    work_date: date | None = None,
) -> PointInTime:
    """Select the version of one provision (one language) applicable on ``as_of``.

    ``history_start`` is the earliest date from which the instrument's versions are indexed and
    ``work_date`` the date the instrument was made. A date between the two falls in a gap: an
    earlier, unindexed version may have applied, so the answer is ``history_not_indexed`` rather
    than "not yet in force".
    """
    dated = _dated(versions)
    prospective = [v for v in versions if v.status == "prospective"]
    current = next((v for v in reversed(dated) if covers(v, as_of)), None)
    earlier = [v for v in dated if v.valid_to is not None and v.valid_to <= as_of]
    later = [v for v in dated if v.valid_from is not None and v.valid_from > as_of]
    undated = [v for v in versions if v.status == "in_force" and v.valid_from is None]
    proposed = next((v for v in reversed(_dated(versions, ("proposed",))) if covers(v, as_of)), None)
    if current is not None:
        status: PointInTimeStatus = "in_force" if current.status == "in_force" else "repealed"
    elif dated and as_of < dated[0].valid_from and undated:  # type: ignore[operator]
        # An edition-attested wording precedes the first dated one (a newer edition was observed).
        status, current = "in_force_date_unverified", undated[-1]
    elif dated and as_of < dated[0].valid_from:  # type: ignore[operator]
        # With no recorded enactment date, an earlier wording may exist: report the gap.
        in_gap = history_start is not None and as_of < history_start and (work_date is None or work_date <= as_of)
        status = "history_not_indexed" if in_gap else "not_yet_in_force"
    elif undated:
        # Known to be in force only at a source date (e.g. a code edition); the start is unknown.
        status, current = "in_force_date_unverified", undated[-1]
    elif proposed is not None:
        status, current = "proposed", proposed
    elif prospective:
        status = "prospective_only"
    else:
        status = "no_version"
    return PointInTime(status=status, version=current, earlier=earlier, later=later, prospective=prospective)
