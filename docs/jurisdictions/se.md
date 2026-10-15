# Sweden - `SE`

Status: **the whole open collection of statutes in force** (5,336 laws, ordinances and notices), profile `draft_unverified`
(0.3.0); current consolidated text in Swedish, no history. It started as a sample of eight statutes (table below).

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

## What is ingested (widened 2026-10-15)

- **Manifest** `config/manifests/se-riksdagen-sfs.yaml`, made by `scripts/build_sfs_manifest.py` from the Riksdag's own document
  list `dokumentlista/?doktyp=sfs&utformat=json` (about 90 requests, 200 records a page). The list serves at most 10,000 hits per
  query (page 51 of 200 answers HTTP 400), so it is read in the default order, then oldest first and newest first, until all
  11,567 records are seen. Of them: **5,336 statutes in force with a YYYY:N number are listed** (the pilot eight first, then by
  number); left out are 5,906 repealed statutes (the list says "Författningen är upphävd"), 192 records without a YYYY:N
  number (the N series, RFS, pre-1900 printed-volume references) and 133 Riksbank / National Debt Office regulations.
- **Scale**: 5,336 documents, one request each at 5 s = **7.4 hours**. From the sample run: about 62 provision versions and
  16 KB of text per statute, so about **340,000 provision versions** and **about 0.5 GB of database** (plus the raw JSON, 26 KB a
  statute, 0.14 GB in the object store). Far below the 15 GB threshold, so the default manifest is the whole list. Ingest in batches:
  `python scripts/ingest_in_batches.py se-riksdagen-sfs --batch 100`.
- **Parser additions** (parser 1.2.0, module 1.2.0): repealed and text-less records are skipped (`ParseSkipped`, not errors);
  a statute with no numbered section ("Regeringen föreskriver att följande uppräkningsfaktorer ska användas ...") is one
  unnumbered provision, cited as the statute; old statutes print "1 §." with a full stop; an annex (`Bilaga`) is its own
  provision instead of being glued to the last transitional point; a one-sentence announcement is not taken for a heading;
  "AVDELNING I." and "m. m." headings are read; the four fundamental laws are cited by name; ids with a
  leading zero (`sfs-1810-0926`) are kept as printed.

### The pilot eight

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
Unnumbered statutes: `första stycket förordningen (2014:1308) om uppräkningsfaktorer ...`; annexes: `bilagan till lagen (2013:125)
om avtal mellan Sverige och Antigua och Barbuda ...` (`bilaga 2 till ...` when numbered). Codes and the four fundamental laws
(regeringsformen, successionsordningen, tryckfrihetsförordningen, yttrandefrihetsgrundlagen) are cited by name; "avtalslagen" is an
established short name (table in the parser); others from the title with the SFS number. "första stycket" is cited even for single-stycke sections. To confirm.

## Known gaps

- No history (one current consolidation per statute); no original statutes, amendment register, case law (HD/HFD) in this source,
  treaties or local regulations; the 192 records without a YYYY:N number and the Riksbank's regulations are not listed.
- Chapters/headings are context only; headings and stycke boundaries are inferred from the plain-text layout; an annex is
  one provision without its table layout. Old sections divided into "mom." (`1 § 1 mom.`, e.g. 1944:407) are not parsed and
  are reported as errors (1 in about 420 in-force statutes sampled).
- Hard wraps: no space after a line-final hyphen, so some layout hyphens remain ("gär-ningsorterna").
- A statute marked in force by the list may be repealed before its record is republished; the parser then skips it.
- Swedish search uses PostgreSQL `swedish` (no compound splitting). An undated wording's stated end is kept in its note.

## Results

- Pilot (2026-10-14, `ulis_se`): 8 statutes, 8,018 provision versions, 5,692 passages, `verify-sources` 8/8, 0 errors; ingest idempotent.
  Point in time: 2018:218 1 kap. 3 § gives the old wording on 2026-10-14 and the new one on 2027-02-01 (its new point 4 is
  `not_yet_in_force` before 2027-01-01); BrB 32 kap. 1 § has a wording valid on 2027-01-01 only. Hits carry `PROFILE_UNVERIFIED`,
  `ATTESTED_ONLY_AT`, `AS_OF_AFTER_CURRENCY`, `REVISED_TEXT`, `SOURCE_NOT_AUTHENTIC`.
- Widening (2026-10-15, sample, not the whole manifest; about 90 list pages and 500 statutes fetched): fresh database, parser 1.2.0,
  random draws from the manifest plus the pilot eight: 150 statutes -> 158 documents, 156 stored, 2 skipped (records with no text),
  0 errors; 200 statutes -> 208 documents, 187 new, 20 already stored, 1 skipped, 0 errors. Before the parser fixes 13 of the first
  100 documents and 3 of the third draw failed (unnumbered statutes, "1 §.", "AVDELNING I.", "m. m.", a one-sentence announcement).
  A draw of 140 from the unfiltered list had 69 repealed (skipped) and one `mom.` error (1944:407, unsupported).
- Final sample database: 408 Swedish statutes (with 311 Irish, 227 Dutch, 18 Danish documents): `verify-sources` **964 of 964 pass**
  (128,588 units re-derived from the raw bytes). `tests/test_riksdagen_sfs.py`: 18 tests (15 unit, 3 PostgreSQL).
- Searches (`jurisdiction=SE`): "berövar annan livet döms för mord" -> `3 kap. 1 § första stycket brottsbalken`; "bidrag till
  arbetslivsmuseer" -> `1 § första stycket förordningen (2000:1078) om bidrag till arbetslivsmuseer`; "skattefrihet anslag
  lågkonjunkturfond" -> `2 § första stycket kungörelsen (1959:82) ...`; "Antigua och Barbuda utbyte av upplysningar i skatteärenden"
  -> `bilagan till lagen (2013:125) ...`; "Sveriges utrikes underrättelsetjänst personuppgifter": 0 hits on 2026-10-14, on 2027-02-01
  `1 kap. 3 § första stycket 4 lagen (2018:218) ...` (`OUTSTANDING_CHANGES`).
