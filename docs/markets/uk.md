# Market: United Kingdom (England and Wales, Scotland, Wales, Northern Ireland)

Batch 8, 2026-10-16. Dev database `ulis_m_gb`, test database `ulis_test_m_gb`. Details: [gb.md](../jurisdictions/gb.md) (legislation),
[gb-case-law.md](../jurisdictions/gb-case-law.md) (decisions). "Manifest" = what a full ingest would fetch; only slices were run here.

Update 2026-10-18 (completeness audit, see [gb.md](../jurisdictions/gb.md)): the never-revised instruments (`gb-legislation-gov-uk-secondary-made`,
65,456 works) are law in force as made unless revoked and are now loaded by default, last (opt-out: `load-all --skip`, documented in the
descriptor); the Welsh texts of Welsh legislation are fetched as parallel expressions (3,675 works, language `cy`, Welsh citations); the three
manifests were regenerated from the listings of 2026-10-18 (7,178 / 17,931 / 65,456 works). The "Welsh-language texts not fetched" and
"off by default" statements below describe the state of 2026-10-16.

## Inventory

| Collection | Source id | Status | In the manifest (before -> now) | What the publisher offers beyond | Action |
|---|---|---|---|---|---|
| Acts, Measures, Orders in Council | `gb-legislation-gov-uk` | active | 3,940 current texts + 9 histories -> 7,178 works (6,252 with every version, 926 as enacted/made) | 13,167 `ukpga`, 19,972 `ukla`, 2,160 `apgb`, 869 `ukppa`, 273 `gbla` and others as PDF only (disallowed `*/data.pdf`, no OCR) | Rebuilt from the listing feeds; repealed items and history included |
| Statutory instruments/rules, revised | `gb-legislation-gov-uk-secondary` | active, new | 1 sample -> 17,878 with history (UK 9,831, SSI 2,121, WSI 1,574, NISR 4,292, Church 60) | 62,000+ instruments PDF only | New source |
| Statutory instruments/rules, as made | `gb-legislation-gov-uk-secondary-made` | active, `load_by_default: false`, new | 0 -> 65,505 (one version each) | same | New source, separate so the default load stays smaller |
| UK Supreme Court | `gb-uksc-judgments`, `gb-uksc-judgments-pdf` | active | 126 (2021-) -> + 1,428 case pages (2009-) | press summaries | PDF parser added |
| Privy Council | `gb-jcpc-judgments`, `-pdf` | active, new | 0 -> 140 + 862 case pages (2009-) | - | New sources, GB-JCPC court |
| Employment Appeal Tribunal, Upper Tribunal (AAC) | `gb-govuk-tribunal-decisions` | active, new | 0 -> 4,727 | Employment Tribunal 134,808, property 18,331, asylum support 101 (personal data); UT Tax and Chancery 1,451 (no text) | New source |
| Northern Ireland courts | `gb-ni-judiciary-judgments` | active, new | 0 -> 6,530 decisions (back to 1968 in the listing) | 1,480 listing items without a neutral-citation page | New source, four NI courts in the profile |
| Find Case Law (CoA, High Court, tribunals) | `gb-find-case-law` | planned (rejected) | 0 | the whole service | Terms checked: licence application needed |
| House of Lords 1996-2009 | `gb-hl-judgments` | planned (rejected) | 0 | all judgments of the House | Cloudflare challenge on the host |
| Scottish courts | `gb-scotcourts-judgments` | planned | 0 | Court of Session, High Court of Justiciary | Archive only through a search that robots.txt closes |
| BAILII / CommonLII | `gb-bailii` | rejected | 0 | the free UK and Irish case-law database | robots.txt disallows (BAILII), 403 (CommonLII) |

## What was added

- `scripts/build_legislation_gov_uk_manifest.py` rebuilt: 23 types from the publisher's own listings (209 requests), repealed items kept, PDF-only
  items counted in the manifest headers; compact entries (`path`, `path@made`); three manifests. Connector: `history_default`, single-version
  fetch, 404s remembered. Parser 1.3.0: more types (Old Scottish/Irish/English Acts, NI Acts and Orders, Church Measures), base dates for pre-1991
  Acts, sectionless Acts as one provision.
- Shared change: `ulis/temporal/validity.py` `derive_valid_to` ends a version at the next version that starts strictly later (two versions on one
  date made a zero-length interval that the database check refused; found on asp/2005/16); test in `test_legislation_gov_uk_history.py`.
- New case-law connectors and parsers (`uksc_pdf`, `jcpc`, `govuk_decisions`, `ni_judiciary`, shared `court_pdf_units`), registered in the pipeline;
  profiles: GB 0.5.0 (`GB-JCPC`, `GB-EAT`, `GB-UTAAC`, sources), GB-NIR 0.2.0 (`GB-NICA`, `GB-NIHC`, `GB-NICC`, `GB-NIOTH`); NI Orders in Council cite as `SI`.
