# InfoLEG (Argentina) fixtures

Excerpts of real pages of **InfoLEG - Leyes Argentinas** (Ministerio de Justicia de la Nación),
<https://www.argentina.gob.ar/normativa>, retrieved on **2026-10-14** by the ULIS connector (identifying
User-Agent, `Crawl-delay: 10` from robots.txt). Licence: the portal's terms
(<https://www.argentina.gob.ar/terminos-y-condiciones>) license all contents under *Creative Commons Atribución 4.0
Internacional*; attribution: InfoLEG - Información Legislativa, Ministerio de Justicia de la Nación. The updated
texts are informative; the authentic text is the Boletín Oficial (<https://www.argentina.gob.ar/normativa/aviso-legal>).

Instruments (`<name>`): `constitucion` (id 804, texto original of Ley 24.430), `cccn` (id 235975, Ley 26.994),
`codigo-penal` (id 16546, Ley 11.179), `ley-25326` (id 64790), `lct` (id 25552, Ley 20.744) and `ley-19549`
(id 22363). For every instrument two files:

- `<name>.summary.html` - the norm's summary page (`/normativa/nacional/norma-<id>`), reduced to its `<title>`,
  canonical link and `<article>` (number, organ, dates, Boletín Oficial data, links to the texts);
- `<name>.text.html` - the text page (`.../actualizacion` = texto actualizado, `.../texto` = texto original), reduced to
  the text body (`infoleg-norma-body`). The Constitution, Ley 25.326 and Ley 19.549 are complete; for the Civil and
  Commercial Code, the Penal Code and the Ley de Contrato de Trabajo only the passages the tests need are kept (whole
  lines, in the original order, markup unchanged; tags left unbalanced by the cuts are dropped), so the structural
  headings between them (and thus `context_heading`) are incomplete.

`meta.json` holds the URL and retrieval time of every page; `robots.txt` is the site's robots.txt as read that day.
Tests build the connector's bundle from these files, so the bytes the parser sees are those the connector would store.
