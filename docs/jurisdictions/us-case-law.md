# United States (federal): case law from the Caselaw Access Project - NOT INGESTED

Status: **planned, blocked by robots.txt.** No US decision is in ULIS. The source is recorded in
`config/sources/us-caselaw-access-project.yaml` (`status: planned`); the licence gate refuses it.
US legislation (US Code titles, sample public laws) is unaffected, see [us-code.md](us-code.md).

## What was checked (2026-10-14)

| URL | Finding |
|---|---|
| https://static.case.law/robots.txt | `User-agent: *` / `Disallow: /`. The whole data host is disallowed for every robot, including the ULIS bot (checked with `urllib.robotparser`). |
| https://case.law/robots.txt | `User-agent: *` / `Disallow: /caselaw/` / `Sitemap: /sitemap.txt`. The project website's case browser is disallowed; the terms and about pages are not. |
| https://case.law/terms/ (text read from https://case.law/templates/cap-terms-page.js, the page is JavaScript-rendered) | "Terms of Use", effective March 13, 2024. The Caselaw Data (https://static.case.law/) is "available for public use under the CC0 1.0 Public Domain Designation", provided "as is" with no warranty about third-party content. Harvard imposes no legally binding conditions; it requests, as Community Norms, attribution to Harvard University and the Caselaw Access Project and that improvements be shared on the same open terms. Site text is CC BY-SA 4.0 and site code MIT (not data). |

Conclusion: the licence is unambiguous (CC0), but the publisher's access policy forbids robots on
the host that serves the data. Under the onboarding rules ULIS respects robots.txt and works
around no refusal (no other user agent, no mirror of the data set, no browser automation), so
**no case, volume or reporter file was fetched**.

Request log (all on 2026-10-14): `static.case.law` 2 requests - `robots.txt` and, in the same batch
and before its content had been read, `ReportersMetadata.json`. That second request was an ordering
mistake against the rule "read robots.txt first": the file was discarded and nothing from it is
recorded or used. `case.law` 6 requests (robots.txt, `/`, `/sitemap.txt`, `/terms/`, `/about/`, the
terms script), none to a disallowed path. The static site's layout (reporters, volumes, per-case
JSON) was therefore **not examined** and no field mapping is claimed. Copies of both robots.txt
files are kept in `tests/fixtures/caselaw_access_project/`.

To proceed, one of: written permission from the Library Innovation Lab for an identified research
bot, or a robots.txt on `static.case.law` that admits it. Other publishers of US opinions
(for example the Supreme Court's own site or GovInfo's court-opinion collections) are separate
sources that would each need their own licence and robots.txt review; none was checked here.

## What changed for a permitted source

- **Court hierarchy (US profile 0.2.0).** The profile now holds the Supreme Court (`US-SC`,
  rank 1) and the thirteen courts of appeals (`US-CA1` ... `US-CA11`, `US-CADC`, `US-CAFC`, rank 2,
  appeals to `US-SC`, bound by their own earlier decisions - marked "To verify"). Before, only
  `US-SC` existed, so `classify_judgment` labelled a Supreme Court decision "persuasive only"
  (no court below it). Now it is `precedent_vertical` ("Binds courts whose appeals lie to ..."),
  and `binding_precedent` for any forum court in the table. A circuit is binding only for itself,
  `persuasive_precedent` for another circuit. District courts and state courts are not modelled.
  Pinned by `tests/test_caselaw_access_project.py`.
- **Not built** (needs real data first): connector, manifest, parser, and the judgment citation
  format (`Roe v. Wade, 410 U.S. 113 (1973)` - the module's default would print the case name and
  reporter citation without comma and year; US pinpoints are pages, not numbered paragraphs, so the
  format depends on what the permitted source provides). Statute links in opinions would be set
  only where one `/us/usc/...` target is exact.
- **Judicial treatment** (overruled, distinguished, followed) is in no data set examined; ULIS
  would not infer it from text.

## Known gaps

- No US case law at all; searches for US return statutes and rules only.
- Court hierarchy limited to the Supreme Court and the courts of appeals.

## Results (2026-10-14)

- Dev DB `ulis_uscase`: `migrate`; `sync-catalog` -> 34 jurisdictions, 32 sources, 4 concepts (14 US
  courts stored); `ingest us-caselaw-access-project` -> exit 2, "refused: ... source status is
  'planned'; licence review status is 'pending_review'; licence does not permit bulk_download
  ('not_allowed'); no connector/parser configured"; `verify-sources` -> exit 0, nothing to verify.
- `classify_judgment` for `US-SC`: no forum court -> `precedent_vertical`; forum `US-CA9` ->
  `binding_precedent`.
- Checks: `ruff check` and `ruff format --check` (222 files) clean, `mypy ulis` clean (133 files); full
  suite against `ulis_test_uscase`: **741 passed**, including the 4 tests in
  `tests/test_caselaw_access_project.py` (source gated, robots evidence, court ranking, authority labels).
