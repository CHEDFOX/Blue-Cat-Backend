# Estonia (EE) - Riigi Teataja

**Status:** profile `draft_unverified` (not reviewed by counsel). Source `ee-riigi-teataja`, connector `riigi_teataja`, parser
`riigi_teataja_xml`, module `ee`, language `et` (EU membership is added separately). Widened from six pinned acts to the text in
force of every act and regulation of the state and of the local governments (known only after the scan: an estimated 16 000-21 000).

## Source and licence

- Riigi Teataja (Justiits- ja Digiministeerium) publishes every version of every act as XML in yearly archives,
  `https://www.riigiteataja.ee/avaandmed/ERT/xml.<year>.zip` ("avaandmed" = open data; `LOEMIND.txt`: generated 2026-10-11; sums in
  `SHA256SUMS`). Listing (2026-10-15): `xml.2024.zip` 344 MB, `xml.2025.zip` 2.6 GB, `xml.2026.zip` 43 GB with 254 454 members
  `<globaalID>.xml` (all versions of all acts, state and local government), plus `*.en.zip` (English, not used). The site's `/akt/<id>.xml`
  links return the web application's HTML shell and its API is undocumented and not used, so **the archive is the only list of the
  acts in force**. The server answers range requests (`206`), now and then a small range with the whole file (`200`): the connector
  then reads nothing and repeats the request.
- [robots.txt](https://www.riigiteataja.ee/robots.txt) disallows `*/kohtulahendid/*`, `*/kohtuteave/*` (court decisions: not ingested)
  and 5 647 `*/akt/<id>/*` acts. The connector applies the rules itself (PoliteHttp does not read robots.txt) to the archive path and
  to every act it delivers; no crawl delay, 5 s kept.
- **Licence:** the publisher's open-data page is a JavaScript application whose terms text could not be read (re-tried 2026-10-15; a
  web search found none), so the approval rests on statutes read from the archive: Autoriõiguse seadus § 5(1) p 3 (legislation is not
  protected by copyright), Riigi Teataja seadus § 7(1) (free of charge) and § 12(1) (reuse with a note that the official text is in
  Riigi Teataja and a link). AMBER in `docs/licence-review.md`; counsel review of database rights and of the terms is recommended.
- **Authority:** RTS § 3(1) calls the consolidated texts "ametlikud terviktekstid"; none prevails over the algtekst, so
  `text_origin = official_consolidation`, `authoritative = false`, source authority `official_non_authentic`.

## What is ingested (manifest `config/manifests/ee-riigi-teataja.yaml`)

1. **Six pinned works with history** (the pilot, 12 consolidated texts): Põhiseadus (PS; four texts from 2011-07-22), Isikuandmete
   kaitse seadus (IKS; 2019, 2026-03, 2026-10), Töölepingu seadus (TLS; 2026-07, 2026-10), Karistusseadustik (KarS), Võlaõigusseadus
   (VÕS) and Haldusmenetluse seadus (HMS).
2. **`select`: the text in force of every act (`seadus`) and regulation (`määrus`)** of the state and of the local governments
   (volikogu, valitsus), found by reading the whole archive in 64 MiB ranges (810 ranges, 30.9 GB, plus 582 probes of the first
   20 KB of members over 8 MiB). Each member's `<metaandmed>` gives text type, document type, issuer, `kehtivuseAlgus`/`kehtivuseLopp`
   and the group (`terviktekstiGrupiID`, one act). **The end date of a superseded text is not reliable** (Maksukorralduse seadus
   has older texts without one), so the text in force on the archive's date is the group's text with the **latest `kehtivuseAlgus`
   up to that date**. If that text is the empty tombstone of a repealed act (`<sisu/>`, a "Kehtetu" mark), an old HTML fragment,
   ended, over 50 MB or disallowed by robots.txt, the act is not ingested and no older text of it. The winners wait in scratch files
   while the archive is read; documents are delivered at the end. The optional `exclude_issuers` (a regular expression on the issuer)
   leaves out the local governments (two thirds of the documents, a quarter of the database); `part: "i/n"` is for trials.

