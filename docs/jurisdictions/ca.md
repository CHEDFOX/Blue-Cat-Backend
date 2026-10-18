# Canada (federal) - `CA`

Status: **every consolidated federal Act and regulation in both official languages** at one pinned commit of the
Department of Justice's Justice Laws XML repository (manifest: 953 Acts and 4,462 regulations, 10,830 files),
profile `draft_unverified` (0.2.0). The Acts were loaded and verified earlier (see
[test-results.md](../test-results.md): 953 Acts in both languages, 0 errors, 2 conflicts); this page records how the
regulations were added, the slice that was ingested as evidence, and what remains out. Not legal advice; a
reproduction, not an official version.

## Source

| | |
|---|---|
| Source id | `ca-justice-laws-xml` (`config/sources/ca-justice-laws-xml.yaml`) |
| Publisher | Department of Justice Canada, [Justice Laws Website](https://laws-lois.justice.gc.ca/) |
| Access | Public GitHub repository [justicecanada/laws-lois-xml](https://github.com/justicecanada/laws-lois-xml), read with `git` (partial clone of one commit, sparse checkout of the listed files). No request is made to the Justice Laws Website itself; no API key, no robots.txt question arises |
| Pinned commit | `b7292cfb886d9e0ad60852e7eeb058ff07c5e71a` (consolidations "current to" 2025-07-24 to 2026-06-14, per file) |
| Connector | `git_snapshot` (`ulis/sources/connectors/git_snapshot.py`): every file pinned by SHA-256 |
| Parser | `justice_canada_xml` 1.1.0 (`ulis/sources/parsers/justice_canada.py`): `<Statute>` and `<Regulation>` roots |
| Module | `ca` 1.1.0 (`ulis/jurisdictions/modules/ca.py`): McGill-style citations in English and French |
| Manifest | `config/manifests/ca-justice-laws-xml.yaml`, built by `scripts/build_justice_canada_manifest.py` |

### Licence (recorded before anything was fetched)

- The repository's `LICENSE.md` at the pinned commit reads: "Open Government Licence - Canada. Copyright (c) 2020
  Government of Canada - Gouvernement du Canada. http://open.canada.ca/en/open-government-licence-canada" (and the
  same in French). The [Open Government Licence - Canada 2.0](https://open.canada.ca/en/open-government-licence-canada)
  grants a worldwide, royalty-free, perpetual, non-exclusive licence to copy, modify, publish, distribute and
  commercially exploit the information, subject to attribution ("Contains information licensed under the Open
  Government Licence – Canada.") and to not using it in a way that suggests official status or endorsement.
- The [Reproduction of Federal Law Order](https://laws-lois.justice.gc.ca/eng/regulations/SI-97-5/) (SI/97-5) permits
  anyone to reproduce federal enactments and consolidations "without charge or request for permission", provided due
  diligence is exercised in ensuring the accuracy of the materials and the reproduction is not represented as an
  official version.
- Consolidations on the Justice Laws Website are official and evidence of the law since 1 June 2009 (Legislation
  Revision and Consolidation Act, RSC 1985, c L-22, s 31); the XML reproduced by ULIS is not an official version, so
  `authority: official_non_authentic`, `text_origin: official_consolidation`, and every result carries
  `OFFICIAL_CONSOLIDATION_REPRODUCED`. English and French are equally authoritative (`authoritative: true` for both,
  `EQUALLY_AUTHENTIC_VERSIONS` on every result).
- Licence review status: `approved` (project review on 2026-10-14; counsel review still recommended).

## What the manifest lists

The repository at the pinned commit holds **971 Acts** (`eng/acts/*.xml`, each with a French twin in `fra/lois/`) and
**4,892 regulations** (`eng/regulations/*.xml`, French twins in `fra/reglements/`: 3,070 `SOR-*`, 1,251 `SI-*` and
571 `C.R.C.,_c._*` files; the French file names write `DORS-`, `TR-` and `C.R.C.,_ch._`). Every English file has its
French twin. The manifest lists **953 Acts and 4,462 regulations in both languages, 10,830 files**, each with its
SHA-256, title and point-in-time date; its header states these counts. The Acts part of the manifest is unchanged from
the earlier Acts-only manifest (same 1,906 entries, same commit).

**448 instruments are not listed** and are named under `excluded` with the reason: their consolidation holds only a
repeal notice (`<Identification>` and `<Repealed>`, no `Body`, `Order` or `Schedule`), so there is no text to ingest.
The parser raises `ParseSkipped` for them (counted as skipped, not as an error, if they are ever fed to the pipeline).

The 18 Acts (all inspected; each file is 1.3-2.4 KB and holds the title, the repeal notice and, for some, a
`RecentAmendments` list):

| Chapter | Title (English) | Notice |
|---|---|---|
| C-0.4 | Canada Agricultural Products Act | [Repealed, 2012, c. 24, s. 78] |
| C-10.54 | Canada–Ukraine Free Trade Agreement Implementation Act | [Repealed, 2024, c. 3, s. 39] |
| C-15.21 | Canadian Environmental Assessment Act, 2012 | [Repealed, 2019, c. 28, s. 9] |
| C-31.7 | Claim Settlements (Alberta and Saskatchewan) Implementation Act | [Repealed, 2018, c. 27, s. 684] |
| C-36.75 | Consumer-Driven Banking Act | [Repealed, 2026, c. 3, s. 246] |
| D-1.65 | Digital Services Tax Act | [Repealed, 2026, c. 3, s. 126] |
| F-11.8 | First Nations Land Management Act | [Repealed, 2022, c. 19, s. 143] |
| F-12 | Fish Inspection Act | [Repealed, 2012, c. 24, s. 76] |
| I-6 | Department of Indian Affairs and Northern Development Act | [Repealed, 2019, c. 29, s. 382] |
| M-3.2 | Meat Inspection Act | [Repealed, 2012, c. 24, s. 77] |
| N-7 | National Energy Board Act | [Repealed, 2019, c. 28, s. 44] |
| P-16.8 | Poverty Reduction Act | [Repealed, 2019, c. 29, s. 316] |
| P-19.3 | Preclearance Act | [Repealed, 2017, c. 27, s. 64] |
| P-29.7 | Public Health Events of National Concern Payments Act | [Repealed, 2020, c. 5, s. 10] |
| P-29.71 | Public Health Events of National Concern Payments Act | [Repealed, 2020, c. 12, s. 11] |
| P-31.65 | Public Sector Equitable Compensation Act | [Repealed before coming into force, 2018, c. 27, s. 429] |
| P-8.3 | Perfluorooctane Sulfonate Virtual Elimination Act | [Repealed, 2023, c. 12, s. 68] |
| S-1.04 | Safe Drinking Water for First Nations Act | [Repealed, 2022, c. 10, s. 178] |

The 430 regulations of the same shape (for example `SOR-78-148` Special Voting Rules, "[Repealed, 2018, c. 31,
s. 398]"; `C.R.C.,_c._1465`, "[Repealed, SOR/2024-232, s. 1]") are listed one by one in the manifest. Wholly repealed
instruments that still carry their repealed text (a root `<Repealed>` plus a `Body`, `Order` or `Schedule`; 550 of
the listed regulations) **are** listed: they are recorded with every provision `repealed` (empty text, the notice as
the note) and a `repeal` lifecycle event dated from the notice's `lims:inforce-start-date`, and nothing of them is
searchable.

## The `<Regulation>` format and how it is parsed

- `Identification`: `InstrumentNumber` (`SOR/2001-227`, `SI/2000-100`, `C.R.C., c. 870`; in French `DORS/2001-227`,
  `TR/2000-100`, `C.R.C., ch. 870`), `RegistrationDate`, `ConsolidationDate`, `EnablingAuthority` (one or more
  `XRefExternal reference-type="act" link="C-38.8"`: the Act's consolidated chapter, identical in both languages),
  `LongTitle`/`ShortTitle`, `RegulationMakerOrder` (`P.C.`/`C.P.` or `T.B.`/`C.T.`, order number, date). Five
  instruments are numbered otherwise: four regulations enacted directly by a statute (`2010, c. 12, s. 91`,
  `2018, c. 12, s. 187`, `2022, c. 19, s. 116`, `2026, c. 3, s. 175`) and one order in council (`P.C. 1945-7421`); the
  Post Office Savings Bank Regulations are numbered by page (`C.R.C. 1955, p. 2561`).
- `Order` (3,882 instruments): the order in council or other order that made the regulation, or the whole text of a
  statutory instrument (proclamation, remission order, coming-into-force order). It is unnumbered and its
  `Provision` paragraphs differ in number between English and French in 92 instruments, so it is indexed as **one
  provision, eId `order`** (kind `order`, no pinpoint).
- `Body`: the same `Section`/`Subsection`/`Paragraph`/.../`Definition` structure as the Acts (63,412 sections), plus
  `Reserved` markers ("[B.01.407 to B.01.449 reserved]", editorial, not indexed) and, in 19 instruments, schedules
  inside the body (indexed like root-level schedules).
- `Schedule` (1,601 instruments at the root, 19 more inside the body): as for the Acts; `RelatedProvs` and `NifProvs`
  schedules are not indexed (the latter feeds `outstanding_changes`). A schedule whose only content is `<Repealed>` is
  recorded as repealed (for the Acts too: see "Effect on the Acts" below).
- Repealed definitions (`<DefinedTermEn>term</DefinedTermEn>[Repealed, ...]`) are `repealed` with the notice as the
  note (parser 1.0.0 kept them in force with the notice as their text).
- `RecentAmendments` (616 instruments), identification `Note`/`ReaderNote` and `Footnote`/`FootnoteRef` are
  editorial and not law text.
- **Identifiers.** Work URI `/ca/regulation/<number>` from the instrument number in its English form, lower-cased with
  abbreviation dots removed: `/ca/regulation/sor-2001-227`, `/ca/regulation/si-2000-82`, `/ca/regulation/crc-c-870`,
  `/ca/regulation/2010-c-12-s-91`. The French number is normalised to the same (`DORS`->`SOR`, `TR`->`SI`,
  `ch.`->`c.`, `art.`->`s.`, `C.P.`->`P.C.`), as are repository file names and the `link` attributes of
  cross-references, so `resolve_uri` maps both `.../eng/regulations/SOR-2001-227/` and
  `.../fra/reglements/DORS-2001-227/` to the same work. All 4,892 English files give 4,892 distinct work URIs. The
  provision eIds are the label-based ones of the Acts (`section-4-1-a`, `section-1-def-peace-officer`, `schedule-1`)
  plus `order`; the expression URI is `<work>/<eng|fra>@<lims:pit-date>`.
- **Dates.** `version_date` = `lims:pit-date`; `currency_date` = `lims:current-date`; `work_date` = `RegistrationDate`
  (absent for 544 instruments, mostly C.R.C. chapters: then `None`); each provision's `valid_from` = the latest
  `lims:inforce-start-date` in its subtree (`validity_basis: source_periods`), as for the Acts.
- **`ext`** (language-neutral, validated by `CaExt`): `instrument_number` (English form), `instrument_series`
  (`SOR`/`SI`/`CRC`, absent for the few numbered otherwise), `registration_date`, `enabling_acts` (consolidated
  chapters), `order_number`, `order_date`.
- **References.** `XRefExternal` of type `act` and `regulation` become `cites` links (parser 1.0.0 extracted only
  `act`). The
  enabling Acts become `made_under` links from the instrument's first provision (its order, where it has one). A
  regulation's reference to itself ("hereby makes the annexed ...") is not a citation.

### Effect on the Acts (parser 1.0.0 -> 1.1.0)

The same parser reads the Acts, so the committed (1.0.0) and the new (1.1.0) parser were run over all 1,906 listed Act
files on 2026-10-18 (190 s) and every document field and every provision (eId, kind, labels, status, text, note,
dates, headings, structure, references) compared. Titles, URIs, numbers, `ext`, outstanding changes, every eId and
every in-force provision's text and dates are identical. 576 files differ, in these four ways only:

- 1,398 repealed definitions in 319 files (for example `section-2-1-def-chief-review-officer` of A-11.44, printed
  "Chief Review Officer [Repealed, 2026, c. 3, s. 533]") are `repealed` with empty text and the notice as the note;
  parser 1.0.0 kept them in force with the notice as their text.
- 150 schedules in 76 files whose only content is the repeal notice, and 10 sections in 2 files (Excise Tax Act, E-15,
  ss 23.31, 23.33, 68.161, 75.1 and 89 in both languages) whose text is the notice repeated once per repealed
  subsection, are `repealed` in the same way.
- The 59 Acts (118 files) whose consolidation carries a root `<Repealed>` above their individually repealed text now
  record a `repeal` lifecycle event dated from the notice's `lims:inforce-start-date`.
- 1,261 provisions in 196 files gain 1,430 `cites` links to regulations (for example the Access to Information Act,
  s 3 "business day"(c), to the Federal Courts Rules, SOR/98-106).

Acts loaded with parser 1.0.0 carry the old reading of these 1,559 provisions until they are re-ingested from the same
pinned files (not done here).

### Citations (McGill Guide style, both languages; conventions to be confirmed by a Canadian reviewer)

| | English | French |
|---|---|---|
| SOR section | Leamy Lake Navigation Channel Regulations, SOR/98-547, s 2 | Règlement sur le chenal de navigation du lac Leamy, DORS/98-547, art 2 |
| Definition paragraph | ..., SOR/98-547, s 1 “peace officer”(a) | ..., DORS/98-547, art 1 « agent de la paix » a) |
| Schedule | ..., SOR/98-547, Sch 1 | ..., DORS/98-547, ann 1 |
| C.R.C. chapter | Armed Forces Postal Regulations, CRC, c 1274, s 4 | Règlement des postes pour les forces armées, CRC, c 1274, art 4 |
| Statutory instrument, its order | Reservation to the Crown Waiver Order (Tuktoyaktuk, N.W.T.), SI/2000-82 | Décret sur la renonciation aux terres réservées à la Couronne (Tuktoyaktuk, T.N.-O.), TR/2000-82 |

## Evidence (dev database `ulis_ca`, fresh and migrated, 2026-10-18)

- **Manifest build** (`scripts/build_justice_canada_manifest.py <checkout> --all`, 2 min 30 s): "10830 documents pinned
  at b7292cfb886d9e0ad60852e7eeb058ff07c5e71a: 953 Acts and 4462 regulations in both languages; 448 excluded (18 Acts
  and 430 regulations hold only a repeal notice, 0 failed)". Before: 1,906 documents (Acts only), 18 excluded.
- **Slice ingest**, every 44th listed regulation in both languages (**102 regulations, 204 files**; a temporary slice
  manifest under `var/`, outside the repository, read through the `git_snapshot` connector's hash-checked `fetch` on the
  already-present checkout of the pinned commit): 204 documents new, **102 instruments, 204 instrument versions,
  7,882 provision versions, 6,935 passages, 1,276 citation links** (0 resolved: the Acts they cite are not in this
  database), 9 `repeal` events, **0 errors, 0 conflicts, 0 skipped, 17 s**. Content tables 23.3 MB (2.95 KB per
  provision version); raw object store 6.7 MB.
- **`python -m ulis.cli verify-sources`**: **204 pass, 0 fail**, 7,882 units re-derived from the raw files, 5 s (run
  again later the same day: 204 pass, 0 fail, 11.5 s on a busier machine).
- **Searches** on the test database through `tests/test_justice_canada_regulations.py`: "peace officers or Commission
  employees acting in the course of their employment" -> `Leamy Lake Navigation Channel Regulations, SOR/98-547, s 2`
  (`document.type` `regulation`; limitations `OFFICIAL_CONSOLIDATION_REPRODUCED`, `PROFILE_UNVERIFIED`,
  `EQUALLY_AUTHENTIC_VERSIONS`); "les agents de la paix et les employés de la Commission" ->
  `Règlement sur le chenal de navigation du lac Leamy, DORS/98-547, art 2`; "Armed Forces Postal Regulations" ->
  `Armed Forces Postal Regulations, CRC, c 1274, s 1`. `provision_at` on `/ca/regulation/sor-98-547/~section-3`
  (repealed by SOR/2007-113) reports `repealed`; `~section-2` in French reports `in_force` with the DORS citation.
- **Tests**: `tests/test_justice_canada_regulations.py` (8: SOR, French twin, C.R.C., SI without body, stubs skipped,
  language-neutral numbers and URLs, ingest + search, repealed lookup) and `tests/test_justice_canada.py` (5,
  unchanged): 13 passed. Fixtures: real, unmodified files in `tests/fixtures/justice_canada/` (README names each; every fixture
  was checked byte for byte against the pinned clone on 2026-10-18).
- **Gates** (2026-10-18, on the branch rebased onto main `8de07ca`): `ruff check` and `ruff format --check` clean (435
  files), `mypy ulis` clean (236 files); the manifest rebuilt from the pinned clone is identical to the committed one
  (2 min 24 s). Full suite with `ULIS_TEST_DATABASE_URL` on the test database `ulis_test_ca`: **2015 passed, 0 failed**
  (13 min 19 s).

## Full-load estimate (regulations, both languages)

From a parse of all 8,924 listed regulation files with the new parser (83 s, no error; `var/corpus-stats` run of
2026-10-18) and the slice's measured costs:

| | Measured on the full file set | Estimate for the load |
|---|---|---|
| Files, raw bytes | 8,924 files, 397,650,963 bytes (379 MiB) | raw object store about 0.4 GB |
| Provisions parsed | 614,361 (126,824 sections, 127,910 subsections, 229,330 paragraphs, 64,220 subparagraphs, 41,908 definitions, 8,329 clauses, 992 subclauses, 72 sub-subclauses, 7,012 schedules, 7,764 orders) | about 614,000 provision versions (one per provision and language) |
| Of which repealed / prospective | 33,031 / 4,538 | recorded, not searchable |
| Passages (the indexer's rule: an index unit in either language, not repealed, with text; it gives exactly the slice's 6,935) | 541,745 | about 542,000 passages |
| Citation references | 50,236 | about 50,000 citation links (the `made_under` and `cites` links to Acts resolve once the Acts are loaded) |
| Outstanding changes (amendments not in force) | 766 | 766 |
| Wholly repealed instruments with text kept | 550 (1,100 `repeal` events) | 550 instruments with every provision repealed |
| Database | 2.95 KB per provision version with passages and indexes (slice) | about **1.8 GB** of content tables |
| Time | 464 provision versions per second (slice, 17 s for 7,882); `verify-sources` 1,580 units per second | about **22 min** to ingest and 7 min to verify on this machine; large instruments with big tables (Food and Drug Regulations, C.R.C., c. 870) may be slower, so allow 30-45 min |

English and French share identical eId sets in 3,877 of the 4,462 instruments; over the whole set 304,279 eIds are
shared, 2,895 exist only in English and 2,904 only in French (0.95%: repealed definitions that lost their paired term,
sections labelled in words such as "15. and 16" / "15. et 16", and the 221 instruments whose two language files hold
different numbers of provision elements). Series: 2,770 SOR, 1,149 SI, 538 C.R.C., 5 numbered otherwise.

## Known gaps and limitations

- Current consolidation only (one point in time per instrument and language); earlier wordings are not indexed and
  dates before a provision's current wording are reported as `history_not_indexed`.
- 448 instruments (18 Acts, 430 regulations) hold only a repeal notice and are not listed; named in the manifest.
- The making order is one unnumbered provision without a pinpoint (the recital is paragraphed differently per language).
- The "Related provisions" schedule, `RecentAmendments`, identification notes, footnotes and `Reserved` markers are not
  indexed; amendments not in force are reported as outstanding changes and not applied.
- One instrument, the Post Office Savings Bank Regulations, is numbered by a different page in each language
  (`C.R.C. 1955, p. 2561` / `p. 2802`), so its two versions have two work URIs (`/ca/regulation/crc-1955-p-2561` and
  `/ca/regulation/crc-1955-p-2802`).
- A schedule whose label carries no number (a lone "SCHEDULE"/"ANNEXE": 1,273 English and 1,303 French schedules in
  1,024 instruments) has the eId `schedule`, repeated ones numbered in document order; the two languages pair up except
  in the 17 instruments where their counts of such schedules differ.
- No case law, no bills, no provincial or territorial law (see the provincial profiles and
  [ca-au-subnational.md](ca-au-subnational.md)); the profile is `draft_unverified` and the citation conventions await a
  Canadian reviewer. The full regulation load has not been run here (slice only, above).
