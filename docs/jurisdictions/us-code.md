# United States Code (US, federal): all 53 titles and the five appendices from the OLRC

Status: **draft unverified profile** (US profile 0.6.0). Every title of the United States Code that the OLRC offers
(27 positive-law, 26 not) and every appendix it offers (to titles 5, 11, 18, 28 and 50) in USLM XML, one release
point, English only. Research aid, not legal advice.

| | |
|---|---|
| Source | `config/sources/us-uscode-house.yaml` (`us-uscode-house`, active) |
| Manifest | `config/manifests/us-uscode-house.yaml` (release point 119-121, dated 2026-10-09; 53 titles and 5 appendices, SHA-256 pins), written by `scripts/build_uscode_manifest.py` |
| Connector / parser | `ulis/sources/connectors/uscode_house.py` (`uscode_house`, on `PoliteHttp`); `ulis/sources/parsers/uslm.py` (`uslm` 1.4.0); citations in `ulis/jurisdictions/modules/us.py` (`us` 1.5.0) |
| Tests | `tests/test_uscode_house.py`; fixtures `tests/fixtures/uscode_house/` (titles 1, 17, 27 at release point 119-120; the appendices to titles 18 and 50 and the download page at 119-121, as downloaded) |

## Why not GovInfo

GovInfo's bulk data has no US Code (`/bulkdata/USCODE` is a 404; its USCODE collection is PDF and HTML). The USLM XML
comes from the Code's author, the Office of the Law Revision Counsel (OLRC), so that is the source used.

## Source and licence (read 2026-10-14, 2026-10-15 and 2026-10-18)

