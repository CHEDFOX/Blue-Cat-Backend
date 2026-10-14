# Czechia (CZ) - e-Sbírka open data

**Status:** pilot, profile `draft_unverified` (not reviewed by counsel). Source `cz-esbirka-opendata`, connector
`esbirka_opendata`, parser `esbirka_opendata_json`, module `cz`, language `cs` (EU membership is added separately).

## Source and licence

- The Ministry of the Interior publishes the whole e-Sbírka as daily JSON dumps in an Apache directory,
  `https://opendata.eselpoint.gov.cz/datove-sady-esbirka/` (the host named as `openDataBasePath` in the portal's
  configuration; `robots.txt` answers 404). Four files are used: `001PravniAktZneni` (versions of acts, 178 MB gz),
  `003PravniAktZneniFragment` (fragments of every version, 1.24 GB), `004PravniAktFragment` (fragment texts, 531 MB) and
  `006PravniAktMetadata` (6 MB). The portal `https://e-sbirka.gov.cz` (was `www.e-sbirka.cz`, now 308) and `api.e-sbirka.cz` are
  JavaScript shells: their terms could not be read, and the REST API is not used.
- **Licence:** no licence text of the publisher's own was found, so the approval rests on statutes read from the corpus
  itself: autorský zákon 121/2000 Sb. § 3 a) (legislation is not protected by copyright), 106/1999 Sb. § 5a (open data) and
  222/2016 Sb. § 1, § 6, § 7 (the Collection is the official gazette, kept by the Ministry, public at a distance). Counsel
  review of database rights and of the portal's terms is recommended. All quotes are in the source YAML.
- **Authority:** 222/2016 Sb. § 7 odst. 1 makes the consolidated text published with an amending act binding, but this is not
  established for every time version in the dumps, so `authoritative = false`, `official_consolidation`,
  source authority `official_non_authentic`.

## What is ingested

`config/manifests/cz-esbirka-opendata.yaml`: the whole history up to 2026-10-14 of 31 main acts of the Sbírka zákonů (Ústava, Listina,
občanský zákoník, trestní zákoník, zákoník práce, OSŘ, trestní řád, zákon o obchodních korporacích, 110/2019, tax, consumer and
other codes) and the version in force of every other act, decree and government regulation in force (`in_force: true`). On the
dumps of 2026-10-14 that is **24,809 acts, 26,327 versions, 6.75 million version-fragments** (4.85 GB slice, built in 10 min;
the sandbox could not ingest all of it, see below). Treaties (`sm`) and the historical Úřední listy are not selected.

## How versions and dates work

e-Sbírka gives each act a series of versions: as promulgated (ELI date `0000-00-00`) and a consolidated "časové znění" for each day
the text changes, each with `účinnost od/do`. The as-promulgated text starts the day the first consolidated one does, so only an
act without a consolidated version of that day keeps it. A version is a **whole-instrument snapshot**
(`validity_basis = revision_snapshot`, shared `ulis/ingestion/snapshots.py`): every provision gets the version's period (end =
day after `účinnost do`, open if not before `as_of`); unchanged wording continues across versions and an absent provision ends.
A fragment with its own later effective day (`odlišná účinnost`) starts then and is left out of a version that ends before it - the
first Civil Code version (2012-03-22 to 2013-12-31) has no provision, the Labour Code's first has only its annex. Versions after
`as_of` are not taken. `source_periods` was not used: the dump dates versions, not provisions.

Identifiers: work `/cz/sb/<year>/<number>`, expression `.../ces@<ELI date>`, provision `eid` = ELI path without containers
(`par_2910.odst_1.pism_a`); `num_path` `["sec:2910", "sub:1", "pt:a"]` from the fragment labels, in document order.
Text keeps source wording; `<var>` labels are dropped, `<sup>` becomes superscript digits, tables become lines of cells, footnotes
become the `note` of the provisions that mark them. Acts without numbered provisions (sdělení, usnesení) become one `text` provision.

## Citations

`§ 2910 občanského zákoníku`, `§ 40 odst. 1 trestního zákoníku`, `§ 52 odst. 1 písm. a) zákoníku práce`, `čl. 2 odst. 3 Ústavy České
republiky`, `§ 5 odst. 2 zákona č. 110/2019 Sb.`, `§ 3 vyhlášky č. 10/2020 Sb.`. Genitive names of 12 codes are a table in
`ulis/jurisdictions/modules/cz.py`; other acts are cited by number. Convention to be confirmed by a Czech reviewer.

## Results (2026-10-14)

- Dev run into `ulis_cz` with a temporary manifest (5 acts with full history - 1/1993, 110/2019, 40/2009, 89/2012, 262/2006 - and 4
  current: 2/1993, 121/2000, 106/1999, 222/2016): 144 versions seen, 142 ingested, 2 skipped (Criminal Code 2009-02-09 and Civil Code
  2012-03-22, no provision applies before their effective day), 9 instruments, 18,653 provision versions created and 362,511
  corroborated by later versions, 16,101 passages, 0 errors, 598 s. `verify-sources`: 142 of 142 pass.
- Parser robustness on 1,500 random versions of the full committed slice: 1,479 parsed, 21 skipped (no text), 0 errors.
- Searches (CLI): "Škůdce, který vlastním zaviněním poruší povinnost" -> `§ 2910 občanského zákoníku`; "Kdo jiného úmyslně usmrtí" -> `§ 140
  odst. 1 trestního zákoníku`; "přidělovat zaměstnanci práci podle pracovní smlouvy" -> `§ 38 odst. 1 písm. a) zákoníku práce`; "Lid je
  zdrojem veškeré státní moci" -> `čl. 2 odst. 1 Ústavy České republiky`; with `--as-of 1996-05-01`, `čl. 10` returns the 1993 wording
  ("Ratifikované a vyhlášené mezinárodní smlouvy o lidských právech...") instead of the 2002 one.
- Tests: `tests/test_esbirka_opendata.py` 16 (parser, citations, slice/connector offline, resumable download, 3 PostgreSQL). Full suite: 768 passed (an earlier run had 12
  unrelated failures from exhausted PostgreSQL connection slots shared with other jobs; they pass alone and on rerun). ruff, mypy clean.

## Known gaps

Mirrors `coverage.known_gaps` of the profile: the full in-force ingest (about 6.75 million fragment rows, estimated 2 h at the rate of the
dev run) was not run here; no treaties, historical gazettes, ordinances or case law; no cross-references between acts (the dump gives
link ids only; `008PravniAktOdkaz` unused); text outside numbered provisions (enacting formulas) is dropped when numbered provisions
exist; text continuing after a list is appended to the list's provision; tables and annexes are flattened; versions after the retrieval
date are not ingested; Czech has no PostgreSQL stemmer (`simple` configuration, exact word forms needed); the dump layout may change.
