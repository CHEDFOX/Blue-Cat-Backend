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

The `.akn` responses are stored gzip-compressed (`*.akn.gz`, byte-for-byte identical when
decompressed); the tests decompress them into a temporary cache. `*.json` files record the
URL and retrieval time.

Contains public sector information licensed under the Open Government Licence v3.0.
