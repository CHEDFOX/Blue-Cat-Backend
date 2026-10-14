"""``python -m ulis.cli research`` (PostgreSQL, scripted model)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assistant_support import happy_script

from ulis import cli
from ulis.assistant import oneshot
from ulis.assistant.llm import FakeLLM
from ulis.ingestion.pipeline import IngestionReport

pytestmark = pytest.mark.db


def test_cli_research_prints_the_json_result(
    db_url: str,
    ingested: IngestionReport,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ULIS_DATABASE_URL", db_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-offline")
    fake = FakeLLM(happy_script())
    monkeypatch.setattr(oneshot, "default_llm_factory", lambda config: lambda depth: fake)
    case = tmp_path / "case.txt"
    case.write_text("The controller keeps backups of client files.", "utf-8")
    code = cli.main(
        [
            "research",
            "--question",
            "How long may a controller keep personal data?",
            "--jurisdiction",
            "xa",
            "--as-of",
            "2024-01-01",
            "--case-file",
            str(case),
        ]
    )
    out, err = capsys.readouterr()
    assert code == 0
    result = json.loads(out)
    assert result["authorities"][0]["citation"] == "Personal Data Act 2015 (XA) s 12(1)"
    assert result["guidance"]["issues"][0]["reasoning"] and result["removed_claims"] == 0
    assert "[understanding]" in err and "[verifying]" in err  # progress goes to stderr, JSON stays clean on stdout


def test_cli_research_without_key_or_with_bad_input_exits_nonzero(
    db_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ULIS_DATABASE_URL", db_url)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert cli.main(["research", "--question", "x", "--jurisdiction", "XA"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
    assert cli.main(["research", "--question", "x", "--jurisdiction", "ZZ"]) == 2
    assert cli.main(["research", "--question", "q" * 4001]) == 2
