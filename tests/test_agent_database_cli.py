from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from repo_issue_intelligence.cli import app

runner = CliRunner()


def test_agent_database_commands_are_explicit_and_create_only(tmp_path: Path) -> None:
    destination = tmp_path / "private" / "v2.sqlite3"
    missing = runner.invoke(app, ["agent-db", "inspect", str(destination)])
    assert missing.exit_code != 0
    assert not destination.exists()
    created = runner.invoke(app, ["agent-db", "create-v2", "--destination", str(destination)])
    assert created.exit_code == 0, created.output
    inspected = runner.invoke(app, ["agent-db", "inspect", str(destination)])
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.output)["kind"] == "knownv2"
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT count(*) FROM agent_v2_runs").fetchone() == (0,)
    before = destination.read_bytes()
    repeated = runner.invoke(app, ["agent-db", "create-v2", "--destination", str(destination)])
    assert repeated.exit_code != 0
    assert destination.read_bytes() == before


def test_cli_migration_preserves_source_and_redacts_errors(tmp_path: Path) -> None:
    source = tmp_path / "token-sensitive-source.sqlite3"
    shutil.copy2(Path(__file__).parent / "fixtures/protocol_v2/legacy_agent.sqlite3", source)
    destination = tmp_path / "private" / "copy.sqlite3"
    before = source.read_bytes()
    result = runner.invoke(
        app, ["agent-db", "migrate", "--source", str(source), "--destination", str(destination)]
    )
    assert result.exit_code == 0, result.output
    assert source.read_bytes() == before
    assert "token-sensitive" not in result.output
    refused = runner.invoke(
        app, ["agent-db", "migrate", "--source", str(source), "--destination", str(source)]
    )
    assert refused.exit_code != 0
    assert "token-sensitive" not in refused.output
    assert str(tmp_path) not in refused.output
    assert source.read_bytes() == before
