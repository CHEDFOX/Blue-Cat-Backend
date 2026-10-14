# Source discovery and licensing strategy

> Licence statements about real sources in this repository are **preliminary research
> notes**. They are recorded with `review_status: pending_review` and must be confirmed by
> counsel. Nothing here is legal advice about reuse rights.

## 1. Principles

1. **Public is not permitted.** Being able to fetch a page grants no right to bulk download,
   reproduce, redistribute or process it automatically. Every source has a descriptor with
   an explicit licence assessment, and the pipeline refuses unapproved sources
   (`licence_gate`, tested in `tests/test_catalog.py` and `tests/test_ingestion.py`).
2. **Authoritative before convenient.** Prefer official, ideally authentic, publications.
   Record each source's `authority` (official authentic / official non-authentic /
   authoritative secondary / unofficial / fictional). Responses say so
   (`SOURCE_NOT_AUTHENTIC`).
3. **APIs and authorised bulk feeds before page retrieval.** Page-level retrieval is used
   only where terms permit it, at published rate limits, identified by a contact user agent.
4. **Provenance is permanent.** Raw bytes are kept (where the licence permits retention)
   with SHA-256, retrieval time and the licence in force at retrieval.
5. **Configurable connectors, not hard-coded sites.** A connector is selected by the
   descriptor's `connector` field. Adding a country should mean a descriptor, a connector
   (if the access method is new) and a parser (if the format is new). The core never changes.

## 2. Source priority

| Priority | Source type | Examples (to be confirmed per jurisdiction) |
|---|---|---|
| 1 | Official legislation portals with structured data and point-in-time versions | legislation.gov.uk (CLML/AKN), US Code (USLM), EUR-Lex/Cellar (Formex/AKN4EU) |
| 2 | Official gazettes (authentic publication of record) | National gazettes; EU Official Journal |
| 3 | Official court and tribunal databases | Supreme/constitutional court sites; Find Case Law (UK) |
| 4 | Official treaty repositories | UN Treaty Collection; regional organisation treaty offices |
| 5 | International organisations and recognised institutions | Legal information institutes (e.g. members of the Free Access to Law Movement), subject to their terms |
| 6 | Open-data legal repositories | Government open-data portals; openly licensed datasets |
| 7 | Licensed commercial providers | Via contract, for headnotes, citators and historical coverage |

## 3. Source descriptor (`config/sources/*.yaml`)

Recorded for every source: `jurisdictions`, `document_kinds`, `languages`, `coverage`,
`access` (method, endpoint, authentication), `update_frequency`, `rate_limits`,
`licence` (id, URL, review status, reviewer and date, and separate permissions for
redistribution, bulk download, automated processing (including ML), commercial use, plus
attribution text and conditions), `provenance`, `known_limitations`, `status`
(active / planned / suspended / rejected), `connector`, `parser`.

The licence gate admits a source only when **all** of these hold: status `active`; licence
review `approved` or `not_required`; bulk download and automated processing allowed (with
or without conditions); connector and parser configured.

## 4. Current register

