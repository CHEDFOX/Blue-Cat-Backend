# Netherlands (NL): national legislation from the Basiswettenbestand

Status: **the wetten, AMvB's and ministeriele regelingen of the Basiswettenbestand that are in force, draft unverified
profile.** About 13,600 regulations (of the 43,430 `BWBR` identifiers in the publisher's sitemap), Dutch only; the full version
history (all toestanden since the BWB starts) for two of them (Grondwet, UAVG), the current consolidation for all others. Research
aid, not legal advice. Not reviewed by a Dutch lawyer.

| | |
|---|---|
| Profile | `config/jurisdictions/nl.yaml` (`NL`, parent `INT`, `draft_unverified`, version 0.3.0) |
| Source | `config/sources/nl-bwb.yaml` (`nl-bwb`, active, CC0 1.0) |
| Manifest | `config/manifests/nl-bwb.yaml` (43,430 regulations; script `scripts/build_bwb_manifest.py`) |
| Connector | `ulis/sources/connectors/bwb.py` (`bwb`, on `PoliteHttp`) |
| Parser | `ulis/sources/parsers/bwb.py` (`bwb_xml` 1.2.0) |
| Citations | `ulis/jurisdictions/modules/nl.py` (`nl`) |
| Tests | `tests/test_bwb.py`, fixtures in `tests/fixtures/bwb/` |

## Widening to the whole collection (2026-10-15)

- **Where the list comes from.** The repository has no list of the regulations (`/bwb/` redirects to a description of the service),
  the SRU service disallows all robots, and the base set of the whole collection (about 160 GB, 14 GB zipped) is given out on request
  through KOOP's contact form once a month - not obtainable by this project. The one public list is the sitemap that
  `wetten.overheid.nl/robots.txt` declares (`Sitemap: https://wetten.overheid.nl/sitemap.xml`; the catch-all `User-agent: *` allows
  it; 7 MB, 47,145 addresses). `scripts/build_bwb_manifest.py` asks for that one file (identifying User-Agent, 30 s delay, the delay
  KOOP sets for the legal-research bots it allows) and takes the BWB identifier from each address: **43,430 `BWBR`** regulations
  (3,704 treaties `BWBV` are left out). No page of the website is fetched.
- **Conduct note.** KOOP's robots.txt disallows named AI/LLM crawlers (ClaudeBot, anthropic-ai, GPTBot, "rag-kennisbank", ...) on the
  website. ULIS's own identified research bot is not among them, it reads one declared sitemap from the website and the CC0 files
  from the repository (the route the dataset page describes). KOOP's stated preference about AI use stays the open question of
  `docs/licence-review.md` (it weighs more now), and the project cannot get KOOP's view: an operator who counts it as binding should
  leave this source off.
- **What is stored.** Only wetten (and rijkswetten), AMvB's and ministeriele regelingen (also the archiefselectielijsten) in force.
  The connector reads each regulation's `manifest.xml`; if the last toestand has ended the regulation has lapsed and is skipped
  (`connector.lapsed`); if `wetgeving/@soort` is another kind (beleidsregel, zbo, KB, pbo, circulaire, the `-BES` variants) the toestand
  is dropped (`connector.unsupported`); a 404 or an unreadable manifest (a regulation with no toestand in force yet) is a
  `fetch_errors` entry and the run goes on. Parser 1.2.0 also reads `<regeling>` bodies (most ministerial regulations), the
  unnumbered `<enig-artikel>` and skips `<officiele-inhoudsopgave>`.
- **Scale** (random sample of 319 regulations): 58% lapsed, 31% in force and stored, 9% another kind, 1% not yet in force. So about
  61,000 requests (43,430 manifests + 17,700 toestanden), **85 hours at 5 s**, about 13,600 regulations, 96 provision versions each
  (median 11; the largest has 1,946), about **1.3 million provision versions** and **about 3 GB of database** (raw XML on top). Below the
  15 GB threshold, so the default manifest is the whole list. Ingest in batches: `python scripts/ingest_in_batches.py nl-bwb --batch 200`.
- **Sample run** (fresh database, 319 random regulations plus the six pilot regulations): **100 stored** (9,616 provision versions, 7,873
  passages), 3 skipped as "no toestand in force when the manifest was generated", **0 parse errors**. Before the parser read these
  structures the first draws had 20 parse errors (18 `<regeling>` bodies, 1 `<enig-artikel>`, 1 table of contents). The 186 lapsed
  and 30 other-kind regulations cost a manifest request (the second group also its toestand) and are not stored. `verify-sources` on
  that database (227 Dutch documents with 408 Swedish, 311 Irish and 18 Danish): **964 of 964 pass**, 128,588 units re-derived from the
  raw bytes. `tests/test_bwb.py` now has 25 tests (22 unit, 3 PostgreSQL).
- Searches: "standaardpremie 2014 vastgesteld" -> the Regeling vaststelling standaardpremie 2014, art. 1 (a `<regeling>`); the pilot
  searches below return the same first hits as before.

## Source and licence

The Basiswettenbestand (BWB) holds the consolidated texts of Dutch national regulations; KOOP
(Kennis- en Exploitatiecentrum voor Officiële Overheidspublicaties) publishes it on
wetten.overheid.nl and, as open data, in the repository
`https://repository.officiele-overheidspublicaties.nl/bwb/<BWB id>/` (a `manifest.xml` per
regulation, one XML file per *toestand* = consolidated version, and a WTI file with the
"wetstechnische informatie"). What the publisher says (read on 2026-10-14; robots.txt first):

| Page | Finding |
|---|---|
| [data.overheid.nl: Basis Wetten Bestand](https://data.overheid.nl/dataset/basis-wetten-bestand) | Data owner KOOP (Rijk); "Licentie: CC-0 (1.0)" for the dataset and for each download (toestand, WTI and manifest schemas, handleiding); "Toegang: Publiek". "Alle regelingen uit het basiswettenbestand zijn als open data beschikbaar in XML-formaat. Deze kunnen gedownload worden over HTTPS vanaf de repository. Daardoor is het mogelijk om een eigen kopie van het basiswettenbestand op te bouwen." |
| [Een eigen kopie van het basiswettenbestand opbouwen](https://www.overheid.nl/help/wet-en-regelgeving/een-eigen-kopie-van-het-basiswettenbestand-opbouwen) | About 45,000 regelingen with more than 100,000 toestanden, each with a WTI and a manifest file; manifest, WTI and toestand files "kunt u eenvoudig via HTTPS downloaden"; schemas `toestand_2015-2.xsd`, `wti_2015-2.xsd`, `_manifest.xsd`; updates via SRU. |
| [Handleiding SRU BWB](https://data.overheid.nl/sites/default/files/dataset/ab78ba3f-bc07-49cc-95f2-0e23b8653181/resources/Handleiding%2BSRU%2BBWB.pdf) | SRU 2.0 at `zoekservice.overheid.nl/sru/Search`, `x-connection=BWB`; records give each toestand's geldigheidsperiode, zichtperiode and its location (and its manifest's and WTI's) on the repository. |
| [Bijzonderheden consolidatie](https://www.overheid.nl/help/wet-en-regelgeving/bijzonderheden-consolidatie) | "De geconsolideerde teksten zijn geen officiële teksten in de zin van de Grondwet. Alleen publicaties in het Staatsblad, de Staatscourant en andere van overheidswege verkrijgbaar gestelde publicatiebladen hebben een officieel karakter." Retroactive effect ("terugwerkende kracht") is recorded in the WTI and marked in the text. |
| [Inhoud, actualiteit, volledigheid en betrouwbaarheid](https://www.overheid.nl/help/wet-en-regelgeving/inhoud-actualiteit-volledigheid-en-betrouwbaarheid) | Acts and AMvBs from 1 May 2002; at least 95% of decisions processed within two working days; errors occur ("A-fouten"); "De informatie in Wet- en regelgeving vormt geen bekendmaking in de zin van de Grondwet." BES legislation since 10 October 2010; Aruba, Curaçao and Sint Maarten are no longer maintained. |
| Art. 11 Auteurswet (BWBR0001886, toestand 2026-01-01_0, from the repository) | "Er bestaat geen auteursrecht op wetten, besluiten en verordeningen, door de openbare macht uitgevaardigd, noch op rechterlijke uitspraken en administratieve beslissingen." |

**robots.txt** (read before anything else):

| Host | robots.txt | ULIS use |
|---|---|---|
| `repository.officiele-overheidspublicaties.nl` | none (404) | all downloads |
| `zoekservice.overheid.nl` (SRU) | `User-agent: *` / `Disallow: /` | **not used** - the toestand list comes from each regulation's `manifest.xml` instead |
| `wetten.overheid.nl` (website) | catch-all allows most paths but disallows `/*/informatie/xml`, `/service/`, search results; **named AI/LLM crawlers are disallowed entirely** (GPTBot, ClaudeBot, Claude-Web, anthropic-ai, CCBot, PerplexityBot, "rag-kennisbank", ...); legal-research bots "AstralLaw" and "45weg-legal-research" allowed with Crawl-delay 30 | **no page crawled**; robots.txt and, since the widening, the declared `sitemap.xml` (one request) are read |
| `www.overheid.nl`, `data.overheid.nl` | catch-all allows the help and dataset pages; data.overheid.nl Crawl-delay 10; both disallow ClaudeBot and other AI crawlers | 8 documentation pages, 10 s apart on data.overheid.nl |

Conclusion: licence review **approved** (CC0 1.0 for the BWB open data; Acts are without
copyright). ULIS attributes the source anyway and says on every result that the text is not
official. **Flag for the project lead:** KOOP's website and portals block named AI/LLM crawlers,
including a "rag-kennisbank". ULIS fetched only CC0 open data from the repository host (which has
no robots.txt) with its own identified User-Agent, and did not crawl wetten.overheid.nl (apart from its sitemap, see above); but the
publisher's stated preference should be weighed before ULIS uses this corpus for model training
or retrieval-augmented generation at scale.

Retrieval: `PoliteHttp`, User-Agent `ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)`,
5 s between requests (10 s on data.overheid.nl), HTTPS to the repository host only, cache under
`var/sources/repository.officiele-overheidspublicaties.nl/`. The onboarding made about 96 requests
in all: about 46 in a first exploration session whose cache was lost with its worktree, then 50
re-fetches (5 robots.txt files, 6 manifests, 21 toestanden, 6 WTI files read once for the
abbreviations, the Auteurswet manifest and toestand, 8 documentation pages), 2 of them automatic
retries. The SRU service was never called.

## What is ingested

| Act | BWB id | Toestanden ingested | Entry into force (work date) | Provision versions | repealed | prospective | Passages | Current toestand (SHA-256) | Currency |
|---|---|---|---|---|---|---|---|---|---|
| Grondwet | BWBR0001840 | **all 11** (2002-03-21_0 ... 2023-02-22_0) | 1840-09-12 | 451 | 34 | 1 | 334 | 2023-02-22_0 (`01daa61076951c9d…`) | 2026-09-15 |
| Uitvoeringswet AVG (UAVG) | BWBR0040940 | **all 6** (2018-05-25_0 ... 2026-09-01_0) | 2018-05-25 | 379 | 0 | 5 | 322 | 2026-09-01_0 (`3c251b75faaa79bf…`) | 2026-10-14 |
| Burgerlijk Wetboek Boek 1 | BWBR0002656 | current | 1970-01-01 | 2,065 | 123 | 1 | 1,613 | 2025-07-05_0 (`1818aea1ec493987…`) | 2026-10-14 |
| Burgerlijk Wetboek Boek 6 | BWBR0005289 | current | 1992-01-01 | 1,370 | 3 | 0 | 1,181 | 2026-07-16_0 (`091f3702115565ab…`) | 2026-10-06 |
| Wetboek van Strafrecht | BWBR0001854 | current | 1886-09-01 | 2,268 | 126 | 0 | 1,820 | 2026-07-01_0 (`bdd6982a889ef6a1…`) | 2026-10-14 |
| Algemene wet bestuursrecht | BWBR0005537 | current | 1994-01-01 | 2,091 | 19 | 1 | 1,712 | 2026-08-15_0 (`5916d0e65a5f9bdf…`) | 2026-10-14 |
| **Total** | | **21 toestanden + 6 manifests** | | **8,624** | **305** | **8** | **6,982** | | |

Work URI `/nl/bwb/<BWB id>`; one Dutch expression per toestand, `/nl/bwb/BWBR0001840/nld@2023-02-22_0`.
"Currency" is the date the regulation's manifest was generated (see below).

## How the XML is read

- **Two documents per regulation.** Each toestand (`<toestand>`) is the whole regulation as valid
  from its `@inwerkingtreding`. The `manifest.xml` (`<work>`) is ingested after the toestanden as a
  *change report* on the current toestand (`ulis.ingestion.snapshots`).
- **Structure.** Boek, titeldeel, hoofdstuk, afdeling, paragraaf and divisie are headings
  (`context_heading`, e.g. "Boek 6 Algemeen gedeelte van het verbintenissenrecht – Titel 3
  Onrechtmatige daad – Afdeling 1 Algemene bepalingen"). Provisions: articles (`art-162`,
  `art-3:4`, `art-xix`), members (`art-162-lid-1`), items of numbered lists (`art-4:21-lid-2-onderdeel-a`,
  nested `...-onderdeel-a-onderdeel-1`) and annexes (`bijlage-2`, one provision each). An
  unnumbered article is named from its heading (`art-algemene-bepaling`, the Grondwet's general
  provision of 2022). Unmarked or dash lists stay text; members or items whose numbers repeat in one
  article stay undivided (Awb art. 8:36c holds two parallel texts with members 1-4 each), since
  "lid 1" would be ambiguous. Containers keep their members' numbers and items' labels; a leaf's
  text starts after its own label. The preamble (`aanhef`), closing formula (`wetsluiting`) and
  structural editorial text are not provisions.
- **Exact text, editorial material in notes.** `<redactie>` elements are KOOP's editorial text and
  never go into `text`: "Vervallen" (repealed), "Dit lid is nog niet in werking getreden.",
  "Wijzigt deze wet." (amending articles whose text the consolidation omits) and insertions inside
  a sentence such as the indexed amounts in art. 4:113 Awb ("Redactioneel, bij “vergoeding bedraagt
  € 9 indien de schuld minder dan € 454”: Per 1 januari 2015: € 500") or art. 23 lid 4 Sr ("Per 1
  januari 2026: € 550."). The note also carries the source publication (`Bron: Stb.2023-62
  (wijziging)`), remarks from the element's metadata and any retroactive date. A member or item
  shares its article's notes, not its siblings'.
- **References.** `intref`/`extref` Juriconnect identifiers become citation links
  (`jci1.3:c:BWBR0001840&hoofdstuk=7&artikel=132a&...` → `/nl/bwb/BWBR0001840`, `art-132a`;
  `lid=` → `-lid-N`). 4,200 links, 2,107 resolved to an ingested instrument.
- **Type.** `wetgeving/@soort` `wet` → `act` (the Grondwet → `constitution`), `AMvB` →
  `order_in_council`, `ministeriele-regeling` → `ministerial_regulation`; anything else is rejected.
- `text_origin = official_revised`, `authoritative = false`, source authority `official_non_authentic`.
- **XML safety.** defusedxml with DTDs, entities and external references forbidden (the files
  have no DOCTYPE); only the roots `toestand` and `work` are accepted; unknown elements in the
  regulation body are rejected loudly.

## Dates and versions

- **Per-article dates.** Every BWB element carries `@inwerking` (the date that version of the
  element entered into force), `@bron` and `@effect`; members and list items have none and share
  their article's. ULIS dates every provision from its article's `@inwerking`
  (`valid_from`); repealed articles (`@status="vervallen"`) start a `repealed` version on the
  repeal date; `@status="nogniet"` (and "nog niet in werking getreden") is `prospective` with no
  start date.
- **Toestanden as snapshots** (`validity_basis = revision_snapshot`, consolidation kind
  `point_in_time`): toestanden are ingested oldest first; an unchanged wording continues the
  version recorded from the previous toestand (5,257 corroborations), a changed wording starts a
  new version at its article's new `@inwerking` and ends the previous one there (derived end), and a
  provision absent from a later toestand ends on that toestand's date (explicit end). Over the full
  Grondwet and UAVG series every changed wording had a new `@inwerking` between the previous and
  the new toestand; no "restated" versions or conflicts occurred.
- **What `@inwerking` means.** It is the date of the element's latest version *in the BWB*, which can
  be a technical re-placement of the text rather than a change in wording: Stb. 2019, 33
  ("tekstplaatsing") gave all 147 in-force Grondwet articles `@inwerking` 2018-12-21, as Stb. 2002,
  200 had done with 2002-03-21 for 143 of them. With the full series the unchanged wording continues from 2002-03-21; a current-only
  ingestion of the same Act would date it from 2018-12-21. Dates are what the source states, not the
  date the wording was first enacted.
- **History gaps.** The BWB starts in 2002 for Acts. Before an instrument's first ingested toestand,
  a date after the Act's entry into force (`intitule/@inwerking`, the work date) is reported as
  `history_not_indexed`; before the entry into force as `not_yet_in_force`. For the four
  current-only Acts every article is still dated from its own `@inwerking` (e.g. art. 6:162 BW from
  1992-01-01), so a search on an earlier date finds it, with `HISTORY_INCOMPLETE` warning that other
  text in force then may be missing.
- **Retroactive effect** (`terugwerkend.datum`) is noted ("Terugwerkende kracht tot en met
  2006-01-01" on art. 4:21 Awb); the version still starts on its entry into force, because the
  earlier toestanden attest a different text for the retroactive period.
- **Two time axes in the source.** When a toestand is later corrected for retroactive effect, the BWB
  keeps the old variant (`_0`, zichtdatum closed) and adds a corrected one (`_1`). ULIS uses the
  variants visible when the manifest was generated (Awb: 185 of 236 listed toestanden, Sr 120 of
  126, BW 1 60 of 66).
- **Currency and scheduled changes.** The manifest's generation date (`@gegenereerd`) is the
  currency date of the current toestand. Toestanden starting after it are outstanding changes
  ("Consolidated version scheduled from ... not applied to this text and not ingested"), never
  text; none of the six Acts had one on 2026-10-14 (tested on an edited manifest). The start of
  every listed toestand is a `version_change` lifecycle event (445, plus 6 entries into force).
- The manifest's own `datum_inwerkingtreding` is not used: for the Wetboek van Strafrecht it says
  1886-01-30, the toestand 1886-09-01.
- **Hashes.** The connector compares each download with the manifest's SHA-512: 15 of 21 match;
  the six Grondwet toestanden of 2002-2017 do not (KOOP regenerated those files with richer
  metadata, e.g. 34,177 bytes listed against 453,738 served). Recorded per document
  (`manifest_sha512_matches`), not fatal; ULIS's own SHA-256 of the bytes received is what
  `verify-sources` checks.

## Citations

Dutch practice: "art." + number, "lid", "onder" for list items and "sub" below that, then the
abbreviation chosen in the profile (`extensions.nl.abbreviations`, each validated to be one of the
abbreviations the regulation's WTI lists, or its citeertitel), else the citeertitel itself.

| Provision | Citation |
|---|---|
| `/nl/bwb/BWBR0005289/~art-162-lid-1` | art. 6:162 lid 1 BW |
| `/nl/bwb/BWBR0002656/~art-30-lid-1` | art. 1:30 lid 1 BW |
| `/nl/bwb/BWBR0001854/~art-310` | art. 310 Sr |
| `/nl/bwb/BWBR0001840/~art-1` | art. 1 Grondwet |
| `/nl/bwb/BWBR0001840/~art-algemene-bepaling` | Algemene bepaling Grondwet |
| `/nl/bwb/BWBR0005537/~art-3:4-lid-2` | art. 3:4 lid 2 Awb |
| `/nl/bwb/BWBR0005537/~art-4:21-lid-2-onderdeel-a` | art. 4:21 lid 2 onder a Awb |
| `/nl/bwb/BWBR0005537/~bijlage-2` | Bijlage 2 Awb |
| `/nl/bwb/BWBR0040940/~art-46` | art. 46 UAVG |

WTI abbreviations: Grondwet GW, Gw (cited in full, "Grondwet"); UAVG; BW, BW Boek 1, BW1 / BW,
BW Boek 6, BW6; Sr, WvS, WvSr; Awb. The BW book number comes from the citeertitel ("Burgerlijk
Wetboek Boek 6"). To verify with a Dutch reviewer: "onder a" versus "sub a", and "Gw" versus
"Grondwet".

## Profile

Civil law, unitary (decentralised), treaty reception `monist` with arts. 93-94 Grondwet quoted
"To verify". Courts: Hoge Raad, Afdeling bestuursrechtspraak van de Raad van State, Centrale Raad
van Beroep, College van Beroep voor het bedrijfsleven (rank 1 each in their field), gerechtshoven
(rank 2, appeal to the Hoge Raad), rechtbanken (rank 3; simplified appeal routes). Precedent
`no_formal_precedent`, no vertical binding; art. 120 Grondwet ("De rechter treedt niet in de
beoordeling van de grondwettigheid van wetten en verdragen.") and the treaty review of arts. 93-94
in the notes, "To verify". No `memberships` (the EU membership is added by the lead). Language `nl`
uses PostgreSQL's `dutch` configuration (already in `SEARCH_CONFIGS`).

## Change to shared code

`ulis/ingestion/pipeline.py` (3 lines): a provision recorded as a container (not indexed) whose
later version is a leaf or has its own text is now marked `index_unit = True`. Without it, a
snapshot series could never index the new wording: art. 46 UAVG had two members until 2026-09-01
and one undivided text since, and its current text was not searchable. Side effect: the earlier
composite text of such an article (members 1 and 2 together) is indexed too, so a 2020 search
returns both "art. 46 UAVG" and "art. 46 lid 1 UAVG". Passages for this corpus: 6,978 → 6,982. All
other jurisdictions' tests pass unchanged. Registrations (parser, connector, `resolve_uri`, module)
are one line each; `tests/test_catalog.py` lists `NL` and `nl-bwb`.

## Known gaps

- Wetten, AMvB's and ministeriele regelingen in force only (about 13,600 of 43,430 identifiers); no treaties, policy rules
  (beleidsregels), zbo regulations, KB's, BES variants or lapsed regulations; no decentral regulations (CVDR), Aruba/Curaçao/Sint
  Maarten out of scope; case law only as a 30-decision sample (see [nl-case-law.md](nl-case-law.md)); no EU law in this profile.
- History only for the Grondwet and the UAVG; the BWB itself starts in 2002. The list of regulations is the website's sitemap, so one added later is missing until the manifest is regenerated; a regulation that fails to parse costs two errors (its toestand and the change report of its manifest).
- Not the authentic text: the Staatsblad and Staatscourant are not ingested.
- Members and list items carry their article's date; `@inwerking` can be a technical re-placement
  date; retroactive effect is a note, not a validity period; superseded variants of corrected
  toestanden are not ingested.
- A provision that was prospective in an earlier toestand keeps that prospective version in its
  timeline after it entered into force (e.g. art. 21a UAVG).
- Preambles, closing formulas, the articles inside annexes (each annex is one provision) and
  parallel texts within one article are not divided further.
- The citation abbreviations are configured from the WTI (read once, not ingested); the WTI's change
  history per article is not used.
- Frisian texts and translations are not ingested.

## Results (2026-10-14)

Checks in the worktree: `ruff check` and `ruff format --check` (110 files) clean; `mypy ulis` clean
(71 source files); full test suite against PostgreSQL (`ulis_test_nl`): **224 passed**, of which
`tests/test_bwb.py` has 20 tests (17 unit, 3 PostgreSQL), plus the NL additions to
`tests/test_catalog.py`. Fixtures: 675 KB (gzip) - the full UAVG series, two Grondwet toestanden,
the current BW Boek 6 and Awb, and their manifests.

Real run against `ulis_nl`: `migrate`; `sync-catalog` → `{"jurisdictions": 21, "sources": 13,
"concepts": 4}`; `ingest nl-bwb` (from the polite cache, 21 s):

```
documents_seen 27, documents_new 27, instruments_created 6, instrument_versions_created 21,
provision_versions_created 8624, provision_versions_corroborated 5257, provision_versions_restated 0,
citation_links_created 4200, citation_links_resolved 2107, passages_indexed 6982, conflicts [], errors []
```

Running `ingest` again: `documents_unchanged 27`, nothing created (idempotent).

`verify-sources`: 27/27 **pass**, 13,881 provision-version attestations re-derived from the raw bytes
(8,624 versions + 5,257 corroborations) and 451 lifecycle events re-derived (445 from the
manifests).

Sample searches (`python -m ulis.cli search ... --jurisdiction NL`):

| Query | First result |
|---|---|
| `onrechtmatige daad toegerekend schade vergoeden` | art. 6:162 lid 1 BW |
| `wegneemt oogmerk wederrechtelijk toe te eigenen diefstal` | art. 310 Sr |
| `nadelige gevolgen besluit onevenredig doelen` | art. 3:4 lid 2 Awb |
| `huwelijk twee personen verschillend gelijk geslacht` | art. 1:30 lid 1 BW |
| `nummer identificatie persoon bij wet voorgeschreven` | art. 46 UAVG |
| `gelijke gevallen gelijk behandeld discriminatie` `--as-of 2020-01-01` | art. 1 Grondwet, the wording before 2023-02-22 ("... ras, geslacht of op welke grond dan ook ..."), with `LATER_VERSION_EXISTS` ("replaced by a later version with effect from 2023-02-22") and the search-level `HISTORY_INCOMPLETE` ("For 4 instrument(s) in scope, indexed versions start after 2020-01-01 (earliest 2025-07-05)") |

Every hit carries `PROFILE_UNVERIFIED`, `VERSION_PERIOD` (e.g. "This version applies from
1992-01-01; no end date recorded."), `VERSION_NOTE` (source publication and editorial notes),
`CITATOR_COVERAGE`, `REVISED_TEXT` and `SOURCE_NOT_AUTHENTIC`; with the default date (2026-10-14)
after a manifest's generation date also `AS_OF_AFTER_CURRENCY`. Point-in-time lookups:
art. 1 Grondwet is `in_force` with the old wording on 2023-02-21 and the new one on 2023-02-22,
`history_not_indexed` on 2001-01-01; art. 10 lid 1 UAVG ends on 2020-01-01 (explicit); art. 21a
UAVG is `not_yet_in_force` on 2019-01-15 and `in_force` from 2019-02-19; art. 23a UAVG is
`prospective_only` on 2026-10-14; art. 6:162 lid 1 BW is `not_yet_in_force` in 1991.
