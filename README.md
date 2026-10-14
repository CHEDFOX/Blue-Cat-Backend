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
| `ulis-fictional-sample` | XA, XA-NP, XB, XR | Invented constitution, Acts, bill, judgments, bilingual code and treaty | Fictional test data |

Aurelia (`XA`), Borealis (`XB`) and the Northern Union (`XR`) are **invented** (ISO
user-assigned codes). India (`IN`) has a *draft, unverified* profile and no corpus; queries
for `IN` say so explicitly. India Code refuses automated access (HTTP 403), so India waits
for the publisher's permission. See [sources-and-licensing.md](docs/sources-and-licensing.md).
