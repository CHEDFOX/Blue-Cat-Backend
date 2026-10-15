# Canada and Australia: provincial, state and territorial law

Each province, state and territory is its own profile (`CA-ON`, `CA-BC`, ... `CA-NU`; `AU-NSW`, `AU-VIC`, `AU-QLD`, `AU-WA`,
`AU-SA`, `AU-TAS`, `AU-ACT`, `AU-NT`), `kind: constituent_unit`, parent `CA` or `AU`, `draft_unverified`. Scoping uses the
existing parent chain, no core change: a search for `CA-ON` sees Ontario law and federal Canadian law (chain `CA-ON, CA, INT`);
a search for `CA` or `CA-MB` never sees Ontario law, and a federal search says so (`SUBNATIONAL_LEGISLATION_EXCLUDED`).
Same for Australia. Profiles of units with no corpus have `corpus_status: none` (the search says so: `NO_CORPUS`).

## Evaluation of the publishers (2026-10-14, robots.txt first)

| Unit | Publisher | Result |
|---|---|---|
| QLD | legislation.qld.gov.au | **Ingested.** CC BY 4.0 (copyright page); robots.txt bars only `/view/pdf/`, `/view/xml/compare`, `/view/html/compare`, `/admin/`; XML of each Act's current reprint |
| TAS | legislation.tas.gov.au | **Ingested.** CC BY 4.0; robots.txt bars `/admin/`; XML of each Act's current version |
| ON | ontario.ca/laws (e-Laws) | **Ingested.** King's Printer policy: statutes may be reproduced without permission or charge if accurate, acknowledged "(c) King's Printer for Ontario", marked unofficial; robots.txt allows `/laws/`; HTML only (no XML, API or bulk) |
| MB | web2.gov.mb.ca/laws | **Ingested.** OpenMB licence (read in full); no robots.txt; HTML (bilingual page, English column read) |
| BC | bclaws.gov.bc.ca | planned: robots.txt `User-agent: * / Disallow: /` (only Googlebot and Bingbot allowed) |
| AB | kings-printer.alberta.ca | planned: robots.txt `Disallow: /` for all |
| QC | legisquebec.gouv.qc.ca | planned: HTTP 403 on robots.txt |
| NS | nslegislature.ca | planned: robots.txt would allow a 10 s crawl but no copyright or reuse statement was found |
| NSW | legislation.nsw.gov.au | planned: HTTP 403 on robots.txt; the API host is unreachable from the sandbox |
| ACT | legislation.act.gov.au | planned: footer "All rights reserved", the JACS Copyright Policy it names answers 403, PDF and Word only (no XML) |
| VIC | legislation.vic.gov.au | rejected: "authorised electronic versions ... for personal use only"; document files barred by robots.txt |
| WA | legislation.wa.gov.au | planned: a Creative Commons licence was announced in 2019 but no licence wording found; HTML/PDF/RTF only |
| SA | legislation.sa.gov.au | planned: HTTP 403 on robots.txt |
| SK, NB, NL, PE, YT, NT, NU, AU-NT | - | not evaluated yet (profile only) |

Nothing was fetched from a blocked host beyond its robots.txt (and, for the ACT, the pages of the register itself); no other
identity, proxy or route was tried. The findings, with URLs, are in `config/sources/<id>.yaml`; `tests/test_subnational.py`
checks that the licence gate refuses each of them. The federal Australian parser (EPUB) is not the same family as these XML
files, so no parser was reused; Queensland and Tasmania each have their own (different DTDs).

## What is ingested

| Source | Manifest | Documents in the manifest | Parser | Versions |
|---|---|---|---|---|
| `au-qld-legislation` | `config/manifests/au-qld-legislation.yaml` | all 534 public Acts in force on 2026-10-14 | `qld_quills_xml` | current reprint, `validity_basis = revision_snapshot` |
| `au-tas-legislation` | `...au-tas-legislation.yaml` | 43 principal Acts (curated; the site gives no usable list) | `tas_act_xml` | current version, snapshot |
| `ca-on-e-laws` | `...ca-on-e-laws.yaml` | 37 statutes (curated and checked; e-Laws's list page is script-driven) | `ontario_elaws_html` | current consolidation, `source_periods` |
| `ca-mb-manitoba-laws` | `...ca-mb-manitoba-laws.yaml` | all 490 C.C.S.M. Acts | `manitoba_laws_html` | current consolidation, snapshot |

Run on the owner's server with `ulis.cli ingest <source id>` (Queensland takes about 45 minutes at the 5 s delay). In the
sandbox only a slice was ingested (numbers below). To extend Tasmania or Ontario, add identifiers to the manifest.

## Dates and versions

* Queensland, Tasmania, Manitoba date the whole version, not provisions: Qld `first.valid.date`, Tas `FIRST.VALID.DATE`
  (`END.VALID.DATE` 9999-11-02 is a sentinel), Manitoba "in effect since" (and "current as of" = currency date). Each
  provision starts on that date and earlier wordings are "history not indexed" (`HISTORY_INCOMPLETE`), not "not yet in force".
  Queensland and Tasmania state no currency date (`CURRENCY_UNKNOWN`).
* Ontario: version date = consolidation period start; a section starts at the latest in-force date of its "Section Amendments
  with date in force" list; a section with no dated amendment has no start date. "Not in force" amendments are outstanding changes.
* Repealed/omitted provisions: status `repealed`, no text, the history in `note`. Legislative history and drafting notes are
  never in the text.

## Citations (module `au_state`, module `ca_prov`; conventions to be confirmed by local reviewers)

`Criminal Code (Qld) s 302(1)(a)`, `Criminal Code Act 1899 (Qld) s 9`, `Human Rights Act 2019 (Qld) sch 1 (definition of ‘act’)`,
`Criminal Code (Tas) s 158`, `Courts of Justice Act, RSO 1990, c C.43, s 1(1) “action”`, `The Human Rights Code, CCSM c H175, s 8(1.1)`.

## Known gaps

Only current versions; no subordinate legislation, regulations, bills or case law; no courts are modelled; no French (Manitoba's
French column is in the same page but is not read; Ontario French not fetched); Ontario/Tasmania lists are curated; Queensland
drafting notes and a draft Commonwealth Bill inside the Succession to the Crown Act schedule are not indexed; Tasmanian
appendices (forms) are not indexed; the XML/HTML are treated as unofficial reproductions (`authoritative = false`).
Section-to-section citation links between provincial Acts are by work only for Ontario (the e-Laws anchors do not map to eIds).

## Results

RESULTS_PLACEHOLDER
