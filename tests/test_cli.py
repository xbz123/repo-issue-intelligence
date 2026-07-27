import json
from pathlib import Path

from typer.testing import CliRunner

from repo_issue_intelligence.cli import app

runner = CliRunner()


def test_investigate_issue_accepts_documented_options(tmp_path: Path) -> None:
    output = tmp_path / "investigation.json"

    result = runner.invoke(
        app,
        [
            "investigate-issue",
            "examples/issues.json",
            "--issue",
            "184",
            "--repo",
            ".",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output.exists()


def test_agent_run_and_review_commands(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    output = tmp_path / "agent-run.json"

    run_result = runner.invoke(
        app,
        [
            "agent-run",
            "examples/issues.json",
            "--repo",
            "examples/demo_repository",
            "--top-k",
            "1",
            "--database",
            str(database),
            "--output",
            str(output),
        ],
    )

    assert run_result.exit_code == 0, run_result.output
    run_payload = json.loads(output.read_text(encoding="utf-8"))
    run_id = run_payload["run_id"]
    assert run_payload["investigations"][0]["candidates"][0]["file"] == "auth_service.py"

    review_result = runner.invoke(
        app,
        [
            "agent-review",
            run_id,
            "--decision",
            "approved",
            "--notes",
            "CLI review",
            "--database",
            str(database),
        ],
    )

    assert review_result.exit_code == 0, review_result.output
