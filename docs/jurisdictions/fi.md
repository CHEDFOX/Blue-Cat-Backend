# Finland - `FI`

Status: profile `draft_unverified` (0.3.0). The committed manifest lists **every consolidated statute in
force that has text** in Finlex open data (5,579 works, Finnish and Swedish; see "Widened to every statute in
force"); the real runs in "Results" cover the original seven Acts (two with every earlier temporal version the
API holds) and a 40-work slice of the widened manifest. Since 2026-10-18 a full load reads the documents from Finlex's
daily **download package** (one 4.4 GB file instead of about 11,100 API requests; see "Download package route"); all
11,108 listed documents were read from it and a 50-work slice was ingested from it. The full database load has not been
run here.

## Source

| | |
|---|---|
| Source id | `fi-finlex` (`config/sources/fi-finlex.yaml`) |
| Publisher | Ministry of Justice, Finland; Finlex is maintained by the Legal Register Centre ([help](https://www.finlex.fi/en/instructions)) |
| Access | Full load: Finlex's download package [`statute-consolidated.zip`](https://www.finlex.fi/files/open-data/archives/statute-consolidated.zip) ("Updated legislation (4.1 GB)" on the [download page](https://www.finlex.fi/en/open-data/download-materials); rebuilt daily), the same Akoma Ntoso documents in one zip. Single works: the Finlex open data REST API, Akoma Ntoso XML: `GET https://opendata.finlex.fi/finlex/avoindata/v1/akn/fi/act/statute-consolidated/{year}/{number}/{fin\|swe}@{version}` ([open data](https://www.finlex.fi/en/open-data), [quick guide](https://www.finlex.fi/en/open-data/integration-quick-guide), [OpenAPI](https://opendata.finlex.fi/Finlex_avoin_data_v0_4_0.yaml)). No key; a User-Agent header is required |
| Connector | `finlex` (`ulis/sources/connectors/finlex.py`): `mode: package` (the committed manifest; the package Finlex serves when a run starts, or an exact pin; resumable download, read in place) or `mode: api` (`PoliteHttp`, one document per request) |
| Parser | `finlex_akn` (`ulis/sources/parsers/finlex.py`) |
| Module | `fi` (`ulis/jurisdictions/modules/fi.py`) |
| Manifest | `config/manifests/fi-finlex.yaml` |

### Licence and terms (read before any statute was fetched, 2026-10-14)

- [Finlex FAQ](https://www.finlex.fi/en/faq) (Finnish: [ukk](https://www.finlex.fi/fi/ukk)): "There are
  no restrictions on use, on the right of access to information or on the utilisation of the data
  published in Finlex. All information published in Finlex is public." - "Finlex open data is
  available under a **Creative Commons Attribution 4.0 (CC BY 4.0)** licence." - "The data may not be
  used for purposes that breach or compromise the protection of personal data. The data may not be
  combined in a manner that violates a person's privacy."
- [Quick guide](https://www.finlex.fi/en/open-data/integration-quick-guide): the API may be called
  "without identification or registration"; "All endpoints require you to set 'User-Agent'-header";
  when volumes are restricted the service answers **429**; `Accept-Encoding: gzip` is recommended.
- `robots.txt`: `https://opendata.finlex.fi/robots.txt` answers 404 (no file);
  `https://www.finlex.fi/robots.txt` disallows only `/_next/image`. The site root
  `https://opendata.finlex.fi/` answers 403 - not a refusal of the API, and nothing outside the
  documented API paths was requested. ULIS sends one request every 1 s since 2026-10-18 (every 5 s before; no
  numeric limit is published, only HTTP 429 when volumes are restricted: `config/sources/fi-finlex.yaml`).
- Copyright: statutes have no copyright in Finland - Copyright Act 404/1961, 9 § 1 mom. 1 kohta
  ("Tekijänoikeutta ei ole: 1) lakeihin ja asetuksiin"), read in the Finlex consolidation.
- Attribution used on every result (Finlex prescribes no wording; CC BY 4.0 form): `Source: Finlex
  open data, Ministry of Justice, Finland (www.finlex.fi), licensed under CC BY 4.0
  (https://creativecommons.org/licenses/by/4.0/). Consolidated text; not the authentic text of the
  statutes. Structure normalised by ULIS.`

Licence review status: `approved` (project review; counsel review still recommended).

### Legal status of the text: consolidated, not authentic

Finlex publishes two kinds of statute documents:

| FRBR subtype | What it is | `FRBRauthoritative` | Ingested |
|---|---|---|---|
| `statute` | The statute as published in the Statute Book of Finland (säädöskokoelma / författningssamling), text plus PDF | `true` | No |
| `statute-consolidated` | "Ajantasainen lainsäädäntö" / "uppdaterad lagstiftning": the statute with its amendments worked in by Finlex's editors | `false` | **Yes** |

The Statute Book is published in the public data network (Act on the Statute Book 188/2000, 12-13 §)
in Finnish and Swedish (11 §), and both language versions are equally authentic. The consolidations
have no legal status, and the source itself marks them non-authoritative. ULIS therefore records
them as `text_origin = official_revised`, `authoritative = false`, source authority
`official_non_authentic`: every hit carries `REVISED_TEXT` and `SOURCE_NOT_AUTHENTIC`. The parser
refuses `statute` documents rather than mislabel them. Finnish and Swedish versions share their
identifiers, so they group as parallel versions. English translations on Finlex are unofficial and
are not ingested.

## What is ingested

| Act | Statute | Temporal versions (per language) | Provisions | Provision versions (fi+sv) | Passages |
|---|---|---|---|---|---|
| Suomen perustuslaki / Finlands grundlag (Constitution) | 731/1999 | 1 (20180817) | 450 | 900 | 638 |
| Rikoslaki / Strafflag (Criminal Code), whole Act | 39/1889 | 1 (20260795) | 3,244 | 6,597 | 5,019 |
| Laki varallisuusoikeudellisista oikeustoimista (Contracts Act) | 228/1929 | 1 (20040128) | 108 | 216 | 132 |
| Perintökaari / Ärvdabalk (Inheritance Code) | 40/1965 | 1 (20250983) | 722 | 1,443 | 948 |
| Työsopimuslaki / Arbetsavtalslag (Employment Contracts Act) | 55/2001 | 1 (20260412) | 556 | 1,112 | 836 |
| Hallintolaki / Förvaltningslag (Administrative Procedure Act) | 434/2003 | **9** (20150801 … 20250541) | 321 | 727 | 513 |
| Tietosuojalaki / Dataskyddslag (Data Protection Act) | 1050/2018 | **9** (20181050 as enacted … 20260380) | 177 | 439 | 323 |

The Code of Judicial Procedure (4/1734) was not taken (size; the Criminal Code already covers a
large Act). History was requested for the Contracts Act and the Constitution too: every earlier
version of 228/1929 answers 404, and so did the one version of 731/1999 tried (20111112); the
Administrative Procedure Act lacks the versions before 2015 (20030434, 20091408, 20100581,
20140368: 404). Version ids are the amending statutes' numbers (`20260380` = statute 380/2026),
taken from each latest version's `finlex:amendedBy` list.

## Widened to every statute in force (2026-10-16)

- **What the two indexes say.** The download package `statute-consolidated.zip` (4.1 GB; on 2026-10-16 only its
  central directory was read, with 2 Range requests) has 348,144 members: 56,478 statutes, each in Finnish and Swedish, 1.17 temporal
  versions per language on average. The API's list (`GET /akn/fi/act/statute-consolidated/list?isInForce=true&
  langAndVersion=fin@latest`, at most 10 per page, no total) names **38,585 statutes in force** (3,885 pages). But
  **33,006 of them are shells**: `<hcontainer name="contentAbsent"/>`, a title and metadata, 3-5 KB, no text and no
  dates (older statutes whose consolidation Finlex has not made). A random draw of 40 statutes from the package gave 12
  with text, of which 5 in force; the in-force list gave 5,579 with text.
- **Manifest** `config/manifests/fi-finlex.yaml`, written by `python -m scripts.build_finlex_manifest`: the in-force
  list intersected with the statutes whose latest document in the package index is at least 5,500 bytes (shells are
  3.3-6.3 KB, the smallest document with text seen is 5.5 KB, so the cut is not clean: about 1 listed work in 7
  is a shell that is skipped at ingest, and a text document smaller than 5.5 KB would be missed), per language
  (`languages: [fin]` for the 50 works with text in one language only). 5,579 works: the seven above + 5,572; by
  decade 2020s 1,435, 2010s 1,276, 2000s 1,410, 1990s 728, earlier 730. The list's pages overlap slightly (about
  0.7 % duplicate entries), so a few statutes in force may be missing; a later run of the script fills them.
- **Parser** 1.1.0: a statute no longer in force (`isInForce` false) and a shell raise `ParseSkipped` (counted as
  skipped documents); all fifteen Finlex statute types are kept as instrument types (`act`, `decree`, and e.g.
  `decision` for Verohallinnon päätös 718/2024, `announcement`); the FI profile is **0.3.0** because its
  `instrument_types` list grew (one pinned test updated). **Connector:** a statute that cannot be fetched (404, 5xx)
  goes to `fetch_errors` and the run continues; a refusal (401/403/429/451) or 20 failures in a row stops it; a work
  may carry its own `languages`.
- **Cost of the full run:** 11,108 documents = about 11,100 requests = **3.1 hours** at 1 s (15.4 hours at the earlier 5 s); XML about **0.5 GB**
  (494 MB for the latest documents; 44 KB each). Database about **2-3 GB**: 13 works loaded alone took 30.5 MB for
  7.6 MB of XML (4.0x), the 40-work slice 19.6 MB for 3.8 MB (5.1x, shells included); 199 provision versions per
  work in the slice, so about 0.9 million provision versions.
- **Slice run** (40 works drawn at random from the manifest, seeded; 80 requests, 7 min): 80 documents, **68
  ingested** (34 works: decree 46 documents, act 16, decision 6), 12 skipped (6 shells above the size cut), **0
  errors**, 6,752 provision versions, 5,426 passages, `verify-sources` **68/68 pass** (6,752 units). Searches
  (`ulis.cli search … --jurisdiction FI`): `tupakkatuote` → Tupakkalaki (549/2016) 2 § 1 mom., 11 § 1 mom.;
  `elintarvikevalvonta` → Maa- ja metsätalousministeriön asetus elintarvikevalvonnasta (315/2021) 15 § 1 mom. 4 kohta;
  `kreditinstitut` → Lag om resolution av kreditinstitut och värdepappersföretag (1194/2014) 1 kap. 3 § 1 mom.
- The 4.1 GB package itself was not used then; it is now the full-load route (next section).

## Download package route (2026-10-18)

**Terms, read first (2026-10-18).** The open data page counts the packages as part of the same service ("To access
the open data, you can perform searches using a REST application programming interface (API) or access a user
interface to download data packages", [open data](https://www.finlex.fi/en/open-data)); the
[download page](https://www.finlex.fi/en/open-data/download-materials) offers them as plain links, with no sign-up or
terms of their own ("The materials are ZIP-compressed, and the packages are updated with new documents once a day."),
so the FAQ's CC BY 4.0 grant and its privacy conditions apply (`config/sources/fi-finlex.yaml`). No notice is asked
for. `https://www.finlex.fi/robots.txt`, read again before the download, has `User-Agent: *`, `Disallow:
/_next/image` and no Crawl-delay: `/files/open-data/archives/` is allowed.

**The package, downloaded once** (`FinlexConnector.download_package`: robots.txt, then one GET with the ULIS
User-Agent; a broken transfer resumes with `Range` + `If-Range: <ETag>`, so a package rebuilt meanwhile is fetched
whole again rather than spliced; the first 401/403/429/451 stops it):

| | |
|---|---|
| URL | `https://www.finlex.fi/files/open-data/archives/statute-consolidated.zip` |
| Size | **4,397,198,140 bytes** (4.1 GiB) |
| SHA-256 | `8c07c5b96f4d5063b841bb156eb204fb8f783c6e96d34c810555200aa7ce3e45` (checked again with `sha256sum`: 3.5 s) |
| Last-Modified / ETag | `Sat, 17 Oct 2026 23:27:28 GMT` / `"0d44183df8d844dd14a81c32ddfaa8e8-525"` |
| Retrieved | 2026-10-18 11:57:24 UTC: 3 requests (robots.txt and 2 GETs), **143.5 s** including the SHA-256 (about 30 MB/s) |
| Integrity | CRC-32 of all **346,017 members** checked (`zipfile.testzip`, 40.9 s): no error |
| Cache | `var/sources/www.finlex.fi/files/open-data/archives/statute-consolidated.zip` + `.meta.json` |

**Which package a run loads.** The committed manifest has `mode: package` and `package: {url, pin: latest,
approx_bytes}`: each run loads the package Finlex serves when it starts, so a fresh server load needs nothing done
beforehand. A cached package is reused while one HEAD request shows the same ETag (else Last-Modified) and size;
otherwise it is deleted and the current one downloaded (one package on disk at a time), and a cached package that is
gone (for example deleted by a cache pruner after an ingest) is simply downloaded again. If Finlex cannot be asked (5xx
or no connection after 4 tries) the cached package is used and the run says so; a refused HEAD stops the run. The
package's size, SHA-256, ETag, Last-Modified and retrieval time are written to the cache's `.meta.json` and into every
document's metadata (`package_bytes`, `package_sha256`, `package_etag`, `package_last_modified`,
`package_retrieved_at`, stored with each source document), and a run opens one package file and reads every document
from it, so two packages are never mixed. For a reproducible run, `package.pin: exact` with `bytes`, `sha256`,
`last_modified`, `etag` and `retrieved_at` (written by `python -m scripts.finlex_package --pin`) accepts only that
package and refuses any other (`PackageChanged`); Finlex stops serving it at its next rebuild, so it can be repeated
only while it is cached.

**Which member is the latest version.** Members are `akn/fi/act/statute-consolidated/<year>/<number>/<lang>@<version>/
main.xml`. `fin@` (no number) is the statute **as enacted** (its `FRBRversionNumber` is the statute's own number:
the package's `2018/1050/fin@` is byte-identical to the API's `fin@20181050`), and `fin@<version>` each later temporal
version. Of the 11,108 listed documents 6,076 have only the as-enacted member; 5,032 have numbered versions, 1,254 of
them also `fin@`, which is then always the oldest. The latest is therefore the highest-numbered member, else `fin@`;
the API's `@latest` matched that rule in every comparison below except where the API already had a newer version.
(`scripts/build_finlex_manifest.py` assumed `fin@` was the current text; it is corrected, and the manifest is
unchanged: no statute's latest document falls on the other side of the 5,500-byte text threshold.)

**All listed documents read from the package** (no network; 2026-10-18):

| | |
|---|---|
| Listed latest documents (5,579 works) | **11,108**, all present in the package: **0 missing**, **0 errors** (CRC-32 and the member's own `FRBRuri` checked) |
| Read from the package | **11,094** latest + 32 earlier temporal versions of the two `history` Acts = 11,126 documents, 514,875,592 bytes, **10.3 s** (+3.2 s to check and open the package, 0.3 s to index its 132,499 `main.xml` members) |
| Taken from the API instead | **14** (7 works: 4/1734, 400/2015, 801/2017, 616/2019, 221/2022, 64/2023, 380/2023): the package's own metadata names a newer amending statute (871-887/2026, consolidated 2026-10-08/09) that has no member yet |
| History versions in neither | 8: 434/2003 `@20030434`, `@20091408`, `@20100581`, `@20140368` in both languages - the same versions the API answered 404 for on 2026-10-14 |
| Shells (`contentAbsent`) | 1,098, skipped at ingest as before |
| Parsed without a database | **82.3 s**: 9,780 documents parsed (767,046 provisions), 1,097 shells and 27 statutes no longer in force skipped, **222 refused** by the parser ("missing statuteProvisionsWrapper": decisions whose text is in an attachment; the API serves the same bytes, so this is a parser gap, not a package one) |

**Byte comparison with the API** (38 documents: 32 API responses fetched on 2026-10-18 - 30 for this comparison,
seeded random works with text plus edge cases, and 2 fetched by the slice's fallback - and 6 fixtures fetched on
2026-10-14/15; 37 with text, 19 Finnish and 18 Swedish):

| Result | Documents |
|---|---|
| Byte-identical (same SHA-256) | **25** (24 with text: 13 fi, 11 sv; e.g. Criminal Code 39/1889 fin/swe 2,260,855 / 2,260,840 bytes, Code of Judicial Procedure 4/1734 fin/swe, Constitution 731/1999, Data Protection Act 1050/2018 `@20250437` and as enacted) |
| Same version, rewritten by the publisher after the package was built | 9 (API `dateProduced` 2026-10-18 for 4, the same 2026-10-17 for 5): reference targets now point to consolidated sections (`/akn/fi/act/statute/2018/560` → `/akn/fi/act/statute-consolidated/2018/560#sec_82`) and metadata order changed. Parsed: 4 identical `ParsedDocument`s, 5 identical except those reference targets |
| Newer version in the API | 4: 380/2023 fin/swe (API `20260887`, package `20260803`) and 64/2023 fin/swe (`20260871` vs `20250006`); the connector detects both (the package names 887/2026 and 871/2026) and takes them from the API |

Of the three flagged works checked against the API, 380/2023 and 64/2023 had the newer version and 4/1734 did not (its
API `@latest`, 20260687, is byte-identical to the package's): the check errs towards asking the API.

**Slice ingest through the package route** (dev database `ulis_fib`, fresh; 50 works: 49 drawn from the manifest with
seed 20261018, plus 64/2023): 100 documents seen, **89 new** (85 from the package, 4 from the API: 4/1734 and 64/2023),
11 skipped (10 shells, 1 no longer in force), **0 errors, 0 conflicts**; 45 instruments, 89 versions, 9,468 provision
versions, 317 citation links (5 resolved), 6,907 passages; **28.4 s** in all (3 API requests). `verify-sources`:
**89/89 pass**, 9,468 units, 6.1 s. Database 34 MB for 5.9 MB of XML. Searches (`ulis.cli search … --jurisdiction
FI`): `holhoustoimi edunvalvoja` → Laki holhoustoimesta (442/1999) 92 § 2 mom. (a package document);
`fosforia sisältävien lannoitevalmisteiden` → Valtioneuvoston asetus fosforia sisältävien lannoitevalmisteiden ja lannan
käytöstä (64/2023) 5 § 1 mom. 2 kohta (version 20260871, from the API); `rättegångsbalk domstol` → Rättegångsbalk
(4/1734) 22 kap. 3 § 1 mom. 1 punkten.

**Cost of a full load.** Before (API route): 11,108 latest documents plus history, about 11,140 requests = **3.1 hours**
at 1 s start to start (4.6 h if latency adds; 15.4 h at the earlier 5 s). After (package route): **one 4.4 GB download**
(143.5 s here; bandwidth-bound: about 12 min at 50 Mbit/s, 1 h at 10 Mbit/s), about 23 API requests (14 fallbacks, 8
history versions, robots.txt: about 23 s at 1 s), then local work: reading 10 s, parsing 82 s, and the database writes,
which both routes share (not measured on a full load: 30-60 min extrapolated from the slice's 9,468 provision versions
to the 767,046 provisions the manifest parses into; about 2 GB). `load-all --dry-run` shows `>= 2s + 4.1 GB` for
`fi-finlex` (`approx_bytes`, the size of the package of 2026-10-17).

**Requests made for this work** (all with the ULIS User-Agent, one connection, at least 1 s apart): www.finlex.fi 5
(robots.txt twice, 2 GETs of the package, 1 HEAD by `scripts/finlex_package.py`); opendata.finlex.fi 35 (robots.txt
three times, 404; 32 documents). No 401, 403, 429 or 451.

**Refreshing (daily).** Finlex rebuilds the package once a day under the same URL; the API is updated continuously.
`load-all --refresh` (and any later `ingest fi-finlex`) runs fi-finlex like a first load: `--refresh` first deletes
cached downloads older than `--max-cache-age-days` (7), then the connector asks Finlex with one HEAD whether the cached
package is still current, downloads the new package only when the ETag changed, and the pipeline skips every document
whose bytes it already holds (same SHA-256). `fi-finlex` is therefore a live source again (not in the static pins).

```
python -m scripts.finlex_package                   # manifest pin, cached package, what Finlex serves now (robots + HEAD)
python -m scripts.finlex_package --replace         # download Finlex's current package now if it differs from the cache
python -m scripts.finlex_package --replace --pin   # ... and pin it exactly for a reproducible run
python -m scripts.finlex_package --live            # back to pin: latest (the committed default)
```

Single works come from the API one document at a time: a manifest with `mode: api` listing them, or
`FinlexConnector(manifest, "fi-finlex", cache, mode="api")`. Two caveats, both measured above: the package lags the
API for new amendments (a version that no package member mentions yet is only picked up by a later package or by the
API route), and the publisher rewrites documents without a new version number; loading such a rewrite over the stored
document is refused by the pipeline ("already recorded from different source bytes") and the stored text is kept, so
a refresh over an existing database reports those documents as errors. A load into a fresh database is not affected.

## How dates and versions work

- **Per-provision start dates come from the source** (`validity_basis = source_periods`). Each
  element whose wording comes from an amending statute carries `finlex:originalVersion="@20111112"`
  and a label (`4.11.2011/1112`); unmarked elements keep the nearest marked ancestor's wording, or
  the statute's as enacted. The entry-into-force date of each amending statute is in
  `finlex:amendedBy` (and the statute's own in `finlex:inForce`). Example: Constitution 1 § 3 mom.
  ("Suomi on Euroopan unionin jäsen") from 2012-03-01 (1112/2011); 1 § 1 mom. from 2000-03-01.
- Older amending statutes sometimes lack the date in the metadata. Only then, the date is read from
  that statute's commencement provision printed after the text, and only when it is the single
  standard sentence ("Tämä laki tulee voimaan 1 päivänä elokuuta 1989." / "Denna lag träder i kraft
  den 1 augusti 1989."); the provision's note says so. Anything else (staged commencement,
  commencement by decree) leaves the start `None` (`EFFECTIVE_DATE_UNKNOWN`): 11 provision
  versions in the dev DB.
- **Pending wordings.** Finlex prints a wording that is enacted but not yet in force together with
  the wording that still applies, after an authorial note ("L:lla 638/2026 muutettu 7 § tulee
  voimaan 1.3.2028. Aiempi sanamuoto kuuluu:"). Both are recorded: Criminal Code 2 a:7 has its
  current wording 1999-10-01 → 2028-03-01 (explicit end) and the new one from 2028-03-01. 146
  provision versions in the dev DB start after 2026-10-14.
- **Containers** (sections, subsections with lists) are recorded as they stand on the consolidation
  date and at each later change in their subtree. A later wording printed without the wording it
  replaces (and not marked "lisätty" / "har tillfogats", i.e. inserted) makes the container's
  earlier text unknown; it is then recorded only from that date, and the earlier state comes from
  earlier temporal versions or is a history gap.
- **Repeals**: "4 momentti on kumottu L:lla 22.5.2026/380." → status `repealed`, empty text, the note
  kept, from the entry into force of the statute the note links to (here 2026-09-01). Data
  Protection Act 14 § 4 mom. is in force on 2026-06-01 (from version 20250437) and repealed on
  2026-10-01 (version 20260380).
- **Temporal versions** are ingested in the order in which they took effect (amending statutes do
  not always take effect in the order of their numbers: 1112/2022 on 2023-06-25, 239/2023 on
  2023-03-01); `version_date` is the entry into force of the version's own statute,
  `currency_date` the source's `dateConsolidated`. An unchanged wording is
  corroborated, not duplicated. When a later version silently corrects a wording (typo fixes such as
  "asemesta" → "asemasta", Administrative Procedure Act 5 §), the pipeline's existing point-in-time
  rule records the corrected text from the first version showing it (`START_DATE_OBSERVED`): 8
  such restatements (Data Protection Act 29 § in Swedish, Administrative Procedure Act 5 § and
  70 § in Finnish), 0 conflicts.
- The shared `ulis/ingestion/snapshots.py` mechanism (`revision_snapshot`) is **not** used: Finlex
  versions are whole-law documents, but they date every provision individually, which is more
  precise than dating everything from the snapshot. Consequence: a provision that disappears from a
  later version without a repeal note (e.g. inside a chapter repealed as a whole) is not ended.
- History gaps: for Acts ingested in their latest version only, a date before a provision's current
  wording is `history_not_indexed` (e.g. Constitution 1 § 3 mom. on 2010-01-01).

## Identifiers

Finlex eIds carry the version (`chp_1__sec_1__subsec_3v20111112`) and are **not consistent between
the two language versions**: an introductory or closing clause is sometimes tagged as a subsection or
an unnumbered "paragraph" in one language only, a list item as a subsection, and eId numbers do not
always follow printed numbers (Criminal Code 1:11.2: "7 a)" is `para_8`, "8)" is `para_8_2`). ULIS
builds its own identifiers in the same style from the printed numbers (`chp_2a__sec_7__subsec_1__para_3a`)
and, for subsections (not numbered in print), from their position after normalising those encodings.
Result on the ingested latest versions: identical identifier sets in both languages for six Acts;
the Criminal Code differs by one subsection (an earlier wording printed only in Swedish, 41:2) and
the Inheritance Code by one repeal note present only in Finnish (5:2). Some amendment markers also
differ between languages (e.g. Administrative Procedure Act 29 § marked 581/2010 only in Swedish);
each language is recorded as published, so such provisions do not group as parallel versions.
Work URIs: `/fi/act/{year}/{number}` (`/fi/act/1889/39-001` for the Criminal Code, Finlex's number).

## Citations

One documented style, title first, the statute number as printed (`docNumber`), then the pinpoint in
the language of the text; chapters only for Acts whose sections restart in each chapter (detected
from the document):

- `Suomen perustuslaki (731/1999) 6 § 2 mom.` / `Finlands grundlag (731/1999) 6 § 2 mom.`
- `Rikoslaki (39/1889) 21 luku 1 §` / `Strafflag (39/1889) 21 kap. 1 § 2 mom.`
- `Rikoslaki (39/1889) 2 a luku 7 § 1 mom. 1 kohta`
- `Rikoslaki (39/1889) 34 luku 4 § 1 mom. 4 kohdan a alakohta` / `Strafflag (39/1889) 34 kap. 4 § 1 mom. 4 punkten underpunkt a`
- `Tietosuojalaki (1050/2018) 14 § 4 mom.`

Customary abbreviations (`RL 21:1 §`, `PL 6 §`) and inflected forms (`rikoslain 21 luvun 1 §`,
`6 § grundlagen`) are not generated: the source gives neither. To be confirmed by a Finnish reviewer.

## Profile

Unitary republic, civil-law (Nordic); official languages fi, sv. Courts: Korkein oikeus (KKO) and
Korkein hallinto-oikeus (KHO) at rank 1 (Constitution 3 §), courts of appeal → KKO, administrative
courts → KHO, district courts → courts of appeal. Precedent: `jurisprudence_constante`, not
formally binding; KKO/KHO precedents are followed in practice (marked "to verify"). Treaty
reception: dualist (to verify). No memberships are recorded here (EU membership is added with the EU
profile).

Tests with the package route (2026-10-18, own test database): full suite **2,121 passed** (1,326 s); `tests/test_finlex.py`
37 tests (package route against API fixtures, history and fallbacks, exact-pin refusal, API override, a member
that is not its document, the committed live manifest and load plan, the package script's switch between `latest` and
an exact pin, the resumable download against a fake server - resume, rebuilt package, refusals 401/403/429/451, newer
package, robots.txt - the live package against a fake server - a "current" package different from the sample is
downloaded and named in every document while an exact pin of the sample refuses it, reuse after one HEAD, replacement
when the ETag changes, download after pruning, offline, HEAD failure and refusal, a cache altered on disk - and a
PostgreSQL ingest of the package fixture whose source-document records name the package's SHA-256). Fixture:
`tests/fixtures/finlex/package/statute-consolidated-sample.zip` (14 real members). ruff, ruff format, mypy pass.

Tests after widening (2026-10-16, own test database): full suite **1,291 passed**; `tests/test_finlex.py` 18 tests (committed manifest, shell / repealed / decision fixtures, connector robustness, PostgreSQL search of a decision in both languages). ruff, ruff format, mypy pass.

## Known gaps

- Consolidated (non-authentic) text only; no original Statute Book texts, translations, case law (KKO/KHO
  precedents are on Finlex but not ingested) or treaties. The 33,006 in-force statutes without text are not ingested.
  The full 5,579-work database load has not been run (slices of 40 from the API and 50 from the package have; all
  11,108 documents were read and parsed from the package); the database size and time are estimates.
- 222 of the 11,126 documents (2.0 %) are refused by the parser ("missing statuteProvisionsWrapper": decisions whose
  text is in an attachment) on either route.
- The download package lags the API for new amendments: a newer version is taken from the API only when the
  package's own metadata names it (14 documents on 2026-10-18); rewritten documents of an unchanged version are not
  re-ingested over the stored ones (see "Refreshing").
- Full history only for the Data Protection Act; the Administrative Procedure Act from 2015; every other
  statute in its latest version (add `history: true` per work, in a fresh database).
- Start dates are those the source records for the statute that gave each wording; where an amending
  statute commenced in stages (stated only in its text, e.g. 1010/1995 for one subsection of the
  Criminal Code), a date can be wrong for some provisions.
- Chapters and parts are context, not provisions; a chapter repealed as a whole between two versions
  is not recorded as repealed.
- Subsection numbers are positional after normalisation; when an amendment renumbers subsections,
  a subsection's earlier history can be attributed to its new number.
- Source inconsistencies between the Finnish and Swedish XML are recorded as published (see
  Identifiers).
- Åland's own legislation and autonomy are not modelled.
- Search uses PostgreSQL's `finnish` and `swedish` configurations (stemming only, no compound
  splitting: "tietosuoja" does not match "tietosuojavaltuutettu").

## Results (2026-10-14)

### Requests made

About 93 HTTPS requests in total, one every 5 s or more, all with the ULIS User-Agent: 13 to
www.finlex.fi (robots, sitemaps, help, FAQ, open-data pages, the example AKN document), 4 to the
API documentation (robots 404, Swagger UI, OpenAPI file), 12 exploratory API calls (including the
Act on the Statute Book 188/2000 and the Copyright Act 404/1961, read for the licence and status
notes), and 64 by the connector (14 latest versions + 50 history requests, of which 18 were 404).
No 403/429 was received from the API.

### Checks

- `ruff check`, `ruff format --check`, `mypy ulis`: pass.
- Full suite against `ulis_test_fi` (after merging main, including Switzerland): **216 passed** in
  125 s (12 of them in `tests/test_finlex.py`: 9 unit, 3 PostgreSQL).

### Real run on `ulis_fi` (fresh database)

`migrate`, `sync-catalog` (21 jurisdictions, 13 sources, 4 concepts), `ingest fi-finlex` (from the
connector's cache of the responses fetched the same day):

```
documents_seen 46, documents_new 46, instruments_created 7, instrument_versions_created 46,
provision_versions_created 11434, provision_versions_corroborated 7414,
provision_versions_restated 8, citation_links_created 517, citation_links_resolved 18,
passages_indexed 8409 (fi 4205, sv 4204), conflicts [], errors []
```

`verify-sources`: **46 / 46 documents pass**, 18,848 provision units and 614 lifecycle events
re-derived from the raw bytes.

### Sample searches (Python API, `jurisdiction=FI`)

| Query (language, as of) | First hit | Notes |
|---|---|---|
| "tappaa toisen" (fi, 2026-10-14) | `Rikoslaki (39/1889) 21 luku 1 § 1 mom.` "Joka tappaa toisen, on tuomittava taposta …", from 1995-09-01 | `REVISED_TEXT`, `SOURCE_NOT_AUTHENTIC`, `PROFILE_UNVERIFIED`, `AS_OF_AFTER_CURRENCY` (consolidated 2026-09-04) |
| "lika inför lagen" (sv, 2026-10-14) | `Finlands grundlag (731/1999) 6 § 1 mom.` "Alla är lika inför lagen." | Swedish text; currency 2018-10-05 |
| "henkilötietojen käsittely tieteellistä tutkimusta varten" (fi) | `Tietosuojalaki (1050/2018) 31 § 1 mom. 3 kohta`; then 4 § 1 mom. (from 2026-09-01), 31 § 1 mom. | |
| "työnantaja saa irtisanoa työsopimuksen" (fi) | `Työsopimuslaki (55/2001) 7 luku 3 § 1 mom.` (from 2026-01-01); then 7:10.2, 7:2.1 | `VERSION_NOTE` "Wording from …" |
| "akkreditoi sertifiointielimen" (fi, 2026-06-01) | `Tietosuojalaki (1050/2018) 14 § 4 mom.`, 2019-01-01 → 2026-09-01 | `LATER_VERSION_EXISTS` |
| same query (fi, 2026-10-01) | `Tietosuojalaki (1050/2018) 36 a § 1 mom.` (inserted 2026-09-01) | 14 § 4 mom. is repealed on that date |

Point in time: Data Protection Act 14 § 4 mom. is `in_force` on 2026-06-01 and `repealed` on
2026-10-01; Criminal Code 2 a:7 on 2028-06-01 returns the wording of 638/2026.
