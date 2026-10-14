"""Builds the derived ``passages`` retrieval index from canonical tables.

The index can be dropped and rebuilt at any time; it never holds information that is not
derivable from provision versions and judgment paragraphs.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.languages import search_config, segment_for_search

INDEX_VERSION = 1


def rebuild_passages(
    session: Session,
    catalog: Catalog,
    instrument_ids: Iterable[Any] | None = None,
    judgment_ids: Iterable[Any] | None = None,
) -> int:
    """(Re)index the given instruments/judgments; ``None`` for both means everything."""
    rebuild_all = instrument_ids is None and judgment_ids is None
    inst_ids = list(session.scalars(select(m.Instrument.id))) if rebuild_all else list(instrument_ids or [])
    judg_ids = list(session.scalars(select(m.Judgment.id))) if rebuild_all else list(judgment_ids or [])
    count = 0
    if inst_ids:
        session.execute(delete(m.Passage).where(m.Passage.instrument_id.in_(inst_ids)))
        count += _index_instruments(session, inst_ids)
    if judg_ids:
        session.execute(delete(m.Passage).where(m.Passage.judgment_id.in_(judg_ids)))
        count += _index_judgments(session, judg_ids)
    session.flush()
    return count


def _index_instruments(session: Session, instrument_ids: list[Any]) -> int:
    count = 0
    for instrument in session.scalars(select(m.Instrument).where(m.Instrument.id.in_(instrument_ids))):
        provisions = {
            p.id: p for p in session.scalars(select(m.Provision).where(m.Provision.instrument_id == instrument.id))
        }
        rows = session.execute(
            select(m.ProvisionVersion, m.InstrumentVersion)
            .join(m.InstrumentVersion, m.ProvisionVersion.instrument_version_id == m.InstrumentVersion.id)
            .join(m.Provision, m.ProvisionVersion.provision_id == m.Provision.id)
            .where(m.Provision.instrument_id == instrument.id)
        ).all()
        headings = _headings_by_provision_language(session, list(provisions))
        for pv, iv in rows:
            prov = provisions[pv.provision_id]
            if not prov.index_unit or pv.status == "repealed" or not pv.text:
                continue
            top = prov
            while top.parent_id is not None:
                top = provisions[top.parent_id]
            context_parts = [iv.title or instrument.title]
            top_heading = headings.get((top.id, pv.language))
            if top_heading:
                context_parts.append(top_heading)
            if pv.heading and pv.heading != top_heading:
                context_parts.append(pv.heading)
            session.add(
                m.Passage(
                    kind="provision",
                    provision_version_id=pv.id,
                    instrument_id=instrument.id,
                    jurisdiction_id=instrument.jurisdiction_id,
                    doc_type=instrument.instrument_type,
                    language=pv.language,
                    search_config=search_config(pv.language),
                    context=". ".join(context_parts),
                    text=pv.text,
                    search_context=segment_for_search(". ".join(context_parts)),
                    search_text=segment_for_search(pv.text),
                    status=pv.status,
                    valid_from=pv.valid_from,
                    valid_to=pv.valid_to,
                    extent=prov.extent or instrument.extent,
                    authoritative=iv.authoritative,
                    is_proposed=instrument.is_proposed,
                    index_version=INDEX_VERSION,
                )
            )
            count += 1
    return count


def _headings_by_provision_language(session: Session, provision_ids: list[Any]) -> dict[tuple[Any, str], str]:
    out: dict[tuple[Any, str], str] = {}
    if not provision_ids:
        return out
    for pv in session.scalars(select(m.ProvisionVersion).where(m.ProvisionVersion.provision_id.in_(provision_ids))):
        if pv.heading:
            out.setdefault((pv.provision_id, pv.language), pv.heading)
    return out


def _index_judgments(session: Session, judgment_ids: list[Any]) -> int:
    count = 0
    rows = session.execute(
        select(m.JudgmentParagraph, m.JudgmentVersion, m.Judgment)
        .join(m.JudgmentVersion, m.JudgmentParagraph.judgment_version_id == m.JudgmentVersion.id)
        .join(m.Judgment, m.JudgmentVersion.judgment_id == m.Judgment.id)
        .where(m.Judgment.id.in_(judgment_ids))
    ).all()
    for para, version, judgment in rows:
        context = judgment.case_name + (f" {judgment.neutral_citation}" if judgment.neutral_citation else "")
        session.add(
            m.Passage(
                kind="judgment_paragraph",
                judgment_paragraph_id=para.id,
                judgment_id=judgment.id,
                jurisdiction_id=judgment.jurisdiction_id,
                doc_type="judgment",
                language=version.language,
                search_config=search_config(version.language),
                context=context,
                text=para.text,
                search_context=segment_for_search(context),
                search_text=segment_for_search(para.text),
                status="decided",
                valid_from=judgment.decision_date,
                valid_to=None,
                extent=[judgment.jurisdiction_id],
                authoritative=version.authoritative,
                is_proposed=False,
                index_version=INDEX_VERSION,
            )
        )
        count += 1
    return count
