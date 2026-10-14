# Test results

Recorded on 2026-10-14 in the build container: Python 3.13.16, PostgreSQL 16.15, Node 22, Chromium
(Playwright 1.56.1). These are the actual outputs of the runs, not targets. Older per-corpus sections below
keep their original numbers; the first table is the current state.

## Backend (`Blue-Cat-Backend`), current

| Check | Command | Result |
|---|---|---|
| Lint, format, types | `ruff check`, `ruff format --check`, `mypy ulis` | clean (mypy: no issues in 133 source files) |
| Config | `python -m ulis.cli validate-config` | 34 jurisdictions, 31 sources |
| Migrations | `alembic upgrade head` (0001-0006: core schema, validity basis, valid-from basis, expression short title, search text for unspaced scripts, research jobs) | applied cleanly |
| Full suite (PostgreSQL) | `ULIS_TEST_DATABASE_URL=... pytest -q` | **737 passed** in 284 s |

Real data in the development database: 26 jurisdiction ids, 2,463 instruments, 741,226 provisions, 961,804
passages, 3,696 source documents, all re-derived from raw bytes by `verify-sources` (`cli status`). The
fictional jurisdictions (XA, XA-NP, XB, XR) are invented test data.

Known gaps and conflicts, stated rather than hidden:

* Canada: 953 Acts in both languages ingested with **0 errors and 2 conflicts** (a duplicate provision identifier
  in N-16.62, section 15.1-2, in both languages); the second occurrence is not stored.
* Germany: a 1,101-law trial from the download cache ingested in 191 s: 1,064 laws, about 102,000 provisions, 27
  laws that contain no text skipped and counted (not errors), **0 errors**. The full 6,139-law load has not been run
  (the download is at one request per 5 s, at least 8.5 h).
* Not run here: the Docker image, `docker compose up`, Caddy, systemd, the real language-model path (no API key in
  the sandbox) and any load test. The research pipeline was run end to end with a scripted stand-in for the model on
  the real database (verifier removes invented claims), and the website was run against the real API in the same
  way (login, job, polling, result).
* Search quality is uneven: Spanish ranking is weak because the instrument title in the context boosts every
  provision of that law; Japanese and Chinese search uses character pairs (no morphological analysis).

## Backend, older breakdown (at the Canada stage)

| Check | Command | Result |
|---|---|---|
| Config | `python -m ulis.cli validate-config` | 15 jurisdictions, 8 sources, 4 concepts |
| Full suite (PostgreSQL) | `ULIS_TEST_DATABASE_URL=... pytest -q` | 117 passed in about 23 s |

Tests per file at that stage: `test_catalog.py` 14 · `test_parser_and_storage.py` 11 · `test_rules.py` 13 ·
`test_ingestion.py` 8 · `test_search.py` 17 · `test_lookup_and_verification.py` 7 ·
`test_api.py` 7 · `test_uslm.py` 15 · `test_us_federal.py` 10 (real US documents, fixtures in
`tests/fixtures/uslm`) · `test_legislation_gov_uk.py` 10 (real UK documents, fixtures in
`tests/fixtures/legislation_gov_uk`) · `test_justice_canada.py` 5 (real Canadian documents in
both languages, fixtures in `tests/fixtures/justice_canada`). Every country added since has its own test
file named after its source (`tests/test_<source>.py`), plus the research assistant (`test_assistant_*.py`),
API hardening (`test_security.py`) and operations (`test_ops_cli.py`).

## Real Canadian corpus (dev database, full manifest)

| Step | Result |
|---|---|
| `ingest ca-justice-laws-xml` (sparse partial clone of the pinned commit: 18 files, 28 MB) | 18 expressions (9 Acts × English and French), 9 instruments, 47,747 provision versions, 42,883 passages, 2,275 citation links (632 resolved inside the corpus), **0 errors, 0 conflicts**, 102 s |
| `verify-sources` (all four sources) | **287/287 documents pass**; 456,175 units re-derived from raw bytes in 101 s |
| Search latency (CA, about 139k passages in total) | 0.12–0.28 s per query |
| Spot checks (search, CA, 2026-07-01) | "first degree murder" → Criminal Code s 610(3), s 662(2); "copyright infringement damages" → Copyright Act s 35(1), s 38.1(6)(c); "divorce one year separation" → Divorce Act s 8(2)(a); "droit d’accès aux documents" (French) → Loi sur l’accès à l’information, art 4(1) |

### Defects found on Canadian data (and fixed, with tests)

1. French hits were cited with the English title ("Access to Information Act, LRC 1985 ...")
   because citations used the work's title. Each expression now keeps its own short title
   (migration 0004).
2. Paragraph labels and defined terms in `num_path` came from whichever language was ingested
   first. Labels are now language-neutral and the Canadian module renders `(a)` in English
   and `a)` in French, with the defined term in the citation's language.
