# IMPO (Uruguay) fixtures

Excerpts of real answers of **IMPO** (Dirección Nacional de Impresiones y Publicaciones Oficiales),
<https://www.impo.com.uy/datos-abiertos/>, retrieved on **2026-10-14** by the ULIS connector (identifying User-Agent,
`Crawl-Delay: 10` from robots.txt) as `https://www.impo.com.uy/bases/<kind>/<number>-<year>?json=true`. Licence:
*Licencia de Datos Abiertos - Uruguay, versión 0.1* (commercial and non-commercial use, adaptation and redistribution
allowed with a note of origin: <https://www.impo.com.uy/wp_impo/wp-content/uploads/2018/06/licencia-de-datos-abiertos.pdf>).
The updated texts are informative; the Diario Oficial is the publication of record.

Files (`<name>.json`): `constitucion` (Constitución de la República), `codigo-civil` (Ley 16.603), `codigo-penal`
(Ley 9.155), `ley-18331` (protección de datos personales), `ley-17016` (estupefacientes: articles that only amend
another text), `decreto-414-2009` (regulation of Ley 18.331) and `decreto-ley-14653` (a decree-law of 1977). Each file
keeps the norm-level fields as IMPO sent them and only some of the `articulos` objects, each unchanged (the Constitution's,
the Civil Code's, the Penal Code's, Ley 18.331's and the decree's lists are shortened; Ley 17.016 and the decree-law are
complete). The files are ISO-8859-1, the encoding IMPO uses; re-indented for reading (the source has its own spacing).

`meta.json` holds the URL, retrieval time and content type of every answer; `robots.txt` is the site's robots.txt as read
that day. Tests build the connector's bundle from these files, so the bytes the parser sees are those the connector would
store.
