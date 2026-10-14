# United Kingdom (GB): UK Supreme Court judgments from the Court's website

Status: **draft unverified profile (0.3.0), full manifest ingested.** The 126 judgments of the UK Supreme Court (2021-2026) that the
Court publishes in full as HTML on its own website (156 case pages: joined appeals have one page per case number), English, linked to the legislation in [gb.md](gb.md) where that is ingested.
Research aid, not legal advice.

| | |
|---|---|
| Source / manifest | `config/sources/gb-uksc-judgments.yaml` (active, approved), `config/manifests/gb-uksc-judgments.yaml` (126 case ids) |
| Connector / parser | `ulis/sources/connectors/uksc.py`, `ulis/sources/parsers/uksc_html.py` (`uksc_html` 1.0.0) |
| Profile / citations | `config/jurisdictions/gb.yaml` (0.3.0), `ulis/jurisdictions/modules/gb.py` (1.1.0, OSCOLA judgment citations) |
| Tests | `tests/test_uksc.py` (13), fixtures in `tests/fixtures/uksc/` (3 real pages) |

## Source and licence (read 2026-10-14, robots.txt first)

| Page | Finding |
|---|---|
| `www.supremecourt.uk/robots.txt` (also the bare host and `jcpc.uk`) | `User-agent: *` / `Disallow: /contact-us/book-room-hearing`: judgments and the sitemap are allowed. |
| [Terms and conditions](https://www.supremecourt.uk/about/terms-and-conditions) | "You may use and re-use Crown copyright material from this website ... under the terms of the Open Government Licence, provided it is reproduced accurately and not in a misleading context." Not extended to third-party material or video footage. |
| [OGL v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) | Copy, publish, distribute, adapt, exploit commercially and non-commercially, with the attribution "Contains public sector information licensed under the Open Government Licence v3.0."; excludes personal data, third-party rights, logos; no endorsement. |
| [Sitemap](https://supremecourt.uk/sitemap.xml) | 1,583 case pages and 156 `/cases/judgments/uksc-<year>-<number>` pages (the judgment in full, HTML). Older judgments are PDFs only. |

Conclusion: licence **approved** (OGL v3.0 per the Court's own terms; counsel review recommended). The Find Case Law restriction
(`gb-find-case-law`: computational analysis needs a licence) concerns the National Archives' service and does not apply to the
Court's site; BAILII's robots.txt disallows `/uk/`, `/ew/`, `/ie/`, `/scot/`, `/wales/`, `/eu/` and is not used.

## What is ingested

Every judgment page in the sitemap (156 case pages, `UKSC/2021/0098` to `UKSC/2026/0080`; the test page `mock-case-id2` and the
variant `uksc-2025-0058-a` left out) holds 126 different judgments: the real run found that 30 case pages (22 judgments) are
further case numbers of joined appeals with the same judgment and neutral citation, which the pipeline cannot store twice (the
pages differ in bytes). The manifest lists one page per judgment and names the 30 others in comments; the connector also skips and
reports a repeated citation. One request each, 5 s apart (about 11 minutes), cached under `var/sources/www.supremecourt.uk/`.
Extend by re-reading the sitemap. The Privy Council (`jcpc.uk`, same terms and layout, 140 HTML judgment pages) is not ingested:
its judgments are mostly on the law of other countries.

## How a judgment is read

- **Fields** (element ids of the page): `case-title` as case name, `case-id` as docket (`UKSC/2022/0124`), `neutral-citation-number`
  ("[2024] UKSC 23") as neutral citation, `judgment-date` as decision date and version date; `ext` holds parties, interveners, the
  decision appealed, the justices and hearing dates. Work `/gb/uksc/{year}/{n}`. Not authoritative (the signed judgment is).
- **Paragraphs** (exact text, whitespace collapsed): `heading` (h2-h5 and all-bold blocks), `text`, `item`, and `considerations`:
  numbered paragraphs ("47. ..."), `num` "47", the number removed from the text. A run must continue 1, 2, 3 ... (or restart at 1 and
  be followed by 2); a quoted "1. The contractor warrants ..." or a numbered passage quoted from another judgment stays unnumbered.
- **Links**: the Court's own hyperlinks to `legislation.gov.uk/id/...` (Act or section) are `source_markup` links, resolved to the
  work and provision wherever that Act is ingested; "[yyyy] UKSC n" in the text is linked (`pattern`) to `/gb/uksc/{yyyy}/{n}`.
  Links to the National Archives or BAILII copies are ignored.

## Citations and authority

OSCOLA: `Abbey Healthcare (Mill Hill) Ltd v Augusta 2008 LLP (formerly Simply Construct (UK) LLP) [2024] UKSC 23 [47]`. The GB profile
says `stare_decisis` with vertical binding and lists only the Supreme Court, so a hit is "Persuasive only" unless a forum court below
it is given (the profile has none): see the authority rationale on each result.

## Real run and tests (dev DB `ulis_courts`)

`migrate`, `sync-catalog`, `ingest gb-legislation-gov-uk` (the nine Acts and instruments of the manifest, from the local cache:
159 documents, 38,851 provision versions), `ingest gb-uksc-judgments`: 126 judgments, 26,877 paragraphs and passages (15,608
numbered), 697 links (551 `[yyyy] UKSC n` patterns, 92 of them resolved to ingested judgments; 146 legislation hyperlinks, the two
to the Human Rights Act 1998 resolved to the work, the others cite Acts that are not ingested), no errors; a second run: 126 unchanged.
`verify-sources`: 935 pass, 0 fail (all sources of the dev DB, 126 of them judgments). Searches (`GB`): "collateral warranty
construction contract adjudication" -> `Abbey Healthcare ... [2024] UKSC 23 [32]`; "grave risk of harm child Hague Convention" ->
`In the Petition of DM No 1 [2026] UKSC 32 [108]`; "public authority acting incompatibly with Convention rights" -> Human Rights Act
1998 s 6(1) and `... (Respondent) (Scotland) [2025] UKSC 38 [1]`. Authority: "Precedent: binds courts below the deciding court", listing
the courts of the three UK legal systems whose appeals lie to the Supreme Court (from the GB child profiles).
Tests: 13 in `tests/test_uksc.py`; full suite 780 passed (434 s); ruff check, ruff format --check, mypy clean.

## Known gaps

- 126 judgments from 2021 on; the Court's other judgments since 2009 are PDF only (a PDF text parser, e.g. pypdf, would be needed);
  no press summaries, no Privy Council, Court of Appeal or High Court judgments.
- Website version, not the signed judgment; a redesign of the page (element ids) would need a parser update. Reporting restrictions
  (anonymity orders) are left as published.
- Later treatment (overruling, distinguishing) is not recorded; case links only by the "[yyyy] UKSC n" pattern.