3. Labels beginning with a footnote reference produced the empty identifier `section-`.
4. The same term defined twice in one subsection produced duplicate identifiers.
5. Acts in supplements to the Revised Statutes were cited as "SC 1985" instead of
   "RSC 1985, c 3 (2nd Supp.)".

## Real UK corpus (dev database, full manifest)

| Step | Result |
|---|---|
| Fetch (`legislation_gov_uk` connector, identified User-Agent, 5 s crawl delay, cache) | 161 point-in-time documents for 9 works (195 MB cached); no request refused or throttled |
| `ingest gb-legislation-gov-uk` (from the cache, fresh database) | 159 versions (2 dates resolved to an already-fetched version), 9 instruments, 38,851 provision versions created, 301,080 corroborated by later versions, 64 restated wordings dated by observation, 35,286 passages, **0 errors, 0 conflicts**, 214 s |
| `verify-sources` (all three sources, fresh database) | **269/269 documents pass**; 408,428 units re-derived from raw bytes in 86 s |
| Search latency (GB-ENG, about 96k passages in total) | 0.27–0.35 s per query (single node, no tuning) |

### Defects found on real UK data (and fixed, with regression tests or census)

1. Validity and extent restrictions sit inside `<analysis>`, not directly in `<meta>`; the
   first parse attached no dates or extent to any provision.
2. A period with an end but no start ("to 2007-01-15") was read as "no start date", so
   section 15 of the Theft Act appeared "not yet in force" in 2006. It now starts at the
   revised data's basedate (1 February 1991 for older Acts).
3. Alternative wordings with an empty period (replaced on the day they were due to commence)
   broke the interval constraint (7 documents rejected). They never applied and are skipped.
