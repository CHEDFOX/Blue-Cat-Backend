# Search engine evaluation: PostgreSQL full-text vs Tantivy for the lexical stage (2026-10-19)

Question from the owner: can the lexical stage of `search()` (today PostgreSQL full-text search on `passages.tsv`) be
replaced by Tantivy, an embedded Rust search library, to save space, without losing quality? Before deciding, every
quality gate had to be measured. This document reports those measurements. It does not recommend switching; it says
what passed, what did not, and what would have to be fixed first.

Short answer:

- In the `tantivy-pg` mode (Tantivy indexes PostgreSQL's own lexemes and runs the query PostgreSQL builds), matching
  was identical to PostgreSQL on every comparison made: 240/240 judged queries, 89/89 syntax probes, 1181/1181
  randomized filter requests. Ranking (BM25 instead of `ts_rank_cd`) was judged better: nDCG@10 +0.109
  [95% CI +0.066, +0.154]. The judges were language models, not lawyers.
- The `tantivy-native` mode (no PostgreSQL in the lexical stage) ranks the same as `tantivy-pg` but its tokenizer
  differs from PostgreSQL's in 15 of 1181 filter requests and 1 of 89 syntax probes.
- The space saving is real but moderate: about 0.8 GB of Tantivy index against about 2.0 GB of `tsv` column plus GIN
  index, so about 1.2 GB (about 14% of the 8.6 GB dev database). The lexical stage is 2.5 to 4 times faster at the
  median, but end-to-end `search()` improves only about 2%, because about 0.7 s of every call goes to a full-table
  scan in `ulis/retrieval/coverage.py` that has nothing to do with the engine.
- The biggest quality problems found are shared by every engine, PostgreSQL included: 45% of the judged queries
  return nothing (81% of natural-language questions), and citation-style queries almost never find the cited
  provision. Changing the engine does not fix either.

## Fixes made after this evaluation (2026-10-19)

The four engine-independent problems below were fixed in production `search()` (PostgreSQL); the engine was not
changed. Design: `docs/multilingual-retrieval.md`, section "When not every word matches, and citations". Measured
on the same 240 judged queries, dev database and `today` as above, through the real `search()` with limit 10.
"Known item" is a provision the query's author marked as the answer (`relevant` in the query files), found among the
10 results; it is not the blind judges' nDCG, which would need new judgments for the new results.

| | before | after |
|---|---|---|
| queries returning nothing | 109 of 240 (45%) | 2 (1%) |
| natural-language questions returning nothing | 78 of 96 (81%) | 1 (1%) |
| known item in the top 10, all queries | 94 (39%) | 182 (76%) |
| known item at rank 1, all queries | 63 | 119 |
| known item in the top 10, natural questions | 5 of 96 | 52 (31 at rank 1) |
| known item in the top 10, citations | 1 of 24 | 24 of 24 (22 at rank 1) |
| mean reciprocal rank (known item) | 0.304 | 0.573 |
| `search()` latency p50 / p95 / max | 1,006 / 1,226 / 1,673 ms | 170 / 587 / 1,404 ms; 146 / 376 / 1,113 ms after the language fix of 2026-10-20 (results identical) |

Per language, known item in the top 10: en 14 -> 21, fr 12 -> 27, de 9 -> 19, es 14 -> 20, sv 14 -> 24, fi 11 -> 25,
ja 11 -> 24, cy 9 -> 22 (of 30 each). 88 queries improved; one moved down: sv-18 `artikel 6.1 e
dataskyddsförordningen allmänt intresse` now lists GDPR Art. 6(1)(e), which it cites, first and the expected Swedish
provision second. Of the 55 queries that already found at least 10 passages holding every term, 50 keep exactly the
same top 10; the other 5 are the citations sv-28, cy-28 and cy-30 (the cited provision now comes first) and ja-26
and ja-27 (changed by the `-term` fix). The "before" latencies were measured while another job used the machine;
the evaluation's own figure was 775 ms at p50.

1. **Every word had to match.** When fewer passages than requested hold every term, passages holding most of them
   follow, marked `partial_query` with the terms each lacks (`PARTIAL_MATCH`). Full matches always rank first.
2. **Citations.** "§ 823 Abs. 1 BGB", "Art. 6(1)(f) GDPR", "3 kap. 1 § brottsbalken", "RL 28:1", "art. 1902 CC",
   "刑法第百九十九条", "adran 173 Deddf Rhentu Cartrefi (Cymru) 2016" and the other 17 judged citations resolve to the
   cited provision, which is listed first (`citation`). Laws are found by curated short names
   (`config/citation-aliases.yaml`), publisher abbreviations, numbers and titles in every language (migration 0007).
3. **0.7 s per call in `coverage.py`.** Counts are cached until the next ingestion run.
4. **Bugs.** A Japanese `-term` now excludes; a NUL byte (and other control characters) no longer reaches PostgreSQL.

Speed and recall were traded once, on measurement: relaxing "any 4 of 8" common English words costs PostgreSQL about
2 s (70 combinations checked per candidate among hundreds of thousands of US passages), so such a form is tried only
when nothing at all was found. Without that guard, earlier runs found about the same number of known items but took
up to 2.1-2.4 s; with a strict two-thirds floor instead, 7 fewer natural questions found their answer. The slowest
query left is fr-28 (11 terms, 1.4 s).

What remains: the known item is still missed by 58 of 240 queries, 44 of them natural-language questions (en-20 and
fi-21 return nothing: en-20 asks about 2010, before the indexed Australian compilation starts); ranking of the new
partial results has not been judged blind. Section 6 (what a Tantivy switch would need) is unchanged.

## Corrections after independent review (2026-10-19)

Two reviewers recomputed every metric from the raw files (judgments, key, results) without reusing this report's
scripts. The arithmetic and the blind judging hold: every table value in sections 3.2 and 3.3 reproduces, the
pools are exactly the union of the three engines' top 10, and the five worst regressions listed are in fact the five
worst. The following claims were overstated and are corrected here; where the sections below differ, this section
wins.

- **Matching parity counts include empty results.** 109 of 240 judged queries, 874 of 1,181 filter requests and 37
  of 89 probes return nothing on every engine and agree trivially. The informative figures for `tantivy-pg` are
  **131/131 non-empty queries, 307/307 non-empty filter requests and 48/48 non-empty probes identical**, with one
  more probe (`a & b | !c`) unverifiable because it reaches the 100,000 cut-off. In pg mode PostgreSQL builds the
  tsquery itself, so identical parsing holds by construction; the probes test the translation. Native mode differs on
  2 of 89 probes, and 11 of its 15 filter-run mismatches are citation or number inputs (`§ 1`, `1 §`, `RL 28:1`).
- **The ranking gain is concentrated.** The 23 boolean queries give 51% of the +0.109 nDCG@10, and English plus
  Japanese give 63%. Without boolean queries the gain is +0.066 [+0.029, +0.107]; without boolean queries and
  without en and ja (69 queries) it is **+0.019 [-0.009, +0.046], indistinguishable from zero**. 69 of the 120
  evaluable queries have 10 or fewer matches, so both engines show the same set (mean gain +0.005); the whole gain
  comes from the 51 queries with more than 10 matches (+0.25, 11 of them worse). Informative queries per language
  (more than 10 matches): en 10, fr 5, de 4, es 7, sv 2, fi 3, ja 10, cy 10.
- **Per-language "same or better" is not demonstrated.** No non-inferiority margin was set; the lower 95% bounds are
  negative in cy (-0.058), sv (-0.024), de (-0.020), fi (-0.016), es (-0.013) and fr (-0.012), with paired sign-flip
  p-values of 0.16-0.57. Gate 6 is therefore partial for both modes. Intervals on fewer than about 10 queries
  (citation n = 6, the 17 point-in-time queries: 4 better, 3 worse, 10 same, permutation p about 0.14) should be read
  as not significant; nothing is corrected for multiplicity. `tantivy-native` and `tantivy-pg` have identical
  per-query values; their slightly different intervals are bootstrap noise.
- **Point-in-time candidate sets are not identical in every test.** The point-in-time runner recorded candidate
  counts, not ids, and in 9 tests the count was exactly the 200 budget. Re-run with ids: tantivy-pg shares only 65 of
  200 candidates with PostgreSQL in pitb-jp-06, 71 in pitb-jp-09 and 174-198 in pitb-jp-07, a ranking effect at the
  budget cut, not a filter difference. Coverage is thin: tests per language en 215, fr 192, es 166, ja 76, sv 22,
  de 6, cy 5, **fi 0**; version-boundary cases exist only for en, es, fr and ja. Gate 4 stays partial and is not
  established for fi, sv, de and cy.
- **Recall at the 200-candidate budget rests on 10 queries** (en-07, en-22, fi-18, cy-25 and six ja); none is fr, de,
  es or sv. On those 10 Tantivy's 200 candidates share on average 54% with PostgreSQL's. The 0.274 -> 0.292 target
  recall comes from 9 queries.
- **Japanese `-term` does nothing useful on any engine, PostgreSQL included** (a production bug, not engine-specific):
  the CJK path ORs every whitespace-separated term through `phraseto_tsquery`, and `phraseto_tsquery('simple',
  '-強盗')` gives `'強盗'`, so `窃盗 -強盗` means "窃盗 OR 強盗". The judges graded most documents of ja-26 and ja-27 0
  for this reason.
- **Flags not exercised on the dev corpus:** `include_proposed` (no passage has status `proposed`) and
  `include_case_law=false` (no judgment paragraphs) are covered only by the unit tests. Non-empty filter comparisons
  are thin for languages ja 3, de 6, es 6, fi 6, cy 8 and jurisdictions JP 9, GB-ENG 4, DE 8. Concept expansions
  fired on only 5 judged queries (en 2, fr 2, de 1).
- **Judge rubric.** The rubric in section 2 is a paraphrase. The judges' prompt read: 3 = directly answers the query
  for that jurisdiction and date; 2 = clearly relevant and useful (closely related provision, definitions the answer
  depends on); 1 = marginally relevant; 0 = not relevant, wrong jurisdiction, or not in force on the query's date when
  the query asks about the law on that date. The batch files also carried an older 0-2 instruction that every judge
  overrode. Regenerate the batch files before any lawyer re-judging.
- **The cross-check against the query files is not independent:** the query authors picked targets with the help of
  PostgreSQL search, and the same model family judged. It is a consistency check, not a validation.
- **Test suite.** The 2,178-passed run predates commit 2197b90; the full suite was re-run on the merged integration
  branch (result recorded in `docs/test-results.md`).

What does not change: no engine-specific crash or error; in pg mode no filter, validity-boundary, NULL-start or status
difference was found; the problems shared by every engine (45% empty results, citation queries) dominate quality.

## 1. What was tested

### Corpus

Dev database `ulis`, unchanged before and after the runs: 2,519,798 passages in 14 jurisdictions. Every passage is a
legislative provision; the corpus holds no case law, no treaties and no passages with status `repealed` or
`proposed` (2,518,541 `in_force`, 1,257 `prospective`).

| jurisdiction | passages | languages |
|---|---|---|
| US (full eCFR) | 1,866,278 | en |
| CA | 183,399 | en 92,891, fr 90,508 |
| AU | 116,274 | en |
| JP | 76,296 | ja |
| ES | 54,443 | es |
| GB | 51,926 | en |
| DE | 49,158 | de |
| FI | 35,561 | fi 17,788, sv 17,773 |
| FR | 23,263 | fr |
| GB-WLS | 22,297 | en 11,161, cy 11,136 |
| EU | 19,522 | en 12,832, de 3,345, fr 3,345 |
| SE | 16,401 | sv |
| GB-NIR | 3,601 | en |
| GB-SCT | 1,379 | en |

By language: en 2,156,342, fr 117,116, ja 76,296, es 54,443, de 52,503, sv 34,174, fi 17,788, cy 11,136. The
corpus is dominated by US English; every other language has between 11 thousand and 117 thousand passages.

### Engines

All three engines run inside the real `search()` (classification, grouping, limitations unchanged), with limit 10
and candidate budget 200; only `_candidates()` is swapped. `today` was fixed at 2026-10-18.

- `pg`: production code. `websearch_to_tsquery` per search configuration, `phraseto_tsquery` of
  `segment_for_search()` bigrams for CJK, concept expansions, filters, `ts_rank_cd(tsv, q, 1|32)`.
- `tantivy-pg`: one Tantivy index holding PostgreSQL's own lexemes (`passages.tsv`) with positions and weights.
  PostgreSQL is still used at query time, but only to build the tsquery texts (one `SELECT`); the query is
  translated operator by operator. Filters run on Tantivy fast fields with SQL NULL semantics. Ranking is BM25
  (k1 1.2, b 0.75; field A weight 1.0, field B weight 0.4).
- `tantivy-native`: same index layout and ranking, but tokenizing, stemming (Snowball via rust-stemmers),
  stopwords and the `websearch_to_tsquery` parser are re-implemented in Rust. No PostgreSQL in the lexical stage.
- `tantivy-pg+prox` and `tantivy-native+prox`: the two Tantivy modes with an optional proximity boost. Run for
  information only; they did not add documents to the judging pools.

Code: `ulis/retrieval/tantivy_engine.py` and `scripts/search_eval/` on branch `wt/search` (commit 2197b90). Production
`search()` is unchanged.

### Test sets

| test | size | what it checks |
|---|---|---|
| Judged queries | 240 (30 per language) | recall, ranking, latency |
| Syntax probes | 89 inputs, all 8 languages | robustness to legal and hostile query syntax |
| Point-in-time cases | 682 tests in 107 cases | validity dates, statuses, `include_*` flags |
| Randomized filter requests | 1,200 (1,181 compared) | filter parity, field by field |
| Sizes | full database | index and table sizes |

Judged queries per language: 12 natural-language questions, 9 keyword queries, 3 quoted phrases, 3 boolean (`OR`,
`-term`), 3 citations. In each language 6 of the 30 queries ask for a past date (`as_of`). Totals: natural 96,
keyword 72, phrase 24, boolean 24, citation 24. Jurisdictions per language: en US 7, EU 6, CA 5, AU 5, GB-EAW 4, GB 3;
fr FR 20, CA 6, EU 4; de DE 22, EU 8; es ES 30; sv SE 20, FI 10; fi FI 30; ja JP 30; cy GB-WLS 30.

## 2. Method

### Recall and parity

For every judged query, syntax probe and filter request, the full match set was compared (budget 100,000, i.e. no
ranking cut) between PostgreSQL and each Tantivy mode, passage by passage. The candidate set at the real budget of 200
was compared too; it depends on ranking.

### Ranking: blind pooled judging by language models, not lawyers

For each judged query, the top 10 of `pg`, `tantivy-pg` and `tantivy-native` were merged and deduplicated by
provision version (the pool). Each document got an opaque id and the documents of each query were shuffled, so the
judges could not tell which engine had returned what. Judges saw the query, a one-line intent written with the query,
the jurisdiction, the `as_of` date, citation, heading, status, validity dates and the first 700 characters of the
text. They graded each document 0 to 3:

- 3: the provision the query is looking for;
- 2: a closely related provision, or a part of the target provision;
- 1: related or peripheral;
- 0: not relevant, wrong jurisdiction, out of force on the date, or containing an excluded `-term`.

**The judges were language-model agents (Claude), not lawyers.** 16 primary batches (two per language) covered 1,002
query-document pairs. 8 agreement batches (one per language, 5 queries each, 391 pairs) were graded again by a
different agent instance, with different opaque ids and a new order, to measure consistency. What this implies:

- The grades measure whether a careful reader with general legal knowledge would call a provision relevant. They are
  not a professional legal assessment. Some grades depend on legal facts a model may get wrong (several judges noted
  where they graded from what they knew of a statute rather than from the 700 characters shown).
- Both judges of a pair are the same model family. Their agreement measures consistency; errors they share are not
  detected by it.
- The intents were written by agents that had also looked at the PostgreSQL results when building the query set. If
  anything, that favours PostgreSQL.
- Before any production decision, a sample should be re-graded by lawyers for at least the launch languages.

All 24 judges used the 0-3 scale; the batch files carried an older 0-2 instruction that every judge noticed and
overrode in favour of the task's 0-3 scale. All 1,002 primary pairs and all 391 agreement pairs were graded exactly
once (checked against the key: none missing, none extra, no duplicates). Grade distribution (primary): 0: 277,
1: 365, 2: 199, 3: 161.

### Metrics

- nDCG@10 with gain 2^grade - 1. The ideal ranking is built from all judged documents of the query (the pool).
  Unjudged documents are removed from the ranked list before scoring (condensed lists). For the three pooled engines
  there were none (0 of 703 top-10 documents); the `+prox` variants had 61 unjudged documents of 703, so their
  numbers are information only.
- P@5: share of the top 5 with grade 2 or 3 (always divided by 5).
- MRR@10: reciprocal rank of the first grade-3 document in the top 10 (0 if none).
- Evaluable queries: 120 of 240. 109 queries have an empty pool because all three engines return nothing; 11 more
  have a pool in which every document was graded 0 (en-03, fr-02, fr-22, es-02, es-03, es-06, es-09, es-11, es-12,
  es-14, sv-28). 102 of the 120 have at least one grade-3 document.
- 95% confidence intervals: percentile bootstrap over queries, 10,000 resamples. Differences against `pg` are paired:
  the same resampled queries are used for both engines.
- Inter-judge agreement: weighted Cohen's kappa (linear and quadratic weights on the 0-3 scale), unweighted kappa,
  exact and within-one agreement.

Important limit of pooled nDCG: the ideal ranking only contains what the three engines found. Because the three
engines have identical match sets, pooled nDCG measures how well each engine orders the same matches; it cannot see
relevant provisions that no engine matched. That is measured separately below with the graded relevance lists in the
query files (written before the runs), which name the target provisions whether or not any engine found them.

## 3. Results

### 3.1 Recall

| measure | pg | tantivy-pg | tantivy-native |
|---|---|---|---|
| Full match set identical to pg, judged queries (budget 100,000) | reference | 240/240 | 240/240 |
| Full match set identical to pg, syntax probes | reference | 89/89 (1) | 88/89 |
| Filter requests identical to pg (1,181 compared, 1,192,654 passages) | reference | 1181/1181 | 1166/1181 |
| Passages only in pg / only in the engine (filter runs) | - | 0 / 0 | 26 / 32 |
| Candidate set at budget 200: recall against pg | 1.0 | 0.965 | 0.965 |
| Zero-result rate, judged queries | 0.454 | 0.454 | 0.454 |
| Zero-result rate, natural-language questions | 0.81 | 0.81 | 0.81 |
| Recall@10 of the query files' target provisions | 0.216 | 0.245 | 0.245 |
| Recall of the target provisions in the 200 candidates | 0.274 | 0.292 | 0.292 |

(1) `a & b | !c` reached the 100,000 budget in both engines, so its cut-off depends on ranking; the match logic is the
same.

The 3.5% of PostgreSQL's 200 candidates that Tantivy does not pass to `_classify()` are lost at the budget cut, not
by matching: the two rankings pick a different top 200 from the same matches. Measured against the target provisions,
Tantivy's 200 candidates contain more of them (0.292 against 0.274).

Absolute recall is low on every engine. `websearch_to_tsquery` ANDs every term, so a question like "Can my employer
ask for a doctor's certification before approving FMLA leave for a serious health condition?" (en-01) matches
nothing, and Tantivy copies that semantics exactly.

### 3.2 Ranking (blind pooled LLM judgments)

Overall, 120 evaluable queries:

| engine | nDCG@10 | P@5 (grade >= 2) | MRR@10 (first grade 3) | unjudged in top 10 |
|---|---|---|---|---|
| pg | 0.773 [0.719, 0.823] | 0.298 [0.253, 0.345] | 0.543 [0.464, 0.624] | 0 of 703 |
| tantivy-pg | 0.882 [0.852, 0.910] | 0.393 [0.347, 0.440] | 0.662 [0.589, 0.733] | 0 of 703 |
| tantivy-native | 0.882 [0.853, 0.909] | 0.393 [0.348, 0.440] | 0.662 [0.587, 0.735] | 0 of 703 |
| tantivy-pg+prox (information only) | 0.860 [0.827, 0.890] | 0.402 [0.355, 0.448] | 0.651 [0.576, 0.725] | 61 of 703 |

Paired differences against pg (better / worse / same = number of queries):

| engine | nDCG@10 diff | better/worse/same | P@5 diff | better/worse/same | MRR@10 diff | better/worse/same |
|---|---|---|---|---|---|---|
| tantivy-pg | +0.109 [+0.066, +0.154] | 48/18/54 | +0.095 [+0.052, +0.142] | 32/8/80 | +0.119 [+0.059, +0.181] | 35/12/73 |
| tantivy-native | +0.109 [+0.068, +0.155] | 48/18/54 | +0.095 [+0.052, +0.142] | 32/8/80 | +0.119 [+0.058, +0.182] | 35/12/73 |
| tantivy-pg+prox | +0.087 [+0.043, +0.133] | 46/23/51 | +0.103 [+0.060, +0.150] | 34/7/79 | +0.108 [+0.051, +0.170] | 31/12/77 |

`tantivy-pg` and `tantivy-native` returned the same top 10 for 239 of 240 queries; in the other one (ja-16) two
documents at ranks 2 and 3 swap, which changes no metric. The mode choice therefore matters for matching parity,
not for ranking. The proximity boost does not help overall (nDCG lower than without it, more queries worse).

Per language (Tantivy = `tantivy-pg`):

| language | evaluable queries | pg nDCG@10 | Tantivy nDCG@10 | nDCG diff [95% CI] | pg P@5 | Tantivy P@5 | P@5 diff [95% CI] | pg MRR | Tantivy MRR | MRR diff [95% CI] | nDCG better/worse/same |
|---|---|---|---|---|---|---|---|---|---|---|---|
| en | 18 | 0.639 | 0.890 | +0.251 [+0.112, +0.399] | 0.256 | 0.522 | +0.267 [+0.100, +0.444] | 0.354 | 0.676 | +0.322 [+0.142, +0.514] | 10/1/7 |
| fr | 15 | 0.891 | 0.939 | +0.048 [-0.012, +0.136] | 0.333 | 0.360 | +0.027 [-0.027, +0.093] | 0.647 | 0.733 | +0.087 [-0.091, +0.267] | 3/3/9 |
| de | 15 | 0.859 | 0.940 | +0.081 [-0.020, +0.220] | 0.320 | 0.387 | +0.067 [-0.027, +0.200] | 0.533 | 0.567 | +0.033 [-0.067, +0.133] | 3/3/9 |
| es | 17 | 0.797 | 0.854 | +0.056 [-0.013, +0.131] | 0.365 | 0.377 | +0.012 [-0.082, +0.106] | 0.519 | 0.564 | +0.045 [-0.103, +0.193] | 7/2/8 |
| sv | 14 | 0.928 | 0.940 | +0.013 [-0.024, +0.048] | 0.329 | 0.329 | +0.000 [+0.000, +0.000] | 0.800 | 0.839 | +0.039 [-0.071, +0.150] | 4/2/8 |
| fi | 11 | 0.885 | 0.918 | +0.033 [-0.016, +0.080] | 0.309 | 0.309 | +0.000 [+0.000, +0.000] | 0.826 | 0.886 | +0.061 [-0.045, +0.204] | 4/1/6 |
| ja | 16 | 0.555 | 0.789 | +0.234 [+0.095, +0.387] | 0.163 | 0.325 | +0.163 [+0.087, +0.237] | 0.411 | 0.634 | +0.223 [+0.062, +0.408] | 9/3/4 |
| cy | 14 | 0.701 | 0.802 | +0.101 [-0.058, +0.279] | 0.329 | 0.500 | +0.171 [-0.014, +0.371] | 0.387 | 0.470 | +0.083 [-0.125, +0.316] | 8/3/3 |

nDCG@10 per language for all three engines, with 95% CI:

| language | pg | tantivy-pg | tantivy-native |
|---|---|---|---|
| en | 0.639 [0.480, 0.793] | 0.890 [0.830, 0.944] | 0.890 [0.832, 0.944] |
| fr | 0.891 [0.802, 0.963] | 0.939 [0.881, 0.983] | 0.939 [0.882, 0.983] |
| de | 0.859 [0.717, 0.969] | 0.940 [0.898, 0.978] | 0.940 [0.898, 0.978] |
| es | 0.797 [0.690, 0.895] | 0.854 [0.781, 0.922] | 0.854 [0.781, 0.923] |
| sv | 0.928 [0.860, 0.987] | 0.940 [0.882, 0.987] | 0.940 [0.881, 0.988] |
| fi | 0.885 [0.810, 0.957] | 0.918 [0.840, 0.978] | 0.918 [0.839, 0.981] |
| ja | 0.555 [0.360, 0.738] | 0.789 [0.666, 0.899] | 0.789 [0.668, 0.900] |
| cy | 0.701 [0.549, 0.828] | 0.802 [0.693, 0.896] | 0.802 [0.695, 0.893] |

The point estimate is better or equal for Tantivy in every language and every metric. The difference is clearly
outside chance only in English and Japanese. In the other six languages the per-language samples (11 to 17
evaluable queries) are too small to tell a small gain from no change; none shows a measurable loss.

Per query type:

| type | evaluable queries | pg nDCG@10 | Tantivy nDCG@10 | nDCG diff [95% CI] | pg P@5 | Tantivy P@5 | P@5 diff [95% CI] | pg MRR | Tantivy MRR | MRR diff [95% CI] | nDCG better/worse/same |
|---|---|---|---|---|---|---|---|---|---|---|---|
| keyword | 58 | 0.804 | 0.888 | +0.084 [+0.027, +0.146] | 0.328 | 0.400 | +0.072 [+0.017, +0.131] | 0.603 | 0.683 | +0.080 [-0.001, +0.166] | 20/10/28 |
| phrase | 24 | 0.932 | 0.964 | +0.032 [-0.003, +0.085] | 0.300 | 0.350 | +0.050 [-0.008, +0.125] | 0.861 | 0.948 | +0.087 [+0.004, +0.205] | 5/2/17 |
| boolean | 23 | 0.495 | 0.789 | +0.294 [+0.157, +0.437] | 0.313 | 0.522 | +0.209 [+0.061, +0.374] | 0.257 | 0.603 | +0.346 [+0.178, +0.516] | 18/5/0 |
| citation | 6 | 0.775 | 0.865 | +0.090 [+0.012, +0.212] | 0.067 | 0.167 | +0.100 [+0.000, +0.233] | 0.018 | 0.056 | +0.037 [+0.000, +0.111] | 3/0/3 |
| natural | 9 | 0.853 | 0.875 | +0.022 [-0.042, +0.107] | 0.222 | 0.289 | +0.067 [+0.000, +0.200] | 0.389 | 0.324 | -0.065 [-0.306, +0.139] | 2/1/6 |

The 17 evaluable point-in-time queries (past `as_of`): nDCG pg 0.879, tantivy-pg 0.941, difference +0.062
[+0.001, +0.148].

Only 6 of 24 citation queries and 9 of 96 natural-language questions return anything that can be evaluated, so the
citation and natural rows say little.

Cross-check with the graded relevance lists in the query files (240 queries, written before the runs; unmatched
queries score 0): nDCG@10 0.220 (pg), 0.254 (both Tantivy modes); MRR@10 0.304 and 0.355; queries with a relevant
document in the top 10: 94 and 104. Pairwise nDCG on these lists: Tantivy better on 42 queries, worse on 12, same
on 186. The direction agrees with the blind judging. The low absolute values come from the 109 queries that match
nothing and from target provisions that no engine matched.

### 3.3 Inter-judge agreement

| language | queries | pairs | exact | within 1 | kappa (unweighted) | kappa (linear) | kappa (quadratic) | same side of grade 2 | mean grade, primary / second |
|---|---|---|---|---|---|---|---|---|---|
| overall | 40 | 391 | 0.870 | 1.000 | 0.814 | 0.875 | 0.930 | 0.962 | 1.10 / 1.18 |
| en | 5 | 44 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.39 / 1.39 |
| fr | 5 | 61 | 0.852 | 1.000 | 0.779 | 0.851 | 0.915 | 0.902 | 1.23 / 1.31 |
| de | 5 | 37 | 0.946 | 1.000 | 0.922 | 0.949 | 0.972 | 0.973 | 1.05 / 1.00 |
| es | 5 | 57 | 0.737 | 1.000 | 0.606 | 0.720 | 0.830 | 0.930 | 0.77 / 0.90 |
| sv | 5 | 24 | 0.917 | 1.000 | 0.881 | 0.922 | 0.956 | 0.958 | 1.67 / 1.58 |
| fi | 5 | 43 | 0.977 | 1.000 | 0.955 | 0.971 | 0.983 | 1.000 | 1.44 / 1.47 |
| ja | 5 | 67 | 0.940 | 1.000 | 0.909 | 0.944 | 0.972 | 0.955 | 0.85 / 0.88 |
| cy | 5 | 58 | 0.690 | 1.000 | 0.512 | 0.613 | 0.735 | 1.000 | 0.93 / 1.24 |

Overall linear kappa 0.875, 95% CI [0.797, 0.936] (bootstrap over the 40 queries). Confusion matrix (rows primary
judge, columns second judge):

| | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| 0 | 94 | 30 | 0 | 0 |
| 1 | 3 | 139 | 8 | 0 |
| 2 | 0 | 7 | 60 | 2 |
| 3 | 0 | 0 | 1 | 47 |

No pair differs by more than one grade, and almost all disagreement is 0 against 1 (peripheral against irrelevant),
mostly in Welsh (the second judge gave 1 to repeated "fundamental provision" boilerplate that the primary judge gave
0) and Spanish (decree-law preambles). English agreed on all 44 pairs, which says more about the two instances
reasoning alike than about the grades being right.

Sensitivity: on the agreement queries, the Tantivy gain is the same with either judge's grades (nDCG difference
+0.122 [+0.048, +0.209] with the primary grades on 37 queries, +0.107 [+0.037, +0.188] with the second judge's on
38 queries).

