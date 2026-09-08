from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
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


def test_imported_legacy_tables_reject_all_writes_and_require_exact_guards(tmp_path: Path) -> None:
    destination = tmp_path / "destination.sqlite3"
    shutil.copy2(LEGACY_FIXTURE, destination)
    with _connection(destination) as connection:
        original = {
            table: connection.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("agent_runs", "agent_traces", "agent_snapshots")
        }
        apply_v2_schema(connection)
        for table, rows in original.items():
            for statement in (
                f"INSERT INTO {table} SELECT * FROM {table} LIMIT 1",
                f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} LIMIT 1",
                f"UPDATE {table} SET run_id = run_id",
                f"DELETE FROM {table}",
            ):
                with pytest.raises(sqlite3.IntegrityError, match="legacy history is read-only"):
                    connection.execute(statement)
                assert connection.execute(f"SELECT * FROM {table}").fetchall() == rows
        assert inspect_agent_database(connection).kind is DatabaseKind.KNOWN_V2
        connection.execute("DROP TRIGGER agent_runs_legacy_no_insert")
        assert inspect_agent_database(connection).kind is DatabaseKind.CORRUPT
        connection.execute(
            "CREATE TRIGGER agent_runs_legacy_no_insert BEFORE INSERT ON agent_runs "
            "BEGIN SELECT 1; END"
        )
        assert inspect_agent_database(connection).kind is DatabaseKind.CORRUPT


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


@pytest.mark.parametrize("recursive_triggers", [0, 1])
@pytest.mark.parametrize(
    "table, assignments",
    [
        ("agent_v2_runs", {"snapshot_json": '{"changed":true}'}),
        ("agent_v2_issues", {"evidence_set_id": None}),
        ("agent_v2_evidence_sets", {"sealed": 0, "sealed_at": None}),
        ("agent_v2_evidence_items", {"content": "changed"}),
        ("agent_v2_evidence_items", {"evidence_id": "another-id"}),
        ("agent_v2_llm_attempts", {"request_json": '{"changed":true}'}),
        ("agent_v2_llm_attempts", {"attempt_id": "another-active-id"}),
        ("agent_v2_reviews", {"expected_review_version": 1}),
        ("agent_v2_reviews", {"review_id": "another-id", "expected_review_version": 1}),
        ("agent_v2_traces", {"payload_json": '{"changed":true}'}),
        ("agent_v2_traces", {"trace_id": "another-id"}),
    ],
)
def test_replace_cannot_discard_protected_rows(
    tmp_path: Path, table: str, assignments: dict[str, object], recursive_triggers: int
) -> None:
    path = tmp_path / "replace.sqlite3"
    connection = _new_v2_connection(path)
    _seed_issue(connection)
    connection.execute("""
        INSERT INTO agent_v2_evidence_sets
            (evidence_set_id, run_id, issue_number, collection_context_json)
        VALUES ('unsealed', 'run-1', 7, '{}')
        """)
    connection.execute("""
        INSERT INTO agent_v2_evidence_items
            (evidence_set_id, evidence_id, ordinal, file, requested_range, actual_range,
             char_count, content, selection_kind, collector_protocol)
        VALUES ('unsealed', 'E1', 0, 'a.py', '1-2', '1-2', 1, 'x', 'candidate', 'v2')
        """)
    connection.execute("""
        INSERT INTO agent_v2_reviews
            (review_id, run_id, issue_number, evidence_set_id, principal_id, idempotency_key,
             operation, expected_review_version, decision, payload_json, response_json, created_at)
        VALUES ('review-1', 'run-1', 7, 'evidence-1', 'p', 'k', 'review', 0,
                'approved', '{}', '{}', 't2')
        """)
    connection.execute("""
        INSERT INTO agent_v2_llm_attempts
            (attempt_id, run_id, issue_number, evidence_set_id, ordinal, request_json,
             started_at, state)
        VALUES ('attempt-1', 'run-1', 7, 'evidence-1', 0, '{}', 't3', 'in_progress')
        """)
    connection.execute("""
        INSERT INTO agent_v2_traces VALUES ('trace-1', 'run-1', '{}', 't3')
        """)
    connection.commit()
    connection.close()
    # Protection must survive reopening with SQLite's default recursive triggers off.
    with _connection(path) as connection:
        connection.execute(f"PRAGMA recursive_triggers = {recursive_triggers}")
        if table == "agent_v2_reviews":
            connection.execute("""
                UPDATE agent_v2_llm_attempts
                SET state = 'failure', finished_at = 't4', error_json = '{}'
                """)
        cursor = connection.execute(f"SELECT rowid, * FROM {table} LIMIT 1")
        row = dict(
            zip((column[0] for column in cursor.description), cursor.fetchone(), strict=True)
        )
        # Most cases exercise declared keys, not the implicit rowid conflict.
        if not (table == "agent_v2_traces" and "trace_id" in assignments):
            row.pop("rowid")
        row.update(assignments)
        before = list(connection.iterdump())
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT OR REPLACE INTO {table} ({', '.join(row)}) "
                f"VALUES ({', '.join('?' for _ in row)})",
                tuple(row.values()),
            )
        assert list(connection.iterdump()) == before