| Source | Status | Licence (preliminary) | Gate result | Notes |
|---|---|---|---|---|
| `ulis-fictional-sample` | active | CC0-1.0, self-authored | **Admitted** | Fictional; pinned by SHA-256 manifest |
| `us-gpo-uslm-samples` | active | US government work (17 U.S.C. 105) + CC0 waiver, stated in the repository `LICENSE.md` and each file's `dc:rights` | **Admitted** | Real; GitHub `usgpo/uslm` pinned to one commit, files pinned by SHA-256; converted XML, not authenticated |
| `gb-legislation-gov-uk` | active | Open Government Licence v3.0 (stated in the site footer; fair-use policy encourages re-use) | **Admitted** | Akoma Ntoso with point-in-time versions, extent and status; fetched politely (identifying User-Agent, robots `Crawl-delay: 5`, cache); outstanding effects reported |
| `ca-justice-laws-xml` | active | Open Government Licence – Canada (repository `LICENSE.md`) and the Reproduction of Federal Law Order (SI/97-5) | **Admitted** | Real; GitHub `justicecanada/laws-lois-xml` pinned to one commit, sparse checkout of the listed files, each pinned by SHA-256; English and French; every result says the reproduction is not an official version |
| `de-gesetze-im-internet` | active | Free use and reuse stated on the site; laws are not protected by copyright (§ 5 Abs. 1 UrhG) | **Admitted** | XML downloads intended for automated processing (site notes, item 6); consolidations, not the authentic Bundesgesetzblatt; English translations not ingested (consent required) |
| `eu-eur-lex` | active | Commission Decision 2011/833/EU (stated for the Cellar dataset); EUR-Lex itself answered with a bot challenge and was not used | **Admitted** | Publications Office Cellar REST/SPARQL; OJ texts (authentic only in the e-OJ since 2013) and consolidated Treaties (documentation); the EUR-Lex legal notice should be read by a person |
| `es-boe-consolidada` | active | BOE reuse licence (Law 37/2007): attribution "Basado en datos de la Agencia Estatal Boletín Oficial del Estado" | **Admitted** | Open-data API with every consolidated version; texts are "meramente informativo" |
| `jp-egov-laws` | active | Public Data License 1.0 (compatible with CC BY 4.0): attribution and a statement that data was processed | **Admitted** | e-Gov API v2; the Official Gazette (官報) prevails |
| `ch-fedlex` | active | Enactments not protected by copyright (Art. 5 URG); free download for reuse (Art. 19 PublA); notice required by Art. 49 PublO shown on every result | **Admitted** | Akoma Ntoso from www.fedlex.admin.ch/filestore (allowed by that host's robots.txt; the data host's robots.txt disallows its own /filestore/ path, which ULIS does not use) - to confirm with the Federal Chancellery |
| `gb-find-case-law` | planned | Open Justice Licence (to confirm) | Refused | Our understanding: computational analysis requires a separate licence. Recorded as `automated_processing: not_allowed` until held |
| `us-uscode-house` | planned | US government work, 17 U.S.C. 105 (to confirm) | Refused | Non-positive-law titles are prima facie evidence (1 U.S.C. 204) |
| `eu-eur-lex` | planned | Commission Decision 2011/833/EU reuse (to confirm) | Refused | Only the e-OJ is authentic; consolidations are documentation |
| `in-india-code` | planned | **Unknown** | Refused | Review site terms and Copyright Act 1957 s.52(1)(q) (reproduction of Acts with commentary; judgments treated differently) |

## 5. Discovery process for a new jurisdiction

1. **Inventory**: legislature(s), gazette, consolidation service, courts and tribunals,
   treaty office, and whether each publishes authentic or merely official text.
2. **Assess**: coverage (years, levels, languages), format (XML/AKN, HTML, PDF/scans),
   point-in-time support, update cadence, API limits.
3. **Licence review**: terms of use, statutory exceptions, database rights, personal-data
   restrictions in judgments (anonymisation duties), and AI/ML processing terms. Record the
   result in the descriptor and get sign-off.
4. **Pilot**: record a small fixture set (with permission), build connector + parser, and
   measure parse accuracy against a hand-checked sample.
5. **Activate** the descriptor only after steps 1–4. Monitor terms for changes; a change
   sets the source to `suspended` until it is re-reviewed.

## 6. Recommendation for the first real jurisdiction

From a purely technical and licensing-clarity standpoint, a portal with structured
point-in-time data and a published open licence is the lowest-risk pilot. legislation.gov.uk
is the best-documented example. India is an important target, but the reuse terms for bulk
legislation text and court sources are not yet established and must be reviewed first. The
choice is a product and legal decision for the team. The platform supports either.

**Second real source (2026-10-14):** legislation.gov.uk, after network access was opened.
Licence and fair-use terms were read from the site itself before activation. The connector
fetches each Act's revised text at every date in its lifecycle, so the whole version history
since the start of the revised data is indexed (1 February 1991 for older Acts; enactment for
later ones). Earlier dates are reported as `history_not_indexed`, never as "not in force".
Known gaps: Welsh-language texts of Welsh legislation are not ingested yet; editorial
annotations are kept as notes, never as law text; unapplied effects are reported as
outstanding changes.

**Third real source (2026-10-14):** Canada's consolidated federal Acts, from the Department
of Justice's public XML repository. The Reproduction of Federal Law Order allows anyone to
reproduce federal enactments and consolidations without permission, with due diligence for
accuracy and without presenting the reproduction as an official version; ULIS re-derives
every text from the pinned raw files and states on every result that it is not an official
version. Only the current consolidation is in the repository, so earlier wordings are
reported as a history gap (the Justice Laws website has earlier point-in-time versions;
not fetched yet).

**India (2026-10-14):** indiacode.nic.in answers our identified crawler with HTTP 403 (Access
Denied). We do not work around blocks. India stays at "no corpus" until the publisher grants
access or offers a bulk-data channel, and the licence review is complete.

**First real source (2026-10-14):** US federal law from the GPO's public GitHub repository
(the only official publisher reachable from the build container), via the `git_snapshot`
connector. **Environment note:** the container's network policy blocked the official
portals tried (legislation.gov.uk, govinfo.gov, uscode.house.gov, eur-lex.europa.eu,
publications.europa.eu, indiacode.nic.in). To build real connectors with recorded fixtures,
those hosts must be allowed in the environment's network settings.
