# Storage and search plan

Decision record, 2026-10-20. It covers the launch set of the 11 markets on Hostinger KVM 2 (2 vCPU, 8 GB RAM, 100 GB NVMe). KVM 4 (4 vCPU, 16 GB, 200 GB) is the fallback size.

Conventions used throughout:

- **Measured**: measured on the dev database on a 4-CPU, 15 GB machine with a warm cache, unless the line says otherwise. The dev database is 8,610 MB, with 2.52 M passages in 14 jurisdictions.
- **Estimate**: a projection. Every launch-set figure carries the ±50% on the launch counts in docs/go-live.md. The launch set is about 16.8 M provision versions, 14.6 M passages and 8.3 GB of raw text (estimate).
- MB = 10^6 bytes, GB = 10^9 bytes.
- Nothing in this plan is lossy. Every byte it removes is either an exact duplicate or exactly re-derivable, and each such claim was checked on every dev row.

Status (2026-10-20): step 1 is partly done: the `indexed_languages` fix and the strict stage limited to the
configurations indexed in scope shipped with this document (results identical on the 240 judged queries). Every other
step waits for the owner's go-ahead. The raw evidence of section 8 lives in the session's scratch directory, outside
the repository.

## 1. Decision

PostgreSQL stays the single source of truth, slimmed so that it stores every law text exactly once. Each text is compressed and carries a fingerprint that is checked every time it is read. Word search moves into one compact, separate search index (Tantivy). That index is rebuilt from PostgreSQL and holds no text of its own.

This is both the fastest and the smallest option we measured:

- **Faster.** On the same data, and finding the same matches, the slowest kind of search ("most of the words") ran 20 to 200 times faster.
- **Smaller.** At launch size, the database plus the index is estimated at about 15 GB, against about 48 GB for today's layout.
- **Fits in memory.** The index (about 2.3-2.9 GB, estimate) fits in the memory of the 8 GB server, so searches do not wait on the disk. Today's design cannot do this at launch size.
- **Nothing lawyers rely on changes.** Texts come back byte for byte. Every text can still be re-checked against the publisher's original files. Dates, statuses, citations and sources stay in PostgreSQL.

We rejected the PostgreSQL-only alternative. It needs no new component, but it is about twice the size unless it runs on a special compressing filesystem, and it leaves UK searches taking seconds at launch.

The costs:

- About 12.5-15.5 engineer-weeks of work (estimate).
- One visible change: results are ordered by a different scoring formula (BM25). AI judges preferred it, but a lawyer must review it before the old search is removed. The old search stays available as a fallback until that review passes and the new index has run in production for several weeks.

## 2. Expected results at launch-set scale on KVM 2

### 2.1 Disk

| Layout at launch scale | PostgreSQL | Search index | Data total | Basis |
|---|---|---|---|---|
| Today's layout | 47.9 GB (21.1 GB on ZFS-32K) | inside PostgreSQL: tsv 8.64 GB + GIN 1.77 GB | 47.9 GB | estimate. Bytes model D0 reproduces the measured dev size within 0.1%. docs/go-live.md says ~60 GB |
| PostgreSQL-only cleanup (rejected; for reference) | 32.95 GB, or 27.3 GB with text blocks; 15.4 / 13.8 GB on ZFS-32K | inside PostgreSQL | same | estimate, models D1 and D2 |
| Chosen, during the soak (slim passages kept as the fallback) | 27.3 GB | 2.9 GB | about 30 GB | estimate, model D2 plus the index |
| **Chosen, end state** | **12.3 GB** (6.8 GB on ZFS-32K, optional) | **2.3-2.9 GB** | **about 15 GB** (about 8-23 GB within the ±50%) | estimate, arch/kvm2fit/budget.py over model D3 without passages |

**At dev scale**, the end state is about 1,913 MB of PostgreSQL (model) plus an index of about 0.51-0.55 GB, against 8,610 MB measured today. The index size is computed from measured component sizes; that layout has never been built as a single index.

**Where the 48 GB goes at launch (estimate, today → end state):**

- These go to zero and are replaced by the 2.3-2.9 GB index: tsv 8.64, GIN 1.77, other passages columns 2.83 and passages btrees 1.57.
- These go to zero because they are exact duplicates or derivable:
  - passages.text, a copy of provision_versions.text: 4.61. It was byte-identical on 2,519,798 of 2,519,798 dev rows.
  - passages.context: 1.76.
  - the Japanese bigram forms: 0.68.
- provision_versions.text: 6.55 (pglz) → 1.37 (64 KB zstd-9 blocks; 6.04x measured on disk).
- Other provision_versions columns: 4.68 → 3.28 (binary hash, block pointer, NULL-means-derived source columns).
- provision_version_sources: 4.25 → 0.94. Only the extra attestations are stored; 92% of dev rows repeat the version's own source.
- provisions: heap 3.24 → 2.60 and indexes 3.31 → 1.18. The derivable uri goes, the unused and redundant indexes go, and the btrees are rebuilt fresh.
- provision_versions indexes: 2.66 → 1.52. Other tables: 1.37, unchanged.

**Whole disk, steady state (estimate): about 33-37 GB of the 100 GB.** That is the data (about 15 GB) plus:

- the object store, 6-7.5 GB (docs);
- WAL up to 4 GB (max_wal_size);
- Docker images, about 5 GB;
- one zstd pg_dump, 3-5 GB. That is an estimate: the dump has no passages and the text blocks are already compressed.

Today's layout on ext4 comes to about 69-71 GB by the same sum, with 47.9 GB of PostgreSQL and the documented 6.3 GB dump (estimate).

**Peak during the first launch load (estimate): about 55-60 GB.** This assumes three things:

- the canonical tables are loaded first;
- the download cache is pruned per source after verification;
- the Tantivy index and the slim passages fallback are built after the load.

The parts:

- During the load:
  - OS 5 GB, images 5 GB;
  - PostgreSQL about 16 GB (12.3 plus about 30% dead tuples);
  - WAL 4 GB;
  - objects 7.5 GB;
  - about 12 GB of cache in flight (budget.py assumption).
- After the load, with the cache gone:
  - Tantivy 5.8 GB (two generations during the final merge);
  - passages fallback about 15 GB.

If the cache is pruned only at the end, the peak is about 78 GB without the passages fallback (budget.py) and over 90 GB with it. Per-source pruning is therefore a requirement; it is a small CLI change. Today's documented peak is 70-100 GB (docs/go-live.md).

**If the launch load happens before steps 9-10** (see section 4), PostgreSQL holds about 41 GB with slim passages, plus the 2.9 GB index. It drops to about 26 GB of PostgreSQL once passages is dropped (estimate: D0 minus 4.61 + 1.76 + 0.68, minus about 14.8). That is why the canonical steps should be done on dev before the launch load.

### 2.2 Search speed

**Lexical stage at dev, measured on the same captured inputs.** The match sets are identical (strict 240/240; relaxed grid 434/434).

| Stage | Queries | PostgreSQL today, p50 / p95 / max | Tantivy, best layout, p50 / p95 / max |
|---|---|---|---|
| Strict (`_candidates`) | 240 | 12.21 / 39.11 / 404.3 ms | 2.60 / 6.05 / 15.5 ms per call, including the PostgreSQL tsquery SELECT (Rust alone 0.57 / 1.39 / 8.38) |
| Relaxed "most of the words", as run inside search() | 166 | 17.06 / 282.8 / 932.9 ms | 2.06 / 6.30 / 11.85 ms per round trip (Rust 0.83 / 3.94 / 6.69) |
| "any ceil(n/2) of n" forms | 146 | 34.1 / 409.6 / 1,981 ms | 0.66 / 2.12 / 9.67 ms (Rust) |