## Dates, versions, parsing, citations

Each text is a whole-act snapshot (`revision_snapshot`, `ulis/ingestion/snapshots.py`): `valid_from` = `kehtivuseAlgus`, `valid_to` =
day after `kehtivuseLopp` when given. The source gives no dates per provision; the amending act and entry into force of each changed
provision are its `note` and lifecycle events. XML dates may carry a time-zone offset (`2019-01-15+02:00`): the calendar date as
written is used. Streamed acts have one text each, the pinned works keep theirs. `currency_date` is not set (the XML states none).

Structure: paragrahv (§), lõige (lg), punkt/alapunkt; chapters are context headings. Regulations (`määrus`, instrument type
`regulation`) have a **preamble** (`preambul`, "Määrus kehtestatakse ... alusel"). A correction of an error (`veaparandus`, inside or
beside the text) is a `note`, not text (the first parser put it into the text of KarS § 365² lg 1; `verify-sources` found it).
`ParseSkipped` (not an error) for an empty tombstone, an HTML-fragment text (old migrated acts), a draft (`EELNÕU`: the publisher's
schema examples) and other document types. Annexes (lisa), the introductory formula and links are not ingested.

Citations: `PS § 10`, `KarS § 113 lg 1`, `TLS § 92 lg 1 p 6`; an act without an abbreviation, as most regulations, by title and
issuer: `Passiivse kaitse vahendite loetelu (Kaitseminister) § 1`, `... preambul`. To be confirmed by an Estonian reviewer.

## Scale and results (2026-10-15)

- **Full manifest:** one scan of the archive, about 1 400 requests = 2 hours at the 5 s delay plus about 31 GB (half an hour).
  Documents: state 3 000-8 000 (the sample's upper bound is 8 200; about a thousand laws), local governments about 13 000 (341 and
  372 per hundredth of the archive in two slices; the sample says 12 100): **16 000-21 000**. About 2 million provision versions;
  database **4-6 GB** (2.4 KB per version in long acts; a local regulation has 52 versions and 100 KB), raw XML in the object store
  7-8 GB (not compressed). Under the 15 GB limit, so nothing is left out; `exclude_issuers` narrows it.
- **Slices** (`ulis_wee`): the six works + part 50/100 (state): 25 documents, 12 202 provision versions + 3 036 corroborated,
  9 574 passages, 0 errors, 90 s; part 80/100 (local): 341 regulations, 17 728 versions, 14 911 passages, 0 errors, 27 s.
  `verify-sources`: 366 of 366 pass (32 966 units). Database 74 MB (10 MB empty). The parser alone on 944 more winners (parts 30/100
  and 95/100): 592 parsed, 11 drafts skipped, 0 errors.
- Searches: "teise inimese tapmise eest" -> KarS § 113 lg 1; "Rapla Keskraamatukogu tagatis" -> Rapla Keskraamatukogu tagatise võtmise
  ja tagastamise kord (Rapla Vallavalitsus) § 4 lg 1. Failures (no stemming): "Kristiine linnaosas tänavanimi" found nothing (the
  text has "tänavanime"); "kord ajatamise maksuvõla tasumine" gave an unrelated fisheries regulation (not in the slice).
- Tests: `tests/test_riigi_teataja.py` 29 (9 new functions); full suite 1 294 passed; ruff, ruff format, mypy clean.

## Known gaps

No history for the streamed acts (texts that start after 2026-10-11 are not ingested); no decisions, orders, treaties, explanatory
notes or case law (court-decision paths are disallowed); the count of acts in force is an estimate; Estonian only; no PostgreSQL
text-search configuration for Estonian (`simple`: exact word forms); annexes, tables other than as text and cross-reference links are
not ingested; a member over 50 MB is not ingested; the archive is regenerated, so a corrected member is refused as a conflict; the
selection needs the whole archive read before the first document is delivered. No shared code was changed.
