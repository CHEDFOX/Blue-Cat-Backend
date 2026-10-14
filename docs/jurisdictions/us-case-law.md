# United States (federal): case law from GovInfo

Status: **two sources active (pilot, draft unverified profile 0.3.0), five examined and not used.** Supreme Court
decisions (United States Reports, volumes 2-586) and opinions of the thirteen courts of appeals, from GovInfo, the
U.S. Government Publishing Office. Research aid, not legal advice. Legislation: [us-code.md](us-code.md).

| | |
|---|---|
| Sources | `us-govinfo-usreports` and `us-govinfo-uscourts` (active, approved), manifests `config/manifests/us-govinfo-{usreports,uscourts}.yaml` |
| Code | `connectors/govinfo.py` (robots.txt + PoliteHttp), `govinfo_usreports.py`, `govinfo_uscourts.py`; parsers of the same names, `parsers/pdf_text.py` |
| Profile / citations | `config/jurisdictions/us.yaml` (0.3.0), `ulis/jurisdictions/modules/us.py` (1.2.0) |
| Tests | `tests/test_govinfo_usreports.py`, `test_govinfo_uscourts.py`, `test_us_case_law_sources.py`; fixtures in `tests/fixtures/govinfo_*` |

## Sources examined (2026-10-14, robots.txt read before anything else from each host)

| Source | robots.txt / terms | Decision |
|---|---|---|
| **GovInfo** (www.govinfo.gov) | robots: no Crawl-delay, only /core/, /profiles/, search and admin paths disallowed, a sitemap per collection. `/about/policies`: 17 U.S.C. 105, "all work of the United States Government" in the public domain, though "publications may contain copyrighted material". `/sitemaps`: "can be used by systems to crawl and harvest content". The JSON API needs an api.data.gov key (not used); USREPORTS and USCOURTS are not in the Bulk Data Repository. | **Ingested** by the keyless route: sitemap, `/metadata/pkg/<pkg>/mods.xml`, `/content/pkg/...` |
| Supreme Court (supremecourt.gov) | robots: /images/, /rss/, /cdn/ disallowed, Crawl-delay 1, two named crawlers barred; policies page has no reuse terms. Opinions only as PDF (slip opinions, preliminary prints 588-606, bound volumes 502-587). | `planned` (`us-supremecourt-gov-opinions`): open to robots, not built (PDF layout) |
| CourtListener / Free Law Project | `robots.txt` answered **HTTP 403** (CloudFront "Request blocked") twice. Bulk-data page, licence and S3 files not read or fetched. | `planned` (`us-courtlistener-bulk`), no workaround |
| Cornell LII | robots: Crawl-delay 10. Terms: compilation and markup CC BY-NC-SA 2.5, commercial redistribution not consented; no explicit bot permission. | `rejected` (`us-cornell-lii`) |
| Justia | robots allow all; terms of service **HTTP 403**. | `planned` (`us-justia-supreme-court`) |
| Caselaw Access Project | CC0 data, host robots.txt disallows all (previous worker). | `planned` (`us-caselaw-access-project`) |

## What is ingested

- **United States Reports** (`us-govinfo-usreports`): the manifest lists all 584 volumes GovInfo has (2-586; none for 1 and
  465). Per volume: the package MODS (every case: title, `410 U.S. 113`, decision date, first page) and the "Text rendition"
  (OCR text of the whole volume) or, where the package links none (found from about 520), the volume PDF (about 4 MB, text
  layer, read with `pdftotext`). The parser cuts each case out between the running heads of its first page and the next
  case's, with a caption check (`CERTIORARI TO ...`, a word of the case name); a case it cannot cut out with confidence is
  left out and `ext.volume_cases_located` / `volume_cases_listed` say how many were found. Older layouts (volume 100 and
  most of the 19th century) are not recognised.
- **Units are printed pages** (US opinions have no numbered paragraphs): `num` is the page as printed (`153`, or `153-154`
  when a running head is missing in the OCR), `part` the running head's label (`Syllabus`, `Opinion of the Court`,
  `Douglas, J., dissenting`; over 32 characters it is shortened to `concurring`/`dissenting`/`appendix` and the full label
  is in `ext.long_part_labels`), `text` the page's lines in source order without the running head, page number, printer's
  job mark or typesetter's slugs. Footnotes stay at the foot of the page where the OCR puts them. OCR errors are kept
  (lost "fi"/"fl" ligatures: "defnition"); in PDFs soft hyphens are joined and ligature glyphs expanded.
