# Croatia (HR) - Narodne novine

**Status:** pilot, profile `draft_unverified` (not reviewed by counsel). Source `hr-narodne-novine`, connector `narodne_novine`,
parser `narodne_novine_html`, module `hr`. Language `hr` (no PostgreSQL text-search configuration for Croatian: the `simple`
configuration is used, no stemming, inflected forms match only when identical). EU membership is not recorded here.

## Source and licence

- Narodne novine d.d. runs the official gazette site <https://narodne-novine.nn.hr>. Its [data access page](https://narodne-novine.nn.hr/data_access.aspx):
  "Use is available to everyone, anonymously and free of charge"; "Access is provided to any internet crawler who seeks to index and use the content";
  yearly lists of all regulations as CSV/XLSX for professional use; ELI metadata "for ... automated and semi-automated processing"; bots asked to
  make at most three requests per second. [robots.txt](https://narodne-novine.nn.hr/robots.txt): about 200 `Disallow` lines naming individual removed
  article pages, the advertisements part and category-3 searches; no Crawl-delay. The connector applies them to the ELI path, to the act's
  year/edition/number and to the redirect target.
- The footer's general terms say all content is protected by copyright and may be used "pod uvjetima propisanim Zakonom" (under the conditions of the law).
  Basis for reuse: Zakon o autorskom pravu i srodnim pravima ([NN 111/2021](https://narodne-novine.nn.hr/eli/sluzbeni/2021/111/1941)) članak 18. stavak 3. (read with this
  parser): official texts "kao što su zakoni, uredbe, odluke" and their collections stop being protected once published "radi službenog informiranja javnosti".
  Counsel review recommended. Authenticity of the web edition was not established: `authoritative = false`, authority `official_non_authentic`.

## What is ingested (manifest `config/manifests/hr-narodne-novine.yaml`)

4,769 acts of the official part, 1990-2026, built from the publisher's yearly lists (37 requests): every `zakon` (4,612, including amending and constitutional laws),
the `pročišćeni tekst` consolidated texts the gazette published (151) and the Constitution (6). Not listed: regulations, decisions, rulings, the international part,
advertisements. The full manifest is about 6.7 hours at 5 s per request; a slice was run (below).

## Structure, dates and versions

- Work URI `/hr/nn/<year>/<edition>/<number>` (the ELI path), expression `.../hrv@<publication date>`. Provisions: `preamble` (promulgation decree, title,
  preamble), `art-12` / `art-12a`, `-par2` (stavak), `-pt3` (točka), `-suba` (podtočka), `zav-1` (closing: Klasa, signature), `pril-N` (annex). Parts and chapters ("I. OPĆE ODREDBE")
  are context headings; the italic line before "Članak N." is the article heading. Structure is read from the printed numbering (old Word-converted pages and current
  markup alike); a line that matches no numbering stays text of the unit above.
- **Each act is the text as published.** The gazette does not consolidate (an amendment is a separate act), the ELI metadata give the adoption and publication dates
  but not the entry into force (it is in the text), and no end date exists. So `valid_from`/`valid_to` are empty, every provision is "in force, start unknown"
  (`EFFECTIVE_DATE_UNKNOWN`, status `in_force_date_unverified`, category `applicability_unverified`), `CURRENCY_UNKNOWN`, and every result carries
  `OUTSTANDING_CHANGES_INSTRUMENT` ("Amendments published after this edition are not applied"). Point-in-time answers are not possible.

## Citations (module `hr`)

`članak 90. Ustava Republike Hrvatske (pročišćeni tekst, NN 85/2010)`, `članak 5. stavak 5. točka 1. Zakona o službenoj iskaznici (NN 30/2023)`, `članak 12.a ...`.
Genitive for `Zakon`, `Ustav`, `Ustavni zakon`, `Kazneni zakon`-type names; other titles: `<title>, članak 5. (NN 1/2020)`.

## Results (2026-10-14/15)

- Tests: `tests/test_narodne_novine.py` 18 passed (parser on three real pages of different eras, connector offline incl. robots-named acts, 404 and redirect
  checks, committed manifest, PostgreSQL ingest, no point-in-time answer, verify).
- Real slice (`ulis_eur`, 26 acts 1990-2025 incl. the Constitution NN 56/1990 and 85/2010, Zakon o radu 2004, Copyright Act 2021; 27 requests): 26 documents,
  6,789 provision versions, 5,924 passages, 0 errors; `verify-sources`: 26 pass. Search "Službeni tekstovi iz područja zakonodavstva ... prestaju biti zaštićeni autorskim
  pravom" -> `članak 18. stavak 3. Zakona o autorskom pravu i srodnim pravima (NN 111/2021)`.

## Known gaps

Amendments not applied (older laws may be repealed); no validity periods; only laws, consolidated texts and the Constitution; regulations, decisions, case law and
local acts not ingested; ELI relations kept in `ext` (amends, repeals) only; tables as text rows; the entry-into-force date is not extracted from the text.
