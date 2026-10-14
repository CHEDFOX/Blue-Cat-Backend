#!/usr/bin/env python
"""Run the research-assistant evaluation (eval/questions.yaml) against your database with a real key.

    ANTHROPIC_API_KEY=... python scripts/eval_research.py --dry-run          # list the questions, no API calls
    ANTHROPIC_API_KEY=... python scripts/eval_research.py --only de-theft    # one question
    ANTHROPIC_API_KEY=... python scripts/eval_research.py                    # all of them (real API spend)

Each question is answered by the full pipeline (understand, search, draft, verify). The report shows, per
question, which expected citations the answer cites, how many statements the verifier removed and the
token use; full answers are written to eval/results/<timestamp>.json (not committed).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ulis.assistant import evaluation
from ulis.assistant.config import AssistantConfig, load_assistant_config
from ulis.assistant.evaluation import EvalQuestion
from ulis.assistant.llm import LLMError
from ulis.assistant.oneshot import NotConfigured, run_once
from ulis.assistant.pipeline import PipelineError, ResearchInput
from ulis.assistant.schemas import ResearchResult
from ulis.catalog import Catalog
from ulis.settings import REPO_ROOT, Settings, load_settings

DEFAULT_QUESTIONS = REPO_ROOT / "eval" / "questions.yaml"


def evaluate(
    questions: list[EvalQuestion],
    run: Callable[[EvalQuestion], ResearchResult],
    *,
    log: Callable[[str], None] = lambda message: None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Answer every question; returns (score rows, full results). A failed question is a row with ``error``."""
    rows: list[dict[str, Any]] = []
    full: list[dict[str, Any]] = []
    for n, question in enumerate(questions, start=1):
        started = time.monotonic()
        log(f"[{n}/{len(questions)}] {question.id} ({question.jurisdiction})")
        try:
            result = run(question)
        except (PipelineError, LLMError) as exc:
            # A question that could not be answered counts as zero recall / not passed, never as a skipped one.
            rows.append(
                {
                    "id": question.id,
                    "jurisdiction": question.jurisdiction,
                    "error": f"{exc.code}: {exc.message}",
                    "recall": 0.0 if question.expected_citations else None,
                    "passed": False,
                }
            )
            full.append({"id": question.id, "error": exc.code})
            continue
        row = evaluation.score(question, result)
        row["seconds"] = round(time.monotonic() - started, 1)
        rows.append(row)
        full.append({"id": question.id, "result": result.model_dump(mode="json")})
    return rows, full


def format_report(rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines = [f"{'question':34} {'result':8} {'recall':>6} {'removed':>7} {'search':>6} {'tokens in/out':>15}  missing"]
    for r in rows:
        if "error" in r:
            lines.append(f"{r['id']:34} {'ERROR':8} {'':>6} {'':>7} {'':>6} {'':>15}  {r['error']}")
            continue
        recall = "-" if r["recall"] is None else f"{r['recall']:.2f}"
        verdict = "pass" if r["passed"] else "FAIL"
        missing = "; ".join(r["missing"]) if r["missing"] else ("" if r["recall"] is not None else "(control)")
        tokens = f"{r['input_tokens']}/{r['output_tokens']}"
        lines.append(
            f"{r['id']:34} {verdict:8} {recall:>6} {r['removed_claims']:>7} {r['searches']:>6} {tokens:>15}  {missing}"
        )
    lines += [
        "",
        f"questions {summary['questions']}, completed {summary['completed']}, mean recall {summary['mean_recall']}, "
        f"fully answered {summary['fully_answered']}/{summary['scored']}, controls passed "
        f"{summary['controls_passed']}/{summary['controls']}",
        f"removed claims {summary['removed_claims']}, answers with unverified authorities "
        f"{summary['unverified_authorities']}, searches {summary['searches']}, tokens in/out "
        f"{summary['input_tokens']}/{summary['output_tokens']}",
    ]
    return "\n".join(lines)


def main(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    catalog: Catalog | None = None,
    config: AssistantConfig | None = None,
    run: Callable[[EvalQuestion], ResearchResult] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--only", help="comma-separated question ids")
    parser.add_argument("--depth", choices=["standard", "deep"], help="override the depth of every question")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "eval" / "results", help="directory for the full JSON")
    parser.add_argument("--dry-run", action="store_true", help="validate and list the questions; no API calls")
    parser.add_argument("--fail-under", type=float, help="exit 1 if the mean recall is below this (0..1)")
    args = parser.parse_args(argv)

    settings = settings or load_settings()
    catalog = catalog or Catalog.load(settings.config_dir)
    config = config or load_assistant_config()
    questions = evaluation.load_questions(args.questions, catalog)
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        unknown = wanted - {q.id for q in questions}
        if unknown:
            print(f"unknown question ids: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        questions = [q for q in questions if q.id in wanted]
    if args.depth:
        questions = [q.model_copy(update={"depth": args.depth}) for q in questions]
    if args.dry_run:
        for q in questions:
            print(f"{q.id:34} {q.jurisdiction:7} {q.as_of or '-'}  expects {q.expected_citations or 'insufficient'}")
        print(f"{len(questions)} question(s); no API calls made")
        return 0

    def answer(q: EvalQuestion) -> ResearchResult:
        request = ResearchInput(
            question=q.question, case_text=None, jurisdiction=q.jurisdiction, as_of=q.as_of, depth=q.depth
        )
        return run_once(settings, catalog, config, request)

    runner = run or answer
    if run is None and not config.llm_configured:
        print("ANTHROPIC_API_KEY is not set; use --dry-run to only validate the questions.", file=sys.stderr)
        return 2
    print(
        f"Answering {len(questions)} question(s) with real model calls; this takes minutes and costs money.",
        file=sys.stderr,
    )
    try:
        rows, full = evaluate(questions, runner, log=lambda message: print(message, file=sys.stderr))
    except NotConfigured as exc:
        print(f"not configured: {exc}", file=sys.stderr)
        return 2
    summary = evaluation.summarise(rows)
    print(format_report(rows, summary))
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = args.out / f"{stamp}.json"
    target.write_text(
        json.dumps({"summary": summary, "rows": rows, "results": full}, ensure_ascii=False, indent=2), "utf-8"
    )
    print(f"\nfull results: {target}")
    if args.fail_under is not None and (summary["mean_recall"] or 0.0) < args.fail_under:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
