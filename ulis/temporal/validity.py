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


def _dated(versions: Sequence[VersionLike]) -> list[VersionLike]:
    return sorted(
        (v for v in versions if v.valid_from is not None and v.status in ("in_force", "repealed")),
        key=lambda v: v.valid_from,  # type: ignore[arg-type, return-value]
    )


def derive_valid_to(versions: Sequence[Span]) -> dict[Any, tuple[date | None, str]]:
    """Return ``{version_id: (valid_to, basis)}`` for versions of one provision in one language.

    ``basis`` is ``explicit`` (stated by the source), ``derived`` (start of the next version)
    or ``open`` (no later version known).
    """
    out: dict[Any, tuple[date | None, str]] = {}
    dated = _dated(versions)
    for i, v in enumerate(dated):
        span = v if isinstance(v, Span) else None
        if span is not None and span.valid_to_explicit and span.valid_to is not None:
            out[v.id] = (span.valid_to, "explicit")
        elif i + 1 < len(dated):
            out[v.id] = (dated[i + 1].valid_from, "derived")
        else:
            out[v.id] = (None, "open")
    for v in versions:
        out.setdefault(v.id, (None, "open"))
    return out


def covers(version: VersionLike, as_of: date) -> bool:
    return (
        version.valid_from is not None
        and version.valid_from <= as_of
        and (version.valid_to is None or as_of < version.valid_to)
    )


PointInTimeStatus = Literal["in_force", "repealed", "not_yet_in_force", "prospective_only", "no_version"]


@dataclass
class PointInTime:
    status: PointInTimeStatus
    version: VersionLike | None
    earlier: list[VersionLike] = field(default_factory=list)
    later: list[VersionLike] = field(default_factory=list)
    prospective: list[VersionLike] = field(default_factory=list)


def resolve_at(versions: Sequence[VersionLike], as_of: date) -> PointInTime:
    """Select the version of one provision (one language) applicable on ``as_of``."""
    dated = _dated(versions)
    prospective = [v for v in versions if v.status == "prospective"]
    current = next((v for v in reversed(dated) if covers(v, as_of)), None)
    earlier = [v for v in dated if v.valid_to is not None and v.valid_to <= as_of]
    later = [v for v in dated if v.valid_from is not None and v.valid_from > as_of]
    if current is not None:
        status: PointInTimeStatus = "in_force" if current.status == "in_force" else "repealed"
    elif dated and as_of < dated[0].valid_from:  # type: ignore[operator]
        status = "not_yet_in_force"
    elif prospective:
        status = "prospective_only"
    else:
        status = "no_version"
    return PointInTime(status=status, version=current, earlier=earlier, later=later, prospective=prospective)
