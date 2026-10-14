# legislation.gov.uk test fixtures (real UK legislation)

Unmodified responses from https://www.legislation.gov.uk, cached by the
`legislation_gov_uk` connector (same layout as `var/sources/legislation.gov.uk`):

| File | Content |
|---|---|
| `ukpga/1968/60/current.akn.gz` | Theft Act 1968, revised, current point in time |
| `ukpga/1968/60/2001-10-01.akn.gz` | Theft Act 1968 as it stood on 1 October 2001 (before the Fraud Act 2006 repeals) |
| `ukpga/1998/42/current.akn.gz` | Human Rights Act 1998, revised, current point in time |

The `.akn` responses are stored gzip-compressed (`*.akn.gz`, byte-for-byte identical when
decompressed); the tests decompress them into a temporary cache. `*.json` files record the
URL and retrieval time.

Contains public sector information licensed under the Open Government Licence v3.0.
