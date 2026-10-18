# United Kingdom (legislation.gov.uk) - `GB`, `GB-ENG`, `GB-SCT`, `GB-WLS`, `GB-NIR`

Status: **every Act, Measure and Order in Council that legislation.gov.uk holds as XML (7,178 works), and every statutory
instrument and rule it holds as XML (83,387: 17,931 revised, 65,456 never revised and in force as made unless revoked), each
with the versions the site gives, in English and - for Welsh legislation - in Welsh**, profile `draft_unverified`. Court
decisions are in [gb-case-law.md](gb-case-law.md); the whole market is summarised in [../markets/uk.md](../markets/uk.md).
Listings and slice runs of 2026-10-18 (completeness audit: never-amended instruments, Welsh texts, manifests refreshed).

| | |
|---|---|
| Sources / manifests | `gb-legislation-gov-uk` (7,178 works, 100 with a Welsh text), `gb-legislation-gov-uk-secondary` (17,931 revised instruments, 583 with a Welsh text), `gb-legislation-gov-uk-secondary-made` (65,456 as made, 2,992 with a Welsh text; loaded by default, last); `config/sources/<id>.yaml`, `config/manifests/<id>.yaml`, all written by `scripts/build_legislation_gov_uk_manifest.py` (listing of 2026-10-18) |
| Connector / parser | `ulis/sources/connectors/legislation_gov_uk.py` / `ulis/sources/parsers/legislation_gov_uk.py` (`legislation_gov_uk_akn` 1.4.0); citations `ulis/jurisdictions/modules/gb.py` 1.2.0 (English and Welsh) |
| Tests | `tests/test_legislation_gov_uk.py`, `_wide.py` (committed manifests), `_versions.py` (connector), `_history.py` (real versions, database), `_welsh.py` (Welsh texts, as-made instruments, load order; database); fixtures in `tests/fixtures/legislation_gov_uk` |

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

The listing feeds (`/<type>/data.feed?results-count=1000&page=N`, 209 requests for all types on 2026-10-16, 213 on 2026-10-18) give each item's path, its latest
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
| `uksi` / `ssi` / `wsi` / `nisr` instruments / `ukci` Church Instruments | 109,382 / 11,454 / 6,771 / 18,553 / 60 | revised 9,868 / 2,135 / 1,575 / 4,293 / 60; as made 50,700 / 6,589 / 3,090 / 5,077 | 48,814 / 2,730 / 2,106 / 9,183 |

Listing of 2026-10-18 (213 feed pages, all fetched that day at 5 s intervals; the run cut by a container restart resumed from its cache): the
primary manifest has 7,178 works (6,252 with history, 926 as enacted or made), as on 2026-10-16; the secondary 17,931 (was
17,878: 53 instruments revised for the first time or newly published); the as-made 65,456 (was 65,505: 49 fewer, instruments the
site has since revised move to the secondary manifest, new ones come in). PDF-only items: 36,725 primary (13,167 `ukpga`, 19,972
`ukla`, 869 `ukppa`, 273 `gbla`, 2,160 `apgb`, 28 `aep`, 35 `aip`, 1 `apni`, 133 `nisi`, 87 `ukcm`) and 62,833 secondary (48,814
`uksi`, 2,730 `ssi`, 2,106 `wsi`, 9,183 `nisr`), among them the Northern Ireland Statutory Rules before 1996 and the Local Acts
before 1991 that are still in force; they are counted in the manifest headers and stay out (robots.txt disallows `*/data.pdf`, no OCR).
Acts before 1963 use regnal-year paths (`ukpga/Eliz2/10-11/58`, `aosp/1540/SessMar14/37`); "1859 c. 1" names two Acts, so work
URIs keep the path form. Old Scottish Acts that have no sections become one provision (`body`) with the Act's text.

## Never-amended statutory instruments are law in force (`gb-legislation-gov-uk-secondary-made`)