4. 308 "conflicts" on the first full run, found by an offline census of every provision across
   versions: forecast wordings dated after their own version (amended before commencement),
   container texts dated by the container's period only, repealed text shown as dots in some
   versions and omitted in others, and genuine restatements. Rules for each are in
   [temporal-model.md](temporal-model.md#point-in-time-series-legislationgovuk).
5. "Not authentic text" was shown for the English text of UK law; the revised-text notice
   already covers what is meant.

## Real US federal corpus (dev database, full manifest)

| Step | Result |
|---|---|
| `ingest us-gpo-uslm-samples` (clone of pinned GPO commit + parse + persist + index) | 96 documents, 299 instruments, 310 versions, 68,373 provision versions, 29,257 citation links (7,690 resolved inside the corpus), 60,488 passages, **0 errors, 0 conflicts**, about 8 min |
| Excluded at manifest build (with reasons) | 12 files: engrossed amendments (3), bill stages with no dated action (7), 1 bill without type/stage, 1 compilation without an approval date |
| `verify-sources` (all sources) | **110/110 documents pass**; 68,497 units re-derived from raw bytes in 23 s |
| Search latency on about 60k passages | 0.06–0.33 s per query (single node, no tuning) |

### Defects found on real data (and fixed, with regression tests)

1. GPO data tags "§ 226.5a" and "§ 226.5b" with `value="226.5"`; the parser now prefers the
   printed number when it is a more specific form of the value.
2. Public Law 113-287 embeds a whole new US Code title inside section 3 without quotation
   markup; the parser had turned the quoted sections into provisions of the Act. Structural
   containers nested inside a provision are now treated as text of that provision.
3. That 438,000-character section then outranked focused provisions; ranking is now
   length-normalised.
4. False friend: English "conservation" triggered the French label *conservation* (=
   retention). Concept matching can now be restricted to the question's language
   (`query_language`).

### Defects found by the tests during development (and fixed)

1. `ingestion_runs.status` was `VARCHAR(20)`, too short for `completed_with_errors`. A run
   with rejected documents failed to record itself. Widened to 32; migration regenerated.
2. A test expected one conflict when a subsection's text was altered. The pipeline
   correctly reported two: the subsection *and* its containing section, whose composite
   text also changed. The test was corrected.
3. A test assumed an `XA` search returns only `XA`/`XR` material. Provincial (`XA-NP`)
   *case law* is correctly in scope because it is the same national court system (and is
   flagged as overruled). Provincial *legislation* is correctly excluded. The test now
   asserts exactly that.
4. The coverage page showed that the XA profile's known-gaps text over-counted XA
   judgments. It was corrected under a profile version bump (1.0.0 → 1.0.1). Both versions
   are retained, and a test now enforces that changed content requires a version bump.

### End-to-end CLI run (fresh database)

```
$ python -m ulis.cli ingest ulis-fictional-sample
documents_seen 14, documents_new 14, instruments_created 7, instrument_versions_created 11,
provision_versions_created 62, provision_versions_corroborated 24, judgments_created 3,
paragraphs_created 16, citation_links_created 14, citation_links_resolved 14,
passages_indexed 63, conflicts [], errors []

$ python -m ulis.cli ingest ulis-fictional-sample      # second run
documents_new 0, documents_unchanged 14                 # idempotent

$ python -m ulis.cli verify-sources
pass  (all 14 source documents; 102 units re-derived from raw bytes)

$ python -m ulis.cli ingest in-india-code               # exit code 2
refused: source 'in-india-code' may not be ingested: source status is 'planned'; licence review
status is 'pending_review'; licence does not permit automated_processing ('unknown'); licence
does not permit bulk_download ('unknown'); no connector/parser configured

$ python -m ulis.cli search "retain personal data" --jurisdiction XA --as-of 2020-06-01 --limit 2
1. Personal Data Act 2015 (XA) s 12(1)  [Binding: legislation in force]
   "A controller shall not retain personal data for longer than seven years after the purpose
    for which it was collected has been fulfilled."
   ! FICTIONAL_DATA ...
   ! LATER_VERSION_EXISTS: This provision was replaced by a later version with effect from 2022-01-01.
   ! TREATMENT_OF_OTHER_VERSION: ... Minerva Holdings Ltd v Information Commissioner [2023] XASC 7 [1] ...
   ! CITATOR_COVERAGE: ... Absence of indexed treatment is not evidence that no court has considered this provision.
2. Personal Data Act 2015 (XA) s 12(2) ...
* INFO SUBNATIONAL_LEGISLATION_EXCLUDED: Legislation of XA-NP applies only within those units ...
* INFO QUERY_EXPANDED: ... Shared vocabulary does not imply that provisions have equivalent legal effect.
```

### What the integration tests demonstrate

| Requirement | Evidence (test) |
|---|---|
| Document ingestion, licence-gated, idempotent, non-destructive | `test_sample_corpus_ingested`, `test_reingestion_is_idempotent`, `test_conflicting_text_is_recorded_not_overwritten`, `test_invalid_documents_are_rejected_and_run_continues`, `test_licence_gate_blocks_planned_sources` |
| Jurisdiction-aware search | `test_jurisdiction_hierarchy_and_territorial_extent` (provincial Act found for XA-NP, excluded for XA-SP and XA), `test_foreign_law_excluded_unless_requested_and_never_binding`, `test_treaty_domestic_effect_follows_profile` (dualist XA vs monist XB; no treaty before entry into force) |
| Temporal correctness | `test_returns_the_version_in_force_on_the_date` (including the 2021-12-31 / 2022-01-01 boundary), `test_what_law_applied_on_the_date`, `test_repealed_prospective_and_not_yet_in_force` |
| Exact source citations | `test_every_hit_is_an_exact_source_citation`: every hit's text is re-parsed from the raw stored bytes at its locator and compared verbatim; hashes match |
| Legal-status limitations | Bills (`PROPOSED_NOT_LAW`), prospective (`NOT_IN_FORCE`), outstanding changes, currency, judicial treatment of other versions, overruled precedent (`NEGATIVE_TREATMENT`), unindexed jurisdiction (`NO_CORPUS`, `PROFILE_UNVERIFIED`), missing jurisdiction (clarification) |
| Precedent rules | `test_precedent_and_negative_treatment` (Supreme Court binding on a District Court forum; overruled High Court decision) |
| Multilingual | `test_cross_lingual_concept_expansion_and_parallel_versions`, `test_french_query_cites_french_text`, `test_language_selection` |
| Verification | `test_judgment_quotation_checked_against_the_version_then_in_force`, `test_source_integrity_passes_for_every_document`, `test_source_integrity_detects_tampering` |
| Security | `test_health_is_public_but_data_requires_a_key`, `test_refuses_to_start_without_keys_outside_development`, `test_search_endpoint` (audit stores a query hash, not the text), parser rejects XXE and entity expansion |

## Frontend (`Blue-Cat-Web`)

| Check | Result |
|---|---|
| `tsc --noEmit` | Pass |
| `vitest run` | 8 passed |
| `next build` (Next 15.5.27) | Compiled successfully; routes `/`, `/provision`, `/coverage` |
| `playwright test` against the live API + production server | **9 passed**: date-dependent version with exact text and limitations; point-in-time timeline; India "no corpus" behaviour; real US public law (citation, presumed effective date, absent before enactment); real CFR text flagged as attested only at its edition date; real UK law (England vs Scotland extent; Theft Act s 15 in force in 2006 and repealed in 2008); real Canadian law in English and French (citations in each language, reproduction notice, earlier wording reported as a history gap); cross-lingual grouping of authentic versions; coverage and licence register |

## Not verified

- `Dockerfile` and `docker-compose.yml` were written but not built: no Docker daemon was
  available in the build container.
- The GitHub Actions workflows have not run yet (they will on the first push).
- Three real sources have been tested (US GPO USLM samples; legislation.gov.uk; Justice
  Canada XML). India Code refuses automated access (HTTP 403); other portals' licences are
  pending review.
- Canadian earlier versions (point-in-time history on the Justice Laws website) and
  regulations are not ingested.
- Welsh-language texts of Welsh legislation are not ingested.
- Parser accuracy on real data has not been measured against a hand-checked sample.
- Ranking quality has not been evaluated: there is no expert-built evaluation set yet.
