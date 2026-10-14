# Poland (PL) - Sejm ELI API (Dziennik Ustaw)

**Status:** pilot, profile `draft_unverified` (not reviewed by counsel). Source `pl-eli-sejm`, connector `eli_sejm`,
parser `eli_sejm_bundle`, module `pl`. Language `pl` (no PostgreSQL text-search configuration for Polish exists, so the
`simple` configuration is used: no stemming, inflected forms match only when identical).

## Source and licence

- The ELI API of the Chancellery of the Sejm, <https://api.sejm.gov.pl/eli>, serves records and texts of the Dziennik Ustaw.
  Documentation: [eli.html](https://api.sejm.gov.pl/eli.html), [eli_pl.html](https://api.sejm.gov.pl/eli_pl.html) (last
  updated 2025-11-25), [OpenAPI](https://api.sejm.gov.pl/eli/openapi/): **no terms of use, licence, rate limit or ban on
  automated use**. [api.sejm.gov.pl/robots.txt](https://api.sejm.gov.pl/robots.txt) is HTTP 404 (no restrictions);
  [eli.gov.pl/robots.txt](https://eli.gov.pl/robots.txt) is `User-Agent: *` / `Disallow:` (only it and the home page were
  read there). `isap.sejm.gov.pl` answered 403 to the bot in an earlier probe and is **not used**.
- Basis for reuse: Prawo autorskie art. 4 pts 1-2, read from the API's own text
  ([art. 4](https://api.sejm.gov.pl/eli/acts/DU/2022/2509/text.html/artykul%3D4)): "Nie stanowią przedmiotu prawa autorskiego:
  1) akty normatywne lub ich urzędowe projekty; 2) urzędowe dokumenty, materiały, znaki i symbole".
- Status of the text. The Dziennik Ustaw publishes the act as announced (`ogl`) and, as an *obwieszczenie* of the Marszałek
  Sejmu, its consolidated text (*tekst jednolity*, `tj`; [art. 16](https://api.sejm.gov.pl/eli/acts/DU/2019/1461/text.html/artykul%3D16)
  of the Act of 20 July 2000: at least every 12 months if the act was amended). A "tekst ujednolicony" (`uj`) is "bez mocy
  prawnej" ([eli.gov.pl](https://eli.gov.pl)) and PDF only: not ingested. ULIS ingests the **tekst jednolity**:
  `text_origin = official_consolidation`, `authoritative = false`, authority `official_non_authentic` (the authentic law is
  the act and its amendments as published; to verify with counsel).

## What is ingested (manifest `config/manifests/pl-eli-sejm.yaml`)

HTML is parsed, PDF never. For announcements from 2025 on the API offers PDF only (`textHTML: false`), so the newest HTML text
of each act is older than the newest published one. Two successive texts per act (one for the data protection act), 11 in all,
each `Dz.U. year poz. N (legal-status date; provisions)`: Kodeks cywilny `DU/1964/93` 2023/1610 (2023-07-28; 2753), 2024/1061
(2024-06-19; 2765); Kodeks karny `DU/1997/553` 2022/1138 (2022-04-27; 1566), 2024/17 (2023-12-05; 1701); ustawa o ochronie
danych osobowych `DU/2018/1000` 2019/1781 (2019-08-29; 597); Kodeks pracy `DU/1974/141` 2022/1510 (2022-05-26; 1995), 2023/1465
(2023-06-13; 2213); Kodeks postępowania administracyjnego `DU/1960/168` 2023/775 (2023-04-13; 1086), 2024/572 (2024-03-27; 1087);
Kodeks rodzinny i opiekuńczy `DU/1964/59` 2020/1359 (2020-07-14; 682), 2023/2809 (2023-12-06; 699).

**The Constitution (`DU/1997/483`) is not ingested: PDF only.** The Kodeks rodzinny i opiekuńczy takes its place (family law).
The `text.html` of an *act's* own record is the text as announced (the 1964 Kodeks cywilny still speaks of "jednostki gospodarki
uspołecznionej"): not current law, not ingested.

## Structure, dates and versions

- Work URI `/pl/du/<year>/<position>` (the act's Dz.U. address), expression `.../pol@<legal-status date>`. Provisions: article
  (`art-415`; `art-22_1` = 22¹; `art-18_3a` = 18³ᵃ; range `art-109-157`), § (`-par1`), ust. (`-ust1`), pkt (`-pkt2`), lit. (`-lita`),
  `preamble`, and `division` (a heading that only says "(... - uchylony)"). Księga, Część, Tytuł, Dział, Rozdział, Oddział are
  context headings. Every unit's number is checked against its printed label.
- Each text is a whole-act snapshot (`revision_snapshot`): unchanged wording continues, changed wording starts on the later
  text's date, an absent provision ends. The source dates no provision, so `SNAPSHOT_DATES` warns a wording may have applied
  earlier; `currency_date` is the legal-status date (`CURRENCY`, `AS_OF_AFTER_CURRENCY`).
- Each act's API record is a change report on its newest text: later amending acts and later consolidated texts (Kodeks cywilny:
  6 amending acts, tekst jednolity 2025/1071 and 2026/795) are outstanding changes without text; each dated amending act is an
  `amendment` event (113 for Kodeks cywilny).
- Footnotes are notes; publication addresses printed in the text ("(Dz. U. z 2023 r. poz. 775)") are text. A unit that is only
  "(uchylony)" is `repealed` (word in `note`, plus a note that the repeal date is not stated).

## Citations (module `pl`) and profile

`art. 415 k.c.`, `art. 148 § 1 k.k.`, `art. 148 § 2 pkt 1 k.k.`, `art. 22¹ k.c.`, `art. 18³ᵃ k.p.`, `art. 7 k.p.a.`, `art. 23 k.r.o.`,
`art. 35 ust. 1 ustawy o ochronie danych osobowych` (no abbreviation: the genitive of the name). Profile: civil law, unitary,
treaty reception `monist` (Constitution art. 91, to verify); Trybunał Konstytucyjny, Sąd Najwyższy, Naczelny Sąd Administracyjny
and lower courts (to verify); no formal precedent. No EU membership added.

## Results (2026-10-14)

- Real fetch: 29 requests, 5 s apart. Ingest `ulis_pl`: 17 documents (11 texts, 6 change reports), 6 instruments, 11 versions,
  9704 provision versions + 7440 corroborated, 7647 passages, 533 events, 0 errors, 0 conflicts. `verify-sources`: 17 of 17 pass
  (17144 units).
- Tests: `tests/test_eli_sejm.py` 26 passed; full suite 445 passed on `ulis_test_pl`; ruff, ruff format, mypy clean. Fixtures:
  `tests/fixtures/eli_sejm/` (excerpts, ~250 KB).
- Searches: "Kto z winy swej wyrządził drugiemu szkodę" -> art. 415 k.c.; "zabiera w celu przywłaszczenia cudzą rzecz ruchomą" -> art.
  278 § 1 k.k.; "przetwarzanie danych osobowych" -> art. 70 ust. 1 ustawy o ochronie danych osobowych; "pracodawca może rozwiązać
  umowę o pracę bez wypowiedzenia" -> art. 52 § 1 k.p.; `--as-of 2024-01-15` returns the 2023 wording of art. 24 § 1 k.c. ("lub
  zapłaty"), today the 2024 one ("albo zapłaty"). Failure: "kradzież cudzej rzeczy ruchomej" found nothing (the word is not in the code).

## Known gaps

The corpus cannot say what these acts provide today (texts of 2019-2024; newer ones are PDF only); no Constitution, other acts,
regulations, Monitor Polski, treaties or case law; no per-provision dates; the announcement text, footnotes on division headings
and cross-reference links are not kept; unknown markup stops the parse; re-ingesting a recorded text from changed bytes is
refused (the change reports are what refreshes).
