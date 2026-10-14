"""Coverage statistics: what is actually indexed for each jurisdiction."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ulis.catalog import Catalog
from ulis.db import models as m


def coverage_for(
    session: Session, catalog: Catalog, jurisdiction_ids: Iterable[str] | None = None
) -> list[dict[str, Any]]:
    ids = list(jurisdiction_ids) if jurisdiction_ids is not None else list(catalog.jurisdictions.profiles)
    instruments = dict(
        session.execute(select(m.Instrument.jurisdiction_id, func.count()).group_by(m.Instrument.jurisdiction_id)).all()
    )
    judgments = dict(
        session.execute(select(m.Judgment.jurisdiction_id, func.count()).group_by(m.Judgment.jurisdiction_id)).all()
    )
    passages = dict(
        session.execute(select(m.Passage.jurisdiction_id, func.count()).group_by(m.Passage.jurisdiction_id)).all()
    )
    languages: dict[str, list[str]] = {}
    for jid, lang in session.execute(select(m.Passage.jurisdiction_id, m.Passage.language).distinct()):
        languages.setdefault(jid, []).append(lang)
    out = []
    for jid in ids:
        profile = catalog.jurisdictions.get(jid)
        sources = [s.id for s in catalog.sources if jid in s.jurisdictions and s.status == "active"]
        out.append(
            {
                "jurisdiction": jid,
                "name": profile.name,
                "fictional": profile.fictional,
                "profile_version": profile.profile_version,
                "profile_verification": profile.verification.status,
                "corpus_status": profile.coverage.corpus_status,
                "instruments": instruments.get(jid, 0),
                "judgments": judgments.get(jid, 0),
                "passages": passages.get(jid, 0),
                "indexed_languages": sorted(languages.get(jid, [])),
                "active_sources": sources,
                "known_gaps": list(profile.coverage.known_gaps),
            }
        )
    return out
