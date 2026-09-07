from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from repo_issue_intelligence.agent_store_migrations import (
    DatabaseKind,
    MigrationError,
    apply_v2_schema,
    backup_agent_database,
    inspect_agent_database,
)

ROOT = Path(__file__).resolve().parents[1]
LEGACY_FIXTURE = ROOT / "tests" / "fixtures" / "protocol_v2" / "legacy_agent.sqlite3"


def _connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _new_v2_connection(path: Path) -> sqlite3.Connection:
    connection = _connection(path)
    apply_v2_schema(connection)
    return connection


def _seed_issue(connection: sqlite3.Connection, *, run_id: str = "run-1", issue: int = 7) -> None:
    connection.execute(
        """
        INSERT INTO agent_v2_runs
            (run_id, snapshot_json, configuration_json, inputs_json, selection_json,
             status, created_at, updated_at)
        VALUES (?, '{}', '{}', '{}', '{}', 'RUNNING', 't0', 't0')
        """,
        (run_id,),
    )
    connection.execute(
        """
        INSERT INTO agent_v2_issues (run_id, issue_number, deterministic_state)
        VALUES (?, ?, 'pending')
        """,
        (run_id, issue),
    )
    connection.execute(
        """
        INSERT INTO agent_v2_evidence_sets
            (evidence_set_id, run_id, issue_number, collection_context_json)
        VALUES ('evidence-1', ?, ?, '{}')
        """,
        (run_id, issue),
    )
    connection.execute(
        """
        UPDATE agent_v2_issues SET evidence_set_id = 'evidence-1'
        WHERE run_id = ? AND issue_number = ?
        """,
        (run_id, issue),
    )
    connection.execute("""
        UPDATE agent_v2_evidence_sets SET sealed = 1, sealed_at = 't1'
        WHERE evidence_set_id = 'evidence-1'
        """)
    connection.commit()


def test_inspect_distinguishes_empty_legacy_known_unknown_and_corrupt(tmp_path: Path) -> None:
    empty = tmp_path / "empty.sqlite3"
    with _connection(empty) as connection:
        assert inspect_agent_database(connection).kind is DatabaseKind.EMPTY

    legacy = tmp_path / "legacy.sqlite3"
    shutil.copy2(LEGACY_FIXTURE, legacy)
    with _connection(legacy) as connection:
        assert inspect_agent_database(connection).kind is DatabaseKind.LEGACY0

    unknown = tmp_path / "unknown.sqlite3"
    with _connection(unknown) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.commit()
        assert inspect_agent_database(connection).kind is DatabaseKind.UNKNOWN

    partial = tmp_path / "partial.sqlite3"
    with _connection(partial) as connection:
        connection.execute("CREATE TABLE agent_v2_runs (run_id TEXT PRIMARY KEY)")
        connection.commit()
        assert inspect_agent_database(connection).kind is DatabaseKind.CORRUPT

    versioned_empty = tmp_path / "versioned-empty.sqlite3"
    with _connection(versioned_empty) as connection:
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
        assert inspect_agent_database(connection).kind is DatabaseKind.CORRUPT

    known = tmp_path / "known.sqlite3"
    with _new_v2_connection(known) as connection:
        assert inspect_agent_database(connection).kind is DatabaseKind.KNOWN_V2
    with sqlite3.connect(known) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        apply_v2_schema(connection)
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    damaged = tmp_path / "damaged-v2.sqlite3"
    with _new_v2_connection(damaged) as connection:
        connection.execute("DROP TRIGGER agent_v2_attempts_finalize_once")
        assert inspect_agent_database(connection).kind is DatabaseKind.CORRUPT

    altered_trigger = tmp_path / "altered-trigger-v2.sqlite3"
    with _new_v2_connection(altered_trigger) as connection:
        connection.execute("DROP TRIGGER agent_v2_attempts_finalize_once")
        connection.execute(
            """
            CREATE TRIGGER agent_v2_attempts_finalize_once
            BEFORE UPDATE ON agent_v2_llm_attempts
            WHEN OLD.attempt_id IS NOT NEW.attempt_id
            BEGIN
                SELECT RAISE(ABORT, 'different literal');
            END
            """
        )
        assert inspect_agent_database(connection).kind is DatabaseKind.CORRUPT

    view = tmp_path / "view-v2.sqlite3"
    with _new_v2_connection(view) as connection:
        connection.execute("CREATE VIEW unexpected_view AS SELECT 1")
        assert inspect_agent_database(connection).kind is DatabaseKind.CORRUPT


