# United States Code fixtures (OLRC USLM XML, release point 119-120)

Unmodified per-title archives as downloaded on 2026-10-14 from the Office of the Law Revision
Counsel, https://uscode.house.gov/download.html ("All files are current through Public Law 119-120
(10/07/2026)"), each holding one `usc<NN>.xml`. Whole titles, not slices; the archives are used as
received (they are also what the connector yields and what the parser opens).

| File | SHA-256 | Title | Retrieved (UTC) | Exercises |
|---|---|---|---|---|
| `xml_usc01@119-120.zip` | `6d7d505d...8ce9ed5` | 1, General Provisions (positive law) | 15:28:54 | small title; 1 U.S.C. 204 |
| `xml_usc17@119-120.zip` | `aa970d45...bb0f3e6c` | 17, Copyrights (positive law) | 15:29:09 | 17 U.S.C. 107; repealed sections, a renumbered placeholder |
| `xml_usc27@119-120.zip` | `e47d81f7...083b01b` | 27, Intoxicating Liquors (NOT positive law) | 15:27 (approx.) | `is-positive-law` = no; omitted and transferred placeholders |

Source URLs: `https://uscode.house.gov/download/releasepoints/us/pl/119/120/xml_usc<NN>@119-120.zip`.
Licence: works of the United States Government, not subject to copyright (17 U.S.C. 105); the OLRC
states no licence of its own (see `config/sources/us-uscode-house.yaml`). The decompressed XML was
checked for secret-like strings (none).
