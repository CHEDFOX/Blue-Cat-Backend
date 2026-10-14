# Italy (State law) - Normattiva

Profile `IT` (draft, unverified), source `it-normattiva`, parser `normattiva_akn`, connector
`normattiva`, module `it`. Status: **pilot sample** of six State instruments, Italian only. Regional
law, case law and EU membership are not covered.

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

## What is ingested

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

Only six instruments; no other codes, regional law, regulations, treaties, case law (Corte costituzionale,
Cassazione, Consiglio di Stato), EU law. Commi the source does not number (Constitution, codes, older acts)
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
