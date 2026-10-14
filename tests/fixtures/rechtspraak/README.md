# Rechtspraak open data fixtures

Real responses of the Open Data van de Rechtspraak, retrieved on 2026-10-14 with the ULIS research bot
(5 s between requests; `https://data.rechtspraak.nl/robots.txt` is 404, i.e. no restrictions) and stored
gzip-compressed, byte for byte, as `<ECLI with ":" replaced by "_">.xml.gz`:

`https://data.rechtspraak.nl/uitspraken/content?id=<ECLI>`

| ECLI | Why it is here |
|---|---|
| `ECLI:NL:HR:2020:591` | short Hoge Raad arrest (art. 81 RO): headnote with art. 6:162 BW, one numbered section, formal relations |
| `ECLI:NL:HR:2021:203` | structured criminal arrest: procedure section, paragroups 3.1 and 3.2, art. 138a Sr |
| `ECLI:NL:RVS:2020:2660` | Raad van State decision whose numbering is part of the text ("3. Gelet op ...") |
| `ECLI:NL:HR:1919:AG1776` | oldest decision (Lindenbaum/Cohen): no structure, a footnote, the publisher's "Wetsverwijzing" markup |
| `ECLI:NL:HR:1965:AB7079` | Kelderluik: cited in a footnote of the Urgenda judgment (resolved decision-to-decision link) |
| `ECLI:NL:HR:2019:2006` | Urgenda: nested paragroups (5.7.1), 51 footnotes, a table, art. 6:162 / 6:163 BW references |

The texts are the Rechtspraak's pseudonymised publications. Decisions are not protected by copyright
(art. 11 Auteurswet); the source is Rechtspraak.nl (see `config/sources/nl-rechtspraak.yaml`).
The legislation these tests link to comes from `tests/fixtures/bwb`.
