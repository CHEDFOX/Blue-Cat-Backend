# United Kingdom (legislation.gov.uk) - `GB`, `GB-ENG`, `GB-SCT`, `GB-WLS`, `GB-NIR`

Status: **every Act, Measure and Order in Council that legislation.gov.uk holds as XML (7,178 works), and every statutory
instrument and rule it holds as XML (83,383), each with the versions the site gives**, profile `draft_unverified`. Court
decisions are in [gb-case-law.md](gb-case-law.md); the whole market is summarised in [../markets/uk.md](../markets/uk.md).

| | |
|---|---|
| Sources / manifests | `gb-legislation-gov-uk` (7,178 works), `gb-legislation-gov-uk-secondary` (17,878 revised instruments), `gb-legislation-gov-uk-secondary-made` (65,505 as made, `load_by_default: false`); `config/sources/<id>.yaml`, `config/manifests/<id>.yaml`, all written by `scripts/build_legislation_gov_uk_manifest.py` |
| Connector / parser | `ulis/sources/connectors/legislation_gov_uk.py` / `ulis/sources/parsers/legislation_gov_uk.py` (`legislation_gov_uk_akn` 1.3.0) |
| Tests | `tests/test_legislation_gov_uk.py`, `_wide.py` (committed manifests), `_versions.py` (connector), `_history.py` (real versions, database); fixtures in `tests/fixtures/legislation_gov_uk` |

## Licence and fair use (read 2026-10-15, re-read 2026-10-16)

- Everything is under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)
  (site footer); pre-1987 instruments contributed by Westlaw UK need "Westlaw UK derived from Crown Copyright material and
  contributed to legislation.gov.uk" besides the OGL attribution.
