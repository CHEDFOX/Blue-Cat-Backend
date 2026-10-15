# United States state law: Washington, Texas, Oregon, Utah (US-WA, US-TX, US-OR, US-UT)

Status: **pilot, draft unverified profiles** (0.1.0). Statutes only (no annotations, no case law, no regulations, no
constitutions), current text, English. Each State is a `constituent_unit` profile with parent `US`: its law applies
inside the State only, federal law reaches it through the parent chain (`US-WA` -> `US` -> `INT`). Research aid, not legal advice.

| State | Source (manifest) | Connector / parser | Module, citation |
|---|---|---|---|
| WA | `us-wa-rcw` (2,785 chapters) | `us_wa_rcw` / `us_wa_rcw_html` | `us_wa`: `RCW 9A.32.030` |
| TX | `us-tx-statutes` (30 codes) | `us_tx_statutes` / `us_tx_statutes_zip` | `us_tx`: `Tex. Penal Code § 19.02`, `Tex. Code Crim. Proc. art. 38.23` |
| OR | `us-or-ors` (689 candidate chapters) | `us_or_ors` / `us_or_ors_html` | `us_or`: `ORS 163.115` |
| UT | `us-ut-code` (96 titles) | `us_ut_code` / `us_ut_code_xml` | `us_ut`: `Utah Code § 76-5-203(2)` |

Shared code: `connectors/us_state_http.py` (manifest, robots.txt of every host, delay, 404 -> `fetch_errors`, a refusal stops the run),
`sources/us_state_bundle.py` (the envelope below), `parsers/us_state_common.py`. Tests: `tests/test_us_states.py`, fixtures `tests/fixtures/us_states/`.

## Sources and licences (read 2026-10-14; details and URLs in each `config/sources/us-*.yaml`)

