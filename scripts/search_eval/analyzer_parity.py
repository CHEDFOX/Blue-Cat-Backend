"""Measure how closely mode "native" copies PostgreSQL's analysis.

    python scripts/search_eval/analyzer_parity.py --db URL [--sample 2000] [--queries FILE]

1. Passages: for a random sample, compares passages.tsv (PostgreSQL) with the native tsvector of
   the same context/text (lexemes, positions, weights). Reports the share of identical
   tsvectors, lexeme-occurrence precision/recall and the most frequent differences.
2. Queries: compares websearch_to_tsquery (PostgreSQL) with the native copy for a list of legal
   queries (built-in list plus --queries, one per line) in every configuration of --configs.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import text

from ulis.db import make_engine, make_session_factory
from ulis.retrieval import tantivy_engine as te

QUERIES = [
    "401(k)",
    "6(1)(f)",
    "§ 1983",
    "42 U.S.C. § 1983",
    "L. 121-1",
    "Art. 6 Abs. 1 lit. f DSGVO",
    '"right to vote" or suffrage -felony',
    "hardship distribution 401(k) plan",
    "self-defense",
    "non-Federal entity",
    '"code of federal regulations"',
    "employer's liability",
    "U.S.C. 552a(b)(3)",
    "12 CFR 1026.19(e)",
    "section 501(c)(3) organization",
    "OR",
    "-",
    '"unclosed phrase',
    "données personnelles conservées",
    "Datenschutz-Grundverordnung",
    "protección de datos personales",
    "personuppgifter behandling",
    "henkilötietojen käsittely",
    "個人情報の保護",
    "Deddf Llywodraeth Cymru 2006",
    "x OR y z",
    "safe harbor -fisheries or wildlife",
    "e-mail address and/or telephone",
    "§§ 1.1-1.5",
    "26 U.S.C. 401(k)(2)(B)(i)(IV)",
]

_TSV = re.compile(r"'((?:[^']|'')*)'(?::([0-9A-D,]+))?")


def parse_pg_tsv(tsv: str) -> dict[str, list[tuple[int, str]]]:
    out: dict[str, list[tuple[int, str]]] = {}
    for m in _TSV.finditer(tsv):
        lex = m.group(1).replace("''", "'").replace("\\\\", "\\")
        positions = []
        for p in (m.group(2) or "").split(","):
            if p:
                w = p[-1] if p[-1] in "ABCD" else "D"
                positions.append((int(p.rstrip("ABCD")), "B" if w != "A" else "A"))
        out[lex] = positions
    return out


def passages(session: Any, proc: te.TantivyProcess, sample: int) -> dict[str, Any]:
    rows = session.execute(
        text(
            "SELECT search_config::text, coalesce(search_context, context, ''), coalesce(search_text, text), tsv::text"
            " FROM passages TABLESAMPLE SYSTEM (1) LIMIT :n"
        ),
        {"n": sample},
    ).all()
    identical = 0
    tp = fp = fn = 0
    missing: Counter[str] = Counter()
    extra: Counter[str] = Counter()
    by_config: Counter[str] = Counter()
    for cfg, ctx, body, tsv in rows:
        pg = parse_pg_tsv(tsv)
        nat = proc.tsvector(cfg, ctx, body)
        by_config[cfg] += 1
        if pg == nat:
            identical += 1
        pg_occ = {(lex, p, w) for lex, ps in pg.items() for p, w in ps}
        nat_occ = {(lex, p, w) for lex, ps in nat.items() for p, w in ps}
        tp += len(pg_occ & nat_occ)
        fn += len(pg_occ - nat_occ)
        fp += len(nat_occ - pg_occ)
        for lex in set(pg) - set(nat):
            missing[lex] += 1
        for lex in set(nat) - set(pg):
            extra[lex] += 1
    n = len(rows)
    return {
        "passages": n,
        "configs": dict(by_config),
        "identical_tsvectors": identical,
        "identical_share": round(identical / n, 4) if n else None,
        "occurrence_recall": round(tp / (tp + fn), 5) if tp + fn else None,
        "occurrence_precision": round(tp / (tp + fp), 5) if tp + fp else None,
        "top_lexemes_only_in_postgresql": missing.most_common(25),
        "top_lexemes_only_in_native": extra.most_common(25),
    }


def queries(session: Any, proc: te.TantivyProcess, items: list[str], configs: list[str]) -> dict[str, Any]:
    same, diffs = 0, []
    for cfg in configs:
        for q in items:
            pg = session.execute(
                text("SELECT websearch_to_tsquery(CAST(:c AS regconfig), :q)::text"), {"c": cfg, "q": q}
            ).scalar()
            nat = proc.websearch(cfg, q)
            if pg == nat:
                same += 1
            else:
                diffs.append({"config": cfg, "q": q, "postgresql": pg, "native": nat})
    total = len(items) * len(configs)
    return {"queries": total, "identical": same, "identical_share": round(same / total, 4), "differences": diffs}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--sample", type=int, default=2000)
    ap.add_argument("--queries", type=Path)
    ap.add_argument("--configs", default="english,french,german,spanish,swedish,finnish,simple")
    args = ap.parse_args(argv)
    items = list(QUERIES)
    if args.queries:
        items += [line.strip() for line in args.queries.read_text().splitlines() if line.strip()]
    sessions = make_session_factory(make_engine(args.db))
    with sessions() as session, te.TantivyProcess() as proc:
        report = {
            "passages": passages(session, proc, args.sample),
            "queries": queries(session, proc, items, args.configs.split(",")),
        }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
