"""Loads everything needed to cite, qualify and verify a provision version or judgment paragraph."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.jurisdictions.modules import JudgmentCitationInput, ProvisionCitationInput


@dataclass
class SourceInfo:
    source_id: str
    source_name: str
    source_authority: str
    licence_id: str
    licence_name: str
    attribution: str | None
    document_id: str
    external_id: str
    document_uri: str | None
    retrieved_at: datetime
    sha256: str
    locator: str


@dataclass
class Treatment:
    relation: str
    citing_kind: str  # judgment | legislation
    citing_title: str
    citing_citation: str
    citing_uri: str
    date: date | None
    pinpoint: str | None
    is_proposed: bool = False


@dataclass
class VersionSummary:
    id: str
    language: str
    status: str
    valid_from: date | None
    valid_to: date | None
    valid_to_basis: str
    text_sha256: str


@dataclass
class ProvisionData:
    provision: m.Provision
    version: m.ProvisionVersion
    instrument: m.Instrument
    instrument_version: m.InstrumentVersion
    source: SourceInfo
    citation: str
    currency_date: date | None
    outstanding_changes: list[dict[str, Any]]
    timeline: list[VersionSummary]
    parallel_languages: list[dict[str, Any]]
    judicial_treatment: list[Treatment]
    legislative_treatment: list[Treatment]
    implementing_instruments: list[str] = field(default_factory=list)


@dataclass
class JudgmentData:
    paragraph: m.JudgmentParagraph
    version: m.JudgmentVersion
    judgment: m.Judgment
    court_name: str
    source: SourceInfo
    citation: str
    treatment: list[Treatment]
    cites: list[dict[str, Any]]


def _source_info(session: Session, catalog: Catalog, doc_id: uuid.UUID, locator: str) -> SourceInfo:
    doc = session.get(m.SourceDocument, doc_id)
    assert doc is not None
    descriptor = catalog.sources.get(doc.source_id)
    return SourceInfo(
        source_id=doc.source_id,
        source_name=descriptor.name,
        source_authority=descriptor.authority,
        licence_id=descriptor.licence.id,
        licence_name=descriptor.licence.name,
        attribution=descriptor.licence.attribution_text,
        document_id=str(doc.id),
        external_id=doc.external_id,
        document_uri=doc.uri,
        retrieved_at=doc.retrieved_at,
        sha256=doc.sha256,
        locator=locator,
    )


def provision_citation(catalog: Catalog, instrument: m.Instrument, provision: m.Provision, language: str) -> str:
    module = catalog.module_for(instrument.jurisdiction_id)
    profile = catalog.jurisdictions.get(instrument.jurisdiction_id)
    return module.provision_citation(
        ProvisionCitationInput(
            instrument_title=instrument.short_title or instrument.title,
            instrument_type=instrument.instrument_type,
            jurisdiction_id=instrument.jurisdiction_id,
            top_kind=provision.top_kind,
            num_path=list(provision.num_path),
            language=language,
            ext=instrument.ext or {},
            profile_extensions=profile.extensions,
        )
    )


def judgment_citation(
    catalog: Catalog, judgment: m.Judgment, court_name: str, paragraph: str | None, language: str
) -> str:
    module = catalog.module_for(judgment.jurisdiction_id)
    return module.judgment_citation(
        JudgmentCitationInput(
            case_name=judgment.case_name,
            neutral_citation=judgment.neutral_citation,
            decision_date=judgment.decision_date,
            docket=judgment.docket,
            court_id=judgment.court_id,
            court_name=court_name,
            paragraph=paragraph,
            language=language,
            jurisdiction_id=judgment.jurisdiction_id,
        )
    )


def _ancestor_ids(session: Session, provision: m.Provision) -> list[uuid.UUID]:
    ids = [provision.id]
    parent_id = provision.parent_id
    while parent_id is not None:
        ids.append(parent_id)
        parent = session.get(m.Provision, parent_id)
        parent_id = parent.parent_id if parent else None
    return ids


def _citing(session: Session, catalog: Catalog, link: m.CitationLink) -> Treatment | None:
    if link.from_kind == "judgment_paragraph":
        row = session.execute(
            select(m.JudgmentParagraph, m.JudgmentVersion, m.Judgment, m.Court)
            .join(m.JudgmentVersion, m.JudgmentParagraph.judgment_version_id == m.JudgmentVersion.id)
            .join(m.Judgment, m.JudgmentVersion.judgment_id == m.Judgment.id)
            .join(m.Court, m.Judgment.court_id == m.Court.id)
            .where(m.JudgmentParagraph.id == link.from_id)
        ).one_or_none()
        if row is None:
            return None
        para, version, judgment, court = row
        return Treatment(
            relation=link.relation,
            citing_kind="judgment",
            citing_title=judgment.case_name,
            citing_citation=judgment_citation(catalog, judgment, court.name, para.num, version.language),
            citing_uri=judgment.uri,
            date=judgment.decision_date,
            pinpoint=para.num,
        )
    if link.from_kind == "provision_version":
        row2 = session.execute(
            select(m.ProvisionVersion, m.Provision, m.Instrument)
            .join(m.Provision, m.ProvisionVersion.provision_id == m.Provision.id)
            .join(m.Instrument, m.Provision.instrument_id == m.Instrument.id)
            .where(m.ProvisionVersion.id == link.from_id)
        ).one_or_none()
        if row2 is None:
            return None
        pv, prov, inst = row2
        return Treatment(
            relation=link.relation,
            citing_kind="legislation",
            citing_title=inst.title,
            citing_citation=provision_citation(catalog, inst, prov, pv.language),
            citing_uri=prov.uri,
            date=pv.valid_from or inst.work_date,
            pinpoint=None,
            is_proposed=inst.is_proposed,
        )
    return None


def load_provision(session: Session, catalog: Catalog, version_id: uuid.UUID) -> ProvisionData:
    pv = session.get(m.ProvisionVersion, version_id)
    assert pv is not None
    provision = session.get(m.Provision, pv.provision_id)
    assert provision is not None
    instrument = session.get(m.Instrument, provision.instrument_id)
    iv = session.get(m.InstrumentVersion, pv.instrument_version_id)
    assert instrument is not None and iv is not None

    currency = session.scalar(
        select(func.max(m.InstrumentVersion.currency_date))
        .join(m.ProvisionVersionSource, m.ProvisionVersionSource.instrument_version_id == m.InstrumentVersion.id)
        .where(m.ProvisionVersionSource.provision_version_id == pv.id)
    )
    ancestors = _ancestor_ids(session, provision)
    eids = {session.get(m.Provision, a).eid for a in ancestors}  # type: ignore[union-attr]
    outstanding: list[dict[str, Any]] = []
    attested_by = session.scalars(
        select(m.InstrumentVersion)
        .join(m.ProvisionVersionSource, m.ProvisionVersionSource.instrument_version_id == m.InstrumentVersion.id)
        .where(m.ProvisionVersionSource.provision_version_id == pv.id)
    ).all()
    for attesting in attested_by:
        for change in attesting.outstanding_changes or []:
            if (change.get("affects_eid") in eids or change.get("affects_eid") is None) and change not in outstanding:
                outstanding.append(change)

    timeline = [
        VersionSummary(str(v.id), v.language, v.status, v.valid_from, v.valid_to, v.valid_to_basis, v.text_sha256)
        for v in session.scalars(
            select(m.ProvisionVersion)
            .where(m.ProvisionVersion.provision_id == provision.id, m.ProvisionVersion.language == pv.language)
            .order_by(m.ProvisionVersion.valid_from.asc().nulls_last())
        )
    ]
    parallel = [
        {
            "language": v.language,
            "version_id": str(v.id),
            "authoritative": iv2.authoritative,
            "text_origin": iv2.text_origin,
            "text": v.text,
            "citation": provision_citation(catalog, instrument, provision, v.language),
        }
        for v, iv2 in session.execute(
            select(m.ProvisionVersion, m.InstrumentVersion)
            .join(m.InstrumentVersion, m.ProvisionVersion.instrument_version_id == m.InstrumentVersion.id)
            .where(
                m.ProvisionVersion.provision_id == provision.id,
                m.ProvisionVersion.language != pv.language,
                m.ProvisionVersion.status == pv.status,
                m.ProvisionVersion.valid_from.is_(None)
                if pv.valid_from is None
                else m.ProvisionVersion.valid_from == pv.valid_from,
            )
        )
    ]

    links = session.scalars(select(m.CitationLink).where(m.CitationLink.to_provision_id.in_(ancestors))).all()
    judicial: list[Treatment] = []
    legislative: list[Treatment] = []
    for link in links:
        t = _citing(session, catalog, link)
        if t is None:
            continue
        (judicial if t.citing_kind == "judgment" else legislative).append(t)
    judicial.sort(key=lambda t: t.date or date.min)
    legislative.sort(key=lambda t: t.date or date.min)

    implementing: list[str] = []
    if instrument.instrument_type == "treaty":
        for link in session.scalars(
            select(m.CitationLink).where(
                m.CitationLink.to_instrument_id == instrument.id, m.CitationLink.relation == "implements"
            )
        ):
            t = _citing(session, catalog, link)
            if t is not None:
                implementing.append(t.citing_title)

    return ProvisionData(
        provision=provision,
        version=pv,
        instrument=instrument,
        instrument_version=iv,
        source=_source_info(session, catalog, pv.source_document_id, pv.source_locator),
        citation=provision_citation(catalog, instrument, provision, pv.language),
        currency_date=currency,
        outstanding_changes=outstanding,
        timeline=timeline,
        parallel_languages=parallel,
        judicial_treatment=judicial,
        legislative_treatment=legislative,
        implementing_instruments=sorted(set(implementing)),
    )


def load_judgment_paragraph(session: Session, catalog: Catalog, paragraph_id: uuid.UUID) -> JudgmentData:
    para = session.get(m.JudgmentParagraph, paragraph_id)
    assert para is not None
    version = session.get(m.JudgmentVersion, para.judgment_version_id)
    assert version is not None
    judgment = session.get(m.Judgment, version.judgment_id)
    assert judgment is not None
    court = session.get(m.Court, judgment.court_id)
    assert court is not None
    treatment = [
        t
        for t in (
            _citing(session, catalog, link)
            for link in session.scalars(select(m.CitationLink).where(m.CitationLink.to_judgment_id == judgment.id))
        )
        if t is not None
    ]
    cites = [
        {"relation": link.relation, "text": link.citation_text, "uri": link.to_uri}
        for link in session.scalars(
            select(m.CitationLink).where(
                m.CitationLink.from_kind == "judgment_paragraph", m.CitationLink.from_id == para.id
            )
        )
    ]
    return JudgmentData(
        paragraph=para,
        version=version,
        judgment=judgment,
        court_name=court.name,
        source=_source_info(session, catalog, version.source_document_id, para.eid),
        citation=judgment_citation(catalog, judgment, court.name, para.num, version.language),
        treatment=sorted(treatment, key=lambda t: t.date or date.min),
        cites=cites,
    )
