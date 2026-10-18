"""Timings and agreement of the candidate stages (PostgreSQL vs EXPERIMENTAL Tantivy pg/native).

    python scripts/search_eval/query_tool.py --db URL --index-root DIR [--jurisdiction US] \
        [--queries FILE] [--warm 5] [--cold] [--out results.jsonl]

For every query and engine:
  * lexical stage alone (``_candidates`` or the Tantivy replacement, budget 200): cold time (with
    --cold: the index files, or PostgreSQL's passages heap/TOAST/index files, are evicted from the
    OS page cache first and the Tantivy process is restarted; PostgreSQL's shared_buffers cannot be
    evicted without a restart and stay warm), then the median of --warm runs;
  * the real ``search()`` end to end (warm median);
  * agreement with PostgreSQL: overlap of the 200 candidates and of the final top 10.
Prints one JSON line per query and a summary (medians, p95).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text

from ulis.catalog import Catalog
from ulis.db import make_engine, make_session_factory
from ulis.retrieval import tantivy_engine as te
from ulis.retrieval.search import SearchRequest, _candidates, _expansions, _scope, search
from ulis.settings import REPO_ROOT

QUERIES = [
    "hardship distribution 401(k) plan",
    "6(1)(f)",
    "42 U.S.C. § 1983",
    '"Federal Register" publication',
    "privacy impact assessment information in identifiable form",
    "commodity pool operator exemption from registration",
    "electronic fund transfers unauthorized consumer liability",
    "hazardous waste manifest -radioactive",
    "medical device premarket notification 510(k)",
    "small business set-aside or sole source",
    "airworthiness directive compliance",
    '"gross income" definition',
    "occupational exposure limit benzene",
    "section 501(c)(3) organization",
    "12 CFR 1026.19(e)",
    "self-employment tax",
    "endangered species critical habitat designation",
    '"equal employment opportunity" complaint',
    "Medicare Part D prescription drug",
    "the",
]
ENGINES = ("pg", "tantivy-pg", "tantivy-native")
TODAY = date(2026, 10, 18)


def evict(paths: list[Path]) -> None:
    """Drops clean pages of these files from the OS page cache (posix_fadvise DONTNEED)."""
    for p in paths:
        try:
            fd = os.open(p, os.O_RDONLY)
        except OSError:
            continue
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)


def index_files(index_dir: Path) -> list[Path]:
    return [p for p in index_dir.iterdir() if p.is_file()]


def pg_files(session: Any) -> list[Path]:
    datadir = Path(session.execute(text("SHOW data_directory")).scalar())
    rels = session.execute(
        text(
            "SELECT pg_relation_filepath(c.oid) FROM pg_class c WHERE c.oid IN ("
            " SELECT 'passages'::regclass UNION SELECT reltoastrelid FROM pg_class WHERE relname = 'passages'"
            " UNION SELECT indexrelid FROM pg_index WHERE indrelid = 'passages'::regclass)"
        )
    ).scalars()
    files = []
    for rel in rels:
        base = datadir / rel
        files += [p for p in base.parent.glob(base.name + "*")] if base.parent.exists() else []
    return files


def ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--index-root", type=Path, required=True)
    ap.add_argument("--jurisdiction", default="US")
    ap.add_argument("--queries", type=Path)
    ap.add_argument("--warm", type=int, default=5)
    ap.add_argument("--cold", action="store_true")
    ap.add_argument("--budget", type=int, default=200)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    queries = [q.strip() for q in args.queries.read_text().splitlines() if q.strip()] if args.queries else list(QUERIES)
    catalog = Catalog.load(REPO_ROOT / "config")
    sessions = make_session_factory(make_engine(args.db))
    out = args.out.open("w") if args.out else None
    rows = []
    with sessions() as session:
        pgf = pg_files(session) if args.cold else []
        procs: dict[str, te.TantivyCandidates] = {}

        def stage(engine: str, restart: bool = False) -> te.TantivyCandidates:
            mode = engine.removeprefix("tantivy-")
            if restart and engine in procs:
                procs.pop(engine).process.close()
            if engine not in procs:
                procs[engine] = te.TantivyCandidates(te.TantivyProcess(args.index_root / mode), mode)
            return procs[engine]

        for q in queries:
            req = SearchRequest(q=q, jurisdiction=args.jurisdiction or None, limit=10)
            as_of = TODAY
            scope = _scope(session, catalog, req.jurisdiction, as_of)
            expansions = _expansions(session, q, None)[1]
            rec: dict[str, Any] = {"q": q}
            cand_ids: dict[str, list[str]] = {}
            top_ids: dict[str, list[str]] = {}
            for engine in ENGINES:
                fn: Any
                if engine == "pg":
                    fn = _candidates
                    if args.cold:
                        evict(pgf)
                else:
                    fn = stage(engine, restart=args.cold)
                    if args.cold:
                        evict(index_files(args.index_root / engine.removeprefix("tantivy-")))
                t0 = time.perf_counter()
                cands = fn(session, req, as_of, scope, expansions, args.budget)
                cold = ms(t0)
                warm = []
                for _ in range(args.warm):
                    t0 = time.perf_counter()
                    fn(session, req, as_of, scope, expansions, args.budget)
                    warm.append(ms(t0))
                cand_ids[engine] = [str(c.passage_id) for c in cands]
                with te.use_engine(None if engine == "pg" else fn):
                    full = []
                    result: dict[str, Any] = {}
                    for _ in range(max(1, args.warm // 2)):
                        t0 = time.perf_counter()
                        result = search(session, catalog, req, today=TODAY, candidate_budget=args.budget)
                        full.append(ms(t0))
                top_ids[engine] = [r["passage_id"] for r in result["results"]]
                rec[engine] = {
                    "candidates": len(cands),
                    "matched": fn.last.get("matched") if engine != "pg" else None,
                    "lexical_cold_ms": cold,
                    "lexical_warm_ms": statistics.median(warm) if warm else None,
                    "search_warm_ms": statistics.median(full),
                }
            for engine in ENGINES[1:]:
                a, b = set(cand_ids["pg"]), set(cand_ids[engine])
                ta, tb = set(top_ids["pg"]), set(top_ids[engine])
                rec[engine]["candidate_overlap"] = round(len(a & b) / len(a | b), 3) if a | b else 1.0
                rec[engine]["top10_overlap"] = round(len(ta & tb) / len(ta | tb), 3) if ta | tb else 1.0
            rows.append(rec)
            line = json.dumps(rec, ensure_ascii=False)
            print(line, flush=True)
            if out:
                out.write(line + "\n")
        for p in procs.values():
            p.process.close()
    summary: dict[str, Any] = {}
    for engine in ENGINES:
        for key in ("lexical_cold_ms", "lexical_warm_ms", "search_warm_ms"):
            vals = sorted(r[engine][key] for r in rows if r[engine][key] is not None)
            if vals:
                summary[f"{engine}.{key}.median"] = round(statistics.median(vals), 2)
                summary[f"{engine}.{key}.p95"] = vals[min(len(vals) - 1, int(0.95 * len(vals)))]
        if engine != "pg":
            for key in ("candidate_overlap", "top10_overlap"):
                summary[f"{engine}.{key}.mean"] = round(statistics.mean(r[engine][key] for r in rows), 3)
    print(json.dumps({"summary": summary}, indent=2))
    if out:
        out.write(json.dumps({"summary": summary}) + "\n")
        out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