@pytest.mark.parametrize("state", ["success", "failure", "unknown"])
def test_replace_cannot_reopen_terminal_attempt(tmp_path: Path, state: str) -> None:
    with _new_v2_connection(tmp_path / "terminal.sqlite3") as connection:
        _seed_issue(connection)
        insert = """
            INSERT OR REPLACE INTO agent_v2_llm_attempts
                (attempt_id, run_id, issue_number, evidence_set_id, ordinal, request_json,
                 started_at, state)
            VALUES ('attempt-1', 'run-1', 7, 'evidence-1', 0, '{}', 't2', 'in_progress')
            """
        connection.execute(insert)
        connection.execute(
            "UPDATE agent_v2_llm_attempts SET state = ?, finished_at = 't3', "
            "analysis_json = ?, error_json = ? WHERE attempt_id = 'attempt-1'",
            (state, "{}" if state == "success" else None, None if state == "success" else "{}"),
        )
        before = list(connection.iterdump())
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert)
        assert list(connection.iterdump()) == before


@pytest.mark.parametrize("recursive_triggers", [0, 1])
@pytest.mark.parametrize(
    "table", ["agent_v2_runs", "agent_v2_issues", "agent_v2_evidence_sets", "agent_v2_llm_attempts"]
)
def test_update_replace_cannot_delete_another_row(
    tmp_path: Path, table: str, recursive_triggers: int
) -> None:
    with _new_v2_connection(tmp_path / "update-replace.sqlite3") as connection:
        _seed_issue(connection)
        connection.execute(f"PRAGMA recursive_triggers = {recursive_triggers}")
        connection.execute("""
            INSERT INTO agent_v2_runs
                (run_id, snapshot_json, configuration_json, inputs_json, selection_json,
                 status, created_at, updated_at)
            VALUES ('run-2', '{}', '{}', '{}', '{}', 'RUNNING', 't0', 't0')
            """)
        connection.execute("""
            INSERT INTO agent_v2_issues (run_id, issue_number, deterministic_state)
            VALUES ('run-1', 8, 'pending')
            """)
        connection.execute("""
            INSERT INTO agent_v2_runs
                (run_id, snapshot_json, configuration_json, inputs_json, selection_json,
                 status, created_at, updated_at)
            VALUES ('run-3', '{}', '{}', '{}', '{}', 'RUNNING', 't0', 't0')
            """)
        connection.execute("""
            INSERT INTO agent_v2_evidence_sets
                (evidence_set_id, run_id, issue_number, collection_context_json)
            VALUES ('unsealed', 'run-1', 7, '{}')
            """)
        insert = """
            INSERT INTO agent_v2_llm_attempts
                (attempt_id, run_id, issue_number, evidence_set_id, ordinal, request_json,
                 started_at, state)
            VALUES (?, 'run-1', 7, 'evidence-1', ?, '{}', 't2', 'in_progress')
            """
        connection.execute(insert, ("attempt-1", 0))
        connection.execute("""
            UPDATE agent_v2_llm_attempts
            SET state = 'failure', finished_at = 't3', error_json = '{}'
            """)
        connection.execute(insert, ("attempt-2", 1))
        connection.commit()
        extra = {
            "agent_v2_runs": "",
            "agent_v2_issues": "",
            "agent_v2_evidence_sets": ", sealed = 1, sealed_at = 't4'",
            "agent_v2_llm_attempts": ", state = 'failure', finished_at = 't4', error_json = '{}'",
        }[table]
        before = list(connection.iterdump())
        target_row, source_row = (2, 3) if table == "agent_v2_runs" else (1, 2)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"UPDATE OR REPLACE {table} SET rowid = {target_row}{extra} "
                f"WHERE rowid = {source_row}"
            )
        assert list(connection.iterdump()) == before


