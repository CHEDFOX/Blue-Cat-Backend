# Japan (national law) - `JP`

Status: **pilot sample**, profile `draft_unverified` (0.1.0). Seven laws from e-Gov 法令検索,
two of them with their full revision history since 1 April 2017.

## Source

| | |
|---|---|
| Source id | `jp-egov-laws` (`config/sources/jp-egov-laws.yaml`) |
| Publisher | Digital Agency (デジタル庁). Data prepared by the Ministry of Justice reflecting each amendment, checked by the ministries concerned ([help page](https://laws.e-gov.go.jp/help)) |
| Access | 法令API Version 2, XML: `GET https://laws.e-gov.go.jp/api/2/law_revisions/{law_id}?response_format=xml` and `GET …/law_data/{law_revision_id}?response_format=xml` ([spec](https://laws.e-gov.go.jp/api/2/swagger-ui/), OpenAPI `lawapi-v2.yaml` 2.1.139). No API key |
| Connector | `egov_laws` (`ulis/sources/connectors/egov_laws.py`, uses `PoliteHttp`) |
| Parser | `egov_law_xml` (`ulis/sources/parsers/egov_law.py`) |
| Manifest | `config/manifests/jp-egov-laws.yaml` |

### Licence and terms (read before any law data was fetched, 2026-10-14)

- [e-Gov 法令検索 terms](https://laws.e-gov.go.jp/terms): copyright in the site belongs to the Digital
  Agency unless stated otherwise; the **Public Data License 1.0 (公共データ利用規約（第1.0版）, PDL1.0)**
  applies. Site-specific points: [copyright policy, 重要情報](https://www.digital.go.jp/copyright-policy#important).
- [PDL1.0](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0): anyone may copy,
  publicly transmit, adapt and otherwise use the content freely, **including commercially** (s 1),
  with **attribution** (s 1.1); edited or processed content must say so and by whom and must not be
  presented as unprocessed government output (s 1.1); **compatible with CC BY 4.0**, and use under
  CC BY is permitted (s 1.7).
- Attribution used on every result: `出典：e-Gov法令検索（デジタル庁）（https://laws.e-gov.go.jp/）、PDL1.0。ULISが構造化・索引化のために加工して作成`.
- `robots.txt`: `https://laws.e-gov.go.jp/robots.txt` returns the single-page application, i.e.
  there is no robots file; the ULIS default of one request every 5 seconds is used. Digital Agency's
  API prototype notes ask users not to load the servers; ULIS fetched 50 API responses in total.
- Legal status (help page, 免責事項): the site may use notation that differs from the Official Gazette
  for editorial reasons, and **where the e-Gov data differs from the Official Gazette (官報), the
  Gazette prevails**. The text is therefore `official_revised` (a maintained consolidation),
  `authoritative: false`, source authority `official_non_authentic`: every hit carries
  `REVISED_TEXT` and `SOURCE_NOT_AUTHENTIC`.
- Statutes themselves are outside copyright under Copyright Act art 13 (to verify; not relied on).

Licence review status: `approved` (project review; counsel review still recommended).

## What is ingested

| Law | law_id | Revisions ingested | Provisions | Versions | Passages |
|---|---|---|---|---|---|
| 日本国憲法 (Constitution) | 321CONSTITUTION | 1 (in force 1947-05-03, never amended) | 216 | 216 | 178 |
| 民法 (Civil Code) | 129AC0000000089 | current (2026-06-24) | 2,812 | 2,812 | 2,278 |
| 刑法 (Penal Code) | 140AC0000000045 | **14**, 2016-06-23 … 2026-05-21 | 680 | 1,059 | 857 |
| 民事訴訟法 (Code of Civil Procedure) | 408AC0000000109 | current (2026-06-24) | 1,543 | 1,543 | 1,268 |
| 個人情報の保護に関する法律 (APPI) | 415AC0000000057 | **24**, 2017-03-01 … 2026-10-01 | 1,138 | 1,675 | 1,479 |
| 労働基準法 (Labour Standards Act) | 322AC0000000049 | current (2026-07-17) | 463 | 463 | 386 |
| 会社法 (Companies Act) | 417AC0000000086 | current (2026-08-12) | 6,172 | 6,172 | 5,417 |

Language: Japanese only (`ja`). Indexed: preamble, articles (条), paragraphs (項), items (号),
sub-items (号細分), the law's own original supplementary provisions (附則) and appended tables (別表).

## Structure, identifiers and text

- 編/章/節/款/目 (Part … Division) are headings, not provisions. Provisions and eIds are
  language-neutral: `article-709`, `article-27-paragraph-1-item-2`, `article-27_2` (第二十七条の二),
  `article-38:84` (第三十八条から第八十四条まで, deleted), `suppl-article-1` (附則第一条), `preamble`,
  `appdx-table-1`. Work URIs: `/jp/act/{law_id}`, `/jp/constitution/321CONSTITUTION`; expressions
  `…/jpn@{enforcement date}`.
- An article with **one paragraph is one provision** (cited 第709条, not 第709条第1項) and its items are
  `article-N-item-M` (第N条第M号), following Japanese citation practice. With several paragraphs the
  article is a container of `article-N-paragraph-P`.
- Text is the exact XML text. Article captions (見出し, e.g. `（不法行為による損害賠償）`) are headings.
  Paragraph numbers and item titles are printed before the text with an ideographic space, as
  e-Gov renders them; columns (definition items) are joined with an ideographic space; table cells
  with ` | `. **Ruby readings (`<Rt>`, furigana) are dropped** (`<Ruby>姦<Rt>かん</Rt></Ruby>` → `姦`).
- A provision whose whole text is 削除 is `repealed` with note `削除` and empty text.
- `ext`: `law_id`, `law_num` (法令番号), `law_type`, `abbrev`.

## Dates and versions

- Each e-Gov revision is a **snapshot of the whole law** as amended by one amendment, in force from
  that amendment's enforcement date (`amendment_enforcement_date`, 施行期日). No per-provision dates
  exist, so documents are `validity_basis: revision_snapshot`. When several amendments take effect on
  the same day, e-Gov lists one revision per amendment; only the first listed (cumulative, checked on
  刑法 2025-06-01, which contains both amendments) is the law of that day and is fetched.
- New core support (`ulis/ingestion/snapshots.py`, see "Shared changes"): an unchanged wording
  continues the previous version; a provision absent from a later snapshot (removed or renumbered)
  ends on that snapshot's date (`valid_to_basis: explicit`); snapshots must be recorded oldest first.
- History starts at e-Gov's data baseline (1 April 2017): the oldest revision is the one in force then
  (刑法: 2016-06-23; APPI: 2017-03-01). Dates between promulgation (`work_date`) and that revision are
  `history_not_indexed`; dates before promulgation `not_yet_in_force`.
- **Not-yet-enforced amendments** (`UnEnforced`) are never fetched as text. The revision list is
  ingested as a *change report* for the current revision: each pending amendment becomes an
  instrument-level outstanding change (`OUTSTANDING_CHANGES_INSTRUMENT`) with its promulgation date
  and scheduled date. When e-Gov only gives a placeholder ordering date
  (`amendment_scheduled_enforcement_date`, "not necessarily the actual date"), ULIS reports the date
  as not fixed and quotes the enforcement rule (e.g. 「公布の日から起算して二年を超えない範囲内において政令で定める日」).
  The report also attests the current revision as current on its retrieval date (`currency_date`).
  Pending: 民法 5, 会社法 5, APPI 6, 労働基準法 2, 民事訴訟法 1, 刑法 0, Constitution 0.
- Example (real ingest): 刑法第177条 - `（強姦）` 2016-06-23 → 2017-07-13, `（強制性交等）` 2017-07-13 →
  2023-07-13, `（不同意性交等）` from 2023-07-13; 第178条 repealed (削除) from 2023-07-13 and its two
  paragraphs end there; 第199条 reads 懲役 until 2025-06-01 and 拘禁刑 from 2025-06-01.
- Caveat seen in the data: amending laws' transitional provisions are not indexed. The 2023
  amendment's text of 第177条 already reads 「拘禁刑」 from 2023-07-13 (it was drafted on top of the
  2022 amendment); until 2025-06-01 a supplementary provision of the amending law (not indexed)
  governs how that word applies.

## Citations (module `jp`)

`民法第709条`, `刑法第199条`, `刑法第27条の2`, `個人情報の保護に関する法律第27条第1項第2号`,
`日本国憲法第9条第2項`, `日本国憲法前文`, `民法第38条から第84条まで`, `個人情報の保護に関する法律附則第1条`,
`個人情報の保護に関する法律別表第一`. Full law titles throughout; Arabic numerals (horizontal writing);
sub-items by their printed title (`…第2号イ`). To be confirmed by a Japanese reviewer.

## Profile

Unitary state, civil-law system, treaty reception `monist` (to verify), courts 最高裁判所 → 高等裁判所 →
地方裁判所 (family and summary courts not modelled), precedent `no_formal_precedent`, `vertical_binding:
false`: Supreme Court precedents are de facto authoritative (to verify). Search: `ja` has no PostgreSQL
configuration, so the `simple` configuration is used with CJK character bigrams (`ulis/languages.py`).

## Shared changes (core)

1. `ulis/ingestion/snapshots.py` (new) and hooks in `ulis/ingestion/pipeline.py`: revision-snapshot
   continuation, end-on-absence, oldest-first order, and change reports (`consolidation_kind:
   change_report`: replaces the target expression's outstanding changes, sets its currency to the
   retrieval date, adds lifecycle events). Only documents marked `revision_snapshot` /
   `change_report` take these paths; other sources are unaffected. Event persistence was moved into
   `_add_events` / `_add_treaty_parties` without behaviour change. Without this, every revision would
   create a new version of every provision ("replaced by a later version" for unchanged text) and
   renumbered provisions would stay open-ended.
2. `ulis/verification/integrity.py`: verification also re-derives the lifecycle events a document
   recorded (revision lists record no text, so they were otherwise unverifiable and broke
   `test_source_integrity_passes_for_every_document`).
3. Registrations: parser, connector, `resolve_uri` (pipeline), module (`modules/__init__.py`),
   `tests/test_catalog.py` (profile `JP`, approved source). No migration.

## Results (2026-10-14)

- Network: 63 requests in total (laws.e-gov.go.jp: 10 for terms/help/spec pages, 50 API responses -
  7 revision lists, 43 law texts; www.digital.go.jp: robots, copyright policy, PDL1.0).
- `ingest jp-egov-laws` on a fresh `ulis_jp`: documents_seen 50, new 50, instruments 7, instrument
  versions 43, provision versions created 13,940, corroborated 21,235, restated 0, passages 11,863,
  conflicts 0, errors 0 (40 s). Re-run: 50 unchanged.
- `verify-sources`: 50/50 pass, 35,360 units checked (revision lists: 2-33 events each).
- Tests (`ULIS_TEST_DATABASE_URL=…/ulis_test_jp`): full suite **138 passed**; `tests/test_egov_jp.py`
  16 tests (7 parser/module, 3 connector, 6 PostgreSQL). Without a database: 77 passed, 61 skipped.
  ruff check, ruff format --check and mypy pass.
- Sample searches (`ulis.cli search … --jurisdiction JP`):
  - `故意又は過失によって他人の権利` → 1. 民法第709条 (with `OUTSTANDING_CHANGES_INSTRUMENT`: 5 pending amendments).
  - `個人データを第三者に提供` → 個人情報の保護に関する法律第27条第2項, 第41条第6項, 第27条第1項.
  - `人を殺した者` `--as-of 2020-01-01` → 刑法第199条 「…五年以上の懲役に処する。」 (+ `HISTORY_INCOMPLETE`
    for the laws without history); as of 2026-10-14 → 「…五年以上の拘禁刑に処する。」
  - `国の交戦権` → 日本国憲法第9条第2項.
  - `解雇の予告` → 労働基準法第22条第2項, 第20条第3項, 第20条第2項. 第20条第1項 is missed: its wording
    「解雇しようとする…その予告」 does not contain the query's bigrams 雇の/の予 - a limit of bigram search.

## Known gaps

- Sample of 7 laws; history only for 刑法 and APPI and only from 1 April 2017.
- No Cabinet Orders, ministerial ordinances, rules, notices, treaties, local ordinances or case law.
- Supplementary provisions of amending laws (改正附則, including transitional and reading-substitution
  rules), the table of contents, the enacting formula, figures and forms are not indexed; furigana dropped.
- Pending amendments are reported for the whole law, not linked to the provisions they change.
- Revision lists are cached; delete `var/sources/laws.e-gov.go.jp/api2/law_revisions` to pick up new
  revisions. Adding history for a law already ingested without it requires a fresh database
  (snapshots must be recorded oldest first).
- Japanese Law Translation (English) is unofficial and not ingested. No morphological analysis.
