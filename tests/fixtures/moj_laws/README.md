# 全國法規資料庫 fixtures (Taiwan)

Unmodified pages of the Laws & Regulations Database of the Republic of China (Taiwan), Ministry
of Justice (法務部), `https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=<PCode>` (所有條文, the one
law path the site's robots.txt allows), and `https://law.moj.gov.tw/robots.txt`, retrieved on
2026-10-14 by the ULIS connector (`ulis/sources/connectors/moj_laws.py`). They are stored
gzip-compressed in the connector's cache layout (`cache/law.moj.gov.tw/…`), each with the
`.meta.json` the fetcher wrote (URL, status, retrieval time). Tests decompress them. The pages
carry an ASP.NET `__VIEWSTATE` blob and CSP nonces (site state, not secrets).

Licence: 政府資料開放授權條款－第1版 (Open Government Data License, version 1.0, compatible with
CC BY 4.0), https://data.gov.tw/license, applied to the site by its 政府網站資料開放宣告
(https://law.moj.gov.tw/Service/Copyright.aspx). 法務部 2026 全國法規資料庫. 此開放資料依政府資料開放授權條款
(Open Government Data License) 進行公眾釋出，使用者於遵守本條款各項規定之前提下，得利用之。

Consolidation cut-off date (法規整編資料截止日) on every page: 民國 115 年 10 月 02 日 (2026-10-02).

| File | Content |
|---|---|
| `LawClass/LawAll/A0000001.html` | 中華民國憲法 (Constitution), 公布日期 1947-01-01, with preamble (前言) |
| `LawClass/LawAll/A0000002.html` | 中華民國憲法增修條文 (Additional Articles), 修正日期 2005-06-10 |
| `LawClass/LawAll/B0000001.html` | 民法 (Civil Code), 修正日期 2026-08-17; arts 166-1 and 1223 not yet in force (生效狀態) |
| `LawClass/LawAll/C0000001.html` | 中華民國刑法 (Criminal Code), 修正日期 2026-07-22 |
| `LawClass/LawAll/I0050021.html` | 個人資料保護法 (Personal Data Protection Act), 修正日期 2025-11-11; 26 articles not yet in force |
| `LawClass/LawAll/N0030001.html` | 勞動基準法 (Labor Standards Act), 修正日期 2024-07-31; sub-items (目) in art 59 |
| `robots.txt` | `Disallow: /` with `Allow: /LawClass/LawAll.aspx` (and two other paths) |

Added with the widened manifest (retrieved 2026-10-15, same layout, unmodified; consolidation cut-off 民國 115 年 10 月 08 日 = 2026-10-08):

| File | Content |
|---|---|
| `LawClass/LawAll/M0140056.html` | 植物診療機構開業登記及管理辦法 (an order, never amended: 發布日期 2025-08-12) |
| `LawClass/LawAll/N0030028.html` | 最低工資法（112.12.27制定） (a law whose name carries a note in brackets; 公布日期 2023-12-27) |
| `LawClass/LawAll/J0130056.html` | 天然氣事業輸儲設備防災相關設施裝置維修辦法 (an order; art 3 has numbered lists 1. 2. 3. below the 目 level) |
| `LawClass/LawAll/D0000248.html` | 廢 太魯閣國家公園管理處編制表 (repealed: 廢止日期 2024-05-03) |
| `LawClass/LawAll/G0300098.html` | 財政部關務署臺中關編制表 (a chart published as an attachment only: no article text) |
