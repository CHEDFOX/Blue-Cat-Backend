# France (FR)

Profile `FR` 0.2.0 (`draft_unverified`), module `fr`, language `fr`. Sources: `fr-dila-legi` (legislation) and the case-law
sources `fr-dila-constit`, `fr-dila-cass`, `fr-dila-inca`, `fr-dila-jade`. EU membership is not recorded in the profile (left to the lead).

## Source and licence
- Publisher: Direction de l'information légale et administrative (DILA), open data on `https://echanges.dila.gouv.fr/OPENDATA/<DB>/`
  (`LEGI`, `CONSTIT`, `CASS`, `INCA`, `JADE`): one **stock** (`Freemium_<db>_global_20250713-140000.tar.gz`; LEGI 1.17 GB, JADE 1.24 GB,
  INCA 687 MB, CASS 260 MB, CONSTIT 12.5 MB) and one **incremental** archive per publication day (LEGI: 461 archives, 1.9 GB, up to 2026-10-13;
  466 up to `LEGI_20261017-211053.tar.gz` when the listing was read again on 2026-10-18 at 00:38 UTC).
- **Stock pin** (`config/manifests/fr-dila-legi.yaml`): `Freemium_legi_global_20250713-140000.tar.gz`, 1,171,027,316 bytes, SHA-256
  `f862b4a2238e11ec3c48ec70f3b1c68bac0bb1985907841666b98608653d4eca`. DILA's data sheet obliges a first integration to use the latest stock:
  on 2026-10-18 the host's index still listed this stock of 2025-07-13 as the only one (no newer stock exists), so the pin stands; the
  file downloaded again that day (one request, 865 s, resumable connector) had the same size and SHA-256. The stock holds 2,276,109 text, article and section files.
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
  5,285 after the increments to 2025-07-31; and, since 2026-10-18, **DECRET**: **53,383 decrees** in the stock (version files by state:
  42,673 VIGUEUR, 8,595 ABROGE, 1,237 PERIME, 711 MODIFIE, 99 ANNULE, 34 ABROGE_DIFF, 27 VIGUEUR_DIFF, 7 MODIFIE_MORT_NE; 676,228 files,
  520,693 of them article versions; 6,355 files present in both trees are kept once). Orders (ARRETE: 81,452 texts, 598,608 article files)
  are not selected: see "Decrees and orders" below.
- **Case law** (`dila_case_law` connector, `dila_juri_xml` parser; archives read newest first, so the latest version of a decision wins, deletion lists honoured):
  `fr-dila-constit` all 7,112 decisions of the stock (+ increments); `fr-dila-cass` Cour de cassation decisions of 2000 onwards (about 40,000 of 144,000);
  `fr-dila-inca` unpublished Cour de cassation decisions of 2020 onwards; `fr-dila-jade` Conseil d'État and Tribunal des conflits (CAA/TA can be added).
  Selection keys: `juridictions` (exact or `prefix*`), `year_from/year_to`, `natures`, `max_documents`.

## Dates and versions
`validity_basis = source_periods`. One expression per text holds the whole history (`version_history`), dated at the last archive applied (`currency_date`).
An article **number** is one provision (`art-L233-3`, `R*229-8` = `R229-8`); each DILA article file is one provision version with `DATE_DEBUT`/`DATE_FIN`
(`2999-01-01` = open end; a year ≥ 2200 = "date not fixed": no date, status `prospective`). When a version ends and none starts then (abrogation, transfer),
a `repealed` version starts at that date (note: `ETAT ABROGE; DATE_FIN ...`). `MODIFIE_MORT_NE` (empty period) and `ANNULE` versions are not ingested.
A version the database leaves **without any period** (`DATE_DEBUT` and `DATE_FIN` both `2999-01-01`, no `ETAT`; `TYPE ENTIEREMENT_MODIF`: an article
whose whole content amends other texts, for which the consolidation prints only "A modifié les dispositions suivantes : ...") is not ingested either
(parser 1.1.0): nothing can be attested for any day, and that summary is the DILA's note, not the words of the article. The article (or section) is
recorded as a provision without a version, so the structure of the text is complete; the amendments appear in the notes of the amended articles.
When that summary stands in place of the words of a *dated* version, it goes to the note and the text is empty.
Future-dated versions (`VIGUEUR_DIFF`) are `in_force` from their stated date (dump currency applies). Sections are container provisions with dated titles;
an article's heading is its innermost section title. The note holds the `NOTA` and the source's `CREATION`/`MODIFICATION`/`ABROGATION` link labels.
Increments overlay the stock file by file (`liste_suppression_legi.dat` honoured). A new run after new archives yields a new expression per text.

