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
