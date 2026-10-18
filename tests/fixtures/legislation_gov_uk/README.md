# legislation.gov.uk test fixtures (real UK legislation)

Unmodified responses from https://www.legislation.gov.uk, cached by the
`legislation_gov_uk` connector (same layout as `var/sources/legislation.gov.uk`):

| File | Content |
|---|---|
| `ukpga/1968/60/current.akn.gz` | Theft Act 1968, revised, current point in time |
| `ukpga/1968/60/2001-10-01.akn.gz` | Theft Act 1968 as it stood on 1 October 2001 (before the Fraud Act 2006 repeals) |
| `ukpga/1998/42/current.akn.gz` | Human Rights Act 1998, revised, current point in time |
| `ukpga/Vict/22/1/current.akn.gz`, `ukpga/Vict/22-23/1/current.akn.gz` | Burial Act 1859 and Clerk of the Council Act 1859 (both "1859 c. 1"; pre-1963 Acts use regnal-year paths) |
| `ukpga/Eliz2/7-8/49/current.akn.gz` | Chevening Estate Act 1959 (a deed in its Schedule repeats paragraph numbers, hence repeated eIds) |
| `anaw/2020/3/current.akn.gz`, `anaw/2020/3/current.cy.akn.gz` | Children (Abolition of Defence of Reasonable Punishment) (Wales) Act 2020, current revised text, in English and in Welsh (`.../welsh/data.akn`; retrieved 2026-10-18) |
| `wsi/2024/1337/made.akn.gz`, `wsi/2024/1337/made.cy.akn.gz` | Elections and Elected Bodies (Wales) Act 2024 (Commencement No. 1) Order 2024, WSI 2024/1337, as made (never revised), in English and in Welsh (retrieved 2026-10-18) |

The `.akn` responses are stored gzip-compressed (`*.akn.gz`, byte-for-byte identical when
decompressed); the tests decompress them into a temporary cache. `*.json` files record the
URL and retrieval time.

Contains public sector information licensed under the Open Government Licence v3.0.
