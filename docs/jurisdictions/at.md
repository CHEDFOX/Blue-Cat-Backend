# Austria (AT)

Status: **thin pilot, draft_unverified.** One real provision is ingested (§ 83 StGB). The rest of
the planned selection and all case law were not fetched, because RIS permits automated access only
20:00-05:00 Vienna time (or at weekends) and the pilot was closed before that window.

## Sources

| Id | What | Status |
|---|---|---|
| `at-ris-bundesrecht` | Bundesrecht konsolidiert (RIS OGD API v2.6 search + document XML), parser `ris_bundesrecht_xml` | active |
| `at-ris-judikatur` | VfGH, VwGH, Justiz/OGH decision texts | **planned** (licence reviewed; no parser, nothing fetched) |

Connector `ris_ogd` (`ulis/sources/connectors/ris_ogd.py`, on `PoliteHttp`), manifest
`config/manifests/at-ris-bundesrecht.yaml` (the *intended* selection: B-VG, ABGB, StGB, DSG, UGB,
AVG, MRG; **not run**). Its decisions fetcher is untested.

## Licence and terms (read 2026-10-14, before fetching)

- **CC BY 4.0**: RIS OGD FAQ <https://www.ris.bka.gv.at/UI/Ogd.aspx> ("Die Nutzung der RIS-Daten via
  OGD erfolgt gemäß der Namensnennung 4.0 International"); data.gv.at record "RIS Daten Version 2.6"
  <https://www.data.gv.at/datasets/0fb9ae1a-92cb-4ab8-a589-470c16d4fe21> lists
  <https://creativecommons.org/licenses/by/4.0/> for the API, handbook and schemas. No registration.
  The publisher prescribes no attribution wording; ULIS's is in the source YAML.
- **Mandatory technical terms** (same page): at most 0.5 requests/s, no parallel connections, queries
  only 20:00-05:00 Vienna time or at weekends/public holidays, larger use announced to
  ris.it@bka.gv.at (**not done**; this pilot stays at manual-use volume), IP blocking otherwise.
  The connector enforces 5 s spacing, sequential access, the window (holidays not computed) and a
  request budget. No bulk dumps exist.
- **robots.txt**: `ris.bka.gv.at` Crawl-delay 2, disallows `/GeltendeFassung/`, `/showdata/` (not
  `/Dokumente/`). `data.bka.gv.at` (API host) disallows all agents except the EU ECLI crawler; ULIS
  reads that as aimed at crawlers because the OGD terms invite API use, and runs only listed queries.
  **To confirm with the publisher.**
- **Legal status**: OGD-RIS API handbook V2_6 (Apr 2026): consolidated law is "lediglich der
  Information", "rechtlich unverbindlich"; only the Bundesgesetzblatt authentic from 2004 binds.
  Modelled as `official_revised`, `authoritative=false`, source authority `official_non_authentic`.

## Dates and versions

RIS has one document per § / Art. / Anl. per version (Fassung) with Inkrafttretensdatum and, once
superseded, Außerkrafttretensdatum (`validity_basis: source_periods`). Subdivisions (Abs., Z, lit.)
inherit the paragraph's period. The end date is the last day of validity, stored as the next day
(half-open). **The XML field name for the end date (`akra`) is an assumption**; the only real document
seen is a current version, so the end-date code is tested on a synthetic edit of it, and no
historical version or repealed paragraph has been verified. RIS states no currency date
(`CURRENCY_UNKNOWN`). The API supports `Fassung.FassungVom` and in-force/out-of-force date ranges.

## Citations (de)

`§ 83 Abs. 3 Z 1 StGB`, `Art. 7 Abs. 1 B-VG`, `§ 4 Z 2 lit. a DSG`, `Anl. 1 UGB`
(`ulis/jurisdictions/modules/at.py`). Decision citation (court, date, Geschäftszahl, Rz, ECLI) is
coded but untested. Profile: VfGH, VwGH and OGH as three parallel apex courts; doctrine
`jurisprudence_constante`, no formal binding precedent ("to verify" notes). No EU membership entry.

## Known gaps

No case law; no history beyond one version; one provision only; no regulations, Länder law, BGBl.
(authentic text); NOR-document "Beachte"/"Anm." kept as notes; lists with dashes are inline text.

## Evidence (2026-10-14)

- Ingest into `ulis_at` of the hand-retrieved § 83 StGB document (exact RIS bytes, retrieved
  07:46 UTC, in `tests/fixtures/ris/`): 1 document, 1 instrument, 6 provision versions, 5 passages,
  0 conflicts/errors. `verify-sources`: `pass ris:NOR40217848 units=6`. The manifest-driven
  `ingest at-ris-bundesrecht` was **not** run (outside the access window).
- Searches (as of 2026-10-14): "Körperverletzung Freiheitsstrafe" -> `§ 83 Abs. 1 StGB` (binding
  legislation; REVISED_TEXT, CURRENCY_UNKNOWN, PROFILE_UNVERIFIED, SOURCE_NOT_AUTHENTIC); "mißhandelt
  fahrlässig verletzt" -> `§ 83 Abs. 2 StGB`; "Beförderungsbedingungen Kontrolle" -> `§ 83 Abs. 3 Z 1
  StGB`; as of 2015-01-01 "Körperverletzung" -> 0 results (version from 2020 only).
- Checks on the merged branch: ruff, ruff format, mypy clean; full suite 354 passed on `ulis_test_at`.
