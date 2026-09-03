from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .benchmark import BenchmarkCaseResult

BENCHMARK_STORE_SCHEMA_VERSION = 1


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class BenchmarkStore:
    """Persist resumable benchmark case results without storing provider credentials."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
            if schema_version not in {0, BENCHMARK_STORE_SCHEMA_VERSION}:
                raise ValueError(
                    "Unsupported benchmark checkpoint schema version: "
                    f"{schema_version}"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    configuration_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS benchmark_case_results (
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    result_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, case_id),
                    UNIQUE (run_id, ordinal),
                    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id)
                );
                """
            )
            connection.execute(
                f"PRAGMA user_version = {BENCHMARK_STORE_SCHEMA_VERSION}"
            )

    def create_run(self, configuration: dict[str, Any]) -> tuple[str, datetime]:
        run_id = uuid4().hex
        created_at = datetime.now(UTC)
        timestamp = created_at.isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO benchmark_runs
                    (run_id, status, created_at, updated_at, configuration_json)
                VALUES (?, 'running', ?, ?, ?)
                """,
                (run_id, timestamp, timestamp, _canonical_json(configuration)),
            )
        return run_id, created_at

    def resume_run(
        self,
        run_id: str,
        configuration: dict[str, Any],
    ) -> datetime:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT created_at, configuration_json
                FROM benchmark_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["configuration_json"] != _canonical_json(configuration):
                raise ValueError(
                    f"Benchmark run {run_id} configuration does not match this command"
                )
            connection.execute(
                """
                UPDATE benchmark_runs
                SET status = 'running', updated_at = ?
                WHERE run_id = ?
                """,
                (datetime.now(UTC).isoformat(), run_id),
            )
        return datetime.fromisoformat(row["created_at"])

    def save_result(
        self,
        run_id: str,
        ordinal: int,
        result: BenchmarkCaseResult,
    ) -> None:
        timestamp = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            run = connection.execute(
                "SELECT 1 FROM benchmark_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            connection.execute(
                """
                INSERT INTO benchmark_case_results
                    (run_id, case_id, ordinal, result_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, case_id) DO UPDATE SET
                    ordinal = excluded.ordinal,
                    result_json = excluded.result_json,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    result.case_id,
                    ordinal,
                    result.model_dump_json(),
                    timestamp,
                ),
            )
            connection.execute(
                """
                UPDATE benchmark_runs SET updated_at = ? WHERE run_id = ?
                """,
                (timestamp, run_id),
            )

    def load_results(self, run_id: str) -> dict[str, BenchmarkCaseResult]:
        with self._connect() as connection:
            run = connection.execute(
                "SELECT 1 FROM benchmark_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            rows = connection.execute(
                """
                SELECT result_json
                FROM benchmark_case_results
                WHERE run_id = ?
                ORDER BY ordinal
                """,
                (run_id,),
            ).fetchall()
        results = [
            BenchmarkCaseResult.model_validate_json(row["result_json"])
            for row in rows
        ]
        return {result.case_id: result for result in results}

    def mark_complete(self, run_id: str, *, failed: bool) -> None:
        timestamp = datetime.now(UTC).isoformat()
        status = "completed_with_failures" if failed else "completed"
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE benchmark_runs
                SET status = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (status, timestamp, run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(run_id)

    def status(self, run_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM benchmark_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return str(row["status"])
