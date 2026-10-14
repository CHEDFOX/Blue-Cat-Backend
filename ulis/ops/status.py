"""`status`: what is loaded, per jurisdiction, and how fresh the last ingestion and verification are."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ulis.api.security import migration_heads
from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ops.text import format_table, human_age


@dataclass
class JurisdictionStatus:
    jurisdiction_id: str
    instruments: int = 0
    provisions: int = 0
    passages: int = 0
    judgments: int = 0
    documents: int = 0
    verified_documents: int = 0
    failed_documents: int = 0
    last_verified: datetime | None = None
    last_run: datetime | None = None
    last_run_source: str | None = None
    last_run_status: str | None = None
    sources: set[str] = field(default_factory=set)


def _counts(session: Session, jurisdiction_col: Any, extra: Any = None) -> dict[str, int]:
    stmt = select(jurisdiction_col, func.count(extra) if extra is not None else func.count()).group_by(jurisdiction_col)
    return {str(j): int(n) for j, n in session.execute(stmt)}


def collect_status(session: Session, catalog: Catalog, include_empty: bool = False) -> dict[str, Any]:
    now = datetime.now(UTC)
    rows: dict[str, JurisdictionStatus] = {}

    def row(jurisdiction_id: str) -> JurisdictionStatus:
        return rows.setdefault(jurisdiction_id, JurisdictionStatus(jurisdiction_id))

    for j, n in _counts(session, m.Instrument.jurisdiction_id).items():
        row(j).instruments = n
    provisions = (
        select(m.Instrument.jurisdiction_id, func.count(m.Provision.id))
        .join(m.Provision, m.Provision.instrument_id == m.Instrument.id)
        .group_by(m.Instrument.jurisdiction_id)
    )
    for j, n in session.execute(provisions):
        row(j).provisions = int(n)
    for j, n in _counts(session, m.Passage.jurisdiction_id).items():
        row(j).passages = n
    for j, n in _counts(session, m.Judgment.jurisdiction_id).items():
        row(j).judgments = n

    # Source documents per jurisdiction (via the versions they attest), and the sources they came from.
    doc_pairs = (
        select(m.Instrument.jurisdiction_id, m.InstrumentVersion.source_document_id)
        .join(m.InstrumentVersion, m.InstrumentVersion.instrument_id == m.Instrument.id)
        .union(
            select(m.Judgment.jurisdiction_id, m.JudgmentVersion.source_document_id).join(
                m.JudgmentVersion, m.JudgmentVersion.judgment_id == m.Judgment.id
            )
        )
    )
    doc_source = {str(d): s for d, s in session.execute(select(m.SourceDocument.id, m.SourceDocument.source_id))}
    verification = {
        str(subject): (result, at)
        for subject, result, at in session.execute(
            text(
                "SELECT DISTINCT ON (subject_id) subject_id, result, checked_at FROM verification_records "
                "WHERE subject_kind = 'source_document' AND \"check\" = 'source_integrity' "
                "ORDER BY subject_id, checked_at DESC"
            )
        )
    }
    for j, doc_uuid in session.execute(doc_pairs):
        r = row(j)
        r.documents += 1
        doc_id = str(doc_uuid)
        if doc_id in doc_source:
            r.sources.add(doc_source[doc_id])
        if doc_id in verification:
            result, at = verification[doc_id]
            r.verified_documents += 1
            r.failed_documents += result != "pass"
            if r.last_verified is None or at > r.last_verified:
                r.last_verified = at

    runs = {
        s: (status, finished or started)
        for s, status, started, finished in session.execute(
            text(
                "SELECT DISTINCT ON (source_id) source_id, status, started_at, finished_at "
                "FROM ingestion_runs ORDER BY source_id, started_at DESC"
            )
        )
    }
    for r in rows.values():
        for source_id in sorted(r.sources):
            if source_id in runs and (r.last_run is None or runs[source_id][1] > r.last_run):
                r.last_run_status, r.last_run = runs[source_id]
                r.last_run_source = source_id

    if include_empty:
        for jurisdiction_id in catalog.jurisdictions.profiles:
            row(jurisdiction_id)
    loaded_sources = set(doc_source.values())
    active = sorted(s.id for s in catalog.sources if s.status == "active" and s.authority != "fictional")
    current = set(session.execute(text("SELECT version_num FROM alembic_version")).scalars())
    totals = {
        "jurisdictions_with_data": sum(1 for r in rows.values() if r.instruments or r.judgments),
        "instruments": sum(r.instruments for r in rows.values()),
        "provisions": sum(r.provisions for r in rows.values()),
        "passages": sum(r.passages for r in rows.values()),
        "judgments": sum(r.judgments for r in rows.values()),
        "source_documents": len(doc_source),
    }
    return {
        "generated_at": now,
        "migrations": {
            "current": sorted(current),
            "head": sorted(migration_heads()),
            "at_head": current == migration_heads(),
        },
        "totals": totals,
        "jurisdictions": sorted(rows.values(), key=lambda r: r.jurisdiction_id),
        "sources_never_loaded": [s for s in active if s not in loaded_sources],
        "verification": {
            "documents_verified": len(verification),
            "documents_total": len(doc_source),
            "last_verified": max((at for _, at in verification.values()), default=None),
        },
    }


def render_status(data: dict[str, Any]) -> str:
    now: datetime = data["generated_at"]
    totals, mig, ver = data["totals"], data["migrations"], data["verification"]
    migration_note = "at head" if mig["at_head"] else "NOT at head, expected " + ", ".join(mig["head"])
    lines = [
        f"ULIS status at {now:%Y-%m-%d %H:%M} UTC",
        f"  migrations: {', '.join(mig['current']) or 'none'} ({migration_note})",
        f"  loaded: {totals['jurisdictions_with_data']} jurisdictions, {totals['instruments']:,} instruments, "
        f"{totals['provisions']:,} provisions, {totals['passages']:,} passages, {totals['judgments']:,} judgments, "
        f"{totals['source_documents']:,} source documents",
        f"  verify-sources: {ver['documents_verified']:,}/{ver['documents_total']:,} documents checked, "
        f"last {human_age(now, ver['last_verified'])}",
        "",
    ]
    table_rows: list[list[object]] = []
    for r in data["jurisdictions"]:
        verified = f"{r.verified_documents}/{r.documents}" if r.documents else "-"
        if r.failed_documents:
            verified += f" ({r.failed_documents} FAILED)"
        elif r.verified_documents:
            verified += f", {human_age(now, r.last_verified)}"
        ingestion = (
            f"{r.last_run_source} {r.last_run_status}, {human_age(now, r.last_run)}" if r.last_run_source else "-"
        )
        table_rows.append(
            [r.jurisdiction_id, r.instruments, r.provisions, r.passages, r.judgments, ingestion, verified]
        )
    lines.append(
        format_table(
            ["JURISDICTION", "INSTRUMENTS", "PROVISIONS", "PASSAGES", "JUDGMENTS", "LAST INGESTION", "VERIFIED"],
            [[c if not isinstance(c, int) else f"{c:,}" for c in rowvals] for rowvals in table_rows],
            right=(1, 2, 3, 4),
        )
        if table_rows
        else "no jurisdiction has data yet: run `python -m ulis.cli load-all`"
    )
    if data["sources_never_loaded"]:
        lines += ["", "active sources with no stored document: " + ", ".join(data["sources_never_loaded"])]
    return "\n".join(lines)


def status_as_json(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    out["generated_at"] = data["generated_at"].isoformat()
    out["verification"] = {
        **data["verification"],
        "last_verified": data["verification"]["last_verified"].isoformat()
        if data["verification"]["last_verified"]
        else None,
    }
    out["jurisdictions"] = [
        {
            "jurisdiction": r.jurisdiction_id,
            "instruments": r.instruments,
            "provisions": r.provisions,
            "passages": r.passages,
            "judgments": r.judgments,
            "source_documents": r.documents,
            "documents_verified": r.verified_documents,
            "documents_failed_verification": r.failed_documents,
            "last_verified": r.last_verified.isoformat() if r.last_verified else None,
            "last_ingestion": {"source": r.last_run_source, "status": r.last_run_status, "at": r.last_run.isoformat()}
            if r.last_run and r.last_run_source
            else None,
            "sources": sorted(r.sources),
        }
        for r in data["jurisdictions"]
    ]
    return out
