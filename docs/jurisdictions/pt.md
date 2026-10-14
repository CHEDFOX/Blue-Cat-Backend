# Portugal - Diário da República (planned, nothing ingested)

Profile `PT` (draft, unverified, corpus `none`), source `pt-diario-da-republica` (**planned**), module `pt`
(citation formatting only). No connector and no parser exist, on purpose: no machine-readable form of the
Diário da República with clear reuse terms could be found, so ULIS stopped before fetching any law text.

## What was checked (2026-10-14)

| Check | Result |
|---|---|
| `https://diariodarepublica.pt/dr/home`, `/dr/detalhe/...`, `/sitemap.xml` | every path answers HTTP 200 with the same 2.3 KB HTML shell of a JavaScript (OutSystems) application (`window.OutSystemsApp = { basePath: '/dr/' }`); no legal text, no sitemap |
| `robots.txt` of `diariodarepublica.pt` and `dre.pt` | no rules: the answer is the application's HTML error page (`dre.pt` redirects to `diariodarepublica.pt/dr/error`) |
| open-data portal `dados.gov.pt` (API `/api/1/datasets/`, `/organizations/`) | no dataset and no organisation of INCM (Imprensa Nacional-Casa da Moeda) or of the DRE for "diário da república", "legislação", "INCM", "Imprensa Nacional Casa da Moeda", "dre.pt", "constituição código civil"; the hits are municipal and environmental datasets that merely mention the Diário da República. Its `robots.txt` answers HTTP 500 |
| DRE terms of use / legal notice (INCM reuse conditions) | only inside the JavaScript application, not readable; **unread** |
| ELI, XML or JSON dumps, consolidated-code downloads | none found; the DRE's own text is loaded by the application from undocumented screen-service calls, which ULIS does not use |
| second-hand | a web search surfaced an article (apcmc.pt) saying that a 2016 decree-law on free and universal access to the Diário da República foresaw consolidated legislation and content in reusable (open data) formats; not verified, and it states no licence |

Conclusion: JavaScript-only pages and unknown terms, so no scraping and no mirror (the rules of the brief).
The source record has `status: planned`, `licence.review_status: pending_review`, every permission
`unknown`; `licence_gate` refuses it (`tests/test_pt_planned.py`).

## What exists

* `config/jurisdictions/pt.yaml`: research-note profile (civil law, unitary, courts, instrument types), `draft_unverified`.
* `ulis/jurisdictions/modules/pt.py`: citations in Portuguese practice, tested without any data:
  `artigo 483.º do Código Civil`, `artigo 483.º, n.º 1, do Código Civil`, `artigo 1.º-A do Código Penal`,
  `artigo 5.º, n.º 1, alínea a), da Lei n.º 58/2019`, `artigo 13.º da Constituição da República Portuguesa`.
* Tests: `tests/test_pt_planned.py` (4 tests): gate refusal, nothing registered, profile stub, citations.

## Known gaps

Everything: the Constituição, Código Civil, Código Penal, Código do Trabalho, Código de Processo Civil, Código
de Processo Penal, Lei n.º 58/2019 (GDPR implementation), Código das Sociedades Comerciais and all other law,
and case law, are not ingested.

## To unblock

A person should ask INCM whether an open-data export or API of the Diário da República and of the consolidated
legislation exists and under which licence, and read the DRE legal notice. If a bulk XML/JSON dump with stated
reuse terms exists, add a manifest-based connector and parser as for Luxembourg and activate the source.
