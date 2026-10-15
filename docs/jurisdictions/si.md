# Slovenia (SI) - Uradni list RS (archive)

**Status:** pilot, profile `draft_unverified` (not reviewed by counsel). Source `si-uradni-list`, connector `uradni_list`, parser `uradni_list_html`,
module `si`. Language `sl` (no PostgreSQL text-search configuration for Slovenian: the `simple` configuration is used, no stemming, inflected forms
match only when identical). EU membership is not recorded here.

## Source and licence

- Uradni list Republike Slovenije d.o.o. publishes the official gazette at <https://www.uradni-list.si>. Its general terms
  ([pravna obvestila](https://www.uradni-list.si/podjetje/pravna-obvestila), 2.1) restrict the site's materials to personal, non-commercial viewing and then say:
  "Omejitve uporabe gradiv ne veljajo za Uradni list Republike Slovenije in v njem objavljene vsebine" (the restrictions do not apply to the Official Gazette and
  its content). [robots.txt](https://www.uradni-list.si/robots.txt): only `Disallow: /_pdf/*/*a/`. Every issue index says the shown content is archive (ZoUL art. 33):
  issues after 28 February 2026 are on pisrs.si. The exemption is a statement by the publisher, not a licence text; counsel review recommended.
- **PISRS (pisrs.si), the state's legal information system, is not used** (`si-pisrs`, planned): its terms forbid reproducing it as a database, and its API
  ([OpenAPI](https://pisrs.si/swagger/PisrsAPI.json)) needs an X-API-Key that a person must request. It would add the unofficial consolidated texts.
- Authenticity of the HTML view was not established: `authoritative = false`, authority `official_non_authentic`.

## What is ingested (manifest `config/manifests/si-uradni-list.yaml`)

MANIFEST_SUMMARY

## Structure, dates and versions

- Work URI `/si/ul/<year>-<part>-<number>` (the gazette's own id, e.g. `/si/ul/2025-01-3978`), expression `.../slv@<publication date>`. Provisions: `preamble`
  (the President's promulgation decree and the title), `art-12` / `art-12a`, `-par2` (odstavek), `-pt3` (točka), `-suba` (podtočka), `zav-1` (signature lines),
  `pril-N` (annex). Part headings ("I. SPLOŠNE DOLOČBE") are context; the parenthesised line after "N. člen" is the article heading.
- **Each act is the text as published.** The gazette does not consolidate (an amendment is a separate act) and the page states no entry-into-force date (it is in
  the text) and no end. So no validity period is recorded: `EFFECTIVE_DATE_UNKNOWN`, status `in_force_date_unverified`, category `applicability_unverified`,
  `CURRENCY_UNKNOWN`, and every result carries `OUTSTANDING_CHANGES_INSTRUMENT` ("Amendments published after this issue are not applied"). Point-in-time answers are not possible.

## Citations (module `si`)

`1. člen ZJRM-2`, `drugi odstavek 1. člena ZJRM-2`, `1. točka 2. člena ZJRM-2`, `podtočka a) 2. točke prvega odstavka 3. člena ZX`, `12.a člen`; an act without
an abbreviation in its title: `3. člen Uredbe o Pravno-informacijskem sistemu Republike Slovenije` (genitive) or `<title>, 3. člen`.

## Results (2026-10-15)

RESULTS

## Known gaps

Amendments not applied; no validity periods; only the manifest's laws and consolidated texts (Jan 2025 - Feb 2026 and five core acts); no regulations, municipal acts, treaties
or case law; the gazette since March 2026 (pisrs.si) and the PISRS consolidated texts are not ingested; structure is read from printed numbering; tables are text rows.
