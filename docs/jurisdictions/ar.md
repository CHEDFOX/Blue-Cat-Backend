# Argentina (AR) - national legislation
Status: **partial, draft_unverified**. Six pilot instruments plus **every national law (tipo Ley) that InfoLEG publishes in
text: 8,113 laws** (widened 2026-10-15), one language (`es`), source `ar-infoleg`. Provincial law: out of scope.

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
- Not used: the legacy host `servicios.infoleg.gob.ar` (robots.txt 404) and the CKAN API of datos.gob.ar (`/api/` is
  disallowed). Used since 2026-10-15, for the list of laws only: the open-data record
  <https://datos.gob.ar/dataset/justicia-base-infoleg-normativa-nacional> ("Licencia Creative Commons Attribution 4.0"),
  one zip of one CSV on datos.jus.gob.ar (50 MB, one request; both hosts' robots.txt: `Crawl-Delay: 10`, Disallow `/api/`,
  `/revision/`, `/dataset/rate/`, `/dataset/*/history`). It holds metadata and links, not texts.

## The whole collection (2026-10-15)

`python -m scripts.build_infoleg_manifest` streams the CSV (429,648 norms, last norm 2026-08-27, Ley 27.825): **27,620 are
`Ley`**, but only 8,115 link a text (`texto_original` 7,390, both 610, only `texto_actualizado` 115); the other 19,505 have a
summary only. The manifest lists 8,113 laws (two without a number, "S/N", left out) - 725 with the texto actualizado,
the rest the original - and keeps the six pilot entries unchanged. Each new entry carries the CSV row (`dataset_row`:
organ, sanction and Boletín dates, summary titles, `observaciones`, counts `modificada_por` / `modifica_a`), so the
connector fetches **one page per law** (`/normativa/nacional/norma-<id>/(actualizacion|texto)`) and stores a bundle of
format 2 (text page + row + the dataset's sha256). **8,119 requests at 10 s = 22.6 h.** A law is `/ar/ley/<number>`; 13
numbers were issued twice before 1863 and get the year (`/ar/ley/5-1854`). Title: `Ley 24.240 - DEFENSA DEL CONSUMIDOR`
(the editors' summary title, as given); citations `art. 3, Ley 24.240`. 19th-century headings `Art. 1° Abrese ...` are read.

What the CSV says that the pages do not is kept as warnings, not as law: 3,549 listed laws have `modificada_por` > 0 but only
the original text ("InfoLEG lists N norm(s) that modify this law but publishes no updated text"), and 404 carry an
`observaciones` that mentions abrogation ("ABROGADA POR ART. 30 DE LA LEY N° 21.795 ... RESTITUIDA SU VIGENCIA POR ...",
quoted whole). InfoLEG gives no repeal flag, so a law without either can still be repealed. Pages with an image, a
superscript/subscript or no `Art.` line are skipped (`ParseSkipped`), not errors.

**Scale.** A uniform random sample of 70 listed laws (fetched and parsed 2026-10-15): 68 read, 2 skipped; 10.6 provision
versions per law (median 3, max 163), raw bundle 49 KB. Extrapolated: ~86,000 provision versions, **~0.3 GB** of PostgreSQL
and ~0.4 GB of raw bundles; about 3% of laws skipped. Slice run on `ulis_ar` (89 laws = the sample + the pilot + 13 chosen
for their features): `ingest` 86 new, 3 skipped, 0 errors, 5,045 provision versions; `verify-sources` **86 of 86 pass**.
Searches: "defensa del consumidor relación de consumo proveedor" -> `art. 3, Ley 26.361`, `art. 3, Ley 24.240`; "al que matare
a otro" -> `art. 79 CP`; "Abrese un crédito suplementario" -> `art. 1, Ley 274` (a 1861 law, warning "1 norm(s) listed as
modifying"); all with `ATTESTED_ONLY_AT`.

## Ingested (the six pilot instruments, real run 2026-10-14)

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

- National laws with a text only (8,113 of 27,620); no decrees, decree-laws, resolutions, treaties, case law or Boletín
  Oficial. Annexes (and so the text of treaties approved by law) are not read. The profile
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
