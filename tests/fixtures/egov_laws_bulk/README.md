# e-Gov bulk download fixtures (Japan)

A small archive in the layout of e-Gov 法令検索's XML bulk download (Digital Agency, Government of Japan),
`https://laws.e-gov.go.jp/bulkdownload?file_section=1&only_xml_flag=true` (`all_xml.zip`), for the connector's
`mode: bulk` (`ulis/sources/connectors/egov_laws.py`). Made by ULIS on 2026-10-18 from the archive downloaded that day
at 11:51 UTC (297,202,960 bytes, SHA-256 `4581bf3a78278a83a18e5a7043f41e8ba56613b5458149db59e47c56979b744c`; the files
are dated 2026-10-17):

- `cache/laws.e-gov.go.jp/bulkdownload/all_xml.zip` (118,428 bytes): 15 revision files copied **unmodified**
  (same member names `{law_revision_id}/{law_revision_id}.xml`, same bytes) and `all_law_list.csv` holding the
  header line and the 22 lines of these 13 laws, **unmodified** (UTF-8 with BOM, LF). The `.meta.json` is the one
  the fetcher wrote for the real archive (URL, retrieval time).
- `cache/laws.e-gov.go.jp/api2/laws/bulk-9924b68f4429864b/offset000000.xml.gz`: the entries (`<law>…</law>`) of
  these 13 laws from the API's list of laws in force (`GET /api/2/laws?repeal_status=None&limit=1000&offset=N`,
  10 pages read on 2026-10-18 at 11:53 UTC), copied **unmodified** into one page whose `total_count` and `count`
  are 13 (the real pages say 9,005 and 1,000). The directory name is the first 16 hex digits of this fixture
  archive's SHA-256, as the connector keys the list to its archive.

| Law | law_id | In the archive |
|---|---|---|
| 日本国憲法 | 321CONSTITUTION | revision in force |
| 個人情報の保護に関する法律 | 415AC0000000057 | revision in force (2026-10-01); 6 list lines not yet in force (3 with a placeholder date) |
| 成田国際空港の安全確保に関する緊急措置法施行令 | 353CO0000000167 | revision in force |
| 密閉形蓄電池の表示の標準となるべき事項を定める省令 | 405M50000400033 | revision in force |
| 昭和二十二年政令第五十二号 / 昭和二十年勅令第七百三十号 | 322CO0000000052, 320IO0000000730 | revision in force (typed twice by the list) |
| 昭和二十二年総理庁・大蔵省・…・司法省令第一号 | 322M40010B72001 | revision in force (law number over 40 characters) |
| 大正五年農商務省令第三十号（農商省主管歳入証券納付ニ関スル件） | 205M10000200030 | revision in force (no amending law, `amendment_type` 3) |
| 会計検査院規則の公布に関する規則 | 322R00000001001 | revision in force (a Rule) |
| 刑法等の一部を改正する法律の施行に伴う関係法律の整理等に関する法律 (令和四年法律第六十八号) | 504AC0000000068 | revision in force (`amendment_law_id` 000000000000000); 1 line not yet in force |
| 刑法施行法 | 141AC0000000029 | revision in force (the list's abbreviation adds 「抄」) |
| 公的年金制度…経過措置に関する省令 | 428M60000040069 | revision in force, which e-Gov marks `PreviousEnforced` with no `CurrentEnforced` |
| 防災庁設置法 | 508AC0000000061 | revision in force (2026-07-17) **and** the texts of its two stages not yet in force (2026-11-02; 2028-07-16, a placeholder): the tests check they are never loaded |

API responses for the same revisions of 11 of these laws are in `tests/fixtures/egov_laws/` (the tests compare the
two routes). Licence: 公共データ利用規約（第1.0版） / Public Data License 1.0, compatible with CC BY 4.0
(https://www.digital.go.jp/resources/open_data/public_data_license_v1.0; site terms https://laws.e-gov.go.jp/terms).
出典：e-Gov法令検索（デジタル庁）（https://laws.e-gov.go.jp/）. Subset made by ULIS (the selection of files and lines and the
list page's counts); the law data itself is unchanged.
