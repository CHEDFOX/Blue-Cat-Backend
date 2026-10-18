# United States federal regulations: the eCFR (all CFR titles, current text)
Status: **all 50 CFR titles ingested and verified (2026-10-18): 8,124 parts, 1,942,069 provision versions, 8,124/8,124 re-derived from the stored files; US profile still draft unverified**.
Research aid, not legal advice. The eCFR is an *unofficial* editorial compilation.

| | |
|---|---|
| Source | `config/sources/us-ecfr.yaml` (`us-ecfr`, active); manifest `config/manifests/us-ecfr.yaml` (50 titles, 49 files, title 35 reserved) |
| Connector / parser | `ulis/sources/connectors/ecfr.py` (`ecfr`, on `PoliteHttp`); `ulis/sources/parsers/ecfr.py` (`ecfr_xml` 1.1.0); citations in `ulis/jurisdictions/modules/us.py` (`us` 1.2.0) |
| Tests | `tests/test_ecfr.py` (25 tests, 5 on PostgreSQL); fixtures `tests/fixtures/ecfr/` (title 1 whole, parts of titles 12 and 17) |

## Source and licence (read 2026-10-14)

* **The eCFR API is not used.** [ecfr.gov/robots.txt](https://www.ecfr.gov/robots.txt) disallows, for every robot,
  `/api/versioner/v1/full/` (full title XML, the endpoint for dated versions) and `/api/renderer/v1/content/`.
  The API documentation page returned HTTP 403 ("Your IP address has been blocked", Support Code 02) to the
  sandbox; it was not retried or worked around, so the eCFR's own terms could not be read. Only
  `/api/versioner/v1/titles.json` (not disallowed) was fetched once, for the title list.
* **Source used:** GPO's bulk-data collection of the same compilation,
  [govinfo.gov/bulkdata/ECFR](https://www.govinfo.gov/bulkdata/ECFR): `title-<N>/ECFR-title<N>.xml`, current files
  only. [govinfo robots.txt](https://www.govinfo.gov/robots.txt) does not disallow `/bulkdata` (no Crawl-delay).
* **Terms:** the [eCFR XML User Guide](https://www.govinfo.gov/bulkdata/ECFR/resources/ECFR-XML-User-Guide.pdf) (1.2):
  "It is not an official legal edition of the CFR"; the XML is "not part of the official on-line format", not
  digitally signed; "there are no restrictions on re-use ... because U.S. Government works are not subject to
  copyright"; re-publishers may not use the NARA seal or stylized CFR logos. [GovInfo notice](https://www.govinfo.gov/about/policies):
  17 U.S.C. 105. Licence **approved**; counsel review still recommended.

## What is ingested

One instrument per CFR part, `/us/cfr/t<title>/p<part>` (`instrument_type` `regulation`). The connector splits each
title file into parts (the part's bytes exactly as received inside a small envelope with the file's URL, SHA-256,
Last-Modified and the volume's `AMDDATE`), so memory is bounded by one title file and an unchanged part is
recognised on a later run. Provisions: sections (`sec_220.1`), appendices (`app_A`) and, derived from the printed
numbering (a, 1, i, A, italic 1, italic i), paragraphs (`sec_220.1__para_b__para_3__para_ii`; the same eIds as the
annual-edition sample, so `/us/cfr/t12/s220.1/b/3/ii` resolves). Notes: source credit (`CITA`), section authority,
editorial notes, footnotes. The part's authority statement is `ext.cfr_authority`. Not ingested: reserved sections,
ranges and parts, tables of contents, graphics (a note says where they were), subpart "Source:" notes.

## Dates, versions and legal status

* `validity_basis = attested_at_edition`: each part is attested as in force on the date of its file, the
  `Last-Modified` GPO sends (UTC), kept in `version_date`/`currency_date`; no start date is invented
  (`ATTESTED_ONLY_AT`, and `AS_OF_AFTER_CURRENCY` after that date). A newer file whose wording differs is recorded from
  its own date (`valid_from_basis = observed`), unchanged wording is corroborated: a re-run builds a short history.
* **No dated snapshots:** the versioner API is closed to robots and GPO keeps only the current file, so
  `ulis/ingestion/snapshots.py` is not used; earlier dates carry the current-only limitation, as for the US Code.
* `text_origin = official_consolidation`, `authoritative = false`; the legal edition is the annual CFR (PDF/text).
  Authority: a regulation is `binding_regulation` (1.2, below statutes at 1.25) when a date is known; attested-only text
  (statutes and regulations alike) is `applicability_unverified` (0.9). Core unchanged.

## Citations

`12 CFR 220.1`, `12 CFR 220.1(b)(3)(ii)`, `12 CFR part 302, appendix A` (the Federal Register's own form, no year:
the eCFR is not a printed edition). The 2018-2019 annual-edition sample keeps `27 C.F.R. § 478.11(b) (2018)`.
## Results (full load, 2026-10-18)

The first load of the whole manifest, on a fresh database in the build container (4 cores, cached files after the
first download: 49 files, 787 MB on disk; 49 requests at the 5 s delay plus the transfer).

* **Three defects that only a full load shows**, each fixed with a regression test before the final run:
  1. title 24 holds a `<DIV5 TYPE="PART">` with no number whose only content is the heading "Appendixes A-C to
     Subtitle A [Reserved]"; it stopped the whole title ("unexpected part number ''"). It is an empty part now.
  2. a part printed across several volumes (26 CFR part 1 in 15 volumes; 29 CFR 1910; 40 CFR 52, 60, 63; 41 CFR
     50-128 and 301-304; 50 CFR 17: 17 parts) came as several envelopes with the same version id: the first chunk
     was stored and 189 later chunks refused as "already recorded from different source bytes". The connector now
     yields one envelope per part number and the parser reads the sections of every chunk.
  3. title 48 (the Federal Acquisition Regulations System) numbers its sections "1.000", "52.212-4" without the
     section sign the parser required: all 817 parts of the title parsed as empty and were skipped. Bare numbers
     and "Sec. 1-1" / "Section 01" labels are sections now (parser 1.1.0); bare ranges stay reserved blocks.
* **Final state** (`ingest us-ecfr` twice: the 49 titles, then title 48 after fix 3; `status`): 8,126 parts seen,
  **8,124 instruments and versions, 1,942,069 provision versions, 1,812,535 passages, 0 errors, 0 conflicts**;
  2 parts skipped as reserved without text (48 CFR parts 1042 and 2426). `verify-sources`: **8,124 of 8,124 pass**.
  Database 6.1 GB for the eCFR alone (about 3.2 KB per provision version with its search index). About an hour of
  parsing and persisting from the cache on this machine, one transaction per run.
* Sections or appendices that sit in no part are not ingested: 22 in all (title 5: 2, title 15: 1, title 22: 2,
  title 25: 1, title 41: 2, title 48: 14), reported by the connector at the end of each title.

## Results (sandbox, 2026-10-14)
* **Slice ingested: titles 1, 12 and 17** (the manifest lists all). 4 requests (robots.txt and 3 files, 17 MB for
  title 17, 39 MB for title 12). Ingest report: 537 parts (28 + 395 + 114; 80 reserved ranges dropped by the
  connector, none outside a part), 537 instruments and versions, **140,187 provision versions** (10,517
  sections, 324 appendices, 129,346 paragraphs and appendix blocks), 132,190 passages indexed, 0 errors, 0 conflicts, 0 skipped.
  `verify-sources`: **537 of 537 pass** (stored texts re-derive from the stored envelopes).
* Sections with irregular numbering kept whole: 120 of 6,932 (title 12), 62 of 3,314 (title 17), 0 of 271 (title 1).
  Long appendices are cut at HD1/HD2 headings (the 559,471-character "Supplement I to Part 1005" is 184 blocks).
* Tests: `tests/test_ecfr.py` 22 passed (5 of them need PostgreSQL); full suite 759 passed.
  `ruff check`, `ruff format --check` and `mypy ulis` pass.
* Searches (dev DB, as of 2026-10-14): "initial margin requirements for brokers and dealers extending credit" ->
  `12 CFR 221.3(a)(2)`, `12 CFR 220.3(c)`; "privacy impact assessment information in identifiable form" ->
  `1 CFR 603.2`; "commodity pool operator exemption from registration" -> `17 CFR 3.12(h)(3)`, `17 CFR 4.13`;
  "electronic fund transfers unauthorized consumer liability" -> `12 CFR 1005.12(b)(2)(ii)`. Every hit carries
  `ATTESTED_ONLY_AT`, `AS_OF_AFTER_CURRENCY` (date 2026-10-06), `OFFICIAL_CONSOLIDATION_REPRODUCED`,
  `SOURCE_NOT_AUTHENTIC`, `PROFILE_UNVERIFIED`; authority `applicability_unverified`.


## Known gaps

* Current text only, unofficial, possibly behind the website; history comes from the annual editions (`us-govinfo-bulk-cfr-fr`, load them first: [us-govinfo-bulk.md](us-govinfo-bulk.md)).
* About 2% of sections (irregular numbering) are one provision; unnumbered paragraphs belong to the paragraph before
  them; numbers repeated after unnumbered text (definitions) get `_dup2` eIds and an "Ambiguous pinpoint" note.
* Content outside any part is not ingested (22 sections or appendices across titles 5, 15, 22, 25, 41 and 48, see
  the full-load results). Tables are flattened; authority and Federal Register citations are not linked to the US
  Code. Each `ingest` run is one transaction over the whole manifest (about an hour from the cache).
