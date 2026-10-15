# European Union (EU)

Status: **partial, draft_unverified profile**. Every regulation, directive and decision in force that Cellar holds as
Formex 4 in English (26,875 acts on 2026-10-15), plus the Treaties and the Charter; the Treaties, the Charter and five core
acts also in French and German. Not reviewed by counsel.

## Source and access

| | |
|---|---|
| Source id | `eu-eur-lex` (`config/sources/eu-eur-lex.yaml`) |
| Publisher | Publications Office of the European Union |
| Service used | Cellar, the repository behind EUR-Lex: SPARQL endpoint `https://publications.europa.eu/webapi/rdf/sparql` (metadata) and RESTful item URLs `https://publications.europa.eu/resource/cellar/<id>/DOC_<n>` (files) |
| Connector | `eu_cellar` (`ulis/sources/connectors/eu_cellar.py`, uses `PoliteHttp`) |
| Parser | `eu_cellar` (`ulis/sources/parsers/eu_cellar.py`) |
| Manifest | `config/manifests/eu-eur-lex.yaml` |
| Profile | `EU` (`config/jurisdictions/eu.yaml`), module `eu` (`ulis/jurisdictions/modules/eu.py`) |

**eur-lex.europa.eu was not used.** On 2026-10-14 it answered every automated request, including
`robots.txt`, with an AWS WAF bot challenge (HTTP 202, empty body, `x-amzn-waf-action: challenge`).
ULIS does not pass challenges, so no EUR-Lex page was fetched.

`https://publications.europa.eu/robots.txt` redirects to `https://op.europa.eu/robots.txt`:
`User-agent: *`, `Allow: /`, no `Crawl-delay`; `/resource/` and `/webapi/` are not disallowed.
ULIS identifies itself (`ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)`),
waits at least 5 s between requests, follows no redirects to plain HTTP (item URLs come from
SPARQL and are requested over HTTPS directly) and caches every response under
`var/sources/publications.europa.eu/`. The full manifest needed 78 requests (24 SPARQL queries,
54 files); a re-run makes none.

For each work the connector runs three SPARQL queries (work metadata with the kind of each date;
amending, repealing and correcting acts with their annotations and titles; the files of each
language version), then downloads the files of the requested format. For each work and language
it yields one *bundle*: a JSON envelope with every response used, exactly as received (UTF-8 text,
or base64), with URL, retrieval time and SHA-256. The parser checks every hash. Images (scanned
forms in the Brussels Ia annexes) are listed with their hash but their bytes are not kept.

## Licence (reviewed 2026-10-14)

Conclusion: **reuse authorised with source acknowledgement** (`review_status: approved`, all
permissions `allowed_with_conditions`). Evidence, all from the publisher:

- The Publications Office's dataset *Sparql and RESTful API access to European Union law and
  publications (CELLAR)* on the official EU data portal,
  <https://data.europa.eu/data/datasets/sparql-cellar-of-the-publications-office>, describes the
  exact service used ("Key collections of EU information – legal content and general publications
  – can be fetched directly from … the CELLAR, using the machine-readable SPARQL endpoint facility
  or the HTTP RESTful web services") and licenses every distribution under the *European
  Commission reuse notice*, <http://data.europa.eu/eli/dec/2011/833/oj>.
- Commission Decision 2011/833/EU (read from Cellar, OJ L 330, 14.12.2011, p. 39): documents are
  available for reuse for commercial or non-commercial purposes, free of charge, without individual
  application (art 4); conditions may be acknowledging the source, not distorting the original
  meaning, and non-liability of the Commission (art 6(2)).
- Cellar "Users" page, <https://op.europa.eu/en/web/cellar/users>: data re-users (academic and
  research institutions, publishers …) "can search and retrieve publications and their associated
  metadata directly from Cellar".
- Publications Office copyright notice,
  <https://op.europa.eu/en/web/about-us/legal-notices/publications-office-of-the-european-union-copyright>:
  refers reuse of EUR-Lex content to the EUR-Lex notice; questions on reuse of Cellar content go to
  op-copyright@publications.europa.eu.
- data.europa.eu copyright notice, <https://data.europa.eu/en/copyright-notice>: restates the
  Commission reuse policy (Decision 2011/833/EU).