- **Courts of appeals** (`us-govinfo-uscourts`): the manifest lists all 313 court-year sitemaps of the 13 courts (1988 to
  2026, newest first); per package the MODS and the PDF of each opinion. One unit per PDF page (no pinpoint: the PDF page is
  not the slip opinion's page). PDFs without text layer (scans, e.g. the First Circuit in 2001) are skipped, not OCR'd.
  District, bankruptcy and specialised courts (155 more codes) are not in the profile and not listed.
- **Citations** come from the sources: `Roe v. Wade, 410 U.S. 113, 153 (1973)` (reporter citation = MODS `usCitation`, year
  of the MODS decision date; 47 of the 789 MODS dates are `YYYY-01-01`, flagged in `ext.decision_date_caveat`);
  `Kolenc v. HHS, No. 23-1808 (Fed. Cir. Jan. 3, 2024)` (docket from the MODS; no reporter citation exists in the source).
  Search labels the unit `p. 153` / `PDF p. 2` through a new module hook. **Judicial treatment is not in the data and is never
  inferred**; no statute or case links are made.

## Numbers (2026-10-14, sandbox slice; the owner's server runs the full manifests)

- Download: 22 USREPORTS volumes (100, 150, 200, 250, 275, 300, 330, 350, 365, 380, 410, 420, 450, 480, 500, 520, 540, 550,
  560, 570, 580, 586) and 7 court-year sitemaps with 5 packages each (ca13, caDC, ca9 2024; ca1 2001; ca2 2023; ca5 2024; ca11 2023).
- `ingest us-govinfo-usreports`: 22 documents, 21 new, 1 skipped (volume 100: layout not recognised), 0 errors; **789
  judgments, 13,318 pages indexed**; of the 951 cases listed in the 21 volumes 789 (83%) were cut out (61-100% by volume;
  the six PDF volumes 520-580: 148 of 160).
- `ingest us-govinfo-uscourts`: 40 opinions, 35 new, 5 skipped (First Circuit 2001 scans), 0 errors; **35 judgments, 569 pages**.
- `verify-sources`: 56 pass, 0 fail. Full-manifest size: USREPORTS about 1,170 requests and 2 GB (about 2 h of polite waiting);
  USCOURTS tens of thousands of opinions at two requests each (weeks; resumable from the cache, `limit_per_sitemap` slices).
- Searches (`jurisdiction US`): "Ninth Amendment reservation of rights ... terminate pregnancy" -> Roe v. Wade, 410 U.S. 113, 153;
  "partial-birth abortion" -> Gonzales v. Carhart, 550 U.S. 124, 141; "Hatch Act political activity ..." -> United Public Workers v.
  Mitchell, 330 U.S. 75, 82; "Consumer Safety Officer removal ..." -> Kolenc v. HHS (Fed. Cir.). Labels `precedent_vertical` (Supreme
  Court), `binding_precedent` for the Federal Circuit with `forum_court US-CAFC`, `persuasive_precedent` otherwise.
- Checks: `ruff check`, `ruff format --check` and `mypy ulis` clean; full suite against `ulis_test_usgov`: **786 passed** (741
  before; 45 new tests in the three US case-law test files).

## Known gaps

- No Supreme Court decision after volume 586 (2018 Term), so a later decision that overruled or limited an ingested case is
  absent; check later history elsewhere. Volume 1 and 465 are missing at GovInfo. Many cases of older volumes, the first and
  last case of a volume (often) and cases sharing a first page are not cut out. No district or state courts, no unpublished/
  published flag (the clerk's docket text is stored as given). OCR text is not authentic; PDF page != slip opinion page.
- Needs `pdftotext` (poppler-utils) on the ingesting machine for PDF volumes and all USCOURTS opinions.
- A core addition: `JurisdictionModule.judgment_unit_label` (default "para N") so US hits say `p. 153`; no schema change.
