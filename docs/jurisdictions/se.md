# Sweden - `SE`

Status: **pilot sample**, profile `draft_unverified` (0.1.0). Eight statutes, current consolidated text in Swedish.
## Source and licence (read 2026-10-14, before any statute was fetched)

- Source `se-riksdagen-sfs`: Riksdagens öppna data, `GET https://data.riksdagen.se/dokument/sfs-<year>-<number>.json`
  (type `sfs`, subtype `sfst`). Connector `riksdagen_sfs` (PoliteHttp), parser `riksdagen_sfs_json`, module `se`,
  manifest `config/manifests/se-riksdagen-sfs.yaml`. Document ids: [så fungerar dokument-ID](https://www.riksdagen.se/sv/dokument-och-lagar/riksdagens-oppna-data/anvandarstod/sa-fungerar-dokument-id/).
- [Användningsvillkor](https://www.riksdagen.se/sv/dokument-och-lagar/riksdagens-oppna-data/anvandarstod/anvandningsvillkor/):
  "Riksdagens öppna data är fria att användas och spridas vidare så länge du anger källa" ("Källa: Sveriges riksdag");
  services must not suggest the Riksdag stands behind them; copyright material (images) excluded; GDPR for personal data.
  Statutes have no copyright (lag (1960:729) 9 §, read in the dataset). Review: `approved` (counsel review recommended).
- robots: `data.riksdagen.se/robots.txt` 404; `www.riksdagen.se/robots.txt` disallows nothing. No rate limit published; 5 s delay.
- **Legal status**: the texts are the Government Offices' consolidations (each record: "Källa: Fulltext (Regeringskansliet)",
  `rkrattsbaser.gov.se/sfst`), republished by the Riksdag. Not authentic: a statute is promulgated when published in
  Svensk författningssamling on the website (lag (1976:633) 5 §). Recorded as `official_revised`, `authoritative=false`,
  source `official_non_authentic` → `REVISED_TEXT` + `SOURCE_NOT_AUTHENTIC` on every hit. `svenskforfattningssamling.se`
  answered **403** to the ULIS User-Agent and was not fetched again; its address and the 2018 start are unverified.

## What is ingested (dev DB `ulis_se`)

| Statute | SFS | Consolidated to | Record published | Provisions | Versions | Dated | Prospective | Passages |
|---|---|---|---|---|---|---|---|---|
| Regeringsformen | 1974:152 | SFS 2022:1600 | 2025-10-17 | 597 | 597 | 0 | 0 | 395 |
| Brottsbalk | 1962:700 | SFS 2026:1656 | 2026-10-13 | 2,184 | 2,348 | 33 | 258 | 1,673 |
| Avtalslagen | 1915:218 | SFS 1994:1513 | 2025-10-17 | 114 | 114 | 0 | 0 | 72 |
| Ärvdabalk | 1958:637 | SFS 2026:251 | 2026-07-01 | 509 | 509 | 0 | 0 | 324 |
| Lag om anställningsskydd | 1982:80 | SFS 2022:836 | 2025-10-17 | 366 | 366 | 0 | 0 | 279 |
| Förvaltningslag | 2017:900 | SFS 2025:582 | 2025-10-17 | 178 | 178 | 0 | 0 | 128 |
| Dataskyddslagen | 2018:218 | SFS 2026:1589 | 2026-08-25 | 149 | 154 | 6 | 0 | 105 |
| Rättegångsbalk | 1942:740 | SFS 2026:1657 | 2026-09-08 | 3,629 | 3,752 | 11 | 121 | 2,716 |

## Dates and versions

- The dataset holds **one current consolidation per statute**; no earlier consolidations → `attested_at_edition`
  (start unknown, `ATTESTED_ONLY_AT`); `version_date` = `currency_date` = the record's `publicerad` date.
- **Stated dates only**: a wording that changes is printed twice, `/Upphör att gälla U:2027-01-01/` and
  `/Träder i kraft I:2027-01-01/` (also `Rubriken`, `Kapitlet`). The new wording is recorded from the stated date;
  "den dag som regeringen bestämmer" → `prospective`. Both are reported as outstanding changes (BrB 107, RB 19,
  2018:218 1). Brottsbalken 27-30 kap. (lag 2026:1654) are replaced as whole chapters, prospectively. What a dated new
  wording no longer has (a section, stycke or point) is recorded as repealed from that date. Ingest is idempotent.
- `Lag (2010:1408).` (last amending statute) is editorial → `note` ("Wording from Lag (2010:1408)."), never `text`.
- Repeal notes ("7 § har upphävts genom lag (1988:942).", renumbering "Har betecknats …", ranges "13-27 §§ …") →
  `repealed`, undated, note kept. Repealed statutes (`upphavd`) are refused by the parser.

## Identifiers and citations

eIds: `chapter-3-section-1-paragraph-2-point-1` (chapter only when sections restart per chapter), `-subpoint-a`,
`-indent-2` (strecksats), `transitional-1976-871-point-2`. Stycken are numbered by position (not printed). Work URI
`/se/sfs/1962/700`. Citations (module `se`): `3 kap. 1 § första stycket brottsbalken`, `1 kap. 2 § första stycket
regeringsformen`, `36 § första stycket avtalslagen`, `7 § första stycket lagen (1982:80) om anställningsskydd`,
`9 § första stycket förvaltningslagen (2017:900)`, `2 kap. 1 § första stycket 1 regeringsformen`, `30 kap. 1 § första
stycket andra strecksatsen brottsbalken`, `regeringsformen, övergångsbestämmelserna till SFS 1976:871 punkt 2`.
Codes and fundamental laws are cited by name; "avtalslagen" is an established short name (table in the parser);
others from the title with the SFS number. "första stycket" is cited even for single-stycke sections. To confirm.

## Known gaps

- Eight statutes; no history; no ordinances, other fundamental laws, case law (HD/HFD), treaties or local regulations.
- Chapters/headings are context only; headings and stycke boundaries are inferred from the plain-text layout.
- Hard wraps: no space after a line-final hyphen, so ~15 layout hyphens remain in BrB ("gär-ningsorterna").
- Swedish search uses PostgreSQL `swedish` (no compound splitting). An undated wording's stated end is kept in its note.

## Results (2026-10-14)

- ~24 HTTPS requests (terms, robots, 11 documents incl. 2 reference Acts); the ingest ran from the cache.
- `ruff check`, `ruff format --check`, `mypy ulis`: pass. Full suite on `ulis_test_se`: **226 passed** (129 s),
  10 of them in `tests/test_riksdagen_sfs.py` (7 unit, 3 PostgreSQL; fixtures: 6 gzip responses, 337 KB).
- Fresh `ulis_se`: `migrate`, `sync-catalog` (22 jurisdictions, 14 sources), `ingest se-riksdagen-sfs`: documents 8/8
  new, instruments 8, provision versions 8,018, passages 5,692, conflicts 0, errors 0. Re-run: 8 unchanged.
  `verify-sources`: **8/8 pass** (8,018 units).
- Searches (`jurisdiction=SE`): "uppsägning … sakliga skäl" → `7 § första stycket lagen (1982:80) om anställningsskydd`;
  "berövar annan livet döms för mord" → `3 kap. 1 § första stycket brottsbalken`; "avtalsvillkor jämkas oskäligt" →
  `36 § första stycket avtalslagen`; "prövningstillstånd ledning av rättstillämpningen" → `54 kap. 10 § första stycket
  rättegångsbalken`; "Sveriges utrikes underrättelsetjänst personuppgifter": 0 hits on 2026-10-14, on 2027-02-01
  `1 kap. 3 § första stycket 4 lagen (2018:218) …` (from 2027-01-01, `OUTSTANDING_CHANGES`). Hits carry
  `PROFILE_UNVERIFIED`, `ATTESTED_ONLY_AT`, `AS_OF_AFTER_CURRENCY`, `REVISED_TEXT`, `SOURCE_NOT_AUTHENTIC`.
- Point in time: 2018:218 1 kap. 3 § → old wording (`in_force_date_unverified`) on 2026-10-14, new one on 2027-02-01;
  its new point 4 is `not_yet_in_force` before 2027-01-01; BrB 32 kap. 1 § has a wording valid on 2027-01-01 only.
