# How small can ULIS get without losing quality? Research and benchmarks (2026-10-18)

Question from the owner: reduce the data and the whole project to a negligible weight, without affecting quality.
"Quality" is held to five tests: (1) verbatim text returned byte for byte; (2) every stored text re-derivable from the
publisher's original bytes; (3) search with the same or better recall and ranking, multilingual including Japanese,
sub-second; (4) point-in-time answers; (5) exact citations and provenance. Lossy techniques (summaries, embeddings as a
replacement, quantised text) were excluded from the start.

Method: five research tracks (compressed search engines; serverless and static designs; search on compressed text;
version deduplication; codecs and the entropy floor), three benchmarks on the fully loaded US eCFR (8,124 parts, 1.94
million provision versions, 1.81 million passages; database 6.1 GB), a synthesis, and three skeptical reviews that
corrected the synthesis. Numbers below are the corrected ones. Only the eCFR was measured in full; launch-set figures are
scaled per market and carry the go-live guide's plus or minus 50 percent.

## Answer

| Stage | Launch-set footprint | Status |
|---|---|---|
| Original design | ~130 GB | - |
| Lossless quick wins: zstd object store, `prune-cache`, ZFS/btrfs zstd under PostgreSQL, zstd dumps | **~30-35 GB** | **done** (`docs/deployment.md` section 13) |
| Medium tier: search in an embedded Tantivy index, one compressed copy of each text, compressed metadata | ~15-22 GB | researched and benchmarked, not built |
| Physical floor (estimate) | ~10-12 GB | - |

"Negligible" is not reachable for a lossless, searchable, verifiable corpus: about one order of magnitude below the
original design, not two. The floor is set by three things no codec removes: the information in the text itself (legal
text compresses to about 10-12% of its raw size with the best mature codecs, 14-17% when random access is needed), the
positional search index that phrase search needs (0.25-0.30x of the indexed text, about 0.9x for Japanese character
pairs), and publisher files that arrive already zipped (French LEGI, German `xml.zip`, US Code zips), which cannot shrink
while their SHA-256 must still verify.

## Benchmarks on the eCFR (text and search tables)

| Layout | Size | vs PostgreSQL | Warm search latency | Exactness |
|---|---|---|---|---|
| PostgreSQL today: `passages` + `provision_versions` | 4.99 GB | 1x | 3-39 ms | reference |
| Parquet with zstd, text tables only | 0.46-0.54 GB | ~10x | no usable search (DuckDB FTS 0.5-2.1 s, no phrases, no CJK) | 3.75 M texts, SHA-256 identical |
| SQLite FTS5 + per-row zstd with a trained dictionary | 1.26 GB | ~4x | 4.6-20 ms | 0 mismatches; finds 99.7% of PostgreSQL's matches |
| Tantivy, `passages` only (search structures 375 MiB vs PostgreSQL's 1,454 MiB) | 0.59-0.91 GB | 3.3-5.1x | 0.25-2.5 ms | every stored text byte-exact |

Two facts drive the savings. `passages.text` is byte-identical to `provision_versions.text` for all 1.81 million
passages, so the search table can point at one text store. And 99.9% of paragraph rows are under PostgreSQL's 2 KB
compression threshold, so they are stored uncompressed; block compression outside PostgreSQL fixes that.

Version deduplication (measured on 73 versions of Regulation Z): storing each provision's versions in one compressed
frame is 41-58x smaller than storing every version, against 5.5x for per-value compression; raw snapshots as zstd
`--patch-from` delta chains took 277 MB of snapshots to 620 KB, every version rebuilt byte for byte. This matters for
the UK, France, Spain and Japan once their histories are loaded.

## Recommended path beyond the quick wins (medium tier)

1. **Search:** an embedded Tantivy index (MIT licence, no server), postings with positions, built per release, holding an
   integer key per passage; analyzers copying PostgreSQL's (its stopword lists, Snowball stemmers for en, fr, de, es, sv,
   fi; Welsh unstemmed as today); Japanese indexed from ULIS's own `segment_for_search()` output with a plain tokenizer
   (Tantivy's n-gram tokenizer sets every position to 0 and would break phrase search). Pin the tested version (0.26.2).
2. **Text:** one canonical copy of each provision-version text in zstd blocks (Parquet or SQLite), with a binary 32-byte
   SHA-256 per text kept outside the block so a wrong pointer can never return the wrong text with a "correct" hash.
3. **Metadata and history:** PostgreSQL keeps versions, validity, provenance and verification records (ingestion is
   order-dependent and stateful), without the text bytes and without the search copy; version UUIDs stay, because the API
   and verification records use them.
4. **Raw sources:** the zstd object store as now; publisher originals (bulk archives, title files) copied to cold storage
   before `prune-cache` if they are to be kept, as delta chains once histories accumulate.

Effort: about 4-8 engineer-weeks. Gain: about 1.5-2x on top of today's 30-35 GB.

## What must be true before switching search (quality gates)

* **Ranking:** BM25 shares on average only 4 of its top 10 results with today's `ts_rank_cd`. Either prove parity or better
  with relevance judgments (at least 50 queries per language: en, fr, de, es, sv, fi, ja, cy; pooled top 20 from both
  engines), or build a scorer that reproduces `ts_rank_cd`. No judgment set exists yet (`eval/questions.yaml` has 17
  end-to-end assistant questions with expected citations, none graded for ranking).
* **Query syntax:** a parser compatible with `websearch_to_tsquery` (quoted phrases, `or` in any case, `-term`), never
  Tantivy's own query parser: it fails on "401(k)" and "6(1)(f)" and treats "or" as a word.
* **Filters:** every predicate of today's search as an index field (kind, language, doc_type, extent with its empty case,
  instrument and judgment ids, validity with NULL semantics), checked by an equivalence test.
