# Sweden - `SE`

Status: **the whole open collection of statutes in force** (5,353 laws, ordinances and notices; manifest regenerated
2026-10-18, when 17 statutes whose SFS number carries a balk or page suffix were recovered), profile `draft_unverified`
(0.3.0); current consolidated text in Swedish, no history. It started as a sample of eight statutes (table below).

## Source and licence (read 2026-10-14, before any statute was fetched)

- Source `se-riksdagen-sfs`: Riksdagens öppna data, `GET https://data.riksdagen.se/dokument/sfs-<year>-<number>.json`
  (type `sfs`, subtype `sfst`). Connector `riksdagen_sfs` (PoliteHttp), parser `riksdagen_sfs_json`, module `se`,
  manifest `config/manifests/se-riksdagen-sfs.yaml`. Document ids: [så fungerar dokument-ID](https://www.riksdagen.se/sv/dokument-och-lagar/riksdagens-oppna-data/anvandarstod/sa-fungerar-dokument-id/).
- [Användningsvillkor](https://www.riksdagen.se/sv/dokument-och-lagar/riksdagens-oppna-data/anvandarstod/anvandningsvillkor/):
  "Riksdagens öppna data är fria att användas och spridas vidare så länge du anger källa" ("Källa: Sveriges riksdag");
  services must not suggest the Riksdag stands behind them; copyright material (images) excluded; GDPR for personal data.
  Statutes have no copyright (lag (1960:729) 9 §, read in the dataset). Review: `approved` (counsel review recommended).
- robots: `data.riksdagen.se/robots.txt` 404; `www.riksdagen.se/robots.txt` disallows nothing. No rate limit published (re-checked 2026-10-18); 1 s delay since 2026-10-18 (5 s before).
- **Legal status**: the texts are the Government Offices' consolidations (each record: "Källa: Fulltext (Regeringskansliet)",
  `rkrattsbaser.gov.se/sfst`), republished by the Riksdag. Not authentic: a statute is promulgated when published in
  Svensk författningssamling on the website (lag (1976:633) 5 §). Recorded as `official_revised`, `authoritative=false`,
  source `official_non_authentic` → `REVISED_TEXT` + `SOURCE_NOT_AUTHENTIC` on every hit. `svenskforfattningssamling.se`
  answered **403** to the ULIS User-Agent and was not fetched again; its address and the 2018 start are unverified.

## What is ingested (widened 2026-10-15)

- **Manifest** `config/manifests/se-riksdagen-sfs.yaml`, made by `scripts/build_sfs_manifest.py` from the Riksdag's own document
  list `dokumentlista/?doktyp=sfs&utformat=json` (about 90 requests, 200 records a page). The list serves at most 10,000 hits per
  query (page 51 of 200 answers HTTP 400), so it is read in the default order, then oldest first and newest first, until all
  11,567 records are seen (2026-10-18: 107 list requests). Of them: **5,353 statutes in force with an SFS number are listed**
  (5,336 with a plain YYYY:N number and 17 whose number carries a balk or page suffix, see "Suffixed SFS numbers" below; the
  pilot eight first, then by number); left out are 5,906 repealed statutes (the list says "Författningen är upphävd"), 174
  records without an SFS number of these forms (166 of the N series, 8 supplement references such as `1899:bih. 25`) and 133
  Riksbank / National Debt Office regulations. `load-all --refresh` re-fetches only the statutes already listed; new ones
  appear after the script is re-run (delete the cached list pages `var/sources/data.riksdagen.se/dokumentlista@*` first;
  `docs/deployment.md`, "Refresh").
- **Scale**: 5,353 documents, one request each at 1 s = **1.5 hours** (7.4 hours at the earlier 5 s). From the sample run: about 62 provision versions and
  16 KB of text per statute, so about **340,000 provision versions** and **about 0.5 GB of database** (plus the raw JSON, 26 KB a
  statute, 0.14 GB in the object store). Far below the 15 GB threshold, so the default manifest is the whole list. Ingest in batches:
  `python scripts/ingest_in_batches.py se-riksdagen-sfs --batch 100`.
- **Parser additions** (parser 1.2.0, module 1.2.0): repealed and text-less records are skipped (`ParseSkipped`, not errors);
  a statute with no numbered section ("Regeringen föreskriver att följande uppräkningsfaktorer ska användas ...") is one
  unnumbered provision, cited as the statute; old statutes print "1 §." with a full stop; an annex (`Bilaga`) is its own
  provision instead of being glued to the last transitional point; a one-sentence announcement is not taken for a heading;
  "AVDELNING I." and "m. m." headings are read; the four fundamental laws are cited by name; ids with a
  leading zero (`sfs-1810-0926`) are kept as printed.

### Suffixed SFS numbers (regenerated 2026-10-18)

- **What was wrong.** `scripts/build_sfs_manifest.py` kept only numbers matching `^(\d{4}):(\d{1,4})$`, so the statutes
  whose SFS number carries a suffix were counted among the "records without a number": the two balkar of the 1734 law
  still in force, **Handelsbalk (1736:0123 2)** and **Byggningabalk (1736:0123 1)**, and the statutes of the 1800s and early
  1900s cited with the page of the volume, **Lag (1904:48 s.1) om samäganderätt**, **Lag (1907:36 s.22)** and 13 more. The
  manifest went from **5,336 to 5,353 statutes** (+17, 0 removed, no title changed). Recovered: 1736:0123 1, 1736:0123 2,
  1845:50 s.1, 1851:55 s.4, 1855:82 s.1, 1863:83 s.6, 1866:37 s.1, 1880:48 s. 1, 1885:56 s.1, 1898:64 s.10, 1904:12 s.3,
  1904:26 s.1, 1904:48 s.1, 1906:60 s. 1, 1907:36 s. 22, 1909:53 s. 7, 1910:72 s.1 (as the list prints them). The list
  spells lagen om samäganderätt twice, `1904:48 s.1` and `1904:48s1` (two records, one statute): the canonical spelling is
  listed once. The 8 supplement references (`1878:bih. 56 s. 1`, `1899:bih. 25`, ...) and the 166 N-series records stay out.
- **Document ids and URLs.** The Riksdag's id is the number as printed with the colon replaced, space included:
  `sfs-1736-0123 2`, `sfs-1907-36 s. 22` (checked against all 11,434 `sfst` records: the id is always `sfs-` + the number,
  up to letter case). The manifest's `sfs` is therefore the number as the list prints it, and the connector percent-encodes
  it in the request (`https://data.riksdagen.se/dokument/sfs-1736-0123%202.json`, cached as `sfs-1736-0123_202.json`).
- **One work per statute** (parser 1.3.0, module 1.3.0). `parse_sfs_number` (`ulis/sources/parsers/riksdagen_sfs.py`) reads
  `YYYY:N`, `YYYY:N B` (balk, one digit) and `YYYY:N s.P` (page; also `s. P` and `sP`), and gives one canonical spelling
  (`1736:0123 2`, `1904:48 s.1`, `1907:36 s.22`) and one language-neutral work URI: the serial as an integer plus the suffix
  without spaces or dots, `/se/sfs/1736/123-2`, `/se/sfs/1904/48-s1`, `/se/sfs/1907/36-s22` (plain numbers are unchanged,
  `/se/sfs/1810/926` for 1810:0926). `ext` keeps `sfs_number` (canonical), `number` (123) and `suffix` ("2", "s.1"); the
  title check and `citation_name` tolerate the titles' own spacing ("(1907:36 s.22)" for a record numbered `1907:36 s. 22`).
  Citations: `1 kap. 5 § första stycket handelsbalken`, `2 kap. 1 § byggningabalken`, `1 § första stycket lagen (1904:48 s.1)
  om samäganderätt`. The connector refuses a manifest that lists two spellings of one number.
- **Old layouts the parser now reads** (needed for 2 of the 17): byggningabalken prints repeal notes as `1 § upphävd genom
  förordning (1933:269).` (no "har"), `7 kap. upphävd genom ...`, `har upphävts genom förordning (1885:28 s. 4).` and
  `har upphävts genom förordning ?`; these, and repeals by a förordning, kungörelse or a page-suffixed statute (handelsbalken:
  `3 § har upphävts genom lag (1905:38 s.1).`, 19 sections), are now `repealed` with the note kept (before, the note was
  in-force text). A chapter heading's title must start in upper case or be a repeal note, so `16 kap. och 17 kap. 1, 2 och
  5 §§ jordabalken samt ...` in 3 § of lagen (1907:36 s.22) is the section's text, not a chapter. The 11 earlier fixtures
  parse byte-for-byte as before (digest compared). A database loaded with parser 1.2.0 holds the old reading for such
  statutes and they verify only after re-ingestion into a fresh database.
- **Slice run** (2026-10-18, fresh dev database `ulis_regen`, the four statutes named above plus 1845:50 s.1 and 2017:900;
  6 requests): 6 documents, 6 instruments, 540 provision versions, 297 passages, **0 errors**; `verify-sources` **6 of 6
  pass** (116, 157, 47, 21, 21 and 178 units). Before the parser changes, 2 of the 6 failed with "text outside any section".
  Fixtures: `tests/fixtures/riksdagen/.../sfs-1736-0123-2`, `-1`, `sfs-1904-48-s.1`, `sfs-1907-36-s.22` (real records, gzip).

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
`/se/sfs/1962/700`; a suffixed number keeps its suffix in the serial segment, `/se/sfs/1736/123-2` (handelsbalken),
`/se/sfs/1904/48-s1` (lagen (1904:48 s.1) om samäganderätt). Citations (module `se`): `3 kap. 1 § första stycket brottsbalken`, `1 kap. 2 § första stycket
regeringsformen`, `36 § första stycket avtalslagen`, `7 § första stycket lagen (1982:80) om anställningsskydd`,
`9 § första stycket förvaltningslagen (2017:900)`, `2 kap. 1 § första stycket 1 regeringsformen`, `30 kap. 1 § första
stycket andra strecksatsen brottsbalken`, `regeringsformen, övergångsbestämmelserna till SFS 1976:871 punkt 2`.
Unnumbered statutes: `första stycket förordningen (2014:1308) om uppräkningsfaktorer ...`; annexes: `bilagan till lagen (2013:125)
om avtal mellan Sverige och Antigua och Barbuda ...` (`bilaga 2 till ...` when numbered). Codes and the four fundamental laws
(regeringsformen, successionsordningen, tryckfrihetsförordningen, yttrandefrihetsgrundlagen) are cited by name; "avtalslagen" is an
established short name (table in the parser); others from the title with the SFS number. "första stycket" is cited even for single-stycke sections. To confirm.

## Known gaps

- No history (one current consolidation per statute); no original statutes, amendment register, case law (HD/HFD) in this source,
  treaties or local regulations; the 174 records without an SFS number of the forms read (166 N-series, 8 supplement `bih.`
  references) and the Riksbank's regulations are not listed. New statutes enter the manifest only when the script is re-run.
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
  (128,588 units re-derived from the raw bytes). `tests/test_riksdagen_sfs.py`: 20 tests (17 unit, 3 PostgreSQL) after the
  2026-10-18 regeneration (id forms, the four suffixed fixtures).
- Regeneration (2026-10-18, see "Suffixed SFS numbers"): 107 list requests and 6 statute requests; manifest 5,336 -> 5,353;
  slice of 6 into a fresh `ulis_regen`: 0 errors, `verify-sources` 6 of 6.
- Searches (`jurisdiction=SE`): "berövar annan livet döms för mord" -> `3 kap. 1 § första stycket brottsbalken`; "bidrag till
  arbetslivsmuseer" -> `1 § första stycket förordningen (2000:1078) om bidrag till arbetslivsmuseer`; "skattefrihet anslag
  lågkonjunkturfond" -> `2 § första stycket kungörelsen (1959:82) ...`; "Antigua och Barbuda utbyte av upplysningar i skatteärenden"
  -> `bilagan till lagen (2013:125) ...`; "Sveriges utrikes underrättelsetjänst personuppgifter": 0 hits on 2026-10-14, on 2027-02-01
  `1 kap. 3 § första stycket 4 lagen (2018:218) ...` (`OUTSTANDING_CHANGES`).