def test_review_version_only_advances_with_an_appended_review(tmp_path: Path) -> None:
    with _new_v2_connection(tmp_path / "review.sqlite3") as connection:
        _seed_issue(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("""
                INSERT INTO agent_v2_issues
                    (run_id, issue_number, deterministic_state, review_version)
                VALUES ('run-1', 8, 'pending', 3)
                """)
        insert = """
            INSERT INTO agent_v2_reviews
                (review_id, run_id, issue_number, evidence_set_id, principal_id, idempotency_key,
                 operation, expected_review_version, decision, payload_json, response_json,
                 created_at)
            VALUES (?, 'run-1', 7, 'evidence-1', 'p', ?, 'review', ?, ?, '{}', '{}', 't2')
            """
        for version in (0, 1):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("UPDATE agent_v2_issues SET review_version = review_version + 1")
            connection.execute(insert, (f"r{version}", f"k{version}", version, "approved"))
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("UPDATE agent_v2_issues SET review_version = ?", (version,))
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert, ("stale", "stale", version, "rejected"))
            assert connection.execute("SELECT review_version FROM agent_v2_issues").fetchone() == (
                version + 1,
            )
        # A failed transaction rolls back the appended review and version together.
        connection.rollback()
        assert connection.execute("SELECT review_version FROM agent_v2_issues").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM agent_v2_reviews").fetchone() == (0,)


@pytest.mark.parametrize("kind", [DatabaseKind.EMPTY, DatabaseKind.LEGACY0, DatabaseKind.KNOWN_V2])
def test_analyzed_database_remains_recognizable_and_migratable(tmp_path: Path, kind: DatabaseKind):
    path = tmp_path / "analyzed.sqlite3"
    if kind is DatabaseKind.LEGACY0:
        shutil.copy2(LEGACY_FIXTURE, path)
    with _connection(path) as connection:
        if kind is DatabaseKind.KNOWN_V2:
            apply_v2_schema(connection)
            _seed_issue(connection)
        connection.execute("ANALYZE")
        connection.commit()
        before = list(connection.iterdump())
        assert inspect_agent_database(connection).kind is kind
        assert list(connection.iterdump()) == before
        apply_v2_schema(connection)
        assert inspect_agent_database(connection).kind is DatabaseKind.KNOWN_V2
        connection.execute("CREATE TABLE sqliteXunexpected (value TEXT)")
        assert inspect_agent_database(connection).kind is DatabaseKind.CORRUPT


def test_malformed_statistics_table_is_not_accepted(tmp_path: Path) -> None:
    path = tmp_path / "bad-stat.sqlite3"
    with _new_v2_connection(path) as connection:
        connection.execute("ANALYZE")
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute("""
            UPDATE sqlite_master SET sql = 'CREATE TABLE sqlite_stat1(unexpected TEXT)'
            WHERE name = 'sqlite_stat1'
            """)
        connection.execute("PRAGMA writable_schema = OFF")
    with _connection(path) as connection:
        assert inspect_agent_database(connection).kind is DatabaseKind.CORRUPT


@pytest.mark.parametrize(
    "boundary",
    ["temporary_file", "before_connect", "after_connect", "before_publish", "swap_and_restore"],
)
@pytest.mark.parametrize("replacement_kind", ["file", "symlink"])
def test_backup_rejects_source_identity_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str, replacement_kind: str
) -> None:
    source = tmp_path / "source.sqlite3"
    other = tmp_path / "other.sqlite3"
    displaced = tmp_path / "original.sqlite3"
    destination = tmp_path / "backup.sqlite3"
    for path, marker in ((source, "original"), (other, "replacement")):
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.execute("INSERT INTO marker VALUES (?)", (marker,))
        connection.commit()
        connection.close()
    original_bytes = source.read_bytes()
    other_bytes = other.read_bytes()

    def replace_source() -> None:
        source.rename(displaced)
        if replacement_kind == "file":
            shutil.copy2(other, source)
        else:
            source.symlink_to(other)

    real_connect = sqlite3.connect
    real_mkstemp = tempfile.mkstemp
    real_fsync = os.fsync

    def connect(database, *args, **kwargs):
        if isinstance(database, str) and database.startswith("file:"):
            if boundary in {"before_connect", "swap_and_restore"}:
                replace_source()
            connection = real_connect(database, *args, **kwargs)
            if boundary == "swap_and_restore":
                source.rename(tmp_path / "replacement-at-open.sqlite3")
                displaced.rename(source)
            if boundary == "after_connect":
                replace_source()
            return connection
        return real_connect(database, *args, **kwargs)

    def mkstemp(*args, **kwargs):
        result = real_mkstemp(*args, **kwargs)
        if boundary == "temporary_file":
            replace_source()
        return result

    def fsync(fd):
        real_fsync(fd)
        if boundary == "before_publish":
            replace_source()

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(MigrationError, match="source.*changed"):
        backup_agent_database(source, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".backup.sqlite3.*"))
    assert (source if boundary == "swap_and_restore" else displaced).read_bytes() == original_bytes
    assert other.read_bytes() == other_bytes


