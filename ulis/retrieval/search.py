"""Jurisdiction-aware hybrid retrieval (Phase 2 slice).

Implemented stages (numbering follows docs/multilingual-retrieval.md):
  1. structured jurisdiction / extent / treaty-party / temporal / status filters
  2. exact and stemmed keyword search (PostgreSQL full text, per-language configuration)
  4. provision-level units with stable identifiers and pinpoints
  5. citation-graph data attached to each hit (treatment, amendments)
  6. amendment relationships (legislative treatment, pending bills)
  9. authority-sensitive reranking, with every score component returned
 10. source, currency and coverage limitations on every hit
Cross-lingual recall uses curated concept labels (reported as query expansions), not
machine translation. Stages 3, 7 and 8 (embeddings, factual-pattern and issue matching)
are Phase 3-4 work.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.jurisdictions.modules import ProvisionCitationInput
from ulis.languages import FALLBACK_CONFIG, all_search_configs, segment_for_search
from ulis.retrieval.authority import Authority, TreatyStatus, classify_judgment, classify_provision
from ulis.retrieval.coverage import coverage_for
from ulis.retrieval.details import load_judgment_paragraph, load_provision
from ulis.verification.limitations import (
    Limitation,
    judgment_limitations,
    jurisdiction_limitations,
    provision_limitations,
)

DISCLAIMER = (
    "Research aid only - not legal advice. Results are limited to the indexed corpus; verify every "
    "authority against the official source before relying on it."
)
EXPANSION_PENALTY = 0.8
# ts_rank_cd normalisation: 1 = divide by 1 + log(length), so one very long passage (e.g. a section
# enacting a whole code title) does not outrank focused provisions; 32 = scale into [0, 1).
RANK_NORMALIZATION = 1 | 32
# Passages longer than this are down-weighted: they are imprecise pinpoints (e.g. a section
# that enacts a whole code title) and match almost any query.
LENGTH_PENALTY_START = 5000


def length_factor(length: int) -> float:
    return 1.0 if length <= LENGTH_PENALTY_START else (LENGTH_PENALTY_START / length) ** 0.25


# Keeps one long judgment from crowding out other authorities.
MAX_PARAGRAPHS_PER_JUDGMENT = 2
_LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


class SearchError(ValueError):
    pass


@dataclass
class SearchRequest:
    q: str
    jurisdiction: str | None = None
    as_of: date | None = None
    languages: list[str] = field(default_factory=list)
    include_foreign: bool = False
    include_proposed: bool = False
    include_historical: bool = False
    include_prospective: bool = False
    include_case_law: bool = True
    forum_court: str | None = None
    limit: int = 10
    # Language the question is written in (BCP 47). Restricts concept-label matching; None = any.
    query_language: str | None = None


@dataclass
class _Candidate:
    passage_id: uuid.UUID
    kind: str
    provision_version_id: uuid.UUID | None
    judgment_paragraph_id: uuid.UUID | None
    instrument_id: uuid.UUID | None
    judgment_id: uuid.UUID | None
    jurisdiction_id: str
    doc_type: str
    language: str
    status: str
    valid_from: date | None
    valid_to: date | None
    lexical: float
    direct: bool
    text_length: int
    authority: Authority | None = None
    final: float = 0.0
    group_key: tuple[Any, ...] = ()


def _validate(catalog: Catalog, req: SearchRequest) -> None:
    if not req.q or not req.q.strip():
        raise SearchError("q must not be empty")
    if len(req.q) > 500:
        raise SearchError("q is limited to 500 characters")
    if not 1 <= req.limit <= 50:
        raise SearchError("limit must be between 1 and 50")
    reg = catalog.jurisdictions
    if req.jurisdiction is not None and req.jurisdiction not in reg:
        raise SearchError(f"unknown jurisdiction {req.jurisdiction!r}")
    if req.forum_court is not None:
        if req.forum_court not in reg.courts():
            raise SearchError(f"unknown court {req.forum_court!r}")
        if req.jurisdiction and reg.sovereign(reg.court_jurisdiction(req.forum_court)) != reg.sovereign(
            req.jurisdiction
        ):
            raise SearchError("forum_court must belong to the target jurisdiction's legal system")
    for lang in [*req.languages, *([req.query_language] if req.query_language else [])]:
        if not _LANG_RE.match(lang):
            raise SearchError(f"invalid language code {lang!r}")


def _expansions(
    session: Session, q: str, query_language: str | None
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    # With a known query language, labels are matched only in that language: otherwise a word
    # that exists in two languages with different meanings (English "conservation" vs French
    # "conservation" = retention) would trigger a false expansion.
    matched = (
        session.execute(
            text(
                "SELECT DISTINCT c.key FROM concept_labels l JOIN legal_concepts c ON c.id = l.concept_id "
                "WHERE to_tsvector(l.search_config, :q) @@ phraseto_tsquery(l.search_config, l.label) "
                "AND (CAST(:lang AS text) IS NULL OR l.language = CAST(:lang AS text))"
            ),
            {"q": q, "lang": query_language},
        )
        .scalars()
        .all()
    )
    if not matched:
        return [], {}
    rows = session.execute(
        text(
            "SELECT c.key, l.language, l.label, l.search_config::text FROM concept_labels l "
            "JOIN legal_concepts c ON c.id = l.concept_id WHERE c.key = ANY(CAST(:keys AS text[])) "
            "ORDER BY c.key, l.language, l.label"
        ),
        {"keys": list(matched)},
    ).all()
    report: dict[str, dict[str, list[str]]] = {}
    by_config: dict[str, list[str]] = {}
    for key, language, label, config in rows:
        report.setdefault(key, {}).setdefault(language, []).append(label)
        by_config.setdefault(config, []).append(label)
    return [{"concept": k, "labels": v} for k, v in report.items()], by_config


def _scope(session: Session, catalog: Catalog, target: str | None, as_of: date) -> dict[str, Any]:
    reg = catalog.jurisdictions
    if target is None:
        return {"target": None, "chain": [], "case_law": [], "treaty_ids": [], "treaties": [], "regional": []}
    chain = reg.chain(target)
    sovereign = reg.sovereign(target)
    case_law = [sovereign, *reg.descendants(sovereign)] if sovereign else chain
    treaties = (
        session.execute(
            select(m.Instrument.id, m.Instrument.title)
            .join(m.TreatyParty, m.TreatyParty.treaty_id == m.Instrument.id)
            .where(m.TreatyParty.party_id == sovereign)
        ).all()
        if sovereign
        else []
    )
    return {
        "target": target,
        "chain": chain,
        "case_law": case_law,
        "treaty_ids": [t[0] for t in treaties],
        "treaties": [t[1] for t in treaties],
        # Law and courts of regional organisations the State is a member of on the date.
        "regional": reg.organisations_on(target, as_of),
    }


def _candidates(
    session: Session,
    req: SearchRequest,
    as_of: date,
    scope: dict[str, Any],
    expansions: dict[str, list[str]],
    budget: int,
) -> list[_Candidate]:
    params: dict[str, Any] = {"q": req.q, "as_of": as_of, "budget": budget}
    match_parts, rank_parts, direct_parts = [], [], []
    segmented = segment_for_search(req.q)
    if segmented is not None:
        params["q_segmented"] = segmented
    for config in all_search_configs():
        q_param = "q_segmented" if segmented is not None and config == FALLBACK_CONFIG else "q"
        tsq = f"websearch_to_tsquery(CAST('{config}' AS regconfig), :{q_param})"
        expanded = tsq
        for i, label in enumerate(expansions.get(config, [])):
            name = f"x_{config}_{i}"
            params[name] = label
            expanded += f" || phraseto_tsquery(CAST('{config}' AS regconfig), :{name})"
        is_cfg = f"p.search_config = CAST('{config}' AS regconfig)"
        match_parts.append(f"({is_cfg} AND p.tsv @@ ({expanded}))")
        rank_parts.append(f"WHEN {is_cfg} THEN ts_rank_cd(p.tsv, ({expanded}), {RANK_NORMALIZATION})")
        direct_parts.append(f"WHEN {is_cfg} THEN p.tsv @@ {tsq}")

    filters = [
        "("
        " p.kind = 'judgment_paragraph'"
        " OR (p.status = 'in_force' AND (p.valid_from IS NULL OR :hist"
        "     OR (p.valid_from <= :as_of AND (p.valid_to IS NULL OR p.valid_to > :as_of))))"
        " OR (p.status = 'prospective' AND :prosp)"
        " OR (p.status = 'proposed' AND :proposed AND p.valid_from <= :as_of"
        "     AND (p.valid_to IS NULL OR p.valid_to > :as_of))"
        ")",
        "(:case_law OR p.kind <> 'judgment_paragraph')",
    ]
    params.update(
        hist=req.include_historical,
        prosp=req.include_prospective,
        proposed=req.include_proposed,
        case_law=req.include_case_law,
    )
    if req.languages:
        filters.append("p.language = ANY(CAST(:langs AS text[]))")
        params["langs"] = req.languages
    if scope["target"] is not None and not req.include_foreign:
        filters.append(
            "("
            " (p.kind = 'provision' AND p.doc_type <> 'treaty' AND p.jurisdiction_id = ANY(CAST(:chain AS text[]))"
            "  AND (cardinality(p.extent) = 0 OR CAST(p.extent AS text[]) && CAST(:chain AS text[])))"
            " OR (p.kind = 'provision' AND p.instrument_id = ANY(CAST(:treaty_ids AS uuid[])))"
            " OR (p.kind = 'judgment_paragraph' AND p.jurisdiction_id = ANY(CAST(:system AS text[])))"
            " OR (p.doc_type <> 'treaty' AND p.jurisdiction_id = ANY(CAST(:regional AS text[])))"
            ")"
        )
        params.update(
            chain=scope["chain"], treaty_ids=scope["treaty_ids"], system=scope["case_law"], regional=scope["regional"]
        )

    sql = (
        "SELECT p.id, p.kind, p.provision_version_id, p.judgment_paragraph_id, p.instrument_id, p.judgment_id,"
        " p.jurisdiction_id, p.doc_type, p.language, p.status, p.valid_from, p.valid_to,"
        f" CASE {' '.join(rank_parts)} ELSE 0 END AS lexical,"
        f" CASE {' '.join(direct_parts)} ELSE false END AS direct, length(p.text) AS text_length"
        f" FROM passages p WHERE ({' OR '.join(match_parts)}) AND {' AND '.join(filters)}"
        " ORDER BY lexical DESC, p.id LIMIT :budget"
    )
    return [_Candidate(*row) for row in session.execute(text(sql), params).all()]


def _classify(
    session: Session, catalog: Catalog, req: SearchRequest, as_of: date, candidates: list[_Candidate]
) -> None:
    reg = catalog.jurisdictions
    treaty_ids = {c.instrument_id for c in candidates if c.doc_type == "treaty"}
    parties: dict[Any, list[TreatyStatus]] = {}
    if treaty_ids:
        for tp in session.scalars(select(m.TreatyParty).where(m.TreatyParty.treaty_id.in_(treaty_ids))):
            parties.setdefault(tp.treaty_id, []).append(
                TreatyStatus(tp.party_id, tp.signed_on, tp.consented_on, tp.entry_into_force_on, tp.withdrawn_on)
            )
    implementing: dict[Any, list[str]] = {}
    for tid in treaty_ids:
        implementing[tid] = sorted(
            set(
                session.scalars(
                    select(m.Instrument.title)
                    .join(m.Provision, m.Provision.instrument_id == m.Instrument.id)
                    .join(m.ProvisionVersion, m.ProvisionVersion.provision_id == m.Provision.id)
                    .join(
                        m.CitationLink,
                        (m.CitationLink.from_id == m.ProvisionVersion.id)
                        & (m.CitationLink.from_kind == "provision_version"),
                    )
                    .where(m.CitationLink.to_instrument_id == tid, m.CitationLink.relation == "implements")
                )
            )
        )
    judgment_ids = {c.judgment_id for c in candidates if c.judgment_id}
    courts: dict[Any, str] = {}
    overruled: dict[Any, str] = {}
    if judgment_ids:
        courts = dict(
            session.execute(select(m.Judgment.id, m.Judgment.court_id).where(m.Judgment.id.in_(judgment_ids))).all()
        )
        for link in session.scalars(
            select(m.CitationLink).where(
                m.CitationLink.to_judgment_id.in_(judgment_ids), m.CitationLink.relation == "overrules"
            )
        ):
            citing = session.scalar(select(m.Judgment.neutral_citation).where(m.Judgment.uri == link.from_work_uri))
            overruled[link.to_judgment_id] = f"{citing or link.from_work_uri}"
    pv_rows = {}
    pv_ids = [c.provision_version_id for c in candidates if c.provision_version_id]
    if pv_ids:
        pv_rows = {
            pid: (prov_id, vf)
            for pid, prov_id, vf in session.execute(
                select(m.ProvisionVersion.id, m.ProvisionVersion.provision_id, m.ProvisionVersion.valid_from).where(
                    m.ProvisionVersion.id.in_(pv_ids)
                )
            )
        }
    para_rows = {}
    para_ids = [c.judgment_paragraph_id for c in candidates if c.judgment_paragraph_id]
    if para_ids:
        para_rows = {
            pid: eid
            for pid, eid in session.execute(
                select(m.JudgmentParagraph.id, m.JudgmentParagraph.eid).where(m.JudgmentParagraph.id.in_(para_ids))
            )
        }

    for c in candidates:
        if c.kind == "judgment_paragraph":
            assert c.judgment_id is not None
            c.authority = classify_judgment(
                reg,
                target=req.jurisdiction,
                judgment_jurisdiction=c.jurisdiction_id,
                court_id=courts[c.judgment_id],
                forum_court=req.forum_court,
                overruled_by=overruled.get(c.judgment_id),
                as_of=as_of,
            )
            c.group_key = (
                "j",
                c.judgment_id,
                para_rows.get(c.judgment_paragraph_id) if c.judgment_paragraph_id else None,
            )
        else:
            applies: bool | None
            if c.status == "in_force" and c.valid_from is None:
                applies = None  # attested only at the source's edition date
            else:
                applies = (
                    c.status == "in_force"
                    and c.valid_from is not None
                    and c.valid_from <= as_of
                    and (c.valid_to is None or as_of < c.valid_to)
                )
            c.authority = classify_provision(
                reg,
                target=req.jurisdiction,
                passage_jurisdiction=c.jurisdiction_id,
                doc_type=c.doc_type,
                status=c.status,
                applies_on_date=applies,
                as_of=as_of,
                treaty_parties=parties.get(c.instrument_id),
                implementing_titles=implementing.get(c.instrument_id),
            )
            assert c.provision_version_id is not None
            prov_id, vf = pv_rows[c.provision_version_id]
            c.group_key = ("p", prov_id, vf, c.status)
        c.final = round(
            float(c.lexical)
            * c.authority.weight
            * (1.0 if c.direct else EXPANSION_PENALTY)
            * length_factor(c.text_length),
            6,
        )


def search(
    session: Session, catalog: Catalog, req: SearchRequest, *, today: date, candidate_budget: int = 200
) -> dict[str, Any]:
    _validate(catalog, req)
    as_of = req.as_of or today
    expansion_report, expansions = _expansions(session, req.q, req.query_language)
    scope = _scope(session, catalog, req.jurisdiction, as_of)
    candidates = _candidates(session, req, as_of, scope, expansions, candidate_budget)
    _classify(session, catalog, req, as_of, candidates)
    candidates.sort(key=lambda c: (-c.final, str(c.passage_id)))

    # One hit per provision/paragraph: among parallel language versions prefer a direct match,
    # then a language of the target jurisdiction, then the higher score.
    target_langs = set(catalog.jurisdictions.get(req.jurisdiction).official_languages) if req.jurisdiction else set()

    def preference(c: _Candidate) -> tuple[bool, bool, float]:
        return (c.direct, c.language in target_langs, c.final)

    best: dict[tuple[Any, ...], _Candidate] = {}
    for c in candidates:
        if c.group_key not in best or preference(c) > preference(best[c.group_key]):
            best[c.group_key] = c
    ranked: list[_Candidate] = []
    per_judgment: Counter[Any] = Counter()
    for c in sorted(best.values(), key=lambda c: (-c.final, str(c.passage_id))):
        if c.judgment_id is not None:
            if per_judgment[c.judgment_id] >= MAX_PARAGRAPHS_PER_JUDGMENT:
                continue
            per_judgment[c.judgment_id] += 1
        ranked.append(c)
        if len(ranked) == req.limit:
            break

    results = []
    for rank, c in enumerate(ranked, start=1):
        assert c.authority is not None
        if c.kind == "judgment_paragraph":
            assert c.judgment_paragraph_id is not None
            results.append(_judgment_hit(session, catalog, c, rank, as_of))
        else:
            assert c.provision_version_id is not None
            results.append(_provision_hit(session, catalog, c, rank, as_of))

    involved = sorted({r["jurisdiction"]["id"] for r in results} | ({req.jurisdiction} if req.jurisdiction else set()))
    global_limits: list[Limitation] = [Limitation("NOT_LEGAL_ADVICE", "info", DISCLAIMER)]
    clarifications: list[dict[str, Any]] = []
    if req.jurisdiction is None:
        global_limits.append(
            Limitation(
                "JURISDICTION_NOT_SPECIFIED",
                "warning",
                "No jurisdiction was given. Results from different jurisdictions are "
                "listed together but are not interchangeable.",
            )
        )
        clarifications.append(
            {
                "field": "jurisdiction",
                "reason": "Identify the jurisdiction whose law applies.",
                "options": sorted({r["jurisdiction"]["id"] for r in results}),
            }
        )
    else:
        global_limits.extend(jurisdiction_limitations(catalog, req.jurisdiction))
        profile = catalog.jurisdictions.get(req.jurisdiction)
        if profile.coverage.corpus_status == "none":
            global_limits.append(
                Limitation(
                    "NO_CORPUS",
                    "critical",
                    f"No legal materials for {req.jurisdiction} are indexed. The absence of results says nothing "
                    f"about {req.jurisdiction} law.",
                )
            )
        sub = [
            d
            for d in catalog.jurisdictions.descendants(req.jurisdiction)
            if catalog.jurisdictions.get(d).coverage.corpus_status != "none"
        ]
        if sub:
            global_limits.append(
                Limitation(
                    "SUBNATIONAL_LEGISLATION_EXCLUDED",
                    "info",
                    f"Legislation of {', '.join(sub)} applies only within those units and is excluded; "
                    "search a constituent jurisdiction to include it.",
                )
            )
    if req.jurisdiction is not None and scope["chain"]:
        gap = session.execute(
            text(
                "SELECT count(*), min(h.start) FROM (SELECT i.id, min(iv.version_date) AS start FROM instruments i "
                "JOIN instrument_versions iv ON iv.instrument_id = i.id "
                "WHERE i.jurisdiction_id = ANY(CAST(:chain AS text[])) "
                "AND (i.work_date IS NULL OR i.work_date <= :as_of) "
                "AND NOT i.is_proposed GROUP BY i.id) h WHERE h.start > :as_of"
            ),
            {"chain": scope["chain"], "as_of": as_of},
        ).one()
        if gap[0]:
            global_limits.append(
                Limitation(
                    "HISTORY_INCOMPLETE",
                    "warning",
                    f"For {gap[0]} instrument(s) in scope, indexed versions start after {as_of} (earliest "
                    f"{gap[1]}); text that applied on {as_of} may be missing from these results.",
                )
            )
    if req.as_of is None:
        global_limits.append(
            Limitation("DATE_DEFAULTED", "info", f"No date was given; results reflect the law as indexed for {as_of}.")
        )
    if expansion_report:
        global_limits.append(
            Limitation(
                "QUERY_EXPANDED",
                "info",
                "The query was expanded with curated concept labels in other languages "
                f"({', '.join(e['concept'] for e in expansion_report)}). Shared vocabulary does not imply that "
                "provisions have equivalent legal effect.",
            )
        )
    if any(r["authority"]["category"] == "foreign" for r in results):
        global_limits.append(
            Limitation(
                "FOREIGN_MATERIAL_INCLUDED",
                "warning",
                "Foreign material is included for comparison only; it is not binding.",
            )
        )

    return {
        "query": {**asdict(req), "as_of": as_of.isoformat(), "as_of_defaulted": req.as_of is None},
        "scope": {k: v for k, v in scope.items() if k != "treaty_ids"} | {"foreign_included": req.include_foreign},
        "clarifications": clarifications,
        "query_expansions": expansion_report,
        "total_candidates": len(candidates),
        "results": results,
        "coverage": coverage_for(session, catalog, involved),
        "limitations": [lim.as_dict() for lim in global_limits],
        "disclaimer": DISCLAIMER,
    }


def _jurisdiction_info(catalog: Catalog, jid: str) -> dict[str, Any]:
    p = catalog.jurisdictions.get(jid)
    return {"id": jid, "name": p.name, "fictional": p.fictional, "verification": p.verification.status}


def _authority_dict(a: Authority) -> dict[str, Any]:
    return {"category": a.category, "label": a.label, "rationale": a.rationale, "weight": a.weight}


def _score(c: _Candidate) -> dict[str, Any]:
    assert c.authority is not None
    return {
        "final": c.final,
        "lexical": round(float(c.lexical), 6),
        "authority_weight": c.authority.weight,
        "length_factor": round(length_factor(c.text_length), 4),
        "matched_via": "query" if c.direct else "concept_expansion",
    }


def _source_dict(src: Any) -> dict[str, Any]:
    d = asdict(src)
    d["retrieved_at"] = src.retrieved_at.isoformat()
    return d


def _provision_hit(session: Session, catalog: Catalog, c: _Candidate, rank: int, as_of: date) -> dict[str, Any]:
    assert c.provision_version_id is not None and c.authority is not None
    data = load_provision(session, catalog, c.provision_version_id)
    pv, prov, inst, iv = data.version, data.provision, data.instrument, data.instrument_version
    module = catalog.module_for(inst.jurisdiction_id)
    pin = module.pinpoint(
        ProvisionCitationInput(
            iv.short_title or inst.short_title or inst.title,
            inst.instrument_type,
            inst.jurisdiction_id,
            prov.top_kind,
            list(prov.num_path),
            pv.language,
            ext=inst.ext or {},
            eid=prov.eid,
        )
    )
    return {
        "rank": rank,
        "passage_id": str(c.passage_id),
        "kind": "provision",
        "jurisdiction": _jurisdiction_info(catalog, inst.jurisdiction_id),
        "document": {
            "uri": inst.uri,
            "title": iv.title,
            "type": inst.instrument_type,
            "is_proposed": inst.is_proposed,
            "extent": list(inst.extent),
        },
        "unit": {"uri": prov.uri, "eid": prov.eid, "label": pin, "heading": pv.heading},
        "version": {
            "id": str(pv.id),
            "expression_uri": iv.uri,
            "language": pv.language,
            "status": pv.status,
            "valid_from": pv.valid_from.isoformat() if pv.valid_from else None,
            "valid_to": pv.valid_to.isoformat() if pv.valid_to else None,
            "valid_to_basis": pv.valid_to_basis,
            "valid_from_basis": pv.valid_from_basis,
            "authoritative": iv.authoritative,
            "text_origin": iv.text_origin,
            "consolidation": iv.consolidation_kind,
            "currency_date": data.currency_date.isoformat() if data.currency_date else None,
            "note": pv.note,
        },
        "text": pv.text,
        "text_sha256": pv.text_sha256,
        "citation": data.citation,
        "authority": _authority_dict(c.authority),
        "source": _source_dict(data.source),
        "score": _score(c),
        "parallel_versions": data.parallel_languages,
        "timeline": [
            {
                "id": t.id,
                "status": t.status,
                "valid_from": t.valid_from.isoformat() if t.valid_from else None,
                "valid_to": t.valid_to.isoformat() if t.valid_to else None,
                "valid_to_basis": t.valid_to_basis,
                "valid_from_basis": t.valid_from_basis,
                "current_hit": t.id == str(pv.id),
            }
            for t in data.timeline
        ],
        "treatment": {
            "judicial": [asdict(t) | {"date": t.date.isoformat() if t.date else None} for t in data.judicial_treatment],
            "legislative": [
                asdict(t) | {"date": t.date.isoformat() if t.date else None} for t in data.legislative_treatment
            ],
        },
        "limitations": [lim.as_dict() for lim in provision_limitations(catalog, data, c.authority, as_of)],
    }


def _judgment_hit(session: Session, catalog: Catalog, c: _Candidate, rank: int, as_of: date) -> dict[str, Any]:
    assert c.judgment_paragraph_id is not None and c.authority is not None
    data = load_judgment_paragraph(session, catalog, c.judgment_paragraph_id)
    j, para, ver = data.judgment, data.paragraph, data.version
    return {
        "rank": rank,
        "passage_id": str(c.passage_id),
        "kind": "judgment_paragraph",
        "jurisdiction": _jurisdiction_info(catalog, j.jurisdiction_id),
        "document": {
            "uri": j.uri,
            "title": j.case_name,
            "type": "judgment",
            "court": data.court_name,
            "court_id": j.court_id,
            "decision_date": j.decision_date.isoformat(),
            "neutral_citation": j.neutral_citation,
        },
        "unit": {"uri": f"{j.uri}/~{para.eid}", "eid": para.eid, "label": f"para {para.num}", "heading": para.part},
        "version": {
            "id": str(ver.id),
            "expression_uri": ver.uri,
            "language": ver.language,
            "authoritative": ver.authoritative,
            "text_origin": ver.text_origin,
        },
        "text": para.text,
        "text_sha256": para.text_sha256,
        "citation": data.citation,
        "authority": _authority_dict(c.authority),
        "source": _source_dict(data.source),
        "score": _score(c),
        "treatment": [asdict(t) | {"date": t.date.isoformat() if t.date else None} for t in data.treatment],
        "cites": data.cites,
        "limitations": [lim.as_dict() for lim in judgment_limitations(catalog, data, c.authority, as_of)],
    }
