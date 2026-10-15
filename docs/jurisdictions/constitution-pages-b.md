# Constitutions published as web pages or PDFs (`constitution_pages_b`): Costa Rica, Mexico, Belarus

Part of the sweep recorded country by country in [constitutions-americas-europe.md](../constitutions-americas-europe.md) (67 countries,
67 publishers examined). Three publishers could be ingested; the others are `planned` or `rejected` with the publisher's own words.
Research aid, not legal advice. All profiles are `draft_unverified` and minimal (constitution only).

## Sources and licences (read 2026-10-15)

| Source | Publisher | Documents | Licence basis (AMBER) |
|---|---|---|---|
| `cr-tse-constitution` | Tribunal Supremo de Elecciones | `https://www.tse.go.cr/pdf/normativa/constitucion.pdf` (es, 65 pp.) | site footer: "Basada en una obra en https://www.tse.go.cr" with the link <http://creativecommons.org/licenses/by-sa/4.0/> (rel="license"); the Términos y Condiciones restrict only the Tribunal's marks and graphics |
| `mx-diputados-constitution` | Cámara de Diputados | `https://www.diputados.gob.mx/LeyesBiblio/pdf/CPEUM.pdf` (es, 416 pp.) | no robots.txt, no terms; Ley Federal del Derecho de Autor art. 14 VIII (read on the same host): legislative texts are "No son objeto de la protección como derecho de autor" |
| `by-president-constitution` | Press service of the President | `https://president.gov.by/{ru,be}/gosudarstvo/constitution` | portal notice: "Материалы Портала могут использоваться ... Единственным условием является ссылка на первоисточник. Никакого предварительного согласия ... не требуется" |

Each source YAML quotes the robots.txt, the terms pages and the document header it relied on. None of the three is the authentic
publication (La Gaceta, Diario Oficial de la Federación, the national legal portal); every search hit says so (`SOURCE_NOT_AUTHENTIC`,
`OFFICIAL_CONSOLIDATION_REPRODUCED`). The Belarus notice is silent on commercial use, so `commercial_use: unknown` and a commercial
deployment (`ULIS_COMMERCIAL_USE=1`) refuses that source.

## What the connector and parser do

- Connector `constitution_pages_b`: one manifest per publisher (`config/manifests/<source id>.yaml`: hosts, documents with country,
  language, URL, format `html|pdf|text`, title, `parser_hints`); reads robots.txt first (a refusal, an unreadable file or a closing rule stops
  the host), checks every path, 5 s between requests, cache under `var/sources/<host>/`; a 404 or disallowed path goes to `fetch_errors`.
  The file is stored byte for byte inside a JSON bundle with the manifest's facts.
- Parser: HTML (one paragraph per block element, superscripts kept), text-layer PDF through `pdf_text.py` (running headers/footers and
  bare page numbers dropped, hard wraps re-joined) or plain text. Articles are split by the document's own headings (`Artículo 1o.`,
  `Статья 116¹.`, `Art. 7bis`, `1 straipsnis`, `1-modda` ...); Titles/Chapters/Sections become the context heading; editorial notes
  (`Nota: Reformado ...`, `Párrafo reformado DOF ...`) go to the note; a numbering restart under "transitional/final provisions" is a
  separate series cited `art. T10`; unnumbered text becomes blocks; fewer than three articles recognised means blocks of paragraphs.
- Module `constitution_b`: `Costa Rica Constitution art. 197`, `Belarus Constitution art. 116¹`, `Mexico Constitution art. 133`,
  `... preamble`, `... text block 3`. A profile that already has its own module (Portugal, Brazil) is used through `parser_hints.ext_module`.
- Dates: none of the documents dates its provisions, so versions are `attested_at_edition` (retrieval date, or the publisher's edition
  statement kept as `ext.edition_note`: Mexico "Última reforma publicada DOF 07-10-2026").

## Numbers (dev database `ulis_consb`, this sandbox)

- Ingest: CR 210 provisions (preamble, 197 articles, 12 transitional `T..`), MX 137 (136 articles, 1 block), BY 159 per language (preamble + 158
  articles), 4 documents, 0 errors, 0 skipped; 15 MB of database for 665 provisions; `verify-sources`: 4 of 4 pass.
- Requests: the whole sweep fetched about 300 pages (robots.txt, home/terms pages, documents); the three ingests 8 requests (3 robots + 5 documents).
  Full run of the committed manifests: 6 documents, about 1 minute at the crawl delay.
- Searches (`ulis.cli search`): CR "Un Tribunal Supremo de Elecciones, con el rango e independencia" finds `Costa Rica Constitution art. 9`;
  MX "serán la Ley Suprema de toda la Unión" finds `Mexico Constitution art. 133`; BY "Государственными языками являются белорусский и
  русский" finds `Belarus Constitution art. 17`.
- Tests: `tests/test_constitution_pages_b.py` (43 tests, real fixtures in `tests/fixtures/constitution_pages_b/`: a Costa Rica PDF, the Belarus
  page, text layers of the Mexican and the two Belgian PDFs); full suite 1313 passed against `ulis_test_consb`.

## Known gaps

- Mexico: the 1917 transitional articles and those of every reform decree are not ingested (about 280 of the 416 pages); the Cámara's
  annotations are kept only as notes. Costa Rica: the PDF's reform notes sit under the article they follow. Belarus: no English page.
- Layouts the generic reader does not handle: Commonwealth "Laws of ..." editions (sections, not articles), two-column PDFs (Norway),
  tables of revision references (Belgian Senate page), documents interleaved with commentary (Ukraine). They fall back to blocks or are left planned.
- No history of earlier wordings; no provision start dates; paragraphs and points stay inside the article text; titles of articles are not split.
- `config/manifests/be-dekamer-constitution.yaml` is ready for the planned Belgian source (French and Dutch PDFs, edition March 2021) and is tested.

## Adding a publisher

Read its robots.txt and terms; if they allow reuse (or a statute that excludes legislative texts from copyright is read and quoted),
write `config/sources/<id>.yaml` (`connector: constitution_pages_b`, `parser: constitution_pages_b`) and the manifest, add the profile
(`citation.module: constitution_b`) and the ids to `tests/test_catalog.py`. `scripts/build_constitution_findings_b.py` regenerates the
planned/rejected descriptors and the findings document from `scripts/constitution_findings_b_data.py`.
