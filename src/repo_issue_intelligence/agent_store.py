from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .models import AgentRun, AgentRunStatus, NodeTrace, ReviewDecision


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (datetime, Path)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class AgentStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    node_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    elapsed_ms REAL NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS agent_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    node_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES agent_runs(run_id)
                );
                """
            )

    def save_run(self, run: AgentRun) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_runs (run_id, status, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    payload = excluded.payload
                """,
                (
                    run.run_id,
                    run.status.value,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                    run.model_dump_json(),
                ),
            )

    def get_run(self, run_id: str) -> AgentRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return AgentRun.model_validate_json(row["payload"]) if row else None

    def append_trace(self, run_id: str, trace: NodeTrace) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_traces
                    (run_id, node_name, status, attempt, elapsed_ms, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    trace.node_name,
                    trace.status,
                    trace.attempt,
                    trace.elapsed_ms,
                    trace.model_dump_json(),
                ),
            )

    def list_traces(self, run_id: str) -> list[NodeTrace]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM agent_traces WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [NodeTrace.model_validate_json(row["payload"]) for row in rows]

    def save_snapshot(self, run_id: str, node_name: str, state: dict[str, Any]) -> None:
        payload = json.dumps(state, ensure_ascii=False, default=_json_default)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_snapshots (run_id, node_name, created_at, state_json)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, node_name, datetime.now(UTC).isoformat(), payload),
            )

    def list_snapshots(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT node_name, created_at, state_json
                FROM agent_snapshots
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "node_name": row["node_name"],
                "created_at": row["created_at"],
                "state": json.loads(row["state_json"]),
            }
            for row in rows
        ]

    def review(self, run_id: str, decision: ReviewDecision, notes: str | None = None) -> AgentRun:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status is not AgentRunStatus.AWAITING_REVIEW:
            raise ValueError(f"Run {run_id} is not awaiting review")
        run.status = AgentRunStatus(decision.value)
        run.review_notes = notes
        run.updated_at = datetime.now(UTC)
        self.save_run(run)
        return run
