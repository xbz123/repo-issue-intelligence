"""SQLite schema inspection, backup, and opt-in Protocol v2 preparation.

This module is deliberately independent from :mod:`agent_store`.  PR2A supplies
the database kernel only; no default Store or CLI path calls it.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path


class DatabaseKind(StrEnum):
    EMPTY = "empty"
    LEGACY0 = "legacy0"
    KNOWN_V2 = "knownv2"
    UNKNOWN = "unknown"
    CORRUPT = "corrupt"


@dataclass(frozen=True)
class DatabaseInspection:
    kind: DatabaseKind
    user_version: int | None
    tables: tuple[str, ...]
    problems: tuple[str, ...] = ()

    @property
    def state(self) -> str:
        """Return the portable string form used by dry-run/reporting callers."""

        return self.kind.value

    @property
    def supported(self) -> bool:
        return self.kind in {DatabaseKind.EMPTY, DatabaseKind.LEGACY0, DatabaseKind.KNOWN_V2}


@dataclass(frozen=True)
class MigrationPlan:
    inspection: DatabaseInspection
    action: str


class MigrationError(ValueError):
    """A migration or destination safety check failed."""


class MigrationFault(MigrationError):
    """A deliberate fault-injection failure used by the PR2A tests."""


V2_TABLES = (
    "agent_v2_runs",
    "agent_v2_issues",
    "agent_v2_evidence_sets",
    "agent_v2_evidence_items",
    "agent_v2_llm_attempts",
    "agent_v2_reviews",
    "agent_v2_traces",
)
LEGACY_TABLES = ("agent_runs", "agent_traces", "agent_snapshots")
_STATISTICS_TABLE_SQL = {
    "sqlite_stat1": "CREATE TABLE sqlite_stat1(tbl,idx,stat)",
    "sqlite_stat4": "CREATE TABLE sqlite_stat4(tbl,idx,neq,nlt,ndlt,sample)",
}
_ALLOWED_INTERNAL_TABLES = {"sqlite_sequence", *_STATISTICS_TABLE_SQL}
_V2_VERSION = 2


# This tuple is the sole V2 DDL source.  Expected schema metadata is derived by
# applying these statements to a private memory database, rather than copied by
# hand into a second column/FK/index manifest.
_V2_DDL: tuple[tuple[str, str], ...] = (
    (
        "ddl",
        """
        CREATE TABLE agent_v2_runs (
            run_id TEXT NOT NULL PRIMARY KEY,
            parent_run_id TEXT,
            snapshot_json TEXT NOT NULL CHECK (
                json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'
            ),
            configuration_json TEXT NOT NULL CHECK (
                json_valid(configuration_json) AND json_type(configuration_json) = 'object'
            ),
            inputs_json TEXT NOT NULL CHECK (
                json_valid(inputs_json) AND json_type(inputs_json) = 'object'
            ),
            selection_json TEXT NOT NULL CHECK (
                json_valid(selection_json) AND json_type(selection_json) = 'object'
            ),
            status TEXT NOT NULL CHECK (
                status IN (
                    'RUNNING', 'INTERRUPTED', 'AWAITING_REVIEW', 'PARTIALLY_REVIEWED',
                    'REVIEW_COMPLETED', 'FAILED'
                )
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (parent_run_id) REFERENCES agent_v2_runs(run_id)
        )
        """,
    ),
    (
        "ddl",
        """
        CREATE TABLE agent_v2_issues (
            run_id TEXT NOT NULL,
            issue_number INTEGER NOT NULL CHECK (issue_number > 0),
            deterministic_state TEXT NOT NULL CHECK (
                deterministic_state IN ('pending', 'running', 'succeeded', 'failed')
            ),
            deterministic_report_json TEXT CHECK (
                deterministic_report_json IS NULL OR
                (
                    json_valid(deterministic_report_json)
                    AND json_type(deterministic_report_json) = 'object'
                )
            ),
            evidence_set_id TEXT,
            selected_analysis_attempt_id TEXT,
            review_version INTEGER NOT NULL DEFAULT 0 CHECK (review_version >= 0),
            PRIMARY KEY (run_id, issue_number),
            FOREIGN KEY (run_id) REFERENCES agent_v2_runs(run_id),
            FOREIGN KEY (evidence_set_id, run_id, issue_number)
                REFERENCES agent_v2_evidence_sets(evidence_set_id, run_id, issue_number)
                DEFERRABLE INITIALLY DEFERRED,
            FOREIGN KEY (selected_analysis_attempt_id, run_id, issue_number)
                REFERENCES agent_v2_llm_attempts(attempt_id, run_id, issue_number)
                DEFERRABLE INITIALLY DEFERRED
        )
        """,
    ),
    (
        "ddl",
        """
        CREATE TABLE agent_v2_evidence_sets (
            evidence_set_id TEXT NOT NULL PRIMARY KEY,
            run_id TEXT NOT NULL,
            issue_number INTEGER NOT NULL CHECK (issue_number > 0),
            collection_context_json TEXT NOT NULL CHECK (
                json_valid(collection_context_json)
                AND json_type(collection_context_json) = 'object'
            ),
            sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0, 1)),
            sealed_at TEXT,
            UNIQUE (evidence_set_id, run_id, issue_number),
            CHECK ((sealed = 0 AND sealed_at IS NULL) OR (sealed = 1 AND sealed_at IS NOT NULL)),
            FOREIGN KEY (run_id, issue_number)
                REFERENCES agent_v2_issues(run_id, issue_number)
                DEFERRABLE INITIALLY DEFERRED
        )
        """,
    ),
    (
        "ddl",
        """
        CREATE TABLE agent_v2_evidence_items (
            evidence_set_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
            candidate_rank INTEGER CHECK (candidate_rank IS NULL OR candidate_rank >= 0),
            selection_kind TEXT NOT NULL,
            file TEXT NOT NULL,
            symbol TEXT,
            requested_range TEXT NOT NULL,
            actual_range TEXT NOT NULL,
            truncation_reason TEXT,
            char_count INTEGER NOT NULL CHECK (char_count >= 0),
            content TEXT NOT NULL,
            collector_protocol TEXT NOT NULL,
            PRIMARY KEY (evidence_set_id, evidence_id),
            UNIQUE (evidence_set_id, ordinal),
            FOREIGN KEY (evidence_set_id) REFERENCES agent_v2_evidence_sets(evidence_set_id)
        )
        """,
    ),
    (
        "ddl",
        """
        CREATE TABLE agent_v2_llm_attempts (
            attempt_id TEXT NOT NULL PRIMARY KEY,
            run_id TEXT NOT NULL,
            issue_number INTEGER NOT NULL CHECK (issue_number > 0),
            evidence_set_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
            request_json TEXT NOT NULL CHECK (
                json_valid(request_json) AND json_type(request_json) = 'object'
            ),
            started_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('in_progress', 'success', 'failure', 'unknown')),
            finished_at TEXT,
            analysis_json TEXT CHECK (
                analysis_json IS NULL OR (
                    json_valid(analysis_json) AND json_type(analysis_json) = 'object'
                )
            ),
            error_json TEXT CHECK (
                error_json IS NULL OR (json_valid(error_json) AND json_type(error_json) = 'object')
            ),
            reported_json TEXT CHECK (
                reported_json IS NULL OR (
                    json_valid(reported_json) AND json_type(reported_json) = 'object'
                )
            ),
            local_json TEXT CHECK (
                local_json IS NULL OR (json_valid(local_json) AND json_type(local_json) = 'object')
            ),
            UNIQUE (attempt_id, run_id, issue_number),
            CHECK (
                (
                    state = 'in_progress' AND finished_at IS NULL AND analysis_json IS NULL
                    AND error_json IS NULL AND reported_json IS NULL AND local_json IS NULL
                )
                OR
                (
                    state = 'success' AND finished_at IS NOT NULL AND analysis_json IS NOT NULL
                    AND error_json IS NULL
                )
                OR
                (
                    state IN ('failure', 'unknown') AND finished_at IS NOT NULL
                    AND analysis_json IS NULL AND error_json IS NOT NULL
                )
            ),
            FOREIGN KEY (run_id, issue_number)
                REFERENCES agent_v2_issues(run_id, issue_number)
                DEFERRABLE INITIALLY DEFERRED,
            FOREIGN KEY (evidence_set_id, run_id, issue_number)
                REFERENCES agent_v2_evidence_sets(evidence_set_id, run_id, issue_number)
                DEFERRABLE INITIALLY DEFERRED
        )
        """,
    ),
    (
        "ddl",
        """
        CREATE TABLE agent_v2_reviews (
            review_id TEXT NOT NULL PRIMARY KEY,
            run_id TEXT NOT NULL,
            issue_number INTEGER NOT NULL CHECK (issue_number > 0),
            evidence_set_id TEXT,
            selected_attempt_id TEXT,
            principal_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            operation TEXT NOT NULL,
            expected_review_version INTEGER NOT NULL CHECK (expected_review_version >= 0),
            decision TEXT NOT NULL CHECK (
                decision IN ('approved', 'rejected', 'needs_information')
            ),
            payload_json TEXT NOT NULL CHECK (
                json_valid(payload_json) AND json_type(payload_json) = 'object'
            ),
            response_json TEXT NOT NULL CHECK (
                json_valid(response_json) AND json_type(response_json) = 'object'
            ),
            created_at TEXT NOT NULL,
            UNIQUE (run_id, issue_number, principal_id, idempotency_key, operation),
            FOREIGN KEY (run_id, issue_number)
                REFERENCES agent_v2_issues(run_id, issue_number)
                DEFERRABLE INITIALLY DEFERRED,
            FOREIGN KEY (evidence_set_id, run_id, issue_number)
                REFERENCES agent_v2_evidence_sets(evidence_set_id, run_id, issue_number)
                DEFERRABLE INITIALLY DEFERRED,
            FOREIGN KEY (selected_attempt_id, run_id, issue_number)
                REFERENCES agent_v2_llm_attempts(attempt_id, run_id, issue_number)
                DEFERRABLE INITIALLY DEFERRED
        )
        """,
    ),
    (
        "ddl",
        """
        CREATE TABLE agent_v2_traces (
            trace_id TEXT NOT NULL PRIMARY KEY,
            run_id TEXT NOT NULL,
            payload_json TEXT NOT NULL CHECK (
                json_valid(payload_json) AND json_type(payload_json) = 'object'
            ),
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES agent_v2_runs(run_id)
        )
        """,
    ),
    (
        "index",
        """
        CREATE UNIQUE INDEX one_active_attempt_per_issue
        ON agent_v2_llm_attempts(run_id, issue_number)
        WHERE state = 'in_progress'
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_runs_immutable_inputs
        BEFORE UPDATE ON agent_v2_runs
        WHEN OLD.rowid IS NOT NEW.rowid
          OR OLD.run_id IS NOT NEW.run_id
          OR OLD.parent_run_id IS NOT NEW.parent_run_id
          OR OLD.snapshot_json IS NOT NEW.snapshot_json
          OR OLD.configuration_json IS NOT NEW.configuration_json
          OR OLD.inputs_json IS NOT NEW.inputs_json
          OR OLD.selection_json IS NOT NEW.selection_json
        BEGIN
            SELECT RAISE(ABORT, 'agent_v2 run inputs are immutable');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_runs_no_delete
        BEFORE DELETE ON agent_v2_runs
        BEGIN
            SELECT RAISE(ABORT, 'agent_v2 runs are retained');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_issues_immutable_fields
        BEFORE UPDATE ON agent_v2_issues
        WHEN OLD.rowid IS NOT NEW.rowid
          OR OLD.run_id IS NOT NEW.run_id
          OR OLD.issue_number IS NOT NEW.issue_number
          OR (OLD.deterministic_report_json IS NOT NULL
              AND OLD.deterministic_report_json IS NOT NEW.deterministic_report_json)
          OR (OLD.evidence_set_id IS NOT NULL AND OLD.evidence_set_id IS NOT NEW.evidence_set_id)
          OR (OLD.selected_analysis_attempt_id IS NOT NULL
              AND OLD.selected_analysis_attempt_id IS NOT NEW.selected_analysis_attempt_id)
        BEGIN
            SELECT RAISE(ABORT, 'agent_v2 issue identity and committed fields are immutable');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_issues_selected_attempt_insert_guard
        BEFORE INSERT ON agent_v2_issues
        WHEN NEW.selected_analysis_attempt_id IS NOT NULL
        BEGIN
            SELECT RAISE(ABORT, 'initial selected analysis must be NULL');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_issues_review_version_insert_guard
        BEFORE INSERT ON agent_v2_issues
        WHEN NEW.review_version <> 0
        BEGIN
            SELECT RAISE(ABORT, 'initial review version must be zero');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_issues_review_version_guard
        BEFORE UPDATE OF review_version ON agent_v2_issues
        WHEN NEW.review_version IS NOT OLD.review_version AND (
            NEW.review_version <> OLD.review_version + 1 OR NOT EXISTS (
                SELECT 1 FROM agent_v2_reviews
                WHERE run_id = OLD.run_id AND issue_number = OLD.issue_number
                  AND expected_review_version = OLD.review_version
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'review version advances only with an appended review');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_issues_selected_attempt_guard
        BEFORE UPDATE OF selected_analysis_attempt_id ON agent_v2_issues
        WHEN NEW.selected_analysis_attempt_id IS NOT NULL
         AND NOT EXISTS (
             SELECT 1 FROM agent_v2_llm_attempts AS attempt
             WHERE attempt.attempt_id = NEW.selected_analysis_attempt_id
               AND attempt.run_id = NEW.run_id
               AND attempt.issue_number = NEW.issue_number
               AND attempt.state = 'success'
               AND attempt.evidence_set_id IS NEW.evidence_set_id
         )
        BEGIN
            SELECT RAISE(ABORT, 'selected analysis must be a successful same-issue attempt');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_issues_no_delete
        BEFORE DELETE ON agent_v2_issues
        BEGIN
            SELECT RAISE(ABORT, 'agent_v2 issues are retained');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_evidence_sets_seal_guard
        BEFORE UPDATE ON agent_v2_evidence_sets
        WHEN OLD.rowid IS NOT NEW.rowid
          OR OLD.evidence_set_id IS NOT NEW.evidence_set_id
          OR OLD.run_id IS NOT NEW.run_id
          OR OLD.issue_number IS NOT NEW.issue_number
          OR OLD.collection_context_json IS NOT NEW.collection_context_json
          OR OLD.sealed = 1
          OR NOT (OLD.sealed = 0 AND NEW.sealed = 1 AND NEW.sealed_at IS NOT NULL)
        BEGIN
            SELECT RAISE(ABORT, 'evidence sets are immutable after collection and seal');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_evidence_sets_no_delete
        BEFORE DELETE ON agent_v2_evidence_sets
        BEGIN
            SELECT RAISE(ABORT, 'agent_v2 evidence sets are retained');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_evidence_items_unsealed_insert
        BEFORE INSERT ON agent_v2_evidence_items
        WHEN EXISTS (
            SELECT 1 FROM agent_v2_evidence_sets
            WHERE evidence_set_id = NEW.evidence_set_id AND sealed = 1
        )
        BEGIN
            SELECT RAISE(ABORT, 'evidence items cannot be added after seal');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_evidence_items_no_update
        BEFORE UPDATE ON agent_v2_evidence_items
        BEGIN
            SELECT RAISE(ABORT, 'evidence items are immutable');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_evidence_items_no_delete
        BEFORE DELETE ON agent_v2_evidence_items
        BEGIN
            SELECT RAISE(ABORT, 'agent_v2 evidence items are retained');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_attempts_in_progress_insert
        BEFORE INSERT ON agent_v2_llm_attempts
        WHEN NEW.state <> 'in_progress'
        BEGIN
            SELECT RAISE(ABORT, 'attempts must start in_progress');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_attempts_sealed_insert
        BEFORE INSERT ON agent_v2_llm_attempts
        WHEN NOT EXISTS (
            SELECT 1 FROM agent_v2_evidence_sets
            WHERE evidence_set_id = NEW.evidence_set_id
              AND run_id = NEW.run_id
              AND issue_number = NEW.issue_number
              AND sealed = 1
        )
        BEGIN
            SELECT RAISE(ABORT, 'attempts require a sealed same-issue evidence set');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_attempts_finalize_once
        BEFORE UPDATE ON agent_v2_llm_attempts
        WHEN OLD.rowid IS NOT NEW.rowid
          OR OLD.attempt_id IS NOT NEW.attempt_id
          OR OLD.run_id IS NOT NEW.run_id
          OR OLD.issue_number IS NOT NEW.issue_number
          OR OLD.evidence_set_id IS NOT NEW.evidence_set_id
          OR OLD.ordinal IS NOT NEW.ordinal
          OR OLD.request_json IS NOT NEW.request_json
          OR OLD.started_at IS NOT NEW.started_at
          OR OLD.state <> 'in_progress'
          OR NEW.state = 'in_progress'
        BEGIN
            SELECT RAISE(ABORT, 'attempt request and terminal state are immutable');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_attempts_no_delete
        BEFORE DELETE ON agent_v2_llm_attempts
        BEGIN
            SELECT RAISE(ABORT, 'agent_v2 attempts are retained');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_reviews_binding_guard
        BEFORE INSERT ON agent_v2_reviews
        WHEN EXISTS (
            SELECT 1 FROM agent_v2_llm_attempts
            WHERE run_id = NEW.run_id AND issue_number = NEW.issue_number AND state = 'in_progress'
        )
         OR NOT EXISTS (
             SELECT 1 FROM agent_v2_issues AS issue
             WHERE issue.run_id = NEW.run_id AND issue.issue_number = NEW.issue_number
               AND issue.evidence_set_id IS NEW.evidence_set_id
               AND issue.selected_analysis_attempt_id IS NEW.selected_attempt_id
               AND issue.review_version = NEW.expected_review_version
         )
        BEGIN
            SELECT RAISE(ABORT, 'review target/version is not current or has an active attempt');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_reviews_increment_version
        AFTER INSERT ON agent_v2_reviews
        BEGIN
            UPDATE agent_v2_issues
            SET review_version = review_version + 1
            WHERE run_id = NEW.run_id AND issue_number = NEW.issue_number;
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_reviews_no_update
        BEFORE UPDATE ON agent_v2_reviews
        BEGIN
            SELECT RAISE(ABORT, 'reviews are append-only');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_reviews_no_delete
        BEFORE DELETE ON agent_v2_reviews
        BEGIN
            SELECT RAISE(ABORT, 'agent_v2 reviews are retained');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_traces_no_update
        BEFORE UPDATE ON agent_v2_traces
        BEGIN
            SELECT RAISE(ABORT, 'traces are immutable');
        END
        """,
    ),
    (
        "ddl",
        """
        CREATE TRIGGER agent_v2_traces_no_delete
        BEFORE DELETE ON agent_v2_traces
        BEGIN
            SELECT RAISE(ABORT, 'agent_v2 traces are retained');
        END
        """,
    ),
)


# REPLACE's implicit DELETE does not fire delete triggers on default connections.
# Guard every conflicting identity before SQLite can delete the old row, including
# the implicit rowid and the non-primary unique keys.
_V2_DDL += tuple(
    (
        "ddl",
        f"""
        CREATE TRIGGER {table}_no_replace
        BEFORE INSERT ON {table}
        WHEN EXISTS (SELECT 1 FROM {table} WHERE rowid = NEW.rowid OR ({conflict}))
        BEGIN
            SELECT RAISE(ABORT, 'agent_v2 existing rows cannot be replaced');
        END
        """,
    )
    for table, conflict in (
        ("agent_v2_runs", "run_id = NEW.run_id"),
        ("agent_v2_issues", "run_id = NEW.run_id AND issue_number = NEW.issue_number"),
        ("agent_v2_evidence_sets", "evidence_set_id = NEW.evidence_set_id"),
        (
            "agent_v2_evidence_items",
            "evidence_set_id = NEW.evidence_set_id "
            "AND (evidence_id = NEW.evidence_id OR ordinal = NEW.ordinal)",
        ),
        (
            "agent_v2_llm_attempts",
            "attempt_id = NEW.attempt_id OR "
            "(NEW.state = 'in_progress' AND state = 'in_progress' "
            "AND run_id = NEW.run_id AND issue_number = NEW.issue_number)",
        ),
        (
            "agent_v2_reviews",
            "review_id = NEW.review_id OR (run_id = NEW.run_id "
            "AND issue_number = NEW.issue_number AND principal_id = NEW.principal_id "
            "AND idempotency_key = NEW.idempotency_key AND operation = NEW.operation)",
        ),
        ("agent_v2_traces", "trace_id = NEW.trace_id"),
    )
)


# AgentStore's legacy tables are a compatibility source, not a migration target
# schema.  This small shape is only used to recognize a source without importing
# or changing AgentStore's initialization path.
_LEGACY_COLUMNS = {
    "agent_runs": (
        ("run_id", "TEXT", 0, None, 1),
        ("status", "TEXT", 1, None, 0),
        ("created_at", "TEXT", 1, None, 0),
        ("updated_at", "TEXT", 1, None, 0),
        ("payload", "TEXT", 1, None, 0),
    ),
    "agent_traces": (
        ("id", "INTEGER", 0, None, 1),
        ("run_id", "TEXT", 1, None, 0),
        ("node_name", "TEXT", 1, None, 0),
        ("status", "TEXT", 1, None, 0),
        ("attempt", "INTEGER", 1, None, 0),
        ("elapsed_ms", "REAL", 1, None, 0),
        ("payload", "TEXT", 1, None, 0),
    ),
    "agent_snapshots": (
        ("id", "INTEGER", 0, None, 1),
        ("run_id", "TEXT", 1, None, 0),
        ("node_name", "TEXT", 1, None, 0),
        ("created_at", "TEXT", 1, None, 0),
        ("state_json", "TEXT", 1, None, 0),
    ),
}
_LEGACY_TABLE_SQL = {
    "agent_runs": """CREATE TABLE agent_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )""",
    "agent_traces": """CREATE TABLE agent_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    node_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    elapsed_ms REAL NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
                )""",
    "agent_snapshots": """CREATE TABLE agent_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    node_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
                )""",
}

# Imported history stays byte-for-byte intact. INSERT guards also reject REPLACE
# before its implicit DELETE, even when recursive_triggers is disabled.
_LEGACY_PROTECTION_DDL = tuple(
    (
        "ddl",
        f"""
        CREATE TRIGGER {table}_legacy_no_{operation.lower()}
        BEFORE {operation} ON {table}
        BEGIN
            SELECT RAISE(ABORT, 'legacy history is read-only');
        END
        """,
    )
    for table in LEGACY_TABLES
    for operation in ("INSERT", "UPDATE", "DELETE")
)


def _normalise_sql(sql: str | None) -> str | None:
    """Keep sqlite_master SQL literal-sensitive while trimming outer padding."""

    if sql is None:
        return None
    return sql.strip()


def _v2_object_names(connection: sqlite3.Connection) -> tuple[set[str], set[str], set[str]]:
    rows = connection.execute(
        "SELECT type, name FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
    ).fetchall()
    tables = {name for kind, name in rows if kind == "table"}
    indexes = {name for kind, name in rows if kind == "index"}
    triggers = {name for kind, name in rows if kind == "trigger"}
    return tables, indexes, triggers


def _unexpected_user_objects(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    return [
        (kind, name)
        for kind, name in connection.execute(
            "SELECT type, name FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
        ).fetchall()
        if kind not in {"table", "index", "trigger"}
    ]


def _v2_signature(connection: sqlite3.Connection) -> tuple[object, ...]:
    tables, indexes, triggers = _v2_object_names(connection)
    table_signature: list[object] = []
    for table in V2_TABLES:
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        columns = tuple(
            tuple(row) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        )
        foreign_keys = tuple(
            sorted(tuple(row) for row in connection.execute(f"PRAGMA foreign_key_list({table})"))
        )
        index_list = tuple(
            sorted(
                (row[1], row[2], row[3], row[4])
                for row in connection.execute(f"PRAGMA index_list({table})").fetchall()
            )
        )
        table_signature.append(
            (
                table,
                _normalise_sql(table_sql[0] if table_sql else None),
                columns,
                foreign_keys,
                index_list,
            )
        )

    index_signature = tuple(
        sorted(
            (
                name,
                _normalise_sql(
                    connection.execute(
                        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)
                    ).fetchone()[0]
                ),
            )
            for name in indexes
        )
    )
    trigger_signature = tuple(
        sorted(
            (
                name,
                _normalise_sql(
                    connection.execute(
                        "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", (name,)
                    ).fetchone()[0]
                ),
            )
            for name in triggers
        )
    )
    return tuple(table_signature), index_signature, trigger_signature


@lru_cache(maxsize=2)
def _expected_v2_signature(allow_legacy: bool = False) -> tuple[object, ...]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        _execute_ddl(connection, _V2_DDL, fault_at=None)
        if allow_legacy:
            for statement in _LEGACY_TABLE_SQL.values():
                connection.execute(statement)
            _execute_ddl(connection, _LEGACY_PROTECTION_DDL, fault_at=None)
        return _v2_signature(connection)
    finally:
        connection.close()


def _execute_ddl(
    connection: sqlite3.Connection,
    statements: tuple[tuple[str, str], ...],
    *,
    fault_at: str | Callable[[str, int, str], object] | None,
) -> None:
    for index, (phase, statement) in enumerate(statements):
        if phase == "index":
            _inject_fault(fault_at, "index", index, statement)
        elif phase == "ddl":
            _inject_fault(fault_at, "ddl", index, statement)
        connection.execute(statement)


def _inject_fault(
    fault_at: str | Callable[[str, int, str], object] | None,
    phase: str,
    statement_index: int,
    statement: str,
) -> None:
    if fault_at is None:
        return
    if isinstance(fault_at, str):
        if fault_at == phase:
            raise MigrationFault(f"fault injected before {phase} statement {statement_index}")
        return
    result = fault_at(phase, statement_index, statement)
    if result:
        raise MigrationFault(f"fault injected before {phase} statement {statement_index}")


def _read_user_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _legacy_shape_matches(connection: sqlite3.Connection, *, protected: bool = False) -> bool:
    for table, expected_columns in _LEGACY_COLUMNS.items():
        actual = tuple(
            tuple(row[1:]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        )
        if actual != expected_columns:
            return False
        actual_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if actual_sql is None or _normalise_sql(actual_sql[0]) != _normalise_sql(
            _LEGACY_TABLE_SQL[table]
        ):
            return False
    expected_fk = {
        "agent_traces": ("agent_runs", "run_id", "run_id"),
        "agent_snapshots": ("agent_runs", "run_id", "run_id"),
    }
    for table, (target, source_column, target_column) in expected_fk.items():
        foreign_keys = connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        if not any(
            row[2] == target and row[3] == source_column and row[4] == target_column
            for row in foreign_keys
        ):
            return False
    legacy_objects = connection.execute(
        """
        SELECT type, name FROM sqlite_master
        WHERE name NOT GLOB 'sqlite_*'
          AND tbl_name IN ('agent_runs', 'agent_traces', 'agent_snapshots')
          AND type IN ('index', 'trigger', 'view')
        """
    ).fetchall()
    expected_objects = (
        {
            ("trigger", f"{table}_legacy_no_{operation}")
            for table in LEGACY_TABLES
            for operation in ("insert", "update", "delete")
        }
        if protected
        else set()
    )
    return {tuple(row) for row in legacy_objects} == expected_objects


def _integrity_problems(connection: sqlite3.Connection) -> list[str]:
    problems: list[str] = []
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result and result[0] != "ok":
            problems.append(f"integrity_check: {result[0]}")
    except sqlite3.DatabaseError as error:
        problems.append(f"integrity_check failed: {error}")
    try:
        foreign_key_error = connection.execute("PRAGMA foreign_key_check").fetchone()
        if foreign_key_error is not None:
            problems.append(f"foreign_key_check: {tuple(foreign_key_error)!r}")
    except sqlite3.DatabaseError as error:
        problems.append(f"foreign_key_check failed: {error}")
    for name, sql in connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE name IN ('sqlite_stat1', 'sqlite_stat4')"
    ):
        if sql != _STATISTICS_TABLE_SQL[name]:
            problems.append(f"unexpected statistics schema: {name}")
    return problems


def _validate_v2_structure(connection: sqlite3.Connection, *, allow_legacy: bool) -> None:
    """Validate the DDL, object allow-list, and live FK/integrity state."""

    problems = _integrity_problems(connection)
    if problems:
        raise MigrationError("; ".join(problems))
    unexpected_objects = _unexpected_user_objects(connection)
    if unexpected_objects:
        raise MigrationError(f"unexpected sqlite_master objects: {unexpected_objects!r}")
    tables, _, _ = _v2_object_names(connection)
    expected_tables = set(V2_TABLES)
    if allow_legacy:
        expected_tables |= set(LEGACY_TABLES)
    if tables != expected_tables:
        raise MigrationError(f"unexpected tables after V2 DDL: {sorted(tables)!r}")
    all_tables = {
        row[1]
        for row in connection.execute("SELECT type, name FROM sqlite_master WHERE type = 'table'")
    }
    internal_tables = all_tables - tables
    if internal_tables - _ALLOWED_INTERNAL_TABLES:
        raise MigrationError(f"unexpected internal tables: {sorted(internal_tables)!r}")
    if not allow_legacy and "sqlite_sequence" in internal_tables:
        raise MigrationError("unexpected sqlite_sequence in an empty V2 database")
    if allow_legacy and not _legacy_shape_matches(connection, protected=True):
        raise MigrationError("legacy tables changed during V2 preparation")
    if _v2_signature(connection) != _expected_v2_signature(allow_legacy):
        raise MigrationError("V2 schema validation failed")


def _inspect_connection(connection: sqlite3.Connection) -> DatabaseInspection:
    try:
        user_version = _read_user_version(connection)
        rows = connection.execute(
            "SELECT type, name FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        tables = tuple(
            name for kind, name in rows if kind == "table" and not name.startswith("sqlite_")
        )
        all_tables = {name for kind, name in rows if kind == "table"}
        unexpected_internal = sorted(all_tables - set(tables) - _ALLOWED_INTERNAL_TABLES)
        problems = _integrity_problems(connection)
        problems.extend(
            f"unexpected sqlite_master object: {kind} {name}"
            for kind, name in _unexpected_user_objects(connection)
        )
    except sqlite3.DatabaseError as error:
        return DatabaseInspection(DatabaseKind.CORRUPT, None, (), (f"sqlite error: {error}",))

    if unexpected_internal:
        problems.append(f"unexpected internal tables: {unexpected_internal}")
    table_set = set(tables)
    if "sqlite_sequence" in all_tables and not (
        table_set == set(LEGACY_TABLES) or table_set == set(V2_TABLES) | set(LEGACY_TABLES)
    ):
        problems.append("sqlite_sequence is only valid for a legacy source/copy")
    has_v2_name = bool(table_set & set(V2_TABLES))
    has_legacy_name = bool(table_set & set(LEGACY_TABLES))
    v2_tables_exact = table_set == set(V2_TABLES) or table_set == (
        set(V2_TABLES) | set(LEGACY_TABLES)
    )
    legacy_tables_exact = table_set == set(LEGACY_TABLES)

    if not tables:
        if problems:
            return DatabaseInspection(DatabaseKind.CORRUPT, user_version, tables, tuple(problems))
        kind = DatabaseKind.EMPTY if user_version == 0 else DatabaseKind.CORRUPT
        return DatabaseInspection(kind, user_version, tables)
    if problems:
        return DatabaseInspection(DatabaseKind.CORRUPT, user_version, tables, tuple(problems))
    if v2_tables_exact:
        if _v2_signature(connection) != _expected_v2_signature(has_legacy_name):
            return DatabaseInspection(
                DatabaseKind.CORRUPT, user_version, tables, ("V2 schema mismatch",)
            )
        if user_version == _V2_VERSION and (
            table_set == set(V2_TABLES) or _legacy_shape_matches(connection, protected=True)
        ):
            return DatabaseInspection(DatabaseKind.KNOWN_V2, user_version, tables)
        return DatabaseInspection(
            DatabaseKind.CORRUPT,
            user_version,
            tables,
            ("V2 schema has wrong user_version or legacy shape",),
        )
    if legacy_tables_exact:
        if user_version == 0 and _legacy_shape_matches(connection):
            return DatabaseInspection(DatabaseKind.LEGACY0, user_version, tables)
        return DatabaseInspection(
            DatabaseKind.CORRUPT,
            user_version,
            tables,
            ("legacy schema has wrong version or shape",),
        )
    if has_v2_name or has_legacy_name:
        return DatabaseInspection(
            DatabaseKind.CORRUPT, user_version, tables, ("partial known schema",)
        )
    return DatabaseInspection(DatabaseKind.UNKNOWN, user_version, tables)


def inspect_agent_database(connection: sqlite3.Connection) -> DatabaseInspection:
    """Inspect an already-open SQLite connection without writing to it."""

    return _inspect_connection(connection)


def plan_agent_migration(connection: sqlite3.Connection) -> MigrationPlan:
    inspection = inspect_agent_database(connection)
    actions = {
        DatabaseKind.EMPTY: "create_v2",
        DatabaseKind.LEGACY0: "upgrade_legacy_copy",
        DatabaseKind.KNOWN_V2: "noop",
    }
    return MigrationPlan(inspection, actions.get(inspection.kind, "reject"))


def _enable_foreign_keys(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise MigrationError("SQLite foreign-key enforcement could not be enabled")


def apply_v2_schema(
    connection: sqlite3.Connection,
    *,
    fault_at: str | Callable[[str, int, str], object] | None = None,
) -> None:
    """Create the V2 schema in an empty or legacy-copy database atomically.

    This is an opt-in kernel function.  It deliberately rejects an already-open
    caller transaction, and executes every statement separately instead of using
    ``executescript`` (which can implicitly commit).
    """

    if connection.in_transaction:
        raise MigrationError("cannot apply V2 schema inside an existing transaction")
    inspection = inspect_agent_database(connection)
    if inspection.kind is DatabaseKind.KNOWN_V2:
        _enable_foreign_keys(connection)
        return
    if inspection.kind not in {DatabaseKind.EMPTY, DatabaseKind.LEGACY0}:
        detail = "; ".join(inspection.problems) or inspection.kind.value
        raise MigrationError(f"refusing migration from {detail}")

    _enable_foreign_keys(connection)
    statements = _V2_DDL + (
        _LEGACY_PROTECTION_DDL if inspection.kind is DatabaseKind.LEGACY0 else ()
    )
    try:
        connection.execute("BEGIN IMMEDIATE")
        _execute_ddl(connection, statements, fault_at=fault_at)
        # The version is still zero while structure and FK/integrity state are
        # checked.  A legacy copy may retain its exact source tables.
        _validate_v2_structure(connection, allow_legacy=inspection.kind is DatabaseKind.LEGACY0)
        _inject_fault(fault_at, "version", len(statements), "PRAGMA user_version = 2")
        connection.execute("PRAGMA user_version = 2")
        candidate = _inspect_connection(connection)
        if candidate.kind is not DatabaseKind.KNOWN_V2:
            raise MigrationError(
                "versioned V2 schema failed final validation: "
                + "; ".join(candidate.problems or (candidate.kind.value,))
            )
        _inject_fault(fault_at, "commit", len(statements) + 1, "COMMIT")
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        except BaseException as rollback_error:
            raise MigrationError("migration failed and rollback also failed") from rollback_error
        raise


def backup_agent_database(source: Path | str, destination: Path | str) -> None:
    """Atomically backup a SQLite source to a new, never-overwritten path."""

    source_path = Path(source).absolute()
    destination_path = Path(destination)
    try:
        source_stat = source_path.lstat()
    except FileNotFoundError as error:
        raise MigrationError(f"source database does not exist: {source_path}") from error
    if not stat.S_ISREG(source_stat.st_mode):
        raise MigrationError("source database must be an ordinary file")
    if os.path.lexists(destination_path):
        raise MigrationError(f"destination already exists: {destination_path}")
    if not destination_path.parent.is_dir():
        raise MigrationError("destination parent directory does not exist")
    if source_stat.st_ino == 0:  # pragma: no cover - defensive for unusual filesystems
        raise MigrationError("source database has no stable file identity")

    def check_source_identity() -> None:
        # The URI resolved through a transient symlink must still name the
        # originally validated file. Check both spellings against that identity.
        for path in (source_path, resolved_source):
            try:
                current = path.lstat()
            except OSError as error:
                raise MigrationError("source database identity changed during backup") from error
            # ctime detects a replaced path restored to the original inode.
            # Concurrent main-file writes/checkpoints are conservatively rejected.
            if not stat.S_ISREG(current.st_mode) or (
                current.st_dev,
                current.st_ino,
                current.st_ctime_ns,
            ) != (
                source_stat.st_dev,
                source_stat.st_ino,
                source_stat.st_ctime_ns,
            ):
                raise MigrationError("source database identity changed during backup")

    def source_directory_identities() -> tuple[tuple[int, int, int], ...]:
        # Include ancestors hidden behind symlinks, not just lexical parents.
        return tuple(
            (info.st_dev, info.st_ino, info.st_ctime_ns)
            for parent in (*source_path.parents, *resolved_source.parents)
            for info in (parent.stat(),)
        )

    temporary_path: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination_path.name}.", suffix=".tmp", dir=destination_path.parent
        )
        os.close(fd)
        temporary_path = Path(temporary_name)
        resolved_source = source_path.resolve(strict=True)
        check_source_identity()
        source_directories = source_directory_identities()
        source_uri = f"{resolved_source.as_uri()}?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as source_connection:
            source_connection.execute("PRAGMA query_only = ON")
            source_connection.execute("BEGIN")
            # Pin the actual read snapshot (including WAL) before verifying the
            # path chain. Subsequent destination writes may change a shared dir.
            source_connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
            check_source_identity()
            if source_directory_identities() != source_directories:
                raise MigrationError("source directory identity changed while opening snapshot")
            with closing(sqlite3.connect(temporary_path)) as destination_connection:
                source_connection.backup(destination_connection)
                check_source_identity()
                # Publish a self-contained rollback-journal destination, retaining
                # committed WAL data without carrying a source sidecar forward.
                destination_connection.execute("PRAGMA journal_mode = DELETE")
                destination_connection.commit()
        with temporary_path.open("rb") as handle:
            os.fsync(handle.fileno())
        check_source_identity()
        # A hard-link publish is atomic and fails if another writer created the
        # destination after the initial lexists check; os.replace would overwrite.
        os.link(temporary_path, destination_path, follow_symlinks=False)
        temporary_path.unlink()
        temporary_path = None
    except FileExistsError as error:
        raise MigrationError(f"destination was created concurrently: {destination_path}") from error
    except (OSError, sqlite3.DatabaseError) as error:
        raise MigrationError(f"SQLite backup failed: {error}") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


# Names used by the R5 service-boundary table.  These are aliases, not second
# implementations or separate DDL paths.
apply_agent_migration = apply_v2_schema
backup_database = backup_agent_database
create_v2_schema = apply_v2_schema
DatabaseState = DatabaseKind


__all__ = [
    "DatabaseState",
    "DatabaseInspection",
    "DatabaseKind",
    "MigrationError",
    "MigrationFault",
    "MigrationPlan",
    "V2_TABLES",
    "apply_agent_migration",
    "apply_v2_schema",
    "backup_database",
    "backup_agent_database",
    "create_v2_schema",
    "inspect_agent_database",
    "plan_agent_migration",
]
