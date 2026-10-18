# United States Code fixtures (OLRC USLM XML, release points 119-120 and 119-121)

Unmodified archives as downloaded from the Office of the Law Revision Counsel,
https://uscode.house.gov/download.html, each holding one `usc<NN>.xml` (`usc18a.xml`, `usc50A.xml` for an
appendix). Whole titles and appendices, not slices; the archives are used as received (they are also what the
connector yields and what the parser opens).

Titles, downloaded on 2026-10-14 ("All files are current through Public Law 119-120 (10/07/2026)"):

| File | SHA-256 | Title | Retrieved (UTC) | Exercises |
|---|---|---|---|---|
| `xml_usc01@119-120.zip` | `6d7d505d...8ce9ed5` | 1, General Provisions (positive law) | 15:28:54 | small title; 1 U.S.C. 204 |
| `xml_usc17@119-120.zip` | `aa970d45...bb0f3e6c` | 17, Copyrights (positive law) | 15:29:09 | 17 U.S.C. 107; repealed sections, a renumbered placeholder |
| `xml_usc27@119-120.zip` | `e47d81f7...083b01b` | 27, Intoxicating Liquors (NOT positive law) | 15:27 (approx.) | `is-positive-law` = no; omitted and transferred placeholders |

Appendices, downloaded on 2026-10-18 ("All files are current through Public Law 119-121 (10/09/2026)"):

| File | SHA-256 | Appendix | Retrieved (UTC) | Exercises |
|---|---|---|---|---|
| `xml_usc18a@119-121.zip` | `c3e6999f...d346e64c` | to title 18 (`USCTitleAppendix`, `is-positive-law` = no) | 01:08:53 | the Federal Rules of Criminal Procedure (`courtRules`, 77 `courtRule`s in 9 titles, two reserved, one transferred), the Classified Information Procedures Act and the Interstate Agreement on Detainers (`compiledAct` with `/us/usc/t18a/pl/...` identifiers), a repealed statutory `level` |
| `xml_usc50a@119-121.zip` | `af28e101...6309ed` | to title 50 ("eliminated") | 01:09:03 | a file with notes only and no root identifier: skipped, not an error |

The download page itself, as received on 2026-10-18 01:35:29 UTC (gzip, 46,441 bytes decompressed, nothing edited):
`download.html@119-121.gz` - release point 119-121, 53 title rows and 5 appendix rows; `download_page_excerpt.html` is
seven rows of the page as received on 2026-10-15 (release point 119-120).

Source URLs: `https://uscode.house.gov/download/releasepoints/us/pl/119/<law>/xml_usc<NN>[a]@119-<law>.zip`.
Licence: works of the United States Government, not subject to copyright (17 U.S.C. 105); the OLRC
states no licence of its own (see `config/sources/us-uscode-house.yaml`). The decompressed XML and the page were
checked for secret-like strings (none).
