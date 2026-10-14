# Multilingual, jurisdiction-aware retrieval plan

## Stages

| # | Stage | Now (implemented) | Next |
|---|---|---|---|
| 1 | Structured jurisdiction and authority filters | Ancestor chain + territorial extent; national court system for case law; treaties by party; temporal interval; status (in force / prospective / proposed); language | Regional-organisation law via memberships; forum-specific court sets |
| 2 | Exact text and keyword search | PostgreSQL FTS per language (`english`, `french`, `german`, ... `simple` fallback); weighted tsvector (text A, context B); `websearch_to_tsquery` (phrases, OR, negation) | Trigram for citations and party names; exact-phrase and proximity operators exposed in the UI |
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
