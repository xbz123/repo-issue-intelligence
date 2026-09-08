from __future__ import annotations

import os
import shutil
import sqlite3
import stat
from pathlib import Path

import pytest

from repo_issue_intelligence.agent_database import (
    create_v2_database,
    inspect_database,
    migrate_legacy_database,
    read_migration_provenance,
)
from repo_issue_intelligence.agent_store_migrations import DatabaseKind, MigrationError


def test_explicit_create_is_private_create_only_and_inspect_does_not_create(tmp_path: Path) -> None:
    destination = tmp_path / "private" / "v2.sqlite3"
    with pytest.raises(MigrationError):
        inspect_database(destination)
    assert not destination.parent.exists()
    create_v2_database(destination)
    assert inspect_database(destination).kind is DatabaseKind.KNOWN_V2
    if os.name == "posix":
        assert destination.stat().st_mode & 0o777 == 0o600
        assert destination.parent.stat().st_mode & 0o777 == 0o700
    before = destination.read_bytes()
    with pytest.raises(MigrationError):
        create_v2_database(destination)
    assert destination.read_bytes() == before
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT count(*) FROM agent_v2_runs").fetchone() == (0,)


def test_create_refuses_existing_symlink_and_public_directory(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    source = private / "source.sqlite3"
    create_v2_database(source)
    alias = private / "alias.sqlite3"
    alias.symlink_to(source)
    with pytest.raises(MigrationError):
        create_v2_database(alias)
    assert alias.is_symlink()
    if os.name == "posix":
        public = tmp_path / "public"
        public.mkdir(mode=0o755)
        public.chmod(0o755)
        with pytest.raises(MigrationError):
            create_v2_database(public / "v2.sqlite3")
        assert public.stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize("migrate", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_destination_symlink_ancestor_is_rejected_before_any_creation(tmp_path, migrate, nested):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(private, target_is_directory=True)
    destination = alias / "new" / "db.sqlite3" if nested else alias / "db.sqlite3"
    source = tmp_path / "legacy.sqlite3"
    shutil.copy2(Path(__file__).parent / "fixtures/protocol_v2/legacy_agent.sqlite3", source)
    original = source.read_bytes()
    with pytest.raises(MigrationError):
        if migrate:
            migrate_legacy_database(source, destination)
        else:
            create_v2_database(destination)
    assert list(private.iterdir()) == []
    assert source.read_bytes() == original


def test_explicit_migration_preserves_source_and_records_private_provenance(tmp_path: Path) -> None:
    source = tmp_path / "legacy.sqlite3"
    fixture = Path(__file__).parent / "fixtures" / "protocol_v2" / "legacy_agent.sqlite3"
    shutil.copy2(fixture, source)
    before = source.read_bytes()
    destination = tmp_path / "private" / "migrated.sqlite3"
    migrate_legacy_database(source, destination)
    assert source.read_bytes() == before
    assert inspect_database(source).kind is DatabaseKind.LEGACY0
    assert inspect_database(destination).kind is DatabaseKind.KNOWN_V2
    provenance = read_migration_provenance(destination)
    assert provenance is not None
    assert provenance["source_database"] == str(source.resolve())
    assert provenance["migrated_at"]
    if os.name == "posix":
        assert destination.stat().st_mode & 0o777 == 0o600
        assert (
            destination.with_name(f"{destination.name}.legacy.json").stat().st_mode & 0o777 == 0o600
        )
    with sqlite3.connect(source) as original, sqlite3.connect(destination) as copied:
        assert (
            original.execute("SELECT * FROM agent_runs").fetchall()
            == copied.execute("SELECT * FROM agent_runs").fetchall()
        )
    with pytest.raises(MigrationError):
        migrate_legacy_database(source, destination)


def test_failed_migration_never_overwrites_or_publishes_partial_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "legacy.sqlite3"
    shutil.copy2(Path(__file__).parent / "fixtures/protocol_v2/legacy_agent.sqlite3", source)
    destination = tmp_path / "private" / "migrated.sqlite3"
    real_link = os.link

    def link(src, dst, *args, **kwargs):
        if Path(dst) == destination:
            raise PermissionError("sensitive external error value")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", link)
    with pytest.raises(MigrationError) as error:
        migrate_legacy_database(source, destination)
    assert "sensitive external" not in str(error.value)
    assert not destination.exists()
    assert not list(destination.parent.iterdir())
    assert inspect_database(source).kind is DatabaseKind.LEGACY0


@pytest.mark.parametrize("migrate", [False, True])
def test_publication_syncs_directory_entries_in_order(tmp_path, monkeypatch, migrate):
    source = tmp_path / "legacy.sqlite3"
    shutil.copy2(Path(__file__).parent / "fixtures/protocol_v2/legacy_agent.sqlite3", source)
    destination = tmp_path / "new" / "private" / "db.sqlite3"
    events = []
    real_link, real_fsync = os.link, os.fsync

    def link(src, dst, *args, **kwargs):
        result = real_link(src, dst, *args, **kwargs)
        if Path(dst).parent == destination.parent:
            events.append("receipt" if str(dst).endswith(".json") else "database")
        return result

    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            events.append("directory")
        return real_fsync(fd)

    monkeypatch.setattr(os, "link", link)
    monkeypatch.setattr(os, "fsync", fsync)
    if migrate:
        migrate_legacy_database(source, destination)
        assert events == ["directory", "directory", "receipt", "directory", "database", "directory"]
        assert read_migration_provenance(destination) is not None
    else:
        create_v2_database(destination)
        assert events == ["directory", "directory", "database", "directory"]


@pytest.mark.parametrize("after_database", [False, True])
def test_migration_directory_sync_failure_preserves_publication_invariant(
    tmp_path,
    monkeypatch,
    after_database,
):
    source = tmp_path / "legacy.sqlite3"
    shutil.copy2(Path(__file__).parent / "fixtures/protocol_v2/legacy_agent.sqlite3", source)
    before = source.read_bytes()
    destination = tmp_path / "private" / "db.sqlite3"
    destination.parent.mkdir(mode=0o700)
    receipt = destination.with_name(destination.name + ".legacy.json")
    real_fsync = os.fsync

    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode) and receipt.exists():
            if destination.exists() == after_database:
                raise OSError("private-sync-error")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(MigrationError) as error:
        migrate_legacy_database(source, destination)
    assert "private-sync-error" not in str(error.value)
    assert source.read_bytes() == before
    assert destination.exists() == after_database
    assert receipt.exists() == after_database
    if after_database:
        assert read_migration_provenance(destination) is not None


def test_create_directory_sync_failure_does_not_claim_success_or_overwrite(tmp_path, monkeypatch):
    destination = tmp_path / "private" / "db.sqlite3"
    destination.parent.mkdir(mode=0o700)
    real_fsync = os.fsync

    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("private-sync-error")
        return real_fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fsync)
        with pytest.raises(MigrationError) as error:
            create_v2_database(destination)
    assert "private-sync-error" not in str(error.value)
    assert inspect_database(destination).kind is DatabaseKind.KNOWN_V2
    before = destination.read_bytes()
    with pytest.raises(MigrationError):
        create_v2_database(destination)
    assert destination.read_bytes() == before