Both engines also pay `_relaxable` parsing in PostgreSQL: 3.75 ms at p50 (measured).

By scope, the relaxed totals measured are:

| Scope | PostgreSQL | Tantivy |
|---|---|---|
| EU | 2,237 ms | 22.1 ms |
| FR | 1,759 ms | 28.0 ms |
| FI | 1,482 ms | 45.5 ms |
| US | 366 ms | 4.5 ms |

**search() end to end:**

| Case | p50 | p95 | max | Basis |
|---|---|---|---|---|
| Today, dev | 170 ms | 587 ms | 1.4 s | measured, fix/final4.jsonl. The profile harness, with a fresh session per query, gives 183.6 / 598.7 / 1,645.1 ms |
| Today's design at launch | about 0.8 s | 4-6 s | 17-25 s | estimate (arch/profile launch_latency.py). Lexical time was measured linear in table size: x2.0-2.2 per doubling |
| Chosen at launch, hit rendering not yet batched | 160-220 ms | 260-400 ms | 0.6-0.9 s | estimate: the sum of the measured stage costs plus launch cache misses |
| **Chosen at launch, batched hit rendering** | **60-180 ms** | **160-360 ms** | **0.5-0.8 s** | estimate: the row above minus 40-100 ms of hit rendering |

End-to-end search() through Tantivy has never been run. The two "chosen" rows are sums of stage costs, built as follows (per search, warm, single client, limit 10):

1. **Front stages (measured at dev):** `_expansions` 1.3, `_scope` 1.4, coverage 1.2 and other 1.0 ms at p50. `_classify` 4.6 / 15.5 ms at p50/p95. The tsquery SELECT about 2.2 ms and `_relaxable` 3.75 ms at p50.
2. **Tantivy:** the dev figures above, times 1-3 for the largest launch scope, so about 6-12 ms lexical at p50, 20-40 ms at p95 and 50-100 ms worst (estimate). The largest scope is GB, at about 5.4 M passages, 2.9x today's US, assuming cost linear in the in-scope postings. Unscoped and treaty queries get no doc-range pre-filter, up to about 0.1-0.25 s (estimate).
3. **Hit rendering (measured):** 13.2 ms per hit and about 12 SQL statements per hit; 122.6 / 158.9 ms per page at p50/p95. Batching into about 10-15 statements per page saves 40-100 ms at p50 (estimate; 51% of the per-hit cost is ORM/Python work).
4. **Cache misses during hit rendering at launch:** 9-70 ms (estimate). That is about 10 hits x 25 random pages x a 30-60% miss rate x 0.115 ms per miss, the miss cost measured here. Hostinger NVMe may be 2-4x slower.
5. **Text block reads:** 10-20 x 0.40 ms (measured warm). **JSON encoding:** 3.2 / 6.5 ms (measured).
6. **HISTORY_INCOMPLETE, once cached:** about 0. Today it is 5.2 ms at p50 at dev, and an estimated 100-300 ms at launch.
7. **CPU speed:** KVM 2 vCPUs are assumed 1-1.5x slower than this machine (unmeasured).

limit=50 requests multiply hit rendering by about 5, which gives about 0.2-0.35 s once batched (estimate).

**Throughput on 2 vCPU:**

- Today at dev: 9.48 queries/s with 2 client loops on 2 pinned CPUs, 11.22 with the language fix (measured).
- Today's design at launch: 1.3-1.7 queries/s (estimate).
- Chosen design: about 10-15 queries/s before batching and about 20 after (estimate). This is the measured 178 ms of CPU per query minus PostgreSQL's lexical CPU. No concurrency test with Tantivy has been run.

**Cold start:**

- Today, the first search after an API restart takes 3,121 ms and reads 2,820 MB at dev (US scope, measured), because the coverage count scans the passages heap. At launch that would be 15-20 s (estimate). In the end state, coverage reads a summary table (milliseconds, estimate).
- Tantivy with its files evicted: 64.5 / 91.4 / 97.6 ms per query at p50/p95/max (restart and eviction before each of 30 queries, measured at dev).
- Prewarming the index when the helper starts takes about 3-10 s (estimate, from the 1.2 GB/s sequential read measured here).

### 2.3 What sits in RAM

| Item on KVM 2 (8 GB) | GB | Basis |
|---|---|---|
| OS, Docker, Caddy | 0.5 | estimate (arch/profile) |
| 2 uvicorn workers | 0.3 | measured: search process RSS about 103 MB after 240 queries |
| Tantivy helpers | 0.05 | measured: 4.3 MB of anonymous memory per helper |
| PostgreSQL shared_buffers | 1.5 | setting. Below the compose default of 2 GB, to leave page cache for the index |
| 10 backends | 0.35 | measured: about 35 MB each |
| Incremental ingest and indexer | 0.5 | estimate |
| **Left for the page cache** | **4.8** | arch/kvm2fit/budget.py |

That 4.8 GB of page cache holds:

- **The whole Tantivy index, 2.3-2.9 GB (estimate).** At dev, 240 queries touched 350 of 744 MB. Fast fields were 82-100% resident, postings 56-57% and positions 14%, and a second pass added nothing (measured). The same queries at launch need about 1.3-2.4 GB (estimate, x5-6).
- **PostgreSQL's hot core, 0.4-0.6 GB (estimate):**
  - the btree inner pages of the version, provision and period keys;
  - instruments and instrument_versions, about 0.33 GB at launch;
  - the name GIN indexes;
  - the summary tables.
- **A margin of about 1.3-2.1 GB (estimate).** It is about 0 if the launch counts come in 50% higher (index about 4.4 GB). Pages in shared_buffers are partly cached twice.

The leaf and heap pages for hit rendering, and the text blocks, do not need to stay in RAM. Each miss costs 1-2 random reads, about 0.115 ms per page here (measured).

Today's design at launch, for comparison: broad searches touch GIN 1.8 GB plus 18.5 GB of passages heap and TOAST (tsv 8.6, text copy 4.6, context 1.8, other 2.8, CJK 0.7; estimate). The working set of the 240 judged queries alone is about 5.3 GB (range 3-8, estimate), against about 4.5-5 GB of page cache. Broad searches therefore become disk-bound.

**Choose KVM 4 instead of KVM 2 if any of these happen:**

- The step 0 emulation shows search() p95 above about 400 ms, or the index being evicted from the page cache (proposed gate).
- The real launch counts put the index above about 3.5 GB.
- The launch has to happen before step 7. PostgreSQL search at launch size needs the 16 GB.

docs/go-live.md lists 4 vCPU and 16 GB as the minimum. KVM 2 is below it, and ingestion, index merges and rebuilds share its 2 vCPUs with the API.

## 3. The architecture

### 3.1 Components

| Component | What it stores | Size at launch (estimate) | Role |
|---|---|---|---|
| PostgreSQL 16, canonical tables | instruments and their versions, provisions, provision versions (validity, status, heading, note, 32-byte SHA-256, text pointer), extra attestations, citations, verification records, name indexes | 10.9 GB | single source of truth |
| `text_blocks` (in PostgreSQL) | every verbatim text, once, in 64 KB zstd-9 frames | 1.37 GB | text store |
| Small search tables (in PostgreSQL) | `search_index_journal`, `search_index_state`, `coverage_summary`, `lexeme_stats`, the cached HISTORY_INCOMPLETE set | under 0.05 GB | index bookkeeping |
| Tantivy index (ext4, `/srv/ulis/search/gen-N`) | lexemes, positions and filter values of each index unit; no text | 2.3-2.9 GB | word search, derived and rebuildable |
| `ulis-search` helper (Rust) | nothing; a process | about 5 MB of anonymous memory each | evaluates queries, writes the index |
| Object store | the publishers' raw bytes, zstd-9 | 6-7.5 GB (docs) | evidence for verify-sources |

