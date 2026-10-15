# Liechtenstein (LI) - Lilex (gesetze.li)

**Status:** pilot, profile `draft_unverified` (not reviewed by counsel). Source `li-lilex`, connector `lilex`, parser `lilex_html`,
module `li`. Language `de` (PostgreSQL `german` configuration). No EU/EEA membership is recorded.

## Source and licence

- Lilex, <https://www.gesetze.li>, is the consolidated-law database of the Rechtsdienst der Regierung. Search page
  ([/konso/suche](https://www.gesetze.li/konso/suche)): "Konsolidierte Rechtsvorschriften dienen ausschliesslich der Information und
  entfalten keine Rechtswirkung"; earlier versions are searchable "ab 1. Januar 2010". [Impressum](https://www.gesetze.li/impressum)
  and [Datenschutz](https://www.gesetze.li/datenschutz): no terms of use, no copyright reservation, no reuse licence.
  [robots.txt](https://www.gesetze.li/robots.txt): HTTP 404 (no rules).
- Basis for reuse: Urheberrechtsgesetz Art. 5 Abs. 1 lit. a and Abs. 2 (read on Lilex itself, [LGBl. 1999 Nr. 160](https://www.gesetze.li/konso/1999.160)):
  laws, ordinances and official collections of them are not protected by copyright. This is thinner than a licence; counsel review is
  recommended and info.rdr@llv.li should be told before the full manifest is run.
- Status: the text is a consolidation for information (`official_consolidation`, `authoritative = false`, authority
  `official_non_authentic`); the authentic text is the Landesgesetzblatt (not ingested).

## What is ingested (manifest `config/manifests/li-lilex.yaml`)

The 1,227 national laws (LR 1-9) Lilex listed on 2026-10-14 (read with its own LR-prefix search, nine requests): the version in force of
each, and **every version Lilex lists** for eight core laws (Verfassung, ABGB, PGR, StGB, StPO, DSG, URG, GLG). Treaties (LR 0.x) are not
listed. The full manifest is about 2,500 requests (3.5 h at 5 s); here a slice was run (below). `default_versions: all` fetches the history of every law.

## Structure, dates and versions

- Work URI `/li/lgbl/<year>.<number>` (the LGBl-Nr, e.g. `/li/lgbl/1921.015`), expression `.../deu@<start of the version>`. Provisions: `preamble`
  (enacting formula), `art-5`, `-abs1` (Absatz), `-bsta` (Buchstabe), `-ziff1` (Ziffer), `ueb-N` (loose Übergangsbestimmungen), `abs-N` (laws without
  articles), `anh-N` (closing signatures, appended letters, the amending acts' transitional texts as annex text). Headings (Hauptstück, Titel ...) are context.
- A version's text carries no dates; the version list does ("01.02.2021 - 31.12.2025"; the last entry is open: "01.01.2026"). Each version is a whole-law
  snapshot (`revision_snapshot`): provisions are valid from the version's first day to the day after its last day; an unchanged wording continues; a provision
  a later version no longer has ends there. `currency_date` is not stated by the source (`CURRENCY_UNKNOWN`).
- Footnote marks are removed from the text; the footnote ("Art. 4a Abs. 5 Bst. c aufgehoben durch LGBl. 2021 Nr. 257.") is the provision's note. "Aufgehoben"
  places are `repealed`, keep their number and note. Unknown blocks stay as text, never dropped. `render` collapses source line breaks to spaces and drops soft hyphens.

## Citations (module `li`)

`Art. 5 Abs. 1 Bst. a URG`, `Art. 4a Abs. 5 Bst. c Ziff. 1 GLG`, `Art. 2 Verfassung des Fürstentums Liechtenstein` (no abbreviation in the title: the short title).
The abbreviation is read from the title's parentheses ("(Urheberrechtsgesetz, URG)", "Strafgesetzbuch (StGB) vom ...").

## Results (2026-10-14/15)

- Tests: `tests/test_lilex.py` 22 passed (parser, connector offline, manifest, PostgreSQL ingest of GLG versions 6 and 7, URG, Verfassung; point-in-time at the
  boundary 2021-12-31 / 2022-01-01; idempotent re-ingest; verify).
- Real slice (`ulis_eur`, 17 laws incl. all 5 versions of GLG, 39 requests): 21 documents, 3,325 provision versions, 468 corroborated (unchanged wording across GLG versions),
  2,858 passages, 0 errors; `verify-sources`: 21 pass. Searches: "Nichtraucherschutz Rauchverbot in öffentlichen Gebäuden" -> `Art. 3 Abs. 1 TPG`;
  "Wer ist Urheberin eines Werkes" -> `Art. 8 Abs. 1 URG`.

## Known gaps

Informative consolidation only; amending acts and the LGBl not ingested; no treaties, municipal law, case law; history only for eight laws; abbreviation read from the title;
annex tables reduced to text rows; the site publishes no robots.txt or terms, so a permission from the Rechtsdienst is advisable.
