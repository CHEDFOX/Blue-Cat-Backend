# United States Code (US, federal): all 53 titles from the OLRC

Status: **draft unverified profile** (US profile 0.4.0). Every title of the United States Code that the OLRC offers
(27 positive-law, 26 not) in USLM XML, one release point, English only. Research aid, not legal advice.

| | |
|---|---|
| Source | `config/sources/us-uscode-house.yaml` (`us-uscode-house`, active) |
| Manifest | `config/manifests/us-uscode-house.yaml` (release point 119-120, 53 titles, SHA-256 pins), written by `scripts/build_uscode_manifest.py` |
| Connector / parser | `ulis/sources/connectors/uscode_house.py` (`uscode_house`, on `PoliteHttp`); `ulis/sources/parsers/uslm.py` (`uslm` 1.3.0); citations in `ulis/jurisdictions/modules/us.py` (`us` 1.3.0) |
| Tests | `tests/test_uscode_house.py`; fixtures `tests/fixtures/uscode_house/` (titles 1, 17, 27 as downloaded, download page excerpt) |

## Why not GovInfo

GovInfo's bulk data has no US Code (`/bulkdata/USCODE` is a 404; its USCODE collection is PDF and HTML). The USLM XML
comes from the Code's author, the Office of the Law Revision Counsel (OLRC), so that is the source used.

## Source and licence (read 2026-10-14 and 2026-10-15)

| Page | Finding |
|---|---|
| [robots.txt](https://uscode.house.gov/robots.txt) | Only `User-agent: Slurp` is disallowed; no Crawl-delay. The connector applies it to every URL anyway. |
| [download.html](https://uscode.house.gov/download.html) | Per-title XML archives for the current release point, "current through Public Law 119-120 (10/07/2026)". Positive-law titles carry an asterisk. |
| [about_code.html](https://uscode.house.gov/about_code.html) | 1 U.S.C. 204: prima facie evidence, "except that the text of titles enacted into positive law is legal evidence of the law". |
| [legislation.html](https://uscode.house.gov/codification/legislation.html) | A non-positive-law title "is an editorial compilation of Federal statutes"; the Statutes at Large prevail. |
| [govinfo.gov/about/policies](https://www.govinfo.gov/about/policies) | 17 U.S.C. 105: no copyright in works of the US Government. |

Licence **approved** on the statutory basis; the OLRC states no licence of its own, so counsel review is still recommended.

## What is ingested

All 53 main titles (1 to 54, the title 53 is reserved) as `/us/usc/t<N>`, `instrument_type` `act`, eIds like
`sec_1030__subsec_a__para_2__subpara_C`. The manifest is written from the download page (`parse_download_page`): a
title the page lists but the server answers 404 is recorded in `fetch_errors` and skipped; a changed file (SHA-256) is
refused. Provision text is the law text; headings, source credits and footnotes are kept apart. Not ingested: the
appendices to titles 5, 11, 18, 28, 50, editorial/statutory notes, earlier release points.

## Positive law or not

* `ext.positive_law` comes from the XML (`is-positive-law`); the manifest flag (the page's asterisk) must agree.
* **26 titles are not positive law** (2, 6-8, 12, 15, 16, 19-22, 24-27, 29, 30, 33, 34, 42, 43, 45, 47, 48, 50, 52).
  Their version title says "not enacted as positive law - prima facie evidence only, 1 U.S.C. 204" and every hit carries the core limitation
  `NOT_POSITIVE_LAW` (warning, `ulis/verification/limitations.py`): the Statutes at Large prevail over the text.
* Citations do not change (`26 U.S.C. § 1(a)`, `42 U.S.C. § 2000d`): the Bluebook form has no such marker.

## Dates, versions and legal status

* `validity_basis = attested_at_edition`: each text is known in force at the release point's date, 2026-10-07; no start
  date is invented. The date comes from the download page (`USC_RELEASE_POINT_DATES`; an unlisted release point is refused).
* `text_origin = official_consolidation`, `authoritative = false`.
* Repealed sections: `status = repealed`, empty text, the Code's wording in the note (5,233 in the 53 titles).
  Omitted, transferred and vacant entries carry no law text and are not provisions.
* Footnote marks are removed. Numbers the Code prints twice get `_dup2` eIds and an "Ambiguous pinpoint" note.

## Known gaps

* One release point, no history of the Code itself (the Statutes at Large are in `us-govinfo-bulk-statutes`).
* Notes are not ingested; tables are flattened. Links to Public Laws are mostly unresolved (393,453 links, 83,588
  resolved) because most Acts are not loaded. Not reviewed by a US lawyer.

## Results (sandbox, 2026-10-15)

* Real CLI run `ulis.cli ingest us-uscode-house`, all 53 titles from the cached zips, a fresh database: **762,349
  provision versions** (42: 146,914; 26: 58,371; 10: 44,590; 15: 42,406; 16: 41,048), 665,927 passages indexed, 53
  instruments, 0 errors, 0 conflicts; **22 min 18 s** (1,338 s, with two other ingests running on 4 cores), **peak
  memory 1.9 GB**. `verify-sources`: 53 of 53 pass. The dev database holding all four federal sources is 4.1 GB.
* Needed in shared code: the passage indexer sent one query with more than 65,535 bind parameters for title 42 and
  failed; `rebuild_passages` now works in chunks of 10,000 (`ulis/retrieval/indexer.py`).
* Searches: "individual income tax imposed on taxable income of married individuals filing joint returns" ->
  `26 U.S.C. § 1(a)`; "no person in the United States shall ... on the ground of race ..." -> `42 U.S.C. § 2000d`;
  both with `ATTESTED_ONLY_AT`, `NOT_POSITIVE_LAW`, `OFFICIAL_CONSOLIDATION_REPRODUCED`.
* Tests: `tests/test_uscode_house.py` (the committed manifest loads and every entry passes the connector's validation);
  full suite **1261 passed** in 5 min 33 s; `ruff check`, `ruff format --check` and `mypy ulis` pass.
