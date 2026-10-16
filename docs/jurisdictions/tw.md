# Taiwan (laws of the Republic of China in force in Taiwan) - `TW`

Status: profile `draft_unverified` (0.1.0). The committed manifest lists **every law and regulation page of
the sitemap** of 全國法規資料庫 (Laws & Regulations Database, Ministry of Justice): 11,812 PCodes, current
consolidation only (see "Widened to the whole database"). The real runs in "Results" cover the original eight
laws and a 48-page slice of the widened manifest; the full download has not been run here.
Naming is neutral: the profile is "Taiwan", with the official designation "Republic of China
(Taiwan)" / 中華民國 in `names`, and `kind: other` (no position on international status).

## Source

| | |
|---|---|
| Source id | `tw-moj-laws` (`config/sources/tw-moj-laws.yaml`) |
| Publisher | Ministry of Justice (法務部), 全國法規資料庫工作小組 |
| Access | One HTML page per law: `GET https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=<PCode>` (所有條文) |
| Connector | `moj_laws` (`ulis/sources/connectors/moj_laws.py`, uses `PoliteHttp`) |
| Parser | `moj_law_html` (`ulis/sources/parsers/moj_law.py`) |
| Manifest | `config/manifests/tw-moj-laws.yaml` |
| Module | `tw` (`ulis/jurisdictions/modules/tw.py`) |

### Licence and terms (read before any law page was fetched, 2026-10-14)

