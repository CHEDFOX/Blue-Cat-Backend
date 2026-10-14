# Argentina (AR) - national legislation
Status: **pilot, draft_unverified**. Six instruments, one language (`es`), source `ar-infoleg`. Provincial law: out of scope.

## Source and licence

- **InfoLEG - Leyes Argentinas** (Ministerio de Justicia de la Nación, Centro de Documentación e Información),
  <https://www.argentina.gob.ar/normativa>. Per norm a summary page (`/normativa/nacional/norma-<id>`) and its texts
  (`.../actualizacion` = texto actualizado, `.../texto` = texto original), the pages behind the site's own buttons.
- Licence (read 2026-10-14): the terms (<https://www.argentina.gob.ar/terminos-y-condiciones>) license all contents under
  **CC BY 4.0**; they say nothing against automated reading. Attribution: "Fuente: InfoLEG - Información Legislativa,
  Ministerio de Justicia de la Nación" (ULIS's wording). robots.txt: `Crawl-delay: 10`; it disallows `*?tca=*`,
  `/sites/default/files/infoleg/` (InfoLEG's file downloads), `/*.pdf$` and others, never the norm pages. The connector
  reads robots.txt, waits 10 s and refuses disallowed paths (13 requests for the six instruments).
- **Authenticity**: the Boletín Oficial is authentic (decretos 659/1947 and 207/2016, quoted by the
  [Aviso legal](https://www.argentina.gob.ar/normativa/aviso-legal)); InfoLEG's editors record only express modifications
  and "no se hace responsable por la exactitud de los textos actualizados". Texts are `authoritative: false`
  (`official_revised`; the Constitution `official_conversion`), and every result says so.
- Not used: the legacy host `servicios.infoleg.gob.ar` (robots.txt 404) and the open-data record on
  <https://datos.gob.ar/dataset/justicia-base-infoleg-normativa-nacional> (CC BY 4.0, CSV/ZIP): it holds norm metadata
  and links, not texts.

## Ingested (real run, 2026-10-14)

| Instrument | InfoLEG id | Text | Provisions | Repealed |
|---|---|---|---|---|
| Constitución Nacional (texto oficial, Ley 24.430) | 804 | original | 148 (preamble, 130 arts., 17 transitory) | 0 |
| Código Civil y Comercial (Ley 26.994, Anexo I) | 235975 | actualizado | 2674 | 4 |
| Código Penal (Ley 11.179, t.o. 1984 actualizado) | 16546 | actualizado | 392 | 20 |
| Ley 25.326 (datos personales) | 64790 | actualizado | 48 | 0 |
| Ley de Contrato de Trabajo (Ley 20.744) | 25552 | actualizado | 292 | 16 |
| Ley 19.549 (procedimientos administrativos) | 22363 | actualizado | 37 | 0 |

`ingest ar-infoleg`: 6 documents, 6 instruments, **3591 provision versions**, 3551 passages indexed (repealed ones are not),
0 conflicts, 0 errors. `verify-sources`: **6 of 6 pass** (every stored text re-derived from its stored raw bundle).

## How dates and versions work

The pages state no date of update. Each text is stored as a bundle (summary page + text page, exactly as received, with
URLs and retrieval times) and attested **at its retrieval date** (`validity_basis = attested_at_edition`,
`currency_date` = `version_date` = retrieval date); provisions have no start date. A lookup for any date returns
`in_force_date_unverified` with `ATTESTED_ONLY_AT` (history gap, not "not yet in force"). The "Antecedentes normativos"
list of amending norms is not ingested; the inline notes ("Artículo sustituido por art. 1° de la Ley 27.077 B.O.
19/12/2014") are kept as provision notes. Events: sanction and Boletín Oficial publication of the law.

## Structure rules

One provision per article (`art-14-bis`, `art-105-bis`; suffixes as printed: bis, ter, quater, quinquies, quinque, quinto).
Paragraphs and numbered or lettered items stay in the article text. The Constitution adds `preamble` and `dt-1..17`
(transitory clauses). Headings: Ley 25.326 `(Objeto)` and LCT `Art. 2° — Ambito de aplicación.` give a heading; in the
codes the rubric is the first words of the text. Libro/Título/Capítulo/Sección and centred section titles become
`context_heading`. Editorial notes go to `note`, never `text`; an article with no text and a "derogado" note is `repealed`.

## Citations (module `ar`)

`art. 1109 CCCN`, `art. 79 CP`, `art. 14 bis CN`, `art. 245 LCT`, `art. 5, Ley 25.326`, `art. 14, Ley 19.549`,
`disp. trans. primera CN`, `CN, preámbulo`. Items (`art. 4 inc. a), Ley 25.326`) are produced when a provision has
sub-numbers, which the current sources do not give. Abbreviations to be confirmed by an Argentine reviewer.

## Known gaps

- Six instruments only; no decrees, resolutions, treaties, other laws, case law or Boletín Oficial. The profile
  (courts, precedent, treaty rules) is a simplified draft marked "To verify".
- Informative texts; only express modifications; no history or per-provision dates. An out-of-order "Artículo N" line is
  taken as text (the real run had none).
- Article is the smallest provision; items are not separate. Trailing editorial parentheticals are moved to `note`
  (anchored by the item marker); a heading/rubric heuristic applies to LCT (a line over 150 characters is text).
- CCCN = Anexo I only (not Ley 26.994's own articles or Anexo II); Constitution = Preamble to the last transitory clause.
- Ley 19.549 was issued by the de facto executive in 1972 (InfoLEG lists it as a law); not modelled. The Constitution is
  the original text of Ley 24.430 (the site offers no updated text for it).

## Tests and sample searches
`tests/test_infoleg_ar.py` (fixtures in `tests/fixtures/infoleg_ar/`): 23 tests, 19 parser/module/connector and 4 PostgreSQL
(ingest, search with citation and limitations, history gap and repeal lookups, idempotent re-ingest plus `verify`). Full suite
against the test DB: **442 passed** (3 min 53 s); ruff check/format and mypy clean.

Searches (`ulis.cli search --jurisdiction AR`): "al que matare a otro" -> `art. 79 CP`; "consentimiento libre, expreso e
informado datos personales" -> `art. 5, Ley 25.326`; "derecho de huelga gremios convenios colectivos" -> `art. 14 bis CN`;
"abuso de posición dominante" -> `art. 11 CCCN`; "nulidad absoluta e insanable acto administrativo" -> `art. 14, Ley 19.549` (all with `ATTESTED_ONLY_AT`,
`PROFILE_UNVERIFIED`).