- Tests: 72 in 8 files (`uksc_pdf` 13, `jcpc` 8, `govuk_decisions` 12, `ni_judiciary` 10, legislation `history` 6, `versions` 6, `wide` 13, planned sources 4); full suite 1,873 passed, 1 failed (`test_smoke_real_us_statute...`, fixed on the base branch by a5d3a98,
  not touched here); ruff, ruff format and mypy clean.

## Licence facts (read 2026-10-15 and 2026-10-16, robots.txt first)

- legislation.gov.uk: [OGL v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) for all content, plus the Westlaw UK
  statement for pre-1987 instruments; [fair use](https://www.legislation.gov.uk/fair-use-policy) 1,500 requests per 5 minutes;
  [robots.txt](https://www.legislation.gov.uk/robots.txt) `Crawl-delay: 5`, `*/data.pdf` and `*/data.docx` disallowed;
  [what we have](https://legislation.github.io/data-documentation/what-we-have.html). `research.legislation.gov.uk` robots.txt: HTTP 401.
- Courts: [supremecourt.uk terms](https://www.supremecourt.uk/about/terms-and-conditions) (OGL; jcpc.uk the same);
  [GOV.UK terms](https://www.gov.uk/help/terms-conditions) (OGL; `/api/` open in [robots.txt](https://www.gov.uk/robots.txt));
  [judiciaryni.uk Crown copyright](https://www.judiciaryni.uk/crown-copyright) (OGL; `/judicial-decisions/` open in
  [robots.txt](https://www.judiciaryni.uk/robots.txt)).
- Refused: [Find Case Law re-use](https://caselaw.nationalarchives.gov.uk/re-use-find-case-law-records) "does not permit computational analysis ... you
  will need to apply for a licence"; publications.parliament.uk `robots.txt` HTTP 403 `cf-mitigated: challenge`; scotcourts.gov.uk robots.txt closes
  `/*?query=*`; [BAILII robots.txt](https://www.bailii.org/robots.txt) disallows `/uk`, `/ew`, `/scot`, `/nie`, `/wales`, `/eu`; CommonLII robots.txt 403.
  Gate-refusal tests: `tests/test_gb_planned_sources.py`.

## Scale (estimated from slices; plus or minus 50 percent; 5 s per request, one at a time)

| Source | Documents | Requests | Hours | Provisions / paragraphs | Database | Raw |
|---|---|---|---|---|---|---|
| gb-legislation-gov-uk | 24,700 | 28,200 | 39 | 2.75 million provision versions | 10.7 GB | 9.2 GB |
| gb-legislation-gov-uk-secondary | 42,700 | 48,700 | 68 | 1.18 million | 4.6 GB | 2.4 GB |
| gb-legislation-gov-uk-secondary-made | 65,505 | 65,505 | 91 | 2.31 million | 5.8 GB | 1.8 GB |
| case law, six sources | 13,300 | 22,500 | 31 | 880,000 paragraphs | 2.7 GB | 3.0 GB |
| Default load (without as-made) | 80,700 | 99,400 | 138 | 3.9 million provision versions + 880,000 paragraphs | about 18 GB | about 15 GB |
| Everything | 146,000 | 165,000 | 229 | 6.2 million + 880,000 | about 24 GB | about 16 GB |

Everything would be about 24 GB, above the 15 GB line, so the as-made source is off by default (default about 18 GB; legislation alone 15 GB) (`ingest gb-legislation-gov-uk-secondary-made` or `load-all
--include-history` loads it); to shrink the default further, set `load_by_default: false` on `gb-legislation-gov-uk-secondary` (4.6 GB) or reduce the
primary manifest. Slice runs on 2026-10-16: legislation 35 + 18 + 25 works, 299 documents in total with the case slices (110+43+25 legislation,
8+19+12+12+40+30 decisions), 0 errors, `verify-sources` 299 pass 0 fail, dev database 100 MB; searches returned `Licensing (Scotland) Act 2005,
s 23(8)`, `Gaming (Amendment) Act 1990 (repealed), s 1` (valid 1991-02-01 to 2007-09-01, as of 2000 only), `Community Legal Service (Funding) Order
2000 (revoked), SI 2000/627, art 1` (2000-04-01 to 2007-10-01), `R v Louis Maguire [2015] NICA 71 [1]`, `Recreational Holdings 1 (Jamaica) Ltd v
Lazarus [2016] UKPC 22 [2]`.

## Missing and why

- Court of Appeal, High Court and newer tribunal judgments of England and Wales (Find Case Law only; licence application = manual step), House of
  Lords 1996-2009 (Cloudflare), Scottish court archive (search closed to robots; the RSS keeps 215 judgments), BAILII: no automated route is allowed.
- About 100,000 legislation items are PDF-only on the site; as-enacted originals of works that also have versions are not stored (the pipeline allows one
  expression per URI); Acts before 1991 start at the site's first revised text (1991-02-01), 2006 for NI; Welsh-language texts not fetched.
- Whole-corpus loads were not run (about 229 hours at the crawl delay); the manifests, builders and the slice commands are in place.
