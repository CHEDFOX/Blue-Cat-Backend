# Fixtures of the `constitution_pages_b` parser

Real documents read on 2026-10-15 with the ULIS research bot (robots.txt and terms are quoted in the source descriptors in
`config/sources/`). The parser is tested with the `parser_hints` of the committed manifests in `config/manifests/`.

| File | Source | What it is | Licence |
|---|---|---|---|
| `cr-tse-constitucion-politica.pages.pdf` | `cr-tse-constitution` (https://www.tse.go.cr/pdf/normativa/constitucion.pdf) | PDF pages 3, 4, 61 and 62 of 65 (articles 8-12, 196, 197 and transitional provisions), pages copied unchanged with `pdfseparate`/`pdfunite` | CC BY-SA 4.0 (licence link in the site footer) |
| `by-president-constitution.html.gz` | `by-president-constitution` (https://president.gov.by/ru/gosudarstvo/constitution) | the whole page, gzip-compressed | portal notice: use with a link to the source, no prior consent needed |
| `mx-diputados-cpeum.pages.txt` | `mx-diputados-constitution` (https://www.diputados.gob.mx/LeyesBiblio/pdf/CPEUM.pdf) | text layer of PDF pages 1-3 and 160-162 of 416 as `pdftotext` returns it (form feeds between pages) | Ley Federal del Derecho de Autor art. 14 VIII |
| `be-dekamer-constitution-fr.pages.txt` | `be-dekamer-constitution` (planned; GrondwetFR.pdf) | text layer of PDF pages 1 (cover), 7-10 and 82-83 of 84 | publisher's FAQ, undecided (test material only) |
| `be-dekamer-constitution-nl.pages.txt` | `be-dekamer-constitution` (planned; GrondwetNL.pdf) | text layer of PDF pages 1 (cover), 7-10, 86 and 87 of 88 | publisher's FAQ, undecided (test material only) |

The `.txt` files are the output of the same `pdf_pages` function the parser uses for PDFs, joined with form feeds; the parser reads
them as plain text with page breaks, so running headers and page numbers are dropped exactly as for a PDF. The PDF fixture is kept
whole-page (about 400 KB) to test the PDF path itself; the larger PDFs are not committed.
