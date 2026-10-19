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

import itertools
import math
import re
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ulis.catalog import Catalog
from ulis.db import models as m
from ulis.jurisdictions.modules import ProvisionCitationInput
from ulis.languages import FALLBACK_CONFIG, all_search_configs, search_config, segment_for_search
from ulis.retrieval.authority import (
    AS_ENACTED_KINDS,
    Authority,
    TreatyStatus,
    classify_judgment,
    classify_provision,
)
from ulis.retrieval.citations import ResolvedCitation, parse_citation, resolve
from ulis.retrieval.coverage import coverage_for, database_key, indexed_languages
from ulis.retrieval.details import load_judgment_paragraph, load_provision
from ulis.retrieval.query import cjk_units, clean_query, has_cjk, parse_clauses
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
    tier: int = 0  # -1: the provision the query cites; 0: holds every term of the query; 1: most of them (relaxed)
    missing_terms: list[str] | None = None
    position: int = 0  # document order of a cited provision's units


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


def _cjk_strict_query(q: str, params: dict[str, Any]) -> str | None:
    """The CJK form of the full query, or None when the query has no CJK text.

    A natural question in an unspaced script rarely has every character pair of every word in one provision, so an
    AND of all pairs finds nothing. Each whitespace-separated term becomes a phrase of adjacent character pairs, and
    the terms are ORed: ranking then puts provisions with more terms first. ``-term`` excludes, as everywhere else.
    """
    if segment_for_search(q) is None:
        return None
    positive: list[str] = []
    negative: list[str] = []
    for clause in parse_clauses(q):
        inner = clause.text[1:-1] if clause.quoted else clause.text
        for alternative in re.split(r"\s+or\s+", inner, flags=re.IGNORECASE):
            name = f"q_seg_{len(positive) + len(negative)}"
            params[name] = segment_for_search(alternative) or alternative
            phrase = f"phraseto_tsquery(CAST('{FALLBACK_CONFIG}' AS regconfig), :{name})"
            (negative if clause.negated else positive).append(phrase)
    tsq = "(" + " || ".join(positive) + ")" if positive else ""
    if negative:
        excluded = "!!(" + " || ".join(negative) + ")"
        tsq = f"({tsq} && {excluded})" if tsq else excluded
    return tsq or None


def _filters(req: SearchRequest, scope: dict[str, Any], params: dict[str, Any]) -> list[str]:
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
        if not scope["treaty_ids"]:
            # Implied by the condition above when no treaty is in scope, and simple enough for the index on
            # jurisdiction_id: without it every passage of the language matching the words is fetched first.
            filters.append("p.jurisdiction_id = ANY(CAST(:in_scope AS text[]))")
            params["in_scope"] = sorted({*scope["chain"], *scope["case_law"], *scope["regional"]})
    return filters


_COLUMNS = (
    "p.id, p.kind, p.provision_version_id, p.judgment_paragraph_id, p.instrument_id, p.judgment_id,"
    " p.jurisdiction_id, p.doc_type, p.language, p.status, p.valid_from, p.valid_to"
)


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
    cjk = _cjk_strict_query(req.q, params)
    for config in all_search_configs():
        web = f"websearch_to_tsquery(CAST('{config}' AS regconfig), :q)"
        tsq = cjk if cjk and config == FALLBACK_CONFIG else web
        expanded = tsq
        for i, label in enumerate(expansions.get(config, [])):
            name = f"x_{config}_{i}"
            params[name] = label
            expanded += f" || phraseto_tsquery(CAST('{config}' AS regconfig), :{name})"
        is_cfg = f"p.search_config = CAST('{config}' AS regconfig)"
        match_parts.append(f"({is_cfg} AND p.tsv @@ ({expanded}))")
        rank_parts.append(f"WHEN {is_cfg} THEN ts_rank_cd(p.tsv, ({expanded}), {RANK_NORMALIZATION})")
        direct_parts.append(f"WHEN {is_cfg} THEN p.tsv @@ {tsq}")

    filters = _filters(req, scope, params)
    sql = (
        f"SELECT {_COLUMNS},"
        f" CASE {' '.join(rank_parts)} ELSE 0 END AS lexical,"
        f" CASE {' '.join(direct_parts)} ELSE false END AS direct, length(p.text) AS text_length"
        f" FROM passages p WHERE ({' OR '.join(match_parts)}) AND {' AND '.join(filters)}"
        " ORDER BY lexical DESC, p.id LIMIT :budget"
    )
    return [_Candidate(*row) for row in session.execute(text(sql), params).all()]