* **Point-in-time search:** match sets compared on a dated corpus (UK, France, Japan histories) at boundary dates. The eCFR
  benchmark could not test this: every passage was in force with no start date.
* **Collection statistics:** one index per release (jurisdiction as a filter) or global statistics, because searches span
  several jurisdictions (Germany plus the EU, Wales plus the UK).
* **Verification:** record the parser version on each stored document and keep old parsers runnable; change block rules
  only for new versions.

## Rejected, with the reason

Meilisearch (10-25x the raw text on disk, no stemming); Typesense (RAM 2-3x the indexed fields); PGroonga (index
1.2-1.7x the raw text on top of the table); Manticore (Japanese as single characters); Quickwit on object storage (built
for logs, English-only stemming, a cluster to run); Elasticsearch/OpenSearch clusters (JVM cluster weight; embedded Lucene
10, measured at 617.5 MB, stays the fallback if a JVM is acceptable); ParadeDB `pg_search` (only about 1.5x smaller,
AGPL); `pg_textsearch` (no phrase search); DuckDB full-text search (0.5-2.1 s queries, no phrases, no CJK); SQLite FTS5 as
the main engine (bigger index than Tantivy, no stemmers for fr/de/es/sv/fi, about 0.6 s for frequent terms at eCFR scale);
SQLite or Pagefind served over HTTP range requests (66-314 sequential requests per query, 1.3 to over 20 s); Cloudflare D1
(10 GB per database); `sqlite-zstd` (its authors' own "use at your own risk"); FM-index, r-index and other compressed
self-indexes (research-grade, 2.3-3x larger than zstd-19); context-mixing and LLM compressors (about 1 KB/s and 200 B/s,
verification tied to exact model weights, only 1.3-2x better than bzip3/zstd); dropping positions (breaks phrase search);
versioned databases (Dolt, TerminusDB, XTDB: a full copy per change); backup deduplicators as the history store (8-37x
worse than delta chains).

## Open questions for the owner

* Is BM25 ranking acceptable if relevance judgments show parity or better, or must ranking match today's exactly?
* Is a JVM acceptable (embedded Lucene) or must the stack stay Python/Rust (Tantivy)?
* How deep are the real version histories per market? They decide whether history costs +20% or several times one snapshot.
* Should the publishers' whole original files be kept (cold storage, a few GB) after `prune-cache`?

Benchmark scripts and raw results: the session scratchpad (`bench-parquet-duckdb`, `bench-tantivy`, `bench-sqlite-fts5`);
the measured object-store and dump figures are in `docs/deployment.md` section 13.
