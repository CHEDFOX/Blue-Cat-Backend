# INT-OECD - Organisation for Economic Co-operation and Development (pilot)

Draft, unverified, minimal profile (`config/jurisdictions/int-oecd.yaml`, parent `INT`). One source,
`int-oecd-legal-instruments`: 16 individual instruments of the OECD Compendium of Legal Instruments, in English and French (the two
official languages), read on 2026-10-15. The other international organisations of the sweep are recorded, not ingested (below).

## Licence reading (AMBER, approved)

- robots.txt of legalinstruments.oecd.org: `User-agent: *` / `Disallow:` (nothing).
- The notice on each instrument's PDF: "This document is provided free of charge. It may be reproduced and distributed free of
  charge without requiring any further permissions, as long as it is not altered in any way. It may not be sold."
- The Compendium's own footer: "The present Compendium of OECD Legal Instruments is owned by the OECD. ... shall not be
  reproduced in whole or significant part without express permission from the OECD. For use, reproduction and translation of
  individual Legal Instruments, please consult the Terms and Conditions." Those terms (www.oecd.org/termsandconditions/) answered
  HTTP 403 and were not read; if they restrict more than the notice, the approval falls.
- Consequence: individual instruments only, a curated selection. `load_manifest` refuses more than 25 instruments (the
  Compendium lists 504); `scripts/build_oecd_manifest.py` holds the selection (16, 3%) and `--count` reports the scale without
  writing a manifest. Text is stored exactly; commercial use is `unknown` (a commercial deployment refuses the source).
- The record names the staff member who last published an instrument (name, e-mail); the connector drops that one field.

## What is ingested

Conventions on foreign bribery (0293) and on mutual tax assistance (0235, with its 2010 Protocol 0382), Council acts
on privacy (0188), multinational enterprises (0307), foreign bribery (0378), corporate governance (0413), regulatory policy (0390),
cartels (0452), e-commerce consumers (0422), children online (0389), AI (0449), data access (0463), Declarations (0383, 0404) and one
abrogated Recommendation (0162, abrogated 2023-06-08). 32 documents, 16 works, 1,025 provision versions, 1.1 MB of text,
`verify-sources` 36/36 pass with the constitutions (dev DB 20 MB in all).

## Structure and citation (`ulis/sources/parsers/oecd_legal_instruments.py`, module `oecd`)

HTML from the Word export. Conventions: preamble and articles by the text's `Article N` headings. Council acts: recitals
(`preamble`), Roman paragraphs `I.`, numbered paragraphs `1.`/`1)` under them (`II.4`), decimal principles `1.1.`, lettered
sub-principles `I.A.`, `Section N:` headings, an `ANNEX`/`APPENDIX` line (units cited with it), a second `I.` set named by its bold
heading, the notes after a rule as a text block. Bold-only paragraphs (the OECD's headings) become the next unit's `heading`.
Sub-points (`a)`, `i.`) stay in their paragraph. Citations: `OECD/LEGAL/0293 art. 5`, `OECD/LEGAL/0188 para. II.4`,
`OECD/LEGAL/0449 para. 1.2`, `OECD/LEGAL/0413 appendix para. I.A`. Identifiers do not depend on the language, so the English and
French provisions are parallel versions. Only a divider rule and the bare `Article N` line (its number is the label) are not in the text.

Dates come from the record: valid from the latest of entry into force, last update and last amendment, until the abrogation date;
`validity_basis = source_periods`; earlier wordings are not published, so a date before the current wording is
`history_not_indexed`. `text_origin = official_authentic`, both languages authoritative, no translation.

## Scale of the whole Compendium (not enabled)

504 instruments: 1,512 requests plus the index, about 2.1 hours at 5 s; about 32,000 provision versions (32 per document), about
35 MB of text, roughly 0.2 GB in the database with the index. The selection took 48 requests (4 minutes) and the index.

## Other organisations (config/sources, all `status` planned or rejected, nothing ingested)

| Source | Outcome | Finding |
|---|---|---|
| `wipo-lex` | rejected | Terms forbid automated queries, bulk download and scraping; reproduction only for "academic, research and non-commercial purposes"; robots disallows `/edocs/lexdocs/` |
| `uncitral-texts` | rejected | "UNCITRAL texts are copyrighted, and permission is needed to reproduce UNCITRAL texts" |
| `ilo-normlex` | planned | `normlex.ilo.org/robots.txt` answered HTTP 403; CC BY 4.0 only from 3 May 2023, earlier products "do not automatically benefit from a Creative Commons licence" |
| `wto-legal-texts` | planned | robots.txt of www.wto.org `User-agent: *` `Disallow: /`, of docs.wto.org `Disallow: *`; ULIS's agent is not named |
| `unidroit-instruments` | planned | "Reproduction of the information is authorised, except for commercial purposes, provided the source is duly acknowledged." |
| `hcch-conventions` | planned | the same sentence; Crawl-delay 10; `assets.hcch.net` disallows `/upload/` |

## Known limits

- The retrieval layer has no soft-law authority category: Recommendations and Declarations show "Binding: legislation in force"
  (a follow-up task is queued). Conventions have no recorded parties, so they are listed only without a jurisdiction filter.
- Roman/numbered splitting follows the numbering the text prints; lists numbered inside a paragraph (as in 0307) become units.
- Tests: `tests/test_oecd_legal_instruments.py` (40: seven real fixtures in `tests/fixtures/oecd_legal_instruments/`, connector offline,
  manifest refusals, gates for the six others, DB ingest, search, point-in-time and `verify-sources`).
