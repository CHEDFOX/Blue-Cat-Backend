"""Source integrity: re-derive stored texts from the raw bytes and compare hashes."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.ingestion.pipeline import PARSERS
from ulis.storage.object_store import IntegrityError, ObjectStore


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_source_document(
    session: Session, catalog: Catalog, store: ObjectStore, document_id: uuid.UUID, checker: str = "system"
) -> dict[str, Any]:
    doc = session.get(m.SourceDocument, document_id)
    if doc is None:
        raise KeyError(f"unknown source document {document_id}")
    problems: list[str] = []
    checked = 0
    try:
        raw = store.get(doc.storage_key)
    except (IntegrityError, FileNotFoundError) as exc:
        raw = None
        problems.append(f"raw object unavailable or altered: {exc}")
    if raw is not None:
        if hashlib.sha256(raw).hexdigest() != doc.sha256:
            problems.append("raw bytes do not match the recorded SHA-256")
        parsed_docs = PARSERS[catalog.sources.get(doc.source_id).parser or ""](raw)
        # One raw document can attest several versions of a provision (e.g. a source that carries a
        # provision's whole version history), so each locator maps to the hashes of all its texts.
        units: dict[tuple[str, str], set[str]] = {}
        for d in parsed_docs:
            for p in d.provisions:
                units.setdefault((d.work_uri, p.eid), set()).add(_sha(p.text))
        paras = {(d.work_uri, p.eid): p.text for d in parsed_docs for p in d.paragraphs}
        for pvs, pv, work_uri in session.execute(
            select(m.ProvisionVersionSource, m.ProvisionVersion, m.Instrument.uri)
            .join(m.ProvisionVersion, m.ProvisionVersionSource.provision_version_id == m.ProvisionVersion.id)
            .join(m.InstrumentVersion, m.ProvisionVersionSource.instrument_version_id == m.InstrumentVersion.id)
            .join(m.Instrument, m.InstrumentVersion.instrument_id == m.Instrument.id)
            .where(m.ProvisionVersionSource.source_document_id == doc.id)
        ):
            checked += 1
            source_hashes = units.get((work_uri, pvs.source_locator))
            if source_hashes is None:
                problems.append(f"{pvs.source_locator}: not found in source")
            elif pv.text_sha256 not in source_hashes:
                problems.append(f"{pvs.source_locator}: stored text differs from source")
            if _sha(pv.text) != pv.text_sha256:
                problems.append(f"{pvs.source_locator}: stored text does not match its own hash")
        for para, work_uri in session.execute(
            select(m.JudgmentParagraph, m.Judgment.uri)
            .join(m.JudgmentVersion, m.JudgmentParagraph.judgment_version_id == m.JudgmentVersion.id)
            .join(m.Judgment, m.JudgmentVersion.judgment_id == m.Judgment.id)
            .where(m.JudgmentVersion.source_document_id == doc.id)
        ):
            checked += 1
            source_text = paras.get((work_uri, para.eid))
            if source_text is None or _sha(source_text) != para.text_sha256 or _sha(para.text) != para.text_sha256:
                problems.append(f"{para.eid}: paragraph text differs from source")
    result = "pass" if not problems else "fail"
    session.add(
        m.VerificationRecord(
            subject_kind="source_document",
            subject_id=str(doc.id),
            check="source_integrity",
            result=result,
            details={"checked_units": checked, "problems": problems},
            checker=checker,
        )
    )
    session.flush()
    return {
        "document_id": str(doc.id),
        "external_id": doc.external_id,
        "sha256": doc.sha256,
        "result": result,
        "checked_units": checked,
        "problems": problems,
    }
