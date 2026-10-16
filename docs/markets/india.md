# India market (read 2026-10-16)

Result: **no Indian legislation can be loaded** - every official host (India Code, Legislative Department, eGazette, Law Commission,
data.gov.in, the Supreme Court's website, Lok Sabha, Rajya Sabha) refused the bot (HTTP 403) or reset the connection - and the only bulk
copies of court judgments (AWS Open Data, Dattam Labs) were produced by solving the courts' CAPTCHAs, so they are rejected. One open
route was found and built: **Supreme Court judgments as PDF on the Court's API host (`api.sci.gov.in`)**, AMBER licence reading.

## Inventory

| collection | source id | status | loaded | what the publisher offers beyond that | action |
| --- | --- | --- | --- | --- | --- |
| Constitution of India | `in-legislative-constitution` | planned | nothing | PDF/HTML on legislative.gov.in, Lok Sabha, Rajya Sabha, sansad.in | hosts refused or reset (descriptor of 2026-10-16 kept); no third-party copy accepted |
| Central Acts (as enacted, amended, repealed), subordinate legislation, State Acts | `in-india-code` | planned | nothing | all Central Acts since 1834 and State Acts | `www.indiacode.nic.in` robots.txt and home page HTTP 403 (Akamai); descriptor rewritten with findings |
| Gazette of India (Acts as assented, rules, notifications) | `in-egazette` | planned (new) | nothing | the Gazette back file | `egazette.gov.in` connection reset |
| Law Commission reports | `in-lawcommission` | planned (new) | nothing | reports | `lawcommissionofindia.nic.in` connection reset |
| Open datasets (GODL-India) | `in-data-gov-in` | planned (new) | nothing | no dataset with law text found | `data.gov.in` connection reset |
| Supreme Court judgments 1950 - April 2017 | `in-sci-judis` | **active (AMBER)** | slice of 100 numbers; full manifest 1-45100 | later judgments (address pattern not listable), regional languages | connector, parser, module, tests |
| Supreme Court judgments, High Courts (AWS: 35K SC, 17.8M HC) | `in-dattam-court-judgments` | rejected (new) | nothing | PDFs + parquet, CC-BY-4.0 (uploader's) | CAPTCHA-solving provenance, see below |
| State legislatures' Acts | - | not established | nothing | - | State Acts sit on India Code; state hosts not reached |

## Licence facts (all read 2026-10-16, identifying User-Agent, 5 s apart)

* `https://api.sci.gov.in/robots.txt`: HTTP 200, empty (nothing disallowed). `https://api.sci.gov.in/jonew/judis/<n>.pdf`: the judgment as
  PDF, no login, no CAPTCHA. `https://www.sci.gov.in/robots.txt`: HTTP 403 "Access Denied", so the Court's website policies could not be
  read; only search-engine excerpts exist ("content has been provided and processed by the Supreme Court of India"; "information in the
  original records will be final and binding"). Basis: section 52(1)(q)(iv) Copyright Act 1957 (judgments may be reproduced unless the
  court prohibits it) and *Eastern Book Co. v. D.B. Modak* (2008) 1 SCC 1 (no copyright in the raw text; reporters' headnotes protected) -
  both from excerpts, to verify. `commercial_use: unknown`: the gate refuses this source when `ULIS_COMMERCIAL_USE=1`.
* AWS Open Data: <https://registry.opendata.aws/indian-supreme-court-judgments/> and <https://registry.opendata.aws/indian-high-court-judgments/>
  say "License CC-BY-4.0", no AWS account needed (`s3://indian-supreme-court-judgments`, `s3://indian-high-court-judgments`; checked: public
  listing and objects over HTTPS). Layout: `data/tar/year=YYYY/english/*.tar`, `data/pdf/...`, `metadata/json|tar|parquet/...`; SC metadata
  has title, parties, judges, `nc_display` ("2023 INSC 1043"), case id, CNR, disposal nature. But the uploader's code
  (<https://github.com/vanga/indian-high-court-judgments>, <https://github.com/vanga/indian-supreme-court-judgments>) "solves the eCourts
  arithmetic CAPTCHA automatically with an ONNX model" and imports a CAPTCHA solver for the Supreme Court portal; the project's rule
  (docs/bulk-datasets-survey.md) is to use nothing obtained that way. The licence is not the obstacle (judgments are freely reproducible).
* India Code mirrors: Hugging Face `RUDXLABS/india-central-state-acts` (`license: other`, "public-domain ... under section 52(1)(q)",
  34,729 hash-named PDFs, a scrape of a site that refuses the bot) and GitHub `anoopdixith/TheConstitutionOfIndia` (no licence file) rejected.
  Section 52(1)(q)(ii), as we read it, frees an Act only when reproduced with commentary or other original matter.

## What was added

* `ulis/sources/connectors/sci_judis.py`: number ranges from `config/manifests/in-sci-judis.yaml` (1 to 45100); `PoliteHttp`, robots.txt
  checked per URL, empty answer = no judgment, non-PDF reported in `fetch_errors`, any refusal stops the run.
* `ulis/sources/parsers/sci_judis.py`: PDF text with `pdftotext -layout`. Two layouts: **JUDIS header** (to about 2008: `PETITIONER`,
  `RESPONDENT`, `DATE OF JUDGMENT`, `BENCH`, `CITATION` "1961 AIR 331 / 1961 SCR (2) 40", `ACT` catchwords, `HEADNOTE`, `JUDGMENT`) and
  **signed judgment** (from 2009: cause title, case number, "Versus", author, numbered paragraphs "1." or "1)", judges' names, closing
  "NEW DELHI; MAY 11, 2011." line, an appended Record of Proceedings). Extracts court `IN-SC`, date, case number, parties, bench, author,
  reporter citations, reportable flag, numbered paragraphs (parts `considerations`, `text`, `catchwords`, `headnote`, `record`); page
  furniture and reporter page numbers are dropped; a date that cannot be read means the judgment is skipped, never guessed.
* `ulis/jurisdictions/modules/in_.py` (module `in`): citations in English and Hindi labels (`s.`/`धारा`, `art.`/`अनुच्छेद`); judgments cite
  the reporter citations as printed. `config/jurisdictions/in.yaml` 0.2.0: module `in`, `corpus_status: pilot`, the 25 High Courts as
  courts (`appeals_to: IN-SC`) so a forum court can be named, honest known gaps. Shared files touched (additive): `pipeline.py`
  (connector, parser, version), `modules/__init__.py`, `tests/test_catalog.py` (approved set, `rejected` accepted as a gate reason, IN
  corpus `pilot`), `tests/test_api.py` (IN `pilot`).
* Tests: `tests/test_sci_judis.py` (parser on 8 real PDFs, citations, profile/manifest, connector offline, PostgreSQL ingest and
  search, forum-court authority) and `tests/test_india_blocked.py` (gate refusal of the six blocked records).

## Scale of the full manifest (extrapolated from a slice of 100 numbers)

45,100 requests = 62.7 h at 5 s (about 74 h measured, 5.9 s per number with transfer, parsing and writes). 89 of 100 numbers held a PDF
(6 of 22 single probes were empty), so about 30,000-40,000 judgments. Per judgment: 96 KB PDF, 25 KB text, 17.8 passages, about 128 KB of
database. Whole range (35,000): 3.4 GB raw PDFs, 4.5 GB database, about 620,000 passages - well below the 15 GB limit, so the default
manifest is the whole range. Empty numbers are cached (not asked twice).

## Real run (dev database `ulis_m_in`, PostgreSQL 16)

* `migrate`, `sync-catalog` (148 profiles, 345 sources); slice ingest (cache of the first run, no network): 89 seen, 88 new, 1 skipped
  (33824, a Record of Proceedings), 0 errors, 88 judgments, 1,566 paragraphs, 1,566 passages indexed. (First run with an earlier
  parser: 1 error on the same document; fixed.) `verify-sources`: 88 pass, 0 fail.
* Years in the slice: 1950, 1970, 1978, 1981, 1996, 2007, 2009, 2011, 2014, 2016. Parser outcome on the 89 PDFs: 75 JUDIS header (to 2007
  ones included), 14 signed; 68 with a case number (older judgments seldom print one in the header).
* Searches (CLI, `--jurisdiction IN`): "requisitioned vehicle election duty compensation" -> *National Insurance Co. Ltd. v. Deepa Devi & Ors,
  CIVIL APPEAL NO. 5796 OF 2007 (Supreme Court of India, 11 December 2007), para 2*; "arbitrator section 11(6) Arbitration and
  Conciliation Act" -> *Deepak Kumar Bansal v. Union of India & Anr., CIVIL APPEAL NO.1089 OF 2009 (Supreme Court of India, 17 February
  2009), para 2*; "sales tax cement Kota" -> *ASSOCIATED CEMENT CO. LTD. v. COMMERCIAL TAX OFFICER, KOTA & ORS., 1981 AIR 1887; 1982 SCR (1)
  563; 1981 SCC (4) 578 (Supreme Court of India, 2 September 1981)*. Limitations returned: PROFILE_UNVERIFIED, CITATOR_COVERAGE, AUTHORITY.
* Checks: TESTS_PLACEHOLDER

## What remains missing, and why

* **All legislation** (Constitution, Central and State Acts, history, subordinate legislation, Gazette): hosts refuse the bot. Nothing was
  worked around (no other host name, agent, proxy or mirror). Re-test from the owner's server: if `www.indiacode.nic.in` and
  `egazette.gov.in` answer there and their terms allow reuse, the planned descriptors say where to start.
* **Supreme Court judgments from 2017** (and the Court's 2023+ neutral citations "INSC"): served under `/supremecourt/<year>/<diary>/<file>.pdf`,
  which can only be listed through the Court's search (CAPTCHA, website 403). Orders and daily orders are not ingested.
* **High Courts and tribunals** (the largest part of Indian case law): only on the eCourts portals behind a CAPTCHA, or in the rejected
  AWS copy (17.8 million judgments, 1.25 TiB; it would need a scoped manifest by court and year even if accepted).
* Older judgments have no paragraph numbers in the text layer (cut into unnumbered passages); scanned PDFs are skipped (no OCR);
  unusual cause titles fall back to the case number as the name; no links between judgments, no citator.
