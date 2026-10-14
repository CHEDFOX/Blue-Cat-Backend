# BOE consolidated legislation test fixtures (real Spanish State law)

Unmodified responses of the BOE open-data API
`GET https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/{id}` (`Accept: application/xml`),
retrieved on 2026-10-14 by the `boe_consolidada` connector (same layout as
`var/sources/www.boe.es/legislacion-consolidada/`):

| File | Content |
|---|---|
| `BOE-A-1978-31229.xml.gz` | Constitución Española, all versions (1978, 1992, 2011, 2024, 2026); record updated 2026-05-20 |
| `BOE-A-2018-16673.xml.gz` | Ley Orgánica 3/2018, de Protección de Datos Personales y garantía de los derechos digitales, all versions |
| `BOE-A-1995-25444.xml.gz` | Ley Orgánica 10/1995, del Código Penal, all versions (repeals, range blocks, expired blocks, duplicate listings) |

The XML responses are stored gzip-compressed (byte-for-byte identical when decompressed); the
tests decompress them into a temporary cache. `*.meta.json` files record the URL and retrieval
time.

Basado en datos de la Agencia Estatal Boletín Oficial del Estado (https://www.boe.es), reused
under the AEBOE reuse conditions (https://www.boe.es/informacion/aviso_legal/index.php#reutilizacion).
Textos consolidados de carácter meramente informativo: only the publication in the diario
oficial "Boletín Oficial del Estado" is official and authentic.
