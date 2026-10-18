"""Build the EXPERIMENTAL Tantivy indexes (mode pg and/or native) from a ULIS database.

    cargo build --release --manifest-path scripts/search_eval/tantivy/Cargo.toml
    python scripts/search_eval/build_index.py --db postgresql+psycopg://ulis:ulis@localhost:5432/ulis \
        --index-root /path/to/indexes --mode both [--jurisdiction US] [--fresh]

Writes <index-root>/pg and <index-root>/native. Passages are streamed in id order in batches and
committed every --commit-every rows; re-running the same command resumes after the last commit
(and, on a complete index, appends passages with a higher id). --fresh deletes the index first.
Prints build time, index size (with the per-field breakdown) and the size of PostgreSQL's
passages table for comparison; the summary is also written to <index-root>/build-<mode>.json.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text

from ulis.db import make_engine, make_session_factory
from ulis.retrieval import tantivy_engine as te


def pg_sizes(session: Any) -> dict[str, Any]:
    row = session.execute(
        text(
            "SELECT pg_total_relation_size('passages'), pg_relation_size('passages'),"
            " pg_indexes_size('passages'), pg_relation_size('ix_passages_tsv'),"
            " pg_total_relation_size('passages') - pg_relation_size('passages') - pg_indexes_size('passages'),"
            " (SELECT count(*) FROM passages)"
        )
    ).one()
    return {
        "passages_total_bytes": row[0],
        "passages_heap_bytes": row[1],
        "passages_indexes_bytes": row[2],
        "gin_tsv_index_bytes": row[3],
        "passages_toast_fsm_vm_bytes": row[4],
        "passages_rows": row[5],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--index-root", type=Path, required=True)
    ap.add_argument("--mode", choices=["pg", "native", "both"], default="both")
    ap.add_argument("--jurisdiction", action="append", default=[], help="restrict to these jurisdiction ids")
    ap.add_argument("--fresh", action="store_true", help="delete existing indexes first")
    ap.add_argument("--no-merge", action="store_true", help="keep several segments (faster build, larger index)")
    ap.add_argument("--commit-every", type=int, default=100_000)
    ap.add_argument("--heap-mb", type=int, default=1000)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--batch", type=int, default=5000)
    ap.add_argument("--tsv-size", action="store_true", help="also sum pg_column_size(tsv) (slow on large tables)")
    args = ap.parse_args(argv)

    sessions = make_session_factory(make_engine(args.db))
    modes = ["pg", "native"] if args.mode == "both" else [args.mode]
    opts = te.BuildOptions(
        commit_every=args.commit_every,
        heap_mb=args.heap_mb,
        threads=args.threads,
        merge=not args.no_merge,
        progress=True,
        batch=args.batch,
    )
    if args.jurisdiction:
        opts.where = "p.jurisdiction_id = ANY(CAST(:build_jurisdictions AS text[]))"
        opts.params = {"build_jurisdictions": args.jurisdiction}
    args.index_root.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {}
    with sessions() as session:
        summary["postgresql"] = pg_sizes(session)
        if args.tsv_size:
            summary["postgresql"]["tsv_column_bytes"] = session.execute(
                text("SELECT sum(pg_column_size(tsv)) FROM passages")
            ).scalar()
    for mode in modes:
        index_dir = args.index_root / mode
        if args.fresh and index_dir.exists():
            shutil.rmtree(index_dir)
        t0 = time.perf_counter()
        with sessions() as session:
            result = te.build_index(session, index_dir, mode, opts)
        result["wall_seconds"] = round(time.perf_counter() - t0, 1)
        info = te.index_info(index_dir) or {}
        result["index_bytes"] = info.get("bytes")
        result["per_field_bytes"] = {
            name: sum(parts.values()) for name, parts in sorted((info.get("per_field") or {}).items())
        }
        result["per_field_parts"] = info.get("per_field")
        result["jurisdictions"] = args.jurisdiction or "all"
        summary[mode] = result
        (args.index_root / f"build-{mode}.json").write_text(json.dumps(result, indent=2))
        print(
            f"{mode}: {result['docs_total']} docs, {result['index_bytes'] / 1e6:.1f} MB, "
            f"{result['wall_seconds']} s (resumed after {result['resumed_after']})",
            flush=True,
        )
    pgs = summary["postgresql"]
    print(
        f"postgresql passages: total {pgs['passages_total_bytes'] / 1e6:.1f} MB "
        f"(heap {pgs['passages_heap_bytes'] / 1e6:.1f}, indexes {pgs['passages_indexes_bytes'] / 1e6:.1f}, "
        f"GIN tsv {pgs['gin_tsv_index_bytes'] / 1e6:.1f}, toast {pgs['passages_toast_fsm_vm_bytes'] / 1e6:.1f})"
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