### 3.4 Legal query syntax (89 probes)

26 citations, 27 punctuation and operator inputs, 8 stopword-only, 4 very long, 3 emoji, 15 mixed-script, 4
control or whitespace, 2 without a jurisdiction. Each ran through `search()` and through the lexical stage alone
(budget 100,000).

| measure | pg | tantivy-pg | tantivy-native |
|---|---|---|---|
| Process crashes | 0 | 0 | 0 |
| Errors | 3 | 3 (the same) | 3 (the same) |
| Empty results | 37 | 37 | 36 |
| Match set different from pg | - | 0 | 1 |

- The three errors are identical on every engine: "q must not be empty" (whitespace only) and "q is limited to 500
  characters" are intended validation errors. A NUL byte (`contract\x00law`) raises `psycopg.DataError` in
  `_expansions` on every engine; this is an existing bug that would reach the user as a server error.
- `tantivy-native` differs on one input: `​privacy​` with zero-width spaces finds 0 passages in PostgreSQL and 14 in
  native.
- Inputs that parse identically and match the same passages in every engine: `401(k)`, `6(1)(f)`, `§ 1983`,
  `42 U.S.C. § 1983`, unbalanced quotes, `data -`, `data or -`, `OR OR OR`, `-privacy`, SQL injection text,
  `240.13e-4`, Japanese `OR` and `-`, Welsh `’` against `'`.
