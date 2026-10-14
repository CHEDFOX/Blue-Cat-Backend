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
        parser = PARSERS[catalog.sources.get(doc.source_id).parser or ""]
        parsed = parser(raw)
        by_eid = {p.eid: p for p in parsed.provisions}
        for pvs, pv in session.execute(
            select(m.ProvisionVersionSource, m.ProvisionVersion)
            .join(m.ProvisionVersion, m.ProvisionVersionSource.provision_version_id == m.ProvisionVersion.id)
            .where(m.ProvisionVersionSource.source_document_id == doc.id)
        ):
            checked += 1
            source_prov = by_eid.get(pvs.source_locator)
            if source_prov is None:
                problems.append(f"{pvs.source_locator}: not found in source")
            elif _sha(source_prov.text) != pv.text_sha256:
                problems.append(f"{pvs.source_locator}: stored text differs from source")
            if _sha(pv.text) != pv.text_sha256:
                problems.append(f"{pvs.source_locator}: stored text does not match its own hash")
        paras = {p.eid: p for p in parsed.paragraphs}
        for para in session.scalars(
            select(m.JudgmentParagraph).join(m.JudgmentVersion).where(m.JudgmentVersion.source_document_id == doc.id)
        ):
            checked += 1
            source_para = paras.get(para.eid)
            if source_para is None or _sha(source_para.text) != para.text_sha256 or _sha(para.text) != para.text_sha256:
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
