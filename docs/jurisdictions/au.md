# Australia (Commonwealth) - `AU`

Status: **every principal Act in force** (1,262 titles in the manifest on 2026-10-18: 901 in their latest registered
compilation, 361 never-amended Acts in their text as made), profile `draft_unverified` (0.2.1); every registered
compilation of four titles (Constitution, Acts Interpretation Act 1901, Electronic Transactions Act 1999, Insurance
Contracts Act 1984). Legislative instruments (23,573 principal instruments in force) are a second source,
`au-federal-register-instruments`, **not loaded by default** (about 69 hours of crawl delay, and on a 20-title slice
the parser could read only 2: see "Legislative instruments"). Slices on 2026-10-18: 26 of 30 never-amended Acts
ingested as made, 2 of 20 instruments. The sections below describe the parser and the pilot run on the four history
titles; "Whole-collection widening", "Texts as made" and "Legislative instruments" have the scale and the slice runs.

## Source

| | |
|---|---|
| Source id | `au-federal-register` (`config/sources/au-federal-register.yaml`); legislative instruments: `au-federal-register-instruments` (`config/sources/au-federal-register-instruments.yaml`, `load_by_default: false`) |
| Publisher | Office of Parliamentary Counsel (OPC), which manages the Register under the Legislation Act 2003 ([about](https://www.legislation.gov.au/help-and-resources/using-the-legislation-register/about-the-federal-register-of-legislation)) |
| Access | Public API `https://api.prod.legislation.gov.au/v1/` (OData-style REST, OpenAPI 3.0.1, no key; [spec](https://api.prod.legislation.gov.au/swagger/index.html)): `/v1/Versions` and `/v1/Documents` listings per title, then each compilation document `/v1/documents(titleid=…,start=…,…,format='Epub')?$expand=version` with `Accept: application/json` |
| Connector | `au_federal_register` (`ulis/sources/connectors/au_federal_register.py`, uses `PoliteHttp`) |
| Parser | `frl_epub` (`ulis/sources/parsers/au_federal_register.py`): compilations and texts as made, Acts and legislative instruments |
| Manifests | `config/manifests/au-federal-register.yaml` (Acts), `config/manifests/au-federal-register-instruments.yaml` (instruments), both written by `scripts/build_frl_manifest.py` |

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
  Australian Eastern Standard Time. On 2026-10-18 (texts as made, instruments) it made 42 listing requests for the
  builder (titles and latest versions, 10:27-10:34 AEST), 14 requests for the eight fixture documents and listings
  (10:48-10:53 AEST, one of them a malformed query answered 400 Bad Request, corrected and not retried as such), 10
  requests for the as-made EPUB check and 48 for the instrument titles (11:05-11:20 AEST). A trial of the slice
  driver then re-fetched the two as-made fixture Acts into the working cache: 6 requests (versions, documents, EPUB)
  at 11:42-11:43 AEST, 10 s apart, because its manifest had no `avoid_hours` (a mistake: they were not needed). The
  slice ingests made 54 requests (50 documents and 4 batched listings) at 20:06-20:15 AEST, after the window; the
  re-runs after the parser fix below were served from the cache (0 requests). No request was refused.
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

### Texts as made (2026-10-18)

An Act that was never amended has no compilation: the Register holds only its text **as made** (compilation
number 0, register id = title id), which is its text in force. 361 of the 1,261 principal Acts in force are in that
state (29%; the 2026-10-15 count of 477 also included 116 Acts whose latest version is an *unnumbered* older
compilation, which the builder now lists as compilations). The connector fetches the as-made version when it is the
title's latest registered version (the manifest flags such titles `as_made: true` for information; the connector
decides from the Register's listing, so an Act that is amended later is fetched as a compilation from then on); an
as-made version that has later compilations is still skipped in history runs. All 361 have an EPUB document.

Dating is the honest part. The Register dates the as-made version from its making date (an Act: assent), **not**
from commencement (Criminal Code Act 1995: assent 15 March 1995, commencement 1 January 1997). Commencement is in
each Act's own s 2 and is not extracted. So every provision of a text as made is dated from the assent date with
`validity_basis: enactment_date_presumed` (limitation `EFFECTIVE_DATE_PRESUMED`, warning) and a note on every
provision version ("Text as made, never amended. The Register dates this version from assent (2024-12-02); the Act
commences as it provides (otherwise 28 days after assent, Acts Interpretation Act 1901 s 3A), which is not
recorded here.", limitation `VERSION_NOTE`); the version has no currency date (a text as made states none:
`CURRENCY_UNKNOWN`), `text_origin: official_conversion` (the EPUB is the Register's rendering of the text as made,
not the authorised PDF: `NOT_AUTHENTICATED`) and `consolidation_kind: as_made`. The Register's version end, when
it lists one (an instrument that repeals itself), ends every provision. Nothing is guessed: the claim the note makes
is checked against the source, and a document whose version start it cannot confirm is refused (reported in the
run's errors), not recorded. An Act as made must carry an assent endorsement, styled `AssentDt` or, in older re-keyed
texts, an unstyled "[Assented to 8 July 1999]", whose date is the version start: the Register dates some texts as
made from a retrospective commencement instead (Lake Eyre Basin Intergovernmental Agreement Act 2001: version from
21 October 2000, assent 1 May 2001; Household Stimulus Package Act (No. 2) 2009: from 1 January 2009, assent 18
February 2009), and those are refused. An instrument as made must have the Register's own registration date
(`RegisteredAt`) as its version start.

Front and back matter of an Act as made are not law text: the cover (short title, "No. 101, 2024", long title),
the contents, the assent endorsement ("[Assented to 2 December 2024]"; older texts: "[Reserved for Her Majesty's
pleasure, 14 September 1973]", "[Queen's Assent, 19 October 1973]"), the enacting words ("The Parliament of
Australia enacts:", "BE IT THEREFORE enacted by the Queen, the Senate and the House of Representatives ...") and
the second-reading and assent notes after the last provision (`2ndRd`, `AssentBk`). Older re-keyed texts carry the
endorsement, the second-reading note ("[Minister’s second reading speech made in— ...]") and the bill's print
reference ("(45/99)", printed between two subsections of the last section) without a style; they are recognised by
their wording and treated the same way. The body starts at the assent endorsement; the long title is recorded once;
unstyled recitals before the first heading are the preamble (Royal Style and Titles Act 1973). Fixtures: Commonwealth
Entities (Payment Surcharges) Act 2024 (current template), Royal Style and Titles Act 1973 and A New Tax System (Wine
Equalisation Tax Imposition—General) Act 1999 (older re-keyed texts), Lake Eyre Basin Intergovernmental Agreement Act
2001 (refused: version start before assent).

**Slice run** (`ulis_au`, 2026-10-18, 30 as-made Acts, every 12th of the 361, from the Seat of Government Acceptance
Act 1922 to the Public and Educational Lending Rights (Better Income for Authors) Act 2026): 30 documents and 2 batched
listings, 32 requests, 310 s at the crawl delay. Result with parser 1.1.1 (re-run from the cache on a recreated schema
after the fix below): **26 ingested**, 1,414 provision versions, 1,295 passages, 0 conflicts; **4 refused** (run
errors, nothing stored): the Lake Eyre Basin Intergovernmental Agreement Act 2001 and the Household Stimulus Package
Act (No. 2) 2009 (version start is not the assent date), the Loan Act 1976 (no assent endorsement; re-keyed in
styles the parser does not know) and the Civil Aviation Legislation Amendment Act 1995 (1,032 unstyled paragraphs: no
sections found). If the slice is representative, about 313 of the 361 load (87%). Parse report over the 26: 6 images
noted on their provisions; 648 blocks not reproduced, all in 6 Acts: the schedule items of five consequential
amendment Acts (630 blocks: 187 item headings, 191 items, 37 subitems, 12 transitional items and their paragraphs;
the amendments themselves appear in the amended Acts' compilations, their application and transitional items do not)
and the unstyled Schedule of the Seat of Government Acceptance Act 1922 (the 1920 Agreement: 16 paragraphs, 2
tables). The first run (parser 1.1.0, 20:06 AEST) ingested 27 and showed two defects, fixed in 1.1.1: the unstyled
cover and contents of an older text became a "preamble" and its print reference and second-reading note were
appended to its last provisions (Wine Equalisation Tax Imposition Act), and an unstyled endorsement was not checked,
so the Lake Eyre Act was stored as dated "from assent (2000-10-21)". Searches (`AU`, as of 2026-10-18): "rate of wine
tax payable" -> `A New Tax System (Wine Equalisation Tax Imposition—General) Act 1999 (Cth) s 4` (from 1999-07-08);
"property of any kind belonging to a State" -> `Primary Industries (Excise) Levies Act 2024 (Cth) s 21(2)`, `Recycling
and Waste Reduction Charges (Excise) Act 2020 (Cth) s 6(2) (definition of ‘property of any kind belonging to a
State’)`; each with `EFFECTIVE_DATE_PRESUMED`, `CURRENCY_UNKNOWN`, `PROFILE_UNVERIFIED`, `CITATOR_COVERAGE`
(warnings), `NOT_AUTHENTICATED`, `SOURCE_NOT_AUTHENTIC`, `VERSION_PERIOD` and `VERSION_NOTE` (the as-made note).

Language: English (`en`, PostgreSQL `english` configuration). Indexed: long title, preamble,
sections, subsections, paragraphs (to sub-sub-paragraphs), definitions, schedule clauses and
subclauses, the Criminal Code's sections and Dictionary, the Constitution's covering clauses and
sections, and schedules that are plain text (the Constitution's oath, the Crimes Act form, the
Child Protection Convention in Family Law Act Schedule 1).

## Whole-collection widening (2026-10-15)

- **Manifest** `config/manifests/au-federal-register.yaml`, generated by `scripts/build_frl_manifest.py` from the API's
  `Titles` listing (`isInForce`, `collection = 'Act'`, `isPrincipal`): 4,773 Acts in force, 1,261 principal Acts.
  On 2026-10-15 a principal Act was listed only when its latest version was a registered compilation: **785 titles**
  (777 + the 8 of the pilot); 477 were counted as "as made only" and left out. On 2026-10-18 the builder was corrected
  (116 of those 477 have an unnumbered older compilation as their latest version, e.g. a 1998 reprint registered in
  2004, which the connector and parser always handled) and extended to list the never-amended Acts with their text as
  made: **1,262 titles** = 901 compilations + 361 as made (see "Texts as made"). Legislative instruments (24,071 in
  force) are the separate source below.
- **Requests**: versions and documents of 40 titles per listing request (`batch_listings`), so a title costs one
  request (its EPUB): 1,258 documents + 64 listings + the four history titles (109 compilations + 8 listings) = about
  **1,440 requests, 4.0 hours at the 10 s crawl delay** (2026-10-15: 940 requests, 2.6 hours, for 785 titles).
  Nothing is fetched twice (cache); the connector waits out
  08:00-20:00 UTC+10 before any uncached request (`avoid_hours`), stops at the first 401/403/429/451, records a
  missing or failing title and goes on, and gives up after five failures in a row.
- **Database estimate for the 1,262 titles** (2026-10-18): the 2026-10-15 estimate for 785 titles (620,000 provision
  versions, below) + 116 more compilations at 742 provision versions each (86,000) + about 313 texts as made (87% of
  361, the slice's rate) at 54 each (the slice's 1,414 for 26 Acts: 17,000) = about 723,000 provision versions,
  **about 2.1 GB** of database at 2.85 KB each (the small as-made slice measured 5.05 MB for 1,470 provision
  versions, 3.4 KB each with its fixed index overhead); raw API responses about 0.5 GB (texts as made average 46 KB).
  The instruments source at its slice's rate: about 2,400 instruments at 28 provision versions each (66,000), about
  0.2 GB of database, but about 0.7 GB of raw responses (31 KB on average, cached whether or not they parse).
- **The Register's `@odata.nextLink` drops the `$filter`** (verified: it keeps `$select`, `$orderby`, `$skip`), so
  following it lists unrelated titles; the connector and the builder page with `$skip` instead.
- **Terms**: the help page "advises" contacting the Register 1-2 weeks before "a full site crawl or anything else that
  may use significant system resources". This is advice, not a condition of reuse; ULIS does not contact publishers
  and relies on the stated mitigations (one request per 10 s, off-peak waiting, batching, cache, stop on refusal).
- **Slice run** (`ulis_wau`, 50 titles: the 8 of the pilot + every 30th of the rest; 144 compilations, 1,095 s):
  35 latest-only Acts + 109 history compilations; 66,642 provision versions, 62,281 passages, **0 errors, 0
  conflicts**; `verify-sources` **144 pass, 0 fail** (113,363 units); database 190 MB. 11 of the 42 spread titles were
  never-amended Acts (excluded from the manifest then; listed as made since 2026-10-18). The 31 spread Acts hold 23,002 provision versions (742 per
  Act) against 37,804 for the four large pilot Acts. **Estimate for the full manifest**: about 620,000 provision
  versions, 580,000 passages, **about 1.8 GB** of database (2.85 KB per provision version with passages and indexes),
  raw API responses about 0.4 GB.
- Searches (slice, `AU`, 2026-10-15): "hearsay evidence" -> `Evidence Act 1995 (Cth) s 165(1)(a)`, `s 182(2)`,
  `s 70(2)`; "foreign principal registrable activity" -> `Foreign Influence Transparency Scheme Act 2018 (Cth) s 15`;
  "referendum ballot paper" -> `Referendum (Machinery Provisions) Act 1984 (Cth) s 41AB(1)`; all with
  `AS_OF_AFTER_CURRENCY` (their compilations are dated before today), `SNAPSHOT_DATES`,
  `OFFICIAL_CONSOLIDATION_REPRODUCED`, `SOURCE_NOT_AUTHENTIC`, `PROFILE_UNVERIFIED`.
- Tests: `tests/test_au_federal_register_wide.py` (11: batching, texts as made in batch mode, 40-title chunks, history
  per title, refusal and failure handling, `$skip` paging, off-peak waiting, manifest checks, both committed manifests).

## Legislative instruments (2026-10-18)

The Register's `LegislativeInstrument` collection held **24,071 instruments in force** on 2026-10-18 (24,078 rows
in the API's listing, 7 of them duplicates across pages),
**23,573 of them principal** (regulations, rules, determinations, declarations, orders, standards, plans, ...;
the rest are amending instruments with no text of their own to fetch). The same API, connector and parser serve
them: title ids `F2016L00123`, `F1996B00084` (pre-1996 "backcapture"), `C2004L09583`; compilation register ids
`F2004C00644`. They are a **separate source**, `au-federal-register-instruments` (own descriptor with the same
licence block; own manifest from `scripts/build_frl_manifest.py --collection LegislativeInstrument`; approved in
`tests/test_catalog.py`), with **`load_by_default: false`**: 23,573 documents + listings = about
24,753 requests = **about 69 hours at the 10 s crawl delay**, far more than the Acts, so it is loaded
only with `ingest au-federal-register-instruments` or `load-all --include-history` / `--only`, in batches, outside
Australian business hours and never at the same time as the Acts source (same host). The manifest lists titles only
(48 requests); the per-title version check of the Acts builder would cost 600 more, and the connector lists the
latest version of 40 titles per request anyway and fetches the compilation or the text as made accordingly.

- Work URIs `/au/legislative_instrument/F2026L01385`; `instrument_type: legislative_instrument` (declared in the
  profile); `ext.collection: LegislativeInstrument`. Citations (AGLC-style, to verify): `South Pacific Regional
  Environmental Programme (Privileges and Immunities) Regulations 1996 (Cth) reg 7(1)`, `... Rules 2026 (Cth) r 5`,
  other instruments `Defence (Laverton—Defence Area) Declaration 2026 (Cth) s 3(1)(a)`, schedules `sch cl 1(3)`,
  definitions `reg 3 (definition of ‘Act’)`; the unit word comes from the title ("Regulations", "Rules").
- An instrument as made is dated by the Register from its **registration** (F2026L01385: made 12 October 2026,
  registered 14 October, commencing 26 October by its s 2, repealed by its own s 4 on 30 November, which the Register
  lists as the version's end): `enactment_date_presumed` from the registration date, the note says so, the "Dated"
  line gives `work_date` and an enactment event. The making formula ("I, ..., make this declaration under ...") and
  signature block are not law text. Not every instrument as made is dated that way: the parser requires the version
  start to equal the Register's `RegisteredAt` and refuses the document otherwise (tariff concession orders are dated
  from their effect, instruments back-captured in 2006 from 20 December 2004).
- Styles: instruments drafted in the current OPC template use the Acts' styles; compilations prepared by the Office
  of Legislative Drafting before 2005 use `HR` (regulation heading, number in the text, "[see Note 1]" dropped),
  `R1`/`R2` (regulation, subregulation), `Rc` (closing words), `P1`-`P3` (paragraphs), `definition0`, `Note`, and
  "Notes to the ..." endnotes (`NoteHeading`), all mapped. Instruments drafted by agencies in other templates have
  headings the parser does not recognise: such a document is refused ("no sections found") and reported in the
  run's errors, not ingested. Fixtures: F2026L01385 (as made), F1996B00533 (OLD-template compilation).
- **Slice run** (`ulis_au`, 2026-10-18, 20 principal instruments, every 1,179th of the manifest): 20 documents and 2
  batched listings, 22 requests, 210 s. Result with parser 1.1.1: **2 ingested** (Safety, Rehabilitation and
  Compensation (Defence-related Claims) (Specified Diseases and Employment) Instrument 2017, a compilation; Social
  Security (Tables for the Assessment of Work-related Impairment for Disability Support Pension) Determination 2023,
  as made), 56 provision versions, 53 passages, 0 conflicts; **18 refused**: 12 whose version start is not their
  registration date (6 airworthiness directives back-captured in 2006 and dated 2004-12-20, 5 tariff concession
  orders, and the National Environment Protection (Used Packaging Materials) Measure 2011, made 16 September 2011 and
  registered 20 October 2011 but dated 15 July 2005, which the first run with parser 1.1.0 stored with a note
  calling 15 July 2005 its registration date) and 6 with no recognisable sections (an airworthiness directive form, an
  accounting standard in the AASB's IASB styles, a vehicle standard compiled by the Department, two Repatriation
  Medical Authority Statements of Principles in their own styles and auto-numbered lists, a determination with no
  styles at all). By title, the 23,573 instruments in the manifest are 9,010 airworthiness directives (38.2%), 5,596
  tariff concession orders (23.7%), 2,004 determinations, 919 regulations, 751 Statements of Principles, 367 rules,
  183 vehicle standards, 89 accounting and auditing standards and 4,654 others; all 12 directives and orders in the
  slice were refused.
- **A full load is not worth its cost with this parser**: about 24,750 requests (69 hours at the crawl delay) to
  fetch documents of which, at the slice's rate, about 1 in 10 would be ingested (roughly 2,400 instruments; the sample
  is small). Before it is loaded the parser needs (a) the agency templates (Statements of Principles, accounting and
  vehicle standards, unstyled determinations), (b) a unit for documents that are forms or tables without sections
  (directives, concession orders) and (c) a dating rule for versions that start before registration; or the manifest
  should leave out the directives and concession orders (14,606 titles), which cuts the load to about 9,400 requests
  (26 hours).

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
- "As made" versions (compilation 0) of an Act that was amended later are not ingested: the Register dates them
  from the making date, which is not necessarily commencement (Criminal Code Act 1995: assent 15 Mar 1995,
  commencement 1 Jan 1997). Dates before the earliest indexed compilation are a history gap
  (`history_not_indexed`); before assent (from Endnote 3, when stated) the Act did not exist. For a never-amended
  Act the text as made is the only text and is ingested, dated from assent as `enactment_date_presumed` (see
  "Texts as made"). If such an Act is amended later, the next run adds its compilation as a new snapshot; the
  as-made version's open end is not closed by it (the snapshot rules only chain snapshots), so a lookup after the
  compilation date finds the compilation, while the as-made version's period stays open: re-load the title.
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
| Act as made | `Royal Style and Titles Act 1973 (Cth) s 2(1)`, `Royal Style and Titles Act 1973 (Cth) sch`, `Commonwealth Entities (Payment Surcharges) Act 2024 (Cth) s 5(1)` |
| Legislative instruments | `South Pacific Regional Environmental Programme (Privileges and Immunities) Regulations 1996 (Cth) reg 7(1)`, `... reg 3 (definition of ‘Act’)`, `Court Procedures Rules 2026 (Cth) r 5(2)`, `Defence (Laverton—Defence Area) Declaration 2026 (Cth) s 3(1)(a)`, `... sch cl 1(3)` |

## Profile

`config/jurisdictions/au.yaml`: federal, common law, dualist (to verify); instrument types
constitution > act > legislative_instrument (+ bill); courts High Court of Australia (rank 1),
Federal Court of Australia Full Court and Federal Circuit and Family Court Division 1 (rank 2),
Federal Court single judge (rank 3), Federal Circuit and Family Court Division 2 (rank 4);
stare decisis with vertical binding; all precedent statements marked "To verify". No case law is
ingested.

## Known gaps

- 1,262 titles: every principal Act in force (901 compilations, 361 texts as made) and the Constitution. Not
  ingested: amending Acts (in force but with no text of their own), notifiable instruments, bills, State or
  Territory law, case law. The texts as made are dated from assent (`enactment_date_presumed`), not from
  commencement, which is not extracted. A text as made whose version start the source does not confirm as the
  assent date (the Register dates some from a retrospective commencement; older texts without an endorsement), or
  in which the parser finds no sections, is refused and reported: 4 of 30 on the slice (about 48 of the 361 if the
  slice is representative). In a text as made, schedule items (amendments of other Acts, application and
  transitional items) and unstyled schedule text are not reproduced (648 blocks in 6 of the 26 slice Acts, counted
  in the parse report).
- Legislative instruments (24,071 in force, 23,573 principal) are the separate source
  `au-federal-register-instruments`, not loaded by default (about 69 hours of crawl delay); until it is loaded
  the corpus holds no legislative instrument. The parser reads 2 of the 20 slice instruments: forms and tables
  without sections (airworthiness directives, tariff concession orders: 62% of the titles), agency templates it does
  not know, and texts as made whose version start is not their registration date are refused and reported. A full
  load is not recommended until the parser covers them (see "Legislative instruments"). Only their latest version;
  no history.
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

### Checks and runs, texts as made and legislative instruments (2026-10-18)

- `ruff check`, `ruff format --check`: clean; `mypy ulis`: no issues (236 files).
- Full suite against `ulis_test_au`: **2018 passed** (10 min 47 s; 2016 before the two tests added with parser 1.1.1). AU tests: `tests/test_au_federal_register.py` (23: the 14 of the
  pilot, plus the Act as made in the current template, in a 1973 re-keyed text and in a 1999 re-keyed text with an
  unstyled endorsement, print reference and second-reading note, the refusal of texts as made whose version start the
  source does not confirm (an Act dated from a retrospective commencement, an Act without an endorsement, an
  instrument whose start is not its registration date), the instrument as made, the OLD-template instrument
  compilation, the connector fetching texts as made, and two PostgreSQL tests: as-made Acts searched and looked up
  with `EFFECTIVE_DATE_PRESUMED`, `CURRENCY_UNKNOWN`, `NOT_AUTHENTICATED` and the version note, `verify` passing; the
  instruments source ingested, searched and looked up until the instrument's own repeal),
  `tests/test_au_federal_register_wide.py` (11).
- Dev database `ulis_au` (schema recreated 2026-10-18): `migrate`, `sync-catalog`, then the two slice ingests above
  (the slice manifests, every 12th as-made Act and every 1,179th instrument of the committed manifests, through the
  source's own connector and the pipeline's `Ingestor`, as `scripts/ingest_in_batches.py <source> --manifest <slice>`
  does) and `verify-sources`: **28 pass, 0 fail** (1,470 stored provision texts re-derived from the raw API
  responses); `status`: 28 instruments, 1,470 provisions, 1,348 passages, 28 source documents.

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
