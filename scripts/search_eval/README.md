# EXPERIMENTAL Tantivy candidate stage (evaluation only)

Production search stays in PostgreSQL. These tools build a Tantivy index next to it and run the
real `ulis.retrieval.search.search()` with the lexical stage (`_candidates`) swapped, so both
engines can be compared end to end. Design and semantics: `ulis/retrieval/tantivy_engine.py`
(module docstring) and `tantivy/src/*.rs`.

```sh
# 1. the Rust helper (tantivy 0.26.2, pinned in Cargo.toml / Cargo.lock)
cargo build --release --manifest-path scripts/search_eval/tantivy/Cargo.toml
#    -> scripts/search_eval/tantivy/target/release/ulis-tantivy  (or set ULIS_TANTIVY_BIN)

# 2. indexes: <root>/pg (PostgreSQL lexemes) and <root>/native (no PostgreSQL); resumable
python scripts/search_eval/build_index.py --db postgresql+psycopg://ulis:ulis@localhost:5432/ulis \
    --index-root /path/to/idx --mode both [--jurisdiction US] [--fresh]

# 3. the real search() on one engine (pg | tantivy-pg | tantivy-native | all)
python scripts/search_eval/run_search.py --db URL --index-root /path/to/idx --engine all \
    --q '401(k) hardship distribution' --jurisdiction US

# measurements
python scripts/search_eval/query_tool.py      --db URL --index-root /path/to/idx --cold   # latency, overlap
python scripts/search_eval/equivalence.py     --db URL --index-root /path/to/idx --mode pg  # candidate sets
python scripts/search_eval/ranking_eval.py    --db URL --index-root /path/to/idx            # known-item ranking
python scripts/search_eval/analyzer_parity.py --db URL                                      # native vs PostgreSQL analysis
```

Tests: `tests/test_tantivy_engine.py` (skipped when the binary is not built); Rust unit tests:
`cargo test --release --manifest-path scripts/search_eval/tantivy/Cargo.toml`.
