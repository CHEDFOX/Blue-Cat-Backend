# CFR annual-edition fixtures (GovInfo bulk data, `CFRDOC` XML)

Parts of real volumes downloaded on 2026-10-15 from `https://www.govinfo.gov/bulkdata/CFR/<year>/title-<n>/` (zip per
title and year; the XML member is one volume). Each file keeps the volume's `AMDDATE` and title page (`TITLEPG`) and the
chapter heading of its parts; the `PART` elements are copied byte for byte (their indentation included: removing it is
part of what the parser is tested for). Public domain (17 U.S.C. 105); see `config/sources/us-govinfo-bulk-cfr-fr.yaml`.

| File | Source file | Content | Exercises |
|---|---|---|---|
| `CFR-2025-title12-excerpt.xml` | `CFR/2025/title-12/CFR-2025-title12-vol{2,5,9}.xml` | Parts 214, 302 and 1083 of title 12, edition revised as of 2025-01-01 | subject groups, an appendix with a footnote, a table, source credits, three chapters |
| `CFR-2001-title1-part2.xml` | `CFR/2001/title-1/CFR-2001-title1-vol1.xml` | Part 2 of title 1, edition revised as of 2000-04-01 (the folder of 2001) | the older layout: no title number on the title page, `CFRTITLE` heading |
| `CFR-2012-title1-part2.xml` | `CFR/2012/title-1/CFR-2012-title1-vol1.xml` | Part 2 of title 1, revised as of 2012-01-01 | one section (2.5) worded differently from 2000 |
| `CFR-2023-title1-part2.xml` | `CFR/2025/title-1/CFR-2025-title1-vol1.xml` | Part 2 of title 1, revised as of 2023-01-01 (title 1 was not revised in 2024 or 2025: the file sits in the 2025 folder) | sections 2.3 and 2.5 worded differently from 2012; the same wording as the eCFR of 2026 |

The files were checked for secret-like strings (none).
