# Blue Cat Backend: Universal Legal Intelligence System (ULIS)

Jurisdiction-aware legal corpus, retrieval and verification backend (Python, FastAPI,
PostgreSQL). The frontend is in [`Blue-Cat-Web`](https://github.com/CHEDFOX/Blue-Cat-Web).

> **Status:** a working backend with real law from 27 countries and the EU (see
> [docs/corpus.md](docs/corpus.md)), court decisions from several jurisdictions, an LLM research assistant and a one-server deployment kit. Coverage is
> partial and stated on every response; ULIS makes no claim of legal competence, and the real
> language-model path and the Docker/Caddy/systemd kit have not yet been run end to end. Research
> aid only, not legal advice. **To run it on a server: [docs/deployment.md](docs/deployment.md).**

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
- **Research assistant** (`POST /v1/research`): a question or pasted case file plus a country goes
  through understanding, searching the corpus, drafting and a verification pass that re-reads every
  cited authority and removes any claim whose quote is not in the source. Case text is never stored
  or logged. See [docs/research-assistant.md](docs/research-assistant.md).
- **Operations**: `load-all` (every active source, smallest first, resumable), `status`, backups,
  restore, corpus export/import, API keys, rate limits, `/ready`, JSON logs; lossless space saving
  (zstd object store, `compact-objects`, `prune-cache`, zstd dumps, a compressing filesystem for
  PostgreSQL). See [docs/deployment.md](docs/deployment.md).

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
| POST | `/v1/research` | Start a research job (question, optional case text, jurisdiction, as-of date, depth); returns an id |
| GET | `/v1/research/{id}` | Job status and, when done, the answer with citations and the verification report (kept 24 h) |

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
[Research assistant](docs/research-assistant.md) · [Deployment](docs/deployment.md) ·
[Onboarding a jurisdiction](docs/onboarding-a-jurisdiction.md) · [Go-live](docs/go-live.md) ·
[Footprint research](docs/footprint-research.md) · [Test results](docs/test-results.md)

## Corpora

The full, generated list is in **[docs/corpus.md](docs/corpus.md)**: 144 active sources (laws and court decisions)
and 212 recorded as blocked, suspended or unlicensed, each with the publisher's own findings. The default load
(`load-all`) takes the complete laws in force of eleven launch markets: US (federal), UK, France, Germany, the EU,
Canada, Australia, Spain, Sweden, Finland and Japan; plan, sizes and known gaps in **[docs/go-live.md](docs/go-live.md)**. In short:

* **Laws, current text** for Argentina, Australia (Commonwealth, Queensland, Tasmania), Austria (blocked), Canada (all 953
  federal Acts in English and French, plus Ontario and Manitoba), Croatia, Czechia, Denmark, Estonia, the EU, Finland,
  France (108 codes and the main laws, with history), Germany (all 6,139 federal laws and ordinances, manifest only until
  loaded), Ireland, Italy, Japan, Latvia, Liechtenstein, Luxembourg, the Netherlands, Poland, Slovakia, Slovenia, Spain,
  Sweden, Switzerland, Taiwan, the UK (England and Wales, Scotland, Wales), the US (federal: US Code, CFR, samples;
  states: Texas, Utah, Washington, Oregon) and Uruguay.
* **Court decisions**: Netherlands, EU Court of Justice and General Court, France (Conseil constitutionnel, Cour de
  cassation, administrative courts), the UK Supreme Court, Sweden, US Supreme Court reports (to 586 U.S.) and the
  federal courts of appeals, Finland (data-protection decisions).
* **International**: treaty texts (European Convention on Human Rights, UN Charter, ICJ and ICC statutes ...) as
  published by the Swiss Federal Chancellery.
* The committed manifests list the publishers' whole open corpus where an official bulk route exists; the development
  database holds slices of the largest ones, so figures in the per-country notes are for what was actually loaded.
* Blocked or waiting for permission: India, New Zealand, Austria's RIS, German court decisions, UK Find Case Law, the
  European Court of Human Rights, the Supreme Court of Canada, Australian courts, most of Africa and Asia, and others
  (see the generated list and [docs/permission-requests.md](docs/permission-requests.md)).

Aurelia (`XA`), Borealis (`XB`) and the Northern Union (`XR`) are **invented** (ISO
user-assigned codes). India (`IN`) has a *draft, unverified* profile and no corpus; queries
for `IN` say so explicitly. India Code refuses automated access (HTTP 403), so India waits
for the publisher's permission. See [sources-and-licensing.md](docs/sources-and-licensing.md).
