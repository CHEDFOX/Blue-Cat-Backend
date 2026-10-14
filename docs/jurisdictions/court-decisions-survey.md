# Court decisions: survey of candidate publishers (2026-10-14)

Scope: courts of the jurisdictions whose legislation is already in ULIS. For every publisher the robots.txt and the terms
were read before anything else was fetched (user agent `ULIS-research-bot/0.1`, requests several seconds apart; a refusal
stopped the work on that host). Full findings with URLs are in the `licence`/header comments of each source YAML in
`config/sources/`. ULIS works around no block: no other user agent, proxy, headless browser, mirror or CAPTCHA solving.

## Result

| Country | Publisher | Outcome | Source id |
|---|---|---|---|
| SE | Domstolsverket "Sök rättspraxis" open data API (CC0 on Sveriges dataportal) | **Ingested**: the case reports (referat), 16,505 in the manifest; see [se-case-law.md](se-case-law.md) | `se-rattspraxis` |
| FI | Finlex open data: decisions of the Data Protection Ombudsman (CC BY 4.0, bulk zip); the Chancellor of Justice and Parliamentary Ombudsman decisions are PDFs | **Ingested**, but these are not courts (see [fi-decisions.md](fi-decisions.md)) | `fi-finlex-dpo-decisions` |
| GB | UK Supreme Court's own site, Open Government Licence | **Ingested**: 126 judgments published as HTML (2021-2026); see [gb-case-law.md](gb-case-law.md) | `gb-uksc-judgments` |
| CA | Supreme Court of Canada (decisions.scc-csc.ca) and Federal Court (same platform) | Planned: terms favourable (Reproduction of Federal Law Order), but the Lexum platform answered with a CAPTCHA (403) on the fifth request | `ca-scc-decisions` |
| AU | High Court (hcourt.gov.au), Federal Court, AustLII | Planned: High Court drops the connection, Federal Court and AustLII show a Cloudflare challenge, even for robots.txt | `au-hca-judgments` |
| IE | Courts Service (www2.courts.ie) | Planned: re-use of judgments permitted, but robots.txt disallows `/acc/alfresco/` where the texts are | `ie-courts-judgments` |
| CH | Federal Supreme Court (bger.ch) | Planned: certificate chain incomplete, robots.txt and terms unreadable (verification is never disabled) | `ch-bger-decisions` |
| DK | Domsdatabasen | Planned: API only by permit ("anerkendelsesværdigt formål") | `dk-domsdatabasen` |
| FI | KKO precedents (korkeinoikeus.fi, Finlex) | Planned: not in Finlex open data; the Court's site reserves "all rights" beyond the Copyright Act | `fi-kko-precedents` |
| DE | rechtsprechung-im-internet.de; Bundesverfassungsgericht | Planned: first disallows all robots (already recorded); BVerfG's terms and decision search lie in disallowed paths | `de-rechtsprechung-im-internet`, `de-bverfg-decisions` |
| PL | SAOS | Planned: robots.txt disallows `/api/` | `pl-saos-judgments` |
| NZ | Courts of New Zealand | Planned: Crown copyright, personal or in-house use only | `nz-courts-judgments` |
| EE | Riigi Teataja court decisions | Unchanged: robots.txt blocks court decisions (see `ee-riigi-teataja`) | - |
| GB | Find Case Law (National Archives); BAILII | Unchanged: computational analysis needs a licence; BAILII's robots.txt disallows `/uk/`, `/ew/`, `/ie/`, `/scot/`, `/wales/`, `/eu/` | `gb-find-case-law` |
| CA | CanLII | Not used: `User-agent: *` / `Disallow: /` | - |

## Findings worth knowing

- Finlex's open data has **no court decisions**. The OpenAPI (v0.4.0) knows three `judgment` types (Chancellor of Justice,
  Data Protection Ombudsman, Parliamentary Ombudsman); guessed types for the Supreme Court answer 404. KKO/KHO decisions
  are only on the website, rendered by script.
- The Swedish API is the best structured source found: ECLI, case numbers, statute references with SFS numbers, NJA
  numbers and the full report as HTML; and an official OpenAPI file, CC0 in the data portal's metadata.
- Ireland and Canada both permit re-use in words but block robots in practice; permission requests belong in
  `docs/permission-requests.md` (not edited here).

## What was built and run (dev DB `ulis_courts`; test DB `ulis_test_courts`)

| Source | Ingested here | Verify | New tests |
|---|---|---|---|
| `se-rattspraxis` | 200 reports (newest 25 of 8 courts) of 16,505 in the committed manifest; 17,530 paragraphs | 200 pass | 16 |
| `fi-finlex-dpo-decisions` | whole package: 396 documents, 385 decisions, 23,446 paragraphs | 396 pass | 14 |
| `gb-uksc-judgments` | whole manifest: 126 judgments, 26,877 paragraphs | 126 pass | 13 |

`verify-sources` over the dev DB: 935 pass, 0 fail. Full suite 780 passed in 434 s; `ruff check`, `ruff format --check` and `mypy` clean.
No change to core semantics, the schema or `PoliteHttp`. Shared files touched (additive): `ulis/ingestion/pipeline.py`
(registrations), `tests/test_catalog.py` (approved set), the FI, SE and GB profiles and modules (courts, gaps, version bumps,
judgment citations and ext models). Not edited (as instructed): `docs/sources-and-licensing.md` and `docs/permission-requests.md`;
the lead should add rows for the planned sources above, in particular `ca-scc-decisions` (Deputy Registrar or Lexum),
`ie-courts-judgments` (Courts Service), `dk-domsdatabasen` (API application), `fi-kko-precedents`, `de-bverfg-decisions`,
`pl-saos-judgments`, `nz-courts-judgments`, `au-hca-judgments` and `ch-bger-decisions`.

Not done although open: UK Supreme Court judgments before 2021 (PDF only; needs a PDF text parser), the Privy Council
(`jcpc.uk`, same terms, 140 HTML judgments), Sweden's judgments and decisions (PDF attachments) and the Chancellor of Justice and
Parliamentary Ombudsman decisions in Finland (PDF).

