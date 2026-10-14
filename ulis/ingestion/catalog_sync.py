"""Synchronise the file-based catalog (profiles, courts, sources, concepts) into the database."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.languages import search_config


class ProfileVersionConflict(RuntimeError):
    pass


def _sha(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def sync_catalog(session: Session, catalog: Catalog, actor: str = "system") -> dict[str, int]:
    registry = catalog.jurisdictions
    # Parents before children so foreign keys resolve.
    ordered = sorted(registry.profiles.values(), key=lambda p: len(registry.ancestors(p.id)))
    for profile in ordered:
        sha = profile.sha256()
        existing_version = session.scalar(
            select(m.JurisdictionProfileVersion).where(
                m.JurisdictionProfileVersion.jurisdiction_id == profile.id,
                m.JurisdictionProfileVersion.profile_version == profile.profile_version,
            )
        )
        if existing_version is not None and existing_version.sha256 != sha:
            raise ProfileVersionConflict(
                f"profile {profile.id} {profile.profile_version} changed without a version bump"
            )
        row = session.get(m.Jurisdiction, profile.id) or m.Jurisdiction(id=profile.id)
        row.name = profile.name
        row.kind = profile.kind
        row.parent_id = profile.parent
        row.fictional = profile.fictional
        row.profile_version = profile.profile_version
        row.profile_sha256 = sha
        row.verification_status = profile.verification.status
        row.corpus_status = profile.coverage.corpus_status
        row.official_languages = list(profile.official_languages)
        session.add(row)
        session.flush()
        if existing_version is None:
            session.add(
                m.JurisdictionProfileVersion(
                    jurisdiction_id=profile.id,
                    profile_version=profile.profile_version,
                    sha256=sha,
                    content=profile.model_dump(mode="json"),
                )
            )
            session.add(
                m.AuditEvent(
                    actor=actor,
                    action="profile.loaded",
                    subject=f"{profile.id}@{profile.profile_version}",
                    details={"sha256": sha},
                )
            )
    session.flush()

    for profile in ordered:
        for court in profile.courts:
            court_row = session.get(m.Court, court.id) or m.Court(id=court.id)
            court_row.jurisdiction_id = profile.id
            court_row.name = court.name
            court_row.kind = court.kind
            court_row.rank = court.rank
            court_row.appeals_to = list(court.appeals_to)
            court_row.binds_self = court.binds_self
            session.add(court_row)

    for source in catalog.sources:
        descriptor = source.model_dump(mode="json")
        source_row = session.get(m.Source, source.id) or m.Source(id=source.id)
        source_row.name = source.name
        source_row.publisher = source.publisher
        source_row.kind = source.kind
        source_row.authority = source.authority
        source_row.status = source.status
        source_row.licence_id = source.licence.id
        source_row.licence_review_status = source.licence.review_status
        source_row.descriptor = descriptor
        source_row.descriptor_sha256 = _sha(descriptor)
        session.add(source_row)

    for concept in catalog.concepts:
        crow = session.scalar(select(m.LegalConcept).where(m.LegalConcept.key == concept.key))
        if crow is None:
            crow = m.LegalConcept(key=concept.key, description=concept.description, scope_jurisdiction_id=concept.scope)
            session.add(crow)
            session.flush()
        else:
            crow.description = concept.description
        existing = {
            (lbl.language, lbl.label)
            for lbl in session.scalars(select(m.ConceptLabel).where(m.ConceptLabel.concept_id == crow.id))
        }
        for language, labels in concept.labels.items():
            for label in labels:
                if (language, label) not in existing:
                    session.add(
                        m.ConceptLabel(
                            concept_id=crow.id, language=language, label=label, search_config=search_config(language)
                        )
                    )
    session.flush()
    return {
        "jurisdictions": len(registry.profiles),
        "sources": len(catalog.sources.sources),
        "concepts": len(catalog.concepts),
    }
