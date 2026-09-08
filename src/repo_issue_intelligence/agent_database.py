"""Explicit, create-only Protocol v2 database lifecycle. Never a default initializer."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .agent_store_migrations import (
    DatabaseInspection,
    DatabaseKind,
    MigrationError,
    apply_v2_schema,
    backup_agent_database,
    inspect_agent_database,
)


def inspect_database(path: Path) -> DatabaseInspection:
    """Inspect an existing database without creating it or exposing its contents."""
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise MigrationError("database must be an ordinary file")
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only = ON")
            return inspect_agent_database(connection)
    except (OSError, sqlite3.DatabaseError):
        raise MigrationError("database cannot be inspected") from None


def _private_destination(destination: Path) -> Path:
    destination = destination.absolute()
    if os.path.lexists(destination) or os.path.lexists(_receipt_path(destination)):
        raise MigrationError("destination or migration receipt already exists; choose a new path")
    parent = destination.parent
    missing = []
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    parent = destination.parent.resolve(strict=True)
    info = parent.stat()
    if not stat.S_ISDIR(info.st_mode) or (
        os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o077)
    ):
        raise MigrationError("destination requires an owner-only directory (0700)")
    return parent / destination.name


def _receipt_path(database: Path) -> Path:
    return database.with_name(f"{database.name}.legacy.json")


def create_v2_database(destination: Path) -> None:
    """Create a validated private V2 database; never overwrite an existing target."""
    try:
        destination = _private_destination(destination)
        with tempfile.TemporaryDirectory(prefix=".agent-db-", dir=destination.parent) as staging:
            staged = Path(staging) / "database.sqlite3"
            fd = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with closing(sqlite3.connect(staged)) as connection:
                apply_v2_schema(connection)
            with staged.open("rb") as handle:
                os.fsync(handle.fileno())
            os.link(staged, destination, follow_symlinks=False)
    except (OSError, sqlite3.DatabaseError):
        raise MigrationError(
            "database creation failed; existing files were not overwritten"
        ) from None


def migrate_legacy_database(source: Path, destination: Path) -> None:
    """Upgrade a private backup only; retain the source and its original V1 behavior."""
    receipt: Path | None = None
    published_receipt_identity: tuple[int, int] | None = None
    published = False
    try:
        source_stat = source.lstat()
        if inspect_database(source).kind is not DatabaseKind.LEGACY0:
            raise MigrationError("migration requires a recognized legacy source")
        destination = _private_destination(destination)
        with tempfile.TemporaryDirectory(prefix=".agent-db-", dir=destination.parent) as staging:
            staged = Path(staging) / "database.sqlite3"
            backup_agent_database(source, staged)
            current = source.lstat()
            if (current.st_dev, current.st_ino, current.st_ctime_ns) != (
                source_stat.st_dev,
                source_stat.st_ino,
                source_stat.st_ctime_ns,
            ):
                raise MigrationError("source identity changed during migration")
            with closing(sqlite3.connect(staged)) as connection:
                if inspect_agent_database(connection).kind is not DatabaseKind.LEGACY0:
                    raise MigrationError("copied source is not a recognized legacy database")
                apply_v2_schema(connection)
            target_stat = staged.stat()
            payload = {
                "format": "legacy-copy-v1",
                "source_database": str(source.absolute()),
                "source_identity": {"device": source_stat.st_dev, "inode": source_stat.st_ino},
                "destination_identity": {"device": target_stat.st_dev, "inode": target_stat.st_ino},
                "migrated_at": datetime.now(UTC).isoformat(),
            }
            staged_receipt = Path(staging) / "receipt.json"
            descriptor = os.open(staged_receipt, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            with staged.open("rb") as handle:
                os.fsync(handle.fileno())
            receipt = _receipt_path(destination)
            receipt_stat = staged_receipt.stat()
            os.link(staged_receipt, receipt, follow_symlinks=False)
            published_receipt_identity = (receipt_stat.st_dev, receipt_stat.st_ino)
            # Publish the fully upgraded DB last. A crash before here leaves only
            # a receipt: never reuse that destination automatically.
            os.link(staged, destination, follow_symlinks=False)
            published = True
    except (OSError, sqlite3.DatabaseError):
        raise MigrationError(
            "migration failed; source and existing destinations were not overwritten"
        ) from None
    finally:
        if not published and receipt is not None and published_receipt_identity is not None:
            try:
                current = receipt.lstat()
                if (current.st_dev, current.st_ino) == published_receipt_identity:
                    receipt.unlink()
            except FileNotFoundError:
                pass


def read_migration_provenance(database: Path) -> dict[str, object] | None:
    """Read a private receipt bound to this database file, never infer a migration time."""
    receipt = _receipt_path(database)
    try:
        info = receipt.lstat()
    except FileNotFoundError:
        return None
    try:
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > 16_384
            or (os.name == "posix" and (info.st_uid != os.getuid() or info.st_mode & 0o077))
        ):
            raise MigrationError("migration receipt is not a private regular file")
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        target = database.lstat()
        if (
            not isinstance(payload, dict)
            or payload.get("format") != "legacy-copy-v1"
            or (
                payload.get("destination_identity")
                != {"device": target.st_dev, "inode": target.st_ino}
            )
        ):
            raise MigrationError("migration receipt does not identify this database")
        if not isinstance(payload.get("source_database"), str):
            raise MigrationError("migration receipt has no source")
        migrated_at = datetime.fromisoformat(payload["migrated_at"])
        if migrated_at.tzinfo is None:
            raise MigrationError("migration receipt has no timezone")
        return payload
    except (OSError, ValueError, TypeError, KeyError):
        raise MigrationError("invalid migration receipt") from None
