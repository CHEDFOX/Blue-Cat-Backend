# Italy (State law) - Normattiva

Profile `IT` (draft, unverified), source `it-normattiva`, parser `normattiva_akn`, connector
`normattiva`, module `it`. Status: **pilot**, Italian only: the Constitution, the five pilot acts and, since
2026-10-15, **every code and implementing regulation on Normattiva's "Costituzione e Codici" page - 41 works in all** (section
"Widening"). Ordinary laws, decreti legislativi and decreti-legge are NOT covered: a polite client cannot list them.
Regional law, case law and EU membership are not covered either.

## Source and licence (read 2026-10-14)

[Normattiva](https://www.normattiva.it) (IPZS for the Presidency of the Council) exports any act, as it
applies on a chosen date, with "esporta in Akoma ntoso" (`/do/atto/caricaAKN?dataGU&codiceRedaz&dataVigenza`,
linked from every act page; the FAQ also lists HTML, XML, PDF and EPUB). The export is served only inside
the session the act's own page starts, so the connector requests the act by its URN
(`/uri-res/N2Ls?urn:nir:...`), keeps the cookie, and sends the page as Referer; an error page is never
cached; one retry. `robots.txt` blocks only `/ricerca/` and `/mfr/ricerca/`; no Crawl-delay (6 s used).
**Not used:** the open-data portal `dati.normattiva.it` answered its `robots.txt` with HTTP 409 ("bloccata dai
sistemi di protezione"); ULIS stopped there, so its terms and any API or licence were not read.

[Note legali](https://www.normattiva.it/staticPage/legal): the texts "non hanno carattere di ufficialità";
only the Gazzetta Ufficiale (GU) is authentic and prevails. "La riproduzione dei testi forniti nel formato
elettronico è consentita purchè venga menzionata la fonte, il carattere non autentico e gratuito" (same in the
[FAQ](https://www.normattiva.it/staticPage/faq)). Approved with conditions (source, non-authentic and free
character shown as attribution; selected acts only); commercial use is `unknown`; counsel review recommended.

## Widening (2026-10-15): what the service lets a polite client list

The brief was every code, law, decreto legislativo and decreto-legge in force that Normattiva lets a polite client export. The
export works for any act whose address is known; the question is where a list of acts can be read:

* acts are listed by year, type and date under `/ricerca/` (including `/ricerca/elencoPerData`, the one list the sitemap
  names) and `/mfr/ricerca/`: **disallowed by robots.txt** (`Disallow: /ricerca/`, `/mfr/ricerca/`; re-read 2026-10-15);
* the open-data portal `dati.normattiva.it` (bulk collections) answered its `robots.txt` with HTTP 409 on 2026-10-14 and on
  2026-10-15 (one request each day, the same polite request); ULIS stopped, and did not try `api.normattiva.it`;
* the sitemap lists 20 static pages; `/staticPage/leggi_approvate` and `/staticPage/gazzetta` are shells whose lists load
  by script; the page of a partial URN (`urn:nir:stato:decreto.legislativo:2003`) is an error page;
* the **one readable list is `/staticPage/codici`**: the Constitution and 41 acts (codes and their implementing regulations).
  Guessing acts from number ranges (tens of thousands of requests for pages that mostly do not exist) would be mass
  harvesting of a service that already blocks its open-data host, and is not done.

So `python -m scripts.build_normattiva_manifest` reads that page (and each act's page, to find its Gazzetta date and redactional
code) and adds **35 acts** to the manifest: 41 works with the Constitution and the five pilot acts, 67 documents (the Constitution has
27 versions), about 110 requests at 6 s. Two ministerial decrees on the page (`regolamento di esecuzione` of the c.p.p. and of the
codice della proprietà industriale) have a URN the parser does not read and are skipped by the script. The acts: c.p.c. and
disp. att.; c.p.p. (d.P.R. 447/1988) and its norme di attuazione; codice della strada and its regolamento; codice di giustizia
contabile, del processo tributario, del processo amministrativo, del consumo, delle assicurazioni, dell'ambiente (d.lgs. 152/2006),
dei beni culturali, della proprietà industriale, dell'amministrazione digitale, delle comunicazioni elettroniche, del turismo,
antimafia, del Terzo settore, della protezione civile, della crisi d'impresa, degli incentivi, della nautica da diporto, delle
pari opportunità, dell'ordinamento militare, postale, della navigazione and its regolamento, the codici penali militari, and three
editions of the codice dei contratti pubblici (d.lgs. 36/2023 in force; 50/2016 and 163/2006 as Normattiva still serves them).

Parser 1.1.0 reads what these codes need: **codes exported as one attachment per article** (any act with at least 10 attachments
named `<code>-art. N` and more of them than articles of its own: c.p.c., c. nav., disp. att., codici penali militari, codice postale,
regolamento di navigazione marittima, norme di attuazione c.p.p.; abbreviations `c.p.c.`, `cod. nav.`, `disp. att. c.p.c.`,
`disp. att. c.c.`, `c.p.m.p.` and `c.p.m.g.`, otherwise `<type> <n>/<year>`), **several attachment sets that number alike** (d.lgs.
174/2016 has an Allegato 1, 2 and 3: the commonest is `art. N`, the others are cited as `art. 1 Allegato 2 Norme di attuazione ...`),
**an act that numbers two articles alike** (d.lgs. 152/2006 has two art. 298-bis: `art_298-bis` and `art_298-bis--2`), a duplicated
attachment name whose text is another article (c.p.m.g. "art. 33" is art. 22: not read, listed by `skipped_attachments`), and the
redactional code of acts from 2010 (`18G00011`). Guards: an act with annexed codes of conduct but its own articles (d.lgs. 196/2003)
is not "flat".

**Result** (`ulis_it`, whole manifest from the cache): `ingest` 67 documents, 41 instruments, **53,857 provision versions** (4,031
corroborated), 46,067 passages, 8,285 citation links (1,919 resolved), 0 errors, 69 s; `verify-sources` **67 of 67 pass**;
database 173 MB. Searches (`jurisdiction=IT`, 2026-10-15): "citazione a comparire a udienza fissa" -> `art. 163 c.p.c.`;
"guida sotto l'influenza dell'alcool" -> `art. 186, comma 1, d.lgs. 285/1992`; "diritto di recesso del consumatore contratti a
distanza quattordici giorni" -> `art. 59-octies, comma 1, d.lgs. 206/2005`; "perdita ... assistenza e salvataggio" ->
`art. 552 cod. nav.`; "Chiunque cagiona la morte di un uomo" -> `art. 575 c.p.`. All carry `SNAPSHOT_DATES` and `CURRENCY_UNKNOWN`.

## What is ingested (the pilot instruments)

| Instrument | Work URI | Provisions | Versions |
|---|---|---|---|
| Costituzione | `/it/costituzione/1947-12-27` | 157 (139 articles, 18 transitional/final) | 27 (1948-01-01 ... 2023-10-22) |
| Codice civile (R.D. 262/1942) | `/it/codice.civile/1942/262` | 3,280 articles (162 repealed) | 1 (2026-08-12) |
| Codice penale (R.D. 1398/1930) | `/it/codice.penale/1930/1398` | 997 articles (96 repealed) | 1 (2026-09-30) |
| d.lgs. 196/2003 | `/it/decreto.legislativo/2003/196` | 701 (articles, commi, lettere) | 1 (2026-02-20) |
| L. 300/1970 (Statuto) | `/it/legge/1970/300` | 44 | 1 (2025-11-06) |
| L. 241/1990 | `/it/legge/1990/241` | 269 | 1 (2026-04-21) |

Text is exact, line by line, with Normattiva's `((...))` marks for wording inserted by later amendments;
footnote anchors (`((5))`, `(14)`) are dropped because they are renumbered and would fake changes; the notes
("AGGIORNAMENTO ...") go to `note`. Older texts write è as `e'` (kept as is).

## Dates and versions

The export dates the version of the whole act (ELI `CONSOLIDATED/<YYYYMMDD>` or `ORIGINAL`), not provisions,
so documents are `revision_snapshot` series (`ulis/ingestion/snapshots.py`): unchanged wording continues, a
changed one starts a version, an absent one ends. The Constitution's versions were found by requesting the
export 30 days after the GU publication of each amendment in the export's lifecycle list (27 distinct
versions out of 29 requests; amendments in force within 30 days of one another may share a version).
Examples (point-in-time): art. 9 Cost. has two versions, 1948-01-01 to 2022-03-09 and since (L. cost. 1/2022);
art. 81 changes on 2012-05-08; art. 115 is in force until 2001-11-08, then `repealed`. The other five acts
have one version (applicable on 2026-10-14): earlier dates answer `history_not_indexed`, and a provision starts
on that version's date although its wording may be older (warning `SNAPSHOT_DATES`). The export gives no
repeal date, so a repealed provision starts on the first ingested version showing it repealed. `currency_date`
is unknown (`CURRENCY_UNKNOWN`). `text_origin`: `official_revised` (`official_conversion` for the original
text); `unofficial_compilation` is worded for the US Code, so it is not used.

## Citations (module `it`)

`art. 2043 c.c.`, `art. 575 c.p.`, `art. 3, comma 1, Cost.`, `art. 6, comma 1, lett. a), L. 241/1990`,
`art. 167, comma 1, d.lgs. 196/2003`, `art. 12 disp. prel. c.c.`, `art. 314/2 c.c.`, `XIII disp. trans. fin. Cost.`
Space after a bare article, commas otherwise. To verify with an Italian reviewer.

## Known gaps

41 works only (Constitution, the codes of Normattiva's codes page, five pilot acts); no ordinary laws, decreti legislativi or
decreti-legge outside that page (no list a polite client may read), regional law, treaties, case law (Corte costituzionale,
Cassazione, Consiglio di Stato), EU law. Of two ministerial decrees on the codes page neither is read. Commi the source does not number (Constitution, codes, older acts)
are not separate provisions; books/titles/chapters are not recorded (the export renders them unreliably);
annexes of the d.lgs. 196/2003 and the articles of the decrees approving the codes are not read. Source slips
are handled, not fixed (e.g. c.c. art. 1159-bis printed as 1159: the attachment name wins; 1519-novies/nonies:
the printed number wins). A few accented query words (può, più) miss `puo'` text. The profile's courts,
precedent and treaty rules are "to verify".

## Results (2026-10-14)

- Real ingest into `ulis_it`: 32 documents, 6 instruments, 32 instrument versions, 5,499 provision versions
  created (208 for the 157 Constitution provisions), 4,031 corroborated, 398 citation links (43 resolved),
  4,943 passages, 0 conflicts, 0 errors. About 80 HTTP requests in total (40 for the ingest, the rest reading the legal pages and trying the export). `verify-sources`: 32 pass.
- Searches (`as_of` 2026-10-14): "risarcimento per fatto illecito danno ingiusto" -> `art. 2043 c.c.`;
  "diritti inviolabili dell uomo" -> `art. 2 Cost.`; "reintegrazione del lavoratore licenziamento illegittimo"
  -> `art. 18 L. 300/1970`; "omicidio colposo" -> `art. 589 c.p.`; "interpretazione della legge" ->
  `art. 12 disp. prel. c.c.`.
- Tests: `tests/test_normattiva.py` 23 tests (18 unit, 5 PostgreSQL). Full suite on the tree merged with main: 361 passed; ruff, ruff format and mypy clean.
