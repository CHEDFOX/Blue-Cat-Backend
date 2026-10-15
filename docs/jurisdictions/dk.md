# Denmark (DK) - Retsinformation

**Status:** the six pilot Acts plus the lovbekendtgørelser (LBK H) that Retsinformation marks as in force among the documents
classified so far: 112 laws, a **fraction** of the laws in force (see "What is ingested"). Profile `draft_unverified` (0.2.0, not
reviewed by counsel). Source `dk-retsinformation`, connector `retsinformation`, parser `retsinformation_lexdania` 1.2.0, module `dk` 1.2.0.

## Source and licence

- Retsinformation (Civilstyrelsen, Ministry of Justice). Documents by ELI as Lex Dania XML:
  `https://www.retsinformation.dk/eli/lta/<year>/<number>/xml` (formats in the [FAQ](https://www.retsinformation.dk/faq)). The harvest API
  (`api.retsinformation.dk`, a change feed open 03:00-23:45, one call per 10 s, only the last 10 days) is not used; ULIS keeps one
  request per 10 s anyway.
- [Terms](https://www.retsinformation.dk/api): "gratis, ikke-eksklusiv og ubegrænset brugsret" (copy, distribute, modify, commercial
  use); "Love og regler er ikke underlagt ophavsret, jf. ophavsretslovens § 9". Conditions: no implied endorsement by Civilstyrelsen;
  no inappropriate or disproportionate load. `robots.txt` has no Disallow and no crawl delay (it declares the sitemap). Quotes in the
  source YAML.
- Legal status: Lovtidende is the authentic publication (signed PDF; ELI legal value "definitive"). A lovbekendtgørelse is an official
  consolidation made by the responsible ministry and published in Lovtidende A; the XML is an "official" format. ULIS records
  `text_origin = official_consolidation`, `authoritative = false`. The current/historic marking "er vejledende og uden retlig betydning".

## What is ingested (manifest `config/manifests/dk-retsinformation.yaml`)

- **Why only a part.** Retsinformation has no public list of its lovbekendtgørelser. The one public index, the ELI sitemap (`/sitemap.xml`,
  declared in robots.txt: 21 files, 200,007 documents of every kind, 62,213 Lovtidende A `/eli/lta/<year>/<number>`, 34,435 of them since
  2007, the year of the first Lex Dania XML), gives each document's ELI but not its type; the website's search is a JavaScript
  application. The type and status are in each document's ELI metadata (`/eli/lta/<year>/<number>.json`, about 50 kB):
  `type_document ...#LBKH` and `in_force` (`InForce-inForce` / `notInForce`).
- **`scripts/build_dk_manifest.py`** reads the sitemap, asks for the metadata of the Lovtidende A documents newest first (one request per
  10 s), keeps the LBKH in force, and appends every answer to `config/manifests/dk-retsinformation.classified.tsv`, so a later run goes on
  where this one stopped (`--from-year 2007 --max-requests N`). The pilot Acts keep their consolidation series; every other law is one LBK.
- **Done so far:** 1,501 documents classified (all 892 of 2026 so far and the newest 609 of 2025, down to `lta/2025/1224`): 115 are LBKH,
  107 in force and listed (the manifest has 112 works: 6 pilot + 106 others; one found LBK is a pilot Act). The share of LBKH in force is
  9% in 2026 and 4% in late 2025 (newer consolidations supersede older ones).
- **Scale.** The other 32,934 candidates cost **about 91 hours** at 10 s (4.8 hours per year of candidates); each LBK found costs one more
  request. Assumption: about 800 laws are in force (not known; the older LBKs are mostly superseded). The 115 stored documents hold
  54,681 provision versions (median 246 per consolidation, largest 4,834) and the database 139 MB with passages (2.3 KB per provision version), so
  800 laws would be **about 380,000 provision versions and about 0.9 GB**. Widening needs no code change, only the hours.

| Pilot Act | Consolidations (oldest first) |
|---|---|
| Forvaltningsloven | LBK 1365/2007, 988/2012, 433/2014 (current) |
| Funktionærloven | LBK 81/2009, 1002/2017 (current) |
| Straffeloven | LBK 1294/2025 |
| Aftaleloven | LBK 193/2016 |
| Arveloven | LBK 1347/2021 |
| Databeskyttelsesloven | LBK 289/2024 |

Language `da` (PostgreSQL `danish`). Provisions: paragraphs (`par-9a`, `par-4a` for "§ 4 A", `par-26og27` for a repealed pair "§§ 26 og
27"), stykker (`par-1-stk-2`), numbered/lettered items (`par-1-stk-2-nr-1`, `...-litra-a`), and the amending acts' commencement
provisions reproduced in a consolidation (`ikraft-lov-2013-638-par-3`). Chapters, parts and paragraph-group headings are context.
Footnotes go to `note`; "(Ophævet)" marks a repeal. Parser 1.2.0: a law without a `PopularTitle` is named by its consolidation's title
without "Bekendtgørelse af" (`ext.title_derived`); a `<Praeambel>` is skipped; labels "§ 4 A", "§ 343 ab", "§§ 96 k-o", "§§ 9-9 b",
"§§ 26 og 27" are read; an `<AendringCentreretParagraf>` is a paragraph.

## Dates and versions

Each LBK is a whole-law snapshot (`revision_snapshot`) dated by its own date (`Meta/DiesSigni`, the date in "LBK nr 433 af 22/04/2014"),
which is also the currency date. The source does not date provisions: when each consolidated amendment took effect is only stated in
prose. So a wording may have applied before its snapshot date, and between two consolidations the earlier wording is shown even where an
amendment had already taken effect (SNAPSHOT_DATES / derived end dates). Amending acts that Retsinformation lists as later changes
("Senere ændringer til forskriften") are outstanding changes of the current consolidation and `amendment` events. `Meta/StartDate` is
not used. For the laws found through the ELI metadata only the current LBK is ingested, so there is no earlier wording.

## Citations

`§ 276 i straffeloven`, `§ 3, stk. 2, nr. 1, i forvaltningsloven`, `§ 81, nr. 1, litra a, i straffeloven`, `§ 1 i lov om fleksydelse`,
`§ 4 A i aktieavancebeskatningsloven`, `§§ 26 og 27 i ...` (repealed pair), `§ 3 i lov nr. 638 af 12. juni 2013` (commencement provision).
The consolidation is identified by the expression (`/dk/lov/forvaltningsloven/dan@lbk-2014-433`) and its ELI.

## Known gaps

- **Partial**: only the LBKs found in the classified documents are listed; a law whose current consolidation is older (before the end of
  2025) is missing until the manifest is widened. Grundloven is not ingested (no Lex Dania content before 24 September 2007, no PDF).
- Original and amending acts (LOV H / LOV C), executive orders, case law and treaties are not ingested; annexes (e.g. the GDPR reproduced
  in Databeskyttelsesloven), preambles and introductory formulas are skipped. History only for two Acts.
- A re-fetched document whose metadata changed (e.g. marked historic) is refused as a conflict. A document with no structured XML or an
  unknown structure is an ingest error and skipped; the connector also skips a 404 and the website's HTML shell.
- Faroe Islands and Greenland are out of scope; EU membership is added by the lead.

## Evidence

- Pilot (2026-10-14, `ulis_dk`): 9 documents, 6 instruments, 2,587 provision versions, `verify-sources` 9/9.
- Widening (2026-10-15): about 1,500 metadata requests and one XML request for each LBK found, all at 10 s. The first run of the committed manifest on a
  fresh database had 12 errors in 112 laws (labels such as "§§ 26 og 27." and "§ 343 ab.", an `<AendringCentreretParagraf>`), fixed in
  the parser and covered by tests; the final run: **115 documents, 112 instruments, 54,681 provision versions, 46,477 passages, 0
  errors**, `verify-sources` **115 of 115 pass** (54,990 units).
- Searches (`jurisdiction=DK`): "formålet med loven er at give personer visiteret fleksydelse" -> `§ 1 i lov om fleksydelse`; "straffes
  for manddrab" -> `§ 237 i straffeloven`; "folkeskolen skal give eleverne" -> `§ 1, stk. 1, i folkeskoleloven`.
- Checks: ruff, mypy clean; `tests/test_retsinformation.py` (18 tests, 3 PostgreSQL) incl. a manifest-loads test.
