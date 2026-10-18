"""Run the real ulis search() with the PostgreSQL or an EXPERIMENTAL Tantivy candidate stage.

    python scripts/search_eval/run_search.py --db postgresql+psycopg://ulis:ulis@localhost:5432/ulis \
        --engine tantivy-pg --index-root /path/to/indexes --q "401(k) hardship distribution" --jurisdiction US

--engine pg               search() exactly as in production (PostgreSQL full text)
--engine tantivy-pg       Tantivy index of PostgreSQL's own lexemes, tsqueries built by PostgreSQL
--engine tantivy-native   Tantivy index and queries analysed without PostgreSQL
--engine all              run the three and print them side by side

The index directory of a mode is <index-root>/<mode> (as written by build_index.py). Prints the
ranked results (citation, scores) and timings; --json prints the full search() output.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from ulis.catalog import Catalog
from ulis.db import make_engine, make_session_factory
from ulis.retrieval import tantivy_engine as te
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT

ENGINES = ("pg", "tantivy-pg", "tantivy-native")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="SQLAlchemy URL of the ULIS database")
    ap.add_argument("--engine", choices=[*ENGINES, "all"], default="pg")
    ap.add_argument("--index-root", type=Path, help="directory holding the pg/ and native/ indexes")
    ap.add_argument("--q", required=True)
    ap.add_argument("--jurisdiction")
    ap.add_argument("--as-of", type=date.fromisoformat)
    ap.add_argument("--today", type=date.fromisoformat, default=date.today())
    ap.add_argument("--languages", default="", help="comma-separated BCP 47 codes")
    ap.add_argument("--query-language")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--budget", type=int, default=200, help="candidate budget (search() default 200)")
    for flag in ("foreign", "proposed", "historical", "prospective"):
        ap.add_argument(f"--include-{flag}", action="store_true")
    ap.add_argument("--no-case-law", action="store_true")
    ap.add_argument("--forum-court")
    ap.add_argument("--repeat", type=int, default=1, help="run N times and report every timing")
    ap.add_argument("--json", action="store_true", help="print the full search() output")
    return ap.parse_args(argv)


def request(args: argparse.Namespace) -> SearchRequest:
    return SearchRequest(
        q=args.q,
        jurisdiction=args.jurisdiction,
        as_of=args.as_of,
        languages=[x for x in args.languages.split(",") if x],
        include_foreign=args.include_foreign,
        include_proposed=args.include_proposed,
        include_historical=args.include_historical,
        include_prospective=args.include_prospective,
        include_case_law=not args.no_case_law,
        forum_court=args.forum_court,
        limit=args.limit,
        query_language=args.query_language,
    )


def candidate_stage(engine: str, index_root: Path | None) -> te.TantivyCandidates | None:
    if engine == "pg":
        return None
    if index_root is None:
        raise SystemExit("--index-root is required for the Tantivy engines")
    mode = engine.removeprefix("tantivy-")
    return te.TantivyCandidates(te.TantivyProcess(index_root / mode), mode)


def run(args: argparse.Namespace) -> list[dict[str, Any]]:
    catalog = Catalog.load(REPO_ROOT / "config")
    sessions = make_session_factory(make_engine(args.db))
    req = request(args)
    engines = list(ENGINES) if args.engine == "all" else [args.engine]
    reports = []
    for engine in engines:
        stage = candidate_stage(engine, args.index_root)
        timings, result = [], {}
        try:
            with sessions() as session, te.use_engine(stage):
                for _ in range(args.repeat):
                    t0 = time.perf_counter()
                    result = search(session, catalog, req, today=args.today, candidate_budget=args.budget)
                    timings.append(round((time.perf_counter() - t0) * 1000, 2))
        finally:
            if stage is not None:
                stage.process.close()
        reports.append(
            {
                "engine": engine,
                "ms": timings,
                "lexical_stage": stage.last if stage else None,
                "total_candidates": result["total_candidates"],
                "results": [
                    {
                        "rank": h["rank"],
                        "citation": h["citation"],
                        "passage_id": h["passage_id"],
                        "final": h["score"]["final"],
                        "lexical": h["score"]["lexical"],
                        "authority": h["authority"]["category"],
                    }
                    for h in result["results"]
                ],
                "full": result if args.json else None,
            }
        )
    return reports


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    reports = run(args)
    if args.json:
        json.dump(reports, sys.stdout, indent=2, default=str)
        print()
        return 0
    for rep in reports:
        print(f"== {rep['engine']}: {rep['total_candidates']} candidates, search() ms {rep['ms']}")
        if rep["lexical_stage"]:
            st = rep["lexical_stage"]
            print(
                f"   tantivy: matched {st.get('matched')}, lexical stage {st.get('total_ms', st.get('search_ms'))} ms"
            )
        for r in rep["results"]:
            print(f"   {r['rank']:>2}. {r['final']:<10} {r['lexical']:<10} {r['authority']:<22} {r['citation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