@dataclass
class _Term:
    label: str
    tsq: str  # tsquery text
    frequency: float  # share of passages holding it (estimate)

    @property
    def weight(self) -> float:
        return math.log(1 + 1 / self.frequency)


@dataclass
class _RelaxableQuery:
    """One search configuration's form of the query, split for relaxed matching."""

    optional: list[_Term]  # words and `a or b` groups: most of them must match
    required: list[str]  # quoted phrases: always required
    excluded: list[str]  # -terms: always excluded


_LEXEME = re.compile(r"'((?:[^']|'')*)'")


def _lexeme_frequencies(session: Session) -> tuple[dict[str, float], float]:
    """Share of passages holding each of the most common lexemes, from PostgreSQL's own column statistics
    (``ANALYZE``); lexemes not in the list are rarer than the least common one listed. Cached for ten minutes."""
    key = database_key(session)
    hit = _FREQUENCIES.get(key)
    if hit is not None and time.monotonic() - hit[0] < 600:
        return hit[1], hit[2]
    row = session.execute(
        text(
            "SELECT most_common_elems::text::text[], most_common_elem_freqs FROM pg_stats "
            "WHERE schemaname = current_schema() AND tablename = 'passages' AND attname = 'tsv'"
        )
    ).first()
    freqs: dict[str, float] = {}
    rarest = 1e-4
    if row is not None and row[0] and row[1]:
        # The frequencies are followed by the minimum, the maximum and the null frequency.
        freqs = {lexeme: float(f) for lexeme, f in zip(row[0], row[1], strict=False)}
        rarest = max(float(row[1][len(row[0])]) / 2, 1e-6)
    _FREQUENCIES[key] = (time.monotonic(), freqs, rarest)
    return freqs, rarest


_FREQUENCIES: dict[str, tuple[float, dict[str, float], float]] = {}


def _relaxable(session: Session, q: str, configs: list[str]) -> dict[str, _RelaxableQuery]:
    """Each clause of the query parsed by PostgreSQL on its own, per search configuration, in one round trip."""
    clauses = parse_clauses(q)
    cjk_units_ = cjk_units(" ".join(c.text for c in clauses if not c.negated and not c.quoted and has_cjk(c.text)))
    rows: list[tuple[str, str, str, str, str]] = []  # config, role, label, function, text
    for config in configs:
        for c in clauses:
            if has_cjk(c.text) and config != FALLBACK_CONFIG:
                continue  # an unspaced script is indexed only in the fallback configuration, as character pairs
            if config == FALLBACK_CONFIG and has_cjk(c.text):
                if c.negated or c.quoted:
                    role = "excluded" if c.negated else "required"
                    inner = c.text[1:-1] if c.quoted else c.text
                    rows.append((config, role, c.label, "phrase", segment_for_search(inner) or inner))
                continue
            role = "excluded" if c.negated else "required" if c.quoted else "optional"
            rows.append((config, role, c.label, "web", c.text))
        if config == FALLBACK_CONFIG:
            rows.extend((config, "optional", word, "phrase", unit) for unit, word in cjk_units_)
    if not rows:
        return {}
    parsed = session.execute(
        text(
            "SELECT CASE WHEN u.fn = 'web' THEN websearch_to_tsquery(CAST(u.cfg AS regconfig), u.t)"
            " ELSE phraseto_tsquery(CAST(u.cfg AS regconfig), u.t) END::text, u.ord "
            "FROM unnest(CAST(:cfgs AS text[]), CAST(:fns AS text[]), CAST(:texts AS text[])) "
            "WITH ORDINALITY AS u(cfg, fn, t, ord) ORDER BY u.ord"
        ),
        {"cfgs": [r[0] for r in rows], "fns": [r[3] for r in rows], "texts": [r[4] for r in rows]},
    ).all()
    freqs, rarest = _lexeme_frequencies(session)
    out: dict[str, _RelaxableQuery] = {}
    for (config, role, label, _, _), (tsq, _) in zip(rows, parsed, strict=True):
        if not tsq:  # only stopwords
            continue
        rq = out.setdefault(config, _RelaxableQuery([], [], []))
        if role == "excluded":
            rq.excluded.append(tsq)
        elif role == "required":
            rq.required.append(tsq)
        elif all(t.tsq != tsq for t in rq.optional):
            lexemes = [lex.replace("''", "'") for lex in _LEXEME.findall(tsq)]
            f = [freqs.get(lex, rarest) for lex in lexemes] or [rarest]
            rq.optional.append(_Term(label, tsq, min(1.0, sum(f)) if "|" in tsq else min(f)))
    return out


