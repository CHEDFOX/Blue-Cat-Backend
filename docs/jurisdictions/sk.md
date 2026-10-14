# Slovakia (SK) - Slov-lex (static version)

**Status:** pilot, profile `draft_unverified` (not reviewed by counsel). Source `sk-slovlex-static`, connector `slovlex_static`,
parser `slovlex_static_html`, module `sk`, language `sk` (EU membership is added separately).

## Source and licence

- The Ministry of Justice runs Slov-lex, the electronic Collection of Laws (Zbierka zákonov). Its JavaScript portal
  (`www.slov-lex.sk`) points browsers without scripts to the **static version** `https://static.slov-lex.sk/`, where every act has
  a history page (`/static/SK/ZZ/<year>/<number>/`: table of time versions with the first and last day each applies and the amending
  acts) and one HTML page per time version (`.../<YYYYMMDD>.html`). There is no bulk download, so the connector fetches pages.
- [robots.txt](https://www.slov-lex.sk/robots.txt): `User-agent: *` with an empty `Disallow` and sitemaps on the static host; the static
  host has no robots.txt (404). The connector reads it and applies it to every path; no crawl delay is stated, 5 s is kept. Pages carry
  `<meta name="robots" content="noindex, nofollow">` (about search-engine indexing; robots.txt allows them).
- **Licence:** the portal's terms pages are a JavaScript application and could not be read, so the approval rests on statutes read
  from the static pages of the acts: Autorský zákon 185/2015 Z. z. § 5 ods. 1 písm. b) (legislation is not protected by copyright),
  211/2000 Z. z. § 21f (public licence for reuse of information) and 400/2015 Z. z. § 12 ods. 4 (the electronic Collection is free of
  charge). Quotes are in the source YAML. Counsel review of database rights and of the portal's terms is recommended.
- **Authority:** every page says "Obsah zobrazeného právneho predpisu má informatívny charakter"; only the promulgated text and
  consolidated texts the Ministry declares binding (400/2015 Z. z. § 14) are binding. So `authoritative = false`,
  `official_consolidation`, source authority `official_non_authentic`.

## What is ingested

`config/manifests/sk-slovlex-static.yaml`: 31 acts, as of 2026-10-14. Whole history (every time version, about 400 pages) of the
Constitution 460/1992 Zb., Občiansky zákonník 40/1964 Zb., Obchodný zákonník 513/1991 Zb., Trestný zákon, Trestný poriadok, Zákonník
práce, the three procedure codes of 2015 (CSP, CMP, SSP) and the data-protection act 18/2018; the current version of 21 other main acts.
About 420 pages at one per 5 s is 40 minutes of network time: the run here used a smaller manifest (see Results).
The promulgated text (`vyhlasene_znenie.html`) is not fetched; time versions after `as_of` are not taken.

## How versions and dates work

Each page is a **whole-instrument snapshot** (`validity_basis = revision_snapshot`, shared `ulis/ingestion/snapshots.py`) with
`data-from`/`data-to` from the page's `ucinnost_header`: every provision gets the period (end = day after `data-to`, open if empty);
unchanged wording continues, an absent provision ends. The source gives no dates for single provisions, so `source_periods` does not apply.
Identifiers: work `/sk/zz/<year>/<number>`, expression `.../slk@<date>`, provision `eid` = the page's own element id
(`paragraf-79.odsek-3.pismeno-a`, `ustavnyclanok-25.odsek-1`), `num_path` `["sec:79", "sub:3", "pt:a"]`. The article that holds the act
itself (Čl. I) is not part of the path; articles of amending acts are (`art:II`, `item:1`). An unnumbered odsek is the text of its parent;
footnote markers stay in the text as superscripts and the footnote is the provision's `note` (its links to other acts are `cites`
references, as are links in the text and within the act: 21,715 links stored, 15,190 resolved to ingested acts).

## Citations

`§ 420 Občianskeho zákonníka`, `§ 144 ods. 1 Trestného zákona`, `§ 13 ods. 1 písm. a) Zákonníka práce`, `čl. 2 ods. 1 Ústavy Slovenskej
republiky`, `§ 5 ods. 2 zákona č. 18/2018 Z. z.` (`Zb.` for acts of 1992 and earlier). Genitive names of 12 codes are a table in
`ulis/jurisdictions/modules/sk.py`; other acts are cited by number. Convention to be confirmed by a Slovak reviewer.

## Results (2026-10-14)

- Dev run into `ulis_sk` with a temporary manifest (same 31 acts; Constitution and 18/2018 with full history, the other 8 codes since
  2025-09-01, the rest current): 88 pages (80 requests, 0 fetch errors), 88 versions ingested, 31 instruments, 48,885 provision
  versions created and 90,401 corroborated, 43,588 passages, 0 errors, 384 s. `verify-sources`: 88 of 88 pass.
- Searches (CLI): "Každý zodpovedá za škodu, ktorú spôsobil porušením právnej povinnosti" -> `§ 420 ods. 1 Občianskeho zákonníka`; "Kto iného
  úmyselne usmrtí" -> `§ 145 ods. 1 Trestného zákona`; "Štátna moc pochádza od občanov" -> `čl. 2 ods. 1 Ústavy Slovenskej republiky`;
  "Prevádzkovateľ je povinný pri získavaní osobných údajov" -> `§ 19 ods. 2 zákona č. 18/2018 Z. z.`; with `--as-of 1996-05-01`, `čl. 25 ods. 1`
  returns the 1992 wording ("... je vecou cti každého občana") instead of the 2001 one ("... povinnosťou a vecou cti občanov").
- Tests: `tests/test_slovlex_static.py` 15 (parser, citations, connector offline and refusals, 3 PostgreSQL). Full suite: 768 passed (shared with CZ). ruff, mypy clean.

## Known gaps

Mirrors `coverage.known_gaps` of the profile: about 31 acts only (no regulations, ministerial collections, treaties or case law);
Slovak only; the full-history crawl of the manifest was not run here; time versions are informative, not binding; the promulgated
texts are not ingested; tables and annexes are flattened; no marker for repealed provisions was seen in the pages (a text consisting
of "Zrušený" is handled but untested on real data); Slovak has no PostgreSQL stemmer (`simple`, exact word forms needed); the portal's
terms of use could not be read.