**PostgreSQL, canonical and lean:**

- **provisions:** id, instrument_id, eid, num_path, parent_id, sort_order, kind, extent, index_unit. There is no uri column: uri = instruments.uri || '/~' || eid.
  - This holds for 2,556,690 of 2,556,690 rows.
  - No eid and no instrument uri contains '/~', so splitting at the first '/~' is unambiguous.
  - Indexes: the primary key, uq(instrument_id, eid), and a new partial index on parent_id.
- **provision_versions:** validity dates and their bases, status, heading, note, text_sha256 as bytea(32), and a text pointer (block_id, offset, length).
  - source_locator and source_document_id are NULL when they equal the derived value (provisions.eid, and the instrument version's document). That is true for 100% of the 2,755,417 rows today. A differing value is stored, so a future exception is never lost.
  - Indexes: the primary key and uq_provision_versions_period.
- **text_blocks(id, codec, raw_len, data bytea STORAGE EXTERNAL):**
  - Each block holds 64 KB of raw text at zstd level 9, with the frame checksum on.
  - Blocks are filled in document order: instrument version, provision sort order, language, valid_from.
  - Blocks are immutable and written in the same transaction as the versions that point into them. The SHA-256 of each text is stored outside its block.
  - Measured on 336 instruments (65,249 texts): 6.04x on disk, 0 SHA-256 mismatches, and 0.40 ms per lookup against 0.20 ms today.
- **provision_version_sources:** only the attestations that are not the version's own source, 8% of rows at dev and mostly UK point-in-time snapshots.
  - Columns: (provision_version_id, instrument_version_id), plus nullable source_document_id and source_locator, where NULL means derived.
  - A view exposes the full set, including the implied self rows.
- **Unchanged:** instruments; instrument_versions (plus a new index on source_document_id); source_documents; citation_links; lifecycle events; verification_records; concept labels; ingestion_runs; treaty tables; and the name GIN indexes of migration 0007, which sit on instruments and instrument_versions.
- **New small tables:**
  - `search_index_journal(seq bigserial, instrument_id or judgment_id, run_id)`: an outbox written in the ingestion transaction.
  - `search_index_state`: the committed watermark seq, the index generation and built_at.
  - `coverage_summary(jurisdiction, language, passages, instruments)`: written with every index commit. coverage.py and ops/status.py read it.
  - `lexeme_stats(config, lexeme, doc_freq)`: exact document frequencies for the relaxed term order and its IDF weights.
  - The HISTORY_INCOMPLETE set, cached per ingest epoch.
- **End state:** no passages table, no tsv, and no GIN over legal text.

**The Tantivy index (0.26.2, pinned):**

- **Documents and keys.** There is one document per index unit: a provision version with index_unit set, not repealed and with non-empty text, or a judgment paragraph. Each document is keyed by provision_version_id or judgment_paragraph_id, stored as u64 hi/lo fast fields. That key is what the API returns as passage_id.
- **Text fields.** `a_p` holds the weight-A lexemes of the text and `b_p` the weight-B lexemes of the context, both with term frequencies, positions and norms. `inst_key` is indexed so that a whole instrument can be deleted. There is no `all` field and no stored field.
- **Fast fields.** Every predicate of `_filters()` has one:
  - kind, status, language, search_config, jurisdiction_id, doc_type;
  - extent (multi-valued) and its empty flag;
  - valid_from/valid_to as days, with NULL flags;
  - instrument and text_length.
- **Order.** Documents are written in (jurisdiction, instrument, provision sort_order) order, so a scope is a few doc-id ranges per segment, and the range pre-filter skips the postings of other jurisdictions.
- **Lexemes.** They are PostgreSQL's own (pg mode). The indexer computes `setweight(to_tsvector(cfg, context), 'B')` and `to_tsvector(cfg, text)` in PostgreSQL; Japanese text first goes through `segment_for_search`. Recomputed lexemes equalled the stored tsv on 50,461 of 50,461 sample rows (measured). The check on the full corpus is a gate in step 6.
- **Disk.** The index lives on ext4, not ZFS. Compression measured only x1.18, and on ZFS on Linux the mmap'd files would be cached twice.

**The helper:**

- It is the crate at scripts/search_eval/tantivy, promoted with the experiment code from arch/tantivy/crate:
  - the pigeonhole min-match scorer (minmatch.rs);
  - the doc-range pre-filter (ranges.rs);
  - the per-segment fast-field column cache;
  - the a_p/b_p layout and the clustered build;
  - delete by instrument, commit payloads, and the docfreq and stats operations.
- There is one helper process per API worker, as `TantivyProcess` does today. Readers mmap the index and reload when a commit lands.
- There is exactly one writer, the indexer, under a PostgreSQL advisory lock. Tantivy also holds its own lockfile.

### 3.2 Ingest

1. **Fetch and parse.** The connector fetches the source. The raw bytes go to the object store (zstd), then the parser runs.
2. **One PostgreSQL transaction** writes:
   - the canonical rows, with derived columns left NULL;
   - new text blocks for new or changed texts, packed per instrument in document order;
   - `_recompute_validity` for each touched instrument, which already runs inside the run before reindexing (ulis/ingestion/pipeline.py:556-565);
   - one `search_index_journal` row per touched instrument or judgment, next to the place where pipeline.py:563 calls rebuild_passages today;
   - the ingestion_runs row.

   A rolled-back run leaves no journal rows. Ingestion never deletes provision versions and never rewrites their texts, so the index keys stay valid and the blocks really are immutable.
3. **Index sync.** After the commit (at the end of the run, on LISTEN/NOTIFY, or from a timer), `ulis index sync` runs:
   1. Take the advisory lock and read the journal rows above the watermark.
   2. For each instrument, derive the index units with the indexer.py rules. The context is the instrument version title plus the top heading plus the heading, in a deterministic order (see step 5).
   3. Compute the lexemes in batched SELECTs, at about 7.2k rows/s per core (measured).
   4. Call `delete_term(inst_key)`, then add the new documents, sorted by (jurisdiction, instrument, sort_order).
   5. Commit with payload = the highest journal seq, then update `search_index_state`, `coverage_summary` and `lexeme_stats`.

   Replay is idempotent. While the API is serving, the indexer uses one writer thread.
4. **Verify.** `verify-sources --only <source>`.

Measured costs:

| Operation | Time |
|---|---|
| 63-passage instrument | 42 ms (commit 30.5 ms) |
| 26 CFR part 1 (82,925 passages), 2 writer threads | 18.5 s (commit 0.63 s) |

Upkeep:

- **Forced merge** when deletes pass about 3% of the index. At dev, a 3% delete share cost +5.3% size and +10% latency, and the merge took 27.7 s; at launch, a few minutes (estimate). The default LogMergePolicy never merges for deletes alone.
- **Block repack** when a block is more than about 20% garbage.
- **Full clustered rebuild** into `gen-N+1` after big reloads, about 1-1.8 h on 2 vCPU (estimate), then an atomic swap.

**First launch load:** skip the per-run sync and do one clustered full build at the end. Lexeme computation takes about 34 core-minutes at the measured rate, indexing about 42 minutes at the measured 5.8k docs/s, and the final merge a few minutes (estimate).

### 3.3 Search

1. **Parsing and scope.** `_expansions`, `_scope` and `_resolve_citation` stay in PostgreSQL, unchanged. For treaty scopes, the treaty instruments' jurisdictions also feed the doc-range pre-filter, while the exact filter stays the list of instrument ids. This is untested, because dev has no treaties.
2. **Citation candidates** are read from provision_versions, provisions and instruments with the same filter semantics. This replaces the passages join at ulis/retrieval/search.py:672-680.
3. **Strict stage.**
   - PostgreSQL turns the query into tsquery text: websearch_to_tsquery for each configuration that is indexed in scope, plus phraseto_tsquery for concept labels and the CJK form. This takes about 2.2 ms (measured).
   - The helper evaluates it operator by operator, with the `_filters()` predicates (SQL NULL semantics) as fast-field checks, inside the scope's doc ranges.
   - It returns the top 200 by BM25 (k1 1.2, b 0.75; field A 1.0, field B 0.4; collection-wide IDF).
4. **Relaxed stage.**
   - `_relaxable` is still parsed by PostgreSQL. Term frequencies come from `lexeme_stats` once step 8 has shipped.
   - Each level of the m-of-n ladder is one min-match query. It returns the list of matched terms, so `missing_terms` is exact.
   - Ranking is BM25 x share². The `MAX_LOOSE_COST` guard is kept unchanged.
5. **Re-check.** One batched SELECT by primary key re-applies status and validity at as_of to the candidates' versions. Any candidate that no longer passes is dropped. A lagging index can therefore miss a new hit, but it can never present a closed version as in force.
6. **Ordering.** `_classify`, grouping, `_order` and the limitations are unchanged. The final tie-break uses passage_id, which is now the provision version id.
7. **Hit rendering** is batched. Texts come from the blocks, each checked against its SHA-256.

### 3.4 Point-in-time lookup (`/v1/provisions`)

The lookup runs in PostgreSQL only:

1. Split the uri at the first '/~'.
2. Find the instrument through uq_instruments_uri.
3. Find the provision through uq(instrument_id, eid).
4. Find the versions through the uq_provision_versions_period prefix.
5. Run `resolve_at`.

Children come through the new partial index on parent_id. Today ulis/retrieval/lookup.py:118 seq-scans all of provisions for them, which takes 219-234 ms warm at dev (measured). The text comes from its block and is checked against its SHA-256. Validity, timelines and currency come from provision_versions and provision_version_sources (details.py:244-256), with the same meaning as today.

### 3.5 verify-sources

For each source document:

1. Read the raw bytes from the object store.
2. Re-parse them with the current parser, `PARSERS[parser_id]`. Parser versions are only logged in VerificationRecord details; old versions are not retained.
3. Build the attested set:
   - the self-attested versions: instrument_versions whose source_document_id is this document (through the new index), then that instrument's provisions (uq prefix), then the versions with that instrument_version_id (uq_period prefix);
   - plus the extra pairs.
4. Compare every derived text byte for byte, and by SHA-256, with the block-decoded stored text and with text_sha256. The locator is the stored value, or provisions.eid when it is NULL.

verify-sources never touches the search index.

### 3.6 Failures, backup and restore

| What happens | Behaviour |
|---|---|
| Index missing or corrupt | Word search answers 503 with `SEARCH_INDEX_UNAVAILABLE`. Citation queries, `/v1/provisions`, verify-sources and the assistant's id lookups keep working. Recovery: the previous generation (instant), or a snapshot plus journal replay, or a rebuild (about 1-1.5 h at launch on 2 vCPU, estimate) |
| Index behind (watermark below journal head) | Results carry `SEARCH_INDEX_LAGGING`, naming the pending instruments. The PostgreSQL re-check still removes closed versions, and /ready reports the lag |
| Index ahead of the database (payload above journal head, wrong restore order) | Detected at startup and treated as missing, then rebuilt |
| Snapshot watermark below the oldest retained journal seq | Forced rebuild |
| Helper crash | Restarted (opening took 5.3-5.5 ms, measured), and the request is retried once |
| Damaged text block | The frame checksum and the per-text SHA-256 refuse to serve it, and verify-sources lists it. The texts are re-derived from the object store with the current parser, and accepted only if verify-sources passes |
| Object store lost | Search and lookup are unaffected; verify-sources fails loudly until it is restored |
| Rust binary absent | The image build fails in CI |
| PostgreSQL down | Everything is down, as today |

**Nightly backup, in this order, all shipped off-site:**

1. An index snapshot: a hardlink copy of the immutable segment files plus meta.json.
2. `pg_dump --compress=zstd:9`, about 3-5 GB (estimate).
3. The object store, by `rsync --ignore-existing`.

Keep at most one dump on the server. Taking the index snapshot before the dump guarantees that a restored index is never newer than the restored database.

**Restore:**

1. pg_restore.
2. The objects.
3. The index snapshot.
4. `ulis index sync`, which replays the journal above the snapshot's watermark.
5. `ulis index check`, which must report 0 differences. It compares per-instrument document counts, keys and a lexeme sample against a fresh derivation.

## 4. Staged plan

Principles:

1. Each step ships alone. A step that changes behaviour sits behind a flag or a reversible migration.
2. Gates are measured in the KVM 2 emulation of step 0, not on the 4-CPU machine.
3. Nothing lexical is dropped until Tantivy has served production traffic and passed the judged gates.
4. Rewrites of the canonical tables (steps 9-10) are done and proven on dev before the launch load.

There are two tracks:

- **Track A, speed and RAM:** steps 0-8, then 11.
- **Track B, space:** steps 9-10. It can run in parallel once step 2 is done.

### Step 0. Baseline and KVM 2 emulation (0.5 engineer-week)

- **What.** Set up a cgroup with 2 CPUs and 8 GB: `systemd-run -p AllowedCPUs=0,1 -p MemoryMax=8G`, or `docker --cpus 2 --memory 8g`. Add a launch proxy: dev passages replicated about 3x under synthetic jurisdiction ids, so that one English scope has at least 5.4 M passages, the size GB will be. If the Hostinger box is available, also run fio there (8 KB random reads at QD1) and the 2-CPU throughput harness.
- **Record.**
  - measure.py: p50 170, p95 587, max 1.4 s; known@10 182/240; MRR 0.573;
  - 2-loop throughput: 9.48 q/s;
  - relation sizes;
  - the first cold search: 3,121 ms for US;
  - a full verify-sources run's checked counts.
- **Rollback.** Not applicable; this is measurement only.

### Step 1. Hot-path fixes, no change to existing tables (0.5 week)

- **What:**
  - The one-line `indexed_languages` fix in ulis/retrieval/coverage.py: return `{lang for jid in ids for lang in counts[jid].languages}`.
  - The strict stage ORs only the configurations indexed in scope, instead of all 17. Each arm is gated by `p.search_config` (search.py:298), so this cannot change results as long as coverage is current.
  - HISTORY_INCOMPLETE is cached per ingest epoch.
  - Coverage counts come from a new `coverage_summary` table, refreshed in the ingestion transaction.
  - `jit=off` for API sessions, plus the production planner settings.
  - `gin_clean_pending_list` at the end of every ingestion run (25 pending pages today).
- **Gate:**
  - results identical 240/240;
  - p95 599 → 398 ms and max 1,645 → 1,130 ms, measured for the language fix alone;
  - 2-loop throughput 9.48 → 11.22 q/s, measured;
  - 43% of strict GIN time was spent on configurations with no passage in scope (measured), and should disappear;
  - the first cold search 3.1 s → under 0.1 s (estimate).
- **Rollback.** Revert the commit and drop the summary table.

### Step 2. Index hygiene (0.3-0.5 week)

- **What:**
  - Drop ix_provision_versions_provision_id (110 MB) and ix_provisions_instrument_id (26 MB). Both are prefixes of unique indexes.
  - Make uq_passages_judgment_paragraph_id partial, WHERE NOT NULL; today it is 25 MB of NULLs.
  - REINDEX CONCURRENTLY the big btrees (455 MB of slack measured).
  - Add a partial index on provisions(parent_id) WHERE parent_id IS NOT NULL (about 65 MB at dev, estimate).
- **Gate:**
  - about -0.6 GB at dev;
  - measure.py 240/240 identical;
  - /v1/provisions latency on a sample of uris and dates, before and after (today 219-234 ms warm, from the children seq scan).
- **Rollback.** CREATE INDEX CONCURRENTLY the dropped indexes (minutes at dev), and drop the new one.

### Step 3. Stable passage ids (0.3 week)

- **What.** The API's passage_id becomes the provision_version_id, or the judgment_paragraph_id for judgments. This is 1:1 today (uq_passages_provision_version_id). The `_order` tie-break (search.py:650) and assistant/tools.py:329 switch to it, and the eval judgments are re-keyed. Announce it as an API change.
- **Gate.** On the 240 queries, the only differences are the order among exact ties; count them. known@10 stays 182/240, because it is keyed by version ids.
- **Rollback.** Revert the code; this only chooses which column is exposed.

### Step 4. Batched hit rendering (1-1.5 weeks)

- **What.** A `load_provisions(ids)` helper renders the whole page in about 10-15 statements, instead of about 120 per search. Timelines no longer load texts, and ancestors come from a recursive CTE.
- **Gate:**
  - byte-identical JSON on all 240 queries, including GB provisions with long timelines;
  - the `_provision_hit` stage, today 122.6 / 158.9 ms at p50/p95, measured in the emulation;
  - CPU per query on 2 pinned CPUs, today 178 ms with the language fix.
  - Expected: 40-100 ms less at p50 (estimate).
- **Rollback.** A flag back to the per-hit path.

### Step 5. Deterministic context and slim passages (1 week)

- **What:**
  - **Deterministic context.** `indexer._headings_by_provision_language` (ulis/retrieval/indexer.py:103) takes the first heading in arbitrary scan order, and 712 top-level provisions have more than one distinct heading per language. Fix the order first.
  - **Slim passages.** Add a `text_length` column. tsv becomes a plain column with STORAGE MAIN, computed in SQL by the indexer from the context and CJK strings passed as parameters (models.py:538-539 today). Drop text, context, search_text and search_context.
  - **Readers.** length(p.text) becomes p.text_length at search.py:307, 503 and 675. tantivy_engine.py:189-195 is adapted.
- **Gate:**
  - old and new tsv equal on every row, except the descendants of provisions whose context the determinism fix changed, which are listed and counted;
  - measure.py 240/240 identical, or every difference explained by those rows;
  - -987 MB at dev (measured on copies);
  - 'tax' in US scope 681-752 → 405-415 ms (measured);
  - broad Japanese strict queries 40-50% faster with STORAGE MAIN (measured).
- **Rollback.** passages is derived, so rebuild it with the previous code. At dev, a copy took 20-38 s to insert plus 73-97 s to build the GIN.

### Step 6. Tantivy in shadow (3.5-4 weeks)

- **What:**
  - **Production crate.** Promote the experiment code listed in section 3.1, build it in a multi-stage Docker image, and add CI parity suites: strict 240/240, the relaxed grid 434/434, the 1,181 filter requests and the 89 syntax probes.
  - **Index source.** The index is keyed by provision version, and its lexemes and context are computed from the canonical tables, not from passages.tsv.
  - **Maintenance.** The `search_index_journal` and `search_index_state` tables, `ulis index sync|rebuild|check|snapshot`, generation directories, the advisory lock, LISTEN/NOTIFY and `lexeme_stats`.
  - **Flag.** `ULIS_SEARCH_ENGINE=pg|shadow|tantivy`. Shadow mode runs both engines on a sample of real traffic and logs match-set differences, top-10 overlap and latency. Sample, rather than shadow everything, because shadow doubles the lexical work on 2 vCPU.
- **Gates:**
  - lexeme equality with passages.tsv on all 2.52 M rows, not only a 50k sample;
  - all parity suites on the end-state index;
  - a re-ingest of 26 CFR part 1 end to end (18.5 s in the experiment), with `index check` at 0 differences;
  - failure drills: kill during sync, replay after a crash, restore with an older snapshot, index ahead of the database, journal trimmed past the watermark, helper crash in the middle of a request;
  - helper RSS and page-cache residency in the KVM 2 cgroup with the launch proxy;
  - user-facing latency unchanged in shadow mode.
- **Rollback.** Set the flag to `pg` and delete the index directory. Journal rows are harmless.

### Step 7. Switch the lexical stage to Tantivy (1 week, plus review calendar time)

- **What.** Set the flag to `tantivy`. The slim PostgreSQL path stays live as the fallback, and relaxed term frequencies still come from pg_stats, so the relaxed term order is unchanged.
- **Release gates:**
  - **Quality:** known@10 at least 182/240 and MRR compared with 0.573 for the switched pipeline; pooled judgments per language (en, fr, de, es, sv, fi, ja, cy) at parity or better.
  - **Lawyer review:** the 18 queries the LLM judges rated worse, and short citation-like queries, which were never judged; '401(k)' has a top-10 overlap of 0.2.
  - **Point-in-time:** boundary dates (as_of = valid_from, valid_to and valid_to minus 1 day, each with every include_* flag) on the dated dev slices. Their closed versions: AU 25,946, GB 22,198, ES 18,988, FR 13,890, EU 3,875, JP 1,141, FI 638.
  - **KVM 2 cgroup:** search() p50/p95/max, 2-loop throughput and cold start. The relaxed stage should drop from 10.4 s to about 0.2 s over the 166 queries, as in the measured replay.
- **Rollback.** Set the flag back to `pg`. This is instant, because tsv is still maintained.

### Step 8. Relaxed term statistics from the index (0.5 week)

- **What.** `_lexeme_frequencies` (search.py:337-360) reads the exact doc_freq from `lexeme_stats` instead of the 1,000 most common elements in pg_stats.
- **Gate:**
  - how many of the 166 relaxed flows change, and the effect on known@10;
  - first, the drift of today's results across two fresh ANALYZE runs on a copy, to know how much "parity" pg_stats offers at all.
- **Rollback.** Read pg_stats again while tsv exists. After the drop, a frozen snapshot of the last pg_stats MCEs serves only as a temporary bridge, because it goes stale.

### Step 9. Lean canonical tables, as separately shipped sub-steps (1.5-2 weeks, track B)

- **9a. provisions.uri becomes derived.** lookup.py:46 splits the uri at the first '/~'. The API still returns uri. uq_provisions_uri (279 MB, never scanned by search) is dropped.
  - Gate: /v1/provisions responses byte-equal for a few thousand random uris and dates; lookup p50 before and after.
  - Rollback: add the column back, fill it with an UPDATE from the derivation (100% derivable), and rebuild the index.
- **9b. source_locator and source_document_id become NULL-means-derived**, on provision_versions and on the extra pairs. This changes details.py:332, integrity.py and pipeline.py.
  - Gate: details JSON identical on a sample; a full verify-sources pass.
  - Rollback: an UPDATE that materialises the values.
- **9c. provision_version_sources self rows become implied** (92% of rows). A view exposes the full set. details.py:244-256 and integrity.py:51-64 read it. Add an index on instrument_versions.source_document_id.
  - Gate:
    - a full verify-sources pass with the same checked_units and checked_events and an empty problems list;
    - details and search JSON identical;
    - verify-sources runtime per document.
  - Rollback: INSERT the self rows back from provision_versions.
- **9d. text_sha256 becomes bytea(32).** The API still shows hex.
  - Gate: API payloads identical.
  - Rollback: ALTER back to hex.

### Step 10. Text blocks (1.5-2 weeks, track B)

- **What:**
  - Create `text_blocks` and add the pointer to provision_versions.
  - A writer in the ingestion transaction, and a reader that checks the SHA-256 on every read.
  - Backfill in document order.
  - Switch every reader: details, hits, parallel versions, lookup, verify-sources, the assistant, export.
  - Then drop the text column. This is a table rewrite: about 1 GB transient at dev, about 5-6 GB at launch scale, so do it on dev before the launch load.
  - Add a repack job for garbage.
- **Gate:**
  - a full verify-sources pass;
  - byte-identical hit and lookup JSON;
  - text lookup 0.20 → 0.40 ms (measured) and the change in the hit stage;
  - dev text size 1.36 → about 0.32 GB (estimate from 6.04x);
  - the ratio under simulated incremental UK point-in-time updates, written in small batches;
  - non-US samples large enough to matter (FR was 41 versions in the measurement).
- **Rollback.** Before the drop, switch the readers back. After the drop, decode the blocks into a text column again; this is lossless and SHA-256 verified.

### Step 11. Drop passages after the soak (1-1.5 weeks)

- **When.** Only after Tantivy has served production for several weeks with the gates of steps 7-8 passed, `index check` clean and a full restore drill done.
- **What:**
  - `_citation_candidates` is rebuilt on the canonical tables; its gate is the 23 cited queries, with 24/24 citations found today.
  - ops/status.py and coverage.py switch to `coverage_summary`.
  - The indexer stops writing passages.
  - DROP passages, which removes tsv, the GIN and the passages btrees.
- **Gate:**
  - 240/240 results identical to step 8;
  - database size;
  - the first search after a restart;
  - dump size;
  - the full restore drill timed end to end: pg_restore, objects, index snapshot, journal replay.
- **Rollback.** passages is derived. Recreate it with rebuild_passages from the canonical tables; that code path stays in the repo for one release. At launch scale this takes roughly 0.5-1.5 h on 2 vCPU (estimate: about 34 core-minutes of lexemes plus about 7 minutes of GIN build plus the inserts).

### Step 12. Optional, each measured on its own

- **Integer surrogate keys** in the Tantivy fast fields. Measured on a dev variant: about 452 MB instead of about 550 MB for the same layout, so -44% against the 814 MB evaluation index. At launch that is about -0.6 GB of index and of hot RAM (estimate). It needs integer keys in PostgreSQL.
- **Repack text blocks** at 256 KB and level 19: 7.82x and 0.63 ms per lookup (measured).
- **PostgreSQL data, not the Tantivy files, on ZFS** with recordsize=32K and zstd: 12.3 → about 6.8 GB (model). Do this only after measuring, on the real host, the ARC decompression CPU on 2 vCPU and fsync on the actual vdev.
- **Decide between a_p/b_p and the `all` field** from the seam-phrase measurement (section 7). The `all` field costs about +0.9 GB at launch, for an index of about 3.8 GB.
- **Relax the `MAX_LOOSE_COST` guard** as a judged change: "any half of n" now costs at most 9.7 ms, and 44 of the 58 known-item misses are natural-language questions.

### Effort and contingency

**Effort (estimate, engineer-weeks):**

| Steps | Effort |
|---|---|
| 0 | 0.5 |
| 1 | 0.5 |
| 2 | 0.3-0.5 |
| 3 | 0.3 |
| 4 | 1-1.5 |
| 5 | 1 |
| 6 | 3.5-4 |
| 7 | 1 |
| 8 | 0.5 |
| 9 | 1.5-2 |
| 10 | 1.5-2 |
| 11 | 1-1.5 |
| **0-11 in total** | **about 12.5-15.5**, plus lawyer-review calendar time |
| 0-8 only (the speed and RAM win) | about 8.5-10 |

**Contingency if Tantivy slips past the launch load:** ship slim passages in two LIST partitions (US, and the rest), with SET LOCAL enable_seqscan=off around the lexical statements and jit=off. Measured at dev against public.passages with the same settings:

| Measure | Two partitions | public.passages |
|---|---|---|
| Lexical time, 240 queries | 8.7 s | 16.3 s |
| search() p50 / p95 / max | 129.2 / 233.1 / 821.9 ms | 145.5 / 381.5 / 1,146.6 ms |
| Unscoped lexical time, 40 queries | 3.6 s | 4.2 s (monolithic) |

Results are identical. In that case, launch on KVM 4, because PostgreSQL's lexical hot set at launch does not fit in 8 GB.

## 5. What was rejected and why

Three designs were scored by two independent judges, J1 and J2, from 0 to 10, higher is better. For effort, a higher score means less work.

| Design | Space (J1 / J2) | Performance | Quality and integrity | Operations and risk | Effort |
|---|---|---|---|---|---|
| PG-only: slim, jurisdiction-partitioned PostgreSQL on ZFS zstd | 5 / 5 | 4 / 4 | 8.5 / 8 | 5.5 / 5 | 6 / 6 |
| Slim PostgreSQL plus embedded Tantivy, no passages table | 8.5 / 8 | 8 / 8 | 6.5 / 7 | 6.5 / 5.5 | 4 / 3 |
| KVM2-fit: lean PostgreSQL plus zstd text blocks plus one clustered Tantivy index | 8.5 / 8 | 8 / 8 | 7 / 6.5 | 6 / 5 | 4 / 4 |

Rankings: J1 put KVM2-fit first, then Slim+Tantivy, then PG-only. J2 put Slim+Tantivy first, then KVM2-fit, then PG-only.

The chosen plan merges the two Tantivy designs. They share the same end state, and the judges ranked them first and second in opposite orders. Only PG-only is rejected as the launch architecture.

**PG-only: rejected as the launch architecture; kept as the fallback during the transition.**

- **Space.** At launch it is 32.95 GB raw, or 27.3 GB with text blocks (estimate). It reaches 15.4 / 13.8 GB only on ZFS-32K, and that was simulated, because there is no ZFS here. On a single-disk VPS, ZFS means a file-image vdev, which docs/deployment.md notes ZFS documents as meant for testing.
- **RAM.** Its own estimate of the hot set is about 17 GB raw, or about 7.9 GB compressed, against about 4.5-5 GB of cache on KVM 2. GB, FR and ES broad queries would go to disk.
- **Speed.** It is the best measured layout at dev: p50 132-140, p95 216-237, max 404-444 ms, results identical 240/240. But the gain comes from tiny non-US slices. GIN has no top-k, and partitioning cannot shrink GB's own corpus (about 5.4 M English passages in one partition, estimate). The launch projections are all estimates and disagree:

  | Projection | p50 | p95 | max |
  |---|---|---|---|
  | Its author, mixed load | 0.2-0.3 s | 1.5-2 s | about 5 s |
  | Judge's rerun of project2.py | 240-478 ms | 2.0-2.4 s | 4.2-4.9 s |
  | Gentler basis (US dev lexical times 2.95), GB scope, warm | about 120 ms | about 560 ms (p90) | about 790 ms |

  On any of these bases its lexical stage is one to two orders of magnitude slower than Tantivy.
- **Operations.** It has five traps, and each, when missed, was measured at x4 to x7 on some query class:
  - the seq-scan guard (fi-01: 70 → 528 ms);
  - JIT (1.68 s of compilation for 135 ms of execution);
  - a full GIN pending list (474 pages: p95 1.62 s against 0.35 s);
  - ANALYZE of the partitioned parent;
  - a partition for every new jurisdiction.

  Unscoped queries are 2x slower than monolithic: 8.1 s against 4.2 s over 40 queries with JIT off.
- **What it does best:** it has the best result fidelity (ranking unchanged), the fewest new components and the lowest effort (6-9 weeks).

**Not taken from the KVM2-fit design:**

- **Wrong claims:**
  - "Citation lookup unchanged". `_citation_candidates` reads passages (search.py:672-680), so dropping passages as written breaks citation search.
  - "Re-parse with the recorded parser version". integrity.py uses the current `PARSERS[parser_id]`.
  - Its list of provisions indexes. There is no parent or name index on provisions today; the name GIN indexes sit on instruments and instrument_versions.
- **Gaps:** no ordering of the index snapshot against pg_dump, so a restore could leave the index ahead of the database; and it never said what passage_id becomes.
- **Overstated:**
  - "About 2 GB of RAM margin"; it is about 1.3-2.1 GB at best.
  - 8-11 weeks of effort.
  - Successive versions sharing a block, which holds only for the backfill.
- **Sequencing:** it put the block store and the provenance rewrite on the critical path before the Tantivy switch. Here they are a parallel track.

**Not taken from the Slim+Tantivy design:**

- Hit batching as the last step. Here it is step 4: it is the largest remaining cost once Tantivy is in.
- Text blocks as the last step. Here they come before the launch load.
- Deriving source_locator and source_document_id outright. Here they are NULL-means-derived, so a future exception is stored rather than lost.
- The 1,000-MCE snapshot as a permanent fallback. Here it is only a temporary bridge, and the switch to doc_freq is its own measured step.

**Options measured and dropped along the way:**

| Option | Measured result | Why dropped |
|---|---|---|
| Composite GIN (jurisdiction_id, tsv) | lexical 26.0 s against 14.8 s; cy-03 728 → 2,154 ms | one GIN scan per in-scope value per config arm |
| Composite GIN (search_config, tsv) | 13.6 s against 14.8 s, +10 MB | neutral |
| Partitioning passages by search_config | lexical 21.2 s against 14.8 s | arms are not pruned one by one |
| 14 per-jurisdiction partitions | dev p95 216-237 ms, but unscoped 8.1 s (JIT off) or 21.9 s (JIT on) against 4.2 s | planner traps; GB unchanged |
| lz4 TOAST, toast_tuple_target=128 | lz4 72.04 MB against pglz 71.55 MB; slim table -0.6% | rows under 2 KB are never compressed |
| Tantivy TermSet pre-filter | strict p50 2.89 → 6.33 ms | builds a bitset per segment and config |
| Tantivy built-in minimum_should_match | "half" level p95 7.76 ms against 2.12 ms with min-match | heap merge without skipping |
| Index without positions | 180 MB against 417 MB for the `all` field | breaks phrase queries |
| One zstd frame per instrument | 9.6-10.3x against 6.76x for 64 KB blocks | slower random access; 256 KB blocks are the later option |
| Exact text deduplication | saves 3.2% | not worth a new indirection |
| Containers rebuilt from their children | matched a plain join in 0 of 1,397 US containers | kept as data |
| Tantivy files on ZFS | x1.18 | mmap'd pages cached twice |

## 6. Ideas grafted from the runners-up

**From Slim+Tantivy:**

- The transactional `search_index_journal` outbox, the commit-payload watermark, and `ulis index sync|check|rebuild|snapshot`.
- Snapshot before pg_dump, and detection at startup of an index that is ahead of the database.
- The `SEARCH_INDEX_LAGGING` and `SEARCH_INDEX_UNAVAILABLE` limitations, so citations, `/v1/provisions` and verify-sources keep working while the index is down.
- passage_id = provision_version_id (step 3), which keeps eval judgments stable and fixes the `_order` tie-break.
- `_citation_candidates`, assistant/tools.py:329 and ops/status.py rewritten on the canonical tables before passages is dropped.
- Its check of the bytes model's double count: model D3's 18.70 GB already includes the 4.14 GB index.

**From KVM2-fit:**

- Step 0's KVM 2 cgroup emulation with a launch-proxy index, as the place where every gate is measured.
- Batched hit rendering early, gated on byte-identical JSON.
- Text blocks before the launch load, with the per-text SHA-256 stored outside the block and checked on every read.
- NULL-means-derived provenance columns.
- The point-in-time boundary-date gate.
- The PostgreSQL re-check of candidates' status and validity.
- `all` kept as a ready fallback layout.

**From PG-only:**

- The slim PostgreSQL lexical path as the live fallback through shadow mode and the soak. That path means text_length, tsv STORAGE MAIN, strict arms limited to the configurations in scope, SET LOCAL enable_seqscan=off, jit=off, and gin_clean_pending_list after every run.
- The two-partition (US, rest) layout as the interim if Tantivy slips.
- Treaty instruments' jurisdictions used for pruning.
- A ZFS or LVM snapshot before each ingestion run, if the host allows a real partition.

**From the judges' own code checks:**

- The partial provisions(parent_id) index for lookup.py:118.
- The instrument_versions(source_document_id) index for the rewritten verify-sources. Today it filters provision_version_sources by an unindexed source_document_id, which would mean about 4.3 GB of scans per pass at launch.
- Deterministic context derivation before any rebuild from the canonical tables: 712 top-level provisions have more than one heading per language.
- A forced rebuild when the snapshot watermark is below the oldest retained journal seq.

## 7. Risks and what must still be measured

**Risks:**

1. **Ranking change.** BM25 replaces ts_rank_cd.
   - The evidence so far: LLM judges gave nDCG@10 +0.109, 95% CI [+0.066, +0.154], with the gain in boolean, en and ja queries. 18 queries got worse.
   - Top 10s share only about 4 of 10 results with today's.
   - Short citation-like queries were never judged, and known@10 of the switched pipeline is not measured.
   - Mitigation: the step 7 gates and lawyer review, and no drop of tsv before the soak ends.
2. **No lexical fallback after step 11.** Losing the index means no word search until a snapshot restore or a rebuild (about 1-1.5 h at launch on 2 vCPU, estimate). Mitigation: off-site snapshots and the previous generation kept on disk.
3. **Two stores must agree.** The journal, watermark and snapshot-order protocol is untested end to end. Mitigation: the step 6 failure drills, `index check`, and the PostgreSQL re-check of hits.
4. **Experiment code becomes production code.**
   - The min-match scorer, the range pre-filter and the column cache depend on tantivy-columnar internals of 0.26.2. Pin the version and rerun the parity suites on every upgrade.
   - 0.26.2 has no index sorting, so clustering depends on feed order and periodic rebuilds.
5. **Seam phrases.** A phrase that runs from the context into the text matches in PostgreSQL but not in a_p/b_p. None occurred in the 240 queries or the relaxed forms, but this was not searched exhaustively.
6. **The relaxed term order changes** with doc_freq for terms outside the top 1,000 (step 8 measures it).
7. **GB is 37% of the launch set** and gains little from the pre-filter: tens of ms worst case (estimate). Unscoped and treaty queries get no pre-filter: about 0.1-0.25 s (estimate). The treaty pruning is untested.
8. **Fragmentation.** A 3% delete share cost +5.3% size and +10% latency until a merge, and clustering degrades with incremental segments. Merges and rebuilds must be scheduled off-peak on 2 vCPU.
9. **The RAM margin is thin.** It is about 1.3-2.1 GB, and about 0 if launch counts come in 50% higher. The ±50% on launch counts applies to every total. The term-dictionary growth (x3) is assumed.
10. **KVM 2 is below the documented minimum** of 4 vCPU and 16 GB. Hostinger vCPU speed and NVMe latency are unknown (possibly 2-4x the measured 0.115 ms per miss).
11. **Text-block compression.** Later incremental writes land in new blocks and compress closer to per-value ratios (2.22-2.33x measured without a dictionary) until a repack. The 6.04x basis is 86% US bytes, and per-jurisdiction ratios measured 4.2-9.4x, so 1.37 GB at launch is roughly ±0.4 GB.
12. **Repair uses the current parser.** A damaged block is repaired with today's parser, so a parser change shows up as a verify-sources failure, never as silent drift.
13. **Untested filters.** Judgments, treaties and regional organisations have no real rows in dev, so their filters are covered only by the randomized filter test.
14. **API change.** Clients holding old passage ids break once, at step 3.

**What must be measured next, in priority order:**

1. **The end-state Tantivy index**, built from the canonical tables only:
   - layout: clustered, a_p/b_p, keyed by version, context re-derived and CJK through segment_for_search;
   - equality with passages.tsv on all 2.52 M rows;
   - all parity suites on that index.
2. **End-to-end search() through Tantivy inside the API:** p50/p95/max, known@10 against 182/240, MRR against 0.573, judged per-language parity, and lawyer review of the 18 regressions and the short citation queries.
3. **The KVM 2 emulation** with a GB-sized proxy (at least 5.4 M English passages in one jurisdiction): latency, page-cache residency, RSS and 2-loop throughput, including while an incremental ingest and a forced merge are running.
4. **The real Hostinger KVM 2:** fio random 8 KB reads at QD1 and the 2-CPU throughput harness.
5. **Batched hit rendering:** ms saved and CPU per query, with byte-identical JSON on all 240 queries, including GB-like timelines.
6. **Full verify-sources and byte-equal `/v1/provisions` and details payloads** before and after each canonical change (steps 9-10), and verify-sources runtime per document with the new indexes.
7. **Text blocks under realistic ingest:** the ratio after simulated UK point-in-time updates in small batches, repack cost, cold lookup latency, and non-US samples of useful size.
8. **The incidence of context-to-text seam phrases**, exhaustive over corpus tokens or a large query log, to decide between a_p/b_p and `all`.
9. **Relaxed term order:** doc_freq against pg_stats MCEs, and the baseline drift across two fresh ANALYZE runs.
10. **Dual-store failure drills**, each ending at `index check` = 0 or a forced rebuild.
11. **At launch scale on 2 vCPU:** the Tantivy full-rebuild time and peak disk, and the real term-dictionary growth.
12. **Launch-count inputs that dominate every total:** UK sources per version from more than the 51-work slice, and lexeme volume per GB passage.
13. **Only if ZFS is reconsidered:** the real compressratio, ARC decompression CPU on 2 vCPU, and fsync and bulk-load throughput on the actual vdev, on Hostinger itself.

## 8. Raw evidence

These scratch files are outside the repository and may be cleaned up with the session.

**Harness and baseline:**
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/fix/measure.py
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/fix/final4.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/eval/queries-en.json (and queries-cy, -de, -es, -fi, -fr, -ja, -sv .json in the same directory)
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/eval/index/pg
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/eval/index/native

**Bytes, compression and the size model:**
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/relsizes.csv
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/idxsizes.csv
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/colstats_passages.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/colstats_provision_versions.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/colstats_provisions.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/colstats_provision_version_sources.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/pages_passages.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/pages_provision_versions.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/dup1.out
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/prov.out
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/context.out
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/containers2.out
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/tsvparts.out
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/freshidx.out
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/idxstats.out
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/idxcount.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/toast_exp2.out
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/zstd_test.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/blockstore_64k_z9.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/blockstore_16k_z9.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/blockstore_256k_z19.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/fsratio.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/fsratio_exp.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/fsratio_tantivy.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/perjur.csv
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/model.py
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/model.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/bytes/model_fs.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/kvm2fit/budget.py (end-state subtotal 12.26 GB, RAM budget, load peaks)

**Profile: stages, cold cache, working set, throughput:**
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/warm/report.txt
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/warm_fixlangs/report.txt
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/warm_fixlangs/scale.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/warm/explain_slow.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/warm/explain_summary.txt
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/warm/explain_all.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/warm/hits.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/warm/hits.cprofile.txt
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/workset/workset.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/workset/workset_scale.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/cold/cold.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/cold/report.txt
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/disktest.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/tp/p2t1.summary.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/tp/p2t1_fix.summary.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/tp/p4t1.summary.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/tp/p1t2.summary.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/profile/launch_latency.py

**PostgreSQL structures (all runs compared against final4.jsonl):**
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/run.py
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/compare.py
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/lexsums.txt
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/base_public_prod_fix.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/d1_slim_prod_fix.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/e1b_prod_noseq_fix_statio.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/bd_prod_noseq_fix.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/bd_prod_noseq_fix_nojit.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/b2_prod_noseq_fix_nojit.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/b2_nojur_nojit.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/bd_nojur.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/bd_nojur_nojit.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/e1b_nojur.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/c1_jur_tsv.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/a1_partcfg_prod_noseq_fix.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/e2_pending_full.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/e2_pending_clean.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/sql/bd.sql
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/sql/b2.sql
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/sql/d_slim.sql
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgstruct/sql/fastupdate.sql
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgonly/project.py
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/pgonly/project2.py

**Tantivy:**
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/crate/src/minmatch.rs
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/crate/src/ranges.rs
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/crate/src/search.rs
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/crate/src/index.rs
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/crate/src/experiments.rs
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/crate/target/release/ulis-tantivy
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/capture.py
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/replay_strict.py
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/replay_relaxed.py
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/out/strict_prebuilt.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/out/strict_c_ordm_pf.jsonl
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/out/rel_prebuilt.summary.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/out/rel_c_ordm_pf.summary.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/out/analysis_prebuilt_c_prebuilt_c_ordm_c_ordm_pf.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/out/sizes_prebuilt.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/out/sizes_ordmerged.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/out/layout_ab.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/out/mem_ordm_pf.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/out/mem_prebuilt.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/out/reingest.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/out/merge_ordplus.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/out/build_ord.json
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/out/tsv_cost.out
- /tmp/claude-0/-home-user/04758135-3132-53ed-b30d-03cafc8740ef/scratchpad/arch/tantivy/idx/ord_plus: the clustered index, merged, re-ingested and re-merged once. It takes 709 MB on disk and is kept for the step 6 work.

**Repository documents (unchanged by this work):**
- docs/search-engine-evaluation.md: Tantivy pg-mode parity, the BM25 judgments, the 89 syntax probes and the 1,181 filter requests.
- docs/footprint-research.md: the space tiers, and the roughly 4-of-10 top-10 overlap with BM25.
- docs/go-live.md: launch sources and counts, the size table, and the 4 vCPU / 16 GB minimum.
- docs/deployment.md, section 13: the object store x5.70, dumps, and the ZFS and btrfs notes, including file vdevs.
- Code locations cited: in ulis/retrieval/, search.py (lines 298, 307, 337-360, 503, 650, 672-680), coverage.py, lookup.py (lines 46, 118), details.py (lines 244-256, 332), indexer.py (line 103) and tantivy_engine.py (lines 189-195); ulis/verification/integrity.py (lines 51-64); ulis/ingestion/pipeline.py (lines 556-565, 563); ulis/assistant/tools.py (line 329); ulis/ops/status.py; ulis/db/models.py (lines 538-539).