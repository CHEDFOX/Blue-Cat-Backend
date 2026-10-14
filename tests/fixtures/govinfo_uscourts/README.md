# GovInfo US courts opinions fixtures

Real files from https://www.govinfo.gov, retrieved 2026-10-14 (robots.txt and the policy pages read first; the robots.txt
copy is `../govinfo_usreports/robots.txt`). Public domain (17 U.S.C. 105 per GovInfo's notice).

| File | Source |
|---|---|
| `USCOURTS-ca13-23-01808.mods.xml`, `USCOURTS-ca13-23-01808-0.pdf` | `/metadata/pkg/USCOURTS-ca13-23-01808/mods.xml` and `/content/pkg/USCOURTS-ca13-23-01808/pdf/USCOURTS-ca13-23-01808-0.pdf`: Kolenc v. HHS, Federal Circuit, 3 January 2024 (7 pages, text layer) |
| `USCOURTS-ca1-00-01824.mods.xml`, `USCOURTS-ca1-00-01824-0.pdf` | the same routes for DeRosa v. Lawrence, First Circuit, 10 September 2001 (2 pages, a scan: the only text is the court's e-filing banner) |
| `USCOURTS_ca1_2001_sitemap.xml` | `/sitemap/USCOURTS/USCOURTS_ca1_2001_sitemap.xml`: the 7 packages GovInfo lists for the First Circuit in 2001 |

All files are unchanged. Text extraction needs `pdftotext` (poppler-utils); the PDF tests skip when it is absent.
