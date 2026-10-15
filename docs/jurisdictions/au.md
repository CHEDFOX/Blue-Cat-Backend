# Australia (Commonwealth) - `AU`

Status: **every principal Act in force that has a compilation** (785 titles in the manifest), profile
`draft_unverified` (0.1.0); the latest registered compilation of each, and every registered compilation of four
(Constitution, Acts Interpretation Act 1901, Electronic Transactions Act 1999, Insurance Contracts Act 1984). The
sections below describe the parser and the pilot run on those four; "Whole-collection widening" has the scale.

## Source

| | |
|---|---|
| Source id | `au-federal-register` (`config/sources/au-federal-register.yaml`) |
| Publisher | Office of Parliamentary Counsel (OPC), which manages the Register under the Legislation Act 2003 ([about](https://www.legislation.gov.au/help-and-resources/using-the-legislation-register/about-the-federal-register-of-legislation)) |
| Access | Public API `https://api.prod.legislation.gov.au/v1/` (OData-style REST, OpenAPI 3.0.1, no key; [spec](https://api.prod.legislation.gov.au/swagger/index.html)): `/v1/Versions` and `/v1/Documents` listings per title, then each compilation document `/v1/documents(titleid=…,start=…,…,format='Epub')?$expand=version` with `Accept: application/json` |
| Connector | `au_federal_register` (`ulis/sources/connectors/au_federal_register.py`, uses `PoliteHttp`) |
| Parser | `frl_epub` (`ulis/sources/parsers/au_federal_register.py`) |
| Manifest | `config/manifests/au-federal-register.yaml` |

### Licence and terms (read before any legislation was fetched, 2026-10-14)

- [Terms of use](https://www.legislation.gov.au/terms-of-use), "Copyright": "With the exception of
  the Commonwealth Coat of Arms, and where otherwise noted, all content on the Federal Register of
  Legislation (the Legislation Register) is provided under Creative Commons Attribution 4.0
  International (the CC BY 4.0 licence)" ([licence](https://creativecommons.org/licenses/by/4.0/)),
  for any purpose, even commercially. Conditions: link to the relevant Register pages, attribute
  to the Federal Register of Legislation, link to the licence, indicate changes, no suggestion of
  endorsement. Required wording: "Based on content from the Federal Register of Legislation at
  [full date of download]. For the latest information on Australian Government legislation please
  go to https://www.legislation.gov.au." (changed content) or "Sourced from the Federal Register of
  Legislation at [full date of download]. For the latest information on Australian Government law
  please go to https://www.legislation.gov.au." (unchanged). ULIS divides the text into provisions,
  so every result carries the "Based on content" form, the licence link and the changes made; the
  retrieval date is in each result's source record.
- [Data share and reuse](https://www.legislation.gov.au/help-and-resources/using-the-legislation-register/data-share-and-reuse):
  the Register "allows commercial reuse of our data" and programmatic download; "Automated processes
  can be used to download or spider (crawl) this website, but please respect the crawl-delay
  instruction in our robots.txt file"; contact the Register 1-2 weeks before a full crawl or
  anything using significant resources; incremental crawls preferably outside 08:00-20:00
  Australian time (UTC+10). The API is free, needs no key, and "may be subject to change".
- `robots.txt` ([www](https://www.legislation.gov.au/robots.txt)): `Crawl-delay: 10`, only `/assets/`
  disallowed; the API host has no robots.txt (404). ULIS waits 10 s between requests on both hosts.
  It made 152 requests in total (terms and help pages, API specification, listings, 113
  compilation documents); the bulk of the history (97 documents) was fetched after 20:00
  Australian Eastern Standard Time.
- **Authorised versions** ([about](https://www.legislation.gov.au/help-and-resources/using-the-legislation-register/about-the-federal-register-of-legislation)):
  "The latest version of all in force laws as well as some other laws on the Register have an
  authorised version that can be relied on for the purpose of court proceedings under sections 15ZA
  and 15ZB of the [Legislation Act 2003]. Authorised versions are always in Adobe Acrobat (PDF)
  format". ULIS parses the EPUB of the same compilation (the API reports `isAuthorised` only for
  PDFs): the text is an official compilation reproduced, not an authorised version.
  `text_origin: official_consolidation`, source authority `official_non_authentic`: every hit
  carries `OFFICIAL_CONSOLIDATION_REPRODUCED` and `SOURCE_NOT_AUTHENTIC`. English is the only
  (and authentic) language, so `authoritative: true` as for Canada.

Licence review status: `approved` (project review; counsel review still recommended).

## What is ingested

Titles and compilations ingested into the dev database `ulis_au` (fresh run, 2026-10-14). "Versions"
are provision versions after unchanged wordings were joined across adjacent compilations.

| Title (Register id) | Compilations ingested | Provisions | Versions | Passages |
|---|---|---|---|---|
| Commonwealth of Australia Constitution Act (C2004Q00685) | **6**: every compilation, 1907-04-03 … 1977-07-29 | 250 | 257 | 255 |
| Acts Interpretation Act 1901 (C1901A00002) | **74**: every compilation, 1916-05-30 … 2026-03-28 | 1,065 | 1,914 | 1,769 |
| Insurance Contracts Act 1984 (C2004A02944) | **25**: every registered compilation, 1997-04-07 … 2024-03-01 (ends 2026-10-08) | 1,354 | 3,317 | 2,984 |
| Electronic Transactions Act 1999 (C2004A00553) | **4**: every compilation, 2000-03-15 … 2025-12-05 | 312 | 348 | 316 |
| Crimes Act 1914 (C1914A00012) | latest (2026-08-27) | 10,376 | 10,376 | 9,765 |
| Privacy Act 1988 (C2004A03712) | latest (2026-06-04, ends 2026-12-10) | 4,410 | 4,410 | 4,143 |
| Family Law Act 1975 (C2004A00275) | latest (2025-06-10) | 7,843 | 7,843 | 7,333 |
| Criminal Code Act 1995, incl. the Criminal Code (C2004A04868) | latest (2026-06-30, ends 2027-02-26) | 15,175 | 15,175 | 14,281 |

Without joining unchanged wordings the Acts Interpretation Act alone would have 29,687 provision
versions (one per provision per compilation) instead of 1,914.

Language: English (`en`, PostgreSQL `english` configuration). Indexed: long title, preamble,
sections, subsections, paragraphs (to sub-sub-paragraphs), definitions, schedule clauses and
subclauses, the Criminal Code's sections and Dictionary, the Constitution's covering clauses and
sections, and schedules that are plain text (the Constitution's oath, the Crimes Act form, the
Child Protection Convention in Family Law Act Schedule 1).

## Whole-collection widening (2026-10-15)

- **Manifest** `config/manifests/au-federal-register.yaml`, generated by `scripts/build_frl_manifest.py` from the API's
  `Titles` listing (`isInForce`, `collection = 'Act'`, `isPrincipal`): 4,773 Acts in force, 1,261 principal Acts.
  A principal Act is listed when its latest version is a registered compilation: **785 titles** (777 + the 8 of the
  pilot). The other **477 (38%) were never amended**: the Register holds only their "as made" version, dated from
  the making date (assent), not from commencement, which the parser refuses (see Known gaps). Legislative
  instruments (24,082 in force) were counted and left out: far above the ~5,000 asked for.
- **Requests**: versions and documents of 40 titles per listing request (`batch_listings`), so a title costs one
  request (its EPUB): 781 documents + 40 listings + the four history titles (109 compilations + 8 listings) = about
  **940 requests, 2.6 hours at the 10 s crawl delay**. Nothing is fetched twice (cache); the connector waits out
  08:00-20:00 UTC+10 before any uncached request (`avoid_hours`), stops at the first 401/403/429/451, records a
  missing or failing title and goes on, and gives up after five failures in a row.
- **The Register's `@odata.nextLink` drops the `$filter`** (verified: it keeps `$select`, `$orderby`, `$skip`), so
  following it lists unrelated titles; the connector and the builder page with `$skip` instead.
- **Terms**: the help page "advises" contacting the Register 1-2 weeks before "a full site crawl or anything else that
  may use significant system resources". This is advice, not a condition of reuse; ULIS does not contact publishers
  and relies on the stated mitigations (one request per 10 s, off-peak waiting, batching, cache, stop on refusal).
- **Slice run** (`ulis_wau`, 50 titles: the 8 of the pilot + every 30th of the rest; 144 compilations, 1,095 s):
  35 latest-only Acts + 109 history compilations; 66,642 provision versions, 62,281 passages, **0 errors, 0
  conflicts**; `verify-sources` **144 pass, 0 fail** (113,363 units); database 190 MB. 11 of the 42 spread titles were
  never-amended Acts (the manifest now excludes them). The 31 spread Acts hold 23,002 provision versions (742 per
  Act) against 37,804 for the four large pilot Acts. **Estimate for the full manifest**: about 620,000 provision
  versions, 580,000 passages, **about 1.8 GB** of database (2.85 KB per provision version with passages and indexes),
  raw API responses about 0.4 GB.
- Searches (slice, `AU`, 2026-10-15): "hearsay evidence" -> `Evidence Act 1995 (Cth) s 165(1)(a)`, `s 182(2)`,
  `s 70(2)`; "foreign principal registrable activity" -> `Foreign Influence Transparency Scheme Act 2018 (Cth) s 15`;
  "referendum ballot paper" -> `Referendum (Machinery Provisions) Act 1984 (Cth) s 41AB(1)`; all with
  `AS_OF_AFTER_CURRENCY` (their compilations are dated before today), `SNAPSHOT_DATES`,
  `OFFICIAL_CONSOLIDATION_REPRODUCED`, `SOURCE_NOT_AUTHENTIC`, `PROFILE_UNVERIFIED`.
- Tests: `tests/test_au_federal_register_wide.py` (9: batching, 40-title chunks, history per title, refusal and failure
  handling, `$skip` paging, off-peak waiting, manifest checks, the committed manifest).

## Format choice and parsing

The API offers each compilation as Word (`.docx`), PDF (the authorised version) and EPUB; there
is no HTML or XML endpoint. The EPUB is XHTML generated from OPC's Word master and keeps its
paragraph styles as classes (`ActHead1`-`ActHead5`, `subsection`, `paragraph`, `paragraphsub`,
`Definition`, `notetext`, `Penalty`, ...), so it carries the same structure as the Word file in a
smaller, well-formed XML form. The fixed XHTML 1.1 document type declaration is removed and the
pages are parsed with `defusedxml` (DTDs forbidden); zip size and paths are checked.

- Structure from styles, never guessed: Chapter/Part/Division/Subdivision headings are context;
  sections (`ActHead5`, number in `CharSectno`) are provisions; labels such as `(1)`, `(a)`,
  `(i)`, `(A)`, `1.2` (Australian Privacy Principles) or `(xx.)` (Constitution) sit between two tab
  spacers. A `subsection` without a label is the section's own text; a `paragraph` without a label
  continues the open paragraph; a paragraph whose label is not a plain label (one `(а)` in the Acts
  Interpretation Act uses a Cyrillic letter) stays text of its subsection and is counted.
- Text is the source text with whitespace normalised; non-breaking hyphens (U+2011) are kept.
  Labels of children are printed in the parent's composite text, as for the UK and Canada.
- In-text notes, examples, penalties and simplified outlines are part of the Act (Acts
  Interpretation Act 1901 s 13(1), whose text is in the corpus) and stay in the text of the
  provision they belong to, with their own "Note:"/"Example:"/"Penalty:" labels.
- Never in text: the cover page and "About this compilation", the contents, and the endnotes
  (legislation history, amendment history, editorial changes), detected by their styles
  (`ENotesHeading1` in current compilations; `NotesSection`, `TableOfActsHead`, ... in older ones).
- Older reprints (the Insurance Contracts Act compilations for 1997-2003) use plain `h1`-`h6`
  headings (`h5` = section), `indenta`/`indentii`/`Subsection` styles, `NotesSection` endnotes and an
  editorial "[see Note 1]" pointer in some section headings (dropped from the heading); their
  template placeholders ("Do not delete : Division placeholder") are not text. All 113 compilations
  fetched parse; no paragraph style outside the known set was met.
- Not reproduced, and reported: text placed directly under a Part or Division heading outside any
  section (e.g. a Division's margin note, a Part's boxed simplified outline) and images. Formulas
  are images whose alt text is not part of the Act (s 13(3)); a provision containing one says so
  in its note.
- Identifiers (stable across compilations): work `/au/act/C2004A03712`,
  `/au/constitution/C2004Q00685`; expression `…/eng@<compilation date>`; eIds `section-6-1-def-agency-a`,
  `schedule-1-clause-1-1.2-a`, `section-11.1-1` (Criminal Code), `dictionary-def-abn`,
  `covering-clause-5`, `preamble`, `long-title`; repeated labels in one provision are numbered
  (`section-15-a-2`).

## Dates and versions

- Each compilation shows "the text of the law as amended and in force on [the compilation date]".
  The API gives each version of a title a `start` and an `end` (the start of the next version).
  Every provision of a compilation gets that period: the source dates the compilation as a whole,
  **not** individual provisions, so a provision's text is known to apply during the compilation's
  period, nothing more. The `SNAPSHOT_DATES` limitation says so on every result: `info` when the
  previous adjacent compilation had a different wording (the change happened on the compilation
  date, possibly an editorial change), `warning` when the wording may have applied earlier (the
  earliest indexed compilation containing it).
- Documents are whole-instrument snapshots (`validity_basis: revision_snapshot`, shared mechanism
  `ulis/ingestion/snapshots.py`): an unchanged wording continues the previous version, so a
  version starts only when the wording changed; a provision absent from a later compilation (repealed
  or renumbered) has no version after the last compilation showing it. Compilations are recorded
  oldest first. Because the Register states each compilation's end, two general rules were added:
  a continued wording ends where the latest compilation showing it ends, and a wording does **not**
  continue across a gap (a version of the title for which no compilation is registered): what
  applied in between is unknown, and a lookup in the gap answers `no_version`.
- "As made" versions (compilation 0) are not ingested: the Register dates them from the making
  date, which is not necessarily commencement (Criminal Code Act 1995: assent 15 Mar 1995,
  commencement 1 Jan 1997). Dates before the earliest indexed compilation are a history gap
  (`history_not_indexed`); before assent (from Endnote 3, when stated) the Act did not exist.
- Uncommenced amendments are not shown in compilations. When the latest registered compilation
  already has an end date (a later version exists with no compilation), its provisions end there
  and the later version is reported as an outstanding change; the Register's
  `hasUnincorporatedAmendments` flag is reported too. Neither is applied.
- Repealed provisions are omitted from compilations, so they are never recorded as `repealed`.
- `currency_date` is the compilation date (what the compilation states); requests for later dates
  carry `AS_OF_AFTER_CURRENCY`.
- A rectified compilation (a corrected re-registration) replaces the original: the connector takes
  the highest rectification number. Re-fetching a compilation whose metadata changed (e.g. the
  latest one gaining an end date) gives different bytes for an expression already recorded, which
  the pipeline reports as an error for review.

## Citations (module `au`, AGLC style; to be verified by an Australian reviewer)

| Provision | Citation |
|---|---|
| Section, subsection, paragraph | `Acts Interpretation Act 1901 (Cth) s 15AB(1)` |
| Definition | `Privacy Act 1988 (Cth) s 6(1) (definition of ‘agency’)`; inside it: `Acts Interpretation Act 1901 (Cth) s 2B (definition of ‘Administrative Arrangements Order’ para (b)(ii))` |
| Schedule clause | `Privacy Act 1988 (Cth) sch 1 cl 1.2(a)` (APP subclause numbers replace the clause number), `Privacy Act 1988 (Cth) sch 2 cl 1` |
| Criminal Code (schedule to the Criminal Code Act 1995) | `Criminal Code (Cth) s 11.1(1)`, `Criminal Code (Cth) Dictionary (definition of ‘ABN’)`; the Act's own sections `Criminal Code Act 1995 (Cth) s 3` |
| Constitution | `Australian Constitution s 51(xx)` (source prints `(xx.)`), `Australian Constitution sch` |
| Covering clauses | `Commonwealth of Australia Constitution Act 1900 (Imp) s 5` (year from the Register's making date 9 July 1900; AGLC adds the regnal year "63 & 64 Vict, c 12", which the source documents do not state - to verify) |
| Preamble, long title, unnumbered heading | `Privacy Act 1988 (Cth) Preamble`, `… long title`, `Privacy Act 1988 (Cth) sch 1, ‘Overview of the Australian Privacy Principles’` |

## Profile

`config/jurisdictions/au.yaml`: federal, common law, dualist (to verify); instrument types
constitution > act > legislative_instrument (+ bill); courts High Court of Australia (rank 1),
Federal Court of Australia Full Court and Federal Circuit and Family Court Division 1 (rank 2),
Federal Court single judge (rank 3), Federal Circuit and Family Court Division 2 (rank 4);
stare decisis with vertical binding; all precedent statements marked "To verify". No case law is
ingested.

## Known gaps

- 785 titles (principal Acts in force with a compilation). Not ingested: the 477 never-amended principal Acts
  (as-made only; supporting them needs a decision on how to date text whose only date is assent, e.g. a version
  with no start date and the `EFFECTIVE_DATE_UNKNOWN` limitation), legislative instruments, notifiable instruments,
  bills, State or Territory law, case law.
- Full compilation history only for the Constitution (from compilation 1, 1907), the Acts
  Interpretation Act 1901 (from compilation 1, 1916), the Electronic Transactions Act 1999 and the
  Insurance Contracts Act 1984; the Crimes Act 1914, Privacy Act 1988, Family Law Act 1975 and
  Criminal Code Act 1995 start at their latest compilation.
- History gaps: as-made versions and versions without a registered compilation have no text: in
  the Insurance Contracts Act, 11 versions between 1985-07-03 and 2001-12-15 (so nothing between
  assent and 1997-04-07 and several shorter gaps until 2002) and the version in force since
  2026-10-08.
- The Register dates compilations, not provisions (see above); per-provision commencement and the
  repeal of omitted provisions are not recorded.
- Not reproduced: text directly under Part/Division headings, images and formulas, the endnotes.
- Multi-volume compilations (several EPUB volumes) are refused by the connector; none of the
  selected titles currently has one (the Criminal Code's three volumes come as one EPUB).
- Search: text keeps non-breaking hyphens (`Governor‑General`); a query typed with an ordinary
  hyphen does not match it (search `Governor General`).
- The latest Insurance Contracts Act compilation ends on 2026-10-08 (a later version has no
  compilation yet), so it has no text for today's date.

## Shared changes (core)

- `ulis/ingestion/pipeline.py` (2026-10-15, shared with the UK widening, see [gb.md](gb.md)): the run ANALYZEs
  `provisions`, `provision_versions` and `provision_version_sources` inside its transaction (at the start, every 30 s
  and before the end) after taking their lock with `NOWAIT`, so that autovacuum cannot record "no rows" for tables a
  long run is filling; without it a 56-Act slice took 2 minutes per Act.
- `ulis/ingestion/snapshots.py`: `continues(..., start)` does not continue a wording across a gap
  when the previous snapshot's stated end is before the new snapshot's start; `carry_end` moves a
  continued version's end to the end stated by the snapshot that continues it (or reopens it).
  Sources that state no ends (Japan) are unaffected. Tested in
  `tests/test_au_federal_register.py::test_compilation_snapshots_order_gaps_and_refresh`.
- `ulis/verification/limitations.py`: `SNAPSHOT_DATES` for every `revision_snapshot` source (also
  Japan): the source dates versions of the whole instrument, not provisions.
- Registrations in `ulis/ingestion/pipeline.py` (`PARSERS`, `PARSER_VERSIONS`, `CONNECTORS`,
  `resolve_uri`), `ulis/jurisdictions/modules/__init__.py`, `tests/test_catalog.py`.

## Results (actual)

### Checks (worktree, after merging `claude/cool-edison-dzxoj3`)

- `ruff check`, `ruff format --check`: clean; `mypy ulis`: no issues (65 files).
- Full suite against `ulis_test_au`: **185 passed** (14 in `tests/test_au_federal_register.py`:
  parser structure, notes, definitions, schedules, the Constitution's history, the older reprint
  format, AGLC citations, refusal of unsafe/unsupported input, connector offline cache and
  selection; PostgreSQL: search citation and limitations, history joined across compilations,
  `history_not_indexed` and `no_version` cases, the Privacy Act's period ending at an uncompiled
  version, snapshot order/gap/refresh rules, `verify` and idempotent re-ingestion).

### Real run (`ulis_au`, schema recreated, 2026-10-14)

`migrate`, `sync-catalog` (19 jurisdictions, 12 sources, 4 concepts), then
`ingest au-federal-register` (1 min 56 s, from the polite fetch cache; 16 versions listed by the
Register were skipped by the connector: 4 "as made", 12 without a registered compilation):

```
documents_seen 113, documents_new 113, instruments_created 8, instrument_versions_created 113,
provision_versions_created 43,640, provision_versions_corroborated 46,721, restated 0,
passages_indexed 40,846, conflicts [], errors []
```

Parse report over the 113 compilations (`parse_with_report`): 537 unlabelled continuation
paragraphs kept in the provision they continue, and 25 unstyled paragraphs inside sections kept
as text of the enclosing provision (the Constitution's s 69 list of departments in each of its 6
compilations, one Family Law Act paragraph "(bd)" without a style); 104 images (formulas, 100 of
them in the Insurance Contracts Act compilations), noted on their provisions; 50 blocks outside any
provision not reproduced (10 margin notes under Division or schedule headings, 13 Part-level
simplified-outline paragraphs in the Crimes Act, 27 template placeholders).

`verify-sources`: **113 pass, 0 fail**, 90,361 stored provision texts re-derived from the raw
API responses.

### Sample searches and lookups (Python API, `today` 2026-10-14)

| Query / lookup | Date | Result |
|---|---|---|
| "Foreign corporations trading financial corporations Commonwealth legislative power" | 2026-10-14 | 1st `Australian Constitution s 51(xx)`, 2nd `Australian Constitution s 51` (with `AS_OF_AFTER_CURRENCY`: the latest compilation is dated 1977-07-29) |
| "person who attempts to commit an offence" | 2026-10-14 | `Criminal Code (Cth) s 11.1(4)`, `… s 11.1(4)(b)`; limitations `SNAPSHOT_DATES` (warning), `OUTSTANDING_CHANGES_INSTRUMENT` (version from 2027-02-26 not compiled), `OFFICIAL_CONSOLIDATION_REPRODUCED`, `SOURCE_NOT_AUTHENTIC`, `PROFILE_UNVERIFIED`, `CITATOR_COVERAGE` |
| "promote the purpose or object underlying the Act" | 2000-01-01 | `Acts Interpretation Act 1901 (Cth) s 15AA(1)`, the pre-2011 wording, valid 1981-06-12 → 2011-12-27 |
| lookup `…/C1901A00002/~section-15AA` | 2000-01-01 / 2026-10-14 | old wording (1984-06-12 → 2011-12-27) / "the interpretation that would best achieve the purpose or object of the Act" (from 2011-12-27); timeline of 3 versions |
| "people of any race for whom it is deemed necessary to make special laws" | 1950-01-01 | `Australian Constitution s 51(xxvi)` "… other than the aboriginal race in any State …", valid 1907-04-03 → 1967-08-10, `LATER_VERSION_EXISTS` |
| "contract based on the utmost good faith" | 2020-01-01 / 2026-10-14 | `Insurance Contracts Act 1984 (Cth) s 13(1)` (2013-06-28 → 2026-10-08) / **no results**: the latest compilation ends 2026-10-08 and the version in force since has no compilation (lookup: `no_version`) |
| "APP privacy policy" | 2026-10-14 | `Privacy Act 1988 (Cth) s 13K(1)(b)`, `s 13K(1)`, `sch 1, ‘Overview of the Australian Privacy Principles’` |

Ranking is the shared lexical ranking: a short provision such as s 51(xx) is outranked by long
Criminal Code passages for a query made only of its own words ("trading or financial corporations
formed within the limits of the Commonwealth" does not return it in the top 10).
