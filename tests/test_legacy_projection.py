from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from repo_issue_intelligence import legacy_projection
from repo_issue_intelligence.agent_store import AgentStore
from repo_issue_intelligence.agent_store_migrations import apply_v2_schema
from repo_issue_intelligence.legacy_projection import project_legacy_run

LEGACY_FIXTURE = Path(__file__).parent / "fixtures" / "protocol_v2" / "legacy_agent.sqlite3"


@pytest.mark.parametrize("marker", ["version", "table", "view", "uppercase", "full"])
def test_legacy_entry_rejects_v2_before_creating_tables(tmp_path: Path, marker: str) -> None:
    path = tmp_path / "v2.sqlite3"
    with sqlite3.connect(path) as connection:
        if marker == "full":
            apply_v2_schema(connection)
        elif marker == "version":
            connection.execute("PRAGMA user_version = 2")
        elif marker == "view":
            connection.execute("CREATE VIEW agent_v2_marker AS SELECT 1")
        elif marker == "uppercase":
            connection.execute("CREATE TABLE AGENT_V2_RUNS (value TEXT)")
        else:
            connection.execute("CREATE TABLE agent_v2_future (value TEXT)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="agent-db inspect"):
        AgentStore(path)
    assert path.read_bytes() == before


def test_reused_legacy_writer_refuses_replaced_v2_database(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    shutil.copy2(LEGACY_FIXTURE, source)
    writer = AgentStore(source)
    run = writer.get_run("legacy-success-0001")
    assert run is not None
    trace = writer.list_traces(run.run_id)[0]
    writer.save_run(run)
    writer.save_snapshot(run.run_id, "new-snapshot", {"kept": True})
    replacement = tmp_path / "v2.sqlite3"
    with sqlite3.connect(replacement) as connection:
        apply_v2_schema(connection)
    replacement.replace(source)
    before = source.read_bytes()
    for write in (
        lambda: writer.save_run(run),
        lambda: writer.append_trace(run.run_id, trace),
        lambda: writer.save_snapshot(run.run_id, "wrong-target", {}),
    ):
        with pytest.raises(ValueError, match="agent-db inspect"):
            write()
    assert source.read_bytes() == before


def test_legacy_projection_preserves_raw_report_without_loading_snapshots(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    shutil.copy2(LEGACY_FIXTURE, source)
    with sqlite3.connect(source) as connection:
        raw = connection.execute(
            "SELECT payload FROM agent_runs WHERE run_id = 'legacy-success-0001'"
        ).fetchone()[0]
        # Old snapshots are opaque history, not a source of V2 evidence.
        connection.execute("UPDATE agent_snapshots SET state_json = 'not-json-do-not-read'")
    before = source.read_bytes()
    projection = project_legacy_run(source, "legacy-success-0001")
    assert projection is not None
    assert projection.protocol == "legacy0"
    assert projection.raw_report_json == raw
    assert projection.report["run_id"] == "legacy-success-0001"
    assert projection.source_database == str(source.resolve())
    assert projection.migrated_at is None
    assert projection.provenance_status == "available"
    assert projection.evidence_status == "unavailable"
    assert project_legacy_run(source, "missing") is None
    assert source.read_bytes() == before


def test_imported_history_without_receipt_never_fabricates_provenance(tmp_path: Path) -> None:
    copy = tmp_path / "copy.sqlite3"
    shutil.copy2(LEGACY_FIXTURE, copy)
    with sqlite3.connect(copy) as connection:
        apply_v2_schema(connection)
    projection = project_legacy_run(copy, "legacy-success-0001")
    assert projection is not None
    assert projection.source_database is None
    assert projection.migrated_at is None
    assert projection.provenance_status == "unavailable"
    assert projection.evidence_status == "unavailable"
    with sqlite3.connect(copy) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_v2_runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM agent_v2_llm_attempts").fetchone()[0] == 0


def test_projection_rejects_unknown_database_without_creating_paths(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(FileNotFoundError):
        project_legacy_run(missing, "anything")
    assert not missing.exists()
    with sqlite3.connect(missing) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    with pytest.raises(ValueError, match="legacy"):
        project_legacy_run(missing, "anything")


def test_projection_rejects_symlinks_and_replaced_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "copy.sqlite3"
    shutil.copy2(LEGACY_FIXTURE, database)
    alias = tmp_path / "alias.sqlite3"
    alias.symlink_to(database)
    with pytest.raises(ValueError, match="ordinary"):
        project_legacy_run(alias, "legacy-success-0001")
    with sqlite3.connect(database) as connection:
        apply_v2_schema(connection)
    replacement = tmp_path / "replacement.sqlite3"
    shutil.copy2(LEGACY_FIXTURE, replacement)

    def replace_during_receipt_read(path: Path) -> None:
        replacement.replace(path)

    monkeypatch.setattr(legacy_projection, "read_migration_provenance", replace_during_receipt_read)
    with pytest.raises(ValueError, match="identity changed"):
        project_legacy_run(database, "legacy-success-0001")