- Ranking inside identical match sets differs a lot on short citations. Top-10 overlap with PostgreSQL: `401(k)` 0.2,
  `6(1)(f)` 0.3, `§ 1983` 0.0, `section 6(1)(a)` 0.1, `Ley 39/2015` 0.2, `personal data` without a jurisdiction 0.0.
  These were not judged, so which ranking is better for them is not known.
- Inputs that find nothing on every engine, because every term must match or because of tokenization:
  `Art. 6(1)(f) GDPR`, `§ 823 Abs. 1 BGB`, `s.1(1)`, `art. L121-20`, `SFS 1962:700`, `刑法第199条`, `12000 euros`.
  In the judged set, only 1 of 24 citation queries finds its target on any engine (e.g. sv-28
  `3 kap. 1 § brottsbalken` returns 13 provisions that cite other chapters of the Penal Code, all graded 0).

### 3.5 Point-in-time (682 tests)

294 + 280 version tests (validity boundaries: day before `valid_from`, `valid_from`, day before `valid_to`,
`valid_to`, NULL start, with and without `include_historical` and `include_prospective`) and 50 + 58 status tests,
through `search()` with limit 50.

| measure | pg | tantivy-pg | tantivy-native |
|---|---|---|---|
| Expectation passed | 682/682 | 681/682 | 681/682 |
| Same target presence and returned sibling versions as pg | - | 676/682 | 676/682 |
| Same target rank as pg | - | 581/682 | 581/682 |
| Errors | 0 | 0 | 0 |