- [Fair use policy](https://www.legislation.gov.uk/fair-use-policy): identify the bot; follow
  [robots.txt](https://www.legislation.gov.uk/robots.txt) (`Crawl-delay: 5`; `*/data.pdf`, `*/data.docx`, `/defralex` disallowed, so only
  XML is fetched); at most 1,500 requests per 5 minutes (ULIS: 60). The connector stops on 401/403/451/429, never works around a
  refusal, records a missing work and goes on, and gives up after five failures in a row.
  `research.legislation.gov.uk` answers HTTP 401 for its robots.txt and is not used.

## What the publisher holds and what is listed

The listing feeds (`/<type>/data.feed?results-count=1000&page=N`, 209 requests for all types) give each item's path, its latest
version and its formats. A dated latest-version link means a revised text with point-in-time versions; `enacted`/`made` means one
version; an item with no `data.akn` link exists only as PDF (no text for ULIS, no OCR). Repealed and revoked items are listed.

| Type | Items on the site | With XML (revised / one version) | PDF only |
|---|---|---|---|
| `ukpga` UK Public General Acts (1801-) | 17,560 | 4,307 / 86 | 13,167 |
| `ukla` UK Local Acts | 20,171 | 17 / 182 | 19,972 |
| `ukppa` Private and Personal / `gbla` GB Local / `apgb` GB Acts (1707-1800) | 869 / 273 / 2,218 | 0 / 0 / 58 | 869 / 273 / 2,160 |
| `aep` English Parliament (1235-1706) / `aosp` Old Scots (1424-1707) / `aip` Old Irish | 692 / 109 / 46 | 79+585 / 109 / 11 | 28 / 0 / 35 |
| `apni` NI Parliament (1921-72) / `mnia` / `nia` NI Assembly / `nisi` NI Orders in Council | 288 / 2 / 238 / 691 | 287 / 2 / 226+12 / 497+61 | 1 / 0 / 0 / 133 |
| `ukcm` Church Measures / `asp` / `anaw` / `asc` / `mwa` | 244 / 402 / 44 / 34 / 22 | 157 / 402 / 44 / 34 / 22 | 87 / 0 |
| `uksi` / `ssi` / `wsi` / `nisr` instruments / `ukci` Church Instruments | 109,378 / 11,454 / 6,771 / 18,553 / 60 | revised 9,831 / 2,121 / 1,574 / 4,292 / 60; as made 50,733 / 6,603 / 3,091 / 5,078 | 48,814 / 2,730 / 2,106 / 9,183 |

The primary manifest has 7,178 works (6,252 with history, 926 as enacted or made); the secondary 17,878; the as-made 65,505.
Acts before 1963 use regnal-year paths (`ukpga/Eliz2/10-11/58`, `aosp/1540/SessMar14/37`); "1859 c. 1" names two Acts, so work
URIs keep the path form. Old Scottish Acts that have no sections become one provision (`body`) with the Act's text.

## How the versions are made

Each point-in-time document is a whole-instrument text; the parser reads the site's own validity periods
(`validity_basis="source_periods"`). The earliest version of a work made before the site's revised data begins starts on that date:
1991-02-01 for UK Acts and Church Measures (`BASE_DATES`), 2006-01-01 for NI items; text repealed before then is not on the site. A work held only as enacted/made is one version with no start
date (`EFFECTIVE_DATE_UNKNOWN`). The as-enacted text of a work that also has history is **not stored** (the pipeline refuses a second
expression with the same URI and the dates would overlap); `#made` in a lifecycle is skipped. `derive_valid_to` (shared, `ulis/temporal/validity.py`)
now ends a version at the next version that starts strictly later; two versions on one date produced a zero-length interval that the
database refused (found on the Licensing (Scotland) Act 2005).

## Scale (estimated from the slice; plus or minus 50 percent; 5 s per request)

| Manifest | Documents | Requests | Hours | Provision versions | Database | Raw |
|---|---|---|---|---|---|---|
| primary | 24,700 | 28,200 | 39 | 2.75 million | 10.7 GB | 9.2 GB |
| secondary (revised) | 42,700 | 48,700 | 68 | 1.18 million | 4.6 GB | 2.4 GB |
| secondary-made | 65,505 | 65,505 | 91 | 2.31 million | 5.8 GB | 1.8 GB |

Default loading (primary + secondary) is about 15 GB of database, the as-made source adds 6 GB and is off by default: run
`python -m ulis.cli ingest gb-legislation-gov-uk-secondary-made` (or `load-all --include-history`). Use
`scripts/ingest_in_batches.py <source> --batch 50` for a big source.

## Slice run (dev database `ulis_m_gb`, 2026-10-16)

Stratified, seed 7: 35 primary works (28 with history, 7 as enacted/made; `ukpga` including repealed and 1988-1990 Acts, `aep`,
`aosp`, `apgb`, `aip`, `apni`, `nia`, `nisi`, `ukcm`, `asp`, `anaw`, `asc`, `mwa`), 18 revised instruments, 25 as-made ones. Primary: 123
requests, 111 documents, 110 new, 1 skipped (`aosp/1690/61`, empty body), 12,086 provision versions created, 61,912 corroborated, 42 restated,
47 links, 0 errors, 65 s. Revised instruments: 43 documents, 1,191 provision versions, 195 links. As made: 25 documents, 882 provision versions.
Samples: Gaming (Amendment) Act 1990 (repealed) is valid 1991-02-01 to 2007-09-01; Community Legal Service (Funding) Order 2000, SI 2000/627
(revoked) 2000-04-01 to 2007-10-01; NI Statutory Rule 1998/128 as made.

## Known gaps

- About 100,000 items exist on the site only as PDF (local and private Acts, most instruments before 1987, local SIs); `*/data.pdf` is
  disallowed and ULIS does no OCR. They are counted in the manifest headers, not listed.
- Amendments are recorded by the site from 1994 on (earlier only for EU-related changes): older versions may miss earlier amendments.
  Outstanding effects are reported as limitations, not applied. The text is the site's editorial consolidation, not the authentic version.
- No as-enacted original next to the history of the same work; Welsh-language texts are not fetched; `aosp/1690/61` has no text.
