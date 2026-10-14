"""Ingestion pipeline: licence gate -> fetch -> store raw -> parse -> validate -> persist -> index.

Properties the pipeline guarantees:

* **Licence-gated.** A source whose licence has not been approved is refused before any fetch.
* **Idempotent.** Raw documents are keyed by SHA-256; re-running an ingestion adds nothing.
* **Non-destructive.** Existing versions are never overwritten. A different text for an
  already-recorded version is a *conflict*: it is recorded as a failed verification and
  skipped, for a human to resolve.
* **Traceable.** Every stored text points to the raw source document and the locator (eId)
  it was extracted from; each run is recorded with its statistics and errors.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.retrieval.indexer import rebuild_passages
from ulis.settings import REPO_ROOT
from ulis.sources.connectors.base import Connector, RawDocument
from ulis.sources.connectors.local_manifest import LocalManifestConnector
from ulis.sources.normalized import ParsedDocument, ParsedProvision, ParsedRef
from ulis.sources.parsers.akn import PARSER_VERSION, parse_akn
from ulis.sources.registry import SourceDescriptor, licence_gate
from ulis.storage.object_store import ObjectStore
from ulis.temporal.validity import Span, derive_valid_to

PARSERS: dict[str, Callable[[bytes], ParsedDocument]] = {"akn3": parse_akn}


class ValidationError(ValueError):
    pass


@dataclass
class IngestionReport:
    run_id: str
    source_id: str
    documents_seen: int = 0
    documents_new: int = 0
    documents_unchanged: int = 0
    instruments_created: int = 0
    instrument_versions_created: int = 0
    provision_versions_created: int = 0
    provision_versions_corroborated: int = 0
    judgments_created: int = 0
    paragraphs_created: int = 0
    citation_links_created: int = 0
    citation_links_resolved: int = 0
    passages_indexed: int = 0
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_connector(source: SourceDescriptor, base_dir: Path = REPO_ROOT) -> Connector:
    if source.connector == "local_manifest":
        if not source.access.endpoint:
            raise ValueError(f"source {source.id} has no manifest endpoint")
        return LocalManifestConnector(base_dir / source.access.endpoint, source.id)
    raise ValueError(f"no connector implementation named {source.connector!r}")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Ingestor:
    def __init__(self, session: Session, catalog: Catalog, store: ObjectStore, base_dir: Path = REPO_ROOT):
        self.session = session
        self.catalog = catalog
        self.store = store
        self.base_dir = base_dir

    # ------------------------------------------------------------------ entry point
    def ingest_source(self, source_id: str, actor: str, connector: Connector | None = None) -> IngestionReport:
        source = self.catalog.sources.get(source_id)
        licence_gate(source)  # raises LicenceNotApproved
        parser = PARSERS[source.parser or ""]
        connector = connector or build_connector(source, self.base_dir)

        run = m.IngestionRun(source_id=source_id, actor=actor, status="running")
        self.session.add(run)
        self.session.flush()
        report = IngestionReport(run_id=str(run.id), source_id=source_id)
        touched_instruments: set[Any] = set()
        touched_judgments: set[Any] = set()

        for raw in connector.fetch():
            report.documents_seen += 1
            sha = hashlib.sha256(raw.content).hexdigest()
            existing = self.session.scalar(
                select(m.SourceDocument).where(m.SourceDocument.source_id == source_id, m.SourceDocument.sha256 == sha)
            )
            if existing is not None:
                report.documents_unchanged += 1
                continue
            try:
                with self.session.begin_nested():
                    doc_row = self._store_raw(source, raw, sha)
                    parsed = parser(raw.content)
                    self._validate(parsed)
                    if parsed.doc_kind == "judgment":
                        touched_judgments.add(self._persist_judgment(parsed, doc_row, report))
                    else:
                        touched_instruments.add(self._persist_legislation(parsed, doc_row, report))
                    self._verify(doc_row, "parse", "pass", {"parser": source.parser, "parser_version": PARSER_VERSION})
                report.documents_new += 1
            except Exception as exc:  # recorded, then the run continues with the next document
                report.errors.append(f"{raw.external_id}: {exc}")
                self.session.add(
                    m.VerificationRecord(
                        subject_kind="raw_document",
                        subject_id=raw.external_id,
                        check="parse",
                        result="fail",
                        details={"error": str(exc)},
                        checker="pipeline",
                    )
                )

        self.session.flush()
        for instrument_id in touched_instruments:
            self._recompute_validity(instrument_id)
        report.citation_links_resolved = self.resolve_citations()
        report.passages_indexed = rebuild_passages(
            self.session, self.catalog, instrument_ids=touched_instruments, judgment_ids=touched_judgments
        )
        run.status = "completed_with_errors" if report.errors else "completed"
        run.finished_at = datetime.now(UTC)
        run.stats = {k: v for k, v in report.as_dict().items() if isinstance(v, int)}
        run.errors = report.errors + report.conflicts
        self.session.add(
            m.AuditEvent(
                actor=actor,
                action="ingestion.run",
                subject=source_id,
                details={"run_id": str(run.id), "status": run.status},
            )
        )
        self.session.flush()
        return report

    # ------------------------------------------------------------------ steps
    def _store_raw(self, source: SourceDescriptor, raw: RawDocument, sha: str) -> m.SourceDocument:
        key = self.store.put(raw.content)
        row = m.SourceDocument(
            source_id=source.id,
            external_id=raw.external_id,
            uri=raw.uri,
            media_type=raw.media_type,
            byte_size=len(raw.content),
            sha256=sha,
            storage_key=key,
            retrieved_at=raw.retrieved_at,
            licence_id=source.licence.id,
            metadata_=dict(raw.metadata),
        )
        self.session.add(row)
        self.session.flush()
        self._verify(row, "source_integrity", "pass", {"sha256": sha, "storage_key": key})
        return row

    def _verify(self, doc: m.SourceDocument, check: str, result: str, details: dict[str, Any]) -> None:
        self.session.add(
            m.VerificationRecord(
                subject_kind="source_document",
                subject_id=str(doc.id),
                check=check,
                result=result,
                details=details,
                checker="pipeline",
            )
        )

    def _validate(self, parsed: ParsedDocument) -> None:
        reg = self.catalog.jurisdictions
        if parsed.jurisdiction_id not in reg:
            raise ValidationError(f"unknown jurisdiction {parsed.jurisdiction_id}")
        profile = reg.get(parsed.jurisdiction_id)
        if parsed.doc_kind == "judgment":
            assert parsed.judgment is not None
            court_jurisdiction = reg.court_jurisdiction(parsed.judgment.court_id)
            if court_jurisdiction not in reg.chain(parsed.jurisdiction_id) + reg.descendants(parsed.jurisdiction_id):
                raise ValidationError(f"court {parsed.judgment.court_id} is not in {parsed.jurisdiction_id}")
        elif parsed.instrument_type not in profile.instrument_types:
            raise ValidationError(
                f"instrument type {parsed.instrument_type!r} not declared for {parsed.jurisdiction_id}"
            )
        for j in parsed.extent + [j for p in parsed.provisions for j in p.extent]:
            if j not in reg:
                raise ValidationError(f"unknown jurisdiction in extent: {j}")
        for party in parsed.treaty_parties:
            if party.party not in reg:
                raise ValidationError(f"unknown treaty party {party.party}")
        module = self.catalog.module_for(parsed.jurisdiction_id)
        if parsed.ext_module and parsed.ext_module != module.id:
            raise ValidationError(f"ext module {parsed.ext_module} does not match profile module {module.id}")
        parsed.ext = module.validate_ext(parsed.ext)

    def _persist_legislation(self, parsed: ParsedDocument, doc: m.SourceDocument, report: IngestionReport) -> Any:
        s = self.session
        instrument = s.scalar(select(m.Instrument).where(m.Instrument.uri == parsed.work_uri))
        if instrument is None:
            instrument = m.Instrument(
                uri=parsed.work_uri,
                jurisdiction_id=parsed.jurisdiction_id,
                instrument_type=parsed.instrument_type,
                title=parsed.work_title,
                short_title=parsed.short_title,
                number=parsed.number,
                work_date=parsed.work_date,
                is_proposed=parsed.instrument_type == "bill",
                extent=parsed.extent,
                ext=parsed.ext,
            )
            s.add(instrument)
            s.flush()
            report.instruments_created += 1
        elif (instrument.jurisdiction_id, instrument.instrument_type) != (
            parsed.jurisdiction_id,
            parsed.instrument_type,
        ):
            raise ValidationError(f"{parsed.work_uri}: jurisdiction/type differ from the recorded work")

        if s.scalar(select(m.InstrumentVersion).where(m.InstrumentVersion.uri == parsed.expression_uri)):
            raise ValidationError(f"{parsed.expression_uri} already recorded from different source bytes")
        version = m.InstrumentVersion(
            instrument_id=instrument.id,
            uri=parsed.expression_uri,
            language=parsed.language,
            title=parsed.title,
            authoritative=parsed.authoritative,
            text_origin=parsed.text_origin,
            consolidation_kind=parsed.consolidation_kind,
            version_date=parsed.version_date,
            currency_date=parsed.currency_date,
            outstanding_changes=parsed.outstanding_changes,
            source_document_id=doc.id,
        )
        s.add(version)
        s.flush()
        report.instrument_versions_created += 1

        latest_date = s.scalar(
            select(func.max(m.InstrumentVersion.version_date)).where(m.InstrumentVersion.instrument_id == instrument.id)
        )
        is_latest = latest_date is None or parsed.version_date >= latest_date
        ids_by_eid: dict[str, Any] = {}
        for prov in parsed.provisions:
            ids_by_eid[prov.eid] = self._persist_provision(
                instrument, version, prov, ids_by_eid, doc, report, is_latest
            )

        for ev in parsed.events:
            duplicate = s.scalar(
                select(m.LifecycleEvent).where(
                    m.LifecycleEvent.instrument_id == instrument.id,
                    m.LifecycleEvent.event_type == ev.event_type,
                    m.LifecycleEvent.event_date == ev.date,
                    m.LifecycleEvent.affecting_uri.is_(None)
                    if ev.affecting_uri is None
                    else m.LifecycleEvent.affecting_uri == ev.affecting_uri,
                )
            )
            if duplicate is None:
                s.add(
                    m.LifecycleEvent(
                        instrument_id=instrument.id,
                        event_type=ev.event_type,
                        event_date=ev.date,
                        affecting_uri=ev.affecting_uri,
                        description=ev.description,
                        source_document_id=doc.id,
                        source_locator=ev.locator,
                    )
                )

        for party in parsed.treaty_parties:
            row = s.scalar(
                select(m.TreatyParty).where(
                    m.TreatyParty.treaty_id == instrument.id, m.TreatyParty.party_id == party.party
                )
            )
            values = dict(
                signed_on=party.signed_on,
                consent_type=party.consent_type,
                consented_on=party.consented_on,
                entry_into_force_on=party.entry_into_force_on,
                withdrawn_on=party.withdrawn_on,
                reservations=party.reservations,
            )
            if row is None:
                s.add(m.TreatyParty(treaty_id=instrument.id, party_id=party.party, source_document_id=doc.id, **values))
            elif any(getattr(row, k) != v for k, v in values.items()):
                report.conflicts.append(f"{parsed.expression_uri}: treaty status for {party.party} differs")
                self._verify(doc, "treaty_status_consistency", "fail", {"party": party.party})
        return instrument.id

    def _persist_provision(
        self,
        instrument: m.Instrument,
        version: m.InstrumentVersion,
        prov: ParsedProvision,
        ids_by_eid: dict[str, Any],
        doc: m.SourceDocument,
        report: IngestionReport,
        is_latest: bool,
    ) -> Any:
        s = self.session
        row = s.scalar(
            select(m.Provision).where(m.Provision.instrument_id == instrument.id, m.Provision.eid == prov.eid)
        )
        if row is not None and is_latest:
            # Document order follows the most recent expression (inserted provisions slot in).
            row.sort_order = prov.sort_order
        if row is None:
            row = m.Provision(
                instrument_id=instrument.id,
                eid=prov.eid,
                uri=f"{instrument.uri}/~{prov.eid}",
                kind=prov.kind,
                num_path=prov.num_path,
                top_kind=prov.top_kind,
                parent_id=ids_by_eid.get(prov.parent_eid) if prov.parent_eid else None,
                sort_order=prov.sort_order,
                extent=prov.extent,
                index_unit=prov.is_leaf or prov.has_own_content,
            )
            s.add(row)
            s.flush()

        text_sha = _sha(prov.text)
        existing = next(
            (
                pv
                for pv in s.scalars(
                    select(m.ProvisionVersion).where(
                        m.ProvisionVersion.provision_id == row.id,
                        m.ProvisionVersion.language == version.language,
                        m.ProvisionVersion.status == prov.status,
                    )
                )
                if pv.valid_from == prov.valid_from
            ),
            None,
        )
        if existing is not None:
            if existing.text_sha256 == text_sha:
                s.add(
                    m.ProvisionVersionSource(
                        provision_version_id=existing.id,
                        instrument_version_id=version.id,
                        source_document_id=doc.id,
                        source_locator=prov.eid,
                    )
                )
                report.provision_versions_corroborated += 1
            else:
                report.conflicts.append(
                    f"{row.uri} ({version.language}, from {prov.valid_from}): text differs between "
                    f"{existing.instrument_version_id} and {version.uri}"
                )
                self._verify(
                    doc,
                    "version_text_consistency",
                    "fail",
                    {"provision": row.uri, "existing_version": str(existing.id)},
                )
            return row.id

        pv = m.ProvisionVersion(
            provision_id=row.id,
            instrument_version_id=version.id,
            language=version.language,
            heading=prov.heading,
            text=prov.text,
            text_sha256=text_sha,
            status=prov.status,
            valid_from=prov.valid_from,
            valid_to=prov.valid_to,
            valid_to_basis="explicit" if prov.valid_to else "open",
            note=prov.note,
            source_document_id=doc.id,
            source_locator=prov.eid,
        )
        s.add(pv)
        s.flush()
        s.add(
            m.ProvisionVersionSource(
                provision_version_id=pv.id,
                instrument_version_id=version.id,
                source_document_id=doc.id,
                source_locator=prov.eid,
            )
        )
        report.provision_versions_created += 1
        for ref in prov.refs:
            self._add_link("provision_version", pv.id, instrument.uri, ref, doc)
            report.citation_links_created += 1
        return row.id

    def _persist_judgment(self, parsed: ParsedDocument, doc: m.SourceDocument, report: IngestionReport) -> Any:
        s = self.session
        meta = parsed.judgment
        assert meta is not None
        judgment = s.scalar(select(m.Judgment).where(m.Judgment.uri == parsed.work_uri))
        if judgment is None:
            judgment = m.Judgment(
                uri=parsed.work_uri,
                jurisdiction_id=parsed.jurisdiction_id,
                court_id=meta.court_id,
                case_name=meta.case_name,
                neutral_citation=meta.neutral_citation,
                decision_date=meta.decision_date,
                docket=meta.docket,
                ext=parsed.ext,
            )
            s.add(judgment)
            s.flush()
            report.judgments_created += 1
        if s.scalar(select(m.JudgmentVersion).where(m.JudgmentVersion.uri == parsed.expression_uri)):
            raise ValidationError(f"{parsed.expression_uri} already recorded from different source bytes")
        version = m.JudgmentVersion(
            judgment_id=judgment.id,
            uri=parsed.expression_uri,
            language=parsed.language,
            authoritative=parsed.authoritative,
            text_origin=parsed.text_origin,
            version_date=parsed.version_date,
            source_document_id=doc.id,
        )
        s.add(version)
        s.flush()
        for para in parsed.paragraphs:
            prow = m.JudgmentParagraph(
                judgment_version_id=version.id,
                eid=para.eid,
                num=para.num,
                part=para.part,
                text=para.text,
                text_sha256=_sha(para.text),
                sort_order=para.sort_order,
            )
            s.add(prow)
            s.flush()
            report.paragraphs_created += 1
            for ref in para.refs:
                self._add_link("judgment_paragraph", prow.id, judgment.uri, ref, doc)
                report.citation_links_created += 1
        return judgment.id

    def _add_link(
        self, from_kind: str, from_id: Any, from_work_uri: str, ref: ParsedRef, doc: m.SourceDocument
    ) -> None:
        self.session.add(
            m.CitationLink(
                from_kind=from_kind,
                from_id=from_id,
                from_work_uri=from_work_uri,
                to_uri=ref.href,
                relation=ref.relation,
                citation_text=ref.text,
                extraction_method="source_markup",
                source_document_id=doc.id,
            )
        )

    # ------------------------------------------------------------------ post-processing
    def _recompute_validity(self, instrument_id: Any) -> None:
        s = self.session
        versions = s.scalars(
            select(m.ProvisionVersion).join(m.Provision).where(m.Provision.instrument_id == instrument_id)
        ).all()
        groups: dict[tuple[Any, str], list[m.ProvisionVersion]] = {}
        for pv in versions:
            groups.setdefault((pv.provision_id, pv.language), []).append(pv)
        for group in groups.values():
            spans = [
                Span(pv.id, pv.status, pv.valid_from, pv.valid_to, pv.valid_to_basis == "explicit") for pv in group
            ]
            derived = derive_valid_to(spans)
            for pv in group:
                valid_to, basis = derived[pv.id]
                if pv.status in ("prospective", "proposed"):
                    valid_to, basis = None, "open"
                pv.valid_to, pv.valid_to_basis = valid_to, basis
        s.flush()

    def resolve_citations(self) -> int:
        s = self.session
        resolved = 0
        for link in s.scalars(
            select(m.CitationLink).where(
                m.CitationLink.to_instrument_id.is_(None), m.CitationLink.to_judgment_id.is_(None)
            )
        ):
            work_uri, _, eid = link.to_uri.partition("/~")
            instrument = s.scalar(select(m.Instrument).where(m.Instrument.uri == work_uri))
            if instrument is not None:
                link.to_instrument_id = instrument.id
                if eid:
                    link.to_provision_id = s.scalar(
                        select(m.Provision.id).where(m.Provision.instrument_id == instrument.id, m.Provision.eid == eid)
                    )
                resolved += 1
                continue
            judgment_id = s.scalar(select(m.Judgment.id).where(m.Judgment.uri == work_uri))
            if judgment_id is not None:
                link.to_judgment_id = judgment_id
                resolved += 1
        s.flush()
        return resolved
