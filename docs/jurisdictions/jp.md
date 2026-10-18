# Japan (national law) - `JP`

Status: profile `draft_unverified` (0.1.0). The committed manifest lists **every law in force** that
e-Gov 法令検索 names (8,989 laws; regenerated 2026-10-18 from a list of 9,005 laws not repealed, 16 of which have no
revision in force yet; see "Widened to every law in force") and, since 2026-10-18, loads them from e-Gov's **official XML
bulk download** (`mode: bulk`, see "Bulk download route"): one archive and 50 API requests instead of 18,014 API requests.
All 8,989 laws were parsed from the archive (0 errors) and a 50-law slice was ingested and verified; the real runs recorded
in "Results" also cover the original seven laws (two with their full revision history since 1 April 2017), a 35-law slice
of the widened manifest and a 5-law slice of the regenerated one. The full load into a database has not been run here.

## Source

| | |
|---|---|
| Source id | `jp-egov-laws` (`config/sources/jp-egov-laws.yaml`) |
| Publisher | Digital Agency (デジタル庁). Data prepared by the Ministry of Justice reflecting each amendment, checked by the ministries concerned ([help page](https://laws.e-gov.go.jp/help)) |
| Access | XML bulk download `all_xml.zip`: `GET https://laws.e-gov.go.jp/bulkdownload?file_section=1&only_xml_flag=true` ([bulk page](https://laws.e-gov.go.jp/bulkdownload/)), with the API's list of laws in force `GET …/api/2/laws?repeal_status=None`. 法令API Version 2, XML, for laws with history and single laws: `GET https://laws.e-gov.go.jp/api/2/law_revisions/{law_id}?response_format=xml` and `GET …/law_data/{law_revision_id}?response_format=xml` ([spec](https://laws.e-gov.go.jp/api/2/swagger-ui/), OpenAPI `lawapi-v2.yaml` 2.1.139). No key, registration or notice |
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

## Widened to every law in force (2026-10-15, regenerated 2026-10-18)

- **Manifest** `config/manifests/jp-egov-laws.yaml`, written by `python -m scripts.build_egov_manifest` from the
  API's own list endpoint, `GET /api/2/laws?repeal_status=None&limit=1000&offset=N` (10 requests; spec
  `lawapi-v2.yaml`, parameters `repeal_status`, `law_type`, `limit`). `total_count` 9,003 laws not repealed or
  expired (a further 561 are repealed, expired, suspended or without effect, `repeal_status` ≠ `None`, and are left out). 14 of the 9,003 have no revision in force yet
  (`current_revision_info` is `UnEnforced`) and are left out. Listed: **8,989** = the seven laws above (their
  `history` flags unchanged) + Act 2,073, Cabinet Order 2,310, Imperial Order 66, Ministerial Ordinance 4,103,
  Rule 409 and 21 that the list types twice (`Act,CabinetOrder` 16, `Act,ImperialOrder` 3,
  `Act,MinisterialOrdinance` 2). The rest are `history: false`: the revision in force plus the revision list.
- **Double-typed laws.** For those 21 the list and revision-list endpoints say e.g. `Act,CabinetOrder`, the text
  endpoint says `Act`. A slice run exposed it (the change report named a work the text had not created). They are
  recorded as `act` (the text endpoint's type); `ext.law_type` keeps each response's own value.
- **Connector** (`egov_laws`): a law that cannot be fetched (404, no revision in force, a 5xx that outlasts the
  back-off) goes to `fetch_errors` and the run continues; a refusal (401/403/429/451) or 20 failures in a row
  stops it; a law is yielded only when all its requests succeeded; duplicate ids in the manifest are refused.
  **Parser** 1.1.0: a law with nothing to index raises `ParseSkipped`; double types are mapped.
- **Cost of the full run:** 2 requests per law = about 18,000 requests = **25 hours** at the 5 s delay; XML about
  **1.5 GB**. From a random sample of 98 laws (the 28 below + 70 more, seeded): 163 KB of XML, 127 provision
  versions and 0.63 MB of database per law (5 KB per provision version, most of it the search passages), so
  about 1.1 million provision versions and **4-8 GB** of database (the sample's mean is pulled up by a few
  large laws, e.g. 財務省組織規則 with 3,114 provision versions). Responses are cached (`var/sources/`), so an
  interrupted run resumes without new requests; use `python -m scripts.ingest_in_batches jp-egov-laws
  --batch 50` to commit in batches.
- **Slice run** (manifest = the seven laws + 28 laws drawn at random, seeded, by type: Act 6, Cabinet Order 6,
  Ministerial Ordinance 8, Rule 3, Imperial Order 2, one of each double type; 56 requests, 5 min). The other 70
  random laws (140 requests, 12 min) were loaded into a scratch database: 138 documents, 69 laws, 7,202
  provision versions; one error, a law number longer than the 40-character `instruments.number` column (a joint
  order of eight ministries): such a number is kept only in `ext.law_num` and `number` is NULL (parser 1.1.0).
  The slice of 35: documents seen
  106, instruments 35, instrument versions 71, provision versions 19,125 (+21,235 corroborated), passages 19,442,
  **0 errors** (first run: 3, the double types above; re-run: 103 unchanged, 3 new). `verify-sources`: **106/106
  pass**, 40,360 units. Searches (`ulis.cli search … --jurisdiction JP`): `予防接種の実施` → 予防接種実施規則第1条;
  `検疫感染症` → 検疫法施行令別表第二, 第2条の2第2項; `農薬の登録` → 農薬取締法第36条第1項; `豪雨による災害についての
  激甚災害` → the 2010 Cabinet Order 附則第1項 and 第1項.
- **Regenerated 2026-10-18** (`python -m scripts.build_egov_manifest`: 10 list requests, plus one `laws?limit=1` request
  for the unfiltered total). `total_count` **9,005** laws not repealed (9,003 on 2026-10-15); **16** of them have no
  revision in force yet (`UnEnforced`, 14 before: e.g. 防衛特別所得税に関する政令 from 2027-01-01, 防災庁組織令 from
  2026-11-02) and are left out, so **8,989 laws are listed: the same 8,989 ids** (0 added, 0 removed). Two titles changed in
  the list: 323M40000100034 社会保険診療報酬支払基金法施行規則 → 医療情報基盤・診療報酬審査支払機構法施行規則, and 419M60000802002
  (a 公安委員会 order under the 地域公共交通 act) shortened. The unfiltered list has 9,566 laws, so 561 are repealed, expired,
  suspended or without effect (unchanged). `load-all --refresh` re-fetches only the laws already listed; new laws appear
  after the builder is re-run (delete the cached pages `var/sources/laws.e-gov.go.jp/api2/laws/inforce-cur-offset*.xml`
  first; `docs/deployment.md`, "Refresh").
- **Scope of the API.** e-Gov 法令検索 holds 憲法・法律・政令・勅令・府省令・規則: the Constitution, Acts, Cabinet Orders,
  Imperial Orders, Ministerial Ordinances and Rules. Notices (告示), the rules of the Diet and of the Supreme Court, and
  treaties are not in e-Gov, so they are not in ULIS either (nor local ordinances or case law).
- **Slice of the regenerated manifest** (2026-10-18, dev database `ulis_regen`): 5 laws (日本国憲法, the two retitled
  ordinances above, メートル条約 119IO0000000000, which the API types as an Imperial Order, and 会計検査院規則の公布に関する規則
  322R00000001001; 10 requests): 10 documents (5 revision lists, 5 texts), 5 instruments, 323 provision versions, 275
  passages, **0 errors** (45 s). `verify-sources`: **10 of 10 pass** (texts 216, 73, 10, 20 and 4 units).
- e-Gov's bulk download of all laws (one file instead of 18,000 requests) was evaluated and adopted on 2026-10-18: see
  "Bulk download route".

## Bulk download route (2026-10-18)

**Why.** Digital Agency asked API users not to loop over every law fetching texts one after another
(「全法令をループして条文データを順次取得するようなご利用方法についても…お控えください。」,
[public-test page](https://www.digital.go.jp/policies/legal-practice/public-test), read 2026-10-18), and the API's own spec
(`lawapi-v2.yaml` 2.1.139, 「XML一括ダウンロードについて」) says 「法令データをダウンロードする場合は、XML一括ダウンロードにアクセスするか、
以下のURLを利用してダウンロードしてください。」 naming `https://laws.e-gov.go.jp/bulkdownload?file_section=1&only_xml_flag=true`.
Same licence as the API (PDL1.0); no key, registration or notice.

**The archive** (downloaded once through `PoliteHttp`, identifying User-Agent, robots read first, 2026-10-18 11:51 UTC,
**20.1 s**; cached at `var/sources/laws.e-gov.go.jp/bulkdownload/all_xml.zip`):

| | |
|---|---|
| Size | **297,202,960 bytes** (the bulk page's 「283MB」 is 283.4 MiB); no Content-Length or Last-Modified header |
| SHA-256 | `4581bf3a78278a83a18e5a7043f41e8ba56613b5458149db59e47c56979b744c` (pinned in the manifest) |
| Members | 20,935: 10,467 directories `{law_revision_id}/`, each with one file `{law_revision_id}/{law_revision_id}.xml`, and `all_law_list.csv` (6,005,888 bytes); files dated 2026-10-17 15:01-15:03 |
| Uncompressed | 3,303,703,667 bytes (292,974,162 compressed) |
| XML | a bare `<Law>` root (法令標準XML), the element the API returns in `law_full_text` |
| `all_law_list.csv` | UTF-8 with BOM, LF, one line per revision: 法令種別, 法令番号, 法令名, 法令名読み, 旧法令名, 公布日, 改正法令名, 改正法令番号, 改正法令公布日, 施行日, 施行日備考, 法令ID, 本文URL, 未施行 |
| Rows | 10,467 (= the XML files, one-to-one through 本文URL `…/law/{law_id}/{date}_{amending law id}`), 8,990 law ids; **8,991 in force**, **1,476 marked 未施行** (not yet in force), 621 of them with a 施行日備考 (enforcement note: the date is not fixed) |

Every one of the 8,989 manifest laws is in the archive. Laws with no revision in force at all are not (the 16 the list
names are absent). The archive holds the revision in force and the revisions to come, not the earlier ones (one law,
332AC0000000026, also still lists its previous revision in force).

**Same law data as the API.** For 20 laws (the 9 of `tests/fixtures/egov_laws` and 11 fetched for this, below) the archive's
`<Law>` and the API's `law_full_text/Law` of the same revision were compared element for element: tags, texts, tails and
attribute sets are **identical in all 20** (24,103 provisions once parsed). The bytes differ only in the XML declaration
(the files have one) and in the order of attributes (the API sorts them alphabetically).

**What the files lack, and where it comes from.** A bare `<Law>` carries no law id and no revision data. The connector reads
the API's list of laws in force once per archive (10 pages, cached under the archive's SHA-256) and passes each law's
`law_info` and `current_revision_info` (the fields `law_data` puts in `revision_info`) as document metadata
(`METADATA_PARSERS` in the pipeline; verification re-parses with the stored metadata). Checked for the 8,988 laws with one
row in force: the list's revision in force is the archive's in every case (for 332AC0000000026 it is the later of its two
rows); `<LawNum>` = the list's `law_num` 8,988/8,988; the CSV's era dates read as e-Gov's ISO dates: 公布日 8,988/8,988,
改正法令公布日 8,988/8,988, 施行日 = the date in the revision id 10,467/10,467; `LawType` = the list's type except the 21 laws the
list types twice (the file says `Act`, as the text endpoint does). Two fields are taken from the file, not the list:
`LawTitle/@Abbrev` (equal to `law_data`'s `revision_info/abbrev` in 23 of 23 responses; the list writes 296 differently:
278 in another order, 17 with 「抄」 added, and for 326M50000100030 the file has none where the list has one). `amendment_type` cannot be read from the archive (4 laws have no amending law but type 3), so it
comes from the list.

**Laws the API path cannot load.** For 34 laws e-Gov marks the revision in force `PreviousEnforced` and none
`CurrentEnforced` (3 checked through the API: 322AC0000000026, 322AC0000000054, 322CO0000000016). The API path refuses them
("the revision list has no revision currently in force"); the list's `current_revision_info` and the archive agree on that
revision, so the bulk path loads them.

**Not yet in force.** Rows marked 未施行 become the change report's outstanding changes, exactly as `UnEnforced` revisions of
a revision list do: a row with a 施行日備考 has only a placeholder date (in the 18 revision lists at hand, 28 pending
revisions have both a placeholder date and a comment, 30 neither, none one without the other) and is reported as "not yet
fixed", quoting the note. Their texts, which are in the archive, are never yielded.

**All 8,989 laws parsed from the archive** (connector offline over the committed manifest with `history` off, so that every
law comes from the archive; 2026-10-18): 8,989 texts (1,165,105,058 bytes) and 8,989 change reports, **0 fetch errors, 0
parse errors, 0 skipped**; 1,258,761 provisions; constitution 1, act 2,100, cabinet order 2,310, imperial ordinance 66,
ministerial ordinance 4,103, rule 409; 1,476 outstanding changes on 800 laws (621 with a placeholder date); text origin
`official_revised` 7,365, `official_conversion` 1,624. **61 s** in all (6.6 s reading the archive, 54.1 s parsing; a
second run with the final code, while another test suite shared the machine: same counts, 92 s).

**Same documents as the API path, 20 laws.** The bulk path (connector, offline) and the API path (`parse_egov` of the
`law_data` / `law_revisions` responses: the 9 fixtures of 2026-10-14/15 and 20 requests on 2026-10-18 for 11 more laws chosen
for edge cases: the three `PreviousEnforced` laws above, 332AC0000000026 with two rows in force, 地方税法 with 26 pending
amendments, a Rule, 205M10000200030 and 504AC0000000068 whose revision has no amending law, 337AC0000000150 with ruby in its
title, two laws whose abbreviations the list writes differently):

- **Texts: 20 of 20 identical** (work and expression URIs, titles, number, dates, text origin, `ext`, all 24,103 provisions
  with their eIds, texts, headings and statuses), except the locator of the one `version_change` event, which names where the
  revision data was read: `revision_info/law_revision_id[…]` (API) vs `metadata/revision_info/law_revision_id[…]` (bulk).
- **Change reports: 15 compared, outstanding changes identical in all 15** (52 changes; 地方税法 26, 332AC0000000026 14, APPI
  6, 民法 5, 504AC0000000068 1), every other field identical except the events: the bulk report has the amendment in force and
  the promulgation (31 events), the revision list also names the earlier amendments since 2017 (478; 447 not in the bulk
  report). 3 more (the `PreviousEnforced` laws) only the bulk path produces; for 2 no revision list was fetched.
- Acceptable: the events are lifecycle information, not text; the two laws loaded with history keep the full events from the
  API. Not verifiable here: for the later stages of a law entering into force in stages (all-zero amending-law id, e.g.
  防災庁設置法), the bulk report gives no amending-law link; where the API names the id `000000000000000` it links that.

**Slice ingest** (dev database `ulis_jpb`, 2026-10-18; `scripts/ingest_in_batches.py jp-egov-laws --manifest <slice>`; 50 laws:
the five hand-picked laws without history, the 16 other laws above, 29 drawn at random, seeded): documents seen 100, new 100,
instruments 50, instrument versions 50, provision versions **33,739**, passages 29,736, **0 errors**, **75 s**, no request
(everything cached). Re-run: 100 unchanged (8 s). `verify-sources`: **100 of 100 pass**, 33,739 units and 151 lifecycle
events re-derived from the stored documents and their metadata (8 s). 74 outstanding changes on 16 expressions. Searches:
`故意又は過失によって他人の権利` → 民法第709条; `道路運送車両` → 租税特別措置法第90条の12第1項, 地方税法第442条; `激甚災害` →
激甚災害に対処するための特別の財政援助等に関する法律第11条の2第2項.

**Cost of a full load.** Before: 18,014 API requests (8,989 revision lists, 8,987 texts, 14 + 24 texts of the two laws with
history) = **25.0 hours** at 5 s. After: **51 requests** (the archive, 10 list pages, 2 revision lists and 38 texts for 刑法 and
APPI) = 4.3 minutes of request spacing, plus about 20 s to transfer the archive and about 1 minute to parse; the database
work comes on top (the slice wrote 450 provision versions per second; 1,258,761 provisions at that pace would be about
47 minutes, an estimate only). `network_requests: 51` in the manifest gives `load-all --dry-run` that figure.

**Refresh.** e-Gov rebuilds `all_xml.zip` as laws are promulgated and serves only the latest one, so the connector refuses an
archive other than the pinned one, naming its size and SHA-256 (`jp-egov-laws` is now in `STATIC_PINS`: the weekly
`load-all --refresh` skips it instead of failing). To refresh: delete `var/sources/laws.e-gov.go.jp/bulkdownload/all_xml.zip`,
run, review and move the pin (or set `bytes` and `sha256` to null to take what is served); the list is read again for the new
archive (10 requests). A law whose revision is unchanged has the same file bytes, so its documents are recognised by SHA-256
and cost nothing; a new revision becomes a new snapshot. The per-update-date archives
(`…/bulkdownload?file_section=3&update_date=YYYYMMDD&only_xml_flag=true`, the past 3 months; 61 listed, 2026-07-21 to
2026-10-16) are not used: the one of 2026-10-16 (one request: 9,184,551 bytes) holds 94 revision files and `R081016.csv`
(same 14 columns, 94 rows); 89 of the 94 files are byte-identical to the members of the same name in the full archive of
2026-10-17, the other 5 were replaced by then (a later revision of the same day, a newer revision in force) or belong to two
ordinances with no revision in force yet. A full archive gives one consistent state in one request; replaying daily files
would have to rebuild that state.

**Requests to laws.e-gov.go.jp for this work** (2026-10-18, one connection, at least 5 s between request starts, identifying
User-Agent): 37 = 2 archives (all laws; the update of 2026-10-16), 10 list pages, 20 API responses for the comparison and 5
reads of robots.txt (one per run). No 401, 403, 429 or 451; no retry.

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
- **Not-yet-enforced amendments** (`UnEnforced`; 未施行 in the bulk list) are never loaded as text. The revision list (in
  bulk mode: the law's lines of `all_law_list.csv`) is ingested as a *change report* for the current revision: each pending amendment becomes an
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

Tests after widening (2026-10-16, own test database): full suite **1,291 passed**; `tests/test_egov_jp.py` 24 tests (committed manifest, Cabinet Order / Ministerial Ordinance / double-typed / long-number fixtures, connector robustness, PostgreSQL search with citations). ruff, ruff format, mypy pass.

## Known gaps

- History only for 刑法 and APPI and only from 1 April 2017; every other law is the revision in force on the
  day it was fetched (add `history: true` per law to widen, in a fresh database).
- The full 8,989-law load into a database has not been run (every law was parsed from the bulk archive; slices of 50 (bulk),
  35 and 5 (API) were ingested); the database size figures above are estimates.
- Bulk mode: lifecycle events of the laws loaded from the archive are the amendment in force and the promulgation, not the
  earlier amendments since 2017 (447 of 478 events of 15 revision lists compared). The archive is pinned: a refresh needs the
  pin moved by hand (see "Bulk download route"). The per-update-date archives are not used.
- No notices (告示), Diet or Supreme Court rules, treaties, local ordinances or case law: they are not in e-Gov. Laws
  that are repealed, expired or not yet in force at all are not ingested. New laws enter the manifest only when the
  builder is re-run.
- Supplementary provisions of amending laws (改正附則, including transitional and reading-substitution
  rules), the table of contents, the enacting formula, figures and forms are not indexed; furigana dropped.
- Pending amendments are reported for the whole law, not linked to the provisions they change.
- Revision lists are cached; delete `var/sources/laws.e-gov.go.jp/api2/law_revisions` to pick up new
  revisions (API mode and the two laws with history; bulk mode: see "Refresh" above). Adding history for a law already ingested without it requires a fresh database
  (snapshots must be recorded oldest first).
- Japanese Law Translation (English) is unofficial and not ingested. No morphological analysis.
