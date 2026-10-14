# Onboarding a jurisdiction

The checklist every new jurisdiction follows. The UK (`legislation_gov_uk`) and Canada
(`justice_canada`) implementations are the reference examples. A jurisdiction is added with
configuration and new modules; the core schema, retrieval and verification code stay
jurisdiction-neutral.

## 1. Source and licence first (nothing is fetched before this)

1. Find the **official** publisher (gazette, legislation portal, court database). Prefer
   structured data (Akoma Ntoso, XML, JSON APIs, bulk downloads) over HTML.
2. Read and record, from the publisher itself: the reuse licence or statutory basis (for
   example "official works are not protected by copyright"), terms of use, API terms,
   `robots.txt`, rate limits, and whether the text is authentic, official or only
   informational. Quote or cite where each fact comes from in `licence.reviewed_by`/`notes`.
3. If access is refused (401/403/451, login wall, CAPTCHA), or the terms forbid automated
   reuse, **stop**: record the source as `planned`/`rejected` with the reason. Never work
   around a block, never use another identity, never scrape a site that forbids it.
4. Write `config/sources/<id>.yaml` (schema: `ulis/sources/registry.py`). `status: active`
   only with `licence.review_status: approved` and the permissions it states.

## 2. Profile

`config/jurisdictions/<ID>.yaml`, `verification.status: draft_unverified`, version `0.1.0`.
Courts and precedent rules only as far as you can state them; uncertain points go in `notes`
("To verify - ..."). `coverage.known_gaps` must say plainly what is **not** ingested
(other instrument types, case law, history, languages, subnational law).

## 3. Connector

Reuse what exists:

- `git_snapshot` for a public repository pinned to a commit (`sparse: true` for large ones);
- `ulis/sources/connectors/polite_http.py` (`PoliteHttp`) for websites and APIs: identifying
  User-Agent `ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)`,
  robots crawl delay (5 s if none is given), back-off on 429/5xx, refusals respected, HTTPS
  only to the hosts the manifest lists, cache under `var/sources/<host>/`, offline mode.

A connector reads a manifest (`config/manifests/<id>.yaml`) that lists exactly what to
fetch, yields `RawDocument`s (exact bytes as received, the URL, the retrieval time), and
supports `offline=True` for tests. Register it in `build_connector`
(`ulis/ingestion/pipeline.py`).

## 4. Parser

Produces `ParsedDocument`s (`ulis/sources/normalized.py`). Rules:

- **Text is exact source text.** Editorial notes, footnotes and amendment histories go in
  `note`, never in `text`. Nothing is generated, summarised or translated.
- **Identifiers are stable and language-neutral**: one work URI per instrument
  (`/<cc>/<type>/<native id>`), one `eid` per provision, identical in every language version,
  so parallel versions group together. `num_path` is language-neutral too; the jurisdiction
  module renders it per language.
- **Dates come from the source.** Use per-provision validity when the source gives it
  (`validity_basis: source_periods`); otherwise say what is known: `attested_at_edition`
  (known only to be in force at the consolidation date), `enactment_date_presumed`, or
  `proposal_stage`. A missing date is `None`, never a guess.
- `text_origin`: `official_authentic`, `official_consolidation`, `official_revised`,
  `official_conversion` or `unofficial_compilation`; `authoritative` is true only for
  language versions that are legally authentic.
- Repealed provisions: `status: repealed`, empty text, the repeal note in `note`.
  Unapplied amendments go in `outstanding_changes`.
- Parse with `defusedxml` (`forbid_dtd=True`); reject unexpected roots loudly.

Register the parser in `PARSERS`, `PARSER_VERSIONS` and, if it has its own URIs,
`resolve_uri` (`ulis/ingestion/pipeline.py`). If the language is new and PostgreSQL has a
text-search configuration for it, add it to `SEARCH_CONFIGS` (`ulis/languages.py`);
otherwise `simple` is used (state that in known gaps).

## 5. Jurisdiction module

`ulis/jurisdictions/modules/<cc>.py`: an `ext` model (strict) for native identifiers, and
citation formatting in the jurisdiction's own convention, in each official language.
Register it in `ulis/jurisdictions/modules/__init__.py`. Profile `citation.module` names it.

## 6. Tests and evidence

- Small real fixtures under `tests/fixtures/<source>/` (a few documents; gzip large ones),
  with a README naming the source, licence and retrieval date.
- Unit tests: parser (structure, a repealed provision, dates, languages), citations in every
  language, connector offline behaviour.
- PostgreSQL tests (marked `db`): ingest the fixtures through the pipeline with an in-memory
  connector; search finds a known provision with the right citation and limitations;
  point-in-time lookups behave as the source states.
- Add the profile id to `tests/test_catalog.py` and the source to the approved set.
- Run `ruff check`, `ruff format --check`, `mypy ulis`, the full suite, then a real
  `ingest` of the manifest and `verify-sources`. Record the **actual** numbers in
  `docs/jurisdictions/<cc>.md` together with the source, licence, coverage, citation examples
  and known gaps.

## 7. What not to claim

A jurisdiction with a handful of instruments is a **sample**. The profile's known gaps, the
source's limitations and every search response say so. Coverage is never described as
complete unless the whole official corpus is ingested and verified.