# Terms in more passages than this barely narrow a search (in the US eCFR: "require" 25%, "section" 21%,
# "may" 18%): when relaxing, they only rank.
COMMON_TERM_FREQUENCY = 0.1
# At most this many of the rarer terms are used to select passages when relaxing.
MAX_RELAXED_TERMS = 8
# Combinations times terms times the share of passages holding any of the terms, above which a loose form is tried
# only when nothing was found (measured: about 40 for "any 4 of 8" common English words, 2 s on the dev database).
MAX_LOOSE_COST = 15.0


def _levels(rq: _RelaxableQuery) -> list[tuple[str, bool]]:
    """The relaxed forms of one configuration's query, from strictest to loosest."""
    terms = sorted(rq.optional, key=lambda t: t.frequency)
    selective = [t for t in terms if t.frequency <= COMMON_TERM_FREQUENCY]
    if len(selective) < 2:  # nothing left to relax among the rare terms (one may be misspelt): relax all of them
        selective = terms
    selective = selective[:MAX_RELAXED_TERMS]
    n = len(selective)
    dropped = len(selective) < len(terms)
    fixed = [*(f"( {t} )" for t in rq.required), *(f"!( {t} )" for t in rq.excluded)]
    levels = []
    # Down to half of the terms. Below two thirds, a form is marked costly when PostgreSQL would check many
    # combinations over long posting lists ("any 4 of 8" common English words: 70 combinations per candidate passage
    # among hundreds of thousands, about 2 s): it is then tried only while nothing at all was found.
    spread = sum(t.frequency for t in selective)
    for k in range(0 if dropped else 1, n):
        size = n - k
        if size < max(1, math.ceil(n / 2)):
            break
        groups = [
            "( " + " & ".join(f"( {t.tsq} )" for t in subset) + " )"
            for subset in itertools.combinations(selective, size)
        ]
        costly = n > 2 and size < math.ceil(2 * n / 3) and len(groups) * size * spread > MAX_LOOSE_COST
        levels.append((" & ".join(["( " + " | ".join(groups) + " )", *fixed]), costly))
    return levels


