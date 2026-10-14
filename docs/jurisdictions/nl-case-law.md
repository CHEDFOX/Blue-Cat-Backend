# Netherlands (NL): case law from the Open Data van de Rechtspraak

Status: **pilot sample, draft unverified profile.** 30 hand-picked Dutch decisions (ECLI) of six courts, Dutch
only, linked to the legislation in [nl.md](nl.md). Research aid, not legal advice.

| | |
|---|---|
| Source / manifest | `config/sources/nl-rechtspraak.yaml` (active, approved), `config/manifests/nl-rechtspraak.yaml` (30 ECLIs) |
| Connector / parser | `ulis/sources/connectors/rechtspraak.py`, `ulis/sources/parsers/rechtspraak.py` (`rechtspraak_xml` 1.0.0) |
| Profile / citations | `config/jurisdictions/nl.yaml` (0.2.0: courts `NL-GHDHA`, `NL-RBDHA` added), `ulis/jurisdictions/modules/nl.py` |
| Tests | `tests/test_rechtspraak.py`, fixtures in `tests/fixtures/rechtspraak/` |

## Source and licence (read 2026-10-14, robots.txt first)

| Page | Finding |
|---|---|
| `data.rechtspraak.nl/robots.txt` | 404: none, so no restrictions. `www.rechtspraak.nl/robots.txt` disallows only `/registers/nevenfunctieregister/...` (no AI-crawler rules). |
| [Open Data van de Rechtspraak](https://www.rechtspraak.nl/uitspraken/open-data) | Free public service for (pseudonymised) decisions and ECLI metadata as XML, in two steps (ECLI index `zoeken`, then `content`). "Het is niet mogelijk een complete set te downloaden en het is niet mogelijk meer dan 10 requests/calls per seconde in te dienen"; take other users into account. |
| Technical documentation v1.15 (PDF there) | "Don't hammer the server!": avoid concurrent requests; every record has `dcterms:accessRights` "public" ("vrij gebruikt mag worden"); search by court, date, `modified`; max 1000 entries. |
| [Colofon](https://www.rechtspraak.nl/colofon) | "U mag informatie van deze website overnemen, als u de bron vermeldt", without suggesting endorsement by the Rechtspraak. |
| Art. 11 Auteurswet; [Uitspraken](https://www.rechtspraak.nl/uitspraken) | No copyright in "rechterlijke uitspraken" (quoted in `nl-bwb`). Only a selection of decisions is published (criteria since 2012). |

Conclusion: licence **approved** (reuse with attribution; no restriction on automated use stated; commercial use is an
inference). ULIS attributes "Bron: Rechtspraak.nl" and does not try to re-identify pseudonymised persons. Retrieval:
`PoliteHttp`, identified User-Agent, sequential, 5 s apart, cached in `var/sources/`. Onboarding made 56 requests: 5 on www (robots.txt, 4 pages),
the data host's `robots.txt`, 2 value lists, 17 ECLI-index searches, 31 decision fetches (30 ingested, 1 English translation dropped).

## What is ingested

| Court (`court_id`) | Decisions | Paragraphs | Dates |
|---|---|---|---|
| Hoge Raad (`NL-HR`): tort, contract, privacy, criminal | 17 | 454 | 1919-01-31 .. 2021-07-16 |
| Raad van State, Afdeling bestuursrechtspraak (`NL-ABRVS`) | 6 | 307 | 2019-05-29 .. 2022-04-13 |
| Centrale Raad van Beroep (`NL-CRVB`), CBb (`NL-CBB`) | 2 + 2 | 37 + 25 | March 2021 |
| Rechtbank Den Haag (`NL-RBDHA`), Gerechtshof Den Haag (`NL-GHDHA`) | 2 + 1 | 494 + 92 | 2015 .. 2021 |

ECLIs were found or confirmed through the ECLI index. The Urgenda trio is `ECLI:NL:RBDHA:2015:7145`,
`ECLI:NL:GHDHA:2018:2591`, `ECLI:NL:HR:2019:2006`; the index entries `...:7196` and `...:2007` are English translations,
which the parser refuses.

## How a decision is read

- **Fields**: court from the OWMS identifier (must agree with the ECLI), `dcterms:date` as decision date, `psi:zaaknummer`
  as docket, the ECLI as neutral citation and as case name (the data has none; `dcterms:alternative` would be used),
  procedure, legal area, publications in `ext`. Language `nl`, not authoritative (pseudonymised).
- **Paragraphs** (exact text, whitespace collapsed, footnote markers as `[n]`): `headnote` (inhoudsindicatie);
  `considerations` = every numbered `paragroup`/section and every paragraph starting with a number ("4.1."), `num` "3.4.1";
  `procedure`, `decision`, `text` (header, unnumbered) and `footnote` (own paragraphs). Only considerations carry `num`,
  so `r.o.` never points at a procedural, decision or footnote unit. Section titles and images are not stored.
- **Links** (`citation_links`): `source_markup` = the publisher's `Wetsverwijzing` identifiers (`jci1.31:c:BWBR...`, relation
  `references`) and formal relations (`earlier_instance`, `later_instance`, `opinion`); `pattern` = strictly `art. 6:162
  [lid 2] BW` (Books 1, 6), `art. 3:4 Awb`, `art. 294.2 Sr`, `Grondwet`/`Gw`, `UAVG`, and `ECLI:NL:` strings. Lists
  ("art. 6:162 en 6:163 BW"), "oud BW" and other Books are not linked; unresolved targets stay open.

## Citations and authority

`HR 3 april 2020, ECLI:NL:HR:2020:591, r.o. 2`; `ABRvS 6 november 2020, ECLI:NL:RVS:2020:2660, r.o. 3`; no pinpoint for
headnote, decision and footnotes. Dutch courts have no formal stare decisis: every hit is `persuasive_precedent`
("Persuasive only"; rationale "... very persuasive in practice (to verify)"), also with `forum_court=NL-RBDHA`. Core change:
`ParsedRef.extraction_method` (default `source_markup`), so pattern links are not recorded as source markup.

## Real run and tests (dev DB `ulis_nlcase`)

`migrate`, `sync-catalog`, `ingest nl-bwb` (27 documents, from the local cache), `ingest nl-rechtspraak`: 30 judgments,
1,409 paragraphs (869 numbered considerations, 172 footnotes) and passages, 165 citation links (63 resolved: 48 statute
pattern links (all to the article), 9 decision pattern links, 4 formal relations, 2 Wetsverwijzing), no errors. `verify-sources`: 57 pass, 0 fail. Searches (`NL`): "kelderluik
gevaarzetting onrechtmatige daad" -> HR 5 november 1965; "klimaat Staat bevel reductie broeikasgassen" -> Urgenda r.o.
2.3.2; "verwijdering persoonsgegevens bewaartermijn" -> ABRvS 2 maart 2022, r.o. 5.3. 
Tests: 44 in `tests/test_rechtspraak.py` (parser on 6 real fixtures, pattern cases, refusals, connector, PostgreSQL search
with the BW 6 / Awb fixtures); full suite 546 passed (244 s); ruff check, ruff format --check, mypy clean.

## Known gaps

- 30 decisions, not a corpus; the Rechtspraak publishes a selection; no conclusies (AG opinions), other courts, tuchtrecht.
- Pseudonymised text, not the signed original; no subsequent history (an overruling is unknown to ULIS).
- Flat numbering is read from the text (a restart is taken as an enumeration; a number without a dot is not read).
- Statute links only for the ingested Acts and the strict patterns; they point at the work, not at a toestand.
- Commercial-use permission is inferred; counsel review recommended. Court abbreviations and `r.o.` to verify.