- The one failure is pitb-jp-10 (Japanese provision, `valid_to` date 2023-04-01 with `include_historical`). Both
  engines produce the same 152 candidates, target included; PostgreSQL ranks the target 26th, BM25 51st, so limit 50
  cuts it. The validity filter is the same; the ranking is not.
- The other five tests whose output differs from PostgreSQL are also ranking inside identical candidates: st-10
  (2 tests; `include_proposed` true and false give identical lists on Tantivy too, but ordered differently from
  PostgreSQL), pitb-jp-08 (2 tests) and pitb-jp-10 on 2022-03-31. In these, the target is present on both engines,
  but its rank differs (for example 5 instead of 3, or 1 instead of 4) and so does the set of sibling versions that
  falls within the limit of 50.
- No test failed because of `valid_from`/`valid_to` boundaries, a NULL start, `prospective` status or an
  `include_*` flag.
- The dev corpus has no `repealed` or `proposed` passages. Those statuses are covered only by the unit tests in
  `tests/test_tantivy_engine.py`, which set them on a small test corpus.

### 3.6 Filter parity (1,200 randomized requests)

Two runs of 600 requests: run A used the judged queries and syntax probes, run B short broad terms in all 8 languages
so that more requests return something. 19 requests where PostgreSQL hit the 100,000 budget were skipped; 1,181 were
compared field by field (every field of the candidate except the score), 1,192,654 passages in all.

