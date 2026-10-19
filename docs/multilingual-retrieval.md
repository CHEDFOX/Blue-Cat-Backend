# Multilingual, jurisdiction-aware retrieval plan

## Stages

| # | Stage | Now (implemented) | Next |
|---|---|---|---|
| 1 | Structured jurisdiction and authority filters | Ancestor chain + territorial extent; national court system for case law; treaties by party; temporal interval; status (in force / prospective / proposed); language | Regional-organisation law via memberships; forum-specific court sets |
| 2 | Exact text and keyword search | PostgreSQL FTS per language (`english`, `french`, `german`, ... `simple` fallback); weighted tsvector (text A, context B); `websearch_to_tsquery` (phrases, OR, negation); when too few passages hold every term, most of them (below); direct citation lookup (below) | Trigram for party names; exact-phrase and proximity operators exposed in the UI |
| 3 | Multilingual semantic search | — | Multilingual embedding model behind a provider interface; pgvector HNSW; embeddings stored per passage with model id and version |
| 4 | Provision and concept search | Provision-level units with stable URIs and pinpoints; curated concept labels | Concept graph (broader/narrower/related) per jurisdiction, reviewed by lawyers |
| 5 | Citation graph traversal | Citation links resolved to provisions/judgments; treatment attached to hits | Graph expansion ("authorities citing this", "followed / distinguished") as a retrieval signal |
| 6 | Cross-reference and amendment relationships | Amends / repeals / inserts / proposes_amendment on hits | Version-aware amendment chains; "as amended by" reconstruction |
| 7 | Factual-pattern retrieval | — | Fact extraction from judgments into `propositions(kind=established_fact)` and matching against case facts |
| 8 | Legal issue and procedural-stage matching | — | `legal_issues` taxonomy per jurisdiction; classifier with human review |
| 9 | Authority-sensitive reranking | `lexical × authority weight × match type`, every component returned; preference for target-language versions; diversity cap per judgment | Learned reranker (cross-encoder) evaluated per jurisdiction; authority stays a hard constraint, not just a feature |
| 10 | Source and currency verification | Currency date, derived end dates, outstanding changes, citator coverage, source authority on every hit | Scheduled re-verification against sources; freshness SLAs per source |

## Cross-lingual retrieval

- **Originals first.** Every language version is indexed in its own language with its own
  stemmer. Equally authentic versions (the fictional Borealis Code, the Northern Union
  Convention) are separate expressions of one work and are grouped in results
  (`parallel_versions`). They are never treated as translations of one another.
- **Concept labels (now).** A query that matches a curated label (e.g. *retention*) is
  expanded with the concept's labels in other languages (*conservation*, *Aufbewahrung*).
  Expansion hits are marked `matched_via: concept_expansion` and slightly down-weighted.
  The response includes `QUERY_EXPANDED`, which says shared vocabulary does not imply
  equivalent legal effect.
- **Embeddings (Phase 3).** Multilingual dense retrieval as an additional candidate
  generator, fused with lexical candidates (reciprocal-rank fusion). Results stay subject
  to stage-1 filters and stage-9 authority rules.
- **Translations.** Machine translations will be stored in `translations` with `kind =
  machine`, shown only next to the original, and flagged `MACHINE_TRANSLATION` (critical).
  They are never indexed as if they were source text.

## When not every word matches, and citations (2026-10-19)

The evaluation of 240 judged queries (`docs/search-engine-evaluation.md`) found that 45% returned nothing, 81% of
natural-language questions, because `websearch_to_tsquery` requires every word, and that 1 of 24 citations found the
cited provision, because a provision's number is not part of its indexed text.

- **Most of the words (`ulis/retrieval/search.py`, `_relaxed_candidates`).** When fewer passages than the requested
  number hold every term, search adds passages holding most of them: all the rarer terms but one, then all but
  two, down to half of them; below two thirds, a form that would make PostgreSQL check many combinations over long
  posting lists ("any 4 of 8" common English words) is tried only while nothing at all was found. Terms in more than 10% of passages ("require", "section", "may") only rank; quoted phrases
  stay required and `-terms` excluded. Each clause is parsed by PostgreSQL on its own, so stemming and stopwords are
  those of the full query. Ranking: `ts_rank_cd` of any term times the square of the share of the query's terms the
  passage holds, each term weighted by its rarity (from PostgreSQL's own column statistics). Full matches always rank
  first. Partial hits are marked `matched_via: partial_query` and list `missing_terms`; the response adds
  `PARTIAL_MATCH`. Japanese (unspaced) questions are relaxed over the character pairs of their content words (kanji,
  katakana), not across particles, and report missing words, not pairs.
- **Citations (`ulis/retrieval/citations.py`).** "§ 823 Abs. 1 BGB", "Art. 6(1)(f) GDPR", "3 kap. 1 § brottsbalken",
  "RL 28:1", "art. 56.1 ET", "art. L. 212-1 C. consom.", "刑法第百九十九条", "adran 25 Mesur y Gymraeg (Cymru) 2011",
  "29 CFR 825.305(a)" are parsed into the cited unit and the name of the law. The law is found by a curated short
  name (`config/citation-aliases.yaml`; names of three characters or fewer only when a jurisdiction is given), by the
  publisher's abbreviation (German `jurabk`, Japanese `abbrev`), number or title in any language version (indexes of
  migration 0007), or from the citation itself (US CFR, US Code). The unit is matched against the stored number
  paths; a chapter the citation leaves out is skipped ("36 § avtalslagen"), and a subdivision that does not exist
  falls back to the enclosing unit (`CITED_UNIT_NOT_FOUND`). The cited provision's units come first, in document
  order, under the same date, status and scope filters as every other hit (`matched_via: citation`; `citation` in
  the response). A citation that resolves to a law is answered without the "most of the words" fallback.
- **Query cleaning.** Control characters (a NUL byte cannot be sent to PostgreSQL) become spaces; soft hyphens,
  zero-width spaces and byte-order marks are removed. A Japanese `-term` now excludes, as in every other language.
- **Coverage figures** are cached per database until the next ingestion run (`ulis/retrieval/coverage.py`): counting
  passages took about 0.7 s on every search. Restart the API after a `reindex`.

## False friends and long passages (found on real data)

- **False friends.** English "conservation" matched the French label *conservation*
  (= retention) and expanded an English fisheries query into data-retention provisions. A
  `query_language` parameter now restricts label matching to the question's language; the UI
  defaults to English and offers "Any" explicitly. Tested.
- **Very long passages.** A public law section that enacts a whole code title "as follows"
  (438,000 characters) matched almost every query. Ranking now normalises by length
  (`ts_rank_cd` flag 1) and down-weights passages over 5,000 characters, and the factor is
  returned in the score breakdown. Tested.

## Known limitations of the current slice

- Languages without a PostgreSQL stemmer (e.g. Hindi and other Indic languages) use the
  `simple` configuration: exact tokens only, no stemming or transliteration. Embeddings and
  language-specific analysers are required before such a jurisdiction is evaluated.
- Concept labels are hand-curated and few; recall for paraphrases is limited.
- Ranking weights are hand-set and have not yet been tuned against an evaluation set.

## When to introduce a dedicated search engine

Only when the evaluation set and load tests show PostgreSQL FTS falling short. For
example: recall@20 below target after analyser tuning, p95 latency above 500 ms at the
expected QPS, or a need for features such as per-field BM25 or learning-to-rank plugins.
The engine would be a derived index fed from `passages`, never a source of truth.
