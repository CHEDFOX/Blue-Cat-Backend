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
| `gb-legislation-gov-uk` | planned | Open Government Licence v3.0 (to confirm) | Refused | Point-in-time XML and extent data; outstanding effects must be surfaced |
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

**Environment note (2026-10-14):** the build container's network policy blocked the official
portals tried (legislation.gov.uk, govinfo.gov, uscode.house.gov, eur-lex.europa.eu,
publications.europa.eu, indiacode.nic.in). To build real connectors with recorded fixtures,
those hosts must be allowed in the environment's network settings.