- Jurisdiction scopes: every loaded jurisdiction and none (AU 79, CA 87, DE 59, ES 73, EU 114, FI 103, FR 62, GB 58,
  GB-EAW 62, GB-ENG 43, GB-NIR 67, GB-SCT 63, GB-WLS 80, JP 83, SE 62, US 58, none 28).
- Flags: `include_foreign` 237, `include_historical` 370, `include_prospective` 327, `include_proposed` 368,
  `include_case_law=false` 247, language filters in many combinations, `as_of` on validity boundaries plus or minus
  one day.
- `tantivy-pg`: 1181/1181 identical (307/307 non-empty), 0 passages only in PostgreSQL, 0 only in Tantivy, 0 field
  differences, 0 errors.
- `tantivy-native`: 1166/1181 identical (292/307 non-empty), 26 passages only in PostgreSQL, 32 only in native, 0
  field differences. Every difference is a tokenizer gap, not a filter: a number and a word joined by a dot
  (`1.Cash`, `0022.Kursreserven`, `3.Dos`, `20.Schedule`, `1.ade`), minus signs inside hyphenated numbers
  (`-30-consecutive`, `NQA-2a-1990`, `-742.225.1`), slash paths (`/Deletion`, `/1990-1995`), dotted chains
  (`A.III.1`) and the zero-width space. Example: request "RL 28:1" (FI, 2016-06-23) finds 2 passages in PostgreSQL
  and 0 in native.
