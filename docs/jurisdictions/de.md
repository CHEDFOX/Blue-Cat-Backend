# Germany (DE): federal law from Gesetze im Internet

Status: **pilot sample, draft unverified profile.** Ten federal laws, current consolidation only,
German only. Research aid, not legal advice. Not reviewed by a German lawyer.

| | |
|---|---|
| Profile | `config/jurisdictions/de.yaml` (`DE`, parent `INT`, `draft_unverified`, version 0.1.0) |
| Source | `config/sources/de-gesetze-im-internet.yaml` (`de-gesetze-im-internet`, active) |
| Manifest | `config/manifests/de-gesetze-im-internet.yaml` (10 laws) |
| Connector | `ulis/sources/connectors/gesetze_im_internet.py` (on `PoliteHttp`) |
| Parser | `ulis/sources/parsers/gesetze_im_internet.py` (`gii_norm_xml` 1.0.0) |
| Citations | `ulis/jurisdictions/modules/de.py` (`de`) |
| Tests | `tests/test_gesetze_im_internet.py`, fixtures in `tests/fixtures/gesetze_im_internet/` |

## Source and licence

Gesetze im Internet (https://www.gesetze-im-internet.de) publishes "nahezu das gesamte aktuelle
Bundesrecht" in its currently applicable wording, consolidated continuously by the documentation
unit of the Federal Office of Justice. What the site itself says (read on 2026-10-14):

| Page | Finding |
|---|---|
| [Home page](https://www.gesetze-im-internet.de/index.html) | "Die Rechtsnormen in deutscher Sprache stehen in allen angebotenen Formaten zur freien Nutzung und Weiterverwendung zur Verfügung." / "Wichtig: Die im Internet abrufbaren, konsolidierten Gesetzestexte sind nicht die amtliche Fassung. Diese finden Sie im Bundesgesetzblatt" (electronic since 1 January 2023, www.recht.bund.de). |
| [Hinweise](https://www.gesetze-im-internet.de/hinweise.html) | Item 6: the XML format "ist insbes. für die Nutzer geeignet, die die hier zur Verfügung gestellten Gesetze und Rechtsverordnungen über automatisierte Verfahren weiterverarbeiten möchten"; a daily table of contents is at `/gii-toc.xml`. Item 3: "Stand" names the Ausfertigung of the last amending Act; amendments that enter into force later change the wording only when they enter into force. Item 4: "textlich nachgewiesen, dokumentarisch noch nicht abschließend bearbeitet" concerns documentary data only, not the text. Item 5: some provisions are published with their heading only. Item 7: deep links expressly allowed. |
| [Impressum](https://www.gesetze-im-internet.de/impressum.html) | Publisher: Bundesrepublik Deutschland, represented by the Federal Minister of Justice and Consumer Protection; editor: Bundesamt für Justiz (Kompetenzzentrum Rechtsinformationssystem des Bundes); "Die Bereitstellung der Daten und die technische Umsetzung dieses Internetangebots erfolgen durch die juris GmbH." No guarantee of completeness, correctness or currency. |
| [Datenschutz](https://www.gesetze-im-internet.de/datenschutz.html) | Access logs with anonymised IP and user agent; no tracking. |
| [robots.txt](https://www.gesetze-im-internet.de/robots.txt) | `User-agent: *` / `Disallow:` (everything allowed, no Crawl-delay). |
| [§ 5 UrhG](https://www.gesetze-im-internet.de/urhg/__5.html) | Abs. 1: "Gesetze, Verordnungen, amtliche Erlasse und Bekanntmachungen sowie Entscheidungen und amtlich verfaßte Leitsätze zu Entscheidungen genießen keinen urheberrechtlichen Schutz." |
| [Translations](https://www.gesetze-im-internet.de/Teilliste_translations.html) | The translations "may be used in accordance with the applicable copyright exceptions"; any other use "requires the prior consent of the author or other rights holder". **Not ingested.** |

Conclusion: licence review **approved** for the German-language texts (redistribution,
automated processing and commercial use allowed; bulk download with polite retrieval). ULIS
attributes the source on every result although the site does not require it, and states on
every result that the text is not the official version. Counsel review is still recommended
before production use (for example on database rights in the compiled collection, which the
free-use statement for "alle angebotenen Formate" appears to cover). The optional English
translations were skipped because their user notice requires consent beyond copyright exceptions.

Retrieval: `PoliteHttp` with User-Agent `ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)`,
5 s between requests, HTTPS to `www.gesetze-im-internet.de` only, cache under
`var/sources/www.gesetze-im-internet.de/`. The whole onboarding made 20 requests to the site, one of them an automatic retry (the
pages above, the table of contents, and 12 `xml.zip` downloads: the ten laws, plus SGB I and
GewStG while choosing the tax/social law). The DTD was never fetched.

## What is ingested

One `xml.zip` per law, stored byte-for-byte as downloaded on 2026-10-14 (UTC). "Build date" is
`dokumente/@builddate`, used as version and currency date.

| Law | Kind | Ausfertigung (work date) | Build date | Provision versions | of which repealed | Passages | SHA-256 of the download |
|---|---|---|---|---|---|---|---|
| Grundgesetz (GG) | constitution | 1949-05-23 | 2026-05-06 | 788 | 7 | 639 | `b0dbbb71c7d04ed3…` |
| Bürgerliches Gesetzbuch (BGB) | act | 1896-08-18 | 2026-09-26 | 7,653 | 81 | 6,205 | `a4f41098aad1932d…` |
| Strafgesetzbuch (StGB) | act | 1871-05-15 | 2026-09-28 | 2,990 | 52 | 2,555 | `43e12baef8aed204…` |
| Zivilprozessordnung (ZPO) | act | 1950-09-12 | 2026-09-30 | 3,775 | 52 | 3,068 | `8407085890c8dc7a…` |
| Strafprozeßordnung (StPO) | act | 1950-09-12 | 2026-10-06 | 2,999 | 19 | 2,500 | `fb59fced4b71b550…` |
| Handelsgesetzbuch (HGB) | act | 1897-05-10 | 2026-07-09 | 3,275 | 48 | 2,754 | `063bb99b4f50f63a…` |
| Verwaltungsverfahrensgesetz (VwVfG) | act | 1976-05-25 | 2026-07-28 | 621 | 4 | 534 | `02be966cf1910760…` |
| Bundesdatenschutzgesetz (BDSG) | act | 2017-06-30 | 2026-07-13 | 608 | 0 | 540 | `4b4161639fae051a…` |
| Kündigungsschutzgesetz (KSchG) | act | 1951-08-10 | 2026-05-06 | 106 | 1 | 90 | `bfeebd2320aa0dde…` |
| Gewerbesteuergesetz (GewStG) | act | 1936-12-01 | 2026-08-04 | 450 | 19 | 352 | `904df3757e91157e…` |
| **Total** | | | | **23,265** | **283** | **19,237** | |

Each law is one work (`/de/bund/<abbr>`, e.g. `/de/bund/bgb`, `/de/bund/bdsg_2018`: the site's
path, which is the juris abbreviation lower-cased) with one German expression
(`/de/bund/bgb/deu@2026-09-26`).

## How the XML is read

- **XML safety.** The file declares `<!DOCTYPE dokumente SYSTEM ".../gii-norm.dtd">`. The parser
  strips exactly that bare declaration (an external DTD reference with no internal subset) and
  parses with defusedxml with DTDs, entities and external references forbidden. Any other
  DOCTYPE is rejected; tests check an internal-subset entity, billion laughs and an external
  entity. The archive must contain exactly one XML file (size-capped).
- **Structure.** The first `<norm>` holds metadata (`jurabk`, `amtabk`, `ausfertigung-datum`,
  `fundstelle`, `langue`, `standangabe`). `gliederungseinheit` norms are headings (Buch,
  Abschnitt, Titel). Every other norm is a provision named by `enbez`: `§ 823` → `para-823`,
  `Art 1` → `art-1`, `Präambel`, repealed ranges `(XXXX) §§ 3 bis 6` → `paras-3-bis-6`.
  Inside a norm: `<P>` starting `(1)` → Absatz (`-abs-1`); `<DL>` items `1.` → Nummer (`-nr-1`),
  `a)` / `aa)` / `aaa)` → Buchstabe (`-buchst-a`, `-buchst-aa`); in tax laws
  `<SUP class="Rec">n</SUP>` → Satz (`-satz-n`, sentence 1 is implicit when the first marker is
  2). Labels go into `num_path`; a leaf's text starts after its own label; containers keep the
  labels of their children. The superscript sentence numbers are structure and are not in the text.
- **Lists that cannot be cited safely stay text.** Lists with non-citable labels (roman
  numerals, dashes, balance-sheet schemes) are not split. If one Absatz has several lists that
  restart their numbering or use different kinds of label, they belong to different (unmarked)
  sentences, and "Abs. 1 Nr. 1" would be ambiguous, so none is split (e.g. § 4 Abs. 1 BDSG,
  § 20 Abs. 5 VwVfG). One list continued over two `<DL>` elements is split (Art. 73 Abs. 1 GG).
- **Notes, not text.** Official footnotes (`<Footnotes>`, e.g. "Notifiziert gemäß der Richtlinie
  (EU) 2015/1535") and editorial notes (`<fussnoten>`, e.g. "nach Maßgabe der Entscheidungsformel
  mit GG unvereinbar und nichtig gem. BVerfGE v. 26.2.2020" on § 217 StGB) go into `note` on
  the provision and all its subdivisions, shortened to 1,200 characters. Footnote markers are
  dropped from text and headings.
- **Repealed.** `(weggefallen)` as heading or text (for norms, Absätze or items), or a norm with no
  text whose editorial note says "Weggefallen" (HGB §§ 153 bis 160): status `repealed`, empty
  text, note `(weggefallen)` plus any editorial note. BGB § 651 "(jetzt § 650)" has no text and
  stays an unindexed in-force placeholder.
- **Skipped norms.** `Inhaltsübersicht` (table of contents) and `Anhang EV` (extracts of the
  Unification Treaty, another instrument) are not provisions.
- **Instrument type.** `GG` → `constitution`; a long title containing the word "Verordnung" (and
  not starting with "Gesetz") → `ordinance`; otherwise `act`.
- `text_origin = official_revised` (consolidation by the official publisher's editors, not the
  authentic text), `authoritative = false`, source authority `official_non_authentic`.

## Dates and versions

The XML has **no per-provision dates**. Per-norm `builddate` equals the document's build time.
What it does give: `ausfertigung-datum` (the original Act, e.g. BGB 1896-08-18), the `Stand`
(e.g. "zuletzt geändert durch Art. 6 G v. 23.7.2026 I Nr. 226") and `dokumente/@builddate`.
Because the site applies amendments when they enter into force (Hinweise item 3), each file is
the text in force when it was built. ULIS therefore records:

- `validity_basis = attested_at_edition`, every provision version with `valid_from = None`;
- `version_date = currency_date = builddate`, `work_date = ausfertigung-datum`;
- events: `enactment` (Ausfertigung, with the Fundstelle) and `version_change` (build date);
- `ext.standangaben`: every Stand/Neuf/Hinweis/Sonst entry verbatim. A `Hinweis` saying the
  amendment is "textlich nachgewiesen" is already in the text and stays informational; any other
  `Hinweis` becomes an outstanding change. None of the ten laws has one today.

Effect on answers: a search or lookup shows the current text with `ATTESTED_ONLY_AT` ("The source
attests this text as in force on <build date> ... When it took effect, and whether it applied on
<date>, is not recorded") and authority `applicability_unverified`; point-in-time lookups return
`in_force_date_unverified`, never "not in force". Searches for dates before a law's build date
carry `HISTORY_INCOMPLETE`. Every hit also carries `REVISED_TEXT` and `SOURCE_NOT_AUTHENTIC`.
Repealed provisions have no recorded repeal date, so a lookup returns `no_version` with the
`repealed` entry (note `(weggefallen)`) in the timeline.

Refreshing: the connector serves cached downloads. To refresh, delete the cache entries; a
newer file with changed wording will be reported as a conflict (both texts are undated), so a
history of successive Gesetze im Internet snapshots is future work.

## Citations

German conventions, abbreviation `amtabk` if present, else `jurabk`:

| Provision | Citation |
|---|---|
| `/de/bund/bgb/~para-823-abs-1` | § 823 Abs. 1 BGB |
| `/de/bund/gg/~art-1-abs-1` | Art. 1 Abs. 1 GG |
| `/de/bund/stgb/~para-242-abs-1` | § 242 Abs. 1 StGB |
| `/de/bund/kschg/~para-1-abs-2-nr-1-buchst-a` | § 1 Abs. 2 Nr. 1 Buchst. a KSchG |
| `/de/bund/gewstg/~para-2-abs-1-satz-2` | § 2 Abs. 1 Satz 2 GewStG |
| `/de/bund/gewstg/~para-8-nr-1-buchst-d-satz-2-buchst-aa` | § 8 Nr. 1 Buchst. d Satz 2 Doppelbuchst. aa GewStG |
| `/de/bund/gewstg/~para-9-nr-1-satz-5-nr-1a-satz-2` | § 9 Nr. 1 Satz 5 Nr. 1a Satz 2 GewStG |
| `/de/bund/bdsg_2018/~para-1-abs-2` | § 1 Abs. 2 BDSG |
| `/de/bund/bgb/~paras-3-bis-6` | §§ 3 bis 6 BGB (repealed) |
| `/de/bund/gg/~praeambel` | Präambel GG |

Annexes (none in this sample) would be cited `Anlage 1 zum EStG` (`zum` only for abbreviations
ending in G/GB). Gesetze im Internet URLs resolve to works and provisions
(`https://www.gesetze-im-internet.de/bgb/__823.html` → `/de/bund/bgb`, `para-823`).

## Profile

Civil law, federal, treaty reception `dualist` with notes (Art. 59 Abs. 2 GG, Art. 25 GG), "To
verify". Courts: Bundesverfassungsgericht (rank 1), Bundesgerichtshof, Bundesverwaltungsgericht,
Bundesarbeitsgericht, Bundesfinanzhof, Bundessozialgericht (rank 2, no appeal links; Land courts
not profiled). Precedent `no_formal_precedent`, no vertical binding; § 31 BVerfGG binding effect
noted "To verify". No `memberships` (EU to be added by the lead once an EU profile exists).
Language `de` uses PostgreSQL's `german` text-search configuration (already configured).

## Known gaps

- Ten hand-picked federal laws only; no other federal law, no ordinances, no Land law, no EU law,
  no case law.
- Current consolidation only; no per-provision dates in the source; earlier dates are a history
  gap. Not the authentic text (the Bundesgesetzblatt is not ingested).
- Sentence numbers exist only where the source marks them (tax laws). Elsewhere citations stop at
  Absatz/Nummer/Buchstabe, even where precise practice adds "Satz n" (e.g. the list in § 1 Abs. 2
  KSchG is in Satz 2; ULIS cites "§ 1 Abs. 2 Nr. 1 KSchG").
- Constitutional-court findings ("nichtig", "unvereinbar") are only notes (`VERSION_NOTE`, info
  severity); the text stays `in_force` as the source shows it. Raising them to a warning would need
  a change to the shared limitations logic.
- Tables of contents, `Anhang EV`, notes attached to structural headings and the Weimar
  Constitution articles printed in an unofficial note to Art. 140 GG are not indexed.
- English translations are not ingested (restrictive user notice).
- Refreshing to a newer build of a law with changed wording produces conflicts, not a new version.

## Results (2026-10-14)

Checks in the worktree: `ruff check` and `ruff format --check` (82 files) clean; `mypy ulis`
clean (55 files); full test suite against PostgreSQL (`ulis_test_de`): **139 passed**, of which
`tests/test_gesetze_im_internet.py` has 19 tests (16 unit, 3 PostgreSQL), plus the DE additions
to `tests/test_catalog.py`.

Real run against `ulis_de`: `migrate`; `sync-catalog` → `{"jurisdictions": 16, "sources": 9,
"concepts": 4}`; `ingest de-gesetze-im-internet` (from the polite cache, 36 s):

```
documents_seen 10, documents_new 10, instruments_created 10, instrument_versions_created 10,
provision_versions_created 23265, passages_indexed 19237, citation_links_created 0,
conflicts [], errors []
```

Running `ingest` again: `documents_unchanged 10`, nothing created (idempotent).

`verify-sources`: 10/10 **pass**, 23,265 units re-derived from the raw zip bytes (gg 788, bgb
7653, stgb 2990, zpo 3775, stpo 2999, hgb 3275, vwvfg 621, bdsg_2018 608, kschg 106, gewstg 450).

Sample searches (`python -m ulis.cli search ... --jurisdiction DE`):

| Query | First result |
|---|---|
| `Würde des Menschen unantastbar` | Art. 1 Abs. 1 GG |
| `vorsätzlich oder fahrlässig Eigentum widerrechtlich verletzt Schaden` | § 823 Abs. 1 BGB |
| `fremde bewegliche Sache wegnimmt zuzueignen` `--as-of 2020-01-01` | § 242 Abs. 1 StGB (then § 249 Abs. 1 StGB); `ATTESTED_ONLY_AT` on each hit and `HISTORY_INCOMPLETE` ("For 10 instrument(s) in scope, indexed versions start after 2020-01-01 (earliest 2026-05-06)") |
| `Selbsttötung geschäftsmäßig Gelegenheit` | § 217 Abs. 1 StGB, with `VERSION_NOTE` "... mit GG unvereinbar und nichtig gem. BVerfGE v. 26.2.2020 ..." |
| `Gewerbebetrieb gewerbliches Unternehmen Einkommensteuergesetzes` | § 2 Abs. 1 Satz 2 GewStG |
| `Betriebsrat Kündigung schriftlich widersprochen` | § 1 Abs. 2 Nr. 1 KSchG |
| `Videoüberwachung öffentlich zugänglicher Räume` | § 4 Abs. 4 BDSG, then § 4 Abs. 1 BDSG |

Every hit is labelled "In force at the source date; applicability on the requested date
unverified" and carries `PROFILE_UNVERIFIED`, `ATTESTED_ONLY_AT`, `CITATOR_COVERAGE`,
`REVISED_TEXT` and `SOURCE_NOT_AUTHENTIC`; with the default date (2026-10-14, after the build
dates) also `AS_OF_AFTER_CURRENCY`.