| State | robots.txt | Terms | Licence entry |
|---|---|---|---|
| WA | none (404) on app.leg.wa.gov, lawfilesext, leg.wa.gov | [Disclaimer](https://leg.wa.gov/disclaimer/): no warranty; the Statute Law Committee "claims copyright for both codes" and asks anyone who intends to *sell* them to contact it | approved for research; commercial use `unknown` |
| TX | none (404 / the app shell) on tcss.legis.texas.gov | none stated; Gov. Code 323.0145(d)(2) keeps state copyright; the Download Statutes page offers the zips | approved for research; commercial `unknown` |
| OR | one Microsoft robot, empty Disallow | [Disclaimer](https://www.oregonlegislature.gov/Pages/disclaimer.aspx): public service, not the official record; no copyright claim | approved for research; commercial `unknown` |
| UT | Crawl-delay 10 (honoured) | [Terms](https://le.utah.gov/documents/disclaimer.htm): copy/distribute for personal use; commercial use needs attribution | approved; attribution always carried |

Counsel review is recommended before any commercial deployment of WA, TX or OR. Since *Georgia v. Public.Resource.Org* (2020) the issue is compilation claims and annotations, not the enacted text; no annotation is ingested.

## What is ingested and how

* One raw document per chapter (WA, OR), code (TX) or title (UT) as received, wrapped in an envelope with the URL and retrieval time
  (the pages carry no edition date). The parser dates the document by that retrieval date: `validity_basis = attested_at_edition`,
  `currency_date` = retrieval date, no invented start dates. Each document is an `act` (`/us-wa/rcw/9A.32`, `/us-tx/statutes/PE`,
  `/us-or/ors/163`, `/us-ut/code/76`); each section is a leaf provision (`sec-9A.32.030`; Texas `art-` for Code of Criminal Procedure,
  Vernon's Civil Statutes, Insurance Code - Not Codified, Auxiliary Water Laws). Subsections stay inside the section text.
* Notes, not text: WA session-law credit and Code Reviser notes; TX credit lines and Council notes ("Text of section effective
  until ..."); OR bracketed credit and `Note:` paragraphs; UT history lines. Cross references become citation links (WA, TX, UT).
* Dates the sources do state are used. **Utah** prints a section twice when a later version is enacted (`enddate` on the current text, `effdate` on the new):
  both are stored (`valid_to` / `valid_from`), so a lookup as of 2027-02-01 returns the 2027 wording. **Texas** repeal lines with a future
  effective date ("Repealed by ..., eff. April 1, 2027", 53 articles of the Code of Criminal Procedure) keep the text in force (`valid_to`).
  Repealed placeholders (OR bracket-only sections, TX "Repealed by" sections) are `repealed` with no text.
* Conflicting amendments printed under one number (TX: 4 sections numbered 32.56) are kept as separate provisions (`sec-32.56-v2`) with an "Ambiguous pinpoint" note.

## Real run (dev database `ulis_usstates`, 2026-10-15)

Fetched through the connectors (robots.txt checked; 5 s delay, Utah 10 s: 92 requests, 0 errors) into a polite cache, then ingested into a fresh
database after `migrate` and `sync-catalog` (38 profiles, 45 sources). The committed manifests are the full lists; the sandbox slices are below.

| State | Ingested here | Provisions (versions) | Links created / resolved | Time |
|---|---|---|---|---|
| WA | slice: 18 of 2,785 chapters (9A.04-9A.72, 10.95, 19.86, 46.61, 59.18); the full list is about 4 h of requests | 557 | 1,099 / 537 | 2.5 s (cached) |
| TX | **all 30 codes** (123 MB unpacked) | 122,866 (29 repealed; 12 container articles without text) | 99,051 / 99,051 | 250 s |
| OR | slice: 14 candidate chapters (1, 3, 10, 12, 90, 105, 137, 161, 163-167, 285A) | 2,381 (824 repealed placeholders; 1,557 indexed) | 0 | 4.5 s |
| UT | **all 96 titles** (56 MB) | 27,752 sections, 27,897 versions (250 with a stated start date: versions printed for the future) | 58,645 / 58,410 | 89 s |

Total 152,834 indexed passages; 0 ingestion errors, 0 conflicts. `verify-sources`: **158 documents, 158 pass, 0 fail**. The first Utah run found
three real oddities (lower-case section numbers in title 26B, `depth="null"` and a stray depth); handled and tested. Tests: 52 in
`tests/test_us_states.py`; full suite 789 passed; `ruff check`, `ruff format --check`, `mypy ulis` clean.

Real searches (as of 2026-10-14): US-WA "driving while under the influence ... 0.08 within two hours" -> `RCW 46.61.502`; US-TX "capital murder of a
peace officer or fireman" -> `Tex. Penal Code § 19.03` (second); US-OR "murder in the second degree affirmative defense extreme emotional
disturbance" -> `ORS 163.135`, `ORS 163.115`; US-UT "voluntary intoxication is not a defense" -> `Utah Code § 76-2-306` (`provision_at` 2026-10-14:
`in_force_date_unverified`; 2027-02-01: `in_force`, `valid_from` 2027-01-01, the 2027 wording); the Texas query under `US` finds nothing; in the
PostgreSQL test 17 U.S.C. § 107 is found from US-WA, US-TX, US-OR and US-UT.

## Planned or rejected (no data ingested; each has a source YAML with the findings and a gate-refusal test)

California (Cloudflare challenge on leginfo robots.txt; bulk host `downloads.leginfo.legislature.ca.gov` has no licence, state copyright reserved
by Gov. Code 10248), New York (Open Legislation needs an API key; nysenate.gov law pages behind a challenge), Colorado (text only on LexisNexis),
Illinois (incomplete TLS chain), Florida (allowed, but no reuse terms and no bulk file), Ohio (`Disallow: /` -> rejected), Michigan (robots.txt 403),
Minnesota (allowed; terms not located), Virginia (all rights reserved; no licence), Pennsylvania (allowed; whole-title HTML files; the best next state).

## Known gaps

* No annotations, case law, regulations, constitutions or local law; no past versions (Utah prints future ones); subsections are not provisions.
* WA: the certified PDF is the official RCW; repealed/decodified sections are only in the dispositions tables. TX: sections the Council prints with
  "Text of section prior to repeal" (still applicable to some events) are `repealed` without text; stale `*-old` / `* - Copy` zip members are skipped;
  versions with different effective dates stay side by side in the text, undated. OR: candidate chapters (the publisher lists ranges, not files); the
  2025 Edition excludes the 2025 special and 2026 regular sessions (notices recorded as outstanding changes); the database text is not the official ORS.
  UT: the Historical Code is not ingested; tables are flattened; one encoded character is written `[char set/char]`; a version with a stated end
  date but no start date loses its `valid_to` when validity is recomputed (the end date stays in the note).
* Profiles are unreviewed: courts (apex courts only), commencement rules and citation forms are research notes marked "To verify".
