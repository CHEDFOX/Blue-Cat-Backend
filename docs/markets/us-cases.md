# United States, case law: the openly licensed body of judicial opinions

Status 2026-10-16: **three sources active** (GovInfo's United States Reports and courts of appeals, and the Free Law Project's CourtListener
bulk files), **five examined and not used**. Research aid, not legal advice. Legislation is in [../jurisdictions/us-code.md](../jurisdictions/us-code.md);
the first sweep of this area is in [../jurisdictions/us-case-law.md](../jurisdictions/us-case-law.md).

## Inventory (what main had, why, what was done)

| Collection | Source id | Status | Loaded / listed | What the publisher offers beyond that | Action |
|---|---|---|---|---|---|
| Supreme Court, United States Reports 2-586 (GovInfo) | `us-govinfo-usreports` | active | 584 volumes listed; the parser cuts out about 4 cases in 5 (OCR layout) | no vol. 1, 465; nothing after 586 U.S. | kept; gaps and the overlap with CourtListener documented |
| Courts of appeals (GovInfo USCOURTS) | `us-govinfo-uscourts` | active | 313 court-years of the 13 circuits listed (weeks of polite crawl) | 75 district courts, bankruptcy, special courts: 3,684 court-year sitemaps, no bulk file; `txsd` 2024 alone 1,317 packages = 3.7 h at 2 requests x 5 s | not widened (impractical); district courts come from CourtListener |
| CourtListener bulk files (Free Law Project) | `us-courtlistener-bulk-s3` | **planned -> active** | default scope about 144,000 of 10,174,551 cases (see Scale) | all ten million cases, 3,400 courts, 18 million citations | built: connector, parser, 580 courts; licence read; sized |
| CourtListener website | `us-courtlistener-bulk` | planned | none | | HTTP 403 to the ULIS bot (kept as evidence); never contacted again |
| Caselaw Access Project (static.case.law) | `us-caselaw-access-project` | planned | none | CC0 data, all reporters 1658-2020 | host's robots.txt still `Disallow: /` (re-tested 2026-10-16); the same cases come through CourtListener (`xml_harvard`) |
| Supreme Court site | `us-supremecourt-gov-opinions` | planned | none | bound volumes 561-587 and preliminary prints 588-606 as PDF; older volumes as scans | open to robots; CourtListener has the same decisions; a parser for the preliminary prints is the only missing piece |
| Justia | `us-justia-supreme-court` | planned | none | | terms page HTTP 403 again; nothing taken |
| Cornell LII | `us-cornell-lii` | rejected | none | | CC BY-NC-SA compilation; not re-requested |
| GovInfo "Supreme Court Decisions 1937-1975" (FLITE) | not a source | examined | none | 7,407 decisions of vols. 300-422, XML, bulk only, "not an official version" | duplicates vols. 300-422 and CourtListener; not added |

## What was added

* `ulis/sources/connectors/courtlistener_bulk.py`: downloads the five files of one generation (resumable, one connection, sizes pinned in the
  manifest), builds a SQLite index (clusters, dockets, citations, scope), streams the 55 GB opinions file through `lbzip2`/`bzip2` and a CSV
  reader and yields one bundle per case. Never contacts www.courtlistener.com. `ulis/sources/parsers/courtlistener_bulk.py`: Harvard XML and the
  HTML of the other collections to units; `scripts/courtlistener_scope_report.py` (counts by court and year), `scripts/build_us_courts.py`.
* US profile 0.5.0: 562 more courts (580 listed) (district, bankruptcy, special federal; State highest and appellate), generated from CourtListener's
  court table; US module 1.4.0 (citations of these cases).

## Scale (measured 2026-10-16; the sandbox was shared, load 8-14 on 4 cores, so times are upper bounds)

* **Publication** (generation 2026-09-30; `scripts/courtlistener_scope_report.py` over the full clusters and dockets files, 73.0 million dockets): 10,174,551
  cases in 3,361 courts; 923,861 blocked or undated (never taken); 9,250,686 left: Published 8,101,735, Unknown 830,181, Unpublished 316,275.
  The "Supreme Court" has 498,910 Published cases, but 97% are orders of under 500 characters ("Certiorari denied"); its real opinions are about 30,000.
* **Opinions file**: 55.25 GB bz2, about 350 GB of CSV, about 12 million opinion rows. 19,059 rows read at six places: 28 KB of CSV per row (several text columns), the text
  used 9.5 KB per opinion (Harvard XML 56% of rows, Columbia HTML 24%, plain text 10%, Lawbox 6%, court HTML 3%); modern opinions are longer (courts of appeals 30 KB and
  State supreme courts 27 KB since 2010; State appellate 9-13 KB).
