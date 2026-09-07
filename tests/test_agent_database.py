from __future__ import annotations

import os
import shutil
import sqlite3
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
