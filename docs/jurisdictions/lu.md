# Luxembourg - Legilux consolidated legislation

Profile `LU` (draft, unverified, corpus `partial`), source `lu-legilux`, parser `legilux_akn`, connector `legilux`,
module `lu`. French only. The committed manifest lists the whole open corpus of consolidations that have an XML file;
this sandbox ingested a slice (below).

## Source and licence (read 2026-10-14)

[Legilux](https://legilux.public.lu) is the Journal officiel of the Grand Duchy, published by the Ministère d'État -
Service central de législation. It is a JavaScript application; its help texts are HTML fragments under
`https://legilux.public.lu/api/assets/faq/questions/`. The linked data ("Casemates", JOLux and ELI) is on the SPARQL
endpoint `https://data.legilux.public.lu/sparqlendpoint` (the documented `/sparql` is only the query page).

* **Licence: CC BY 4.0, not CC0.** Help page "Introduction et licence d'utilisation des données"
  (`12-introduction`): "Les textes et les métadonnées sont mis à disposition selon la licence d'utilisation
  http://creativecommons.org/licenses/by/4.0/ qui autorise leur réutilisation pour des utilisations commerciales ou non."
  The data.public.lu record says cc-by, and every XML file carries `jolux:license` CC BY 4.0. The site's general
  conditions of use (`14-utilisation`) say, "sauf indication contraire", personal use only and no redistribution; the
  CC BY statement is the contrary indication, but the publisher does not reconcile the two (counsel review advised).
* **No legal value.** Consolidated texts, codes included, are "sans valeur légale"; the Mémorial A is authentic
  (`1-legilux`). `authoritative = false`, `official_non_authentic`.
* **robots.txt.** `legilux.public.lu` allows `/filestore/eli/etat/leg/` (it disallows `/search`, `/eli/etat/adm/`,
  `/*.docx`, ...). `data.legilux.public.lu` disallows `/eli/*` and `/filestore/*` for all robots but not the SPARQL
  endpoint. As for Switzerland, ULIS queries SPARQL, never fetches the disallowed paths, and downloads each file from
  `https://legilux.public.lu/filestore/...`, where the Legilux site itself serves it (`api/settings`:
  `manifestation_prefix`), checking the URL against that host's robots.txt first. Administrative acts (`/eli/etat/adm/`)
  are excluded. This reading of "robots per host" should be confirmed with the publisher.

## What is ingested

`scripts/build_legilux_manifest.py` (one SPARQL query) wrote `config/manifests/lu-legilux.yaml`: **1,214 works, 2,905
consolidations** with a French XML file: the Constitution (38 consolidations, 1919-2026), 10 codes (civil 19, commerce 7,
consommation 15, fonction publique 21, instruction criminelle 1, pénal 36, procédure civile 17, procédure pénale 36,
santé 3, travail 29), 533 laws, 605 grand-ducal regulations, and ministerial and other acts. Left out and counted:
1,018 consolidations without XML (Code de l'environnement, sécurité sociale, most of the santé code exist only as
HTML/PDF/Word), 754 `recueil` compilations; 26 consolidations dated after the build date are `scheduled`, not fetched.
A full run is about 2,905 requests (>= 4 h at 5 s, about 1 GB: codes ~4 MB each, laws ~100 KB) and is meant for the
owner's server.

**Slice run here** (temporary manifest, 38 works / 102 consolidations: the Constitution, the Code civil, the last 2-3
consolidations of 6 other codes, the law of 1 August 2018, and one text of each other kind): 99 ingested, 3 skipped.

## Dates and versions

Each file is a whole text applicable from a date. The date is the one in the ELI (= `jolux:dateApplicability` in the
linked data); the connector stores it with the file's text in a JSON bundle because some files carry another
consolidation's metadata (e.g. the Code du travail file of 2026-07-26 says 2026-07-01; the Code civil files of 2018-11-01
and 2019-04-28 are byte-identical). The file's own date is kept in `ext.metadata_date` and its end date is then ignored.
When the file states `dateEndApplicability` the version ends the next day (half-open), so a gap with no XML consolidation
is reported as such. Validity basis `revision_snapshot` (`ulis/ingestion/snapshots.py`): unchanged wording continues, a
version starts at the first consolidation that shows its wording. Each article's own `dateApplicability` is kept in the
note. Struck-through wording (`<del>`, `style="del"`) is not read; amendment references printed in the text stay in it.

## Citations

`Art. 1382 du Code civil`, `Art. 63, paragraphe 2 du Code civil`, `Art. L. 121-1 du Code du travail`,
`Art. 34 de la Constitution du Grand-Duché de Luxembourg`, `Art. 73 de la loi du 1er août 2018 portant organisation de la
Commission nationale pour la protection des données et du régime général sur la protection des données`.

## Results

* Ingest of the slice: 102 seen, 99 new, 3 skipped, 0 errors, 0 conflicts; 35 instruments, 99 instrument versions,
  15,401 provision versions created, 57,920 corroborated, 3,863 citation links (477 resolved), 13,803 passages.
  `verify-sources`: 99 pass, 0 fail.
* Searches (`ulis.cli search --jurisdiction LU`): Art. 1382 du Code civil (faute), Art. 1er du Code pénal, Art. L. 211-8 du
  Code du travail, Art. 73 and Art. 10 of the 2018 CNPD law, and `--as-of 2009-02-01` finds the Constitution's Art. 34
  "Le Grand-Duc sanctionne et promulgue les lois" (the 2009-03-16 consolidation drops "sanctionne").
* Tests: `tests/test_legilux.py` 17 tests (parser, citations, connector offline and robots, manifest, ingestion,
  point-in-time, search); see the final report for the full-suite count.

## Known gaps

Consolidations without XML (1,018), recueils, annexes, subject indexes, acts that are one unnumbered text (skipped:
decisions, statutes), the Mémorial B and C, drafts, EU texts, treaties, German/English/Luxembourgish translations, case
law. History starts at the first consolidation with XML (a code may begin as late as 2016-2026). A work whose last
consolidation has no stated end is shown as open-ended even if the text was later repealed or replaced.
