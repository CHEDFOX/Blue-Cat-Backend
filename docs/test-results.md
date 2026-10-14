# Test results

Recorded on 2026-10-14 in the build container (updated after adding the UK corpus): Python 3.13.16, PostgreSQL 16.15, Node 22,
Chromium (Playwright 1.56.1). These are the actual outputs of the runs, not targets.

## Backend (`Blue-Cat-Backend`)

| Check | Command | Result |
|---|---|---|
| Lint | `ruff check ulis tests migrations scripts` | All checks passed |
| Format | `ruff format --check ...` | 69 files already formatted |
| Types | `mypy ulis` | No issues in 49 source files |
| Config | `python -m ulis.cli validate-config` | 14 jurisdictions, 7 sources, 4 concepts |
| Migrations | `alembic upgrade head` (0001 core schema, 0002 validity basis, 0003 valid-from basis) | Applied cleanly |
| Unit tests (no DB) | `pytest -q` without `ULIS_TEST_DATABASE_URL` | 59 passed, 53 skipped (DB tests skip cleanly) |
| Full suite (PostgreSQL) | `ULIS_TEST_DATABASE_URL=... pytest -q` | **112 passed** in about 17 s |

Tests per file: `test_catalog.py` 14 · `test_parser_and_storage.py` 11 · `test_rules.py` 13 ·
`test_ingestion.py` 8 · `test_search.py` 17 · `test_lookup_and_verification.py` 7 ·
`test_api.py` 7 · `test_uslm.py` 15 · `test_us_federal.py` 10 (real US documents, fixtures in
`tests/fixtures/uslm`) · `test_legislation_gov_uk.py` 10 (real UK documents, fixtures in
`tests/fixtures/legislation_gov_uk`).

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
| `playwright test` against the live API + production server | **8 passed**: date-dependent version with exact text and limitations; point-in-time timeline; India "no corpus" behaviour; real US public law (citation, presumed effective date, absent before enactment); real CFR text flagged as attested only at its edition date; real UK law (England vs Scotland extent; Theft Act s 15 in force in 2006 and repealed in 2008); cross-lingual grouping of authentic versions; coverage and licence register |

## Not verified

- `Dockerfile` and `docker-compose.yml` were written but not built: no Docker daemon was
  available in the build container.
- The GitHub Actions workflows have not run yet (they will on the first push).
- Two real sources have been tested (US GPO USLM samples; legislation.gov.uk). India Code
  refuses automated access (HTTP 403); other portals' licences are pending review.
- Welsh-language texts of Welsh legislation are not ingested.
- Parser accuracy on real data has not been measured against a hand-checked sample.
- Ranking quality has not been evaluated: there is no expert-built evaluation set yet.
