# e-Gov 法令検索 fixtures (Japan)

Unmodified responses of the e-Gov 法令API Version 2 (Digital Agency, Government of Japan),
`https://laws.e-gov.go.jp/api/2/…?response_format=xml`, retrieved on 2026-10-14 by the ULIS
connector (`ulis/sources/connectors/egov_laws.py`). They are stored gzip-compressed in the
connector's cache layout (`cache/laws.e-gov.go.jp/api2/…`), each with the `.meta.json` the
fetcher wrote (URL, status, retrieval time). Tests decompress them.

Licence: 公共データ利用規約（第1.0版） / Public Data License 1.0, compatible with CC BY 4.0
(https://www.digital.go.jp/resources/open_data/public_data_license_v1.0; site terms
https://laws.e-gov.go.jp/terms). 出典：e-Gov法令検索（デジタル庁）（https://laws.e-gov.go.jp/）.

| File | Content |
|---|---|
| `law_revisions/321CONSTITUTION.xml` + `law_data/321CONSTITUTION_19470503_…` | 日本国憲法 (Constitution of Japan) |
| `law_revisions/129AC0000000089.xml` + `law_data/129AC0000000089_20260624_…` | 民法 (Civil Code), revision in force from 2026-06-24 |
| `law_revisions/140AC0000000045.xml` + 4 `law_data/140AC0000000045_…` | 刑法 (Penal Code) as in force from 2016-06-23, 2017-07-13, 2023-07-13 and 2026-05-21 (a partial history: the full history has 14 revisions) |
| `law_revisions/415AC0000000057.xml` + `law_data/415AC0000000057_20261001_…` | 個人情報の保護に関する法律 (APPI), revision in force from 2026-10-01; its list has 6 amendments not yet in force |
