# Blue Cat Backend: Universal Legal Intelligence System (ULIS)

Jurisdiction-aware legal corpus, retrieval and verification backend (Python, FastAPI,
PostgreSQL). The frontend is in [`Blue-Cat-Web`](https://github.com/CHEDFOX/Blue-Cat-Web).

> **Status:** Phase 1 foundations plus a working Phase 2 slice. Real data: **UK legislation**
> from legislation.gov.uk (9 Acts and instruments of the UK, Scottish and Welsh legislatures,
> each with its full point-in-time history), **Canadian federal Acts** in English and French
> (9 Acts from the Department of Justice consolidation), and a **sample of US federal law**
> from the US Government Publishing Office (96 official USLM files). Plus a fictional
> multi-jurisdiction test corpus. Coverage is partial and stated on every response; ULIS
> makes no claim of legal competence. Research aid only, not legal advice.

## What works today

- **Universal jurisdiction model**: a versioned, validated profile per jurisdiction
  (international → regional → state → province), with courts, precedent doctrine, treaty
  reception, languages and coverage gaps (`config/jurisdictions`).
- **Licence-gated ingestion** of Akoma Ntoso documents: content-addressed raw storage, SHA-256
  pinning, idempotent and non-destructive persistence, point-in-time versions, citation
  links, run records.
- **Jurisdiction-aware search**: ancestor chain plus territorial extent, national court
  system, treaties by party, "as of" date, per-language full text, curated cross-lingual
  concept expansion, and authority-sensitive ranking with every score component returned.
- **Exact source citations**: every hit carries a jurisdiction-specific citation, exact text
  and hash, source document, SHA-256 and locator. Tests re-derive each text from the raw
  source.
- **Legal-status limitations** on every hit: fictional or unverified profile, version
  period (derived or explicit end date), later versions, currency date, outstanding changes,
  prospective or proposed status, judicial treatment (or its absence), equally authentic
  language versions, treaty domestic effect, foreign authority, and no-corpus warnings.
- **Verification**: quote exactness, and source integrity (re-derive and compare; detects
  tampering).

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
# PostgreSQL 16: `docker compose up db` (compose file untested here), or a local server
export ULIS_DATABASE_URL=postgresql+psycopg://ulis:ulis@localhost:5432/ulis
python -m ulis.cli validate-config
python -m ulis.cli migrate
python -m ulis.cli ingest ulis-fictional-sample      # licence-gated; prints a run report
python -m ulis.cli verify-sources                    # re-derives every text from raw bytes
python -m ulis.cli search "retain personal data" --jurisdiction XA --as-of 2020-06-01
python -m ulis.cli ingest us-gpo-uslm-samples        # real US federal sample (git clone of a pinned GPO commit)
python -m ulis.cli ingest gb-legislation-gov-uk       # real UK legislation with history (polite, cached fetch)
python -m ulis.cli ingest ca-justice-laws-xml         # real Canadian Acts, EN + FR (sparse clone of a pinned commit)
python -m ulis.cli ingest in-india-code              # refused: licence review pending (exit 2)
uvicorn ulis.api.app:create_app --factory --port 8000
```

Tests (unit tests run without a database; integration tests need PostgreSQL):

```bash
ULIS_TEST_DATABASE_URL=postgresql+psycopg://ulis:ulis@localhost:5432/ulis_test pytest -q
ruff check ulis tests && mypy ulis
```

## API (all `/v1` routes need `X-API-Key` unless `ULIS_ENV=development` and no keys are set)

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/search?q=&jurisdiction=&as_of=&forum_court=&include_foreign=&include_proposed=&include_historical=&include_prospective=&language=&limit=` | Jurisdiction-aware search |
| GET | `/v1/provisions?uri=&as_of=&language=` | What a provision said on a date, with its full timeline |
| GET | `/v1/instruments?uri=` / `/v1/judgments?uri=` | Instrument (versions, events, treaty parties) / judgment (paragraphs) |
| GET | `/v1/jurisdictions`, `/v1/jurisdictions/{id}` | Tree; profile, hash, chain, coverage |
| GET | `/v1/sources`, `/v1/coverage`, `/v1/source-documents` | Source register, coverage, raw documents |
| POST | `/v1/verify/quote` | Exact-quote check against a provision version or paragraph |
| POST | `/v1/verify/source-documents/{id}` | Integrity re-derivation |

## Layout

```
config/            jurisdiction profiles, source descriptors (incl. licences), concept labels
sample_corpus/     fictional Akoma Ntoso corpus (CC0) + SHA-256 manifest
ulis/              jurisdictions, sources (connectors/parsers), storage, ingestion,
                   temporal, retrieval, verification, api, cli
migrations/        Alembic
docs/              design documents (start with architecture.md)
tests/             unit + PostgreSQL integration tests
```

## Design documents

[Architecture](docs/architecture.md) · [Core schema](docs/core-schema.md) ·
[Jurisdiction extensions](docs/jurisdiction-extensions.md) ·
[Sources and licensing](docs/sources-and-licensing.md) ·
[Multilingual retrieval](docs/multilingual-retrieval.md) ·
[Temporal model](docs/temporal-model.md) · [Roadmap](docs/roadmap.md) ·
[Security and evaluation](docs/security-and-evaluation.md) ·
[Test results](docs/test-results.md)

## Corpora

| Source | Jurisdiction | Content | Status |
|---|---|---|---|
| `us-gpo-uslm-samples` | US (federal) | 96 official GPO USLM files pinned to commit `4ec9db2`: 14 public laws as enacted, 11 statute compilations, 7 CFR edition volumes (221 parts), 64 bill/resolution versions; 12 files excluded with recorded reasons | Real, public domain; sample only |
| `gb-legislation-gov-uk` | GB, England and Wales, Scotland, Wales | 9 works with every point-in-time version (159 versions): Theft Act 1968, Interpretation Act 1978, Human Rights Act 1998, Consumer Rights Act 2015, Data Protection Act 2018, an SI, a Scottish Act, a Welsh Measure and a Welsh Act; territorial extent, repeals, outstanding effects | Real, Open Government Licence v3.0; selection only |
| `ca-justice-laws-xml` | CA (federal) | 9 Acts in English and French (18 files), current consolidation: Criminal Code, Canada Labour Code, Canada Business Corporations Act, Copyright Act, Divorce Act, Immigration and Refugee Protection Act, Access to Information Act, Privacy Act, PIPEDA | Real, Open Government Licence – Canada; not an official version |
| `de-gesetze-im-internet` | DE (federal) | 10 laws, current consolidation, German: GG, BGB, StGB, ZPO, StPO, HGB, VwVfG, BDSG, KSchG, GewStG ([notes](docs/jurisdictions/de.md)) | Real, free reuse (§ 5 UrhG; site terms); not the authentic text |
| `eu-eur-lex` | EU | 8 instruments in English, French and German: GDPR, Rome I, Brussels Ia, Consumer Rights Directive, AI Act (OJ texts as adopted), Charter, TEU and TFEU (2016 consolidated, documentation only); applies inside member States from accession ([notes](docs/jurisdictions/eu.md)) | Real, reuse under Decision 2011/833/EU; amendments listed, not applied |
| `es-boe-consolidada` | ES (State) | 6 norms with every consolidated version: Constitución, Código Civil, Código Penal, LOPDGDD, Ley 39/2015, Estatuto de los Trabajadores ([notes](docs/jurisdictions/es.md)) | Real, BOE reuse licence; informative, not the official text |
| `jp-egov-laws` | JP | 7 laws in Japanese: Constitution, Civil Code, Penal Code (14 revisions), Code of Civil Procedure, Personal Information Protection Act (24 revisions), Labour Standards Act, Companies Act ([notes](docs/jurisdictions/jp.md)) | Real, Public Data License 1.0; the Official Gazette prevails |
| `ch-fedlex` | CH (federal) | 7 laws in German, French and Italian: Federal Constitution, ZGB/CC, OR/CO, StGB/CP, DSG/LPD, ZPO/CPC, VwVG/PA; consolidation history since 2021 for four ([notes](docs/jurisdictions/ch.md)) | Real, free reuse (Art. 5 URG, Art. 19 PublA); the SR is not the authoritative publication |
| `fi-finlex` | FI | 7 Acts in Finnish and Swedish (consolidated): Constitution, Criminal Code, Contracts Act, Inheritance Code, Employment Contracts Act, Administrative Procedure Act, Data Protection Act; history for two ([notes](docs/jurisdictions/fi.md)) | Real, CC BY 4.0; consolidations are not authentic |
| `au-federal-register` | AU (Commonwealth) | 8 titles: Constitution, Acts Interpretation Act, Crimes Act, Privacy Act, Family Law Act, Criminal Code Act, Insurance Contracts Act, Electronic Transactions Act; full compilation history for four ([notes](docs/jurisdictions/au.md)) | Real, CC BY 4.0; only the stamped PDF is the authorised version |
| `nl-bwb` | NL | 6 Acts: Grondwet and UAVG with every consolidated version since 2002/2018, BW Boek 1 and 6, Wetboek van Strafrecht, Awb ([notes](docs/jurisdictions/nl.md)) | Real, CC0 1.0; consolidations are not official |
| `tw-moj-laws` | TW | 8 laws in Traditional Chinese, current text: Constitution, Additional Articles, Civil Code, Criminal Code, Code of Civil Procedure, Administrative Procedure Act, Personal Data Protection Act, Labor Standards Act ([notes](docs/jurisdictions/tw.md)) | Real, Open Government Data License 1.0; the Presidential Office Gazette is authentic |
| `se-riksdagen-sfs` | SE | 8 statutes in Swedish, current text: Regeringsformen, Brottsbalken, Avtalslagen, Ärvdabalken, LAS, Förvaltningslagen, 2018:218, Rättegångsbalken ([notes](docs/jurisdictions/se.md)) | Real, free reuse naming "Källa: Sveriges riksdag"; consolidations are not authentic |
| `dk-retsinformation` | DK | 6 Acts in Danish (official consolidations): Straffeloven, Aftaleloven, Arveloven, Funktionærloven, Forvaltningsloven, Databeskyttelsesloven; earlier consolidations for two ([notes](docs/jurisdictions/dk.md)) | Real, free unrestricted use (Civilstyrelsen terms); Lovtidende is authentic |
| `ie-irish-statute-book` | IE | 6 Acts as enacted (English): Unfair Dismissals 1977, Non-Fatal Offences 1997, Theft and Fraud 2001, Consumer Protection 2007, Companies 2014, Data Protection 2018 ([notes](docs/jurisdictions/ie.md)) | Real, Oireachtas (Open Data) PSI Licence; amendments not applied |
| `it-normattiva` | IT | 6 instruments in Italian: Costituzione (27 versions), Codice civile, Codice penale, privacy code, Statuto dei lavoratori, L. 241/1990 ([notes](docs/jurisdictions/it.md)) | Real, Normattiva legal note (reproduction allowed naming the source; not authentic) |
| `lv-likumi` | LV | 10 pages in Latvian, current edition: Satversme, Krimināllikums, Civillikums, data processing, Darba and administrative-procedure acts ([notes](docs/jurisdictions/lv.md)) | Real, likumi.lv terms (free access, no copyright in legislation); informative, not authentic |
| `ee-riigi-teataja` | EE | 6 acts in Estonian: Põhiseadus (4 versions), Isikuandmete kaitse seadus (3), Töölepingu seadus (2), Karistusseadustik, Võlaõigusseadus, Haldusmenetluse seadus ([notes](docs/jurisdictions/ee.md)) | Real, no copyright in legislation (Copyright Act § 5); publisher terms not read - counsel review recommended |
| `pl-eli-sejm` | PL | 5 acts in Polish as consolidated texts dated 2019-2024 (Kodeks cywilny, karny, pracy, postępowania administracyjnego, data protection act); newer consolidated texts exist only as PDF and are listed as outstanding changes; the Constitution is PDF-only and not ingested ([notes](docs/jurisdictions/pl.md)) | Real, no copyright in official texts (Prawo autorskie art. 4); not current law without the later changes |
| `us-uscode-house` | US (federal) | 10 positive-law titles of the US Code, official OLRC XML: 1, 5, 11, 17, 18, 28, 31, 35, 38, 44 - about 70,000 provisions at one release point ([notes](docs/jurisdictions/us-code.md)) | Real, public domain (17 U.S.C. 105); no history |
| `ar-infoleg` | AR | 6 instruments in Spanish: Constitución Nacional (original text), Código Civil y Comercial, Código Penal, Ley 25.326, Ley de Contrato de Trabajo, Ley 19.549 ([notes](docs/jurisdictions/ar.md)) | Real, CC BY 4.0 (portal terms); informative, the Boletín Oficial is authentic |
| `nl-rechtspraak` | NL (case law) | 30 Dutch court decisions 1919-2022 (Hoge Raad, Raad van State, CRvB, CBb, lower courts) with numbered considerations and links to the ingested statutes ([notes](docs/jurisdictions/nl-case-law.md)) | Real, Rechtspraak open data (source attribution); hand-picked sample |
| `eu-cellar-case-law` | EU (case law) | 31 judgments of the Court of Justice and General Court in English and French (Schrems, Google Spain, Van Gend en Loos, Costa v ENEL, Francovich, Kadi ...) with links to the ingested EU legislation ([notes](docs/jurisdictions/eu-case-law.md)) | Real, Cellar dataset statement (acknowledgement); hand-picked sample |
| `ulis-fictional-sample` | XA, XA-NP, XB, XR | Invented constitution, Acts, bill, judgments, bilingual code and treaty | Fictional test data |

Aurelia (`XA`), Borealis (`XB`) and the Northern Union (`XR`) are **invented** (ISO
user-assigned codes). India (`IN`) has a *draft, unverified* profile and no corpus; queries
for `IN` say so explicitly. India Code refuses automated access (HTTP 403), so India waits
for the publisher's permission. See [sources-and-licensing.md](docs/sources-and-licensing.md).
