# United States federal regulations: the eCFR (all CFR titles, current text)

Status: **draft unverified (US profile 0.1.2); the committed manifest lists all 50 titles, the sandbox run ingested
a slice (titles 1, 12, 17)**. Research aid, not legal advice. The eCFR is an *unofficial* editorial compilation.

| | |
|---|---|
| Source | `config/sources/us-ecfr.yaml` (`us-ecfr`, active); manifest `config/manifests/us-ecfr.yaml` (50 titles, 49 files, title 35 reserved) |
| Connector / parser | `ulis/sources/connectors/ecfr.py` (`ecfr`, on `PoliteHttp`); `ulis/sources/parsers/ecfr.py` (`ecfr_xml` 1.0.0); citations in `ulis/jurisdictions/modules/us.py` (`us` 1.2.0) |
| Tests | `tests/test_ecfr.py` (21 tests, 5 on PostgreSQL); fixtures `tests/fixtures/ecfr/` (title 1 whole, parts of titles 12 and 17) |

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
* Authority: `instrument_type regulation` ranks as `binding_regulation` (1.2, below `binding_legislation` 1.25) when a
  date is known; attested-only text (statutes and regulations alike) is `applicability_unverified` (0.9). Core unchanged.

## Citations

`12 CFR 220.1`, `12 CFR 220.1(b)(3)(ii)`, `12 CFR part 302, appendix A` (the Federal Register's own form, no year:
the eCFR is not a printed edition). The 2018-2019 annual-edition sample keeps `27 C.F.R. § 478.11(b) (2018)`.

@@RESULTS@@

## Known gaps

* Current text only, unofficial, possibly behind the website; no history; the print CFR is not ingested.
* About 2% of sections (irregular numbering) are one provision; unnumbered paragraphs belong to the paragraph before
  them; numbers repeated after unnumbered text (definitions) get `_dup2` eIds and an "Ambiguous pinpoint" note.
* Content outside any part is not ingested (none in titles 1, 12, 17); the larger titles (26, 40, 42, 48 ...) were not
  run here. Tables are flattened; authority and Federal Register citations are not linked to the US Code.
* A full run is one transaction: run it on a machine with disk and time for ~50 downloads; downloads are cached.