def _relaxed_candidates(
    session: Session,
    catalog: Catalog,
    req: SearchRequest,
    as_of: date,
    scope: dict[str, Any],
    budget: int,
    seen: set[uuid.UUID],
) -> list[_Candidate]:
    """Passages holding most, not all, of the query's terms: used when too few hold them all.

    Selection: all of the rarer terms but one (or all of them, the common ones dropped), then all but two, and so on
    down to half of them (a costly loose form only while nothing at all was found); quoted phrases stay required and
    -terms excluded. Ranking: ``ts_rank_cd`` of any
    term, times the square of the share of the query's terms the passage holds, each term weighted by its rarity
    (inverse frequency). Every hit lists the terms it lacks.
    """
    # Only the languages indexed for the jurisdictions in scope: every other configuration would only cost index scans.
    within = None
    if scope["target"] is not None and not req.include_foreign and not scope["treaty_ids"]:
        within = {*scope["chain"], *scope["regional"], *scope["case_law"]}
    configs = sorted({search_config(lang) for lang in indexed_languages(session, catalog, within)})
    if req.languages:
        configs = [c for c in configs if c in {search_config(lang) for lang in req.languages}]
    queries = {c: rq for c, rq in _relaxable(session, req.q, configs).items() if len(rq.optional) >= 2}
    levels = {c: _levels(rq) for c, rq in queries.items()}
    found: list[_Candidate] = []
    for depth in range(max((len(v) for v in levels.values()), default=0)):
        params: dict[str, Any] = {"as_of": as_of, "budget": budget, "seen": [str(s) for s in seen]}
        filters = _filters(req, scope, params)
        match_parts, rank_parts, matched_parts = [], [], []
        for config, forms in levels.items():
            if depth >= len(forms) or (forms[depth][1] and seen):
                continue
            rq = queries[config]
            is_cfg = f"p.search_config = CAST('{config}' AS regconfig)"
            params[f"r_{config}"] = forms[depth][0]
            params[f"o_{config}"] = " | ".join(f"( {t} )" for t in [*(t.tsq for t in rq.optional), *rq.required])
            total = sum(t.weight for t in rq.optional)
            share, labels = [], []
            for i, t in enumerate(rq.optional):
                params[f"t_{config}_{i}"] = t.tsq
                has = f"p.tsv @@ CAST(:t_{config}_{i} AS tsquery)"
                share.append(f"{t.weight / total:.6f} * ({has})::int")
                labels.append(f"CASE WHEN {has} THEN {i} END")
            match_parts.append(f"({is_cfg} AND p.tsv @@ CAST(:r_{config} AS tsquery))")
            rank_parts.append(
                f"WHEN {is_cfg} THEN ts_rank_cd(p.tsv, CAST(:o_{config} AS tsquery), {RANK_NORMALIZATION})"
                f" * power({' + '.join(share)}, 2)"
            )
            matched_parts.append(f"WHEN {is_cfg} THEN array_remove(ARRAY[{', '.join(labels)}], NULL)")
        if not match_parts:
            break
        sql = (
            f"SELECT {_COLUMNS}, CASE {' '.join(rank_parts)} ELSE 0 END AS lexical, true AS direct,"
            f" length(p.text) AS text_length, CASE {' '.join(matched_parts)} END AS matched"
            f" FROM passages p WHERE ({' OR '.join(match_parts)}) AND {' AND '.join(filters)}"
            " AND NOT (p.id = ANY(CAST(:seen AS uuid[]))) ORDER BY lexical DESC, p.id LIMIT :budget"
        )
        for row in session.execute(text(sql), params).all():
            *columns, matched = row
            config = search_config(columns[8])
            held = set(matched or [])
            missing = list(dict.fromkeys(t.label for i, t in enumerate(queries[config].optional) if i not in held))
            candidate = _Candidate(*columns)
            candidate.tier, candidate.missing_terms = 1, missing
            found.append(candidate)
            seen.add(columns[0])
        if len(seen) >= req.limit:
            break
    return found


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
    # Texts "as enacted" / "as made" show the Act as passed: later amendments and repeals are not applied, so whether
    # such a text still applies is not known from the text itself (only the date it was enacted is).
    as_enacted: set[Any] = set()
    pv_ids = [c.provision_version_id for c in candidates if c.provision_version_id is not None]
    if pv_ids:
        as_enacted = set(
            session.scalars(
                select(m.ProvisionVersion.id)
                .join(m.InstrumentVersion, m.InstrumentVersion.id == m.ProvisionVersion.instrument_version_id)
                .where(m.ProvisionVersion.id.in_(pv_ids), m.InstrumentVersion.consolidation_kind.in_(AS_ENACTED_KINDS))
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
            elif c.status == "in_force" and c.valid_to is None and c.provision_version_id in as_enacted:
                applies = None  # the Act as passed, no later version recorded: whether it still applies is unknown
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


def _order(c: _Candidate) -> tuple[int, int, float, str]:
    return (c.tier, c.position, -c.final, str(c.passage_id))


def _resolve_citation(session: Session, req: SearchRequest, scope: dict[str, Any]) -> ResolvedCitation | None:
    parsed = parse_citation(req.q)
    if parsed is None:
        return None
    within = None if scope["target"] is None or req.include_foreign else [*scope["chain"], *scope["regional"]]
    return resolve(session, parsed, within, scope["target"])


def _citation_candidates(
    session: Session,
    req: SearchRequest,
    as_of: date,
    scope: dict[str, Any],
    citation: ResolvedCitation,
    budget: int,
) -> list[_Candidate]:
    """The passages of the cited provision that pass the search's filters (date, status, scope), in document order."""
    if not citation.provision_ids:
        return []
    params: dict[str, Any] = {"as_of": as_of, "budget": budget, "pids": citation.provision_ids}
    filters = _filters(req, scope, params)
    sql = (
        f"SELECT {_COLUMNS}, 1.0 AS lexical, true AS direct, length(p.text) AS text_length,"
        " array_position(CAST(:pids AS uuid[]), pv.provision_id) AS position"
        " FROM passages p JOIN provision_versions pv ON pv.id = p.provision_version_id"
        f" WHERE pv.provision_id = ANY(CAST(:pids AS uuid[])) AND {' AND '.join(filters)}"
        " ORDER BY position, p.id LIMIT :budget"
    )
    out = []
    for *columns, position in session.execute(text(sql), params).all():
        candidate = _Candidate(*columns)
        candidate.tier, candidate.position = -1, int(position)
        out.append(candidate)
    return out


def search(
    session: Session, catalog: Catalog, req: SearchRequest, *, today: date, candidate_budget: int = 200
) -> dict[str, Any]:
    req = replace(req, q=clean_query(req.q))
    _validate(catalog, req)
    as_of = req.as_of or today
    expansion_report, expansions = _expansions(session, req.q, req.query_language)
    scope = _scope(session, catalog, req.jurisdiction, as_of)
    citation = _resolve_citation(session, req, scope)
    candidates = _citation_candidates(session, req, as_of, scope, citation, candidate_budget) if citation else []
    strict = _candidates(session, req, as_of, scope, expansions, candidate_budget)
    if strict:
        # The units of a long cited article must not push every passage that holds the query's words off the page.
        keep = sorted({c.position for c in candidates})[: max(3, req.limit // 2)]
        candidates = [c for c in candidates if c.position in keep]
    seen = {c.passage_id for c in candidates}
    candidates += [c for c in strict if c.passage_id not in seen]
    full_matches = len(strict)
    # A query that cites a law it names asks for that provision: "most of its words" would add noise.
    if len(candidates) < req.limit and citation is None:
        seen |= {c.passage_id for c in candidates}
        candidates += _relaxed_candidates(session, catalog, req, as_of, scope, candidate_budget, seen)
    _classify(session, catalog, req, as_of, candidates)
    # The cited provision first, in document order; then passages holding every term; then those holding most.
    candidates.sort(key=_order)

    # One hit per provision/paragraph: among parallel language versions prefer the better tier, then a direct match,
    # then a language of the target jurisdiction, then the language of the question, then English (so that the units
    # of one cited article do not come in a mix of languages), then the higher score.
    target_langs = set(catalog.jurisdictions.get(req.jurisdiction).official_languages) if req.jurisdiction else set()

    def preference(c: _Candidate) -> tuple[int, bool, bool, bool, bool, float]:
        return (
            -c.tier,
            c.direct,
            c.language in target_langs,
            c.language == req.query_language,
            c.language == "en",
            c.final,
        )

    best: dict[tuple[Any, ...], _Candidate] = {}
    for c in candidates:
        if c.group_key not in best or preference(c) > preference(best[c.group_key]):
            best[c.group_key] = c
    ranked: list[_Candidate] = []
    per_judgment: Counter[Any] = Counter()
    for c in sorted(best.values(), key=_order):
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
    citation_block = None
    if citation is not None:
        laws = session.execute(
            select(m.Instrument.uri, m.Instrument.title).where(
                m.Instrument.id.in_([uuid.UUID(i) for i in citation.instrument_ids])
            )
        ).all()
        cited_hits = sum(1 for c in ranked if c.tier == -1)
        names = ", ".join(title for _, title in laws)
        citation_block = {
            "cited": citation.cited,
            "instruments": [{"uri": uri, "title": title} for uri, title in laws],
            "exact": citation.exact,
            "results": cited_hits,
        }
        if not citation.provision_ids:
            global_limits.append(
                Limitation("CITED_UNIT_NOT_FOUND", "warning", f"{citation.cited} was not found in {names}.")
            )
        elif not citation.exact:
            global_limits.append(
                Limitation(
                    "CITED_UNIT_NOT_FOUND",
                    "warning",
                    f"{citation.cited} was not found as such in {names}; the nearest enclosing unit is listed first.",
                )
            )
        elif not cited_hits:
            global_limits.append(
                Limitation(
                    "CITED_PROVISION_NOT_APPLICABLE",
                    "warning",
                    f"{citation.cited} ({names}) has no indexed text that applies on {as_of} under the search's "
                    "filters (status, date, scope).",
                )
            )
    partial = sum(1 for c in ranked if c.tier == 1)
    if partial:
        global_limits.append(
            Limitation(
                "PARTIAL_MATCH",
                "info",
                (
                    "No passage in scope contains every term of the query."
                    if full_matches == 0
                    else f"Only {full_matches} passage(s) in scope contain every term of the query."
                )
                + f" {partial} result(s) match it only in part; each lists the terms it lacks (score.missing_terms; "
                "empty when every term is present but not as one phrase).",
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
        "citation": citation_block,
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
    score: dict[str, Any] = {
        "final": c.final,
        "lexical": round(float(c.lexical), 6),
        "authority_weight": c.authority.weight,
        "length_factor": round(length_factor(c.text_length), 4),
        "matched_via": {-1: "citation", 1: "partial_query"}.get(c.tier, "query" if c.direct else "concept_expansion"),
    }
    if c.tier == 1:
        score["missing_terms"] = c.missing_terms or []
    return score


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
        "unit": {
            "uri": f"{j.uri}/~{para.eid}",
            "eid": para.eid,
            "label": catalog.module_for(j.jurisdiction_id).judgment_unit_label(para.num, para.part, j.ext),
            "heading": para.part,
        },
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
