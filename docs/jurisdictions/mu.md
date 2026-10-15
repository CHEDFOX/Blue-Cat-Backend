# Mauritius (MU): Supreme Court Judgment Search - planned, blocked by robots.txt

Status: **nothing ingested.** The court's terms allow reproduction of its judgments, but `robots.txt` closes the whole
site to robots. The connector, parser, module, profile stub and tests are written and wait for the court's written
permission. Research aid, not legal advice.

| | |
|---|---|
| Source / manifest | `config/sources/mu-supreme-court-judgments.yaml` (**planned**), `config/manifests/mu-supreme-court-judgments.yaml` (years 2020-2026, the whole database) |
| Connector / parser | `ulis/sources/connectors/mu_supreme_court.py` (`mu_supreme_court`), `ulis/sources/parsers/mu_judgments.py` (`mu_supreme_court_bundle` 1.0.0) |
| Profile / citations | `config/jurisdictions/mu.yaml` (0.1.0, `corpus_status: none`, courts `MU-SC`, `MU-IC`, `MU-IND`, `MU-DC`, `MU-CC`), `ulis/jurisdictions/modules/mu.py` |
| Tests | `tests/test_mu_supreme_court.py` (36), `tests/test_africa_asia_planned.py`, fixtures in `tests/fixtures/mu_supreme_court/` |

## Source and licence (read 2026-10-14)

| Page | Finding |
|---|---|
| [robots.txt](https://supremecourt.govmu.org/robots.txt) | Standard Drupal file; `User-agent: *` allows only stylesheets, scripts and images under `/core/` and `/profiles/`, disallows `/admin/`, `/search/`, `/user/` ... and ends with **`Disallow: /`**. No crawl delay, no AI-crawler rules. |
| [Terms and Conditions](https://supremecourt.govmu.org/basic-page/4527) | Clause 1: the decisions are "prepared for convenience of reference only"; the official versions are the Mauritius Reports (M.R.) and the Supreme Court Judgments of Mauritius (S.C.J). Clause 3: judgments are the Supreme Court's property and copyright protected, but "may be reproduced free of charge in any format or medium provided it is reproduced accurately and not used in a misleading context; and the source of the material must be identified and the copyright status acknowledged". |
| [Judgment Search](https://supremecourt.govmu.org/judgment-search) | About 12,150 decisions (2020-01-07 to 2026-10-14), 10 per page, as PDFs at `/system/files/judgment/<node id>/<file>.pdf`; filters by year and court. District courts, Intermediate, Industrial and Children's Courts, Bail and Remand Court, Rodrigues; no Supreme Court "SCJ" decision seen. No API or feed. |

Conclusion: the terms and robots.txt say different things about automated access; ULIS follows the stricter one and
does not fetch. The descriptor records `automated_processing: not_allowed` and a draft request in `licence.notes`
(ask whether clause 3 covers automated retrieval and indexing at one request per 5 s and whether robots.txt can allow
the bot). About 25 pages were fetched by hand before the last robots.txt line was noticed (see the survey); the
fixtures come from them and are labelled.

## What is built (tested on fixtures, not run against the site)

- Connector: for each year reads `judgment-search?from_year=Y&to_year=Y&page=N` until a page is empty (the date
  ordering parameters of the site return HTTP 500, so the default newest-first order is used and duplicates are dropped by
  node id), downloads each PDF, extracts the text layer with `pdftotext` (poppler-utils; needed on the server) and yields
  one JSON bundle per decision (listing row, PDF URL/SHA-256/size, text). Checks every URL against robots.txt (the real
  file makes it refuse), 5 s delay, caches, collects per-document problems in `fetch_errors`, stops on 401/403/429/451
  and after 25 failures in a row.
- Parser: header (up to the `JUDGMENT`/`RULING` line), paragraphs at line-start `12.` or `[12]` markers (printed
  numbers kept, repeats and gaps included; quoted lists and implausible numbers are not new paragraphs), unnumbered
  decisions split at blank lines, no paragraph above about 4,000 characters; page-number lines dropped; court from the
  officer's title in the listing; neutral reference = the document number (`2026 INT 287`), date = date delivered;
  language by word count (English or French); `authoritative: false`, `text_origin: official_conversion`.
- Citations: `Turquoise Properties Ltd v Zoobair and Osman Properties Ltd [2026 INT 287], para 5` (convention to verify).

## How dates and versions work

One version per decision, dated by the date delivered (`dd/mm/yyyy` in the listing); no other history. A changed PDF
under the same document number would be reported as an error, not silently replaced.

## Known gaps

No legislation (the site lists Acts as PDFs under the same terms page but the permission names judgments only;
MauritiusLII forbids scraping); no Supreme Court decisions; 2020 onwards only; text is a PDF text layer (scans
skipped, layout lost); no docket numbers, citation links, headnotes or later history; courts classified coarsely.
Names of parties and accused persons are as the court published them.

## Numbers

No ingestion, no `verify-sources` run. Tests: 36 in `test_mu_supreme_court.py` (2 need `pdftotext`), 31 in
`test_africa_asia_planned.py`; see the final report for the full suite. If permission is given: add `poppler-utils`
to `deploy/Dockerfile`, set `status: active` and `review_status: approved` with the reply, run `ingest` in yearly chunks
(about 17 hours of network time for everything), then `verify-sources`.
