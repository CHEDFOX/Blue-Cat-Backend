# New Zealand (NZ)

Status: **planned, blocked - nothing ingested.** Profile `NZ` (draft_unverified, `corpus_status: none`)
and source `nz-pco-legislation` (status `planned`, licence `pending_review`) exist so that search
answers honestly ("no corpus") and the gate refuses ingestion. No connector, parser, fixtures or
citation module were written: no real document could be fetched to build or test them against.

## What was checked (2026-10-14, before any ingestion)

Identifying `ULIS-research-bot/0.1` user agent; eight requests (the API root also gave 404).

| URL | Result |
|---|---|
| `https://api.legislation.govt.nz/robots.txt` | HTTP 404 (no robots rules) |
| `https://api.legislation.govt.nz/docs/` | HTTP 200, the Legislation API v0 documentation (read) |
| `https://api.legislation.govt.nz/v0/works/act_public_1990_109/versions/` | HTTP 401 `{"error":"API key is required"}` |
| `https://www.legislation.govt.nz/robots.txt` | HTTP 202, `x-amzn-waf-action: challenge`, empty body (AWS WAF bot challenge) |
| `https://www.pco.govt.nz/robots.txt` | HTTP 403, Azure WAF JS challenge |
| `https://www.data.govt.nz/robots.txt`, `.../use-data/showcase/parliamentary-counsel-office` | Incapsula bot challenge / empty page |

No challenge was passed or attempted; no other identity, proxy or mirror was used.

## Access

- The official API (<https://api.legislation.govt.nz/docs/>, "Version Zero", may change) serves metadata
  only: works (`act_public_1990_109`), versions (`act_public_1990_109_en_2022-08-30`; each consolidation
  of an Act is a new version) and links to formats (xml, html, pdf).
- Every `/v0` endpoint needs an API key (header `X-Api-Key` or `api_key`). Limits: 10,000 requests per
  key per day; 2,000 per IP per five minutes. Per web search results quoting the PCO developer page
  (not read by ULIS), a key is issued on request by email to **contact@pco.govt.nz** after accepting the
  API terms, and is personal and confidential. ULIS has no key.
- The XML itself is linked on `www.legislation.govt.nz/.../en/<date>.xml/`, the host that answers bots
  with a challenge. So even a key may not allow ingestion; the PCO should also be asked whether an
  identified research client may fetch the XML, or whether a bulk export exists.

## Licence and legal status (NOT verified)

The API documentation states no licence. The terms of use
(<https://www.legislation.govt.nz/learn-more/legislation-data/api-terms-of-use/>) and the copyright
statement could not be read. Second-hand only (search snippets): the data is free to reuse; the PCO
showcase on data.govt.nz is said to state there is no copyright in Acts, Bills and regulations. The
brief expected CC BY 4.0 - **not confirmed**, so the source records `UNVERIFIED`, every permission
`unknown`, and no attribution wording. Under the Legislation Act 2019 the website is said to carry the
official version, declared per publication - not verified; the source uses the conservative
`official_non_authentic` until it is read.

## What a person must do to unblock

1. Read the terms pages and copyright statement in a browser; record exact wording and URLs.
2. Request an API key (contact@pco.govt.nz) and ask if the XML endpoints may be fetched by an identified client.
3. Then build the connector (versions from the API, XML from the format links) and a parser for the PCO
   XML (not AKN), using `ulis/ingestion/snapshots.py` unless the XML dates provisions.

## Planned sample (not fetched; work ids not verified)

New Zealand Bill of Rights Act 1990 (id `act_public_1990_109` appears in the API docs), Crimes Act 1961,
Contract and Commercial Law Act 2017, Privacy Act 2020, Employment Relations Act 2000, and Companies
Act 1993 or Consumer Guarantees Act 1993. Citation convention to implement: `Privacy Act 2020, s 22`,
`Crimes Act 1961, s 128(1)(a)`.

## Evidence (2026-10-14)

- `validate-config`: ok, 32 jurisdictions, 27 sources, 4 concepts.
- `ruff check`, `ruff format --check`, `mypy ulis`: clean (101 source files).
- Full suite against `ulis_test_nz`: **422 passed** (3 new tests in `tests/test_nz_planned.py`: gate
  refuses the planned source, no connector/parser registered, profile stub; `tests/test_catalog.py`
  lists `NZ`).
- Dev DB `ulis_nz`: `migrate`, `sync-catalog` (32 / 27 / 4) ok; `ingest nz-pco-legislation` refused ("source
  status is 'planned'; licence review status is 'pending_review'; ... no connector/parser configured");
  `verify-sources` has nothing to verify (0 documents).
- Search `privacy agency collection of personal information --jurisdiction NZ`: 0 results, coverage
  block reports `corpus_status: none`, 0 instruments, and the known gaps above.

## Known gaps

Everything: no legislation, case law, history, te reo Maori text, secondary legislation, bills or
citation module; licence and authoritative status unverified. The profile (common law, unitary, dualist,
parliamentary sovereignty; Supreme Court, Court of Appeal, High Court; `en`, `mi`; no memberships) is
research notes, all "to verify".
