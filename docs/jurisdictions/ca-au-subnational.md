# Canada and Australia: provincial, state and territorial law

Every province, state and territory is its own profile (`CA-ON` ... `CA-NU`, `AU-NSW` ... `AU-NT`), `constituent_unit`, parent `CA`
or `AU`, `draft_unverified`. Scoping is the existing parent chain, no core change: `CA-ON` sees Ontario and federal Canadian law
(chain `CA-ON, CA, INT`); `CA` or `CA-MB` never see Ontario law and a federal search says so (`SUBNATIONAL_LEGISLATION_EXCLUDED`).
Units with no corpus have `corpus_status: none` (`NO_CORPUS`). Same for Australia.

## Publishers evaluated (2026-10-14, robots.txt read first; details and URLs in `config/sources/<id>.yaml`)

| Unit | Result |
|---|---|
| QLD legislation.qld.gov.au | **ingested.** CC BY 4.0; robots.txt bars only `/view/pdf/`, `/view/*/compare`, `/admin/`; XML of the current reprint |
| TAS legislation.tas.gov.au | **ingested.** CC BY 4.0; robots.txt bars `/admin/`; XML of the current version |
| ON ontario.ca/laws | **ingested.** King's Printer policy (reproduce statutes freely if accurate, acknowledged, marked unofficial); `/laws/` allowed; HTML only |
| MB web2.gov.mb.ca/laws | **ingested.** OpenMB licence (read in full); no robots.txt; HTML, English column |
| BC bclaws.gov.bc.ca | planned: robots.txt `User-agent: * / Disallow: /` (only Googlebot, Bingbot) |
| AB kings-printer.alberta.ca | planned: robots.txt `Disallow: /` |
| QC legisquebec.gouv.qc.ca | planned: HTTP 403 on robots.txt |
| NS nslegislature.ca | planned: robots.txt allows a 10 s crawl, but no reuse statement found |
| NSW legislation.nsw.gov.au | planned: HTTP 403 on robots.txt; API host unreachable |
| ACT legislation.act.gov.au | planned: "All rights reserved"; the JACS policy it names answers 403; PDF/Word only |
| VIC legislation.vic.gov.au | rejected: "authorised electronic versions ... for personal use only" |
| WA legislation.wa.gov.au | planned: a Creative Commons licence was announced (2019), no wording found; no XML |
| SA legislation.sa.gov.au | planned: HTTP 403 on robots.txt |
| SK NB NL PE YT NT NU, AU-NT | not evaluated (profile only) |

Nothing was fetched from a blocked host beyond its robots.txt; no other identity, proxy or route was tried. `tests/test_subnational.py`
checks that the licence gate refuses each blocked source. The federal AU parser reads EPUB, a different family: not reused.

## What is ingested (manifests in `config/manifests/`)

| Source | Manifest lists | Parser | Version |
|---|---|---|---|
| `au-qld-legislation` | all 534 public Acts in force on 2026-10-14 | `qld_quills_xml` | current reprint, snapshot |
| `au-tas-legislation` | 43 principal Acts (curated; the site has no usable list) | `tas_act_xml` | current version, snapshot |
| `ca-on-e-laws` | 37 statutes (curated, checked; the list page is script-driven) | `ontario_elaws_html` | current consolidation |
| `ca-mb-manitoba-laws` | all 490 C.C.S.M. Acts | `manitoba_laws_html` | current consolidation, snapshot |

Run on the owner's server (`ulis.cli ingest <id>`; Queensland takes about 45 minutes at the 5 s delay). Extend Tasmania/Ontario by adding ids.

## Dates and versions

* Qld `first.valid.date`, Tas `FIRST.VALID.DATE` (9999-11-02 is a sentinel) and Manitoba "in effect since" date the whole version
  (`revision_snapshot`); every provision starts then, earlier wordings are "history not indexed", not "not yet in force".
  Manitoba also states "current as of" (currency date); Qld and Tas do not (`CURRENCY_UNKNOWN`).
* Ontario: version = consolidation period start; a section starts at the latest in-force date in its amendments list, none if undated.
  "Not in force" amendments are outstanding changes. Repealed provisions: `repealed`, no text. Histories and notes are never text.

## Citations (modules `au_state`, `ca_prov`; to be confirmed by local reviewers)

`Criminal Code (Qld) s 302(1)(a)`, `Criminal Code Act 1899 (Qld) s 9`, `Human Rights Act 2019 (Qld) sch 1 (definition of ‘act’)`,
`Criminal Code (Tas) s 158`, `Courts of Justice Act, RSO 1990, c C.43, s 1(1) “action”`, `The Human Rights Code, CCSM c H175, s 8(1.1)`.

## Known gaps

Current versions only; no subordinate legislation, regulations, bills, case law or courts; no French (Manitoba's French column is in
the page but not read); Ontario/Tasmania lists curated; Qld drafting notes and a draft Commonwealth Bill in a schedule, and Tas
appendices (forms) not indexed; texts are unofficial reproductions (`authoritative = false`); Ontario links cite the work only.

## Results (sandbox, fresh database, 2026-10-15)

Tasmania and Ontario from the full manifests. **Queensland (6 of 534 Acts: Criminal Code Act 1899, Acts Interpretation Act 1954,
Human Rights Act 2019, Succession to the Crown Act 2013, Penalties and Sentences Act 1992, Information Privacy Act 2009) and Manitoba
(10 of 490) from temporary slice manifests outside the repo**, to limit network time; the committed manifests are the full lists.

| Source | Documents | Provision versions | Passages | Citation links (resolved) | Errors |
|---|---|---|---|---|---|
| `au-tas-legislation` | 43 | 37,136 | 33,371 | 2,099 (1,026) | 0 (Debits Duties Act 2001 answered 404, removed) |
| `ca-on-e-laws` | 37 | 52,033 | 46,605 | 4 (0) | 0 |
| `au-qld-legislation` | 6 | 12,527 | 11,029 | 1,317 (422) | 0 |
| `ca-mb-manitoba-laws` | 10 | 7,202 | 6,554 | 0 | 0 |

`verify-sources`: 96 of 96 pass. Full test suite: 783 passed (46 in `tests/test_subnational.py`); ruff and mypy clean. Searches:
`AU-TAS` "Any person who commits murder is guilty of a crime" -> `Criminal Code (Tas) s 158`; `AU-QLD` "unlawfully kills another" ->
`Criminal Code (Qld) s 306(1)`; `CA-ON` "final order of a judge of the Superior Court of Justice" -> `Courts of Justice Act, RSO 1990,
c C.43, s 19(1)`; `CA-MB` "chief adjudicator" -> `The Human Rights Code, CCSM c H175, s 1 “chief adjudicator”`; `AU` "unlawfully
kills another" and `CA-MB` "trespass to property premises" -> no hit (no leak; `SUBNATIONAL_LEGISLATION_EXCLUDED` for `AU`).
The first Ontario run failed on a repeated section number in the Education Act (a duplicate eId collided with a subsection's;
now `--2`); Manitoba section ranges ("66 to 69") are kept whole.
