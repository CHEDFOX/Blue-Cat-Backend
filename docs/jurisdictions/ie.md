# Ireland (IE) - electronic Irish Statute Book

**Status:** pilot, profile `draft_unverified` (not reviewed by counsel). Source `ie-irish-statute-book`, connector
`irish_statute_book`, parser `eisb_act_xml`, module `ie`. EU membership is not recorded (added separately).

## Source and licence

- eISB (Office of the Attorney General). XML of Acts as enacted, by ELI:
  `https://www.irishstatutebook.ie/eli/<year>/act/<number>/enacted/en/xml`, documented on the
  [open-data page](https://www.irishstatutebook.ie/open-data.html). No `robots.txt` (404) and no published rate
  limit; ULIS waits 5 s between requests and fetched the 6 Act files (plus the policy pages read for this review).
- Licence: re-use "is subject to the Oireachtas (Open Data) PSI Licence", which incorporates CC BY 4.0
  ([licence PDF](https://data.oireachtas.ie/ie/oireachtas/corporate/governanceAndReform/2016/2016-03-27_oireachtas-psi-licence-open-data_en.pdf),
  [Oireachtas copyright and re-use page](https://www.oireachtas.ie/en/copyright-and-reuse/)). Duties: identify the Houses of
  the Oireachtas as creator, keep the copyright notice, refer to the licence, keep the disclaimers, and state that a
  reproduction is "not the official text of the Act or an official restatement". All carried in the source YAML and in
  every result's attribution. The harp and other emblems are outside the licence and are not ingested.
- Authenticity: the [eISB disclaimer](https://www.irishstatutebook.ie/disclaimer.html) gives no warranty of accuracy or
  currency and says publication "does not indicate that any particular provision ... was, or is currently, in force".
  The enrolled, signed Act is the authentic text, so ULIS records `text_origin = official_conversion`,
  `authoritative = false`, source authority `official_non_authentic` (limitation `NOT_AUTHENTICATED`).
- Revised Acts (Law Reform Commission, `revisedacts.lawreform.ie`): `robots.txt` allows all, but the site states no
  re-use licence ("All content Copyright 2026"). Recorded as `ie-revised-acts-lrc`, status `planned`, not fetched.

## What is ingested (manifest `config/manifests/ie-irish-statute-book.yaml`, every file pinned by SHA-256)

| Act | Provisions | Notes |
|---|---|---|
| Unfair Dismissals Act 1977 (No. 10) | 176 | |
| Non-Fatal Offences against the Person Act 1997 (No. 26) | 238 | one unnumbered Schedule |
| Criminal Justice (Theft and Fraud Offences) Act 2001 (No. 50) | 591 | 9 Schedules incl. EU conventions |
| Consumer Protection Act 2007 (No. 19) | 1,104 | Schedules with Parts |
| Companies Act 2014 (No. 38) | 15,220 | 1,448 sections, 17 Schedules; 4.4 MB XML |
| Data Protection Act 2018 (No. 7) | 2,406 | footnotes, Parts and Chapters |

English only, as enacted. Bunreacht na hÉireann is not ingested: eISB offers it as HTML/PDF only
(`/eli/cons/en/xml` is 404).

## Structure, dates and versions

- The XML is presentational. Section structure is rebuilt from printed labels: subsection `(1)`, paragraph `(a)`,
  subparagraph `(i)`, clause `(I)`, subclause `(A)`. The roman `(i)` is told from the letter `(i)` (after `(h)`) by the
  printed indent; a line without label goes to the nearest enclosing level by indent and closes deeper levels, so the
  printed order is kept; labels inside quoted amending text are not structure. Definitions ("In this Act-" followed by
  quoted terms) are provisions (`section-2-1-def-controller`). Parts and Chapters are context headings, not citation
  levels. A test checks that every section's composite text equals its printed lines (no text lost, added or reordered).
- Special characters (`<emdash/>`, `<odq/>`, `<ifada/>`, `<unicode ch=.../>`) are resolved; footnotes go to `note`.
- Identifiers: work `/ie/act/<year>/<number>`, expression `.../en@<enactment date>`, eIds `section-60-3-a-i`,
  `schedule-1`, `schedule-1-part-2`, `long-title`.
- Dates: the XML has only `dateofenactment`. Every provision is `in_force` from that date
  (`validity_basis = enactment_date_presumed`, limitation `EFFECTIVE_DATE_PRESUMED`), which is a lower bound: many Acts
  commence by ministerial order. The Act's own commencement wording is classified (`ext.commencement_basis`:
  `by_order`, `relative_to_passing`, ...) and, with the missing amendments, reported as `outstanding_changes`
  (limitation `OUTSTANDING_CHANGES_INSTRUMENT`). Before the enactment date a provision is `not_yet_in_force`.

## Citations

`Data Protection Act 2018, s 60(3)(a)` - `Criminal Justice (Theft and Fraud Offences) Act 2001, s 4(1)` -
`Consumer Protection Act 2007, s 2(1) (definition of “consumer”)` - `Consumer Protection Act 2007, Sch 1, Pt 1` -
`Data Protection Act 2018, long title`. The title is the Act's own short title with the comma before the year dropped.

## Known gaps

As enacted: later amendments are NOT applied (the Non-Fatal Offences Act has since gained sections 1A, 2A, 3A ...). No
commencement orders or dates. No Constitution, statutory instruments, case law, Irish-language text or Revised Acts.
Schedules are one passage per Schedule (or Part); their numbered paragraphs, images and table layout are not separated.
Search ranks a container with its own text (e.g. `s 228(1)`) alongside its paragraphs.

## Results (2026-10-14)

- Dev DB `ulis_ie`: `ingest` - 6 documents, 6 instruments, 19,735 provision versions, 18,273 passages, 3,712 citation
  links (all resolved), 0 conflicts, 0 errors, 35 s. `verify-sources` - 6 of 6 pass, 19,735 units.
- Searches: "to safeguard cabinet confidentiality ..." -> `Data Protection Act 2018, s 60(3)(a)(i)`; "dismissal of an
  employee shall be deemed ... unfair" -> `Unfair Dismissals Act 1977, s 6(1)`; "a commercial practice is misleading if the
  trader omits or conceals" -> `Consumer Protection Act 2007, s 46(1)`; "directors duty to act in good faith" ->
  `Companies Act 2014, s 228(1)(a)`; "assault causing harm summary conviction" -> `... 1997, s 3(2)(a)`. As of 2018-05-23
  the Data Protection Act returns nothing (`not_yet_in_force`); from 2018-05-24 `in_force` with the limitations above.
- Checks: ruff and mypy clean; full suite on `ulis_test_ie`: 338 passed, 0 failed, 44 of them new
  (`tests/test_irish_statute_book.py`: 40 unit tests, 4 PostgreSQL tests).
