# Court decisions: survey of candidate publishers (2026-10-14)

Scope: courts of the jurisdictions whose legislation is already in ULIS. For every publisher the robots.txt and the terms
were read before anything else was fetched (user agent `ULIS-research-bot/0.1`, requests several seconds apart; a refusal
stopped the work on that host). Full findings with URLs are in the `licence`/header comments of each source YAML in
`config/sources/`. ULIS works around no block: no other user agent, proxy, headless browser, mirror or CAPTCHA solving.

## Result

| Country | Publisher | Outcome | Source id |
|---|---|---|---|
| SE | Domstolsverket "Sök rättspraxis" open data API (CC0 on Sveriges dataportal) | **Ingested** (see [se-case-law.md](se-case-law.md)) | `se-rattspraxis` |
| FI | Finlex open data: decisions of the Chancellor of Justice and the two Ombudsmen (CC BY 4.0, bulk zips) | **Ingested**, but these are not courts (see [fi-decisions.md](fi-decisions.md)) | `fi-finlex-decisions` |
| GB | UK Supreme Court's own site, Open Government Licence | **Ingested** (PDF judgments; see [gb-case-law.md](gb-case-law.md)) | `gb-uksc` |
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
