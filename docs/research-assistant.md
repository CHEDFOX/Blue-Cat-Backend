# Research assistant

A research aid for lawyers, never legal advice. An LLM understands a question or case file, uses the
existing retrieval code as tools, and drafts structured guidance that may cite only retrieved
authorities. A verifier with no LLM then re-reads every cited text from the database.
Code: `ulis/assistant/`, `ulis/api/research.py`, table `research_jobs` (migration `0006`).

## Flow

```
POST /v1/research ──202 {id}──►  job (thread pool, in this process)        GET /v1/research/{id}
                                  │                                          polls status + progress
  1 understanding  1 model call: facts, jurisdictions (default: the given one), as_of, issues
  2 searching      per issue: tool loop (search_law / get_provision / coverage / list_jurisdictions)
                   every tool hit -> registry A1, A2... (exact record: uri, version id, text, sha256)
  3 drafting       1 model call, JSON schema, may cite registry ids only (no case text, only facts)
  4 verifying      no LLM: re-read from DB, hash compare, quote check, drop unsupported items
```

## Tools (strict JSON schemas, `ulis/assistant/tools.py`)

| Tool | Wraps | Notes |
|---|---|---|
| `search_law(query, jurisdiction, as_of, languages?, include_case_law?, query_language?)` | `retrieval.search` | at most 8 hits, text clipped to 1500 chars for the model; registry keeps the full text |
| `get_provision(uri, as_of, language?)` | `retrieval.lookup.provision_at` | full text (6000 chars max), other versions, child uris; no version on the date = nothing citable |
| `list_jurisdictions()`, `coverage(jurisdiction)` | catalog, `retrieval.coverage` | corpus status, counts, languages, known gaps |

Invalid arguments and unknown tools return an error result; they never raise and never consume search budget.

## Verification rules (`ulis/assistant/verify.py`)

- Each cited authority is re-read by version/paragraph id and uri; stored text must equal the retrieved
  text and `sha256(text)` must equal the stored hash. `authorities[].text` always comes from the database.
- A reasoning item survives only if it cites at least one authority, every id is registered and verified,
  every quotation (straight, curly, German, French or CJK quotes; `...` and `[altered]` split a quote) occurs
  in a cited authority (`ulis/verification/quotes.py`), and any `A<n>` in its text is one it cites.
- Conclusions, uncertainties, summary and next steps get the same quote and id checks.
- Dropped items are counted in `removed_claims` (details in `verification.removed`, without the text).
  An issue left without support concludes "insufficient authority indexed", confidence low. Confidence is
  capped at medium when anything was dropped or a cited authority has a critical limitation.
- Limitations of each authority come from `ulis/verification/limitations.py`; coverage notes come from the profile
  (`corpus_status`, `known_gaps`) and counts, never from the model. The disclaimer is always present.

## Limits and safety

- 400 invalid JSON; 413 question > 4000 chars, case_text > 60000 chars or body > 1 MB; 422 other invalid
  input (errors never echo the text); 429 `Retry-After` when a key has `ULIS_RESEARCH_MAX_ACTIVE` jobs
  running or `ULIS_RESEARCH_RATE_PER_HOUR` created; 503 without `ANTHROPIC_API_KEY` (rest of the API unaffected).
- Rate limits: the shared limiter in `ulis/api/security.py` charges every `POST /v1/research*` one unit (three for `depth=deep`); see `docs/deployment.md`.
- Case text is data: control, bidi and tag characters stripped, wrapped in per-job random delimiters (forged
  closing tags are neutralised), shown to the model only in the understanding call, the system prompt says
  to ignore instructions in data. Tool schemas, prompts and output schemas never depend on the input.
- Caps per job: searches (`ULIS_RESEARCH_MAX_SEARCHES`, deep: `..._DEEP`) and total output tokens.

## Privacy

Question and case text exist only in memory while the job runs. Never stored, logged or audited, also not
in errors. The table keeps a SHA-256 of the request, progress, and the structured result (which contains the
model's summary of the case); rows are deleted at `expires_at` (purge on startup and on each new job; reads of
expired ids are 404). Audit events: `research_created` / `research_finished` with the digest, sizes, depth,
status and token counts. A job can only be read with the key that created it. Jobs run in this process: a
restart marks `queued`/`running` jobs failed (`interrupted`); run a single uvicorn process.

## Configuration

| Variable | Default | |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | required for `/v1/research` |
| `ULIS_LLM_MODEL` / `ULIS_LLM_MODEL_DEEP` | `claude-sonnet-5-5` / `claude-opus-5-5` | `depth` standard / deep |
| `ULIS_RESEARCH_MAX_SEARCHES` / `_DEEP` | 12 / 30 | `search_law` calls per job |
| `ULIS_RESEARCH_MAX_OUTPUT_TOKENS` | 48000 (x2 deep) | output tokens per job, all calls |
| `ULIS_RESEARCH_TTL_HOURS` | 24 | result lifetime |
| `ULIS_RESEARCH_WORKERS`, `_RATE_PER_HOUR`, `_MAX_ACTIVE` | 4, 30, 3 | concurrency and per-key limits |
| `ULIS_LLM_FALLBACKS` | `default` | server-side refusal fallback; dropped automatically if the model rejects it, `off` disables |
| `ULIS_LLM_EFFORT` | `auto` | per-stage effort; `off` for models without the parameter |
| `ULIS_LLM_TIMEOUT_S` | 600 | per request |

The client uses the official SDK: streaming, strict tool schemas, structured output (retried once in prose
mode if the schema is rejected), prompt caching on the tool loop, no forced `tool_choice`.

## Trying it with a real key

```
export ANTHROPIC_API_KEY=...            # and ULIS_DATABASE_URL for your database
python -m ulis.cli research --question "How long may an employer give notice?" --jurisdiction DE --as-of 2024-01-01
curl -X POST localhost:8000/v1/research -H "X-API-Key: $KEY" -H 'content-type: application/json' \
     -d '{"question":"...","jurisdiction":"DE"}'          # then GET /v1/research/{id}
python scripts/eval_research.py --dry-run               # 17 questions in eval/questions.yaml; drop --dry-run to run
```

The CLI prints the JSON result on stdout and progress on stderr. The evaluation scores expected
citations (recall), removed claims, verification and token use; controls without a corpus must come
back "insufficient authority indexed". Expected citations are canonical provisions known from the
connector tests, not checked against your database. Tests use `FakeLLM` (`tests/test_assistant_*.py`).

## Known limitations

- Verification proves that cited texts and quotations exist and are unaltered, not that the legal
  reasoning is right; paraphrases are not checked, only quotations and ids.
- Recall depends on keyword retrieval and on the model's queries; there are no embeddings yet.
- Case law and treatment depend on what is indexed; citator coverage is stated per authority.
- A real SDK/API call could not be exercised in the build sandbox (no key): request shapes follow the SDK
  documentation and are covered by stubbed-client tests only.
- Results are the model's summary of the case; they expire after the TTL.