- Weak spots of this test: 874 of the 1,181 requests return nothing on either engine; only 7 requests matched
  Japanese passages; the corpus has no treaties, no case law and no regional-organisation instruments other than
  the EU's, so those branches of the scope filter are covered only by the unit tests on the small test corpus.

### 3.7 Size

| item | size |
|---|---|
| Tantivy index, pg mode (2,519,798 docs, 1 segment) | 813.9 MB, built in 435 s |
| Tantivy index, native mode | 813.6 MB, built in 571 s |
| PostgreSQL `passages` table in total | 4,177 MB (heap 2,710, TOAST 848, indexes 620) |
| of which `tsv` column (sum of `pg_column_size`) | 1,655 MB |
| of which GIN index on `tsv` | 347 MB |
| PostgreSQL lexical stage (`tsv` + GIN) | about 2,003 MB |
| Whole database | 8,608 MB |

Tantivy pg-mode index by field: `all` (positions, used for matching) 452 MB, `a` 157 MB, `b` 56 MB, id columns about
113 MB, everything else about 25 MB. One index serves all jurisdictions, so no per-jurisdiction split is available;
PostgreSQL's `tsv` by jurisdiction is US 1,299 MB, JP 105, CA 69, ES 39, AU 35, DE 21, FR 20, GB 19, EU 16, FI 14,
GB-WLS 9.5, SE 6.1, GB-NIR 1.5, GB-SCT 0.6.

