# Phased implementation plan

Each phase has exit criteria. A jurisdiction moves forward only when **its own** evaluation
passes. Expanding coverage is never a substitute for demonstrated quality.

| Phase | Scope | Exit criteria | Status |
|---|---|---|---|
| **1. Foundations** | Jurisdiction tree and versioned profiles; core schema; provenance; object store; licence gate; secure case workspace | Schema migrations up/down; profile validation; licence gate tests; workspace with RLS and audit | Done, except the workspace (designed in architecture.md §5) |
| **2. First jurisdiction** | One representative jurisdiction with a small, *authorised* corpus; reliable ingestion; source-linked retrieval | Parser accuracy ≥ 99.5% of units on a hand-checked sample; 100% of returned texts re-derivable from raw sources; licence sign-off | **Done for the first set**: US federal, UK, Canada (all 953 Acts, English and French) and 16 more countries and the EU, each with its licence recorded and every stored text re-derivable from raw bytes. Parser accuracy not yet measured by hand; profiles not yet reviewed by counsel |
| **3. Hybrid retrieval** | Multilingual embeddings, fusion, reranking, citation relationships at scale | Recall@20 and nDCG@10 on the jurisdiction's evaluation set beat lexical-only by an agreed margin; latency budget met | Not started |
| **4. Jurisdiction-aware research** | Issue identification, statutory version tracking, authority verification, structured research brief | Expert-graded brief quality; zero fabricated authorities; 100% of cited passages quote-verified | **Prototype**: the research assistant (`POST /v1/research`) runs understand, search, draft, verify with quote checking and authority re-reading; exercised with a scripted model on real data, **not yet with the real model or graded by experts** (`eval/questions.yaml` and `scripts/eval_research.py` are ready for that) |
| **5. Case analysis and QA** | Facts vs allegations, adverse-authority discovery, drafting aids, independent verification agent | Adverse-authority recall measured; reviewer acceptance rate; red-team results | Not started |
| **6. More jurisdictions** | Connectors and modules per jurisdiction; regional organisations (e.g. EU direct effect) | Per-jurisdiction onboarding checklist complete | Under way: 19 countries and the EU; two case-law samples (Netherlands, EU Courts); blocked or waiting for permission: India, New Zealand, Austria (RIS), German court decisions, Hong Kong, Norway, Belgium, Lithuania, Philippines, Mexico, Poland (ISAP) |
| **7. Independent evaluation** | Qualified reviewers per jurisdiction; publish coverage and quality cards | Gates in security-and-evaluation.md met per jurisdiction | Not started |
| **8. Scale** | Partitioning, dedicated search/vector engines, provider integrations, caching | Driven by measured load and corpus size | Not started |

## Immediate next steps (proposed)

1. **Deploy and test with early users** ([deployment.md](deployment.md)), then fix what the first real runs show: the
   real language-model path, the Docker/Caddy/systemd kit and load behaviour have not been run.
2. **Load the full German corpus** (6,139 laws, at least 8.5 h) and more case law (US Caselaw Access Project, ECHR,
   Swiss federal court), each with its licence read from the publisher first.
3. **Grade the research assistant**: write the evaluation questions with practitioners per jurisdiction and run
   `scripts/eval_research.py` with the real model; track unsupported claims removed by the verifier.
4. **Measure parse accuracy** against hand-checked samples, and **have counsel review** profiles and licence
   decisions before wider use.
5. **Implement the secure workspace** (matters, RLS, encrypted uploads, audit) and user accounts before any user
   brings client facts beyond the early-access test.
6. **Phase 3**: embeddings behind a provider interface; fusion; compare against the lexical baseline.

## What we will not do

- Present the corpus as complete: it holds the countries listed in the README and nothing else.
- Present machine translations, commentary or bills as authority.
- Claim coverage of a jurisdiction whose profile is unreviewed or whose corpus is partial.
  Coverage cards are published from `coverage_for()` data, not from marketing copy.
