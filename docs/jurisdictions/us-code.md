# United States Code (US, federal): ten positive-law titles from the OLRC

Status: **pilot sample, draft unverified profile** (US profile 0.1.1). Ten whole titles of the United
States Code in USLM XML, one release point, English only. Research aid, not legal advice.

| | |
|---|---|
| Source | `config/sources/us-uscode-house.yaml` (`us-uscode-house`, active; it was a `planned` stub) |
| Manifest | `config/manifests/us-uscode-house.yaml` (release point 119-120, 10 titles, SHA-256 pins) |
| Connector / parser | `ulis/sources/connectors/uscode_house.py` (`uscode_house`, on `PoliteHttp`); `ulis/sources/parsers/uslm.py` (`uslm` 1.2.0: `uscDoc` handler, zip input); citations in `ulis/jurisdictions/modules/us.py` (`us` 1.1.0) |
| Tests | `tests/test_uscode_house.py`; fixtures `tests/fixtures/uscode_house/` (titles 1, 17, 27 as downloaded) |

## Why not GovInfo (the original plan)

GovInfo's bulk data has **no US Code**: `https://www.govinfo.gov/bulkdata/USCODE` is a 404 and `/bulkdata/`
lists BILLS, CFR, FR, PLAW, STATUTE, COMPS and others. The USCODE collection (sitemap: 1994-2024) is PDF and
HTML: `https://www.govinfo.gov/content/pkg/USCODE-2024-title1.zip` holds `html/`, `pdf/` and METS/MODS files, no
USLM XML (`/content/pkg/.../xml/*.xml` redirects to an error page). The XML comes from the Code's author, the
Office of the Law Revision Counsel (OLRC), so that is the source used.

## Source and licence (read 2026-10-14)

| Page | Finding |
|---|---|
| [robots.txt](https://uscode.house.gov/robots.txt) | Only `User-agent: Slurp` is disallowed; no Crawl-delay. The connector applies it to every URL anyway. |
| [download.html](https://uscode.house.gov/download.html) | Per-title XML, XHTML, PCC, PDF archives for the current release point, "current through Public Law 119-120 (10/07/2026)"; User Guide; USLM schema. Positive-law titles carry an asterisk. |
| [about_code.html](https://uscode.house.gov/about_code.html) | 1 U.S.C. 204: prima facie evidence, "except that the text of titles enacted into positive law is legal evidence of the law". |
| privacy, faq, about-office | No copyright notice, no terms of use. |
| [govinfo.gov/about/policies](https://www.govinfo.gov/about/policies) | "Public Domain & Copyright Notice": 17 U.S.C. 105, no copyright in works of the US Government. |

Conclusion: licence **approved** on the statutory basis (17 U.S.C. 105; enacted law). The OLRC states no licence of
its own, so counsel review is still recommended. No attribution is required; the source is named anyway.

## What is ingested

Titles 1, 5, 11, 17, 18, 28, 31, 35, 38, 44 (all positive law; the largest XML is 19 MB, so none was skipped) as
`/us/usc/t<N>`, `instrument_type` `act`, eIds like `sec_1030__subsec_a__para_2__subpara_C`. Provision text is the
law text; headings, source credits (note + `source_act` links) and footnotes (note) are kept apart. Not ingested:
appendices, editorial/statutory notes, the other titles (26 and 42 are not positive law), earlier release points.

## Dates, versions and legal status

* `validity_basis = attested_at_edition` (as for CFR editions): each text is known in force at the release point's
  date, 2026-10-07; no start date is invented (`ATTESTED_ONLY_AT` for any day, `AS_OF_AFTER_CURRENCY` after it). The
  XML says only `Online@119-120`; the date comes from the download page, held in `USC_RELEASE_POINT_DATES`
  (an unlisted release point is refused, not guessed).
* `text_origin = official_consolidation`, `authoritative = false`: the OLRC online version is not the GPO's
  authenticated edition, although positive-law titles are legal evidence of the law (1 U.S.C. 204).
* `ext.positive_law` (from `is-positive-law`) and the instrument title say which kind a title is; title 27
  (fixture) is labelled "prima facie evidence only, 1 U.S.C. 204". Core limitations add no legal-status warning
  for it, so non-positive-law titles are not ingested.
* Repealed sections and subsections: `status = repealed`, empty text, the Code's wording in the note. The shared
  resolver picks repealed versions only by date, so a lookup says `no_version` and the timeline shows the entry.
  Omitted, transferred, renumbered and vacant entries (39) carry no law text and are not provisions.
* Footnote marks are removed (they would turn "§ 3598." into "3598.1"). Numbers the Code prints twice (5 U.S.C.
  3598) get `_dup2` eIds and an "Ambiguous pinpoint" note (103 provisions).

## Citations

`17 U.S.C. § 107`, `17 U.S.C. § 107(4)`, `18 U.S.C. § 1030(a)(2)(C)`, `1 U.S.C. § 204(a)`. No year: release points are
not printed editions (the release point is in the version data). References below a section resolve to the section
(`/us/usc/t18/s1030/a/2` -> `sec_1030`), because the levels between a section and its parts vary.

## Known gaps

* One release point, no history; no case law or Statutes at Large (see the profile).
* Notes (amendment, effective date, "References in Text") are not ingested; tables are flattened to text.
* Links to Public Laws and Statutes at Large stay unresolved (49,786 links, 5,213 resolved). Not reviewed by a US lawyer.

## Results (2026-10-14)

* Real run (`ulis_uscode`): 10 documents, **70,118 provision versions** (1: 129, 5: 16,498, 11: 4,070, 17: 3,098,
  18: 11,723, 28: 5,192, 31: 8,650, 35: 1,242, 38: 17,306, 44: 2,210), 61,368 passages, 218 repealed, 0 errors,
  0 conflicts, 2 m 26 s; re-run: 10 unchanged. `verify-sources`: **10 of 10 pass**. 11 requests, 5 s apart.
* Searches: "intentionally accesses a computer without authorization ..." -> `18 U.S.C. § 1030(a)(2)`; "fair use
  of a copyrighted work criticism ..." -> `17 U.S.C. § 107`; "federal question jurisdiction" -> `28 U.S.C. § 1331`;
  "automatic stay filing of the petition ..." -> `11 U.S.C. § 362(a)`; "whoever invents or discovers any new and
  useful process ..." -> `35 U.S.C. § 101`.
* Tests: full suite **450 passed** in 240 s; `tests/test_uscode_house.py` has 30 (22 unit, 8 PostgreSQL), plus 1 new case in `test_uslm.py`.
