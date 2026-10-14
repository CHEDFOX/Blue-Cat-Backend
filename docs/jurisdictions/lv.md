# Latvia (LV) - Likumi.lv

**Status:** pilot, profile `draft_unverified` (not reviewed by counsel). Source `lv-likumi`, connector
`likumi_lv`, parser `likumi_lv_html`, module `lv`. Language `lv` (no PostgreSQL text-search configuration
for Latvian exists, so the `simple` configuration is used: no stemming, inflected forms match only when identical).

## Source and licence

- Likumi.lv, run by the official publisher, the state company "Latvijas Vēstnesis". One HTML page per
  document: `https://likumi.lv/ta/id/<id>`. Quotes and links are in `config/sources/lv-likumi.yaml`.
- [Terms of use](https://likumi.lv/par-mums/portala-lietosanas-noteikumi) (updated 09.10.2026): users may "brīvi un
  bez maksas piekļūt portālam un tajā publicētajai informācijai"; they must not act against the portal's security,
  stability or speed. No copying or reuse restriction, no copyright reservation: access approved.
  [Autortiesību likums 6. panta (1)](https://likumi.lv/ta/id/5138-autortiesibu-likums) 1): copyright does not protect
  legislative acts "kā arī šo tekstu oficiālos tulkojumus un oficiālās konsolidētās versijas".
- [robots.txt](https://likumi.lv/robots.txt) is not only asset directories: it also disallows `/*/redakcijas-datums/`,
  `&version_date=` (earlier editions) and about 480 individual document ids; `Crawl-delay: 1` (ULIS waits 5 s). The
  connector reads robots.txt and refuses any manifest id it disallows (none of ours is).
- Status: [footer and About page](https://likumi.lv/par-mums): "Sistematizēti tiesību akti ir informatīvi. Pretrunu gadījumā
  vadās pēc oficiālās publikācijas" (Latvijas Vēstnesis). ULIS records `authoritative = false`, `text_origin =
  official_revised`, source authority `official_non_authentic`.

## What is ingested (manifest `config/manifests/lv-likumi.yaml`)

The current ("spēkā esošā") edition of ten pages: Satversme (57980, constitution), Krimināllikums (88966), Civillikums
as its five published parts (Ievads 90224, Pirmā 90223, Otrā 90222, Trešā 90221, Ceturtā 90220; each part has its own
edition history, so each is an instrument; the combined page 225418 is not used), Fizisko personu datu apstrādes
likums (300099), Darba likums (26019), Administratīvā procesa likums (55567).

## Structure, dates and versions

- Work URI `/lv/likumi/<id>`, expression `.../lav@<edition start>`. Provisions: article (`art-117`, inserted
  `art-38_1` = 38.¹), part (daļa, only where the source prints `(1)`), point (`1)`), sub-point (`a)`), transitional
  provision (`pn-11`), preamble, the legislator's "informatīva atsauce uz ES direktīvām". Unnumbered paragraphs stay
  text of their article. Headings (daļa/sadaļa/nodaļa/apakšnodaļa/I./1.) are context only.
- The page embeds the list of editions with their validity periods and says which one it shows. The document is a
  whole-act snapshot (`revision_snapshot`): every provision runs from the edition's start; where the source states
  the edition's end (Krimināllikums: 10.07.2026-14.10.2026) `valid_to` is the day after; editions published for the
  future ("Nākotnes") are outstanding changes without text. No history: earlier editions are robots-disallowed, so
  dates before the edition start are `history_not_indexed` (work date = adoption date, "Pieņemts").
- The page states no "up to date" date: `CURRENCY_UNKNOWN` on open-ended editions. Amendment annotations ("Ar
  grozījumiem ...") and deletion annotations are notes; a unit that is only "(Izslēgts ...)" is `repealed`.
  `<sup>` becomes superscript digits.

## Citations (module `lv`)

Genitive of the act, containers in the genitive, cited unit in the nominative: `Krimināllikuma 117. pants`, `Satversmes
91. pants`, `Darba likuma 101. panta pirmās daļas 1. punkts`, `Civillikuma 1415. pants`, `Krimināllikuma 38.¹ pants`,
`Krimināllikuma pārejas noteikumu 11. punkts`, `Satversmes ievads`. Genitives are set for the ingested acts only.

## Profile

Civil law, unitary republic; courts Satversmes tiesa, Augstākā tiesa, apgabaltiesas, rajonu (pilsētu) tiesas and the
administrative courts (all to verify); no formal precedent; treaty reception recorded `monist` (to verify). No EU
membership added (the lead adds it).

## Results (2026-10-14)

- Real ingest `ulis_lv`: 10 documents, 10 instruments, 7957 provision versions, 6962 passages indexed, 568 citation
  links (488 resolved), 0 errors, 0 conflicts. `verify-sources`: 10 of 10 pass (7957 units). Provisions: Satversme
  122, Krimināllikums 1697, Civillikums 25 + 486 + 532 + 661 + 1166, FPDAL 224, Darba likums 966, APL 2078.
- Tests: `tests/test_likumi_lv.py` 22 passed (parser, repealed articles/parts/points, citations, robots rules,
  connector offline, ingest, search, point-in-time, idempotent re-ingest); full suite 360 passed on `ulis_test_lv`;
  ruff, ruff format and mypy clean. Fixtures: `tests/fixtures/likumi_lv/` (excerpts, ~110 KB).
- Searches: "Visi cilvēki Latvijā ... likuma un tiesas priekšā" -> Satversmes 91. pants; "Lietas ir ķermeniskas vai
  bezķermeniskas" -> Civillikuma 841. pants; "datu subjekta tiesības apstrāde" -> Fizisko personu datu apstrādes
  likuma 29. pants; "darba devējs uzteikt darba līgumu" -> Darba likuma 110. panta trešā daļa; "slepkavība
  pastiprinošos apstākļos" -> points of Krimināllikuma 117. pants. With `--as-of 2026-10-15` Krimināllikums is not
  returned (the edition held ended 14.10.2026). Failure: "īpašuma tiesības nekustamais īpašums ieilgums" returned
  nothing (no stemming: "ieilgums" does not occur in that form).

## Known gaps

Current edition only (no history; re-ingest to take up a new edition); no other acts, Cabinet regulations, case law
or amending acts; enacting formula, adoption statement, signatures, annexes and tables not ingested; English
translations not ingested; Krimināllikums needs re-ingest after 14.10.2026 (a new edition starts 15.10.2026).