## Citations
`C. civ., art. 1240` · `C. trav., art. L. 1234-5` · `Loi n° 78-17 du 6 janvier 1978, art. 22` · `Décret n°2008-1484 du 22 décembre 2008, art. 1` · `Décret n°2008-1484 du 22 décembre 2008, annexe 1` · `Cons. const., 7 janv. 2022, n° 2021-960 QPC, § 5` ·
`Cass. civ. 1re, 12 juill. 2012, n° 11-17.738` · `CE, 31 mars 2025, n° 493033` · `CAA Paris, 11 juill. 2025, n° 22PA02724`. Abbreviations are in the profile (to verify).
Judgment paragraphs: headnotes (`SOMMAIRE`), the numbered `§` of the decision, the operative part. Links: the Cour de cassation's own list of texts applied
(`source_markup`) and strict patterns (`articles 1569 et 1570 du code civil`, `article 61-1 de la Constitution`, `loi n° 78-17`) to ingested texts (`pattern`).

## Decrees and orders (2026-10-18)
- **Decrees are selected** (`DECRET` in `select.natures`). Verified on the whole stock: the 53,383 decrees were streamed out of the stock (436 s, one
  core; 676,228 files, 880,431,142 bytes of per-text zips) and every one was parsed with the final parser (371.5 s): **0 errors**, 53,214 parsed,
  169 skipped (nothing datable to ingest: decrees made only of amending articles, annulled decrees, or lapsed the day they started). They yield
  **583,746 provision versions** (533,668 article versions: 398,835 in force, 134,772 `repealed` markers, 61 prospective; 50,078 section versions)
  plus 114,186 unrecorded provisions (113,587 undated amending articles, 599 sections; in 30,383 texts). 172,331 article versions are in force
  today with no end; 16 start in the future (`VIGUEUR_DIFF`, not shown as in force before their date); 606 dated versions carry only the DILA's
  amendment summary (stored as note, empty text); 10,647 versions are annexes stored as articles, 1,687 have no number (`art-x`); 303 versions
  quote a placeholder date; 382,753,770 bytes of article text.
- **Sample through the pipeline** (dev database, `ulis_fr`, 2026-10-18): 400 decrees drawn at random (seed 20261018; 327 VIGUEUR, 61 ABROGE,
  7 PERIME, 5 MODIFIE), bundles built by the connector (7,141,245 bytes, 1.2 s), ingested in 8 batches of 50 in 13.5 s: 0 errors, 0 skipped,
  0 conflicts; 400 instruments and expressions, 3,757 provisions, **4,626 provision versions** (3,598 in force, 1,021 repealed, 7 prospective),
  3,176 passages; the database grew from 11,082,775 to 33,332,247 bytes (+22,249,472). `verify-sources`: 400 pass, 4,626 units, 6 s.
- **Estimate for all 53,383 decrees**: raw bundles about 0.95 GB (17.9 KB per decree; the per-text zips of the stock total 880 MB); database
  about 2.8-3.0 GB (55.6 KB per decree, 4.8 KB per provision version, indexes included); about 424,000 passages; stock scan 7.3 min, bundle
  build 2.7 min, parse 6.2 min, ingestion about 30 min at the sample's rate (34 ms per decree; count on 1-2 hours as the tables grow).
  The stock download itself: 1.17 GB.
- **Orders (arrêtés) stay excluded.** Cost if selected: 81,452 texts, 598,608 article files (69,025 of them undated amending articles), so about
  530,000 dated article versions before repeal markers, about 1 GB of raw bundles and 2.7 GB of database on top of the decrees, roughly another
  half hour of ingestion; the stock scan is shared. Reason: they are the lowest tier of the regulations (mostly nominations, tariffs, technical lists and the
  implementing details of decrees) and would double the French corpus; nothing in the parser prevents them (`ARRETE` in `natures`).
- **Parser 1.1.0 and the texts already loaded**: the fix also applies to the laws, ordinances and codes: 46,401 of their article files are
  undated (nearly all of them amending articles) and parser 1.0.0 stored them as in-force versions without a start date (the search treats a missing start as "always
  in force", so they were found at any date), and a few dozen dated versions carry only the summary (91 of their article files are of
  TYPE ENTIEREMENT_MODIF with a real period). A database loaded with parser 1.0.0 must be re-ingested; `verify-sources` on it reports those
  versions as "stored text differs from source".
- Also fixed: titles whose `TITRE` holds a line break (`Décret n°2008-1484\n du 22 décembre 2008`) are collapsed to one line; annexes stored
  as articles are cited `annexe I` / `annexe, art. 4` (not `art. Annexe I`).

## Known gaps
No orders (arrêtés), KALI, JORF, treaties, territorial applicability, link graph between legislative texts; visas and signatures not stored; articles not split into
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
- **verify-sources**: all 18,839 stored source documents (5,275 bundles, 7,408 + 5,474 + 682 decisions) re-parsed from their raw bytes and passed, none failed (681 s).
- Tests: 789 passed in the whole suite (`tests/test_dila_legi.py` and `tests/test_dila_case_law.py` hold the French ones); ruff and mypy clean.
- 2026-10-18 (decrees): `tests/test_dila_legi.py` 34 tests (7 new on the two decree fixtures `decret-2008-1484.zip`, `decret-2020-1006.zip`);
  full suite result in the commit message of the decree change; ruff and mypy clean.