@pytest.mark.parametrize("via_symlink", [False, True])
def test_backup_rejects_parent_directory_swap_and_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, via_symlink: bool
) -> None:
    current = tmp_path / "current"
    other = tmp_path / "other"
    saved = tmp_path / "saved"
    for directory, marker in ((current, "original"), (other, "wrong")):
        (directory / "sub").mkdir(parents=True)
        connection = sqlite3.connect(directory / "sub" / "source.sqlite3")
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.execute("INSERT INTO marker VALUES (?)", (marker,))
        connection.commit()
        connection.close()
    if via_symlink:
        alias = tmp_path / "alias"
        alias.symlink_to(current / "sub", target_is_directory=True)
        source = alias / "source.sqlite3"
    else:
        source = current / "sub" / "source.sqlite3"
    original_stat = source.stat()
    destination = tmp_path / "backup.sqlite3"
    real_connect = sqlite3.connect

    def connect(database, *args, **kwargs):
        if isinstance(database, str) and database.startswith("file:"):
            current.rename(saved)
            other.rename(current)
            connection = real_connect(database, *args, **kwargs)
            connection.execute("BEGIN")
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
            connection.rollback()
            current.rename(other)
            saved.rename(current)
            return connection
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(MigrationError, match="source.*changed"):
        backup_agent_database(source, destination)
    assert source.stat() == original_stat
    assert not destination.exists()
    assert not list(tmp_path.glob(".backup.sqlite3.*"))
    with real_connect(source) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone() == ("original",)


def test_backup_missing_wal_shm_rejects_then_retries_when_stable(tmp_path: Path) -> None:
    live = tmp_path / "live.sqlite3"
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "backup.sqlite3"
    writer = sqlite3.connect(live)
    try:
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("CREATE TABLE marker (value TEXT)")
        writer.execute("INSERT INTO marker VALUES ('wal-data')")
        writer.commit()
        # A recoverable WAL database whose shared-memory index is not yet present.
        shutil.copy2(live, source)
        shutil.copy2(Path(f"{live}-wal"), Path(f"{source}-wal"))
    finally:
        writer.close()
    source_bytes = source.read_bytes()
    wal_bytes = Path(f"{source}-wal").read_bytes()
    with pytest.raises(MigrationError, match="source directory identity changed"):
        backup_agent_database(source, destination)
    assert source.read_bytes() == source_bytes
    assert Path(f"{source}-wal").read_bytes() == wal_bytes
    assert not destination.exists()
    assert not list(tmp_path.glob(".backup.sqlite3.*"))
    backup_agent_database(source, destination)
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone() == ("wal-data",)


def test_backup_rejects_canonical_path_resolving_to_another_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = tmp_path / "current"
    other = tmp_path / "other"
    for directory, marker in ((current, "original"), (other, "wrong")):
        directory.mkdir()
        connection = sqlite3.connect(directory / "source.sqlite3")
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.execute("INSERT INTO marker VALUES (?)", (marker,))
        connection.commit()
        connection.close()
    alias = tmp_path / "alias"
    alias.symlink_to(current, target_is_directory=True)
    source = alias / "source.sqlite3"
    destination = tmp_path / "backup.sqlite3"
    original_bytes = source.read_bytes()
    real_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == source:
            alias.unlink()
            alias.symlink_to(other, target_is_directory=True)
            resolved = real_resolve(path, *args, **kwargs)
            alias.unlink()
            alias.symlink_to(current, target_is_directory=True)
            return resolved
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(MigrationError, match="source.*changed"):
        backup_agent_database(source, destination)
    assert source.read_bytes() == original_bytes
    assert not destination.exists()
    assert not list(tmp_path.glob(".backup.sqlite3.*"))