Replacing `tsv` and its GIN index with one Tantivy index would save about 1.19 GB on this database (2,003 MB to
814 MB, 59% of the lexical stage, about 14% of the whole database). Only one of the two modes would be deployed. The
saving needs work that is not done: the pg-mode build currently reads `passages.tsv`, so it would have to compute
`to_tsvector` during the build instead before the column could be dropped (section 5).

### 3.8 Latency

Warm median of 3 runs after one warm-up per query and engine, nothing else running. No cold-cache measurement.

| measure | pg | tantivy-pg | tantivy-native |
|---|---|---|---|
| Lexical stage p50 / p95 | 9.8 / 69.5 ms | 4.0 / 10.3 ms | 2.4 / 8.7 ms |
| Full `search()` p50 / p95 | 775 / 936 ms | 761 / 911 ms | 762 / 907 ms |

Lexical p50 per language, pg / tantivy-pg / tantivy-native (ms): en 14.6 / 4.7 / 3.2, fr 11.8 / 4.2 / 2.6, de 8.3 /
3.8 / 2.2, es 8.9 / 4.2 / 2.7, sv 7.7 / 3.9 / 2.3, fi 5.6 / 3.5 / 2.0, ja 62.1 / 3.5 / 2.2, cy 9.5 / 4.1 / 2.6. The
largest gain is Japanese, where PostgreSQL evaluates an OR of bigram phrases.

About 0.7 s of every `search()` call, on every engine and even with no candidates, is spent in
`ulis/retrieval/coverage.py`, which aggregates over the whole `passages` table on every call (the
`SELECT DISTINCT jurisdiction_id, language` at line 29 is a full scan of 2.5 million rows). The lexical stage is
1-2% of `search()` time, so changing the engine changes end-to-end latency by about 2%.

## 4. Gate verdicts

"vs PostgreSQL" is the switch question: does the engine keep or improve today's behaviour? "Absolute" notes problems
that every engine has, PostgreSQL included.

| gate | tantivy-pg | tantivy-native | evidence | absolute (all engines) |
|---|---|---|---|---|
| 1. Same or better recall | pass (thin at the budget cut) | partial | pg mode: identical match sets on 240/240 queries, 89/89 probes, 1181/1181 filter requests; target provisions in the 200 candidates 0.292 against 0.274. Native: 15/1181 filter requests differ (26 passages lost, 32 gained) from tokenizer gaps | 45% of queries match nothing (81% of natural-language questions) |
| 2. Same or better ranking | pass (LLM judges, not lawyers) | pass (LLM judges, not lawyers) | nDCG@10 +0.109 [+0.066, +0.154], P@5 +0.095 [+0.052, +0.142], MRR +0.119 [+0.059, +0.181]; better or equal point estimate in all 8 languages and all 5 query types except MRR on natural questions (9 queries, CI spans 0); 18 of 120 queries worse; judge agreement linear kappa 0.875; same result with the second judge's grades | ranking of short citations differs widely between engines and was not judged |
| 3. Robust legal query syntax | partial | partial | no crash, no engine-specific error; pg mode parses and matches all 89 probes like PostgreSQL; native differs on 1 (zero-width space) | citation queries fail on every engine (1 of 24 judged; `Art. 6(1)(f) GDPR`, `§ 823 Abs. 1 BGB` find nothing); NUL byte gives a server error on every engine |
| 4. Identical point-in-time behaviour | partial | partial | 681/682; candidate sets differ in 3 Japanese cases at the 200 budget (ranking, see corrections), no fi tests, no boundary, NULL-start, status or flag failure; 6 tests return a different order, 1 (pitb-jp-10) drops the target below limit 50 because of BM25 ranking; `repealed`/`proposed` only in unit tests | - |
| 5. Identical filters | partial | partial | pg mode 1181/1181 identical, 0 field differences; native's 15 differences are tokenizer gaps, not filters (counted under gate 1); treaties, case law and non-EU regional organisations are not in the corpus and were tested only on the unit-test corpus; 7 requests matched Japanese | - |
| 6. Multilingual (en fr de es sv fi ja cy) | partial (per-language non-inferiority not shown) | partial | pg mode: identical matching in every language, nDCG gain significant in en and ja, positive but within noise in the other six, no language worse; native: tokenizer gaps seen in en, de, es, fi | per-language samples are small (11-18 evaluable queries); Japanese filter coverage thin |
| 7. Size and latency | pass: about 1.19 GB smaller lexical stage (not yet realisable, section 6 item 4); lexical p50 2.5x faster, p95 6.7x; end-to-end 2% faster | pass: same size; lexical p50 4x faster, p95 8x; end-to-end 2% faster | sections 3.7 and 3.8; warm cache only, no load test | end-to-end latency dominated by `coverage.py` full scan |

