# Estonia (EE) - Riigi Teataja

**Status:** pilot, profile `draft_unverified` (not reviewed by counsel). Source `ee-riigi-teataja`, connector
`riigi_teataja`, parser `riigi_teataja_xml`, module `ee`, language `et` (EU membership is added separately).

## Source and licence

- Riigi Teataja (Justiits- ja Digiministeerium) publishes every version of every act as XML in yearly archives,
  `https://www.riigiteataja.ee/avaandmed/ERT/xml.<year>.zip` ("avaandmed" = open data; `LOEMIND.txt`: generated
  2026-10-11; sums in `SHA256SUMS`). `xml.2026.zip` is 43 GB, 254 454 members `<globaalID>.xml`, and the server
  answers range requests, so the connector reads single members (robots.txt, LOEMIND.txt, archive end, the 25 MB
  central directory once, one range per version; CRC-32 checked). The site's `/akt/<id>.xml` links now return the web
  application's HTML shell, and its internal API (`/public-api/`) is undocumented and not used.
- [robots.txt](https://www.riigiteataja.ee/robots.txt) disallows `*/kohtulahendid/*`, `*/kohtuteave/*` (court
  decisions: not ingested) and about 5 600 `*/akt/<id>/*` acts. The connector applies it itself to the archive path
  and to every act it ingests (the shared PoliteHttp does not read robots.txt); no crawl delay is stated, 5 s is kept.
- **Licence:** the publisher's open-data page is a JavaScript application whose terms text could not be read, so the
  approval rests on the statutes read from the archive: Autoriõiguse seadus § 5(1) p 3 (legislation is not protected
  by copyright), Riigi Teataja seadus § 7(1) (free of charge) and § 12(1) (reuse with a note that the official text is
  in Riigi Teataja and a link). Counsel review of database rights and of the page's terms is recommended.
- **Authority:** RTS § 3(1) calls the consolidated texts "ametlikud terviktekstid", made on the basis of the published
  act (algtekst) with validity data (§ 3(2)); § 10(4) treats a mismatch with the algtekst as an error. No provision
  found says a consolidated text prevails, so `text_origin = official_consolidation`, `authoritative = false`,
  source authority `official_non_authentic`. To verify with counsel: the legal force of consolidated texts.

## What is ingested

Six acts, Estonian only, 12 consolidated texts (manifest `config/manifests/ee-riigi-teataja.yaml`, versions in force on
2026-10-14 unless stated): Põhiseadus (PS; four texts, 2011-07-22 onwards), Isikuandmete kaitse seadus (IKS; 2019,
2026-03, 2026-10), Töölepingu seadus (TLS; 2026-07, 2026-10), Karistusseadustik (KarS), Võlaõigusseadus (VÕS, 5 877
provisions) and Haldusmenetluse seadus (HMS). Provisions: paragrahv (§), lõige (lg), punkt/alapunkt (p/alap); chapters
are context headings. Repealed provisions ("Kehtetu - ...") are `repealed`, with the repealing act in the note.

## Dates and versions

Each consolidated text carries `kehtivuseAlgus`/`kehtivuseLopp` (last day). Texts form a whole-act snapshot series
(`revision_snapshot`, shared `ulis/ingestion/snapshots.py`): `valid_from` = start, `valid_to` = day after the stated end
(half-open); unchanged wording continues across texts. The source gives no dates per provision; the amending act and
entry into force of each changed provision are kept as its `note` (e.g. "RT I, 11.04.2025, 1, jõust. 01.03.2026") and
as lifecycle events. XML dates may carry a time-zone offset (`2019-01-15+02:00`); the calendar date as written is used.
Example: PS § 156 lg 2 has four wordings (2011: 18 years; 2015: 16 years; 2025-07-09: citizens and stateless
persons; 2026-03-01: citizens). `currency_date` is not set (the XML states none).

## Citations

`PS § 10`, `KarS § 113 lg 1`, `VÕS § 127 lg 1`, `TLS § 92 lg 1 p 6`, `IKS § 2¹ lg 1`, `PS preambul`; abbreviation = `lyhend`
of the act, superscripts as Unicode. Formatting to be confirmed by an Estonian reviewer.

## Results (2026-10-14)

- Ingest into `ulis_ee`: 12 documents, 6 instruments, 12 versions, 11 146 provision versions created, 3 036
  corroborated, 8 679 passages, no conflicts or errors. `verify-sources`: 12 pass.
- Tests: 357 in the full suite on `ulis_test_ee`, all passing (22 in `tests/test_riigi_teataja.py` and
  `tests/test_polite_http.py`: parser, citations, robots, zip/zip64 reading, offline connector, search, point in time).
  ruff, ruff format and mypy clean.
- Real searches: "teise inimese tapmise eest ..." -> KarS § 113 lg 1; "Eesti on iseseisev ja sõltumatu demokraatlik
  vabariik" -> PS § 1 lg 1; "töötaja mobilisatsioonikäsu alusel tegevteenistuses" -> TLS § 92 lg 1 p 6; "kahju
  hüvitamise eesmärk ..." -> VÕS § 127 lg 1; "haldusorgan ... esitada arvamus" -> HMS § 40 lg 1.

## Known gaps

- Only six acts; no regulations (määrused), local-government acts, treaties, explanatory notes or case law
  (court-decision paths are disallowed by robots.txt; no other source reviewed).
- Estonian only; Riigi Teataja's unofficial English translations are not ingested.
- No PostgreSQL text-search configuration for Estonian: `simple` is used (no stemming, so searches need the exact word
  forms of this highly inflected language; e.g. "isikud" does not match "kodanikud").
- History: PS from 2011, IKS has a gap 2023-11-01 to 2026-03-15; other acts only the current text. Texts that start
  after 2026-10-14 are not ingested.
- Annexes, the introductory formula, tables other than as text and cross-reference links are not ingested.
- The archive is regenerated; the manifest pins versions by `globaalID` (found by reading member metadata on
  2026-10-14), so a corrected member is refused as a conflict.
- Shared code touched: `PoliteHttp` now refuses a Range request that the server answers with the whole file (HTTP 200
  instead of 206), so a 43 GB archive is never read by mistake.
