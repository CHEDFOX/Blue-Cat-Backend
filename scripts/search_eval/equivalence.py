"""Compare candidate SETS of PostgreSQL and the EXPERIMENTAL Tantivy stage on a real database.

    python scripts/search_eval/equivalence.py --db URL --index-root DIR --mode pg|native \
        [--requests 200] [--seed 1] [--budget 100000] [--out report.json]

Runs randomized requests (query, jurisdiction scope, as_of incl. validity boundaries, include_*
flags, languages, query language) through ``search._candidates`` and the Tantivy replacement with a
budget large enough to return every match, and compares the sets field by field (kind, ids,
jurisdiction, doc_type, language, status, validity, direct, text_length; not the score). A request
whose PostgreSQL result reaches the budget is skipped (the set would depend on the ranking).
Passages that are not in the index (inserted after it was built) are left out of the comparison.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text

from ulis.catalog import Catalog
from ulis.db import make_engine, make_session_factory
from ulis.retrieval import tantivy_engine as te
from ulis.retrieval.search import SearchRequest, _Candidate, _candidates, _expansions, _scope
from ulis.settings import REPO_ROOT

QUERIES = [
    "401(k)",
    "6(1)(f)",
    "§ 1983",
    "42 U.S.C. 1983",
    "L. 121-1",
    '"right to vote"',
    "hardship distribution",
    '"Federal Register" -publication',
    "privacy impact assessment",
    "commodity pool operator or trading advisor",
    "unauthorized electronic fund transfer",
    "hazardous waste manifest",
    "premarket notification",
    "set-aside",
    "self-employment",
    "airworthiness directive",
    '"gross income"',
    "benzene exposure",
    "501(c)(3)",
    "1026.19(e)",
    "U.S.C. 552a",
    "and/or",
    "e-mail",
    "endangered species habitat",
    "personal data",
    "données personnelles",
    "Datenschutz",
    "個人情報",
    "zebra mussel",
    "pesticide tolerance",
]


def row(c: _Candidate) -> tuple[Any, ...]:
    return (
        c.kind,
        c.provision_version_id,
        c.judgment_paragraph_id,
        c.instrument_id,
        c.judgment_id,
        c.jurisdiction_id,
        c.doc_type,
        c.language,
        c.status,
        c.valid_from,
        c.valid_to,
        c.direct,
        c.text_length,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--index-root", type=Path, required=True)
    ap.add_argument("--mode", choices=te.MODES, default="pg")
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--budget", type=int, default=100_000)
    ap.add_argument("--jurisdictions", default="US,,US-CA,GB-ENG,FR,DE")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    rng = random.Random(args.seed)  # noqa: S311 - reproducible sampling, not security
    catalog = Catalog.load(REPO_ROOT / "config")
    jurisdictions = [j or None for j in args.jurisdictions.split(",") if not j or j in catalog.jurisdictions]
    sessions = make_session_factory(make_engine(args.db))
    stage = te.TantivyCandidates(te.TantivyProcess(args.index_root / args.mode), args.mode)
    report: dict[str, Any] = {"mode": args.mode, "requests": 0, "identical": 0, "skipped": 0, "mismatches": []}
    with sessions() as session:
        dates = sorted(
            {
                d
                for (d,) in session.execute(
                    text("SELECT DISTINCT valid_from FROM passages WHERE valid_from IS NOT NULL LIMIT 200")
                )
            }
        )
        boundaries = [x for d in dates for x in (d - timedelta(days=1), d)] or [date(2020, 1, 1)]
        for _ in range(args.requests):
            req = SearchRequest(
                q=rng.choice(QUERIES),
                jurisdiction=rng.choice(jurisdictions),
                as_of=rng.choice([None, None, *rng.sample(boundaries, min(3, len(boundaries))), date(1990, 1, 1)]),
                languages=rng.choice([[], [], ["en"], ["fr", "de"]]),
                include_foreign=rng.random() < 0.2,
                include_proposed=rng.random() < 0.3,
                include_historical=rng.random() < 0.3,
                include_prospective=rng.random() < 0.3,
                include_case_law=rng.random() < 0.8,
                query_language=rng.choice([None, "en"]),
            )
            as_of = req.as_of or date.today()
            scope = _scope(session, catalog, req.jurisdiction, as_of)
            expansions = _expansions(session, req.q, req.query_language)[1]
            pg_c = _candidates(session, req, as_of, scope, expansions, args.budget)
            if len(pg_c) >= args.budget:
                report["skipped"] += 1
                continue
            tv_c = stage(session, req, as_of, scope, expansions, args.budget)
            pg = {str(c.passage_id): row(c) for c in pg_c}
            tv = {str(c.passage_id): row(c) for c in tv_c}
            only_pg = sorted(set(pg) - set(tv))
            if only_pg:
                present = set(stage.process.request({"op": "has", "ids": only_pg})["found"])
                pg = {k: v for k, v in pg.items() if k in tv or k in present}
            report["requests"] += 1
            if pg == tv:
                report["identical"] += 1
                continue
            diff_fields = sorted(k for k in set(pg) & set(tv) if pg[k] != tv[k])
            report["mismatches"].append(
                {
                    "request": {k: str(v) for k, v in vars(req).items()},
                    "postgresql": len(pg),
                    "tantivy": len(tv),
                    "only_postgresql": sorted(set(pg) - set(tv))[:10],
                    "only_tantivy": sorted(set(tv) - set(pg))[:10],
                    "n_only_postgresql": len(set(pg) - set(tv)),
                    "n_only_tantivy": len(set(tv) - set(pg)),
                    "field_differences": diff_fields[:10],
                }
            )
    stage.process.close()
    report["identical_share"] = round(report["identical"] / report["requests"], 4) if report["requests"] else None
    out = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.out:
        args.out.write_text(out)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
