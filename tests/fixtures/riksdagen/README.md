# Riksdagen SFS fixtures

Real, unmodified responses of the Riksdag's open data API
(`https://data.riksdagen.se/dokument/sfs-<year>-<number>.json`), retrieved on 2026-10-14 with the
ULIS User-Agent and kept in the `PoliteHttp` cache layout (`<host>/<path>` plus `.meta.json` with
the URL and retrieval time). Each JSON response is stored gzip-compressed (`.json.gz`) to keep the
repository small; the tests decompress it, and the bytes are exactly those received.

| File (under `data.riksdagen.se/dokument/`) | Statute | Consolidated to |
|---|---|---|
| `sfs-1974-152.json.gz` | Kungörelse (1974:152) om beslutad ny regeringsform (Regeringsformen) | SFS 2022:1600 |
| `sfs-1962-700.json.gz` | Brottsbalk (1962:700) | SFS 2026:1656 |
| `sfs-1915-218.json.gz` | Lag (1915:218) om avtal och andra rättshandlingar på förmögenhetsrättens område | SFS 1994:1513 |
| `sfs-1958-637.json.gz` | Ärvdabalk (1958:637) | SFS 2026:251 |
| `sfs-1982-80.json.gz` | Lag (1982:80) om anställningsskydd | SFS 2022:836 |
| `sfs-2018-218.json.gz` | Lag (2018:218) med kompletterande bestämmelser till EU:s dataskyddsförordning | SFS 2026:1589 |

Terms: "Riksdagens öppna data är fria att användas och spridas vidare så länge du anger källa"
(https://www.riksdagen.se/sv/dokument-och-lagar/riksdagens-oppna-data/anvandarstod/anvandningsvillkor/).
Källa: Sveriges riksdag. Statutes are not protected by copyright in Sweden (lag (1960:729) 9 §).
The consolidated texts come from Regeringskansliets rättsdatabaser and are not authentic; see
`docs/jurisdictions/se.md`.
