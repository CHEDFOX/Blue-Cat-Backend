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
