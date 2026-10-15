# Statutes at Large fixtures (GovInfo bulk data, USLM XML "beta")

A few real laws cut out of four volumes downloaded on 2026-10-15 from
`https://www.govinfo.gov/bulkdata/STATUTE/<volume>/STATUTE-<volume>.xml`. Each file keeps the volume's root element and
metadata and holds only the selected `<pLaw>` elements, copied byte for byte (nothing is edited), so the files are
valid, tiny volumes. Public domain (17 U.S.C. 105): each law's own `dc:rights` says so; see
`config/sources/us-govinfo-bulk-statutes.yaml`.

| File | Laws | Exercises |
|---|---|---|
| `STATUTE-1-sample.xml` | 1 Stat. 23 (Act of June 1, 1789, ch. I), 1 Stat. 573, two acts that begin on 1 Stat. 418 | acts before 1901 (no law number, chapters in roman numerals), an act without sections, two acts on one page, margin notes |
| `STATUTE-32-sample.xml` | 32 Stat. 1 (Public Law 57-1), 32 Stat. 1449 (Private Law 57-944), 32 Stat. 1759 (Private Law 57-1122) | the law number found only in the margin note (`/us/pl/57/1`, `/us/pvtl/57/944`), private laws, a short act without sections, a quoted title of an earlier Act that is not the Act's name |
| `STATUTE-72-sample.xml` | 72 Stat. 706 (Public Law 85-317) | `Public Law 85-317` in `citableAs` |
| `STATUTE-138-sample.xml` | Public Law 118-214 (138 Stat. 2805), Public Law 118-250 (138 Stat. 2939) | a modern law with a "short title" section, one that amends title 28 and has no short title |

The files were checked for secret-like strings (none).