| Page | Finding |
|---|---|
| [robots.txt](https://uscode.house.gov/robots.txt) | Only `User-agent: Slurp` is disallowed; no Crawl-delay (unchanged on 2026-10-18). The connector applies it to every URL anyway. |
| [download.html](https://uscode.house.gov/download.html) | Per-title XML archives for the current release point: "All files are current through Public Law 119-121 (10/09/2026)" on 2026-10-18 (119-120, 10/07/2026 on 2026-10-15). 58 XML archives: 53 titles and 5 appendices. Positive-law titles carry an asterisk; "any Appendix to this title has not been enacted as part of the title". |
| [about_code.html](https://uscode.house.gov/about_code.html) | 1 U.S.C. 204: prima facie evidence, "except that the text of titles enacted into positive law is legal evidence of the law". |
| [legislation.html](https://uscode.house.gov/codification/legislation.html) | A non-positive-law title "is an editorial compilation of Federal statutes"; the Statutes at Large prevail. |
| [govinfo.gov/about/policies](https://www.govinfo.gov/about/policies) | 17 U.S.C. 105: no copyright in works of the US Government. |

Licence **approved** on the statutory basis; the OLRC states no licence of its own, so counsel review is still recommended.

## Release points: the manifest carries the date

Each update of the Code is a release point (`119-121` = current through Public Law 119-121). The XML says only
`Online@119-121`; the only date the OLRC states for it is on the download page ("(10/09/2026)"). That date travels:

1. `scripts/build_uscode_manifest.py` reads the page (robots.txt first), writes `release_point: {id, current_through,
   date}` and lists every title and appendix with the SHA-256 of its archive (58 downloads, 5 s apart, cached under
   `var/sources/uscode.house.gov/`);
2. the connector validates the manifest (`date` must agree with the date in `current_through`) and passes
   `release_point`, `release_date` and `current_through` in every `RawDocument.metadata` (stored with the raw document);
3. the pipeline hands that metadata to the parser (`METADATA_PARSERS` in `ulis/ingestion/pipeline.py`; `parse_uslm(content,
   metadata)`), which dates every text by it (`version_date`, `currency_date`, `validity_basis = attested_at_edition`) and
   checks that the archive really is the release point, title and kind (title or appendix) the manifest says.

A new release point therefore needs no code change: regenerate the manifest and ingest. `USC_RELEASE_POINT_DATES` in the
parser stays as a fallback for documents parsed without metadata (fixtures) and as a consistency check (a manifest date that
contradicts a recorded one is refused); a release point neither the manifest nor the table dates is refused, never guessed.

Regenerated on 2026-10-18: release point 119-121, 53 titles (27 positive law) and 5 appendices; every one of the 53 title
archives has a new SHA-256 against 119-120 (the OLRC regenerates every file at a release point); 105 MB cached.

## What is ingested

All 53 main titles (1 to 54, the title 53 is reserved) as `/us/usc/t<N>`, `instrument_type` `act`, eIds like
`sec_1030__subsec_a__para_2__subpara_C`. A title the page lists but the server answers 404 is recorded in `fetch_errors` and
skipped; a changed file (SHA-256) is refused. Provision text is the law text; headings, source credits and footnotes are kept
apart (source credits and footnotes become notes).

### The appendices (`USCTitleAppendix`, `xml_usc<NN>a`)

The OLRC prints after titles 5, 11, 18, 28 and 50 material that "has not been enacted as part of the title". The parser
makes **one instrument per unit** of an appendix, typed by what the unit is, under the OLRC's own identifier where the
file has one and otherwise under the appendix identifier and the unit's heading:

| Appendix | Instruments (type, work URI) |
|---|---|
| 5 | Federal Advisory Committee Act, Inspector General Act of 1978, Ethics in Government Act of 1978 (`act`, `/us/usc/t5a/<heading>`: repealed/transferred placeholders only, the Acts now being 5 U.S.C. chapters 10, 4 and 131); Reorganization Plans (`reorganization_plan`, `/us/usc/t5a/reorgPlans`, 107 plans, eIds `plan_1939_I` ... from the identifiers `/us/usc/t5a/reorgPlan/1939/I`) |
| 11 | Federal Rules of Bankruptcy Procedure (`court_rules`, `/us/usc/t11a/federal-rules-of-bankruptcy-procedure`: the file gives the rule set no identifier; recognised by its heading) |
| 18 | Unlawful Possession or Receipt of Firearms (`act`, repealed §§ 1201 to 1203); Interstate Agreement on Detainers (`act`, `/us/usc/t18a/pl/91/538`); Classified Information Procedures Act (`act`, `/us/usc/t18a/pl/96/456`); Federal Rules of Criminal Procedure (`court_rules`, `/us/usc/t18a/courtRules/Crim`) |
| 28 | Federal Rules of Appellate Procedure (`/us/usc/t28a/courtRules/App`, with its Appendix of Forms as `form_1A` ... provisions), Federal Rules of Civil Procedure (`/us/usc/t28a/courtRules/Civil`), its two sets of supplemental rules (Admiralty or Maritime Claims and Asset Forfeiture Actions; Social Security Actions) as instruments of their own under `/us/usc/t28a/courtRules/Civil/<heading>`, Federal Rules of Evidence (`/us/usc/t28a/courtRules/Evid`); the two note-only levels (judicial financial disclosure, minor disputes) hold no text and yield nothing |
| 50 | eliminated by the OLRC ("Title 50, Appendix, War and National Defense, has been eliminated"): the file holds one note; the pipeline records it as skipped, not as an error |

Rules are provisions of kind `courtRule` (`rule_12__subsec_b__para_6`, `num_path` `["12", "b", "6"]`); the same
placeholder rules apply as for sections (reserved, transferred and renumbered rules are pointers and not provisions; an
abrogated rule - `status="unknown"` with "(Abrogated ...)" - and a superseded form are recorded as repealed with the Code's
wording in the note). `ext` says `usc_appendix: true`, `appendix_unit` (`court_rules`, `act`, `reorganization_plans`),
`appendix_heading` as printed, `court_rules` (`Civil`, `Crim`, `App`, `Evid`, `Bankr`) and `supplemental_rules`. Every result
carries the limitation `USC_APPENDIX` (warning) instead of `NOT_POSITIVE_LAW`: not enacted as part of the title; for court
rules the text of record is the Supreme Court's orders transmitted to Congress (28 U.S.C. 2072-2075); for an Act or plan
the Statutes at Large prevail. Advisory Committee notes, editorial notes and the images of the length-limit charts are not
ingested. The profile's `instrument_types` and the source's `document_kinds` gained `court_rules` and `reorganization_plan`.

Citations (`ulis/jurisdictions/modules/us.py`): `Fed. R. Civ. P. 12(b)(6)`, `Fed. R. Crim. P. 11(b)(1)(A)`,
`Fed. R. App. P. 4(a)(1)`, `Fed. R. App. P. Form 1A`, `Fed. R. Evid. 702`, `Fed. R. Bankr. P. 3001(c)`,
`Fed. R. Civ. P. Supp. R. C(3)` (the module's convention for the supplemental rules), `18 U.S.C. app. § 4 (Classified
Information Procedures Act)` (the Act's name because several Acts with their own § 1 share one appendix),
`Reorg. Plan No. 3 of 1970, 5 U.S.C. app.`; a rule set without an abbreviation is named as printed (`28 U.S.C. app., <name>,
Rule 3`). The pinpoint shown with a hit is `Rule 12(b)(6)` / `Form 1A` / `Reorg. Plan No. I of 1939`, not `§`. Appendix
identifiers resolve (`/us/usc/t28a/courtRules/Civil/rule12/b/6` -> the rule; `/us/usc/t18a/pl/96/456/s4` -> the section;
`/us/usc/t5a/reorgPlan/1939/I` -> the plan); an appendix never resolves to its main title.

## Positive law or not

* `ext.positive_law` comes from the XML (`is-positive-law`); the manifest flag (the page's asterisk) must agree.
* **26 titles are not positive law** (2, 6-8, 12, 15, 16, 19-22, 24-27, 29, 30, 33, 34, 42, 43, 45, 47, 48, 50, 52).
  Their version title says "not enacted as positive law - prima facie evidence only, 1 U.S.C. 204" and every hit carries the core limitation
  `NOT_POSITIVE_LAW` (warning, `ulis/verification/limitations.py`): the Statutes at Large prevail over the text.
* Citations do not change (`26 U.S.C. § 1(a)`, `42 U.S.C. § 2000d`): the Bluebook form has no such marker.

## Dates, versions and legal status

* `validity_basis = attested_at_edition`: each text is known in force at the release point's date, 2026-10-09; no start
  date is invented. The date comes from the download page through the manifest (see above).
* `text_origin = official_consolidation`, `authoritative = false`.
* Repealed sections: `status = repealed`, empty text, the Code's wording in the note (5,233 in the 53 titles at 119-120).
  Omitted, transferred and vacant entries carry no law text and are not provisions.
* Footnote marks are removed. Numbers the Code prints twice get `_dup2` eIds and an "Ambiguous pinpoint" note.

## Known gaps

* One release point, no history of the Code itself (the Statutes at Large are in `us-govinfo-bulk-statutes`).
* Statutory and editorial notes (amendment, effective-date and repeal notes; the Advisory Committee notes to the rules)
  are not ingested; tables are flattened; images are dropped.
* Uncodified statutes (and the Acts as enacted) are only in `us-govinfo-bulk-statutes`; links from source credits to Public
  Laws resolve only when those Acts are loaded (at 119-120: 393,453 links, 83,588 resolved).
* The court rules are the OLRC's reprint; the Supreme Court's orders and the Judicial Conference's text are not ingested.
* State law is out of scope here (the US-WA, US-TX, US-OR and US-UT profiles hold samples). Not reviewed by a US lawyer.

## Results

### 2026-10-18, release point 119-121: title 1 and the five appendices (sandbox, dev database `ulis_us`, fresh)

* Through the real pipeline (`Ingestor` + `UscodeHouseConnector` from the cached archives, the manifest reduced to those
  six entries, same pins): 6 documents seen, 5 new, 1 skipped (`usc50a`: "the appendix to title 50 holds no law text ...
  has been eliminated"), 0 errors, 0 conflicts; **15 instruments** (title 1 and 14 appendix units), **6,820 provision
  versions**, 5,974 passages indexed, 1,891 citation links created (6 resolved: the other Acts are not loaded); **15.4 s**,
  peak memory 150 MB.
* Per instrument (provisions / repealed): 1 U.S.C. 129/0; Fed. R. Bankr. P. 2,361/0; Fed. R. Crim. P. 1,165/0; Interstate
  Agreement on Detainers 11/0; CIPA 59/0; Unlawful Possession of Firearms 1/1; Fed. R. App. P. 799/3 (two abrogated
  rules, one superseded form); Fed. R. Civ. P. 1,632/0; Supplemental Rules (Admiralty) 81/0; Supplemental Rules (Social
  Security) 23/0; Fed. R. Evid. 441/0; Ethics in Government Act 5/5; Federal Advisory Committee Act 1/1; Inspector General
  Act 5/5; Reorganization Plans 107/0.
* `python -m ulis.cli verify-sources`: **5 of 5 pass** (units re-derived: usc01 129, usc05a 118, usc11a 2,361, usc18a
  1,236, usc28a 2,976), 5.4 s.
* Searches (`ulis.cli search --jurisdiction US`): "failure to state a claim upon which relief can be granted" ->
  `Fed. R. Civ. P. 12(b)(6)` (document type `court_rules`); "discovery of classified information by defendants" ->
  `18 U.S.C. app. § 4 (Classified Information Procedures Act)` (`act`); "before the court accepts a plea of guilty or nolo
  contendere" -> `Fed. R. Crim. P. 11(a)(3)`; "Bureau of the Budget transferred to the Executive Office of the President" ->
  `Reorg. Plan No. I of 1939, 5 U.S.C. app.` (`reorganization_plan`); every hit with `USC_APPENDIX`, `ATTESTED_ONLY_AT`,
  `OFFICIAL_CONSOLIDATION_REPRODUCED`, and without `NOT_POSITIVE_LAW`.
* Full-load estimate for the 58 archives: the 53 titles took 22 min 18 s and 762,349 provision versions at 119-120 (below);
  the appendices add 6,691 provision versions in about 15 s, so about 23 minutes and 769,000 provision versions, plus the
  download of 105 MB in 58 requests 5 s apart (about 5 minutes) when the cache is cold.
* Tests: `tests/test_uscode_house.py` 77 tests (23 new: release-point metadata, the appendix to title 18, the eliminated
  appendix to title 50, synthetic supplemental rules, forms, abrogated rules and unnamed units, Bluebook citations, the
  connector's appendix archives and manifest refusals, the whole download page, four PostgreSQL tests); full suite
  **2028 passed** in 12 min 41 s against `ulis_test_us`; `ruff check`, `ruff format --check` and `mypy ulis` pass.

### 2026-10-15, release point 119-120: all 53 titles (sandbox)

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
