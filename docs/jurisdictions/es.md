# Spain (ES) - State legislation from the BOE consolidated-legislation API

Profile `ES` (`config/jurisdictions/es.yaml`, `draft_unverified`), source `es-boe-consolidada`
(`config/sources/es-boe-consolidada.yaml`), manifest `config/manifests/es-boe-consolidada.yaml`,
connector `ulis/sources/connectors/boe_consolidada.py`, parser
`ulis/sources/parsers/boe_consolidada.py`, citation module `ulis/jurisdictions/modules/es.py`,
tests `tests/test_boe_consolidada.py`, manifest generator `scripts/build_boe_manifest.py`. The manifest lists **the State
norms of the AEBOE consolidated collection that are not spent: 6,826 norms** (regenerated 2026-10-18; section "The whole
collection" below); the six norms of the first pilot come first and are described in the tables that follow.

## Source and licence

Publisher: Agencia Estatal Boletín Oficial del Estado (AEBOE), open-data API for consolidated
legislation, documented in [APIconsolidada.pdf](https://www.boe.es/datosabiertos/documentos/APIconsolidada.pdf)
(version of 2 September 2025) and the [API FAQ](https://www.boe.es/datosabiertos/faq/consolidada.php).
`GET https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/{id}` with
`Accept: application/xml` returns one XML document with `<metadatos>`, `<analisis>`,
`<metadata-eli>` and `<texto>` - the consolidated text **with every version of every block**.

Read before anything else was fetched (2026-10-14):

| What | Where | Finding |
|---|---|---|
| Reuse conditions | [aviso legal, "Condiciones de reutilización"](https://www.boe.es/informacion/aviso_legal/index.php#reutilizacion) | Licence-type approved by Resolución of 27 June 2024 (Ley 37/2007). Reuse "para fines comerciales y no comerciales", including copy, distribution and "modificación, adaptación, extracción, reordenación y combinación ... para crear obras derivadas o productos o servicios de valor añadido". |
| Attribution | same | Cite the source with a link to https://www.boe.es; for derived products "Basado en datos de la Agencia Estatal Boletín Oficial del Estado". Mention the date of last update; keep update metadata; identify modifications; do not imply AEBOE endorsement. |
| Official character | same, and FAQ | "No se podrá reutilizar la información de un modo que sugiera que tiene carácter oficial. En particular, en el caso de la legislación consolidada, en cada texto ... deberá indicarse expresamente que se trata de un texto consolidado de carácter meramente informativo." Only the texts published in the electronic edition of the BOE are "oficiales y auténticos". FAQ: consolidation integrates express amendments "en un solo texto, sin valor oficial"; "Los textos consolidados y el análisis jurídico tienen carácter meramente informativo. Para fines jurídicos debe consultarse la publicación oficial." |
| Open data | [datosabiertos](https://www.boe.es/datosabiertos/) | API offered "para facilitar el acceso, la descarga y la reutilización"; reuse implies accepting the conditions above. |
| robots.txt | https://www.boe.es/robots.txt | One `User-agent: *` group, no `Crawl-delay`; `/datosabiertos/` not disallowed; the thousands of per-document disallows (privacy de-indexing) concern none of the six identifiers. |

Conclusion: **approved** (`allowed_with_conditions` for redistribution, bulk download,
automated processing and commercial use). Every search result carries the attribution text
"Basado en datos de la Agencia Estatal Boletín Oficial del Estado (https://www.boe.es). Texto
consolidado de carácter meramente informativo: solo la publicación en el diario oficial «Boletín
Oficial del Estado» es oficial y auténtica." Counsel review is still recommended.

Modelling of legal status: `text_origin = official_revised` (an editorial consolidation by the
official publisher), `authoritative = false`, source `authority = official_non_authentic`. Results
therefore carry `REVISED_TEXT` ("not the authentic enacted text") and `SOURCE_NOT_AUTHENTIC`, plus
`CURRENCY` with the record's `fecha_actualizacion` (only when `estado_consolidacion` is
"Finalizado"; otherwise an outstanding-change warning says the consolidation is out of date).

Politeness: `PoliteHttp` with the ULIS User-Agent, one connection, 1 s between request starts since 2026-10-18 (5 s
before; robots.txt sets no Crawl-delay and AEBOE publishes no rate: `config/sources/es-boe-consolidada.yaml`), cache under
`var/sources/www.boe.es/`. **Requests made in total: 11** (robots.txt, aviso legal, datosabiertos
page, API PDF, API FAQ, and one request per norm). The real ingestion read the cache (0 requests).

## The whole collection (widened 2026-10-15, regenerated 2026-10-18)

`python -m scripts.build_boe_manifest` reads the API's list endpoint (`GET .../legislacion-consolidada?limit=-1`,
`Accept: application/json`, [APIconsolidada.pdf](https://www.boe.es/datosabiertos/documentos/APIconsolidada.pdf) section
2.1; list ordered by last update). The service stops at 10,000 rows, so the script asks again with `to=<date of the
oldest update received>` (2 pages, 3 requests with robots.txt, 18 MB): **12,434 consolidated norms**, of which
**8,814 are State** (`ambito` "Estatal", all identifiers `BOE-A-...`) and 3,620 autonomous-community norms (`Autonómico`),
which are left out because ULIS has no profile for the communities. robots.txt names 1,685 identifiers, none in the list,
and the connector re-checks that on every run. By rango: Real Decreto 3,512, Orden 2,412, Ley 859, Resolución 810,
Real Decreto-ley 315, Acuerdo Internacional 254, Ley Orgánica 184, Circular 144, Decreto 123, Instrucción 122, Real
Decreto Legislativo 44, Acuerdo 30, Reglamento 2, Decreto-ley 2, Constitución 1. 1,988 of them have `vigencia_agotada`
= S (all provisions repealed or spent).

**Listed: the 6,826 State norms without `vigencia_agotada` = S**, the law in force today; the 1,988 spent ones are left
out unless the script is run with `--include-spent`. Regenerated on 2026-10-18 (3 requests: robots.txt, then 10,000 +
4,414 rows over two pages, newest `fecha_actualizacion` 2026-10-16): the same 12,434 norms, 8,814 State and 3,620
regional, **6,826 listed, identical to the manifest of 2026-10-16** (0 added, 0 removed, 0 titles changed). By rango of the
6,826: Real Decreto 2,713, Orden 1,727, Ley 768, Resolución 553, Real Decreto-ley 264, Acuerdo Internacional 240, Ley
Orgánica 175, Circular 118, Decreto 107, Instrucción 100, Real Decreto Legislativo 32, Acuerdo 24, Reglamento 2,
Decreto-ley 2, Constitución 1; all 6,826 have `estado_consolidacion` Finalizado. `load-all --refresh` re-fetches only the
norms already listed; new norms appear after `python -m scripts.build_boe_manifest` is re-run (delete the cached list
pages under `var/sources/www.boe.es/legislacion-consolidada/` first; `docs/deployment.md`, "Refresh").

**What the collection is.** The AEBOE's own FAQ (https://www.boe.es/datosabiertos/faq/consolidada.php) says the
consolidated collection holds "las normas más relevantes del ordenamiento jurídico, principalmente normas con rango de
ley... así como normativa estatal de otros rangos que se ha considerado que tiene especial interés". The list is therefore
not the whole State legal order: **lower-rank State norms the AEBOE does not consolidate** (most órdenes, resoluciones and
other instruments published in the BOE) are not in the API and are a documented gap, and so is **all autonomous-community
law** (the 3,620 regional norms the list holds are not ingested, and the rest of regional law is not in the collection).

Parser 1.1.0 maps the new rangos to instrument types (`decree_law`, `decree`, `regulation`, `ministerial_order`,
`resolution`, `circular`, `instruction`, `agreement`, `international_agreement`) and to the ELI codes the BOE uses
(`dl`, `d`, `reg`, `o`, `res`, `cir`, `ins`, `a`, `ai`); the profile declares them and a rank order (to verify). Citations:
`art. 1 de la Orden CUL/1014/2007`, `art. 1 del Real Decreto 996/2001`; a norm without an official number (most
Resoluciones, Acuerdos) is cited by the head of its title (`Resolución de 31 de mayo de 2016, de la Dirección General
de Seguros y Fondos de Pensiones, Primero`), cut at the clause that says what it does and at 160 characters.

**Norms repealed in full.** The text of a repealed norm keeps showing its last wording, so without a rule every
provision of the 1,988 spent norms would read as in force. The metadata give `estatus_derogacion` S with
`fecha_derogacion` (and `estatus_anulacion` / `fecha_anulacion`): every open-ended provision of the norm ends on that date
(`valid_to`, with a note), a `repeal` event is recorded (the repealing norm "per the BOE analysis" is quoted), and the
ext keeps the dates. A norm marked spent without such a date keeps `vigencia_agotada` in the ext and no end date. Test:
BOE-A-2001-17164 (RD 996/2001, repealed 2017-04-01) is found for 2010-01-01 with `valid_to` 2017-04-01 and not for today.
Only the last wording of each provision takes that end (parser 1.2.0): earlier wordings keep the end derived from the
next one. Before, every wording got the repeal date as an explicit end, so all the wordings of an article read as in
force together (dev database 2026-10-18: 333 provisions of RDLeg 1/1995 and 191 of Ley 30/1992 had overlapping
versions; art. 11 of the 1995 Workers' Statute had 15 wordings all in force until 2015-11-13).

**Norms repealed with exceptions** keep `estatus_derogacion` N and `vigencia_agotada` N, so the list and the metadata
both present them as in force; the BOE states the repeal only in a dated version of the initial note
(`bloque tipo="nota_inicial"`), which its web page prints as a banner above the text. Ley Orgánica 15/1999 (data
protection): "Norma derogada, con efectos de 7 de diciembre de 2018, sin perjuicio de lo previsto en la disposición
adicional 14 de la Ley Orgánica 3/2018, de 5 de diciembre, según establece su disposición derogatoria única.1."
Parser 1.2.0 reads that note: the last wording of each provision ends on the date the note states ("con efectos de ...",
else the version's `fecha_vigencia`), with the note quoted first in `note`, and a `repeal` event is recorded. Articles
of the norm the note names as excepted ("excepción hecha de su artículo 8", RD 255/1996) stay in force; an exception
made by reference to another norm (LO 3/2018 DA 14 keeps arts. 23 and 24 of LO 15/1999 in force) is quoted, not
applied. In the dev database (300 norms) 3 norms are such: LO 15/1999 (257 open wordings), RD 255/1996 (48, art. 8
kept) and RD 1599/1997 (218). Other formulas ("Téngase en cuenta que, a partir del 6 de agosto de 2003, queda derogado
el presente Real Decreto, a excepción del título VI", the note of RD 822/1990) are not read. A norm the BOE presents as in
force without any repeal statement stays in force (Ley 4/1999, which amended Ley 30/1992: no "SE DEROGA" reference,
`estatus_derogacion` N, no note).
A text with no provision at all raises `ParseSkipped`. The connector skips a norm the API does not know (404) and
continues (`fetch_errors`); a refusal stops the run.

**Scale.** 6,827 requests at 1 s = **1.9 h** (9.5 h at the earlier 5 s) for the 6,826 listed (8,815 requests, 12.2 h, with the spent norms). A uniform
random sample of 80 of the 8,814 State norms (cached, parsed, 0 failures, 2026-10-15): 130.5 provision versions per norm (median 33, max 2,786, s.d. 327), 2.5 expressions per norm; raw API
document 0.96 MB on average (median 78 KB, largest 63 MB). Extrapolated: **~1.15 million provision versions (plausibly 0.5-1.8
million), ~4 GB of PostgreSQL** (the 74-norm slice below: 3.3 KB per version including passages and indexes) and
**~8 GB of raw documents** in the object store (heavy tail, uncertain). Use `python -m scripts.ingest_in_batches
es-boe-consolidada --batch 50` rather than one transaction for the whole list.

**Slice run (dev database `ulis_es`).** 74 norms (the 6 pilot norms, LEC, LECrim, Ley 40/2015, 3 current and 2 repealed norms
of each of 15 rangos, and the one norm marked `Desactualizado`; 71 requests, 391 s): `ingest` 0 errors, 692 instrument versions,
38,733 provision versions, 32,946 passages in 103 s; `verify-sources` **74 of 74 pass**. Searches (`jurisdiction=ES`):
"tutela efectiva de los jueces y tribunales" -> `art. 24.1 CE`; "detención preventiva plazo máximo setenta y dos
horas" -> `art. 520.1 LECrim`, `art. 17.2 CE`; "Comisión Española sobre la digitalización ..." -> `art. 1.2 de la Orden
CUL/1014/2007`; "procedimiento de examen previo aplicable patentes alimentación" for 2010-01-01 -> `art. 2 del Real
Decreto 996/2001` (valid to 2017-04-01) and nothing from that decree for 2026-10-15.

**Slice run of the regenerated manifest (2026-10-18, dev database `ulis_regen`).** 5 norms (the Constitución and the
newest in-force norm of four rangos: Real Decreto-ley 29/2026, Orden APA/1082/2026, the Resolución of 5 October 2026 of
the Dirección General del Catastro, Instrucción 1/2026 of the Junta Electoral Central; 5 requests): 5 documents, 9
instrument versions, 815 provision versions, 681 passages, **0 errors** (22 s). `verify-sources`: **5 of 5 pass** (671,
107, 28, 4 and 5 units).

## What is ingested (the six pilot norms)

| Norm | BOE id | Type (BOE rango) | Work URI | Expressions (version dates) | Provisions | Provision versions | Repealed versions | Passages |
|---|---|---|---|---|---|---|---|---|
| Constitución Española | BOE-A-1978-31229 | constitution (Constitución) | `/es/c/BOE-A-1978-31229` | 5 (1978-12-29 ... 2026-05-20) | 663 | 671 | 0 | 549 |
| Código Civil (Real Decreto de 24 de julio de 1889) | BOE-A-1889-4763 | royal_decree (Real Decreto) | `/es/rd/BOE-A-1889-4763` | 74 (from 1889-07-25) | 3,282 | 5,058 | 161 | 4,893 |
| Código Penal (LO 10/1995) | BOE-A-1995-25444 | organic_law (Ley Orgánica) | `/es/lo/BOE-A-1995-25444` | 54 (from 1995-11-24) | 2,890 | 4,768 | 123 | 4,149 |
| LO 3/2018, protección de datos (LOPDGDD) | BOE-A-2018-16673 | organic_law | `/es/lo/BOE-A-2018-16673` | 7 (from 2018-12-06) | 633 | 661 | 0 | 571 |
| Ley 39/2015, procedimiento administrativo común (LPAC) | BOE-A-2015-10565 | law (Ley) | `/es/l/BOE-A-2015-10565` | 14 (from 2015-10-02) | 774 | 813 | 0 | 687 |
| RDLeg 2/2015, Estatuto de los Trabajadores (ET) | BOE-A-2015-11430 | royal_legislative_decree | `/es/rdlg/BOE-A-2015-11430` | 41 (from 2015-10-24) | 928 | 1,267 | 11 | 1,060 |

Full version history for all six norms (every block version the source publishes), Castilian
Spanish only. The Código Civil is recorded with the BOE rango "Real Decreto" (it was published by
Real Decreto under the Ley de Bases of 11 May 1888 and has the rank of law; the classifier treats
it as legislation, not regulation).

## Text, structure and identifiers

* Blocks of type `precepto` become provisions: articles (`art-13`, `art-183ter`), article ranges
  (`arts-617-622`), dispositions (`da-1`, `dt-4`, `dd`, `dd-unica`, `df-3`; bare titles such as the
  Código Civil's "Primera" take their kind from the preceding heading "DISPOSICIONES
  TRANSITORIAS"), the preamble (`preambulo`) and the Código Civil's enacting text
  (`parte-dispositiva`). Provisions of a decree that approves an annexed consolidated text (the
  ET's "Artículo único") are prefixed `rd-` and cited against the decree. Headings
  (`encabezado`, and `precepto` versions that only print a title), signatures and initial notes
  are not provisions.
* Articles are split into apartados ("1."), letters ("a)") and ordinal items ("1.ª", "1.º") from
  the numbering printed at the start of ordinary paragraphs, only while it runs in sequence
  (1, 2, 3 / a, b, c). Indented (`sangrado`) paragraphs and text inside «quotations» (amending
  provisions that quote other laws) are never split. Unnumbered paragraphs belong to the unit
  above them (e.g. the second paragraph of art. 24.2 CE). eIds: `art-24-2`, `art-2-2-a`,
  `art-22-1o`.
* Text is the exact paragraph text (whitespace normalised), one paragraph per line; the article
  heading ("Artículo 6. Tratamiento basado en el consentimiento del afectado.") is the
  provision's heading, not text. `<blockquote>` notes ("Se modifica ... por ... Ref. BOE-A-...",
  "Téngase en cuenta ...", earlier wordings) and the consolidators' "(*)" remarks go to `note`
  (truncated at 1,000 characters). No images or tables occur in the six norms; if they do, tables
  are rendered row by row and images are reported in the note, never invented.

## Dates and versions

* Each block version has `fecha_publicacion` (publication of the norm that produced it),
  `fecha_vigencia` (entry into force of the wording) and `id_norma`. ULIS records **one expression
  per consolidated version date** (`fecha_publicacion`, as BOE labels versions, cf. ELI
  `.../con/<date>`) holding the wordings that version introduced (`consolidation_kind =
  version_delta`), e.g. `/es/c/BOE-A-1978-31229/spa@1992-08-28;actualizacion=20260520T074424Z`.
  The update stamp keeps a later re-fetch from colliding with already-recorded expressions. The
  first expression is the original publication, so the indexed history starts there and dates
  between publication and entry into force are "not yet in force" (the Código Penal was
  published 1995-11-24 and applies from 1996-05-24).
* `valid_from = fecha_vigencia` (`validity_basis = source_periods`); the end is derived from the
  next version of the same unit. An apartado keeps its version while its own words are unchanged
  (art. 13.1 CE has one version since 1978; 13.2 has two).
* Explicit end dates are set only from the source's structure: a unit that is no longer in its
  block's next wording ends when that wording starts (the apartados of art. 15 CP end 2015-07-01);
  a block with `fecha_caducidad` (from which the consolidated text stops showing it) and no
  recorded repeal ends on that date, with a note saying the source does not state when it ceased
  to apply (art. 183 ter CP: 2022-09-07; 4 such blocks in the CP and CC).
* Repeals: a version whose body is "(Derogado)", "(Suprimido)", "(Sin contenido)", "(Anulado)"
  is `repealed` (empty text, marker and note in `note`), also for the article's apartados. Range
  blocks ("Artículos 617 a 622. (Derogados)", vigencia 2015-07-01) repeal each article they cover.
* A wording without `fecha_vigencia` is `prospective` (no entry into force recorded): one case,
  art. 56 CC as worded by Ley 15/2015 (later replaced by the wording in force from 2021-04-30).
  A wording replaced by a later-published one before it took effect is not recorded (none occurs
  in these norms).
* The source sometimes lists the same version of a block twice (same norm and dates) with
  different texts (arts. 274, 515, 611 and 631 CP). The first listing is recorded; the second is
  kept under its own expression (`.../spa@2003-11-26/listado-2;...`) so the pipeline reports it as
  a **conflict** for human review instead of silently choosing.

## Citations

Conventional Spanish style with the law's own numbering (module `es`, to be confirmed by a
Spanish reviewer): `art. 24.1 CE`, `art. 1902 CC`, `art. 138.1 CP`, `art. 2.2.a) LOPDGDD`,
`art. 22.1.ª CP`, `art. 183 ter CP`, `arts. 617 a 622 CP`, `DA 1.ª CE`, `DD, apdo. 3 CE`,
`CE, preámbulo`, `art. 54.1 ET`; without an abbreviation: `art. 3.1 de la Ley 1/2022`; decree
provisions: `art. único del Real Decreto Legislativo 2/2015`. The BOE identifier, rango, número
oficial, ELI and department are kept in `ext` (`EsExt`).

## Shared code changed

* `ulis/verification/integrity.py` (core, minimal): `verify_source_document` mapped each
  (work URI, eId) of a raw document to **one** text. One BOE document carries a provision's whole
  history, so several texts share a locator; the map now holds the set of text hashes per
  locator and a stored version passes if its hash is among them. For every existing source each
  locator has exactly one text, so their behaviour is unchanged (full suite passes).
* Registrations only: `ulis/ingestion/pipeline.py` (parser, parser version, connector),
  `ulis/jurisdictions/modules/__init__.py`, `tests/test_catalog.py` (profile `ES`, source
  approved). Spanish already had the PostgreSQL `spanish` configuration in `ulis/languages.py`.
  No migration.

## Known gaps

* The AEBOE consolidated collection only, State norms not spent (6,826 of the list's 8,814 State norms; the list also
  holds 3,620 regional norms): lower-rank State norms the AEBOE does not consolidate are not in the API at all, no
  autonomous-community law, no case law (TC, TS, AN, TSJ). The abbreviation table still covers only nine norms; the rest
  are cited by rango and number.
* Castilian Spanish only: co-official languages (Catalan/Valencian, Galician, Basque) and
  translations are not ingested.
* Consolidated texts are informative only; the authentic BOE publication is not ingested. Only
  express amendments and repeals are consolidated; tacit repeals and court rulings appear only in
  notes ("Se declara contrario a la Constitución y nulo ...").
* Sub-article units are derived from printed numbering; an unnumbered paragraph after the last
  lettered item is attached to that item. A provision that was a single paragraph and later got
  apartados is indexed both as a whole and by apartado (e.g. art. 138 and 138.1 CP both match).
* `fecha_caducidad` end dates are display dates (usually the publication of the restructuring
  norm), not stated dates of loss of effect (flagged in the note).
* Links in notes ("Ref. BOE-A-...") are not turned into citation links; `analisis` (materias,
  referencias) is stored in the raw document but not modelled.
* Abbreviations are a fixed table (CE, CC, CP, LEC, LECrim, LOPDGDD, LPAC, LRJSP, ET).
* EU membership is not recorded in the profile (to be added with the EU profile).

## Results (2026-10-14)

Checks in the worktree: `ruff check` and `ruff format --check` clean, `mypy ulis` no issues,
**full suite 135 passed** against `ulis_test_es` (52.6 s; 13 new tests in
`tests/test_boe_consolidada.py`, 4 of them PostgreSQL).

Real run on `ulis_es`: `migrate`, `sync-catalog` (16 jurisdictions, 9 sources, 4 concepts),
`ingest es-boe-consolidada` (66 s):

```
documents_seen 6, documents_new 6, instruments_created 6, instrument_versions_created 195,
provision_versions_created 13238, passages_indexed 11909, errors [], conflicts 4:
  ~art-515 (from 2003-10-01) ... /es/lo/BOE-A-1995-25444/spa@2003-09-30/listado-2
  ~art-274, ~art-611, ~art-631 (from 2004-10-01) ... /es/lo/BOE-A-1995-25444/spa@2003-11-26/listado-2
```

`verify-sources`: all 6 pass (units: CE 671, CC 5,058, CP 4,768, LOPDGDD 661, LPAC 813, ET 1,267).

Sample searches (`jurisdiction=ES`, Python API):

| Query | Date | Top result | Notes |
|---|---|---|---|
| tutela efectiva de los jueces y tribunales | 2026-10-14 | art. 24.1 CE (1978-12-29, open) | REVISED_TEXT, SOURCE_NOT_AUTHENTIC, CURRENCY, AS_OF_AFTER_CURRENCY (record updated 2026-05-20) |
| sufragio activo elecciones municipales | 1990-01-01 | art. 13.2 CE, 1978 wording "sufragio activo en", valid to 1992-08-28 (derived) | LATER_VERSION_EXISTS |
| sufragio activo elecciones municipales | 2000-01-01 | art. 13.2 CE, 1992 wording "activo y pasivo" | VERSION_NOTE (Reforma de 27 de agosto de 1992) |
| por acción u omisión causa daño a otro interviniendo culpa o negligencia | 2026-10-14 | art. 1902 CC (from 1889-08-16) | |
| matare a otro reo de homicidio | 2010-01-01 | art. 138 CP (1996-05-24 to 2015-07-01) | LATER_VERSION_EXISTS |
| matare a otro reo de homicidio | 2020-01-01 | art. 138.1 CP (from 2015-07-01), then art. 138 CP | |
| despido disciplinario incumplimiento grave y culpable del trabajador | 2026-10-14 | art. 54.1 ET | |
| consentimiento del afectado para una pluralidad de finalidades | 2026-10-14 | art. 6.2 LOPDGDD | the LOPDGDD and LPAC preambles follow |

Point in time (`provision_at`): art. 13.2 CE on 1992-08-28 gives the new wording (boundary);
art. 135 CE switches on 2011-09-27; art. 617.1 CP is in force on 2015-06-30 and `repealed` on
2015-07-01 (range block); art. 183 ter CP is in force on 2022-09-06 and has no version on
2023-01-01; art. 138.1 CP on 2010-01-01 is "not yet in force" (art. 138 had no apartados then);
art. 56 CC gives the 1981 wording in 2019 and the 2021 wording in 2022.
