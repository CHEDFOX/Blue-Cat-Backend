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

The manifest lists **214 norms** (IMPO offers no list of norms to a visitor who is not registered, so it was built from the
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
in `note`. An amending law's article that IMPO publishes without text keeps empty text and the note. A norm with neither
text nor notes (some 19th-century laws) is skipped.

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

RESULTS_PLACEHOLDER
