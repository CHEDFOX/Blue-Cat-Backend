# Riksdagen SFS fixtures

Real, unmodified responses of the Riksdag's open data API
(`https://data.riksdagen.se/dokument/sfs-<year>-<number>.json`), retrieved on 2026-10-14 (the next five on 2026-10-15, the last four on 2026-10-18) with the
ULIS User-Agent and kept in the `PoliteHttp` cache layout (`<host>/<path>` plus `.meta.json` with
the URL and retrieval time; the four statutes whose number carries a suffix are stored under a file name without
the space of the Riksdag's id, which the meta file gives). Each JSON response is stored gzip-compressed (`.json.gz`) to keep the
repository small; the tests decompress it, and the bytes are exactly those received.

| File (under `data.riksdagen.se/dokument/`) | Statute | Consolidated to |
|---|---|---|
| `sfs-1974-152.json.gz` | Kungörelse (1974:152) om beslutad ny regeringsform (Regeringsformen) | SFS 2022:1600 |
| `sfs-1962-700.json.gz` | Brottsbalk (1962:700) | SFS 2026:1656 |
| `sfs-1915-218.json.gz` | Lag (1915:218) om avtal och andra rättshandlingar på förmögenhetsrättens område | SFS 1994:1513 |
| `sfs-1958-637.json.gz` | Ärvdabalk (1958:637) | SFS 2026:251 |
| `sfs-1982-80.json.gz` | Lag (1982:80) om anställningsskydd | SFS 2022:836 |
| `sfs-2018-218.json.gz` | Lag (2018:218) med kompletterande bestämmelser till EU:s dataskyddsförordning | SFS 2026:1589 |
| `sfs-2014-1308.json.gz` | Förordning (2014:1308) om uppräkningsfaktorer ... (no numbered section) | - |
| `sfs-1959-82.json.gz` | Kungörelse (1959:82) om skattefrihet för vissa anslag ... (sections printed "1 §.") | - |
| `sfs-2013-125.json.gz` | Lag (2013:125) om avtal mellan Sverige och Antigua och Barbuda ... (an annex, 33 KB) | - |
| `sfs-2000-162.json.gz` | Tillkännagivande (2000:162) av uppgift om Riksbankens diskonto (one sentence, no section) | - |
| `sfs-2005-676.json.gz` | Förordning (2005:676) om statsbidrag till viss påbyggnadsutbildning (repealed: skipped) | - |
| `sfs-1736-0123-2.json.gz` | Handelsbalk (1736:0123 2): a balk suffix in the SFS number (Riksdag id `sfs-1736-0123 2`, URL `sfs-1736-0123%202.json`); retrieved 2026-10-18 | SFS 1986:798 |
| `sfs-1736-0123-1.json.gz` | Byggningabalk (1736:0123 1): balk suffix; "1 § upphävd genom förordning (1933:269)." repeal notes; retrieved 2026-10-18 | SFS 1981:426 |
| `sfs-1904-48-s.1.json.gz` | Lag (1904:48 s.1) om samäganderätt: a page suffix (Riksdag id `sfs-1904-48 s.1`); retrieved 2026-10-18 | SFS 2010:26 |
| `sfs-1907-36-s.22.json.gz` | Lag (1907:36 s.22) om vad iakttagas skall ... (record number `1907:36 s. 22`, id `sfs-1907-36 s. 22`; 3 § enumerates "16 kap. ..." as text); retrieved 2026-10-18 | SFS 1943:883 |

Terms: "Riksdagens öppna data är fria att användas och spridas vidare så länge du anger källa"
(https://www.riksdagen.se/sv/dokument-och-lagar/riksdagens-oppna-data/anvandarstod/anvandningsvillkor/).
Källa: Sveriges riksdag. Statutes are not protected by copyright in Sweden (lag (1960:729) 9 §).
The consolidated texts come from Regeringskansliets rättsdatabaser and are not authentic; see
`docs/jurisdictions/se.md`.
