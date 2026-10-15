# United States federal: GovInfo bulk data (Statutes at Large, public laws, compilations, annual CFR, Federal Register)

Status: **draft unverified profile** (US profile 0.4.0). Two active sources, both GPO's USLM/CFR XML from the GovInfo
Bulk Data Repository. The manifests list the whole open scope; the sandbox run is a slice. Research aid, not legal advice.

| | `us-govinfo-bulk-statutes` | `us-govinfo-bulk-cfr-fr` |
|---|---|---|
| Manifest | `config/manifests/us-govinfo-bulk-statutes.yaml`: 141 files (Statutes at Large volumes 1-138, `PLAW-119` public and private zips, `COMPS.zip`) | `config/manifests/us-govinfo-bulk-cfr-fr.yaml`: 1,479 files (1,421 CFR title-years 1996-2026, oldest first; 58 Federal Register months 2022-01 to 2026-10) |
| Connector | `govinfo_bulk_statutes` (`GovInfoStatutesConnector`) | `govinfo_bulk_cfr_fr` (`GovInfoCfrFrConnector`), both in `ulis/sources/connectors/govinfo_bulk.py` |
| Parser | `uslm` 1.3.0 (`statutesAtLarge`, `pLaw`, `statuteCompilation` handlers) | `govinfo_cfr_fr` (`cfr_annual.py`, `federal_register.py`) |
| Tests | `tests/test_govinfo_bulk_statutes.py` (23) | `tests/test_govinfo_cfr_fr.py` (21) |

Manifests are written by `scripts/build_govinfo_manifests.py` from the public directory listings. No SHA-256 pins:
the collections are live; the connector stores the SHA-256 of every file. Both are in `STATIC_PINS` (`ulis/ops/sources.py`).

## Source and licence (read 2026-10-15)

* [robots.txt](https://www.govinfo.gov/robots.txt): `User-agent: *` disallows only site internals; `/bulkdata/` is
  allowed; no Crawl-delay. The connectors apply it to every URL and wait 5 s between requests.
* [GovInfo policies](https://www.govinfo.gov/about/policies): "Title 17, Section 105, United States Code, provides that:
  Copyright protection under this title is not available for any work of the United States Government"; "public
  documents can generally be reprinted without legal restriction". Statutes files say in `dc:rights`: "Pursuant to
  Title 17 Section 105 ... this file is not subject to copyright protection and is in the public domain."
* [CFR XML user guide](https://www.govinfo.gov/bulkdata/CFR/resources/CFR-XML_User-Guide_v1.pdf) 1.2 and the
  [FR guide](https://www.govinfo.gov/bulkdata/FR/resources/FDsys_OFR-XML_User-Guide-v1.pdf): "there are no restrictions
  on re-use ... because U.S. Government works are not subject to copyright"; the XML is not the official edition and not
  digitally signed; the NARA seal and CFR logos may not be used. USLM files are "beta".
* Conclusion: both licences **approved** (`reviewed_by: ULIS project, 2026-10-15, from the publisher's notice`); counsel
  review still recommended. Not authenticated texts: `NOT_AUTHENTICATED` / `OFFICIAL_CONSOLIDATION_REPRODUCED`.

## What is ingested

* **Statutes at Large**: one instrument per law, `/us/pl/<congress>/<n>`, `/us/pvtl/...` where the volume gives the law
  number, else `/us/stat/<volume>/<page>`. Dated from approval (`enactment_date_presumed`); effective dates in the text
  are not tracked. PLAW only from the 119th Congress (earlier laws are in volumes 127-138, so none is stored twice).
* **Compilations** (`COMPS`): an Act as amended by the OLRC (`consolidation_kind = compilation`, `text_origin =
  unofficial_compilation`, limitation `COMPILATION_NOT_OFFICIAL`); under the Act's own URI, or `/us/comp/<fileId>` for the
  228 files without a law number; all 2,701 members of `COMPS.zip` parse. Statute versions: `as_enacted`, `official_conversion`.
* **CFR annual editions**: one instrument per part, `/us/cfr/t<title>/p<part>`, the same URIs and eIds as the eCFR
  (`us-ecfr`), `validity_basis = attested_at_edition` from the "revised as of" date: editions and the current eCFR are
  versions of one work (history). The pretty-printer's indentation is removed (454 of 454 shared provisions of title 12
  equal the eCFR's). Load order: editions oldest first, then the eCFR (`LOAD_AFTER` in `ulis/ops/loading.py`).
* **Federal Register**: final rules only, 2022-2026, one instrument per rule `/us/fr/<volume>/<page>`; provisions are
  summary, dates, contact, preamble and regulatory text (`EFFECTIVE_DATE_UNKNOWN`: the date is prose). Full daily
  archive since 2000 is about 25 GB; extend the manifest to add years.

## Results (sandbox, 2026-10-15, fresh database, three ingests in parallel on 4 cores)

* **Statutes slice** (volumes 1, 2, 10, 24, 32, 47, 66, 72, 100, 120, 138; PLAW-119 public and private zips; a COMPS.zip
  of 378 members, the 228 without a law number and 150 others): 509 documents, 10,384 instruments, **341,555 provision
  versions**, 302,159 passages, 34,661 links (305 resolved), 0 errors, 0 conflicts; 1,625 s, peak 768 MB.
* **CFR/FR slice** (11 CFR zips: 43/1996, 1 and 12/2001, 27/2005, 16/2010, 1 and 12/2012, 27/2018, 2/2022, 1 and 12/2025;
  5 FR months: 2022-03, 2023-07, 2024-11, 2025-05, 2026-10): 131 documents, 2,095 instruments (1,002 CFR parts, 1,093
  rules), **224,304 provision versions created**, 64,505 corroborated, 19,782 restated (history), 209,716 passages,
  0 errors; 1,543 s, peak 924 MB. Then `us-ecfr` title 1 on top: 28 parts, 1,612 corroborated, 13 restated.
* `verify-sources` on the whole database (all four federal sources): **721 of 721 pass** (53 US Code titles, 509
  statutes documents, 38 CFR volumes, 93 FR issues, 28 eCFR parts); 5 min 21 s, peak 1.6 GB.
* Tests: full suite **1261 passed** in 5 min 33 s; `ruff check`, `ruff format --check` and `mypy ulis` pass.
* Searches: "unlawful for an entity to distribute, maintain, or update a foreign adversary controlled application" ->
  `Pub. L. No. 118-50, § 2(a)(1), 138 Stat. 895 (2024)`; "Atlantic bluefin tuna angling category quota transfer" ->
  `91 Fed. Reg. 65,472 (Oct. 15, 2026), summary`; "Regulation T initial margin requirements ..." -> `12 C.F.R. § 221.3(a)(2) (2025)`.
* Full scale (extrapolated from the slices' rate of about 200 provision versions per second, not run): `COMPS.zip`
  alone parses to about 926,000 provisions (over an hour); the 1,421 CFR title-years and the FR months are a matter of
  days; downloads are about 2 GB (statutes) and 4 GB (CFR/FR). Database size for the full scope was not estimated.

## Known gaps

* Search ranks long amending Acts above short provisions (candidate budget 200 in the shared search; not changed).
* Compilations of the same Act under several file ids are separate instruments; an Act's amendments are not linked.
* Editions before 2000 are checked only on samples; tables are flattened; graphics are not reproduced.
* Concurrent resolutions, proclamations, treaties, proposed rules and notices are not ingested. Not reviewed by a US lawyer.
