"""Read original legacy reports without reinterpreting them as V2 executions."""

from __future__ import annotations

import json
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .agent_database import read_migration_provenance
from .agent_store_migrations import DatabaseKind, MigrationError, inspect_agent_database


@dataclass(frozen=True)
class LegacyRunProjection:
    run_id: str
    raw_report_json: str
    source_database: str | None
    migrated_at: str | None
    provenance_status: Literal["available", "unavailable"]
    protocol: Literal["legacy0"] = "legacy0"
    evidence_status: Literal["unavailable"] = "unavailable"

    @property
    def report(self) -> dict[str, Any]:
        """Decode the original payload only; never hydrate old snapshots."""

        return json.loads(self.raw_report_json)


def project_legacy_run(database_path: Path, run_id: str) -> LegacyRunProjection | None:
    requested_path = Path(database_path).absolute()
    original = requested_path.lstat()
    if not stat.S_ISREG(original.st_mode):
        raise MigrationError("legacy projection requires an ordinary database file")
    path = requested_path.resolve(strict=True)
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        inspection = inspect_agent_database(connection)
        if inspection.kind not in {DatabaseKind.LEGACY0, DatabaseKind.KNOWN_V2}:
            raise MigrationError("legacy projection requires a known legacy or V2 database")
        if "agent_runs" not in inspection.tables:
            return None
        row = connection.execute(
            "SELECT payload FROM agent_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        if inspection.kind is DatabaseKind.LEGACY0:
            source_database, migrated_at = str(path), None
        else:
            provenance = read_migration_provenance(path)
            source_database = str(provenance["source_database"]) if provenance else None
            migrated_at = str(provenance["migrated_at"]) if provenance else None
        for spelling in (requested_path, path):
            current = spelling.lstat()
            if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
                original.st_dev,
                original.st_ino,
            ):
                raise MigrationError("legacy database identity changed during projection")
    return LegacyRunProjection(
        run_id=run_id,
        raw_report_json=row[0],
        source_database=source_database,
        migrated_at=migrated_at,
        provenance_status="available" if source_database is not None else "unavailable",
    )