* **Pipeline** (15,364 real cases of the sample through `ingest`, dev DB): 70,767 units (4.6 per case), 143.6 MB of text, 485 MB of tables = 3.4 bytes per byte of text
  (3.55 on a first run of 3,000), 31 KB per case, plus 10 KB of raw bundle on disk; 384 s = 40 cases/s, bound by one database round trip per unit.
  Reading: `bzip2`/`lbzip2` plus the CSV reader ran at 16-68 MB/s of CSV; downloading 63 GB at 6-40 MB/s; building the index 17 min (clusters) + 26 min (dockets) with `lbzip2`.

| Published cases (est.) | cases | text GB | database GB | units (M) |
|---|---|---|---|---|
| State appellate (SA) | 3,172,136 | 29.8 | 106 | 16.0 |
| State courts of last resort (S) | 2,422,157 | 30.4 | 108 | 16.3 |
| Courts of appeals (13 circuits) | 884,870 | 14.9 | 53 | 8.0 |
| District courts (FD) | 467,851 | 11.2 | 40 | 6.0 |
| Supreme Court (opinions only) | ~30,000 | 0.8 | 3 | 0.4 |
| State trial, special federal, others | ~656,000 | 7.1 | 25 | 3.9 |
| **All Published** | **~7.6 million** | **~94** | **~334** | **~51** |

All of it is more than the 100 GB server and about 70 hours of ingestion; **the default scope** (`config/manifests/us-courtlistener-bulk-s3.yaml`) is Supreme Court
opinions of 1,000 characters or more in every year, the 13 circuits from 2015, the 55 State courts of last resort from 2021 and district courts from 2021:
about 144,000 cases, 4.1 GB of text, 2.2 million units, **about 14.5 GB of database** (1.1 h of ingestion; the whole opinions file must still be read to find them).

**Widen** by editing the rules (year, courts or groups, `statuses`, `min_chars`); `only_rules` runs one rule at a time from the same index; roughly 100 GB of database
per million Published cases. A new quarter: change `generation` and the five file names and sizes. Court groups beyond those modelled: `scripts/build_us_courts.py --groups`.

## Licence facts (read 2026-10-15/16, robots.txt first; details in `config/sources/us-courtlistener-bulk-s3.yaml`)

* Bulk files: [wiki.free.law bulk-data page](https://wiki.free.law/c/courtlistener/help/api/bulk-data/bulk-legal-data) (robots allow `/c/`):
  "For developers, legal researchers, journalists, and the public, we provide bulk files", quarterly full snapshots in a public S3 bucket,
  PostgreSQL `COPY` CSV, schema and loader script with each generation; **"Our bulk data files are free of known copyright restrictions."**
  (Public Domain Mark 1.0). The bucket has no robots.txt (HTTP 404 = no restrictions); `free.law` allows everything. No attribution, rate or
  use condition is stated. The website `www.courtlistener.com` answered HTTP 403 to the bot and is never contacted.
* Caselaw Access Project: [Harvard LIL, 2024-03-26](https://lil.law.harvard.edu/blog/2024/03/26/transitions-for-the-caselaw-access-project):
  the access limits "expired this month, and that data can now be fully released without restriction on access or use"; "The Free Law Project
  already includes all CAP cases, as well as cases scraped from court websites, in its CourtListener". `static.case.law/robots.txt` is still
  `Disallow: /`, so it is not fetched.
* Federal opinions are works of the United States Government (17 U.S.C. 105, per [GovInfo's notice](https://www.govinfo.gov/about/policies));
  State opinions are treated alike by the publisher, which is an inference, not a finding. AMBER: counsel review before a commercial launch.
* Left out on purpose: `html_with_citations` (CourtListener's own citation links), `html_anon_2020` (a third-party database whose HTML carries
  editorial summaries), `xml_scan`, headnotes and summaries; cases CourtListener marks blocked (`blocked`, `date_blocked`, on cluster or docket).

## Remaining gaps

* Trial courts of the States (2,600 courts, mostly historical), tribal courts and administrative bodies are not modelled; unpublished and
  "unknown"-status opinions are outside the default scope (a rule's `statuses` takes them).
* Text is the publisher's aggregation, not authentic (OCR errors stay). Pinpoint pages only where the text carries the reporter's page breaks
  and the first one fits a citation of the case; other units are blocks (no pinpoint). No judicial treatment is inferred.
* Supreme Court decisions before 587 U.S. are in two sources (GovInfo's OCR and CourtListener) as two judgments; the CourtListener copy is the
  complete one (GovInfo's parser finds about 4 cases in 5 and fewer before 1900).
* The Supreme Court's own PDFs (preliminary prints 588-606) have no parser; CourtListener covers the decisions.
