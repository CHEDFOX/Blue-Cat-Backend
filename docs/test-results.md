# Test results

Recorded on 2026-10-14 in the build container: Python 3.13.16, PostgreSQL 16.15, Node 22,
Chromium (Playwright 1.56.1). These are the actual outputs of the runs, not targets.

## Backend (`Blue-Cat-Backend`)

| Check | Command | Result |
|---|---|---|
| Lint | `ruff check ulis tests migrations scripts` | All checks passed |
| Format | `ruff format --check ...` | 55 files already formatted |
| Types | `mypy ulis` | No issues in 43 source files |
| Config | `python -m ulis.cli validate-config` | 7 jurisdictions, 6 sources, 4 concepts |
| Migrations | `alembic upgrade head` / `downgrade base` / `upgrade head` | Applied, rolled back and re-applied cleanly |
| Unit tests (no DB) | `pytest -q` without `ULIS_TEST_DATABASE_URL` | 37 passed, 39 skipped (DB tests skip cleanly) |
| Full suite (PostgreSQL) | `ULIS_TEST_DATABASE_URL=... pytest -q` | **76 passed** in about 7 s |

Tests per file: `test_catalog.py` 14 · `test_parser_and_storage.py` 11 · `test_rules.py` 12 ·
`test_ingestion.py` 8 · `test_search.py` 17 · `test_lookup_and_verification.py` 7 ·
`test_api.py` 7.

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
| `playwright test` against the live API + production server | **5 passed**: date-dependent version with exact text and limitations; point-in-time timeline; India "no corpus" behaviour; cross-lingual grouping of authentic versions; coverage and licence register |

## Not verified

- `Dockerfile` and `docker-compose.yml` were written but not built: no Docker daemon was
  available in the build container.
- The GitHub Actions workflows have not run yet (they will on the first push).
- Nothing was tested against real legal sources. Their hosts were blocked by the
  environment's network policy, and their licences are pending review.
- Ranking quality has not been evaluated: there is no expert-built evaluation set yet.
