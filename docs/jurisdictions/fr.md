# France (FR)

Profile `FR` 0.2.0 (`draft_unverified`), module `fr`, language `fr`. Sources: `fr-dila-legi` (legislation) and the case-law
sources `fr-dila-constit`, `fr-dila-cass`, `fr-dila-inca`, `fr-dila-jade`. EU membership is not recorded in the profile (left to the lead).

## Source and licence
- Publisher: Direction de l'information légale et administrative (DILA), open data on `https://echanges.dila.gouv.fr/OPENDATA/<DB>/`
  (`LEGI`, `CONSTIT`, `CASS`, `INCA`, `JADE`): one **stock** (`Freemium_<db>_global_20250713-140000.tar.gz`; LEGI 1.17 GB, JADE 1.24 GB,
  INCA 687 MB, CASS 260 MB, CONSTIT 12.5 MB) and one **incremental** archive per publication day (LEGI: 461 archives, 1.9 GB, up to 2026-10-13).
- Terms, read on 2026-10-14 from the publisher: the host has no `robots.txt` (404, no restriction); each database's data sheet
  (`.../OPENDATA/<DB>/DILA_<DB>_Presentation_*.pdf`) says "réutilisables gratuitement sous licence ouverte v2.0" and obliges
  reusers to cite the DILA, the long download URL, and the name and date of the downloaded file; a first integration must use the latest
  stock. `https://www.dila.gouv.fr/home/open-data-et-api` bases the free reuse on the arrêté of 24 June 2014. Case-law sheets add that
  names of natural persons are pseudonymised (CNIL recommendation of 29 November 2001) and refer to an "Avertissement" document not found.
  `legifrance.gouv.fr` answered the one request made (robots.txt) with a Cloudflare challenge: nothing more was requested from it. The Etalab licence text was not reachable here.
  Conclusion: reuse allowed with attribution (`attribution_text` in each source file carries it); counsel review recommended.
- Consolidated texts are the DILA's consolidation, not authentic (`official_consolidation`, `authoritative = false`); decisions are pseudonymised publications.

## What is ingested
- **Legislation** (`dila_legi` connector, `dila_legi_xml` parser): per text one bundle (zip) of the publisher's XML files byte for byte + `bundle.json`.
  Manifest: 13 required texts (Constitution of 1958, Codes civil, pénal, du travail, de commerce, de procédure civile/pénale, de la consommation,
  de la propriété intellectuelle, CGI, de justice administrative, CRPA, Loi 78-17) and every text of nature CODE (108, 31 repealed), CONSTITUTION (3), LOI,
  LOI_ORGANIQUE, LOI_CONSTIT, ORDONNANCE, DECRET_LOI: **5,280 texts** in the stock (761,513 files: 630,297 article versions, 122,714 section versions),
  5,285 after the increments to 2025-07-31. Decrees (55,000) and orders (83,000) are not selected (add `DECRET`/`ARRETE` to `natures`).
- **Case law** (`dila_case_law` connector, `dila_juri_xml` parser; archives read newest first, so the latest version of a decision wins, deletion lists honoured):
  `fr-dila-constit` all 7,112 decisions of the stock (+ increments); `fr-dila-cass` Cour de cassation decisions of 2000 onwards (about 40,000 of 144,000);
  `fr-dila-inca` unpublished Cour de cassation decisions of 2020 onwards; `fr-dila-jade` Conseil d'État and Tribunal des conflits (CAA/TA can be added).
  Selection keys: `juridictions` (exact or `prefix*`), `year_from/year_to`, `natures`, `max_documents`.

## Dates and versions
`validity_basis = source_periods`. One expression per text holds the whole history (`version_history`), dated at the last archive applied (`currency_date`).
An article **number** is one provision (`art-L233-3`, `R*229-8` = `R229-8`); each DILA article file is one provision version with `DATE_DEBUT`/`DATE_FIN`
(`2999-01-01` = open end; a year ≥ 2200 = "date not fixed": no date, status `prospective`). When a version ends and none starts then (abrogation, transfer),
a `repealed` version starts at that date (note: `ETAT ABROGE; DATE_FIN ...`). `MODIFIE_MORT_NE` (empty period) and `ANNULE` versions are not ingested.
Future-dated versions (`VIGUEUR_DIFF`) are `in_force` from their stated date (dump currency applies). Sections are container provisions with dated titles;
an article's heading is its innermost section title. The note holds the `NOTA` and the source's `CREATION`/`MODIFICATION`/`ABROGATION` link labels.
Increments overlay the stock file by file (`liste_suppression_legi.dat` honoured). A new run after new archives yields a new expression per text.

