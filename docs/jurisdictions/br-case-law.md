# Brazil (BR): decisions of the Superior Tribunal de Justiça from its open data portal

Status: **draft unverified profile, first Brazilian court decisions.** Portuguese only; no Brazilian legislation is loaded (the
Planalto site did not answer, see [latin-america.md](latin-america.md)), so decisions are not linked to statutes. The committed
manifest covers the portal's whole file set (4 Jan 2021 to 8 Oct 2026); only a slice was ingested here. Not legal advice.

| | |
|---|---|
| Source / manifest | `config/sources/br-stj-dados-abertos.yaml` (active, approved), `config/manifests/br-stj-dados-abertos.yaml` (1,313 files) |
| Connector / parser | `ulis/sources/connectors/stj_dados_abertos.py`, `ulis/sources/parsers/stj_integras.py` (1.0.0), `scripts/build_stj_manifest.py` |
| Profile / citations | `config/jurisdictions/br.yaml` (0.1.0), `ulis/jurisdictions/modules/br.py`; search configuration `portuguese` (exists) |
| Tests | `tests/test_stj.py`, fixtures in `tests/fixtures/stj/` (verbatim members of the 2023-11-09 and 2026-10-07 files) |

## Source and licence (read 2026-10-15, robots.txt first)

- `robots.txt`: `User-agent: *`, `Disallow: /dataset/rate/`, `/revision/`, `/dataset/*/history`, **`/api/`**, `Crawl-Delay: 10`.
  The CKAN API is not used (the home page invites it, robots.txt forbids it); the dataset page and the resource download
  links match no rule. Every URL is checked against the rules by the connector.
