# Mauritius (MU): Supreme Court Judgment Search - planned, blocked by robots.txt

Status: **nothing ingested.** The court's terms allow reproduction of its judgments, but `robots.txt` closes the whole
site to robots. The source waits for the court's written permission. Research aid, not legal advice.

| | |
|---|---|
| Source | `config/sources/mu-supreme-court-judgments.yaml` (**planned**; the licence gate refuses it) |
| Profile / citations | `config/jurisdictions/mu.yaml` (0.1.0, `corpus_status: none`, courts `MU-SC`, `MU-IC`, `MU-IND`, `MU-DC`, `MU-CC`), `ulis/jurisdictions/modules/mu.py` |
| Tests | `tests/test_africa_asia_planned.py` (gate refusal) |

**Clean-up note.** About 25 pages of the site were fetched before the final `Disallow: /` line of robots.txt was
noticed. That was a mistake. The pages, the test fixtures, and the connector and parser built from them were deleted
afterwards; the findings below are the only thing kept. If the court gives written permission, build a connector then
(PDF judgments need `poppler-utils`, already in the Docker image).

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