def test_backup_is_read_only_create_only_and_does_not_copy_wal(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    connection = _connection(source)
    try:
        assert connection.execute("PRAGMA journal_mode = WAL").fetchone()[0].lower() == "wal"
        connection.execute("CREATE TABLE source_data (value TEXT)")
        connection.commit()
        connection.execute("INSERT INTO source_data VALUES ('kept')")
        connection.commit()
        assert source.with_name(source.name + "-wal").is_file()

        destination = tmp_path / "destination.sqlite3"
        backup_agent_database(source, destination)
        with sqlite3.connect(destination) as copied:
            assert copied.execute("SELECT value FROM source_data").fetchone() == ("kept",)
        assert not destination.with_name(destination.name + "-wal").exists()
        assert connection.execute("SELECT value FROM source_data").fetchone() == ("kept",)

        with pytest.raises(MigrationError):
            backup_agent_database(source, destination)
        symlink_destination = tmp_path / "symlink.sqlite3"
        symlink_destination.symlink_to(source)
        with pytest.raises(MigrationError):
            backup_agent_database(source, symlink_destination)
        with pytest.raises(MigrationError):
            backup_agent_database(tmp_path / "missing.sqlite3", tmp_path / "new.sqlite3")
    finally:
        connection.close()


def test_legacy_copy_upgrade_preserves_old_tables_and_payload(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    shutil.copy2(LEGACY_FIXTURE, source)
    destination = tmp_path / "destination.sqlite3"
    backup_agent_database(source, destination)

    with _connection(destination) as connection:
        assert inspect_agent_database(connection).kind is DatabaseKind.LEGACY0
        apply_v2_schema(connection)
        connection.commit()
        inspection = inspect_agent_database(connection)
        assert inspection.kind is DatabaseKind.KNOWN_V2
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert {"agent_runs", "agent_traces", "agent_snapshots"} <= tables
        assert {
            "legacy-success-0001",
            "legacy-failed-0001",
        } <= {row[0] for row in connection.execute("SELECT run_id FROM agent_runs")}

    with _connection(source) as connection:
        assert inspect_agent_database(connection).kind is DatabaseKind.LEGACY0
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_attempt_is_single_row_finalize_once_with_strict_ownership(tmp_path: Path) -> None:
    path = tmp_path / "v2.sqlite3"
    with _new_v2_connection(path) as connection:
        _seed_issue(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO agent_v2_llm_attempts
                    (attempt_id, run_id, issue_number, evidence_set_id, ordinal, request_json,
                     started_at, state, finished_at, analysis_json)
                VALUES ('attempt-terminal', 'run-1', 7, 'evidence-1', 1, '{}', 't2',
                        'success', 't3', '{}')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO agent_v2_issues
                    (run_id, issue_number, deterministic_state, selected_analysis_attempt_id)
                VALUES ('run-2', 8, 'pending', 'attempt-terminal')
                """
            )
        connection.execute(
            """
            INSERT INTO agent_v2_llm_attempts
                (attempt_id, run_id, issue_number, evidence_set_id, ordinal, request_json,
                 started_at, state)
            VALUES ('attempt-1', 'run-1', 7, 'evidence-1', 1, '{}', 't2', 'in_progress')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO agent_v2_llm_attempts
                    (attempt_id, run_id, issue_number, evidence_set_id, ordinal, request_json,
                     started_at, state)
                VALUES ('attempt-2', 'run-1', 7, 'evidence-1', 2, '{}', 't2', 'in_progress')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE agent_v2_llm_attempts SET request_json = '{"changed":true}'
                WHERE attempt_id = 'attempt-1'
                """
            )
        connection.execute(
            """
            UPDATE agent_v2_llm_attempts
            SET state = 'success', finished_at = 't3', analysis_json = '{}', reported_json = NULL,
                local_json = '{}'
            WHERE attempt_id = 'attempt-1'
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE agent_v2_llm_attempts
                SET state = 'failure', finished_at = 't4', error_json = '{}'
                WHERE attempt_id = 'attempt-1'
                """
            )
        connection.execute(
            """
            UPDATE agent_v2_issues SET selected_analysis_attempt_id = 'attempt-1'
            WHERE run_id = 'run-1' AND issue_number = 7
            """
        )
        connection.commit()


def test_evidence_seal_attempt_and_review_guards(tmp_path: Path) -> None:
    path = tmp_path / "v2.sqlite3"
    with _new_v2_connection(path) as connection:
        _seed_issue(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO agent_v2_evidence_items
                    (evidence_set_id, evidence_id, ordinal, file, requested_range, actual_range,
                     char_count, content, selection_kind, collector_protocol)
                VALUES ('evidence-1', 'E1', 1, 'a.py', '1-2', '1-2', 1, 'x', 'candidate', 'v2')
                """
            )
        # Attempt creation before a sealed evidence set is rejected by the database rule.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO agent_v2_llm_attempts
                    (attempt_id, run_id, issue_number, evidence_set_id, ordinal, request_json,
                     started_at, state)
                VALUES ('attempt-1', 'run-1', 7, 'missing', 1, '{}', 't2', 'in_progress')
                """
            )
        connection.execute(
            """
            INSERT INTO agent_v2_llm_attempts
                (attempt_id, run_id, issue_number, evidence_set_id, ordinal, request_json,
                 started_at, state)
            VALUES ('attempt-1', 'run-1', 7, 'evidence-1', 1, '{}', 't2', 'in_progress')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO agent_v2_reviews
                    (review_id, run_id, issue_number, evidence_set_id, selected_attempt_id,
                     principal_id, idempotency_key, operation, expected_review_version,
                     decision, payload_json, response_json, created_at)
                VALUES ('review-1', 'run-1', 7, 'evidence-1', NULL, 'p', 'k', 'review',
                        0, 'approved', '{}', '{}', 't3')
                """
            )
        connection.execute("""
            UPDATE agent_v2_llm_attempts
            SET state = 'failure', finished_at = 't3', error_json = '{}'
            WHERE attempt_id = 'attempt-1'
            """)
        connection.execute(
            """
            INSERT INTO agent_v2_reviews
                (review_id, run_id, issue_number, evidence_set_id, selected_attempt_id,
                 principal_id, idempotency_key, operation, expected_review_version,
                 decision, payload_json, response_json, created_at)
            VALUES ('review-1', 'run-1', 7, 'evidence-1', NULL, 'p', 'k', 'review',
                    0, 'approved', '{}', '{}', 't4')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM agent_v2_reviews WHERE review_id = 'review-1'")
        connection.commit()
        assert (
            connection.execute(
                "SELECT review_version FROM agent_v2_issues "
                "WHERE run_id = 'run-1' AND issue_number = 7"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("fault_at", ["ddl", "index", "version", "commit"])
def test_fault_injection_rolls_back_every_step(tmp_path: Path, fault_at: str) -> None:
    path = tmp_path / f"fault-{fault_at}.sqlite3"
    with _connection(path) as connection:
        with pytest.raises(MigrationError):
            apply_v2_schema(connection, fault_at=fault_at)
        assert inspect_agent_database(connection).kind is DatabaseKind.EMPTY
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0

    legacy_path = tmp_path / f"legacy-fault-{fault_at}.sqlite3"
    shutil.copy2(LEGACY_FIXTURE, legacy_path)
    with _connection(legacy_path) as connection:
        before = connection.execute("SELECT count(*) FROM agent_runs").fetchone()[0]
        with pytest.raises(MigrationError):
            apply_v2_schema(connection, fault_at=fault_at)
        assert inspect_agent_database(connection).kind is DatabaseKind.LEGACY0
        assert connection.execute("SELECT count(*) FROM agent_runs").fetchone()[0] == before


def test_existing_transaction_is_rejected_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "transaction.sqlite3"
    with _connection(path) as connection:
        connection.execute("BEGIN")
        with pytest.raises(MigrationError):
            apply_v2_schema(connection)
        connection.rollback()
        assert inspect_agent_database(connection).kind is DatabaseKind.EMPTY