## 5. Failures, with examples

Engine-specific (would be introduced or changed by a switch):

- **Per-query ranking regressions.** 18 of 120 evaluable queries have lower nDCG on Tantivy. The largest: cy-15
  `Comisiynydd Cenedlaethau'r Dyfodol swyddogaethau` 0.809 to 0.281 (PostgreSQL has the four grade-3 s.19(1) items
  that list the Commissioner's functions in its top 10; Tantivy has none of them and ranks Schedule 2 passages that
  only mention the functions instead); es-27 `divorcio OR separación matrimonial requisitos` 0.877
  to 0.614; en-23 `unsolicited commercial electronic messages consent` 0.856 to 0.675; de-12
  `Gewerbesteuer Hebesatz Gemeinde` 1.000 to 0.821; sv-26 `testamente vittnen -inbördes` 1.000 to 0.834.
- **Point-in-time ranking.** pitb-jp-10: the requested historical version is in the candidates but ranked 51st by
  BM25 (26th by PostgreSQL), so limit 50 drops it.
- **Native tokenizer gaps** (native mode only): `1.Cash`, `0022.Kursreserven`, `-30-consecutive`, `NQA-2a-1990`,
  `/Deletion`, `A.III.1`, zero-width space; "RL 28:1" finds 2 passages in PostgreSQL and 0 in native.
- **No incremental index maintenance.** `scripts/search_eval/build_index.py` builds from scratch or appends passages
  with a higher id. It does not update or delete changed passages, so the index would go stale after any re-ingest.

Shared by every engine (not caused by Tantivy, not fixed by it):

- 109 of 240 judged queries return nothing. Example: en-01 (FMLA medical certification question), sv-01 to sv-11,
  fi-01 to fi-08: every term of a natural-language question must match.
- Citation queries: 1 of 24 judged citation queries finds its target. sv-28 `3 kap. 1 § brottsbalken` returns
  provisions that cite other chapters; de-21 `§ 823 Abs. 1 BGB`, es-28 `art. 1902 CC` and fi-29 return nothing;
  cy-28 and cy-30 return provisions related to the cited section but not the section itself.
- `contract\x00law` raises `psycopg.DataError` in `_expansions`.
- About 0.7 s per `search()` call in `ulis/retrieval/coverage.py`.
- Corpus metadata questions raised by the judges (not verified here): the UK Data Protection Act 1998 s.7 version is
  dated in force from 1998-07-16, although the Act commenced on 1 March 2000; provisions of Spain's LO 15/1999 and
  Ley 4/1999 are marked `in_force` although both laws were repealed; the GDPR text is dated from 2016-05-04 (entry
  into force) while it applied from 2018-05-25.

## 6. What would need fixing before any switch

1. Lawyer review of a sample of the judgments (at least the launch languages), and judging of short citation queries
   such as `401(k)` and `§ 1983`, where the two rankings disagree most and nothing was judged.
2. Review the 18 queries where BM25 ranks worse, and the pitb-jp-10 case, and decide whether the ranking needs a
   correction (for example a length or authority signal before the budget cut) or a larger budget.
3. Incremental index maintenance on ingest: update and delete changed passages, not only append, with a consistency
   check against PostgreSQL after each load.
4. To actually save the space: make the pg-mode build compute `to_tsvector` itself (it reads `passages.tsv` today),
   then drop the `tsv` column and the GIN index in a migration; keep a fallback path or a rebuild procedure.
5. If native mode is wanted (no PostgreSQL in the lexical stage): close the tokenizer gaps listed above. Otherwise
   use pg mode, which matched PostgreSQL exactly in every test.
6. Operations: packaging and supervising the Rust helper process, index backup and restore, cold-cache and
   concurrent-load latency, behaviour when the index is missing or older than the database.
7. Test treaties, case law and non-EU regional organisations on real data once such data is loaded, and run the
   filter test again with more Japanese matches.

Independent of the engine, and with a much larger effect on users than the engine choice: the all-terms-must-match
behaviour (45% empty results), citation parsing, the `coverage.py` full scan (0.7 s per call) and the NUL-byte server
error.

## 7. Size and latency gains measured

- Lexical-stage storage: about 2.0 GB (`tsv` 1,655 MB + GIN 347 MB) against 0.81 GB for one Tantivy index: about
  1.19 GB saved, 59% of the lexical stage, about 14% of the 8.6 GB database. Not yet realisable without item 4 of
  section 6.
- Lexical-stage latency (warm): p50 9.8 ms to 4.0 ms (pg mode) or 2.4 ms (native); p95 69.5 ms to 10.3 or 8.7 ms;
  Japanese p50 62 ms to 3.5 or 2.2 ms.
- End-to-end `search()` latency: 775 ms to 761 ms at p50 (about 2%), because of `coverage.py`.

## 8. Reproducing

Scratch scripts and raw outputs were kept outside the repository (session scratch directory `eval/`):
`qc/` (`qrun.py`, `synrun.py`, `pitrun.py`, `equiv.py`, `pools.py`, `analyze.py`), raw runs in `run/*.jsonl`, blind
batches and key in `judge/`, judgments in `judgments/`, final metrics script `final/metrics.py` and its output
`final/metrics.json`. The index builder and the engine are in the repository: `scripts/search_eval/build_index.py`,
`scripts/search_eval/run_search.py`, `ulis/retrieval/tantivy_engine.py`, `tests/test_tantivy_engine.py`.
