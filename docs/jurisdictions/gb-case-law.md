# United Kingdom (GB): court and tribunal decisions

Status: **six active sources, about 13,300 decisions in their manifests** (UK Supreme Court from 2009, Privy Council from 2009,
Employment Appeal Tribunal, Upper Tribunal Administrative Appeals Chamber, Northern Ireland courts), all under the Open Government
Licence, each run on a slice. Four more collections are recorded as `planned` or `rejected` with the publisher's own words and a
gate test (Find Case Law, House of Lords, Scottish courts, BAILII/CommonLII). Legislation is in [gb.md](gb.md); the market summary is
[../markets/uk.md](../markets/uk.md). Research aid, not legal advice; the profile stays `draft_unverified`.

| Source (`config/sources/<id>.yaml`) | Manifest | Connector / parser | Tests |
|---|---|---|---|
| `gb-uksc-judgments` | 126 judgments 2021-2026 (HTML) | `uksc` / `uksc_html` | `test_uksc.py` |
| `gb-uksc-judgments-pdf` | 1,428 case pages 2009-2026 (PDF) | `uksc_pdf` / `uksc_pdf` | `test_uksc_pdf.py` |
| `gb-jcpc-judgments`, `gb-jcpc-judgments-pdf` | 140 pages (HTML) + 862 (PDF), 2009-2025 | `jcpc`, `jcpc_pdf` / the UKSC parsers (`[yyyy] UKPC n`) | `test_jcpc.py` |
| `gb-govuk-tribunal-decisions` | 4,727: EAT 2,634, UT AAC 2,093 | `govuk_decisions` / `govuk_decisions` | `test_govuk_decisions.py` |
| `gb-ni-judiciary-judgments` | 6,530 decisions (NICA, NIQB, NIKB, NICh, NIFam, NIMaster, NICC, others) | `ni_judiciary` / `ni_judiciary` | `test_ni_judiciary.py` |

