# International law and courts - Council of Europe (COE) and the international legal order (INT)

Status: **pilot, draft_unverified profiles `COE` 0.1.0 and `INT` 1.1.0**. Six treaty texts and five protocols, as published by Switzerland.
**No case law of any international court is ingested**: every court's own terms or site forbade it (table below).

## Sources evaluated (2026-10-14; each host's robots.txt and terms read first)

| Source | Finding | Result |
|---|---|---|
| HUDOC / echr.coe.int (`coe-echr-hudoc`) | No robots.txt on hudoc (the SPA answers /robots.txt); echr.coe.int robots allow. Terms (`/copyright-and-disclaimer`): reproduction for private use or information and education in connection with the Court's activities, free of charge, "© ECHR-CEDH"; other use, "in particular commercial", needs prior written permission; nothing on automated retrieval. Electronic text not authentic. | planned, `pending_review`. The undocumented JSON API was not used. Owner/counsel decision or written permission needed. |
| Council of Europe Treaty Office (`coe-treaty-office`) | www.coe.int and rm.coe.int answer the ULIS User-Agent with a Cloudflare 403 block, robots.txt included. | planned; not retried. |
| ICJ (`icj-cij`) | "All rights reserved"; Disclaimer: data "may not be reproduced or used for commercial purposes". robots allow. | planned, `pending_review`. |
| UN Charter, VCLT (`un-web-treaties`) | un.org Terms of Use: personal, non-commercial use, no redistribution, no compilation or derivative works; UN Treaty Collection: crawl delay 215 s, texts as images. | **rejected**. |
| ICC / Rome Statute (`icc-legal-texts`) | Terms page returns a "Browser Validation" interstitial. | planned; not passed. |
| Inter-American Court (`iacthr-judgments`) | CC BY-NC-ND 3.0 stated on the site; robots allow all. | planned (NC and ND need an owner decision). |
| African Court (`african-court-decisions`) | "All Rights Reserved", no terms, no robots.txt. | planned. |

Each has a gate-refusal test (`tests/test_fedlex_treaties.py`). Missing from ULIS because of this: all case law (ECtHR, ICJ, ICC, IACtHR, African Court), the English treaty texts, Protocols No 1, 4, 12, 16.

## What is ingested: `ch-fedlex-treaties`

The treaties exist openly in the Swiss Classified Compilation (SR 0.x) on Fedlex, the source already approved for Swiss law (`ch-fedlex`: official enactments are not protected by copyright, Art. 5 URG; reuse under Art. 49 PublV; legal-information page re-read for this source). Same files, connector (`fedlex`) and politeness rules; new parser `fedlex_treaty` (`ulis/sources/parsers/fedlex_treaty.py`), manifest from `scripts/build_fedlex_treaties_manifest.py`. 11 treaties, 25 consolidations, de/fr/it, 75 files:
ECHR (SR 0.101, from 2012-02-23) and Protocols No 6, 7, 13, 14, 15; Statute of the Council of Europe (0.192.030) - jurisdiction `COE`; UN Charter (0.120), ICJ Statute (0.193.501), VCLT (0.111), Rome Statute (0.312.1) - jurisdiction `INT`.
**It is not the treaty's authentic text.** The French file is "Texte original" (`ext.original_text`); German and Italian are Swiss translations; English is absent; `authoritative` is false everywhere. Every result carries the Art. 49 PublV notice.

## Structure, versions, parties

Work URI `/coe/treaty/ch-sr-0.101`; eIds `art_8/para_1` identical in every language (reader shared with the Swiss parser). Each consolidation is a whole-instrument snapshot (`revision_snapshot`); earlier dates are a history gap. Each file ends with a **scope table** (parties, ratification/accession/succession and entry-into-force dates). It becomes `treaty_parties` for the 21 States that have a profile (`STATES`; others are counted in `ext.parties_in_table`): e.g. ECHR: DE ratified 1952-12-05, in force 1953-09-03. Search then includes a treaty for a party only, with authority `treaty_obligation` and the monist/dualist note; for the US (not a party) it is `foreign`. Signature dates are not in the table; withdrawals are not modelled.

## Citations (modules `coe`, `int` 1.0.0)

`art. 8 al. 1 CEDH` / `Art. 8 Abs. 1 EMRK` / `art. 8 cpv. 1 CEDU`; `art. 2 al. 4 Charte des Nations Unies du 26 juin 1945` (the title stands in where the file has no short form).

## For the lead: memberships and dates (all from the Swiss scope tables, consolidation applicable 2022-03-16)

Statute of the Council of Europe, entry into force for the State (add `memberships: [{organisation: COE, from_date: ...}]`): AT 1956-04-16, CH 1963-05-06, DE 1950-07-13 (the table says accession; to verify associate/full status), DK 1949-08-03, EE 1993-05-14, ES 1977-11-24, FI 1989-05-05, GB 1949-08-03, IE 1949-08-03, IT 1949-08-03, LV 1995-02-10, NL 1949-08-05, PL 1991-11-26, SE 1949-08-03. US, CA, JP, AU, NZ, AR, IN are not in the table. Russia (no profile): accession 1996-02-28 in the 2007-05-11 table, absent from the consolidation applicable from 2022-03-16; ECHR: ratified 1998-05-05, absent from the one applicable from 2022-09-16. Neither table states a withdrawal date; the dates are the consolidations' own.

## Known gaps

No case law; no English text; the Swiss publication may differ from the depositary's text; only 11 treaties; parties only for profiled States; reservations and declarations absent; preambles, signatures and Swiss declaration components not indexed; Italian Rome Statute lists encoded differently (a few eIds differ); some Netherlands rows have no dates in one language (skipped there, taken from the others).

## Results (2026-10-14)

Dev DB `ulis_intl`: `migrate`, `sync-catalog`, `ingest ch-fedlex-treaties`: documents_seen 75, new 75, instruments_created 11, provision_versions_created 7,122 (19,616 corroborated), passages_indexed 6,175, citation links 51 (32 resolved), errors 0, conflicts 0; second run 75 unchanged; `verify-sources` 75 of 75 pass.
Searches (dev DB, `ulis.cli search`, as of 2026-10-14): CH, "Recht auf Achtung des Privat- und Familienlebens" -> `Art. 8 Abs. 1 EMRK` (de; in force for CH since 1974-11-28, monist); IE, "Toute personne a droit au respect de sa vie privée" -> `art. 8 al. 1 CEDH` (fr; dualist note); FI, "Ogni persona ha diritto alla vita" -> `art. 2 cpv. 1 CEDU`; DE, "Verbrechen gegen die Menschlichkeit ..." -> `Art. 7 Abs. 1 Römer Statut des Internationalen Strafgerichtshofs vom 17. Juli 1998`; GB, "Le but du Conseil de l'Europe ..." -> `art. 1 Statut du Conseil de l'Europe du 5 mai 1949`; US (not a party): no ECHR hit. Every hit carries SOURCE_NOT_AUTHENTIC, NOT_AUTHENTIC_TEXT, SNAPSHOT_DATES and PROFILE_UNVERIFIED.
Tests: `tests/test_fedlex_treaties.py` 24 (parser, parties, citations, manifest, offline connector, gate refusal of the 7 recorded sources, PostgreSQL ingest/search/point-in-time) on fixtures of 237 KB. Full suite: 761 tests; 759 passed in the first run and the other 2 (assistant tests) failed only with "remaining connection slots are reserved" on the shared PostgreSQL server and passed when re-run; `ruff check`, `ruff format --check` and `mypy ulis` clean.
