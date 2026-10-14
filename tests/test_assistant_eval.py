"""The evaluation question set and harness (scripted model; the real run needs a key and your database)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from assistant_support import CONFIG, S12_1, happy_script, run_pipeline, understanding_reply
from sqlalchemy.orm import Session, sessionmaker

from ulis.assistant import evaluation
from ulis.assistant.evaluation import EvalQuestion, load_questions, normalise_citation
from ulis.assistant.llm import FakeLLM
from ulis.assistant.schemas import ResearchResult
from ulis.catalog import Catalog
from ulis.ingestion.pipeline import IngestionReport
from ulis.settings import REPO_ROOT, load_settings

QUESTIONS = REPO_ROOT / "eval" / "questions.yaml"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("eval_research", REPO_ROOT / "scripts" / "eval_research.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_question_file_is_valid_and_covers_real_jurisdictions(catalog: Catalog) -> None:
    questions = load_questions(QUESTIONS, catalog)
    assert len(questions) >= 15
    jurisdictions = {q.jurisdiction for q in questions}
    assert len(jurisdictions) >= 12 and not any(catalog.jurisdictions.get(j).fictional for j in jurisdictions)
    assert any(q.expect_insufficient for q in questions) and all(
        q.expected_citations for q in questions if not q.expect_insufficient
    )
    assert {"DE", "GB-ENG", "CA", "US", "JP", "CH", "EU"} <= jurisdictions
    assert len({q.id for q in questions}) == len(questions)


def test_question_validation_rejects_bad_files(tmp_path: Path, catalog: Catalog) -> None:
    def write(body: str) -> Path:
        path = tmp_path / "q.yaml"
        path.write_text(body, "utf-8")
        return path

    head = "version: 1\nquestions:\n"
    item = "  - id: a\n    jurisdiction: ZZ\n    question: A long enough question text?\n    expected_citations: [x]\n"
    with pytest.raises(ValueError, match="unknown jurisdiction"):
        load_questions(write(head + item), catalog)
    with pytest.raises(ValueError, match="expected_citations, or expect_insufficient"):
        load_questions(write(head + item.replace("ZZ", "DE").replace("expected_citations: [x]", "")), catalog)
    with pytest.raises(ValueError, match="duplicate"):
        load_questions(write(head + item.replace("ZZ", "DE") * 2), catalog)
    with pytest.raises(ValueError):
        load_questions(write(head + item.replace("ZZ", "DE") + "    surprise: 1\n"), catalog)


def test_citation_matching_ignores_case_spacing_and_dash_style() -> None:
    assert normalise_citation("Art.  1 Abs. 1 GG") == normalise_citation("art. 1 abs. 1 gg")
    assert normalise_citation("art. 6:162 – lid 1") == normalise_citation("ART. 6:162 - LID 1")


@pytest.mark.db
def test_scoring_and_summary_over_scripted_answers(
    session_factory: sessionmaker[Session], catalog: Catalog, ingested: IngestionReport
) -> None:
    hit = EvalQuestion(
        id="xa-retention",
        question="How long may a controller keep personal data?",
        jurisdiction="XA",
        expected_citations=[S12_1.upper(), "Personal Data Act 2015 (XA) s 99"],
    )
    control = EvalQuestion(
        id="control", question="What does the law of Germany provide here?", jurisdiction="DE", expect_insufficient=True
    )
    answered = run_pipeline(session_factory, catalog, FakeLLM(happy_script()))
    insufficient = run_pipeline(
        session_factory, catalog, FakeLLM([understanding_reply(jurisdictions=("DE",))]), jurisdiction="DE"
    )
    first, second = evaluation.score(hit, answered), evaluation.score(control, insufficient)
    assert (
        first["recall"] == 0.5
        and first["found"] == [S12_1.upper()]
        and first["missing"] == ["Personal Data Act 2015 (XA) s 99"]
    )
    assert first["passed"] is False and first["all_verified"] and first["searches"] == 1
    assert second["passed"] is True and second["recall"] is None and second["all_issues_insufficient"]
    summary = evaluation.summarise([first, second, {"id": "broken", "error": "x"}])
    assert (summary["questions"], summary["completed"], summary["failed"]) == (3, 2, ["broken"])
    assert summary["mean_recall"] == 0.5 and summary["controls_passed"] == 1 and summary["scored"] == 1


@pytest.mark.db
def test_harness_runs_end_to_end_with_an_injected_runner(
    session_factory: sessionmaker[Session],
    catalog: Catalog,
    ingested: IngestionReport,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = load_script()
    qfile = tmp_path / "q.yaml"
    qfile.write_text(
        "version: 1\nquestions:\n"
        "  - id: xa-retention\n    jurisdiction: XA\n    as_of: 2024-01-01\n"
        '    question: "How long may a controller keep personal data?"\n'
        f'    expected_citations: ["{S12_1}"]\n'
        "  - id: xa-broken\n    jurisdiction: XA\n    as_of: 2024-01-01\n"
        '    question: "A question whose model call fails outright"\n'
        '    expected_citations: ["x"]\n',
        "utf-8",
    )

    def runner(question: EvalQuestion) -> ResearchResult:
        llm = FakeLLM(happy_script() if question.id == "xa-retention" else [])
        return run_pipeline(session_factory, catalog, llm, question=question.question)

    out = tmp_path / "results"
    argv = ["--questions", str(qfile), "--out", str(out)]
    code = script.main(
        argv + ["--fail-under", "0.9"], settings=load_settings(), catalog=catalog, config=CONFIG, run=runner
    )
    report = capsys.readouterr().out
    assert code == 1  # one question failed outright, so the mean recall over scored rows is below the bar
    assert "xa-retention" in report and "ERROR" in report and "mean recall" in report
    (saved,) = out.glob("*.json")
    data = json.loads(saved.read_text("utf-8"))
    assert data["summary"]["failed"] == ["xa-broken"] and data["results"][0]["result"]["authorities"]
    only = script.main(
        argv + ["--only", "xa-retention", "--fail-under", "0.9"],
        settings=load_settings(),
        catalog=catalog,
        config=CONFIG,
        run=runner,
    )
    assert only == 0
    assert (
        script.main(argv + ["--only", "nope"], settings=load_settings(), catalog=catalog, config=CONFIG, run=runner)
        == 2
    )


def test_dry_run_needs_no_key_and_real_run_refuses_without_one(
    catalog: Catalog, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    script = load_script()
    assert script.main(["--dry-run", "--only", "de-theft"], settings=load_settings(), catalog=catalog) == 0
    assert "de-theft" in capsys.readouterr().out
    assert script.main([], settings=load_settings(), catalog=catalog) == 2
