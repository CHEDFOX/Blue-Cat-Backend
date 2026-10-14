"""Whole-instrument revision snapshots and change reports.

Some sources publish each revision of a law as the *whole* law as amended, dated by the
amendment's entry into force, without per-provision dates (e-Gov 法令検索 in Japan). Parsers
mark such documents ``validity_basis = "revision_snapshot"`` and date every provision from the
snapshot. Three rules then keep the history honest:

* **Unchanged wording continues.** A provision whose heading, text and status are identical to
  the version attested by the previous snapshot continues that version (corroborated), so a
  version starts only when its wording actually changed.
* **Absence ends a provision.** A provision that the previous snapshot contained and this one
  does not (removed, or renumbered by the amendment) ends on this snapshot's date; the end is
  recorded as ``explicit`` because the source's own text for that date no longer contains it.
* **Order matters.** Snapshots of an instrument are recorded oldest first; a snapshot older than
  one already recorded is refused, because it could split an existing version.

A *change report* (``consolidation_kind = "change_report"``) is a source document that lists,
for an expression already recorded, the changes not yet applied to it (e.g. amendments
promulgated but not yet in force). It records no text: it replaces the expression's outstanding
changes with the list it reports, and attests the expression as current on the date it was
retrieved.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ulis.db import models as m

SNAPSHOT_BASIS = "revision_snapshot"
CHANGE_REPORT = "change_report"


class SnapshotOrderError(ValueError):
    pass


def previous_snapshot(session: Session, version: m.InstrumentVersion) -> m.InstrumentVersion | None:
    """The snapshot of the same instrument and language recorded immediately before ``version``."""
    same = (
        m.InstrumentVersion.instrument_id == version.instrument_id,
        m.InstrumentVersion.language == version.language,
        m.InstrumentVersion.validity_basis == SNAPSHOT_BASIS,
        m.InstrumentVersion.id != version.id,
    )
    later = session.scalar(
        select(m.InstrumentVersion.uri).where(*same, m.InstrumentVersion.version_date >= version.version_date).limit(1)
    )
    if later is not None:
        raise SnapshotOrderError(
            f"{version.uri}: a snapshot of the same date or later ({later}) is already recorded; "
            "snapshots must be recorded oldest first"
        )
    return session.scalar(
        select(m.InstrumentVersion)
        .where(*same, m.InstrumentVersion.version_date < version.version_date)
        .order_by(m.InstrumentVersion.version_date.desc())
        .limit(1)
    )


def attested_by(session: Session, version: m.InstrumentVersion) -> dict[Any, m.ProvisionVersion]:
    """Provision versions attested by an instrument version, keyed by provision id."""
    rows = session.scalars(
        select(m.ProvisionVersion)
        .join(m.ProvisionVersionSource, m.ProvisionVersionSource.provision_version_id == m.ProvisionVersion.id)
        .where(m.ProvisionVersionSource.instrument_version_id == version.id)
    )
    return {pv.provision_id: pv for pv in rows}


def continues(previous: m.ProvisionVersion | None, status: str, heading: str | None, text_sha: str) -> bool:
    """Whether a snapshot's wording is the one the previous snapshot attested."""
    return (
        previous is not None
        and previous.status == status
        and previous.text_sha256 == text_sha
        and (previous.heading or None) == (heading or None)
    )


def end_absent(previous: dict[Any, m.ProvisionVersion], present: set[Any], start: date) -> int:
    """End, on ``start``, the versions of provisions the new snapshot no longer contains."""
    ended = 0
    for provision_id, pv in previous.items():
        if provision_id in present or pv.valid_from is None or pv.valid_from >= start:
            continue
        if pv.valid_to is None or pv.valid_to > start:
            pv.valid_to, pv.valid_to_basis = start, "explicit"
            ended += 1
    return ended
