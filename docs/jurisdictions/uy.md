# Uruguay (`UY`)

Profile `config/jurisdictions/uy.yaml` (draft, unverified), source `config/sources/uy-impo.yaml`, manifest
`config/manifests/uy-impo.yaml`, connector `ulis/sources/connectors/impo_uy.py`, parser `ulis/sources/parsers/impo_uy.py`
(`impo_uy_json`), module `ulis/jurisdictions/modules/uy.py`, tests `tests/test_impo_uy.py`, fixtures `tests/fixtures/impo_uy/`.

## Source and licence

**IMPO** (Dirección Nacional de Impresiones y Publicaciones Oficiales, a public non-state legal person that edits the
Diario Oficial) publishes "toda la información pública disponible en sus bases de datos" as open data: any public norm is
available as JSON at `https://www.impo.com.uy/bases/<kind>/<number>-<year>?json=true`
(<https://www.impo.com.uy/datos-abiertos/>, schema `https://www.impo.com.uy/resources/basesIMPO.json`). Licence:
*Licencia de Datos Abiertos - Uruguay v0.1* (commercial and non-commercial use, adaptation and redistribution allowed with a
note of origin and mention of changes:
<https://www.impo.com.uy/wp_impo/wp-content/uploads/2018/06/licencia-de-datos-abiertos.pdf>). robots.txt: `Crawl-Delay: 10`;
the norm addresses are not disallowed, `/cgi-bin/` (where IMPO's search interface sends anonymous visitors to a login page)
is, and is never requested. IMPO's updated texts are informative (`authoritative: false`, `official_consolidation`); the
Diario Oficial is the publication of record. The licence is short and says nothing on automated retrieval: counsel review is
recommended before production use.

## What is ingested

The manifest lists **214 norms** (175 stored, 39 skipped because IMPO's JSON holds no article text for them) (IMPO offers no list of norms to a visitor who is not registered, so it was built from the
source itself): the Constitution of 1967; nine codes (Civil, Penal, de Comercio, General del Proceso, Tributario, Aduanero, de
la Niñez y la Adolescencia, Aeronáutico, de Minería); ten laws or decrees on common subjects (data protection and its decree,
access to information, consumers, companies, labour procedure, abortion, gender violence, drugs, LUC); and every law,
decree-law and decree that the notes of those texts name as amending or regulating them. To extend it add any public
`bases/<kind>/<number>-<year>` (kinds: `constitucion`, `leyes`, `decretos-ley`, `decretos`, `codigo-*`). Spanish only. Each
article is one provision; work URIs are IMPO's own path (`/uy/codigo-civil/16603-1994`), eIds `art-1308`, `art-9-bis`,
`art-131-144`, `art-242-1`.

## Dates and versions

IMPO states no date of update, so every text is **attested at its retrieval date** (`validity_basis = attested_at_edition`,
no provision start dates, one version per norm; point-in-time requests before the retrieval date answer
`in_force_date_unverified` with the `ATTESTED_ONLY_AT` limitation). Amendment history is not available as text; the notes
("Redacción dada por: Ley Nº 18.719 de 27/12/2010 artículo 152") give one `amendment` / `repeal` event per amending norm,
dated with **that norm's own date**, not the day the change took effect ("Ver vigencia" links are kept in the notes). Repealed
articles ("DEROGADO por ..." or an empty article with "Derogado/s por:") have status `repealed`, no text and the statement
in `note`. An entry that IMPO gives without text and that is not repealed (blanked to "(*)" with a pointer to the original
text, or an article of an amending law: "Este artículo dio nueva redacción a: ...") is **not stored** - nothing says it is in
force - and is counted in `ext.articulos_sin_texto`; a norm left with no provision is skipped (`documents_skipped`).

## Text handling

The words are IMPO's. Changed: tags (`<i>`, `<b>`, `<br>`, `<pre>`) removed, IMPO's `(*)` markers removed, the 72-column
hard wrapping re-joined (a break stays after a sentence-ending mark before a new paragraph, item `A)` or table row), entries
that hold several articles split at their inline labels (`242` / `242-1`). IMPO's JSON contains bare control characters in
strings and is read leniently.

## Citations

`artículo 1308 del Código Civil`, `artículo 72 de la Constitución de la República`, `artículo 9 de la Ley Nº 18.331`,
`artículo 9 bis de la Ley Nº 18.331`, `artículo 3 del Decreto Nº 414/009`, `artículos 131 a 144 del Código Civil`.

## Known gaps

See `coverage.known_gaps` in the profile: a 214-norm subset of a database of tens of thousands; no history or earlier wordings;
the Constitution's transitional provisions (one IMPO entry for all clauses) are not ingested; articles are the smallest unit;
no case law; courts and precedent rules are a draft.

## Results (2026-10-14, dev database `ulis_uy`)

- Fetch: all 214 norms downloaded once each (10 s apart, about 40 minutes), no fetch error, no refusal.
- `ingest uy-impo`: 214 seen, **175 new, 39 skipped** (no article text in IMPO's JSON), 0 errors, 0 conflicts; 175 instruments
  (1 constitution, 121 laws and codes, 53 decrees), **19,844 provisions** (732 repealed), 19,112 passages indexed, 1,505
  lifecycle events, 19,376 citation links (16,199 resolved).
- `verify-sources`: **175 pass, 0 fail**, 19,844 units checked.
- Searches (`ulis search ... --jurisdiction UY`): "hecho ilícito que causa un daño obligación de repararlo" -> `artículo 1319
  del Código Civil`; "derecho a la protección de datos personales inherente a la persona humana" -> `artículo 1 de la Ley Nº
  18.331`; "relación de consumo proveedor información al consumidor" -> `artículo 15 de la Ley Nº 17.250`; "interrupción
  voluntaria del embarazo" -> `artículo 2 de la Ley Nº 18.987`; "derecho de acceso a la información pública sujetos obligados"
  -> `artículo 21 de la Ley Nº 18.381`. Hits carry the limitations `ATTESTED_ONLY_AT`, `SOURCE_NOT_AUTHENTIC` and
  `PROFILE_UNVERIFIED` (asserted in the tests).
- Tests: `tests/test_impo_uy.py` 23 tests (parser, citations, connector offline and failure handling, manifest, PostgreSQL
  ingest/search/point-in-time/verify), `tests/test_latam_planned.py` 8; the full suite (all pre-existing tests included) **768 passed** against `ulis_test_uy`; `ruff check`, `ruff format --check` and `mypy ulis` are clean.
