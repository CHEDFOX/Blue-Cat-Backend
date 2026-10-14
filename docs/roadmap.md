# Phased implementation plan

Each phase has exit criteria. A jurisdiction moves forward only when **its own** evaluation
passes. Expanding coverage is never a substitute for demonstrated quality.

| Phase | Scope | Exit criteria | Status |
|---|---|---|---|
| **1. Foundations** | Jurisdiction tree and versioned profiles; core schema; provenance; object store; licence gate; secure case workspace | Schema migrations up/down; profile validation; licence gate tests; workspace with RLS and audit | Done, except the workspace (designed in architecture.md §5) |
| **2. First jurisdiction** | One representative jurisdiction with a small, *authorised* corpus; reliable ingestion; source-linked retrieval | Parser accuracy ≥ 99.5% of units on a hand-checked sample; 100% of returned texts re-derivable from raw sources; licence sign-off | **In progress: US federal** (GPO USLM sample) **UK** (legislation.gov.uk, full point-in-time history) and **Canada** (federal Acts, English and French, current consolidation). Every stored text re-derivable from raw bytes. Parser accuracy not yet measured by hand; profiles not yet reviewed by counsel |
| **3. Hybrid retrieval** | Multilingual embeddings, fusion, reranking, citation relationships at scale | Recall@20 and nDCG@10 on the jurisdiction's evaluation set beat lexical-only by an agreed margin; latency budget met | Not started |
| **4. Jurisdiction-aware research** | Issue identification, statutory version tracking, authority verification, structured research brief | Expert-graded brief quality; zero fabricated authorities; 100% of cited passages quote-verified | Partly: version tracking and authority classification exist |
| **5. Case analysis and QA** | Facts vs allegations, adverse-authority discovery, drafting aids, independent verification agent | Adverse-authority recall measured; reviewer acceptance rate; red-team results | Not started |
| **6. More jurisdictions** | Connectors and modules per jurisdiction; regional organisations (e.g. EU direct effect) | Per-jurisdiction onboarding checklist complete | Started: UK and Canada added; India blocked (publisher refuses automated access) |
| **7. Independent evaluation** | Qualified reviewers per jurisdiction; publish coverage and quality cards | Gates in security-and-evaluation.md met per jurisdiction | Not started |
| **8. Scale** | Partitioning, dedicated search/vector engines, provider integrations, caching | Driven by measured load and corpus size | Not started |

## Immediate next steps (proposed)

1. **Add jurisdictions one at a time** (next e.g. Germany, the EU, Australia, Canadian regulations and earlier versions),
   each with its licence read from the publisher and recorded before ingestion.
2. **Measure parse accuracy** for the US, UK and Canadian parsers against a hand-checked sample.
3. **Have counsel review the profile** (courts, precedent, commencement practice), then set
   it to `reviewed`.
4. **Build its evaluation set** (100–300 questions with gold authorities and dates, written
   by practitioners), then baseline the current lexical retrieval.
5. **Implement the secure workspace** (matters, RLS, encrypted uploads, audit) before any
   user brings client facts.
6. **Phase 3**: embeddings behind a provider interface; fusion; compare against the baseline.

## What we will not do

- Ingest every country before the first jurisdiction's workflow is proven.
- Present machine translations, commentary or bills as authority.
- Claim coverage of a jurisdiction whose profile is unreviewed or whose corpus is partial.
  Coverage cards are published from `coverage_for()` data, not from marketing copy.