An instrument the site holds in one version only ("as made", a `data.akn` link under `.../made`) has never been revised by the
site: its as-made text is the law in force unless the instrument has been revoked - the same reasoning as Australia's
never-amended Acts. The source was filed under "history / old editions" (`load_by_default: false`) until 2026-10-18; it is now
`load_by_default: true` and `load-all` runs it after the two other UK legislation sources (same host, so one request every 5 s; it
is the largest and the plan orders sources by size). The parser gives such a document `consolidation_kind: as_made`,
`text_origin: official_conversion`, provisions with no start or end date (`valid_from None`) and, since 1.4.0, no currency date (the
site's `dc:modified` is the date of the record, not a date to which the text is up to date). A search hit on one is classified
"In force at the source date; applicability on the requested date unverified" (`applicability_unverified`) with the limitations
`EFFECTIVE_DATE_UNKNOWN` ("The source does not state when this version took effect") and `CURRENCY_UNKNOWN` ("... (as made text)"):
status as made, revocation unknown. Whether an instrument has been revoked is not recorded by the site for these texts; some titles
carry "(revoked)", kept as given.

**Quick go-live switch** (documented in the descriptor): `load-all --skip gb-legislation-gov-uk-secondary-made` leaves the 95-hour
source out of a first load without any file change; or set `load_by_default: false` in
`config/sources/gb-legislation-gov-uk-secondary-made.yaml` (load-all then skips it unless `--include-history` or `--only` names
it). Load it afterwards with `python -m ulis.cli ingest gb-legislation-gov-uk-secondary-made` or
`scripts/ingest_in_batches.py gb-legislation-gov-uk-secondary-made --batch 50` (resumable).

## Welsh texts

Welsh legislation is published on the site in English and in Welsh, two texts of equal standing (Government of Wales Act 2006,
s 156(1)). The listing feeds mark the Welsh text with an `application/akn+xml` link carrying `hreflang="cy"`; the manifest builder
writes `welsh: true` (`+cy` in the compact form) for such works and the connector fetches the Welsh expression of every version it
fetches (`.../welsh/data.akn`, `.../made/welsh/data.akn`, `.../<date>/welsh/data.akn`; cache `*.cy.akn`; one more request per
version). Works marked on 2026-10-18: all 100 Welsh Acts and Measures (`anaw` 44, `asc` 34, `mwa` 22), 583 of the 1,575 revised
Welsh Statutory Instruments, 2,988 of the 3,090 as-made ones, and 4 UK Statutory Instruments (`uksi/2005/2746`, `uksi/2000/2413`,
`uksi/1999/2357`, `uksi/1996/595`): 3,675 works, 3,675 more requests with `current_only` (about 5.1 hours). 992 revised Welsh
instruments (and 102 as-made ones) list no Welsh text of their latest version: the site revised them in English only (921 of them
still list the Welsh text of the as-made version, `contents/made/welsh`), and it answers `.../welsh/data.akn` for such a version
with the **English** text (checked on `wsi/2019/432`, 2026-10-18: `FRBRlanguage eng`, `dc:language en`), so they are not marked
and the connector drops an answer at a Welsh address whose `FRBRlanguage` is not `cym` (recorded in `fetch_errors`, nothing stored).

The parser reads `FRBRlanguage` (`cym`) and `dc:identifier` (`.../welsh`): the Welsh document has the same work URI and the same
eIds, `num_path`, dates and extents as the English one (checked on all 24 provisions of `anaw/2020/3`), language `cy`, expression
URI `/gb/anaw/2020/3/cym@2022-03-21`, `authoritative: true` for both languages of Welsh legislation (`anaw`, `asc`, `mwa`, `wsi`;
other types stay `false` as before, English being their only language), the site's own Welsh title as `short_title` (nothing is
translated; the ULIS label "(as at <date>)" on a revised version's title stays in English). Citations in Welsh (`language="cy"`)
use the Welsh title and the subdivision words in full - `Deddf Plant (Diddymu Amddiffyniad Cosb Resymol) (Cymru) 2020, adran 1(1)`,
`Rheoliadau ..., OS 2024/1337, rheoliad 2(1)`, `Atodlen 1, paragraff 2` (`SI` becomes `OS`, Offeryn Statudol; `SSI`/`SR` keep their
names) - formatting conventions to be confirmed by a Welsh-speaking reviewer; English citations are unchanged. PostgreSQL has no
Welsh text-search configuration, so Welsh passages are indexed and queried with `simple` (exact word forms; stated in the known gaps
and in every source's `known_limitations`). A search with `query_language="cy"` finds the Welsh text with the Welsh citation and the
limitation `EQUALLY_AUTHENTIC_VERSIONS` ("Equally authentic language versions exist (en)"); the English text of the same provision
is its parallel version.

## How the versions are made

Each point-in-time document is a whole-instrument text; the parser reads the site's own validity periods
(`validity_basis="source_periods"`). The earliest version of a work made before the site's revised data begins starts on that date:
1991-02-01 for UK Acts and Church Measures (`BASE_DATES`), 2006-01-01 for NI items; text repealed before then is not on the site. A work held only as enacted/made is one version with no start
date (`EFFECTIVE_DATE_UNKNOWN`). The as-enacted text of a work that also has history is **not stored** (the pipeline refuses a second
expression with the same URI and the dates would overlap); `#made` in a lifecycle is skipped. `derive_valid_to` (shared, `ulis/temporal/validity.py`)
now ends a version at the next version that starts strictly later; two versions on one date produced a zero-length interval that the
database refused (found on the Licensing (Scotland) Act 2005).

## Scale (full load per source; 5 s per request, one request at a time on the one host; sizes from the 2026-10-16 slice, plus or minus 50 percent)

| Source | Works | Requests (committed `current_only`) | Hours | Requests with every version | Hours | Provision versions | Database | Raw |
|---|---|---|---|---|---|---|---|---|
| `gb-legislation-gov-uk` | 7,178 (100 Welsh) | 7,178 + 100 = 7,278 | 10.1 | about 28,200 + 400 Welsh = 28,600 | 40 | 2.75 million | 10.7 GB | 9.2 GB |
| `gb-legislation-gov-uk-secondary` | 17,931 (583 Welsh) | 17,931 + 583 = 18,514 | 25.7 | about 48,700 + 1,500 Welsh = 50,200 | 70 | 1.18 million | 4.6 GB | 2.4 GB |
| `gb-legislation-gov-uk-secondary-made` | 65,456 (2,992 Welsh) | 65,456 + 2,992 = 68,448 | 95.1 (4.0 days) | the same (one version each) | 95.1 | 2.31 million | 5.8 GB | 1.8 GB |
| all three, as `load-all` runs them (one after the other) | 90,565 | 94,240 | 130.9 (5.5 days) | about 147,000 | 205 | 6.2 million | about 21 GB | 13.4 GB |

Request counts are the manifests' own (one per work and language, plus the few versions that answer 404 once); hours are
requests x 5 s. Use `scripts/ingest_in_batches.py <source> --batch 50` for a big source (resumable).

## Slice run (dev database `ulis_m_gb`, 2026-10-16)

Stratified, seed 7: 35 primary works (28 with history, 7 as enacted/made; `ukpga` including repealed and 1988-1990 Acts, `aep`,
`aosp`, `apgb`, `aip`, `apni`, `nia`, `nisi`, `ukcm`, `asp`, `anaw`, `asc`, `mwa`), 18 revised instruments, 25 as-made ones. Primary: 123
requests, 111 documents, 110 new, 1 skipped (`aosp/1690/61`, empty body), 12,086 provision versions created, 61,912 corroborated, 42 restated,
47 links, 0 errors, 65 s. Revised instruments: 43 documents, 1,191 provision versions, 195 links. As made: 25 documents, 882 provision versions.
Samples: Gaming (Amendment) Act 1990 (repealed) is valid 1991-02-01 to 2007-09-01; Community Legal Service (Funding) Order 2000, SI 2000/627
(revoked) 2000-04-01 to 2007-10-01; NI Statutory Rule 1998/128 as made.

## Slice run (dev database `ulis_gb`, 2026-10-18; `scripts/ingest_in_batches.py --manifest <slice>`)

Stratified, seed 7 (`var/slices/*.yaml`): 30 as-made instruments (15 `uksi` including `uksi/2005/2746`, one of the four UK instruments
with a Welsh text, 5 `ssi`, 5 `wsi`, 5 `nisr`; 6 with a Welsh text), 7 Welsh Acts and Measures (`anaw/2013/5`, `anaw/2015/3`,
`anaw/2019/4`, `asc/2025/2`, `asc/2026/9`, `mwa/2009/3`, `mwa/2011/3` (repealed); `current_only`, both languages) and 3 revised Welsh
instruments (`wsi/2020/1614`, `wsi/2019/414`, `wsi/2022/666`; `current_only`, both languages). Results: as made 36 documents (30 English,
6 Welsh), 30 instruments, 36 instrument versions, 1,527 provision versions, 454 citation links, 0 errors, 176 s (36 requests); Welsh Acts
14 documents, 7 instruments, 4,953 provision versions, 147 links, 0 errors, 72 s; Welsh instruments 6 documents, 3 instruments, 574
provision versions, 137 links, 0 errors, 28 s. Database after the slices: 56 source documents (40 English and 16 Welsh instrument
versions; 36 `as_made`, 20 `point_in_time`), 7,054 provision versions, 30 MB. `verify-sources`: 56 pass, 0 fail. Searches (`var/slices/evidence.py`, as of 2026-10-18):

| Query (`jurisdiction`, `query_language`) | First hit | Authority | Limitations (codes) |
|---|---|---|---|
| "hygyrchedd cyfraith Cymru" (GB-WLS, cy) | `Deddf Deddfwriaeth (Cymru) 2019, adran 1(1)` (cy, point in time), "Rhaid i’r Cwnsler Cyffredinol gadw hygyrchedd cyfraith Cymru o dan adolygiad."; parallel version `en`, authoritative; lookup `provision_at(..., language="cy")` gives the same citation and text, `available_languages` `[cy, en]`, the English lookup `Legislation (Wales) Act 2019, s 1(1)`, both valid from 2021-01-23 | binding legislation | REVISED_TEXT, EQUALLY_AUTHENTIC_VERSIONS, CURRENCY, OUTSTANDING_CHANGES_INSTRUMENT, PROFILE_UNVERIFIED, ... |
| "accessibility of Welsh law" (GB-WLS, en) | `Legislation (Wales) Act 2019, s 1(1)` (en, point in time), "The Counsel General must keep the accessibility of Welsh law under review."; parallel version `cy`, authoritative | binding legislation | REVISED_TEXT, EQUALLY_AUTHENTIC_VERSIONS, CURRENCY, OUTSTANDING_CHANGES_INSTRUMENT, PROFILE_UNVERIFIED, ... |
| "rheoliadau cymru" (GB-WLS, cy) | `Rheoliadau Gwastraff (Cymru) (Diwygiadau Amrywiol) (Ymadael â’r UE) 2019, OS 2019/414, rheoliad 8(1)` (cy, point in time), "Mae Rheoliadau Gwastraff Peryglus (Cymru) 2005 wedi eu diwygio fel a ganlyn."; parallel version `en` | binding legislation | REVISED_TEXT, EQUALLY_AUTHENTIC_VERSIONS, CURRENCY, ... |
| "commencement order" (GB, en) | `The Welfare Reform and Pensions Act 1999 (Commencement No. 1) Order 1999, SI 1999/3309, art 1(1)` (en, as made) | `applicability_unverified`: "In force at the source date; applicability on the requested date unverified" | EFFECTIVE_DATE_UNKNOWN, CURRENCY_UNKNOWN ("(as made text)"), NOT_AUTHENTICATED, PROFILE_UNVERIFIED, ... |
| "gorchymyn cychwyn" (GB-WLS, cy) | `Gorchymyn Tatws o Wlad Pwyl (Hysbysu) (Cymru) 2004, OS 2004/2697, erthygl 1(1)` (cy, as made), "Enw'r Gorchymyn hwn yw ... a daw i rym ar 20 Hydref 2004."; parallel version `en` | `applicability_unverified` | EFFECTIVE_DATE_UNKNOWN, CURRENCY_UNKNOWN, EQUALLY_AUTHENTIC_VERSIONS, NOT_AUTHENTICATED, ... |

Stored expressions, for example: `/gb/anaw/2019/4/eng@2026-01-01` and `/gb/anaw/2019/4/cym@2026-01-01` (titles "Legislation (Wales) Act
2019" / "Deddf Deddfwriaeth (Cymru) 2019", both authoritative); `/gb/wsi/2021/1345/eng@2021-11-29` and `.../cym@2021-11-29` (as made, no
currency date); `/gb/uksi/2005/2746/eng@2005-09-28` (authoritative false, as before for `uksi`) and `.../cym@2005-09-28` (authoritative
true): the English text of a UK instrument made in both languages keeps the flag of its type. No fetch error in any run (no Welsh text
missing for a marked work in the slices). Simple-configuration caveat seen in the run: a Welsh query must use the exact word forms of
the text (no stemmer): "cyfraith Cymru hygyrch" found nothing where the text says "hygyrchedd cyfraith Cymru", which then found it.

## Known gaps

- 99,558 items exist on the site only as PDF (36,725 primary: local and private Acts, most Acts before 1988 that were not in force on
  1991-02-01, 133 NI Orders in Council, 87 Church Measures; 62,833 secondary: local and non-print instruments, most instruments before
  1987, the Northern Ireland Statutory Rules before 1996); `*/data.pdf` is disallowed and ULIS does no OCR. They are counted in the
  manifest headers, not listed.
- Amendments are recorded by the site from 1994 on (earlier only for EU-related changes): older versions may miss earlier amendments.
  Outstanding effects are reported as limitations, not applied. The text is the site's editorial consolidation, not the authentic version.
- As-made instruments: status as made, revocation unknown (above). Their Welsh texts (and all Welsh texts) are searched with the
  `simple` configuration, no Welsh stemmer; 992 revised Welsh instruments (and 102 as-made ones) have no Welsh text of their current version on the site (revised in English
  only; the Welsh text of their as-made version, which the site keeps, is not fetched because it is not the text in force); the
  English text of the 4 UK instruments that also have a Welsh text keeps `authoritative: false` (the parser knows the pair only from
  the Welsh document).
- No as-enacted original next to the history of the same work; `aosp/1690/61` has no text.
