# Go-live: the laws in force of eleven launch markets

The first release loads the laws in force of the **US (federal), the UK, France, Germany, the EU, Canada (federal), Australia
(Commonwealth), Spain (State), Sweden, Finland and Japan** (`config/launch-markets.yaml`; why these: [markets/launch-markets.md](markets/launch-markets.md)).
Court decisions, old editions, the states and provinces that are only partly covered, and every other country load on request
(`docs/deployment.md` section 4). Every answer states the date it was checked against; a date before the data starts is reported
as "cannot be checked", never answered from today's text.

Updated 2026-10-18. Only the US eCFR has been loaded in full here (below); every other figure comes from the publishers' own
indexes and from slices of 30-100 works per source, and the sizes are plus or minus 50 percent.

## What `load-all` loads by default

Fourteen sources, every one of them the publisher's whole collection of law in force as its own index lists it (the exceptions
are under "Known gaps"). Pace: the publisher's robots.txt `Crawl-delay` where it sets one (UK 5 s, Australia 10 s), 1 s where
the publisher states no limit, one connection per website, never two sources on one website at once (`docs/onboarding-a-jurisdiction.md`
section 3; evidence in each manifest header). Japan and Finland load from the publishers' own bulk downloads.

| Source | Country | What | Requests | Pace | Network time |
|---|---|---|---|---|---|
| `us-ecfr` | US | all 50 CFR titles (49 files; title 35 reserved) | 50 | 5 s | 4 min |
| `us-uscode-house` | US | all 53 titles of the US Code and the 5 appendices (Federal Rules of Civil, Criminal and Appellate Procedure, Evidence, Bankruptcy) at release point 119-121 | 58 | 5 s | 5 min |
| `jp-egov-laws` | Japan | 8,989 laws in force, from e-Gov's official XML bulk download (one 283 MB archive) | 51 | 5 s | 4 min + download |
| `fr-dila-legi` | France | codes, laws, organic laws, ordonnances, decree-laws and decrees (53,383 decrees), from the DILA LEGI stock and its daily archives | ~470 | 5 s | ~40 min + 3 GB download |
| `ca-justice-laws-xml` | Canada | all 953 federal Acts and 4,462 regulations, English and French | git clone | - | minutes |
| `fi-finlex` | Finland | 5,579 statutes in force with consolidated text, Finnish and Swedish, from Finlex's daily package (4.1 GB) | ~25 | 1 s | download (bandwidth) |
| `se-riksdagen-sfs` | Sweden | 5,353 statutes in force (SFS) | 5,353 | 1 s | 1 h 30 min |
| `de-gesetze-im-internet` | Germany | 6,141 federal laws and ordinances (the publisher's whole table of contents) | 6,141 | 1 s | 1 h 45 min |
| `es-boe-consolidada` | Spain | 6,826 State norms of the BOE consolidated collection with their full history | 6,827 | 1 s | 1 h 55 min |
| `au-federal-register` | Australia | all 1,262 principal Acts in force (901 compilations + 361 never-amended Acts as made) | ~1,440 | 10 s | ~4 h |
| `eu-eur-lex` | EU | 33,970 acts in force (27,269 Formex 4, 6,336 HTML, 365 XHTML; 3,252 as consolidated) | ~69,900 | 1 s | ~19-29 h |
| `gb-legislation-gov-uk` | UK | 7,178 Acts, Measures and Orders (100 Welsh Acts and Measures in Welsh as well), current text | 7,278 | 5 s | ~10 h |
| `gb-legislation-gov-uk-secondary` | UK | 17,931 revised statutory instruments (583 Welsh), current text | 18,514 | 5 s | ~26 h |
| `gb-legislation-gov-uk-secondary-made` | UK | 65,456 never-amended statutory instruments, as made (2,992 Welsh) | 68,448 | 5 s | ~95 h |

`load-all --dry-run` prints the same plan with lower bounds. Parsing and indexing come on top: the full eCFR (1.94 million
provision versions) took about an hour on 4 cores.

**Wall time with `--jobs 6`.** Everything except the UK is loaded within about a day (the EU is the longest at 19-29 h). The three
UK sources share one website and its 5 s delay, so they run one after another: 10 h for the Acts, then 26 h for the revised
instruments, then 95 h for the never-amended instruments, **about 5.5 days for the whole set**. Each source is its own transaction
and is searchable as soon as it commits.

**Faster first go-live.** Load everything except the never-amended UK instruments first (`--skip gb-legislation-gov-uk-secondary-made`):
**about 1.5 days**, then add them with `load-all --only gb-legislation-gov-uk-secondary-made` while the service runs. Going faster on the
UK needs The National Archives' agreement (below).

## Size, with lossless compression

`docs/deployment.md` section 13 has the measurements and the recipes. Nothing is lossy: every text and every source byte comes
back exactly, and `verify-sources` keeps re-deriving every stored text from the publisher's original bytes.

| What | Without | With |
|---|---|---|
| PostgreSQL data directory (on ZFS or btrfs with zstd) | ~60 GB | ~23-27 GB |
| Raw source documents (stored zstd-compressed, level 9) | ~35 GB | ~6-7.5 GB |
| Download cache (`load-all --prune-cache` after verification) | ~35 GB | 0 |
| Each database dump (pg_dump zstd) | ~8 GB | ~6.3 GB |

Steady state about **30-35 GB**; the peak during the first load is about 70-100 GB. **Server:** a 200 GB disk is enough with
room to spare; 8 vCPU and 32 GB of RAM are recommended (searches stay fast when the indexes fit in memory), 4 vCPU and 16 GB
are the minimum (then load with `--jobs 3`). Raise `PG_SHARED_BUFFERS` to 25% of RAM.

## Before the load: what the owner sends

Drafts are in [permission-requests.md](permission-requests.md), section "Notices and faster routes for active sources".

1. **Australia:** the Federal Register of Legislation asks to be contacted 1-2 weeks before a full crawl. Send the notice first.
2. **UK:** legislation.gov.uk's fair-use policy asks to be contacted before a large one-off crawl. Send the notice before loading
   the UK sources; ask in the same message for a faster or bulk route (their Research platform is invitation-only).
3. Optional courtesy notes to the EU Publications Office, the BOE, the Bundesamt für Justiz and the Riksdag before the first
   1-request-per-second run (not required by them).

## Steps on the server

1. Prepare the disk: the PostgreSQL data directory on ZFS (`recordsize=32K`, `compression=zstd`) or btrfs (`compress-force=zstd:3`),
   `docs/deployment.md` section 13.
2. Set up the server as in `docs/deployment.md` sections 1-3 (Docker, `deploy/.env` from `deploy/.env.example`: `ULIS_DOMAIN`,
   `ULIS_ACME_EMAIL`, `POSTGRES_PASSWORD`, `ULIS_API_KEYS`, and `ANTHROPIC_API_KEY` for the research assistant), then
   `docker compose up -d --build` in `/opt/ulis/deploy` and check `/ready`.
3. `docker compose --profile tools run --rm cli load-all --dry-run` (the table above), then
   `docker compose --profile tools run -d --name ulis-load cli load-all --jobs 6 --prune-cache --skip gb-legislation-gov-uk-secondary-made`;
   follow it with `docker logs -f ulis-load`. Add the never-amended UK instruments afterwards with `--only`.
4. When it ends: `... cli status` and `... cli verify-sources` (every stored provision is re-derived from the raw bytes).
5. Check `GET /v1/coverage` (needs an API key): per country, what is loaded and what the profile says is missing. Publish that page.
6. Run the evaluation questions (`eval/questions.yaml`) and read the failures before inviting testers.

## Before taking money

* The licence readings in `docs/licence-review.md` are the project's own, not counsel's. Have a lawyer check them.
* Set `ULIS_COMMERCIAL_USE=1`: the gate then refuses every source whose commercial use is not allowed (all fourteen sources above pass:
  "allowed" or "allowed with conditions", the conditions being attribution and not presenting the text as the official version).
* The Docker/Caddy/systemd setup and the real model have not been run end to end; expect small fixes on the first deploy.

## Known gaps (stated, not hidden)

* **UK:** about 99,500 items exist on legislation.gov.uk only as PDF scans and stay out (robots.txt disallows them; no OCR),
  including pre-1996 Northern Ireland Statutory Rules and pre-1991 Local Acts still in force. Revocation of never-amended
  instruments is not recorded by the publisher. Welsh is searched without stemming.
* **US:** statutory and editorial notes are not ingested; uncodified statutes only with `--include-history`; state law out of scope.
* **EU:** 52 acts in force have no machine-readable text and 67 no English version. A consolidated text starts on the
  date the publisher gives its first consolidated version, which can precede entry into force and application: the
  GDPR reads as in force from 2016-05-04 (published; in force 2016-05-24; applies from 2018-05-25, both dates stored as
  lifecycle events), so a search dated 2017 returns it without a warning (7 of 45 consolidated works start before entry
  into force, 4 before application).
* **France:** arrêtés (orders) are excluded by choice (81,452 more texts); amending articles without dates hold no text of their own.
* **Germany:** current consolidation only (the publisher keeps no history); the publisher describes its collection as "nahezu" complete.
* **Spain:** only the AEBOE consolidated collection (all norms with the rank of law, a selection of lower ranks); no autonomous-community law.
  A norm repealed with exceptions is recognised from the BOE's initial note (parser 1.2.0); an exception made by reference
  to another law (LO 15/1999 arts. 23-24, kept by LO 3/2018) is quoted, not applied, and one other wording of the note
  ("queda derogado el presente Real Decreto, a excepción del título VI") is not yet read.
* **Australia:** about 13% of never-amended Acts are refused by the parser (estimate from a 30-Act slice); legislative instruments
  (`au-federal-register-instruments`) are not loaded by default until their parser handles forms and tables.
* **Canada:** current consolidation only; 430 repeal-only regulation stubs and 18 repeal-only Act stubs are excluded.
* **Japan:** public notices (告示), Diet and Supreme Court rules and treaties are not in e-Gov; bulk-loaded laws record the amendment
  in force, not earlier ones.
* **Finland:** 33,006 statutes in force have no consolidated text at Finlex; 2% of documents keep their text in an attachment the parser refuses.
* **Sweden:** agency regulations are not part of SFS.
* **Not in this release:** case law everywhere; India (its law sites block the bot), Italy (commercial use unknown), the Netherlands and
  Switzerland (outside the launch set), the states and provinces listed under `not_yet`, and every country not named above.
