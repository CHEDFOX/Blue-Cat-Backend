"""Known-item ranking evaluation of search() on PostgreSQL vs the EXPERIMENTAL Tantivy stages.

    python scripts/search_eval/ranking_eval.py --db URL --index-root DIR [--n 100] [--seed 5] \
        [--jurisdiction US] [--out ranking.jsonl]

Without relevance judgments, two synthetic known-item sets drawn from the indexed provisions:
  * heading: the section heading (last sentence of the passage context, at least 3 words, used by
    at most 3 sections) as the query; relevant = every passage of that section;
  * phrase: 4-7 consecutive words from the middle of one passage's text as the query (plain words,
    no quotes); relevant = that passage's section.
For each query and engine the real search() runs (limit 10, budget 200); reported: success@1,
success@10, MRR@10 (rank of the first result from a relevant section) and whether a relevant
passage was among the candidates. Queries are written to --out with per-engine ranks.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text

from ulis.catalog import Catalog
from ulis.db import make_engine, make_session_factory
from ulis.retrieval import tantivy_engine as te
from ulis.retrieval.search import SearchRequest, search
from ulis.settings import REPO_ROOT

ENGINES = ("pg", "tantivy-pg", "tantivy-native")
TODAY = date(2026, 10, 18)
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")


def section_key(num_path: list[str] | None, instrument: Any) -> tuple[Any, str]:
    return (instrument, (num_path or [""])[0])


def heading_of(context: str) -> str:
    # "5 CFR Part 870: Title (eCFR, file of 2026-10-16). Amount of a Living Benefit."
    tail = context.split("). ", 1)[-1].strip().rstrip(".")
    return tail


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--index-root", type=Path, required=True)
    ap.add_argument("--n", type=int, default=100, help="queries per set")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--jurisdiction", default="US")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    rng = random.Random(args.seed)  # noqa: S311 - reproducible sampling, not security
    catalog = Catalog.load(REPO_ROOT / "config")
    sessions = make_session_factory(make_engine(args.db))
    stages = {
        e: te.TantivyCandidates(
            te.TantivyProcess(args.index_root / e.removeprefix("tantivy-")), e.removeprefix("tantivy-")
        )
        for e in ENGINES[1:]
    }
    with sessions() as session:
        sample = session.execute(
            text(
                "SELECT p.id::text, p.instrument_id, pr.num_path, p.context, p.text"
                " FROM passages p TABLESAMPLE SYSTEM (2) REPEATABLE (:seed)"
                " JOIN provision_versions pv ON pv.id = p.provision_version_id"
                " JOIN provisions pr ON pr.id = pv.provision_id"
                " WHERE p.jurisdiction_id = :j AND p.kind = 'provision' LIMIT 20000"
            ),
            {"seed": args.seed, "j": args.jurisdiction},
        ).all()
        heading_sections: dict[str, set[Any]] = {}
        for _pid, inst, path, ctx, _body in sample:
            heading_sections.setdefault(heading_of(ctx), set()).add(section_key(path, inst))
        queries: list[dict[str, Any]] = []
        headings = [h for h, secs in heading_sections.items() if len(_WORD.findall(h)) >= 3 and len(secs) <= 3]
        for h in rng.sample(headings, min(args.n, len(headings))):
            queries.append({"set": "heading", "q": h, "sections": [list(map(str, s)) for s in heading_sections[h]]})
        rows = rng.sample(sample, min(len(sample), args.n * 3))
        for _pid, inst, path, _ctx, body in rows:
            words = _WORD.findall(body)
            if len(words) < 12:
                continue
            k = rng.randint(4, 7)
            start = rng.randint(len(words) // 4, len(words) - k - 1)
            queries.append(
                {"set": "phrase", "q": " ".join(words[start : start + k]), "sections": [[str(inst), (path or [""])[0]]]}
            )
            if sum(1 for q in queries if q["set"] == "phrase") >= args.n:
                break
        # section of every passage the engines return
        out = args.out.open("w") if args.out else None
        stats: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in queries:
            relevant = {tuple(s) for s in item["sections"]}
            req = SearchRequest(q=item["q"], jurisdiction=args.jurisdiction, limit=10)
            for engine in ENGINES:
                with te.use_engine(stages.get(engine)):
                    result = search(session, catalog, req, today=TODAY)
                ids = [r["passage_id"] for r in result["results"]]
                secs = dict(
                    session.execute(
                        text(
                            "SELECT p.id::text, p.instrument_id::text || '|' || coalesce(pr.num_path[1], '')"
                            " FROM passages p JOIN provision_versions pv ON pv.id = p.provision_version_id"
                            " JOIN provisions pr ON pr.id = pv.provision_id WHERE p.id::text = ANY(:ids)"
                        ),
                        {"ids": ids},
                    ).all()
                )
                rank = next(
                    (i + 1 for i, pid in enumerate(ids) if tuple(secs.get(pid, "|").split("|", 1)) in relevant), None
                )
                item[engine] = rank
                stats.setdefault((item["set"], engine), []).append(
                    {"rank": rank, "candidates": result["total_candidates"]}
                )
            if out:
                out.write(json.dumps(item, ensure_ascii=False) + "\n")
        for stage in stages.values():
            stage.process.close()
    summary: dict[str, Any] = {}
    for (qset, engine), vals in sorted(stats.items()):
        ranks = [v["rank"] for v in vals]
        summary[f"{qset}.{engine}"] = {
            "queries": len(ranks),
            "success@1": round(sum(1 for r in ranks if r == 1) / len(ranks), 3),
            "success@10": round(sum(1 for r in ranks if r) / len(ranks), 3),
            "mrr@10": round(statistics.mean(1 / r if r else 0 for r in ranks), 3),
        }
    wins: Counter[str] = Counter()
    for item in queries:
        for engine in ENGINES[1:]:
            a, b = item.get("pg"), item.get(engine)
            ra, rb = a or 99, b or 99
            wins[f"{engine}:better"] += rb < ra
            wins[f"{engine}:worse"] += rb > ra
            wins[f"{engine}:same"] += rb == ra
    summary["pairwise_vs_pg"] = dict(wins)
    text_out = json.dumps(summary, indent=2)
    print(text_out)
    if out:
        out.write(json.dumps({"summary": summary}) + "\n")
        out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