- [政府網站資料開放宣告](https://law.moj.gov.tw/Service/Copyright.aspx) (the site's open data
  declaration), item 1: everything on the Ministry's website, to the extent protected by copyright,
  is released under the **[政府資料開放授權條款－第1版 / Open Government Data License 1.0](https://data.gov.tw/license)**,
  free, non-exclusive and sublicensable, for reproduction, adaptation, editing, public transmission
  and any other use, without time or territorial limit and without further permission - **but the
  source must be stated**. Items 2-5: copyright only (no trademarks or logos); personal data stay
  subject to the Personal Data Protection Act; third parties' moral rights are respected; no
  malicious alteration; no implied endorsement.
- OGDL 1.0: s 2 any purpose including commercial; s 3(2) attribution in the form of the annex
  (顯名聲明), failing which the licence is deemed never granted; s 4(2) compatible with
  **CC BY 4.0**.
- Attribution used on every result (annex format):
  `法務部 2026 全國法規資料庫（https://law.moj.gov.tw/）。此開放資料依政府資料開放授權條款 (Open Government Data License) 進行公眾釋出，使用者於遵守本條款各項規定之前提下，得利用之。政府資料開放授權條款：https://data.gov.tw/license（ULIS 為結構化及索引加工，非法務部之認可。…）`
- Statutes are not subject to copyright (著作權法 art 9(1)(i), to verify; not relied on).
- **[robots.txt](https://law.moj.gov.tw/robots.txt)**: `User-agent: *`, `Disallow: /`, then
  `Allow: /News/NewsDetail.aspx`, `Allow: /LawClass/LawAll.aspx`, `Allow: /SearchEngineSitemap.aspx`;
  no Crawl-delay. This decided the design:
  - The **Open API** ([docs](https://law.moj.gov.tw/api/swagger/index.html): `/api/ch/law/json`,
    `/api/ch/law/xml`, `/api/en/law/...`; bulk ZIPs only, no per-law endpoint; "資料更新時間：2026/10/02")
    is under `Disallow: /` and was **not downloaded**, although the licence would allow it.
  - The per-law full-text page `/LawClass/LawAll.aspx` is explicitly allowed and is what ULIS fetches.
    The connector re-reads robots.txt and refuses any URL it does not allow (RFC 9309 longest
    match; `tests/test_moj_tw.py::test_robots_rules`).
  - History (`LawHistory.aspx`), the old-version page (`LawOldVer.aspx`), single articles and the
    English pages (`/ENG/...`) are disallowed and not fetched; their absence is listed in the gaps.
  - The licence, open-data and API pages are themselves under `Disallow: /`; each was read once,
    by hand, for this review (no crawling).
- Legal status (site footer): 「本網站法規資料係由政府各機關提供之電子檔或書面文字登打製作，若與各法規主管機關之公布文字有所不同，仍以各法規主管機關之公布資料為準」
  - the data is made from electronic files or typed from printed text supplied by the agencies, and
  the text published by the competent authority prevails. The authentic text of a law is its
  promulgation in the Presidential Office Gazette (總統府公報). So: source authority
  `official_non_authentic`, `authoritative: false`, `text_origin` `official_revised` for amended
  laws and `official_conversion` for the one law never amended (the Constitution). Hits carry
  `REVISED_TEXT` / `NOT_AUTHENTICATED` and `SOURCE_NOT_AUTHENTIC`.
- Updates: weekly (every Friday, after consolidation); each page states 法規整編資料截止日
  (consolidation cut-off date), **2026-10-02** for this ingest.

Licence review status: `approved` (project review; counsel review still recommended).

### TLS

Python 3.13 rejected the site's chain ("Missing Subject Key Identifier"): it ends at TWCA Global
Root CA, a root in the system trust store without a Subject Key Identifier, and Python 3.13 enables
the strict X.509 profile (`VERIFY_X509_STRICT`) by default. The connector passes `PoliteHttp` a
context that keeps full verification (trusted chain, host name) and clears only that flag. This
needed one additive core change (below).

## What is ingested

| Law | PCode | Type | Date on page | Provisions | Prospective | Repealed (刪除) | Passages |
|---|---|---|---|---|---|---|---|
| 中華民國憲法 (Constitution) | A0000001 | constitution | 公布日期 1947-01-01 | 331 | 0 | 0 | 306 |
| 中華民國憲法增修條文 (Additional Articles) | A0000002 | constitution | 修正日期 2005-06-10 | 83 | 0 | 0 | 74 |
| 民法 (Civil Code) | B0000001 | act | 修正日期 2026-08-17 | 3,176 | 8 | 65 | 2,547 |
| 中華民國刑法 (Criminal Code) | C0000001 | act | 修正日期 2026-07-22 | 1,231 | 0 | 20 | 994 |
| 民事訴訟法 (Code of Civil Procedure) | B0010001 | act | 修正日期 2023-11-29 | 2,175 | 0 | 107 | 1,670 |
| 行政程序法 (Administrative Procedure Act) | A0030055 | act | 修正日期 2021-01-20 | 610 | 0 | 2 | 517 |
| 個人資料保護法 (Personal Data Protection Act) | I0050021 | act | 修正日期 2025-11-11 | 283 | 113 | 0 | 247 |
| 勞動基準法 (Labor Standards Act) | N0030001 | act | 修正日期 2024-07-31 | 382 | 0 | 0 | 330 |

Language: Chinese (Traditional script), `zh-Hant`, one version per law. Indexed: preambles (前言),
articles (條), paragraphs (項), items (款) and sub-items (目). English translations are not ingested.

## Widened to the whole database (2026-10-15)

- **How the laws are listed.** robots.txt allows three paths; one is `/SearchEngineSitemap.aspx`, which the file
  itself names as `Sitemap:`. One request to it (checked against robots.txt with the connector's matcher) returns
  17,131 URLs: 11,812 `LawClass/LawAll.aspx?pcode=` pages (one per law or regulation, repealed ones included) and
  5,319 `News/NewsDetail.aspx` pages (not read). `python -m scripts.build_moj_manifest` writes the PCodes to
  `config/manifests/tw-moj-laws.yaml`: the original eight first, then the rest in sitemap order. By first letter
  (a ministry/category code): A 716, B 89, C 62, D 1,786, E 125, F 593, G 1,069, H 1,276, I 436, J 1,172,
  K 1,125, L 807, M 749, N 394, O 505, P 115, Q 87, R 256, S 285, T 114, U 51. The Open API stays unused.
- **What the sitemap does not say** is whether a page is repealed. A random sample of 160 pages (seeded; 40 + 120):
  52 repealed (header 廢止日期, 33 %), 7 charts published as attachments only (編制表, no article text, 4 %), 99
  ingested (62 %), 2 errors; so about 37 % of the pages are fetched only to be skipped. Both skip cases raise
  `ParseSkipped` (parser 1.1.0), counted as skipped documents, not errors.
- **Lists below the 目 level.** 3 of the first 120 pages had `1.` `2.` lists (or `（1）`) under a 目, which the
  parser refused. They are now kept, as printed, in the text of the 目 (else 款, else 項) they sit in: complete, but
  not citable on their own (`天然氣事業輸儲設備防災相關設施裝置維修辦法第3條第1項第1款第3目` contains `1.` to `3.`).
- **Still refused (2 of 160):** `B0010050` art 3, where the source prints items 8 and 9 on one line, so item 9
  is not found; `J0100040`, whose 生效狀態 memo (「九十二年十二月五日訂定發布全文 13 條，施行日期，由主管機關定之」)
  names no article number. Both are reported as errors; nothing is guessed.
- **Orders (命令).** Their pages show **發布日期** (date of issue) where a law shows 公布日期; it is recorded as the
  promulgation event and `work_date` of an order never amended. A name that is not a statute name
  (法/律/條例/通則, 中央法規標準法 art 2) is an order or rule: `regulation` (before, an unlisted suffix such as 要點
  was refused). A note in brackets after the name (`最低工資法（112.12.27制定）`) is not part of the name when the
  kind is decided; the title stays as published. Citation: `植物診療機構開業登記及管理辦法第2條第2項第1款`.
- **Connector** (`moj_laws`): a page that is missing (404), not a law page or a 5xx that outlasts the back-off goes
  to `fetch_errors` and the run goes on; a refusal (401/403/429/451), a URL robots.txt disallows or 20 failures
  in a row stops it; a PCode listed twice is refused.
- **Cost of the full run:** 11,812 pages + robots.txt + sitemap = about 11,800 requests = **16.4 hours** at 5 s;
  HTML 55 KB per page = about **0.65 GB**. The 99 ingested pages of the sample gave 7,428 provision versions
  (75 per law); 23 of them, loaded alone, took 3.8 KB of database per provision version. So about 7,300 laws
  ingested (6,400-8,200 allowing for the sample's size), **550,000 provision versions and about 2 GB** of
  database (1.6-2.7).
- **Slice run** (the eight + the first 40 random pages; 40 requests, 4 min): 48 seen, **31 ingested** (the eight
  + 23), 17 skipped, **0 errors**, 9,857 provision versions, `verify-sources` **31/31 pass** (9,857 units).
  The first attempt had 23 errors (repealed pages, orders dated 發布日期, a name with a bracketed note, charts
  without text) before the parser was widened; the other 120 pages were fetched afterwards (13 min) and only
  parsed (no database), which is where the list case above was found.

## Structure, identifiers and text

- 編/章/節/款/目 headings (`h3 char-1` … `char-5`) are context, not provisions
  (`context_heading`, e.g. `第 二 編 債 第 一 章 通則 第 一 節 債之發生 第 五 款 侵權行為`).
- Work URIs `/tw/act/<PCode>`, `/tw/constitution/<PCode>`; expressions `…/zho@<cut-off date>`.
  eIds are language-neutral: `article-184`, `article-184-paragraph-1`, `article-6-paragraph-1-item-2`,
  `article-59-item-4-subitem-1`, `article-21_1` (第21條之1, printed `第 21-1 條`), `preamble`.
- An article with **one paragraph is one provision** (cited 第N條) and its items are
  `article-N-item-M` (cited 第N條第M款), as in Taiwanese practice; with several paragraphs the article
  is a container of `article-N-paragraph-P`. Paragraphs are unnumbered in print; they are counted.
- The page's `line-NNNN` classes are the hanging indent of a line's label, **not** its level
  (`十一、` items and `（一）` sub-items are both `line-0006`; 刑法 art 227-1 is one unlabelled line
  in `line-0004`). The level is read from the label: numeral + `、` (or, in the Constitution, numeral +
  spaces or an ideographic space) is an item; `（一）` a sub-item; anything else a paragraph. Item
  numbers must run 1, 2, 3…; an Arabic-numbered list level would be refused (none occurs).
- Text is the page text exactly (entities decoded, line ends trimmed). An item's own text starts
  after its label; a paragraph's or article's text includes its items as printed.
  `（刪除）` marks a deleted article or item: `repealed`, empty text, the marker as note.
- `ext`: `pcode`, `category` (法規類別), `amended_on` (修正日期). Articles with an attachment
  (`本條文有附件`, e.g. 刑法 art 185-3) carry a note; the attachment is not ingested.
- Instrument type follows 中央法規標準法 arts 2-3 naming (法/律/條例/通則 = act; 規程/規則/細則/辦法/
  綱要/標準/準則 = regulation); the Constitution and its Additional Articles are `constitution`.

## Dates and versions

- The page gives **no per-article dates**: only 修正日期 (date the latest amendment was promulgated)
  or 公布日期 (promulgation of a law never amended), and the database cut-off date. Every provision is
  `validity_basis: attested_at_edition`: in force on the cut-off date (`currency_date` 2026-10-02),
  start unknown (`valid_from` NULL). Point-in-time lookups answer `in_force_date_unverified` with
  `ATTESTED_ONLY_AT` for any date; searches before the cut-off date add `HISTORY_INCOMPLETE`. A later
  re-fetch with a changed wording is recorded from its own cut-off date (`observed`), as for Germany.
- `work_date` is the 公布日期 when the page states it (Constitution: 1947-01-01). For amended laws the
  original promulgation is only in the history page (disallowed) and is left NULL.
- **Not yet in force.** When some articles are not in force, the page has a 生效狀態 row: 「※本法規部分或全部條文尚未生效」
  and a memo naming them, and the article text shown is the **amended wording**. The parser expands
  the article lists (`1-1、12、22～26`, ranges in document order) and records those articles and all
  their paragraphs/items as `prospective` with the memo as note (`NOT_IN_FORCE`, authority
  `prospective_legislation`; excluded from searches unless prospective texts are requested). A memo
  naming no article is refused rather than guessed. Found in this ingest:
  - 個人資料保護法: 26 articles - art 1-1 (added 2023-05-31) and the 2025-11-11 amendment
    (amended 1-1, 12, 18, 21, 22-26, 41, 47-49, 52, 53, 55; added 1-2, 20-1, 21-1 to 21-5, 51-1, 53-1;
    deleted 27), 「施行日期，由行政院定之」 (date to be fixed by the Executive Yuan). Art 27's deletion is
    itself prospective (empty text, status `prospective`).
  - 民法: art 166-1 (added 1999-04-21, date to be fixed by the Executive Yuan with the Judicial Yuan)
    and art 1223 (amended 2026-08-17, 「自公布六個月後施行」 - the date is not computed).
  - **The wording still in force for these articles is not ingested** (it is on `LawOldVer.aspx`,
    which robots.txt disallows): e.g. a search for PDPA art 12's text as of 2026-10-02 returns no
    in-force hit.
- Lifecycle events: `promulgation` (公布日期) or `amendment` (修正日期), and `version_change` at the
  cut-off date.

## Citations (module `tw`)

`民法第184條第1項`, `刑法第271條第1項`, `刑法第227條之1`, `個人資料保護法第6條第1項第2款`,
`個人資料保護法第2條第1款` (single-paragraph article), `勞動基準法第59條第4款第1目`, `民法第166條之1第2項`,
`憲法第8條第2項`, `憲法前言`, `憲法增修條文第1條第2項`. Arabic numerals; 第1項 omitted for an article with one
paragraph; 中華民國刑法 / 中華民國憲法 / 中華民國憲法增修條文 are cited as 刑法 / 憲法 / 憲法增修條文 (court
usage); other laws by full title. To be confirmed by a Taiwanese reviewer.

## Profile

`kind: other` with `parent: INT` (functionally like a state for scoping: case law and legislation
are scoped to TW). Civil-law, unitary, `treaty_reception: unknown` (to verify). Courts: 憲法法庭
(Constitutional Court, rank 1), 最高法院 and 最高行政法院 (rank 2, separate branches; no appeal lies
from them to the Constitutional Court, which reviews final judgments only on constitutional
complaints). Precedent `no_formal_precedent`, `vertical_binding: false`; notes (to verify):
Constitutional Court judgments bind all organs and the people (憲法訴訟法 art 38(1)), Judicial Yuan
interpretations bind all state organs (釋字第185號); the Supreme Court precedent (判例) system was
abolished in 2019 in favour of Grand Chambers (法院組織法 arts 51-10, 57-1). Search: `zh-Hant` has no
PostgreSQL configuration, so the `simple` configuration with CJK character bigrams is used
(`ulis/languages.py`, unchanged).

## Shared changes (core)

1. `ulis/sources/connectors/polite_http.py`: optional `ssl_context` argument (default `None`, which
   calls `urlopen` exactly as before). A context that does not verify certificates and host names is
   rejected. Needed for the TLS issue above; other connectors are unaffected. Tested in
   `tests/test_moj_tw.py` (context passed only when configured; insecure context refused).
2. Registrations: parser `moj_law_html` and connector `moj_laws` (`ulis/ingestion/pipeline.py`), module
   `tw` (`ulis/jurisdictions/modules/__init__.py`), profile `TW` and source `tw-moj-laws` in
   `tests/test_catalog.py`. No `resolve_uri` (the parser emits no citation links). No migration, no
   change to search, scoping, limitations or temporal logic.

## Results (2026-10-14)

- Network: 16 HTTP requests to law.moj.gov.tw (robots.txt twice, home page, open-data declaration,
  API docs (2 incl. redirect) and spec, one law page by hand, then the connector: robots.txt + 8 law
  pages, 5 s apart) and 2 to data.gov.tw (robots.txt answered 500; licence page). A first PoliteHttp
  attempt failed client-side in the TLS handshake (no HTTP request sent).
- `ingest tw-moj-laws` on a fresh `ulis_tw` (`migrate`, `sync-catalog` first): documents_seen 8, new 8,
  instruments 8, instrument versions 8, provision versions created **8,271**, passages **6,685**,
  conflicts 0, errors 0 (13 s, from the cache). Re-run: 8 unchanged, 0 created.
- `verify-sources`: **8/8 pass**, 8,271 units checked.
- Tests (`ULIS_TEST_DATABASE_URL=…/ulis_test_tw`): full suite **234 passed** (128 s);
  `tests/test_moj_tw.py` 18 tests (8 parser/module, 6 connector/robots/TLS/registration, 4 PostgreSQL:
  search with citations and limitations, prospective wording, point in time, idempotent re-ingest +
  verification). Without a database: 149 passed, 85 skipped. `ruff check`, `ruff format --check` and
  `mypy ulis` pass.
- Sample searches (`ulis.cli search … --jurisdiction TW --as-of 2026-10-02`):
  - `因故意或過失，不法侵害他人之權利` → 1. 民法第184條第1項 (`ATTESTED_ONLY_AT`, `REVISED_TEXT`,
    `SOURCE_NOT_AUTHENTIC`, `PROFILE_UNVERIFIED`).
  - `殺人者，處死刑` → 1. 刑法第334條第1項 (犯海盜罪而故意殺人者…), 2. 刑法第271條第1項, 3. 刑法第332條第1項 -
    bigram ranking favours the shorter articles; the longer query `殺人者，處死刑、無期徒刑` ranks 271(1) first.
  - `人民身體之自由應予保障` → 1. 憲法第8條第1項.
  - `行政行為，應以誠實信用之方法為之` → 1. 行政程序法第8條.
  - `雇主延長勞工之工作時間` → 1. 勞動基準法第32條第2項.
  - `病歷、醫療、基因、性生活` → 個人資料保護法第2條第1款, 第2條, 第6條第1項.
  - `因故意或過失，不法侵害他人之權利` `--as-of 1990-01-01` → 民法第184條第1項 with `ATTESTED_ONLY_AT` and
    `HISTORY_INCOMPLETE` (indexed versions start 2026-10-02).
  - `知悉所保有之個人資料被竊取` → no in-force hit (PDPA art 12 as shown is not yet in force; its
    in-force wording is not ingested).

Tests after widening (2026-10-16, own test database): full suite **1,291 passed**; `tests/test_moj_tw.py` 25 tests (committed manifest vs robots.txt, order / bracketed name / repealed / attachment-only / deeper-list fixtures, connector robustness, PostgreSQL search). ruff, ruff format, mypy pass.

## Known gaps

- Current consolidation only; no per-article dates (attested at the cut-off date). The full 11,812-page download
  has not been run (a slice of 48 pages has); the size figures are estimates.
- Articles amended but not in force: only the amended (prospective) wording; the wording in force is
  missing (PDPA 26 articles, Civil Code arts 166-1 and 1223).
- No amendment history (沿革), no original promulgation dates of amended laws.
- The Constitution is shown as promulgated; the Additional Articles suspend or replace several of its
  articles (e.g. arts 25-34 and 135, Additional Articles art 1(2)); the two are not linked.
- Repealed laws are not ingested. No local self-government regulations, treaties, cross-strait agreements,
  case law, Constitutional Court judgments or Judicial Yuan interpretations; no English translations; no
  article attachments (a chart published only as an attachment has no text here).
- Pages whose text is not in the 第N條 form (for example administrative rules numbered 一、二、) would be refused
  by the parser and show up as errors in a run; none occurred in the 160 sampled pages, but 11,812 pages have
  not been read (expect a few percent). Instruments are typed by name only (`act` / `regulation`).
- Bigram search with the `simple` configuration: no word segmentation, no variant-character folding
  (臺/台), short or common bigrams rank loosely.
- Cached pages are kept forever: delete `var/sources/law.moj.gov.tw/LawClass/LawAll/<PCode>.html` (and
  `robots.txt`) to pick up a newer consolidation.

## Unsure

- Whether robots.txt is meant to cover the documented Open API. ULIS does not depend on the answer: only the
  allowed pages are used (the per-law page and the sitemap), and the JSON dump is not fetched.
- Profile statements on precedent, treaty reception and commencement are research notes (to verify).
