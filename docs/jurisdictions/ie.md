# Ireland (IE) - electronic Irish Statute Book

**Status:** every Public Act of the Oireachtas since 1922 (4,007 Acts), **AS ENACTED**; profile `draft_unverified` (0.2.0, not
reviewed by counsel). Source `ie-irish-statute-book`, connector `irish_statute_book`, parser `eisb_act_xml`, module `ie`. EU
membership is not recorded (added separately).

> **As enacted, not as amended.** Each Act is the text in which the Oireachtas passed it. Later amendments, repeals and
> revocations are NOT applied, so an Act (or a provision) found here may have been changed, replaced or repealed since; most Acts
> of the 1920s to 1960s have been repealed in whole or in part. The label "in force" on a dated answer means only "enacted and
> passed" (the Non-Fatal Offences Act 1997, for example, has gained sections since). The statement is carried by the
> document title ("..., as enacted"), `consolidation = as_enacted`, the limitations `EFFECTIVE_DATE_PRESUMED`,
> `OUTSTANDING_CHANGES_INSTRUMENT` and `NOT_AUTHENTICATED`, the source attribution, the profile's known gaps and the manifest header.
> `docs/licence-review.md` records that the date label is not honest for as-enacted texts (open decision, not changed here).

## Source and licence

- eISB (Office of the Attorney General). XML of Acts as enacted, by ELI:
  `https://www.irishstatutebook.ie/eli/<year>/act/<number>/enacted/en/xml`, documented on the
  [open-data page](https://www.irishstatutebook.ie/open-data.html). No `robots.txt` (404) and no published rate
  limit; ULIS waits 5 s between requests.
- Licence: re-use "is subject to the Oireachtas (Open Data) PSI Licence", which incorporates CC BY 4.0
  ([licence PDF](https://data.oireachtas.ie/ie/oireachtas/corporate/governanceAndReform/2016/2016-03-27_oireachtas-psi-licence-open-data_en.pdf),
  [Oireachtas copyright and re-use page](https://www.oireachtas.ie/en/copyright-and-reuse/)). Duties: identify the Houses of
  the Oireachtas as creator, keep the copyright notice, refer to the licence, keep the disclaimers, and state that a
  reproduction is "not the official text of the Act or an official restatement". All carried in the source YAML and in
  every result's attribution. The harp and other emblems are outside the licence and are not ingested.
- Authenticity: the [eISB disclaimer](https://www.irishstatutebook.ie/disclaimer.html) gives no warranty of accuracy or
  currency and says publication "does not indicate that any particular provision ... was, or is currently, in force". The
  enrolled, signed Act is the authentic text: `text_origin = official_conversion`, `authoritative = false`, `NOT_AUTHENTICATED`.
- Revised Acts (Law Reform Commission): no stated re-use licence; `ie-revised-acts-lrc` stays `planned`, not fetched.

## What is ingested

- **Manifest** `config/manifests/ie-irish-statute-book.yaml`, made by `scripts/build_ie_manifest.py` from the eISB's own year
  lists (`/eli/<year>/act/`, one request per year, 1922-2026, "Public Acts" table: number and title): **4,007 Public Acts**, the six
  pilot Acts first with their SHA-256 pins. Not listed: Constitutional Amendment Acts, Private Acts, statutory instruments,
  pre-1922 legislation; the Constitution is HTML/PDF only (`/eli/cons/en/xml` is 404).
- **Scale**: 4,007 requests at 5 s = **5.6 hours** (plus 105 for the lists). From the sample run: about 153 provision versions and
  86 KB of text per Act (median 41 provisions; a few Acts have thousands), so about **630,000 provision versions** and
  **about 1.9 GB of database**, plus the raw XML (about 0.5 GB) in the object store. Below the 15 GB threshold: the manifest is the
  whole list. Ingest in batches: `python scripts/ingest_in_batches.py ie-irish-statute-book --batch 100`.
- Pilot Acts (pinned): Unfair Dismissals Act 1977, Non-Fatal Offences against the Person Act 1997, Criminal Justice (Theft and Fraud
  Offences) Act 2001, Consumer Protection Act 2007, Companies Act 2014 (15,220 provisions) and Data Protection Act 2018.
- Parser 1.2.0 additions from the sample: `<page number=.../>` (a page break, not text), `<degree/>` and `<eolhyphen/>` (characters),
  `<chapter>` inside a Schedule (cited `Sch 1, Pt 1, Ch II`), and old files that open with `<?version xml="1.0"?>`.

## Structure, dates and versions

- The XML is presentational. Section structure is rebuilt from printed labels: subsection `(1)`, paragraph `(a)`,
  subparagraph `(i)`, clause `(I)`, subclause `(A)`; the roman `(i)` is told from the letter `(i)` by the printed indent. Definitions
  ("In this Act-" followed by quoted terms) are provisions (`section-2-1-def-controller`). Parts and Chapters are context headings.
  A test checks that every section's composite text equals its printed lines (no text lost, added or reordered).
- Identifiers: work `/ie/act/<year>/<number>`, expression `.../en@<enactment date>`, eIds `section-60-3-a-i`, `schedule-1`,
  `schedule-1-part-2`, `long-title`.
- Dates: the XML has only `dateofenactment`. Every provision is `in_force` from that date (`validity_basis =
  enactment_date_presumed`, limitation `EFFECTIVE_DATE_PRESUMED`), a lower bound: many Acts commence by ministerial order. The Act's
  commencement wording is classified (`ext.commencement_basis`) and, with the missing amendments, reported as `outstanding_changes`.
  Before the enactment date a provision is `not_yet_in_force`.

## Citations

`Data Protection Act 2018, s 60(3)(a)` - `Criminal Justice (Theft and Fraud Offences) Act 2001, s 4(1)` - `Consumer Protection Act 2007,
s 2(1) (definition of "consumer")` - `Consumer Protection Act 2007, Sch 1, Pt 1` - `Agricultural Produce (Cereals) Act 1938, s 9(1)`.
The title is the Act's own short title with the comma before the year dropped.

## Known gaps

As enacted: later amendments are NOT applied. No commencement orders or dates. No Constitution, statutory instruments, Private
Acts, case law, Irish-language text or Revised Acts. Schedules are one passage per Schedule (or Part/Chapter); their numbered
paragraphs, images and table layout are not separated. Two Acts of the sample could not be read because the source XML is
defective: 1998/46 (`dateofenactment` "199801215") and 1994/34 (`year` 1995 against the enactment date 1994-12-27); they are reported
as ingest errors and not corrected. Search ranks a container with its own text alongside its paragraphs.

## Results

- Pilot run (2026-10-14, `ulis_ie`): 6 Acts, 19,735 provision versions, 18,273 passages, `verify-sources` 6/6, 44 tests.
- Widening run (2026-10-15, fresh database, parser 1.2.0): the six pilot Acts and 320 Acts drawn at random from the manifest in three
  samples (70, 100 and 150 of 4,001; some overlap): **311 distinct Acts stored, 2 errors (the defective files above)**; 66,427 provision
  versions, 60,346 passages, 6,358 citation links. About 430 requests in all, including the 105 year lists. Before the fixes listed
  above the same samples had 10 errors (`<page/>` 2, `<degree/>` 2, `<eolhyphen/>` 1, `<chapter>` in a Schedule 2, `<?version` header 1,
  source defects 2). `verify-sources` on this database: see the run log in the report.
- Searches (`jurisdiction=IE`): "unlawful to import flour or wheaten meal" -> `Agricultural Produce (Cereals) Act 1938, s 9(1)`
  (version from 1938-05-16, title "..., as enacted"); "to safeguard cabinet confidentiality ..." -> `Data Protection Act 2018, s 60(3)(a)(i)`;
  "directors duty to act in good faith" -> `Companies Act 2014, s 228(1)(a)`.
- Checks: ruff and mypy clean; tests `tests/test_irish_statute_book.py` (48 tests, 4 PostgreSQL) with a 1938 Act among the fixtures.
