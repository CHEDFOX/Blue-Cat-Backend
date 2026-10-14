# Riigi Teataja fixtures (Estonia)

Consolidated texts (terviktekstid) in Riigi Teataja's `oigusakt` XML, exactly as stored in the open-data archive
`https://www.riigiteataja.ee/avaandmed/ERT/xml.2026.zip` (members `<globaalID>.xml`, read with HTTP range requests on
2026-10-14, archive generated 2026-10-11), gzip-compressed. Files marked "excerpt" keep only the listed chapters
(peatükid); the cut chapters are removed from the bytes, nothing else is changed.

| File (`<globaalID>.xml.gz`) | Act | Valid |
|---|---|---|
| `127042011002` | Eesti Vabariigi põhiseadus (PS) | 2011-07-22 - 2015-08-12 |
| `115052015002` | PS | 2015-08-13 - 2025-07-08 |
| `111042025002` | PS | 2025-07-09 - 2026-02-28 |
| `111042025003` | PS | 2026-03-01 - |
| `104012019011` | Isikuandmete kaitse seadus (IKS, original text) | 2019-01-15 - 2023-10-31 |
| `106032026010` | IKS | 2026-03-16 - 2026-09-30 |
| `130062026052` | IKS | 2026-10-01 - |
| `106072023031` | Haldusmenetluse seadus (HMS) | 2024-01-01 - 2026-12-31 |
| `109072026028` | Karistusseadustik (KarS), excerpt: chapters 1, 6, 9, 16 | 2026-10-01 - 2026-12-31 |
| `120062026018` | Võlaõigusseadus (VÕS), excerpt: chapters 1, 5, 7, 12, 13 | 2026-09-01 - 2026-12-31 |
| `103072026034` | Töölepingu seadus (TLS), excerpt: chapters 1, 5, 6 | 2026-07-13 - 2026-09-30 |
| `103072026035` | TLS, excerpt: chapters 1, 5, 6 | 2026-10-01 - 2026-12-31 |

Licence: legislation is not protected by copyright (Autoriõiguse seadus § 5(1) p 3); Riigi Teataja is free of charge
(Riigi Teataja seadus § 7(1)) and offers these files as open data ("avaandmed"). See
`config/sources/ee-riigi-teataja.yaml`.