- Court of Justice, <https://curia.europa.eu/site/jcms/d2_5167/en/copyright>: case law may be
  reproduced with acknowledgement (case law is a separate source, see `eu-case-law.md`).

Not verified: the EUR-Lex legal notice itself
(<https://eur-lex.europa.eu/content/legal-notice/legal-notice.html>) because of the bot challenge.
Decision 2011/833/EU covers Commission documents and documents the Publications Office publishes
on its behalf; acts of the Parliament and Council and the Treaties are reused on the strength of
the publisher's licence statement for the Cellar dataset. A person should read the EUR-Lex notice
in a browser, and counsel should review before production use.

Attribution shown on every result: "Source: EUR-Lex / Cellar, Publications Office of the European
Union (https://eur-lex.europa.eu). © European Union."

## Legal status of the texts

- Only the electronic edition of the Official Journal is authentic and produces legal effects
  (Regulation (EU) No 216/2013, art 1(2); it entered into force on 1 July 2013 under its art 5,
  "the first day of the fourth calendar month following its adoption" on 7 March 2013 - both read
  from the regulation itself in Cellar). Before that, the printed OJ.
- The Formex 4 and XHTML files ingested are the publisher's renditions of the OJ text, not the
  signed authentic edition: `text_origin: official_conversion` (limitation `NOT_AUTHENTICATED`),
  and the source is `official_non_authentic` (`SOURCE_NOT_AUTHENTIC`).
- All language versions are equally authentic (TEU, art 55(1), as ingested), so OJ texts are
  `authoritative: true` and hits show `EQUALLY_AUTHENTIC_VERSIONS` with the parallel versions.
- The TEU and TFEU are the 2016 **consolidated versions** (OJ C 202, 7.6.2016), documentation
  produced by the publisher: `official_revised`, not authoritative (limitation `REVISED_TEXT`).
- Consolidated versions of acts are **not** ingested; acts are the original OJ texts.

## The whole collection of acts in force (widened 2026-10-15)

**Listing.** `python -m scripts.build_eu_manifest` asks the SPARQL endpoint (the service the licence statement covers;
`op.europa.eu/robots.txt` allows it), per year of the act, for (1) works with `cdm:resource_legal_in-force` true and a
CELEX number `3YYYY[RLD]NNNN` (sector 3 regulations, directives, decisions; corrigenda such as `32016R0679R(02)` are not
acts) that have a Formex 4 file in English, and (2) their consolidated versions. In force in Cellar: 35,085 acts (decisions
17,515, regulations 16,265, directives 1,305; implementing and delegated acts included); **26,875 have an English Formex 4
file** (14,344 regulations, 971 directives, 11,560 decisions); the other ~8,200 are not listed.

**Consolidated versions.** Cellar does offer them as distributions: a consolidated text is a work of its own (CELEX
`0YYYYTNNNN-YYYYMMDD`, type `CONS_TEXT`, `cdm:act_consolidated_based_on_resource_legal` the base act) with Formex 4
(`CONS.ACT`) and XHTML manifestations in all languages. 5,751 acts in force have consolidated versions (37,267 versions in all);
**2,864 have one with an English Formex 4 file that applies today** - for each, the latest is listed *instead of* the act
as adopted (it is the text in force), the other 24,011 acts as adopted. `INFO.CONSLEG` gives `START.DATE`, `END.DATE`
(99999999 = open) and the consolidation date. The parser (1.1.0) reads `CONS.ACT` with the same Formex reading as an act; the
GDPR's consolidated text has the same 856 identifiers as the Official Journal text, 826 with identical wording and 30 with the
corrections of the corrigenda (e.g. art. 37(1)(c) "or" for "and"). A consolidated version is a **whole-instrument snapshot of
the base act's instrument** (`/eu/reg/2016/679`, expression `.../eng@2016-05-04`; `revision_snapshot`, `point_in_time`,
`official_revised`, not authentic): every provision runs from `START.DATE` to `END.DATE`, `currency_date` is the consolidation
date, the base act's entry-into-force and application dates are kept as events, and the base act's list of changes is not
applied (the amendments are in the text). Earlier dated versions are not ingested (`--all-consolidated-versions` lists them:
~6.5 times as many documents; the snapshot machinery of `ulis/ingestion/snapshots.py` would then chain them oldest first).

**Connector.** `sparql_batch: 20` asks the three queries for 20 works at once (`VALUES`); each work's bundle holds only its
rows, as a part that records `derived_from_sha256` of the full answer and the `selection`. A work may list its own `languages`
(default `[en]`). A batch the endpoint answers with HTTP 5xx is split in halves; a missing language file, a 404 and a work
without metadata go to `fetch_errors`; a refusal stops the run. The parser also reads enacting terms without a numbered article
(one provision `terms`, cited by the instrument alone) and several acts under one CELEX (the others follow the first, their
title as context); an act with no operative text raises `ParseSkipped`.

**Scale.** 26,878 works, about **60,200 requests at 5 s = 84 h** (a uniform random sample of 100 works took 224 requests, 2.24
per work, 1,115 s). The sample: 96 parsed after the two fixes above (4 failed before: 2 unnumbered enacting terms, 2 with two
acts), 7+2+2 consolidated and 85 as adopted; **22.9 provision versions per work** (median 4, max 388), bundle 49 KB
(median 28 KB, max 345 KB). Extrapolated: **~615,000 provision versions, ~2 GB of PostgreSQL, ~1.3 GB of raw bundles**.

**Slice result.** 108 works (the 100-work random sample, the core acts and the Treaties) gave 124 documents, 108 instruments, 15,305 provision versions and 13,626 passages, 0 ingest errors; `verify-sources` 124/124, database 61 MB. Searches on it: GDPR art. 6(1)(a) in English and French, AI Act art. 9, Consumer Rights Directive art. 16b (consolidated), Rome I art. 3(1) and GDPR art. 17(1) in German. Full suite of the widening branch (Spain, Italy, EU, Argentina): 1,309 passed; ruff and mypy clean.

## What is ingested (the pilot instruments)

| Instrument | CELEX | Type | Format | Provisions | Validity used |
|---|---|---|---|---|---|
| Treaty on European Union (consolidated 2016) | 12016M/TXT | `founding_treaty` | XHTML | 234 | attested 2016-06-07 |
| Treaty on the Functioning of the EU (consolidated 2016) | 12016E/TXT | `founding_treaty` | XHTML | 1,123 | attested 2016-06-07 |
| Charter of Fundamental Rights (OJ C 202, 2016) | 12016P/TXT | `charter` | Formex 4 | 121 | from 2009-12-01 (entry into force) |
| Regulation (EU) 2016/679 (GDPR) | 32016R0679 | `regulation` | Formex 4 | 856 | from 2018-05-25 (application; in force 2016-05-24) |
| Regulation (EC) No 593/2008 (Rome I) | 32008R0593 | `regulation` | Formex 4 | 137 | from 2009-12-17 (application; in force 2008-07-24) |
| Regulation (EU) No 1215/2012 (Brussels Ia) | 32012R1215 | `regulation` | Formex 4 (TIFF form images omitted) | 323 | from 2015-01-10 (application; in force 2013-01-09) |
| Directive 2011/83/EU (consumer rights) | 32011L0083 | `directive` | Formex 4 | 204 | from 2011-12-12 (entry into force) |
| Regulation (EU) 2024/1689 (AI Act) | 32024R1689 | `regulation` | Formex 4 zip (act by act) | 1,097 (995 with a version) | from 2026-08-02 (application; in force 2024-08-01) |

Each in English, French and German (24 language versions). Indexed units: articles, numbered
paragraphs, points and subpoints, later subparagraphs that carry points, and annexes (each annex
as one unit). Recitals, citations, final formulas, signatures, Treaty protocols, annexes,
declarations and tables of equivalences are not indexed. Footnotes and the Treaties' editorial
"(ex Article 2 TEU)" references are kept in `note`, never in the text.

Identifiers are language-neutral: work URIs from the ELI (`/eu/reg/2016/679`,
`/eu/treaty/tfeu_2016`), eIds from the Formex identifiers (`art_6__para_1__point_a`,
`art_113__subpara_3__point_a`, `anx_III`), identical in all three languages (checked for every
instrument), so the language versions group as parallel authentic versions.

## Dates and versions

All dates come from the Publications Office metadata (SPARQL), whose annotations say what each
date is (`EV` entry into force, `MA` application, `MA/PART` partial application; labels checked in
the Publications Office authority tables fd_335, fd_370 and fd_375):

- **Validity (`valid_from`) is the act's general date of application** when the metadata records
  one, otherwise its entry into force. Rationale: ULIS answers "what law applied on date D"; the
  GDPR was in force from 24 May 2016 but applied only from 25 May 2018, so on 2017-06-01 a lookup
  of art 6(1)(a) says `not_yet_in_force`. Entry into force, application, partial application and
  transposition dates are all kept as lifecycle events and in each provision's note.
- **Partial application** dates (Rome I art 26 from 2009-06-17; Brussels Ia arts 75-76 from
  2014-01-10; AI Act art 113(a)-(c)) are only noted: the metadata does not say which provisions
  they concern, and ULIS does not derive it from the text. So, for example, AI Act art 5 is shown
  as applying from 2026-08-02 although art 113(a) applies Chapters I and II from 2025-02-02.
- **Directives** are `instrument_type: directive`, and each provision's note states that a
  directive binds Member States as to the result and that national transposing measures are not
  indexed (TFEU, art 288); the transposition deadline is an event.
- **Consolidated Treaties** have `validity_basis: attested_at_edition`: no start date, currency
  date 2016-06-07 (publication), authority "applicability unverified", limitations
  `ATTESTED_ONLY_AT` and, for later dates, `AS_OF_AFTER_CURRENCY`.
- **Amendments and repeals** recorded in the metadata (amending act, role - replacement, addition,
  deletion, partial repeal - modified location and start of validity) are listed as outstanding
  changes, linked to the provision when the location maps to an eId. They are never applied. When
  a located change replaces, deletes, repeals or inserts into a provision, the original wording of
  that provision (and of the containers whose composite text includes it; for replacements also
  its subdivisions) ends on the change's start of validity (`valid_to`, basis explicit) with a note
  that the later wording is not indexed. If that date is on or before the provision's own
  `valid_from`, the original wording never applied and no version is recorded. Results:
  Directive 2011/83/EU - 56 provisions ended (amended by Directives (EU) 2015/2302, 2019/2161,
  2023/2673, 2024/825 and partly repealed by 2019/771); Brussels Ia - annexes I and II ended
  2015-02-26 (Delegated Regulation (EU) 2015/281); AI Act - 102 provisions per language have no
  version because Regulation (EU) 2026/1744 changed them with effect from 2026-07-27, before the
  general date of application.
- **Corrigenda** are listed as instrument-level outstanding changes only for the language versions
  they correct (annotation `language_list`; listed for all languages when the metadata gives none).
- `currency_date` is not set for OJ texts (`CURRENCY_UNKNOWN`): an original text is not "up to date
  to" any date; the amendment list reflects the metadata on 2026-10-14.

## Citations (module `eu`)

| Language | Example |
|---|---|
| en | `Regulation (EU) 2016/679, art 6(1)(a)`; `Regulation (EC) No 593/2008, art 4(1)(a)`; `Directive 2011/83/EU, art 2(1)`; `TFEU, art 101(1)(a)`; `Charter of Fundamental Rights of the European Union, art 8(1)`; `Regulation (EU) 2024/1689, Annex III` |
| fr | `Règlement (UE) 2016/679, art. 6, par. 1, point a)`; `Règlement (CE) n° 593/2008, art. 4, par. 1, point a)`; `Directive 2011/83/UE, art. 2, point 1)`; `TFUE, art. 101, par. 1, point a)` |
| de | `Verordnung (EU) 2016/679, Art. 6 Abs. 1 Buchst. a`; `Verordnung (EG) Nr. 593/2008, Art. 4 Abs. 1 Buchst. a`; `Richtlinie 2011/83/EU, Art. 2 Nr. 1`; `AEUV, Art. 101 Abs. 1 Buchst. a` |

Rules: acts numbered from 2015 `Type (dom) year/number`; earlier regulations `Type (dom) No
number/year`; earlier directives and decisions `year/number/dom` (two-digit year before 1999).
The domain (EU/EC/EEC) comes from the Formex act number (or, in Formex 6 files, from the title).
Points: letters `(a)`/`point a)`/`Buchst. a`, numbers `(1)`/`point 1)`/`Nr. 1`, subpoints
`(i)`/`i)`/`Ziff. i`, later subparagraphs `, subpara 2, point (b)`/`, al. 2, point b)`/
`UAbs. 2 Buchst. b`. Other EU languages fall back to the English pattern with the instrument's
title in that language. Unit-tested against the titles printed in each language.

## Known gaps

See `coverage.known_gaps` in the profile. In short: acts in force with an English Formex 4 file only (26,875 of 35,085);
English only except the Treaties, the Charter and five core acts; acts without a consolidated version are
original OJ texts only (amended wording not indexed), acts with one have only its latest version, with a snapshot date
and no provision dates;
consolidated Treaties attested only at 2016-06-07, without protocols and declarations;
partial application dates not mapped to provisions; AI Act provisions changed before its general
application have no version; no recitals; case law only as the separate selection in `eu-case-law.md`, no international agreements, delegated or
implementing acts or national transposition; EU membership and the effect of EU law inside
Member States are not modelled (left for the core scoping change); the EUR-Lex legal notice could
not be read automatically.

## Results (2026-10-14)

Dev database `ulis_eu`: `migrate`, `sync-catalog` (16 jurisdictions, 8 sources, 4 concepts),
`ingest eu-eur-lex`:

```
documents_seen 24, documents_new 24, instruments_created 8, instrument_versions_created 24,
provision_versions_created 11979, passages_indexed 10707, conflicts 0, errors 0
```

A second `ingest` found 24 documents unchanged and created nothing. `verify-sources`: 24 of 24
source documents pass (3,993 units per language re-derived from the stored raw bundles).

Checks in the worktree: `ruff check` and `ruff format --check` clean, `mypy ulis` clean (55 files),
full test suite against `ulis_test_eu`: **136 passed** (16 in `tests/test_eu_cellar.py`, 4 of them
PostgreSQL tests). Fixtures: 42 unmodified Cellar responses (TEU, Charter, Rome I, Directive
2011/83/EU in three languages), gzip-compressed, 635 KB, replayed offline by the connector.

Sample searches (`jurisdiction=EU`, default date 2026-10-14):

| Query | Top result | Authority / notes |
|---|---|---|
| "processing shall be lawful only if" (en) | Regulation (EU) 2016/679, art 6(1) | binding regulation, valid from 2018-05-25; `CURRENCY_UNKNOWN`, corrigendum 32016R0679R(02) listed |
| "droit à la protection des données à caractère personnel" (fr) | Charte des droits fondamentaux de l'Union européenne, art. 8, par. 1 | binding, from 2009-12-01; then Règlement (UE) 2016/679, art. 85 |
| "Vereinbarungen zwischen Unternehmen Binnenmarkt unvereinbar" (de) | AEUV, Art. 101 Abs. 1 | applicability unverified; `ATTESTED_ONLY_AT`, `AS_OF_AFTER_CURRENCY` |
| "consumer right of withdrawal period of 14 days" (en) | Directive 2011/83/EU, art 9(1) | binding legislation with directive note; `OUTSTANDING_CHANGES` |
| "subliminal techniques beyond a person's consciousness" (en) | Regulation (EU) 2024/1689, art 5(1)(a) | valid from 2026-08-02; outstanding changes by Regulation (EU) 2026/1744 |
| "lawfulness of processing personal data", as of 2017-06-01 (en) | Charter, art 8(2); TFEU, art 16(2) | GDPR correctly absent (applies from 2018-05-25) |

Point-in-time lookups: GDPR art 6(1)(a) on 2017-06-01 `not_yet_in_force`, on 2018-05-25
`in_force` (German citation `Verordnung (EU) 2016/679, Art. 6 Abs. 1 Buchst. a`); Directive
2011/83/EU art 6(1)(h) on 2024-01-01 `no_version` (wording ended 2023-12-18, replaced by
Directive (EU) 2023/2673); TFEU art 101(1) `in_force_date_unverified`.

## Shared files touched

- `ulis/ingestion/pipeline.py`: registered the `eu_cellar` parser, connector and ELI `resolve_uri`.
- `ulis/jurisdictions/modules/__init__.py`: registered `EuModule`.
- `tests/test_catalog.py`: `EU` profile and approved `eu-eur-lex` source.
- `tests/test_ingestion.py`: the licence-gate test used `eu-eur-lex` as its example of a planned
  source; it now uses `gb-find-case-law` (still not approved).

No core semantics, schema or migrations were changed. `ulis/languages.py` already maps en, fr
and de to PostgreSQL configurations.