## Citations
`C. civ., art. 1240` · `C. trav., art. L. 1234-5` · `Loi n° 78-17 du 6 janvier 1978, art. 22` · `Cons. const., 7 janv. 2022, n° 2021-960 QPC, § 5` ·
`Cass. civ. 1re, 12 juill. 2012, n° 11-17.738` · `CE, 31 mars 2025, n° 493033` · `CAA Paris, 11 juill. 2025, n° 22PA02724`. Abbreviations are in the profile (to verify).
Judgment paragraphs: headnotes (`SOMMAIRE`), the numbered `§` of the decision, the operative part. Links: the Cour de cassation's own list of texts applied
(`source_markup`) and strict patterns (`articles 1569 et 1570 du code civil`, `article 61-1 de la Constitution`, `loi n° 78-17`) to ingested texts (`pattern`).

## Known gaps
No decrees/orders, KALI, JORF, treaties, territorial applicability, link graph between legislative texts; visas and signatures not stored; articles not split into
alinéas; versions listed by the DILA but missing from the dump are a history gap; 11 texts present in both trees of the stock keep the first copy; a text that
holds several consolidated-version files (successive titles, e.g. Code rural) is named by the latest, and earlier titles are not kept; a decision listed twice in
one archive (some in the CASS stock) keeps the first copy; some JADE headnotes
are published with broken characters and stored as published; no names of judges, lawyers or parties are stored outside the text; INCA/JADE stocks were not
downloaded in the build environment (disk; parser checked on daily archives); case law of cours d'appel and first instance, and subsequent history, are not covered.
A corrected republication of a decision already ingested is refused by the pipeline ("already recorded from different source bytes"): load a source in one run.

## Numbers from the build run (2026-10-14)
Development database (PostgreSQL 16), sync-catalog, ingestion in batches (`scripts/ingest_in_batches.py`), then `verify-sources` and searches.
- **Legislation**: stock of 2025-07-13 plus the 18 daily archives to 2025-07-31 (the increments up to 2026-10-13 are not applied in this run). All 5,285 selected
  texts ingested, 0 errors: 5,275 stored (10 texts without any article text are skipped), 5,275 expressions: 3,564 laws, 1,437 ordinances, 111 organic acts,
  108 codes, 33 decree-laws, 19 constitutional acts, 3 constitutions. 464,202 provisions, **861,397 provision versions** (744,895 in force, 116,313 `repealed`
  markers, 189 prospective; 427,220 with a stated end, 424,379 open, 9,798 ended by a successor), 622,134 passages, 1.2 GB of raw bundles; the earliest version
  starts in 1539. 2,667 texts have an article with more than one version. Ingestion took about 40 minutes in several resumable runs (13 required texts: 697 s).
- **Case law** (dev database): CONSTIT all 7,408 decisions (1958-11-22 to 2026-10-01; 88,291 paragraphs); CASS 5,474 decisions of 2022-01-04 to 2026-09-17
  (106,679 paragraphs; a slice of the committed manifest, which starts in 2000); JADE 682 decisions of two daily archives of July 2025 (626 CAA, 55 CE, 1 TC;
  the stock was not downloaded); INCA none (stock not downloaded). 207,463 paragraphs, 104,203 links to provisions of which 103,273 (99.1%) resolve to an
  ingested article (6,845 from the Cour de cassation's own list, 96,428 from strict patterns).
- **Searches** (`as_of`): "tout fait quelconque de l'homme qui cause à autrui un dommage" gives `C. civ., art. 1240` at 2026-01-01 and `C. civ., art. 1382` at
  2010-01-01; "responsable du traitement données à caractère personnel" gives `Loi n° 78-17 du 6 janvier 1978, art. 112` first; "la souveraineté nationale
  appartient au peuple" gives `Constitution du 4 octobre 1958, art. 3` then `Cons. const., 9 avr. 1992, n° 92-308 DC, § 10`; "période d'essai contrat de travail
  renouvellement" gives `C. trav., art. L. 1221-23`.
- **verify-sources**: VERIFY_RESULT
- Tests: 789 passed in the whole suite (`tests/test_dila_legi.py` and `tests/test_dila_case_law.py` hold the French ones); ruff and mypy clean.
