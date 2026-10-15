# Federal Register fixture (GovInfo bulk data, `FEDREG` XML)

`FR-2026-10-14-excerpt.xml`: three final rules of the issue of Wednesday, 14 October 2026 (volume 91, number 197),
downloaded on 2026-10-15 from `https://www.govinfo.gov/bulkdata/FR/2026/10/FR-2026-10-14.xml`. The file keeps the
issue's root element and the head of its "Rules and Regulations" unit; the `RULE` elements are copied byte for byte,
each after the page mark (`PRTPAGE`) that precedes it in the issue (the first page of a rule is read from these marks).
Public domain (17 U.S.C. 105); see `config/sources/us-govinfo-bulk-cfr-fr.yaml`.

| Rule (FR Doc.) | First page | Exercises |
|---|---|---|
| 2026-21010 Airworthiness directive, Bombardier (FAA) | 65100 | a rule with two amendments (`REGTEXT`) and a heading between them, a signature, a table |
| 2026-20974 Amendment of boundaries for restricted areas (FAA) | 65107 | a short rule with a one-line DATES paragraph |
| 2026-20979 Food additives, chromium propionate (FDA) | 65109 | sub-headed paragraphs inside the preamble, "Final amendment; order." as the action |

The files were checked for secret-like strings (none).
