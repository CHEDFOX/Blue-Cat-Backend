# Lilex fixtures

Real pages of Lilex, the consolidated-law database of the Principality of Liechtenstein (https://www.gesetze.li,
Rechtsdienst der Regierung), retrieved 2026-10-14 with the project's polite fetcher (identifying User-Agent, 5 s
between requests). Stored gzip-compressed and byte for byte as received (the law pages carry a long hexadecimal
form token that secret scanners mistake for a key).

The site states no terms of use and has no robots.txt; the pages are reproduced under Art. 5 of the
Urheberrechtsgesetz (laws and official collections of them are not protected by copyright), see
`config/sources/li-lilex.yaml`.

| file | page |
|---|---|
| `glg-1999-096-page.html.gz` | law page `/konso/1999.096` (Gleichstellungsgesetz, GLG): the list of versions (3-7) and the iframe address |
| `glg-1999-096-v6.html.gz` | `/konso/html/1999096000?version=6`, applied 01.01.2017 - 31.12.2021 |
| `glg-1999-096-v7.html.gz` | `/konso/html/1999096000?version=7`, applied from 01.01.2022 (Art. 4a Abs. 5 Bst. c repealed) |
| `verfassung-1921-015-page.html.gz` | law page `/konso/1921.015` (Verfassung, 45 versions from 24.10.1921) |
| `verfassung-1921-015-v45.html.gz` | `/konso/html/1921015000?version=45`, applied from 01.01.2026 |
| `urg-1999-160-page.html.gz` | law page `/konso/1999.160` (Urheberrechtsgesetz, URG) |
| `urg-1999-160-v10.html.gz` | `/konso/html/1999160000?version=10`, the version of 2026 (Art. 5: laws are not protected) |

The tests rebuild the connector's cache from these files (offline) and wrap each version text in the connector's
JSON bundle (`ulis-li-lilex-bundle/1`) the way the connector writes it.
