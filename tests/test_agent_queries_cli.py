import json
import stat

from test_api_v2 import setup_run
from typer.testing import CliRunner

from repo_issue_intelligence.cli import app


def test_cli_queries_require_explicit_content_and_private_output(monkeypatch, tmp_path):
    _, store, run = setup_run(monkeypatch, tmp_path)
    runner = CliRunner()
    args = ["agent-query", run.run_id, "--database", str(store.path)]
    shown = runner.invoke(app, args)
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["issue_count"] == 2
    assert "committed'" not in shown.output
    evidence_args = [*args, "--view", "evidence", "--issue", "1"]
    metadata = runner.invoke(app, evidence_args)
    assert metadata.exit_code == 0, metadata.output
    metadata_payload = json.loads(metadata.output)
    assert "content" not in metadata_payload["items"][0]
    evidence_id = metadata_payload["items"][0]["evidence_id"]
    evidence_set_id = metadata_payload["evidence_set_id"]
    output = tmp_path / "public" / "evidence.json"
    output.parent.mkdir(mode=0o755)
    output.write_text("old")
    output.chmod(0o644)
    result = runner.invoke(
        app,
        [
            *evidence_args,
            "--evidence-id",
            evidence_id,
            "--evidence-set-id",
            evidence_set_id,
            "--include-content",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "committed" in json.loads(output.read_text())["content"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    before = store.path.read_bytes()
    for invalid in (
        [*args, "--include-content"],
        [*args, "--view", "issue"],
        [*evidence_args, "--output", str(store.path)],
    ):
        assert runner.invoke(app, invalid).exit_code == 2
    assert store.path.read_bytes() == before
