# Switzerland (federal law) - Fedlex

Profile `CH` (draft, unverified), source `ch-fedlex`, parser `fedlex_akn`, connector `fedlex`,
jurisdiction module `ch`. Status: **pilot sample** of seven federal enactments in German, French
and Italian. Cantonal law is not covered.

## Source

[Fedlex](https://www.fedlex.admin.ch), the publication platform for federal law operated by the
Federal Chancellery under the Publications Act (PublA, SR 170.512). It publishes the Official
Compilation (AS), the Federal Gazette (BBl) and the Classified Compilation (SR). Its linked data
(JOLux ontology) is queryable at `https://fedlex.data.admin.ch/sparqlendpoint`; each SR
consolidation (one per date of applicability) is available as Akoma Ntoso XML, HTML, PDF and
Word, per language.

How ULIS gets the files:

1. `scripts/build_fedlex_manifest.py` asks the SPARQL endpoint for the consolidations of each
   chosen work (`jolux:Consolidation`, `jolux:dateApplicability`, `jolux:dateEndApplicability`,
   XML manifestation per language) and writes `config/manifests/ch-fedlex.yaml` (built on
   2026-10-14). It keeps the consolidation applicable on the build date and, for works marked
   `history`, every earlier consolidation of the same unbroken run that has XML in all three
   languages (each starting the day after the previous one ended). Consolidations applicable
   after the build date are listed under `scheduled` and not fetched.
2. `FedlexConnector` fetches exactly the listed files through `PoliteHttp` (identifying
   User-Agent, 5 s between requests, cache under `var/sources/`, offline mode for tests).

**robots.txt.** `www.fedlex.admin.ch/robots.txt` allows everything except print views.
`fedlex.data.admin.ch/robots.txt` disallows `/filestore/*` for all user agents. The linked data
names files under `https://fedlex.data.admin.ch/filestore/`; the Fedlex website maps that prefix to
`https://www.fedlex.admin.ch/filestore/` (`publicMapping` in
`https://www.fedlex.admin.ch/api/assets/app-config.json`) and loads law texts from there. ULIS
queries SPARQL on `fedlex.data.admin.ch` (allowed), never requests
`fedlex.data.admin.ch/filestore/`, and downloads from the `www` mapping, which robots.txt allows.
This reading should be confirmed with the Federal Chancellery before any larger crawl (see
"Unsure" below). About 95 HTTP requests were made in total for this work (robots, terms pages,
the website configuration, 6 SPARQL queries, 67 XML files).

## Licence and legal status (read 2026-10-14)

* [Legal information](https://www.fedlex.admin.ch/de/legal-information) (served from
  `api/assets/editable-items/editable-pages/de/legal-information.html`): the contents are not
  protected works (Art. 5 Copyright Act, SR 231.1); consulting and downloading data for
  exploitation is free of charge (Art. 19 para. 1 PublA, Art. 48 para. 2 PublO); exploitation is
  subject to Art. 49 PublO.
* [Technical and legal information for third-party users](https://www.fedlex.admin.ch/de/broadcasters):
  quotes Art. 49 PublO; "Das XML Format ist für eine maschinelle Verarbeitung der Daten angedacht".
* Art. 49 para. 1 PublO (SR 170.512.1, in force since 1 July 2022, read in de/fr/it): data must not
  be altered (a), must be visually distinct from comments (b), must carry the notice "Dies ist
  keine amtliche Veröffentlichung. Massgebend ist allein die Veröffentlichung durch die
  Bundeskanzlei." (c), must not look like an official publication (e), sensitive personal data
  rules (f, g), and may be redistributed for a fee only in a value-added form (h).
* opendata.swiss: no dataset for the SR consolidations was found; its
  [terms of use](https://opendata.swiss/en/terms-of-use) therefore do not apply here.

Conclusion: `review_status: approved`, every permission `allowed_with_conditions`, with the
conditions recorded in `config/sources/ch-fedlex.yaml`. The notice is the source's attribution text
(German, French and Italian) and is shown on every result.

Legal status of the text: the AS version is authoritative (Art. 15 para. 1 PublA), and the version
published on the platform is authoritative (Art. 15 para. 2, in force since 1 January 2016). The SR
is a consolidated, continuously updated collection of the AS texts (Art. 11 PublA) and is not
authoritative. German, French and Italian enactments are equally binding (Art. 14 para. 1 PublA).
ULIS therefore records each language version with `text_origin = official_consolidation`,
`authoritative = true` (an authentic language) and source authority `official_non_authentic`.
Every result carries `OFFICIAL_CONSOLIDATION_REPRODUCED`, `SOURCE_NOT_AUTHENTIC` and
`EQUALLY_AUTHENTIC_VERSIONS`.

## What is ingested

| SR | Enactment (de / fr / it abbreviation) | Work URI | Consolidations ingested |
|---|---|---|---|
| 101 | Federal Constitution (BV / Cst. / Cost.) | `/ch/cc/1999/404` | 6: 2021-01-01, 2021-03-07, 2021-11-28, 2022-02-13, 2024-01-01, 2024-03-03 |
| 210 | Civil Code (ZGB / CC / CC) | `/ch/cc/24/233_245_233` | 1: 2026-07-01 |
| 220 | Code of Obligations (OR / CO / CO) | `/ch/cc/27/317_321_377` | 1: 2026-10-01 |
| 311.0 | Criminal Code (StGB / CP / CP) | `/ch/cc/54/757_781_799` | 1: 2026-10-01 |
| 235.1 | Data Protection Act (DSG / LPD / LPD) | `/ch/cc/2022/491` | 3: 2023-09-01 (entry into force), 2025-04-01, 2025-07-07 |
| 272 | Civil Procedure Code (ZPO / CPC / CPC) | `/ch/cc/2010/262` | 7: 2021-01-01 to 2026-07-01 |
| 172.021 | Administrative Procedure Act (VwVG / PA / PA) | `/ch/cc/1969/737_757_755` | 2: 2021-01-01, 2022-07-01 |

Each consolidation is ingested in German, French and Italian (63 files).

## Structure, dates and versions

* **Provisions**: articles, numbered paragraphs, lettered and numbered list items, annexes, and the
  articles of the ZGB final title. ULIS eIds are built from the printed labels and are identical in
  the three languages (`art_8/para_1`, `art_5/lbl_c/lbl_1`, `final_title/art_1`, `annex_1`), so the
  versions group as parallel authentic versions. The file's own eIds are not used: their
  conventions changed between consolidations (`lbl_abis` / `lbl_a_bis`) and they number unlabelled
  elements by position. Grouped articles printed in words ("Art. 50 und 51", "Art. 28d à 28f") take
  their key from the file's eId (`art_50-51`). A few structures differ between the language
  versions in the source itself (e.g. Italian Art. 94 ZGB has two paragraphs); they are kept as
  published.
* **Text** is the exact source text without footnotes; footnotes (amendment history,
  cross-references) are kept as notes. Conversion placeholders (`[tab]`) are dropped.
  Cross-references to other SR enactments in footnotes become `cites` links.
* **Repealed** provisions ("…", or the word "Aufgehoben"/"Abrogé"/"Abrogato") get status
  `repealed`, empty text, the footnote as note, and the repeal date stated in the footnote ("mit
  Wirkung seit 1. Jan. 2008"). When the footnote gives no complete date the date stays unknown
  (e.g. 3 ZGB, 1-2 OR and 2-9 StGB versions per language). Obsolete provisions ("Gegenstandslos")
  are recorded the same way.
* **Validity** (`validity_basis = consolidation_series`): a consolidation states the whole text
  applicable from its date of applicability, so each in-force provision version starts on the date
  of the first indexed consolidation that shows its wording, and is replaced when a later
  consolidation shows a different wording (derived end date). Dates before the earliest indexed
  consolidation are a history gap (`history_not_indexed`, `HISTORY_INCOMPLETE`). For the DSG the
  indexed history starts at entry into force (2023-09-01); ULIS still reports dates between adoption
  (2020-09-25) and entry into force as a history gap, which is cautious but not wrong.
* **Currency**: `currency_date` is the consolidation date ("Stand am"), the only currency the file
  states. Queries after it carry `AS_OF_AFTER_CURRENCY`. When the manifest was built (2026-10-14)
  no later applicable consolidation existed for any of the seven works.

### Core change (pipeline, gated)

The pipeline keyed provision versions on their start date, so a series of whole-instrument
snapshots that date every provision from the snapshot would have created a new, identical version
of every provision for every consolidation. Two rules were added to `ulis/ingestion/pipeline.py`,
active only for parsed documents with `validity_basis = consolidation_series` (no other source
uses it, so existing behaviour is unchanged):

1. **Continuation** (`Ingestor._continued`): when a later consolidation shows the same status and
   text as the latest earlier version of a provision (in that language), and that version has no
   explicit end, the new document corroborates it instead of creating a version.
2. **Closure** (`Ingestor._close_absent`): when the latest consolidation no longer contains a
   provision (renumbered, restructured or dropped), its open in-force version ends on that
   consolidation's date (`valid_to_basis = explicit`), so it is not shown as in force afterwards.
   In the real run this ended 12-14 ZPO versions per language (2025-01-01 restructuring).

Consolidations must be ingested oldest first (the connector does so); extending a history
backwards later would need a fresh database.

## Citations (module `ch`)

| Provision | de | fr | it |
|---|---|---|---|
| Art. 8 para. 1 Constitution | Art. 8 Abs. 1 BV | art. 8 al. 1 Cst. | art. 8 cpv. 1 Cost. |
| Art. 41 para. 1 CO | Art. 41 Abs. 1 OR | art. 41 al. 1 CO | art. 41 cpv. 1 CO |
| Art. 5 let. c no. 1 FADP | Art. 5 lit. c Ziff. 1 DSG | art. 5 let. c ch. 1 LPD | art. 5 lett. c n. 1 LPD |
| Art. 24 para. 5bis FADP | Art. 24 Abs. 5bis DSG | art. 24 al. 5bis LPD | art. 24 cpv. 5bis LPD |
| Final title Art. 1 Civil Code | Art. 1 SchlT ZGB | art. 1 Tit. fin. CC | art. 1 Tit. fin. CC |
| Annex 1 FADP | Anhang 1 DSG | annexe 1 LPD | allegato 1 LPD |

The abbreviation is the source's own short form per language (`FRBRname/@shortForm`). The SR
number and ELI are kept in `ext` (`sr_number`, `eli`, `entry_into_force`). Conventions to be
confirmed by a Swiss reviewer (e.g. "Bst." vs "lit.", commas in French).

## Known gaps

* Seven enactments only; no ordinances, other acts, international treaties (SR 0.x), cantonal law,
  case law (Federal Supreme Court and other federal courts) or AS texts.
* History only for BV, DSG, ZPO and VwVG, and only from the first consolidation published in XML
  (2021 at the earliest). ZGB, OR and StGB: current consolidation only.
* Consolidations published before they apply (Art. 26 let. d PublO; StGB from 2026-11-01, OR from
  2027-07-01, VwVG from 2027-01-01, BV from 2029-01-01) are not ingested and not reported per
  provision; the connector records them as `scheduled_consolidations` in the source document
  metadata only.
* Not ingested: final and transitional provisions other than the ZGB final title (18 blocks in the
  OR, 6 of them with their own Art. 1 ff.; 5 in the StGB; the ZGB "Wortlaut der früheren
  Bestimmungen"; the BV final provisions) and the BV subject index; they have no language-neutral
  label ULIS can cite unambiguously.
* Articles with unnumbered subdivisions (Art. 196 and 197 BV, transitional provisions) are one
  provision each and are cited at article level.
* Wording differences that are only typographic in the source ("…" vs "...") create a new version,
  because text is compared exactly.
* Romansh and English versions are not ingested.

## Results (2026-10-14)

Real run on `ulis_ch`: `migrate`, `sync-catalog`, `ingest ch-fedlex`, `verify-sources`.

| Ingest report | |
|---|---|
| documents seen / new | 63 / 63 |
| instruments / instrument versions | 7 / 63 |
| provision versions created | 45,370 |
| provision versions corroborated (continued) | 47,847 |
| restated / conflicts / errors | 0 / 0 / 0 |
| citation links created / resolved | 1,249 / 264 |
| passages indexed | 35,900 |

`verify-sources`: 63 of 63 source documents pass (93,217 provision units re-derived from the raw
files and matched by hash).

Tests in the worktree: `pytest` (full suite against `ulis_test_ch`) 137 passed, including 15 in
`tests/test_fedlex.py` (parser structure in three languages, citations in de/fr/it, repeal dates,
grouped articles, final title, rejection of unsupported documents, offline connector, and
PostgreSQL ingestion, continuation, closure of absent provisions, point-in-time lookups, search
with limitations); `ruff check`, `ruff format --check` and `mypy ulis` clean.

Sample searches (`ulis.cli search --jurisdiction CH`):

| Query | As of | First result |
|---|---|---|
| Alle Menschen sind vor dem Gesetz gleich | 2026-10-14 | Art. 8 Abs. 1 BV ("Alle Menschen sind vor dem Gesetz gleich."), with `AS_OF_AFTER_CURRENCY` (consolidation of 2024-03-03) |
| cause d'une manière illicite un dommage à autrui | 2026-10-14 | art. 41 al. 1 CO |
| dati personali degni di particolare protezione | 2026-10-14 | art. 5 lett. c LPD, then art. 6 cpv. 7 lett. a LPD, art. 34 cpv. 2 lett. a LPD |
| Kinder und Jugendliche in ihrer Entwicklung gefördert | 2021-06-01 | Art. 41 Abs. 1 lit. g BV, earlier wording, with `LATER_VERSION_EXISTS` (2022-02-13) and `HISTORY_INCOMPLETE` |
| same | 2023-06-01 | Art. 41 Abs. 1 lit. g BV, wording "... sowie ihre Gesundheit gefördert wird." |
| Wirkungen von Tatsachen vor dem Inkrafttreten dieses Gesetzes | 2026-10-14 | Art. 1 SchlT ZGB |

## Unsure / to verify

* The fetch path: downloading from the website's public filestore mapping while the data host's
  robots.txt disallows `/filestore/` there. It follows the robots.txt of the host actually used and
  the publisher's own mapping, but the Federal Chancellery should confirm it is acceptable for
  automated reuse.
* Whether "official_consolidation" with `authoritative = true` per language is the best fit for a
  non-authoritative SR text in an authentic language (the limitations state the AS prevails).
* Instrument type is derived from the SR number (101 = constitution) and the preamble naming the
  Federal Assembly (federal act); other documents are rejected.
* Profile facts on courts, precedent and treaty reception are marked "To verify".