Shared code: `ulis/sources/parsers/court_pdf_units.py` (page footers removed, lines joined, numbered paragraphs found in the "N." or
"[N]" style, quoted numbers not taken for the judgment's own) and `pdf_text.py` (`pdftotext`). Builders: `scripts/build_uksc_pdf_manifest.py
[--court jcpc]`, `build_govuk_decisions_manifest.py`, `build_judiciaryni_manifest.py`. Courts added to the profiles: `GB-JCPC`, `GB-EAT`,
`GB-UTAAC` (GB 0.5.0), `GB-NICA`, `GB-NIHC`, `GB-NICC`, `GB-NIOTH` (GB-NIR 0.2.0).

## Terms and access (read 2026-10-16, robots.txt first)

| Publisher | Finding |
|---|---|
| supremecourt.uk, jcpc.uk | [robots.txt](https://www.supremecourt.uk/robots.txt) closes only a booking page; [terms](https://www.supremecourt.uk/about/terms-and-conditions): re-use "under the terms of the Open Government Licence". |
| GOV.UK | [robots.txt](https://www.gov.uk/robots.txt) closes `/search/all*` and print pages, not `/api/`; [terms](https://www.gov.uk/help/terms-conditions): content "published under the Open Government Licence", feeds offered for re-use. |
| judiciaryni.uk | [robots.txt](https://www.judiciaryni.uk/robots.txt) leaves `/judicial-decisions/` and `/files/` open; [Crown copyright page](https://www.judiciaryni.uk/crown-copyright): OGL. |
| Find Case Law (caselaw.nationalarchives.gov.uk) | **Planned, rejected.** [re-use page](https://caselaw.nationalarchives.gov.uk/re-use-find-case-law-records): the Open Justice Licence "does not permit computational analysis ... you will need to apply for a licence" (also [terms of use](https://caselaw.nationalarchives.gov.uk/terms-of-use)); robots.txt closes named AI crawlers. An application is a manual step, so nothing is fetched. |
| House of Lords 1996-2009 (publications.parliament.uk) | **Planned, rejected.** `robots.txt` answers HTTP 403 with `cf-mitigated: challenge` (a Cloudflare challenge): a refusal, not worked around. |
| Scottish courts (scotcourts.gov.uk) | **Planned.** The archive is reachable only through the site search, which robots.txt closes (`/*?query=*`); the RSS feed (`api.pa.web.scotcourts.gov.uk/web/rss/Judgments`) holds only the latest 215 judgments, no archive. |
| BAILII / CommonLII | **Rejected.** BAILII robots.txt still disallows `/uk`, `/ew`, `/scot`, `/nie`, `/wales`, `/eu`; CommonLII robots.txt answers 403. |

`tests/test_gb_planned_sources.py` checks that each of these sources stays `planned`/`rejected` and that the licence gate refuses it.

## How a decision is read

- **UKSC / JCPC HTML**: element ids of the court's page (case title, case id, neutral citation, judgment date), numbered paragraphs
  ("47. ...") with `num` "47"; the Court's links to `legislation.gov.uk/id/...` are `source_markup` links resolved where the Act is
  ingested; "[yyyy] UKSC n" / "[yyyy] UKPC n" in the text is linked by pattern. Work `/gb/uksc/{year}/{n}`, `/gb/ukpc/{year}/{n}`.
- **UKSC / JCPC PDF**: the case page (HTML) gives title, date, citation and justices; the text is the PDF's text layer. A case page with no
  judgment yet, or a joined appeal with the same citation, is skipped (`not_yet_judged`; the pipeline cannot store one citation twice).
- **GOV.UK tribunals**: the Content API page gives the title, neutral citation and date; the text is GOV.UK's extraction of the PDF
  (`hidden_indexable_content`). Works `/gb/eat/{year}/{n}`, `/gb/ukut/{year}/{n}`, or `/gb/<court>/<slug>` for decisions with no neutral citation.
- **Northern Ireland**: the decision page gives title, "Neutral Citation No.", date and judges; the text is the PDF's. Courts by series:
  NICA to `GB-NICA`, NIQB/NIKB/NICh/NIFam/NIMaster to `GB-NIHC`, NICC to `GB-NICC`, anything else to `GB-NIOTH`. Work `/gb/<series>/{year}/{n}`.
- All are `authoritative: false` (website copy; the signed judgment is the original); paragraph text is exact, whitespace collapsed; a PDF without a text layer is
  `ParseSkipped` (no OCR). Judicial treatment is never inferred. Authority follows the profile (the Supreme Court and the Northern Ireland courts have a
  place in the court hierarchy; the Privy Council and the tribunals have none): see the authority rationale on each result.

## Slice runs (dev database `ulis_m_gb`, 2026-10-16) and scale

Evenly spaced slices of each manifest, fetched at 5 s per request, then `verify-sources` (299 documents of the database, all legislation
and case slices: 299 pass, 0 fail) and searches.

| Source | Slice | Result | Per decision | Whole manifest (documents, requests, hours, paragraphs, database, raw) |
|---|---|---|---|---|
| uksc | 8 pages | 8 judgments, 1,857 paragraphs, 44 s | 232 paragraphs, 394 KB | 126, 126, 0.2 h, 29,000, 0.09 GB, 0.05 GB |
| uksc-pdf | 25 pages | 19 judgments (6 skipped), 1,711 paragraphs, 219 s | 90 paragraphs, 431 KB | about 1,085, 2,500, 3.5 h, 98,000, 0.30 GB, 0.47 GB |
| jcpc | 12 pages | 12 judgments, 1,375 paragraphs, 56 s | 115 paragraphs, 240 KB | 140, 140, 0.2 h, 16,000, 0.05 GB, 0.03 GB |
| jcpc-pdf | 15 pages | 12 judgments (3 skipped), 757 paragraphs, 131 s | 63 paragraphs, 395 KB | about 690, 1,550, 2.2 h, 43,500, 0.14 GB, 0.27 GB |
| govuk | 40 decisions | 40 decisions, 3,263 paragraphs, 197 s | 82 paragraphs, 96 KB | 4,727, 4,727, 6.6 h, 386,000, 1.20 GB, 0.45 GB |
| ni-judiciary | 30 decisions | 30 decisions, 1,414 paragraphs, 297 s | 47 paragraphs, 259 KB | 6,530, 13,500 (401 listing pages, 2 per decision), 18.7 h, 308,000, 0.95 GB, 1.70 GB |

All slices: 0 errors. The database costs about 3.1 KB per paragraph (judgment paragraph row 0.8 KB, passage row with text and indexes 2.3 KB).
Whole case law: about 13,300 decisions, 880,000 paragraphs, 22,500 requests (31 hours), 2.7 GB of database, 3.0 GB of raw responses; estimates
from small slices, plus or minus 50 percent. Searches (2026-10-16): "unfair dismissal employee tribunal" (GB) -> `Acetrip Ltd v Mr A Dogra:
UKEAT/0238/18/BA (Employment Appeal Tribunal, 2019-03-18) [44]`; "appeal against the decision of the Upper Tribunal on a point of law" ->
`AE v Disclosure and Barring Service [2025] UKUT 167 (AAC) [10]`; "appeal from the Court of Appeal of Jamaica" -> `Recreational Holdings 1
(Jamaica) Ltd v Lazarus [2016] UKPC 22 [2]`; "appeal judge sentence conviction" (GB-NIR) -> `R v Louis Maguire [2015] NICA 71 [1]`; all with
`SOURCE_NOT_AUTHENTIC` and `PROFILE_UNVERIFIED`.

## Known gaps

- Find Case Law (Court of Appeal and High Court of England and Wales, newer tribunals), House of Lords 1996-2009, the Scottish courts' archive
  (Court of Session, High Court of Justiciary) and BAILII are not ingested, for the reasons above; no approach other than the publishers' open
  pages is used. The Court of Appeal and High Court judgments therefore remain missing.
- Excluded on purpose: Employment Tribunal decisions (134,808), residential property (18,331) and asylum support (101) decisions (private individuals);
  Upper Tribunal Tax and Chancery decisions (1,451: PDF only, no text in the API). Press summaries, Welsh-language judgments and video are not ingested.
- Text is a website copy (a scan PDF has no text); footnotes stay where the PDF puts them; later treatment (overruled, distinguished) is not recorded.
