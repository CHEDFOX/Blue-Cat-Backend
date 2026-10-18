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
  the delay rule below, back-off on 429/5xx (honouring `Retry-After`), refusals respected, HTTPS
  only to the hosts the manifest lists, cache under `var/sources/<host>/`, offline mode.

**The delay rule.** One connection per publisher host (requests are sequential, never parallel), and
between two request starts:

    delay = max(the publisher's robots.txt Crawl-delay for that host, if any,
                the manifest's crawl_delay_seconds)

- The manifest's `crawl_delay_seconds` is what the research found the publisher allows. Without a
  manifest value the connector uses 5 s. A manifest may go below 5 s, down to the project-wide minimum
  `MIN_CRAWL_DELAY` (1 s), **only** where the publisher states no Crawl-delay, rate or quota of its own;
  `manifest_crawl_delay()` (`polite_http.py`) refuses anything shorter than 1 s.
- `PoliteHttp(..., honour_robots_delay=True)` reads the host's robots.txt afresh before the first network
  request of each run and applies its Crawl-delay (the group for `ULIS-research-bot`, else `*`) as a floor,
  so a Crawl-delay the publisher adds later wins over a shorter manifest value without a code change.
  robots.txt governs only its own host: a Crawl-delay on a sister site does not carry over (write down why).
- A published rate limit that is stricter than robots.txt (for example "one call per 10 s", or a stated wish
  not to loop over the whole collection) goes into the manifest **and** into the connector as its own
  minimum (`manifest_crawl_delay(manifest, minimum=...)`), with the publisher's words in a comment.
  Never go above a published limit, and never use more than one connection unless the publisher
  explicitly permits it.
- Record the evidence (URL, date, the words) in the manifest header and in the source's `rate_limits`.
  If the publisher asks for notice before a full crawl, or for contact before going faster, draft the
  message in `docs/permission-requests.md` and keep the current pace until a written answer arrives.
- `load-all` never runs two sources on one host at the same time, so they share the publisher's budget.
  `load-all --dry-run` shows the delay each source will use and the network time it implies.

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
