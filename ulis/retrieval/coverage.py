"""Coverage statistics: what is actually indexed for each jurisdiction.

Counting passages reads every row of a jurisdiction (about 0.7 s for the 1.9 million passages of the US eCFR), and
every search reports coverage for the jurisdictions it involves. The counts change only when data is loaded, so they
are cached per database and per jurisdiction until the ingestion runs change: every ingestion records its run in the
same transaction as its data, so a committed load always invalidates the cache. A transaction that has written
anything itself counts afresh and leaves the cache alone (its rows may still be rolled back). A `reindex` on its own
does not invalidate the cache; restart the API after one.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ulis.catalog import Catalog
from ulis.db import models as m


@dataclass(frozen=True)
class _Counts:
    instruments: int
    judgments: int
    passages: int
    languages: tuple[str, ...]


# database URL -> (ingestion signature, jurisdiction id -> counts)
_CACHE: dict[str, tuple[tuple[Any, ...], dict[str, _Counts]]] = {}
_LOCK = threading.Lock()


def _signature(session: Session) -> tuple[Any, ...] | None:
    """The state of the ingestion runs, or None when this transaction has written (nothing it sees is settled)."""
    *signature, wrote = session.execute(
        text(
            "SELECT count(*), max(started_at), max(finished_at), pg_current_xact_id_if_assigned() IS NOT NULL "
            "FROM ingestion_runs"
        )
    ).one()
    return None if wrote else tuple(signature)


def _count(session: Session, ids: list[str]) -> dict[str, _Counts]:
    def per_jurisdiction(model: Any) -> dict[str, int]:
        return dict(
            session.execute(
                select(model.jurisdiction_id, func.count())
                .where(model.jurisdiction_id.in_(ids))
                .group_by(model.jurisdiction_id)
            ).all()
        )

    instruments = per_jurisdiction(m.Instrument)
    judgments = per_jurisdiction(m.Judgment)
    passages: dict[str, int] = {}
    languages: dict[str, list[str]] = {}
    for jid, lang, n in session.execute(
        select(m.Passage.jurisdiction_id, m.Passage.language, func.count())
        .where(m.Passage.jurisdiction_id.in_(ids))
        .group_by(m.Passage.jurisdiction_id, m.Passage.language)
    ):
        passages[jid] = passages.get(jid, 0) + n
        languages.setdefault(jid, []).append(lang)
    return {
        jid: _Counts(
            instruments.get(jid, 0), judgments.get(jid, 0), passages.get(jid, 0), tuple(sorted(languages.get(jid, [])))
        )
        for jid in ids
    }


def database_key(session: Session) -> str:
    """Caches are per database: tests and tools open several in one process."""
    return str(session.get_bind().engine.url)


def _counts(session: Session, ids: list[str]) -> dict[str, _Counts]:
    key = database_key(session)
    signature = _signature(session)
    if signature is None:
        return _count(session, list(dict.fromkeys(ids)))
    with _LOCK:
        entry = _CACHE.get(key)
        if entry is None or entry[0] != signature:
            entry = (signature, {})
            _CACHE[key] = entry
        cached = dict(entry[1])
    missing = [jid for jid in dict.fromkeys(ids) if jid not in cached]
    if missing:
        fresh = _count(session, missing)
        cached.update(fresh)
        with _LOCK:
            current = _CACHE.get(key)
            if current is not None and current[0] == signature:
                current[1].update(fresh)
    return cached


def indexed_languages(session: Session, catalog: Catalog, jurisdiction_ids: Iterable[str] | None = None) -> set[str]:
    """Every language with indexed passages in the given jurisdictions, or in all of the catalog's (cached like the
    counts)."""
    ids = list(jurisdiction_ids) if jurisdiction_ids is not None else list(catalog.jurisdictions.profiles)
    return {lang for c in _counts(session, ids).values() for lang in c.languages}


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def coverage_for(
    session: Session, catalog: Catalog, jurisdiction_ids: Iterable[str] | None = None
) -> list[dict[str, Any]]:
    ids = list(jurisdiction_ids) if jurisdiction_ids is not None else list(catalog.jurisdictions.profiles)
    counts = _counts(session, ids) if ids else {}
    out = []
    for jid in ids:
        profile = catalog.jurisdictions.get(jid)
        sources = [s.id for s in catalog.sources if jid in s.jurisdictions and s.status == "active"]
        c = counts[jid]
        out.append(
            {
                "jurisdiction": jid,
                "name": profile.name,
                "fictional": profile.fictional,
                "profile_version": profile.profile_version,
                "profile_verification": profile.verification.status,
                "corpus_status": profile.coverage.corpus_status,
                "instruments": c.instruments,
                "judgments": c.judgments,
                "passages": c.passages,
                "indexed_languages": list(c.languages),
                "active_sources": sources,
                "known_gaps": list(profile.coverage.known_gaps),
            }
        )
    return out