- [Dataset page](https://dadosabertos.web.stj.jus.br/dataset/integras-de-decisoes-terminativas-e-acordaos-do-diario-da-justica):
  licence box "Creative Commons Atribuição" (opendefinition.org `cc-by`, Open Definition badge, no version); "íntegras e
  metadados das decisões terminativas e acórdãos publicados no Diário da Justiça": a ZIP of texts, a JSON of metadata and a CSV
  dictionary per period, updated daily; "Não estão disponíveis dados de processos em segredo de justiça."
- Conclusion: licence **approved** (CC BY: attribution to the STJ; version unstated, commercial use inferred, counsel review
  recommended). Not read: the "Plano de Dados Abertos" PDF, Lei 9.610/98 art. 8 IV, the LGPD. Personal data: the portal only
  excludes cases under secrecy of justice. The texts name parties and lawyers as the Court published them (the monocratic
  summary masks the party: `#{nome_da_parte}`); ULIS stores them as published and does not re-identify.
- Onboarding made 30 requests (about 280 MB), 10 s apart by `PoliteHttp` except one pair 2 s apart (two processes; see the YAML).

## What is ingested

One ZIP of texts plus one JSON of metadata per day since 2021-01-04: 1,309 daily files (1,305 days; four days of Feb 2021 were
uploaded twice and are listed as `ID` and `ID-b`) and four monthly files (Feb-Apr, Jul 2022): about 2,200 downloads, an estimated
13 GB and 2.5 million decisions, more than 6 hours at 10 s apart. Three days have metadata but no ZIP (not fetched); 11 days
list their metadata JSON twice (the first is used; the pair compared, 2025-09-12, was identical). The ZIPs checked from 2022-04 to
2023-11 contain their metadata (no second download). **From early 2026 the ZIPs hold only a handful of texts** (11 texts for
2,721 records on 2026-10-07, 119 on 2026-01-05, against 3,598 on 2025-12-01); records without text are not ingested (counted in
`connector.stats`). Update: `python scripts/build_stj_manifest.py`, then ingest again. Full run (resumable from `var/sources/`):
`python scripts/ingest_in_batches.py br-stj-dados-abertos --batch 5000` (`--metadata tipo=ACÓRDÃO,ACORDAO`: acórdãos only).

## How a decision is read

- **Raw document**: one JSON envelope per decision (verbatim metadata record(s) and verbatim text file; a ZIP holds hundreds),
  whose raw metadata names the ZIP/JSON URLs and SHA-256. A day uploaded twice gives identical bytes.
- **Fields**: court `BR-STJ`; docket and case name = `processo` ("REsp 2093032"); work URI `/br/stj/<SeqDocumento>`; `ext` keeps
  `numeroRegistro`, `tipoDocumento`, minister, `recurso`, `teor`, `descricaoMonocratica`, CNJ subject codes, receipt and
  distribution dates as published. Key spellings and dates (ISO or milliseconds, UTC date) vary between files; the
  placeholder 1900-01-01 is read as absent. **No judgment date exists**: the stored date is the DJe publication (`ext.date_kind`).
- **Paragraphs**: lines split at `<br>`/CR/LF, entities decoded, joined per section into units of at most 2,500 characters; a line
  that is exactly ACÓRDÃO, EMENTA, RELATÓRIO, VOTO(-VISTA/-VOGAL) or DECISÃO sets `part` (`acordao`, `ementa`, `relatorio`,
  `voto`, `decisao`) and is not text; the ementa usually appears twice. `num` is never set.

## Citations and authority

`STJ, REsp 2097990, decisão monocrática, Min. BENEDITO GONÇALVES, DJe de 9/11/2023`; acórdão: `STJ, AREsp 2297789, Rel. Min.
RAUL ARAÚJO, DJe de 9/11/2023`. The data has no state ("/SP") and no `j.` date, so none is shown. Every hit is
`persuasive_precedent` (jurisprudence constante, no formal vertical binding; the CPC art. 927 nuance is a profile note).

## Real run and tests (dev DB `ulis_br`)

`migrate`, `sync-catalog`, `scripts/ingest_in_batches.py ... --batch 5000` on a 9-entry slice: the daily files of 2021-01-04,
2022-05-02, 2023-11-09, 2024-06-12, 2025-10-01, 2026-01-05, 2026-03-11, 2026-10-07 and the April 2022 monthly file (metadata
JSONs of 2024-06-12, 2025-10-01, 2026-01-05, 2026-03-11 fetched through the connector). Connector: 62,237 metadata records, 54,253
with a text (7,451 of the 7,496 without are in the 2026 files). Result: 54,253 judgments (43,397 decisões, 10,856 acórdãos),
267,780 paragraphs = passages (decisao 169,659, voto 46,686, ementa 23,446, relatorio 16,635, acordao 10,881, voto_vista 426,
voto_vogal 28, text 19), no errors, 1,181 s (46/s). `verify-sources`: 54,253 pass, 0 fail (9 min 51 s). Database 1.96 GB
(passages 1.26 GB) plus 1.1 GB of raw envelopes: about 36 KB per decision, roughly 90 GB for 2.5 million decisions.
Searches (`BR`): "contribuição previdenciária patronal faltas justificadas" -> REsp 2097990; "dano moral inscrição indevida
cadastro de inadimplentes" -> REsp 1982697 (Min. Nancy Andrighi, DJe 6/4/2022); "habeas corpus prisão preventiva excesso de
prazo tráfico de drogas" -> HC 729537; "recuperação judicial crédito extraconcursal" -> CC 181489; "plano de saúde negativa de
cobertura tratamento" -> REsp 1981852. Tests: 55 in `tests/test_stj.py` (parser, citations, profile/source, robots rules,
page parsing, committed manifest, offline connector incl. 404/broken ZIP, PostgreSQL ingest, search, idempotent re-ingest);
full suite 1,265 passed (340 s); ruff check, ruff format --check, mypy clean.

## Known gaps

- No legislation, links between decisions or to statutes, later history; no STF (403), TRFs, state or specialised courts.
- The 2026 text gap; only selected terminative decisions and acórdãos; no "espelhos de acórdãos" or súmulas.
- No judgment date, case name, state or chamber; party names as published; not the authentic Diário da Justiça text. About 5
  units per decision (12 million passages for the whole set); ingestion cost is dominated by row writes.


## Scope of a server run (set by the lead, 2026-10-15)

The committed manifest lists the portal's whole file set, but the full set is about 2.5 million decisions (roughly 90 GB of
database, a 6+ hour download and about 15 hours of ingestion): more than the planned 100 GB server can hold next to the
other corpora. The manifest therefore carries `from_period: "20250101"` and `document_types: [acordao]`: only collegiate
decisions (acórdãos, which make precedent) published from 2025 are stored. To widen the scope, edit those two keys (earlier
`from_period`; remove `document_types` to add the single-judge decisões monocráticas, about 80% of the volume) after
checking the free disk (about 16 GB of database per year for both types). The ZIPs are still downloaded in full for each
file in scope (about 30 MB per daily file).
