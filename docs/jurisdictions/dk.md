# Denmark (DK) - Retsinformation

**Status:** pilot, profile `draft_unverified` (not reviewed by counsel). Source `dk-retsinformation`,
connector `retsinformation`, parser `retsinformation_lexdania`, module `dk`.

## Source and licence

- Retsinformation (Civilstyrelsen, Ministry of Justice). Documents by ELI as Lex Dania XML:
  `https://www.retsinformation.dk/eli/lta/<year>/<number>/xml` (formats listed in the
  [FAQ](https://www.retsinformation.dk/faq)). The harvest API (`api.retsinformation.dk`, a change
  feed open 03:00-23:45, one call per 10 s) is not used; ULIS keeps one request per 10 s anyway.
- [Terms](https://www.retsinformation.dk/api): "gratis, ikke-eksklusiv og ubegrænset brugsret"
  (copy, distribute, modify, commercial use); "Love og regler er ikke underlagt ophavsret, jf.
  ophavsretslovens § 9". Conditions: no implied endorsement by Civilstyrelsen; no inappropriate or
  disproportionate load. `robots.txt` has no Disallow and no crawl delay. Quotes in the source YAML.
- Legal status: Lovtidende is the authentic publication (signed PDF; ELI legal value "definitive").
  A lovbekendtgørelse (LBK) is an official consolidation made by the responsible ministry and
  published in Lovtidende A; the XML is an "official" format. ULIS records `text_origin =
  official_consolidation`, `authoritative = false`. Ministries own and update their documents;
  the current/historic marking "er vejledende og uden retlig betydning" (FAQ).

## What is ingested (manifest `config/manifests/dk-retsinformation.yaml`)

| Act | Consolidations (oldest first) |
|---|---|
| Forvaltningsloven | LBK 1365/2007, 988/2012, 433/2014 (current) |
| Funktionærloven | LBK 81/2009, 1002/2017 (current) |
| Straffeloven | LBK 1294/2025 |
| Aftaleloven | LBK 193/2016 |
| Arveloven | LBK 1347/2021 |
| Databeskyttelsesloven | LBK 289/2024 |

Language `da` (PostgreSQL `danish`). Provisions: paragraphs (`par-9a`), stykker (`par-1-stk-2`),
numbered/lettered items (`par-1-stk-2-nr-1`, `...-litra-a`), and the amending acts' commencement
provisions reproduced in a consolidation (`ikraft-lov-2013-638-par-3`). Chapters, parts and
paragraph-group headings are context. Footnotes go to `note`; "(Ophævet)" marks a repeal.

## Dates and versions

Each LBK is a whole-law snapshot (`revision_snapshot`) dated by its own date (`Meta/DiesSigni`, the
date in "LBK nr 433 af 22/04/2014"), which is also the currency date. The source does not date
provisions: when each consolidated amendment took effect is only stated in prose. So a wording
may have applied before its snapshot date, and between two consolidations the earlier wording is
shown even where an amendment had already taken effect (SNAPSHOT_DATES / derived end dates).
Amending acts that Retsinformation lists as later changes ("Senere ændringer til forskriften") are
outstanding changes of the current consolidation (instrument-wide) and `amendment` events.
`Meta/StartDate` is not used (it differs from the Lovtidende publication date for some documents).

## Citations

`§ 276 i straffeloven`, `§ 3, stk. 2, nr. 1, i forvaltningsloven`, `§ 81, nr. 1, litra a, i
straffeloven`, `§ 3 i lov nr. 638 af 12. juni 2013` (commencement provision). The consolidation is
identified by the expression (`/dk/lov/forvaltningsloven/dan@lbk-2014-433`) and its ELI (source
document URI); `ext` holds work-level data only (`popular_title`, `document_type`), because the
instrument's `ext` is fixed by the first consolidation ingested. No genitive form, no "jf. lbk".

## Known gaps

- Grundloven is not ingested: no Lex Dania content for documents before 24 September 2007, no PDF.
- Original and amending acts (LOV H / LOV C), executive orders, case law and treaties are not
  ingested; annexes (e.g. the GDPR reproduced in Databeskyttelsesloven) and introductory formulas
  are skipped. History only for two Acts; earlier dates are a history gap.
- A re-fetched document whose metadata changed (e.g. marked historic) is refused as a conflict.
- Faroe Islands and Greenland are out of scope; EU membership is added by the lead.

## Evidence (2026-10-14)

- Real run on `ulis_dk`: `migrate`, `sync-catalog`, `ingest dk-retsinformation` - 9 documents,
  6 instruments, 9 versions, 2,587 provision versions created, 309 corroborated, 2,149 passages,
  0 conflicts, 0 errors (9 HTTP requests for documents). `verify-sources`: 9/9 pass.
- Searches (`ulis.cli search --jurisdiction DK`): "straffes for manddrab" -> `§ 237 i
  straffeloven`; "tvangsarv livsarvinger" -> `§ 53, stk. 1, i arveloven`; "opsigelse fra
  arbejdsgiverens side varsel" -> `§ 2, stk. 5, i funktionærloven`; "personnummer behandles" ->
  `§ 11, stk. 1, i databeskyttelsesloven`; as of 2013-06-01 "aktindsigt i sagens dokumenter" ->
  `§ 10, stk. 1, i forvaltningsloven`.
- Checks: ruff check, ruff format --check, mypy clean; full suite 263 passed against
  `ulis_test_dk` (13 DK tests in `tests/test_retsinformation.py`).
