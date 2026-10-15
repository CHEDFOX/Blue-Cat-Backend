# Sub-national fixtures (Queensland, Tasmania, Ontario, Manitoba)

Real documents retrieved on 2026-10-14 with the ULIS research client (identifying User-Agent, 5 s delay) from the
publishers' own pages, after reading their licence terms and robots.txt (see `config/sources/`). Stored gzip-compressed
(`.gz`); the tests decompress them. Each file is byte-for-byte what the publisher served, except the two excerpts.

| File | Source | Licence |
|---|---|---|
| `qld/act-2013-022.xml.gz`, `qld/act-2019-005.xml.gz` | https://www.legislation.qld.gov.au/view/xml/inforce/current/act-2013-022 (Succession to the Crown Act 2013), `.../act-2019-005` (Human Rights Act 2019) | CC BY 4.0 |
| `qld/act-1899-009-excerpt.xml.gz` | EXCERPT of https://www.legislation.qld.gov.au/view/xml/inforce/current/act-1899-009 (Criminal Code Act 1899): all clauses of the Act, Schedule 1 reduced to chapter 1 and clauses 300 to 305 of chapter 28; re-serialised, DTD declaration and comments dropped | CC BY 4.0 |
| `tas/act-2000-019.xml.gz` | https://www.legislation.tas.gov.au/view/xml/inforce/current/act-2000-019 (First Home Owner Grant Act 2000) | CC BY 4.0 |
| `tas/act-1924-069-excerpt.xml.gz` | EXCERPT of https://www.legislation.tas.gov.au/view/xml/inforce/current/act-1924-069 (Criminal Code Act 1924): the Act's sections, Schedule 1 reduced to chapters I and XVII (sections 156 to 160); re-serialised | CC BY 4.0 |
| `ontario/90c43.html.gz`, `ontario/90t21.html.gz`, `ontario/90n01.html.gz` | https://www.ontario.ca/laws/statute/90c43 (Courts of Justice Act), `/90t21` (Trespass to Property Act), `/90n01` (Negligence Act, an older page template) | (c) King's Printer for Ontario; reproduction of statutes permitted, see `config/sources/ca-on-e-laws.yaml` |
| `manitoba/h175.html.gz` | https://web2.gov.mb.ca/laws/statutes/ccsm/h175.php?lang=en (The Human Rights Code) | OpenMB Information and Data Use Licence |

Attribution texts are in the source descriptors. Excerpts are marked as such; nothing in them was edited.
Checked: none of the files contains a string that looks like a secret.
