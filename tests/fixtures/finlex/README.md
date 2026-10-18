# Finlex fixtures

Real, unmodified responses of the Finlex open data API (Ministry of Justice, Finland;
https://www.finlex.fi/en/open-data), retrieved on 2026-10-14 by the `finlex` connector and kept
in its cache layout (`cache/<host>/<path>` plus `.meta.json` with the URL and retrieval time).
The API serves them gzip-encoded; they are stored exactly as received.

| File (under `.../statute-consolidated/`) | Statute |
|---|---|
| `1999/731/{fin,swe}@latest` | Constitution of Finland (Suomen perustuslaki / Finlands grundlag), version 20180817 |
| `2018/1050/{fin,swe}@20250437`, `{fin,swe}@latest` | Data Protection Act (Tietosuojalaki / Dataskyddslag), versions 20250437 and 20260380 |
| `1889/39-001/{fin,swe}@latest` | Criminal Code (Rikoslaki / Strafflag), version 20260795 |

Licence: Creative Commons Attribution 4.0 (Finlex FAQ, https://www.finlex.fi/en/faq). Statutes
are not protected by copyright in Finland (Copyright Act 404/1961, 9 §). The consolidated texts
are not authentic; see `docs/jurisdictions/fi.md`.

Added with the widened manifest (retrieved 2026-10-15, same layout and encoding):

| File (under `.../statute-consolidated/`) | Statute |
|---|---|
| `2024/718/{fin,swe}@latest` | Verohallinnon päätös autoveroilmoituksella ilmoitettavista tiedoista / Skatteförvaltningens beslut om uppgifter som ska lämnas i bilskattedeklarationen (718/2024): a decision, not an Act or decree |
| `1932/246/fin@latest` | Asetus eräiden rahalaitosten oikeudesta pitää shekkitiliä (246/1932): in force, but the source holds no text (`contentAbsent`, 3.7 KB) |
| `2020/1175/fin@latest` | A statute with text whose `isInForce` is false |

Added with the download package route (2026-10-18):

`package/statute-consolidated-sample.zip` (85,915 bytes) is a small zip built for the tests from 14 real members of
Finlex's download package `https://www.finlex.fi/files/open-data/archives/statute-consolidated.zip` (the package of
Last-Modified Sat, 17 Oct 2026 23:27:28 GMT, 4,397,198,140 bytes, SHA-256
`8c07c5b96f4d5063b841bb156eb204fb8f783c6e96d34c810555200aa7ce3e45`, downloaded on 2026-10-18). Each `main.xml` is the
member's content unchanged, under its own name and date (re-compressed; the zip container itself is not Finlex's), with
the directory entries the package has for them:

| Members (under `akn/fi/act/statute-consolidated/`) | Why |
|---|---|
| `2024/718/{fin,swe}@` | Only the version as enacted; byte-identical to the API fixtures `2024/718/{fin,swe}@latest` |
| `1932/246/fin@` | A shell (`contentAbsent`); byte-identical to the API fixture |
| `2020/1175/fin@`, `fin@20211065`, `fin@20220521` | As enacted plus two later versions: the latest is the highest-numbered; byte-identical to the API fixture `fin@latest` |
| `2009/330/fin@20240072`, `{fin,swe}@20250869` | Numbered versions only; byte-identical to the API responses `2009/330/{fin,swe}@latest` |
| `2018/1050/fin@`, `fin@20250437`, `fin@20260380` | History: `fin@` is the API's `fin@20181050` byte for byte |
| `2023/64/fin@`, `fin@20250006` | The package lacks version 20260871, which its metadata names: the connector takes the API's `fin@latest` |

API responses added to `cache/` (fetched 2026-10-18 by the `finlex` connector, gzip-encoded as received):
`2009/330/{fin,swe}@latest` (version 20250869), `2018/1050/fin@20181050` (as enacted), `2023/64/fin@latest` (version
20260871, newer than the package's). Same licence (CC BY 4.0, Finlex FAQ).
