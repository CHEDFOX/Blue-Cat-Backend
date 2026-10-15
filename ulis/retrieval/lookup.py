"""Direct lookups: point-in-time provisions, instruments and judgments."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.retrieval.authority import classify_provision
from ulis.retrieval.details import (
    _source_info,
    cited_date,
    judgment_citation,
    load_provision,
    provision_citation,
    title_in,
)
from ulis.temporal.validity import resolve_at
from ulis.verification.limitations import Limitation, jurisdiction_limitations, provision_limitations


class NotFound(LookupError):
    pass


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _source_block(src: Any) -> dict[str, Any]:
    """The source of a text with the attribution its licence requires: returned with every result."""
    block = asdict(src)
    block["retrieved_at"] = src.retrieved_at.isoformat()
    return block


def provision_at(
    session: Session, catalog: Catalog, uri: str, as_of: date, language: str | None = None
) -> dict[str, Any]:
    """Answer "what did this provision say on date D?" with the full version history."""
    provision = session.scalar(select(m.Provision).where(m.Provision.uri == uri))
    if provision is None:
        raise NotFound(f"no provision {uri}")
    instrument = session.get(m.Instrument, provision.instrument_id)
    assert instrument is not None
    versions = session.scalars(select(m.ProvisionVersion).where(m.ProvisionVersion.provision_id == provision.id)).all()
    languages = sorted({v.language for v in versions})
    if language is None:
        official = catalog.jurisdictions.get(instrument.jurisdiction_id).official_languages
        language = next((lang for lang in official if lang in languages), languages[0] if languages else None)
    if language not in languages:
        raise NotFound(f"no {language} text for {uri}; available: {languages}")
    in_lang = [v for v in versions if v.language == language]
    history_start = session.scalar(
        select(func.min(m.InstrumentVersion.version_date)).where(m.InstrumentVersion.instrument_id == instrument.id)
    )
    pit = resolve_at(in_lang, as_of, history_start=history_start, work_date=instrument.work_date)

    limitations: list[Limitation] = []
    selected: dict[str, Any] | None = None
    selected_version_date: date | None = None
    if pit.version is not None:
        data = load_provision(session, catalog, pit.version.id)  # type: ignore[arg-type]
        selected_version_date = cited_date(data.instrument_version, data.currency_date)
        authority = classify_provision(
            catalog.jurisdictions,
            target=None,
            passage_jurisdiction=instrument.jurisdiction_id,
            doc_type=instrument.instrument_type,
            status=data.version.status,
            applies_on_date=None if pit.status == "in_force_date_unverified" else pit.status == "in_force",
            as_of=as_of,
        )
        limitations = provision_limitations(catalog, data, authority, as_of)
        selected = {
            "id": str(data.version.id),
            "status": data.version.status,
            "heading": data.version.heading,
            "text": data.version.text,
            "text_sha256": data.version.text_sha256,
            "valid_from": _iso(data.version.valid_from),
            "valid_to": _iso(data.version.valid_to),
            "valid_to_basis": data.version.valid_to_basis,
            "valid_from_basis": data.version.valid_from_basis,
            "note": data.version.note,
            "expression_uri": data.instrument_version.uri,
            "currency_date": _iso(data.currency_date),
            "authority": {"category": authority.category, "label": authority.label, "rationale": authority.rationale},
            "source": _source_block(data.source),
        }
    else:
        limitations = jurisdiction_limitations(catalog, instrument.jurisdiction_id)
        messages = {
            "not_yet_in_force": f"No version of this provision was in force on {as_of}; it took effect later.",
            "prospective_only": f"On {as_of} this provision had been enacted but was not in force.",
            "no_version": f"No version of this provision is recorded for {as_of}.",
            "history_not_indexed": f"No version of this provision is indexed for {as_of}. Indexed history for this "
            f"instrument starts on {history_start}; an earlier version may have applied, or the provision may not "
            "yet have existed.",
        }
        limitations.insert(0, Limitation("NO_VERSION_ON_DATE", "critical", messages.get(pit.status, pit.status)))

    children = session.scalars(
        select(m.Provision).where(m.Provision.parent_id == provision.id).order_by(m.Provision.sort_order)
    ).all()
    return {
        "provision": {
            "uri": provision.uri,
            "eid": provision.eid,
            "kind": provision.kind,
            "num_path": list(provision.num_path),
            "citation": provision_citation(
                catalog, instrument, provision, language, selected_version_date, title_in(session, instrument, language)
            ),
        },
        "instrument": {
            "uri": instrument.uri,
            "title": instrument.title,
            "type": instrument.instrument_type,
            "jurisdiction": instrument.jurisdiction_id,
            "is_proposed": instrument.is_proposed,
        },
        "as_of": as_of.isoformat(),
        "language": language,
        "available_languages": languages,
        "status_on_date": pit.status,
        "version": selected,
        "timeline": [
            {
                "id": str(v.id),
                "status": v.status,
                "valid_from": _iso(v.valid_from),
                "valid_to": _iso(v.valid_to),
                "valid_to_basis": v.valid_to_basis,
                "valid_from_basis": v.valid_from_basis,
                "text": v.text,
                "note": v.note,
                "selected": pit.version is not None and v.id == pit.version.id,
            }
            for v in sorted(in_lang, key=lambda v: (v.valid_from is None, v.valid_from or date.min))
        ],
        "children": [{"uri": c.uri, "num_path": list(c.num_path)} for c in children],
        "limitations": [lim.as_dict() for lim in limitations],
    }


def instrument_detail(session: Session, catalog: Catalog, uri: str) -> dict[str, Any]:
    instrument = session.scalar(select(m.Instrument).where(m.Instrument.uri == uri))
    if instrument is None:
        raise NotFound(f"no instrument {uri}")
    versions = session.scalars(
        select(m.InstrumentVersion)
        .where(m.InstrumentVersion.instrument_id == instrument.id)
        .order_by(m.InstrumentVersion.version_date, m.InstrumentVersion.language)
    ).all()
    provisions = session.scalars(
        select(m.Provision)
        .where(m.Provision.instrument_id == instrument.id, m.Provision.parent_id.is_(None))
        .order_by(m.Provision.sort_order)
    ).all()
    events = session.scalars(
        select(m.LifecycleEvent)
        .where(m.LifecycleEvent.instrument_id == instrument.id)
        .order_by(m.LifecycleEvent.event_date)
    ).all()
    parties = session.scalars(select(m.TreatyParty).where(m.TreatyParty.treaty_id == instrument.id)).all()
    return {
        "uri": instrument.uri,
        "title": instrument.title,
        "short_title": instrument.short_title,
        "type": instrument.instrument_type,
        "jurisdiction": instrument.jurisdiction_id,
        "number": instrument.number,
        "work_date": _iso(instrument.work_date),
        "is_proposed": instrument.is_proposed,
        "extent": list(instrument.extent),
        "ext": instrument.ext,
        "versions": [
            {
                "uri": v.uri,
                "language": v.language,
                "title": v.title,
                "authoritative": v.authoritative,
                "text_origin": v.text_origin,
                "consolidation": v.consolidation_kind,
                "version_date": _iso(v.version_date),
                "currency_date": _iso(v.currency_date),
                "outstanding_changes": v.outstanding_changes,
            }
            for v in versions
        ],
        "provisions": [{"uri": p.uri, "eid": p.eid, "kind": p.kind, "num_path": list(p.num_path)} for p in provisions],
        "events": [
            {"type": e.event_type, "date": _iso(e.event_date), "affecting_uri": e.affecting_uri} for e in events
        ],
        "treaty_parties": [
            {
                "party": p.party_id,
                "signed_on": _iso(p.signed_on),
                "consent_type": p.consent_type,
                "consented_on": _iso(p.consented_on),
                "entry_into_force_on": _iso(p.entry_into_force_on),
            }
            for p in parties
        ],
        "limitations": [lim.as_dict() for lim in jurisdiction_limitations(catalog, instrument.jurisdiction_id)],
    }


def judgment_detail(session: Session, catalog: Catalog, uri: str) -> dict[str, Any]:
    judgment = session.scalar(select(m.Judgment).where(m.Judgment.uri == uri))
    if judgment is None:
        raise NotFound(f"no judgment {uri}")
    court = session.get(m.Court, judgment.court_id)
    assert court is not None
    out_versions = []
    for v in session.scalars(select(m.JudgmentVersion).where(m.JudgmentVersion.judgment_id == judgment.id)):
        paras = session.scalars(
            select(m.JudgmentParagraph)
            .where(m.JudgmentParagraph.judgment_version_id == v.id)
            .order_by(m.JudgmentParagraph.sort_order)
        ).all()
        out_versions.append(
            {
                "uri": v.uri,
                "language": v.language,
                "authoritative": v.authoritative,
                "text_origin": v.text_origin,
                "source": _source_block(_source_info(session, catalog, v.source_document_id, "")),
                "paragraphs": [
                    {
                        "id": str(p.id),
                        "eid": p.eid,
                        "num": p.num,
                        "part": p.part,
                        "text": p.text,
                        "text_sha256": p.text_sha256,
                        "citation": judgment_citation(catalog, judgment, court.name, p.num, v.language),
                    }
                    for p in paras
                ],
            }
        )
    first_language = str(out_versions[0]["language"]) if out_versions else "en"
    return {
        "uri": judgment.uri,
        "case_name": judgment.case_name,
        "neutral_citation": judgment.neutral_citation,
        "court": {"id": court.id, "name": court.name},
        "jurisdiction": judgment.jurisdiction_id,
        "decision_date": _iso(judgment.decision_date),
        "docket": judgment.docket,
        "citation": judgment_citation(catalog, judgment, court.name, None, first_language),
        "versions": out_versions,
        "limitations": [lim.as_dict() for lim in jurisdiction_limitations(catalog, judgment.jurisdiction_id)],
    }
