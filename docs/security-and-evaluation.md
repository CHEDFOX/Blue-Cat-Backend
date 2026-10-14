# Security requirements and evaluation criteria

## A. Security requirements

Status markers: **[done]** implemented and tested in this repository; **[planned]**
required before production use.

### Data protection and access

- **[done]** API key authentication on all `/v1` routes (constant-time comparison, Bearer or
  `X-API-Key`). The app refuses to start outside development without keys. Keys are never
  logged; callers are identified by a key hash prefix.
- **[done]** Ingestion is not exposed over HTTP; corpus writes happen only through CLI and
  workers with database credentials.
- **[done]** Search audit stores a SHA-256 of the query, never its text. Lawyers' questions
  can reveal client matters.
- **[done]** Security headers (`nosniff`, `DENY` framing, `no-referrer`, `no-store`).
- **[done]** Frontend calls the API server-side, so the API key never reaches the browser.
- **[planned]** OIDC/SAML SSO for users; per-organisation tenancy; PostgreSQL row-level
  security for matters; role-based access (reader, researcher, admin, reviewer).
- **[planned]** Encryption at rest (database, object store with SSE-KMS), TLS everywhere,
  separate keys for client documents; secrets in a secret manager.
- **[planned]** Data-processing agreements and zero-retention settings before any client
  text is sent to an LLM provider; per-organisation opt-in; regional data residency.
- **[planned]** Rate limiting and abuse detection per key; request size limits at the proxy.

### Integrity and supply chain

- **[done]** Raw documents are content-addressed (SHA-256) and stored owner-only (0600/0700).
  Reads verify the digest.
- **[done]** Manifest hash pinning; path-traversal protection in the connector.
- **[done]** Safe XML parsing (`defusedxml`, DTDs forbidden): XXE and entity-expansion
  payloads are rejected (tested).
- **[done]** Integrity re-derivation: every stored text can be re-extracted from raw bytes
  and compared by hash (`ulis verify-sources`, API `/v1/verify/source-documents/{id}`).
  Tampering with either the raw object or the stored text is detected (tested).
- **[done]** Non-destructive ingestion: conflicting texts are logged, never overwritten.
- **[planned]** Signed ingestion runs; object-store versioning and retention lock; dependency
  pinning and vulnerability scanning in CI; SBOM.

### Model safety (Phase 4+)

- **[planned]** Retrieved text is treated as data, never as instructions (prompt-injection
  isolation). Tool calls allowlisted. The model cannot write to the corpus.
- **[planned]** Every model output is stored as a `proposition` with `produced_by` and
  `verification_status`, and must link to supporting passages. Unsupported claims are
  blocked from briefs.

## B. Evaluation criteria (per jurisdiction, independently)

A jurisdiction is shown to users only when it meets its gates. Results are published on a
coverage and quality card.

### Corpus and ingestion

| Metric | Gate |
|---|---|
| Parse accuracy (units whose text exactly matches a hand-checked sample) | ≥ 99.5% |
| Re-derivability (stored text equals re-extracted text, by hash) | 100% |
| Version-date accuracy (valid_from/valid_to vs official sources, sampled) | ≥ 99% |
| Coverage measured against the official inventory (instruments, years, languages) | Published; gaps listed in profile |
| Licence approval for every active source | 100% |

### Retrieval (expert-built evaluation set: question, facts, date, gold authorities)

| Metric | Gate (initial, revisit with reviewers) |
|---|---|
| Recall@20 for gold *binding* authorities | ≥ 0.90 |
| nDCG@10 | Baseline recorded; must not regress |
| Correct version for the question's date | ≥ 99% |
| Foreign or proposed material presented as binding | **0** |
| Latency p95 (search) | ≤ 500 ms at target load |

### Citations and verification

| Metric | Gate |
|---|---|
| Fabricated authorities (a citation not in the corpus) | **0** |
| Quotations that are not exact (unless labelled normalised) | **0** |
| Citation format correctness (module output vs reviewer) | ≥ 98% |
| Hits missing currency, coverage or authority information | **0** |

### Research briefs (Phase 4–5, graded by qualified lawyers in that jurisdiction)

Accuracy of statements of law; completeness, including adverse authority; correct
authority hierarchy; correct temporal analysis; clear separation of facts, allegations,
inferences and open questions; usefulness. Each criterion is graded on a 1–5 rubric with
inter-rater agreement measured. A jurisdiction is released only when an agreed threshold is
reached and no critical error (wrong law, fabricated authority, binding/persuasive confusion)
appears in the sample.

## C. What the current tests cover

See [test-results.md](test-results.md). In summary: profile and licence validation,
parser safety and determinism, point-in-time correctness at interval boundaries, authority
classification, citation formatting, idempotent and non-destructive ingestion,
jurisdiction-aware filtering, exact source citations (each hit's text re-derived from the
raw source), integrity tamper detection, and API authentication and audit.
