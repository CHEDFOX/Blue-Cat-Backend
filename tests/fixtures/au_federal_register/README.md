# Federal Register of Legislation fixtures

Unmodified responses of the Federal Register of Legislation public API
(`https://api.prod.legislation.gov.au/v1/`, Office of Parliamentary Counsel), retrieved on
2026-10-14 (compilations) and 2026-10-18 (texts as made, legislative instruments) by
`ulis.sources.connectors.au_federal_register` and stored gzip-compressed in the connector's cache
layout (`cache/api.prod.legislation.gov.au/<title id>/...`; each `.meta.json` holds the request URL and
retrieval time).

| Title | Files |
|---|---|
| Commonwealth of Australia Constitution Act (C2004Q00685) | versions and EPUB documents listings; compilations 4 (from 1946-12-19), 5 (from 1967-08-10) and 6 (from 1977-07-29), latest rectification |
| Acts Interpretation Act 1901 (C1901A00002) | listings; compilation 39 (from 2026-03-28) |
| Privacy Act 1988 (C2004A03712) | listings; compilation 104 (from 2026-06-04, ending 2026-12-10 when a later version starts) |
| Insurance Contracts Act 1984 (C2004A02944) | the reprint in force from 1998-07-01 (older format: h1-h6 headings, `indenta`/`indentii` styles) |
| Royal Style and Titles Act 1973 (C2004A00044) | listings; the Act as made (never amended; assent 1973-10-19; preamble, two sections and a schedule) |
| Commonwealth Entities (Payment Surcharges) Act 2024 (C2024A00101) | listings; the Act as made (never amended; assent 2024-12-02; current template with `AssentDt`, `2ndRd`, `AssentBk`) |
| Defence (Laverton—Defence Area) Declaration 2026 (F2026L01385) | listings; a legislative instrument as made (registered 2026-10-14, made 2026-10-12, self-repealing on 2026-11-30) |
| South Pacific Regional Environmental Programme (Privileges and Immunities) Regulations 1996 (F1996B00533) | listings; compilation 1 (from 2000-07-01, Office of Legislative Drafting template: `HR`, `R1`, `R2`, `P1`-`P3`, `Note`, `NoteHeading` endnotes) |

Each document file is the document requested with `Accept: application/json` and `$expand=version`:
the EPUB in base64 plus the version metadata (start and end dates).

Licence: Creative Commons Attribution 4.0 International
(https://creativecommons.org/licenses/by/4.0/; terms at https://www.legislation.gov.au/terms-of-use).
Sourced from the Federal Register of Legislation at 14 October 2026 and 18 October 2026. For the latest
information on Australian Government law please go to https://www.legislation.gov.au. (The content is
unchanged; the files are only gzip-compressed.)
