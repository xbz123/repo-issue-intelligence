from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import repo_issue_intelligence.repository_context as repository_context
from repo_issue_intelligence.repository_context import (
    RepositoryContextError,
    capture_repository_context,
)


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _repository(tmp_path: Path, *, origin: str | None = None) -> Path:
    repository = tmp_path / "repo with space"
    source = repository / "src dir-测试"
    source.mkdir(parents=True)
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "tests@example.invalid")
    _git(repository, "config", "user.name", "Protocol v2 tests")
    (source / "tracked file-测试.py").write_text("value = 1\n", encoding="utf-8")
    (source / "line\nname.py").write_text("value = 2\n", encoding="utf-8")
    (repository / "outside.py").write_text("outside = True\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "initial")
    if origin is not None:
        _git(repository, "remote", "add", "origin", origin)
    return repository


@pytest.mark.parametrize("filter_value", [None, "set", "unset", "unspecified", "-"])
def test_current_filter_attribute_detection_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    filter_value: str | None,
) -> None:
    output = (
        b""
        if filter_value is None
        else f"src/service.py\0filter\0{filter_value}\0".encode()
    )

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr=b"")

    monkeypatch.setattr(repository_context.subprocess, "run", fake_run)

    paths = repository_context._current_external_filter_paths(
        tmp_path,
        ("src/service.py",),
    )
    assert paths == (() if filter_value is None else ("src/service.py",))


def test_capture_nested_scope_uses_nul_manifest_and_safe_remote(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        origin="https://user:secret@GitHub.com/Org/Repo.git",
    )
    source = repository / "src dir-测试"
    (source / "untracked decoy.py").write_text("decoy = True\n", encoding="utf-8")
    (repository / "outside.py").write_text("outside = changed\n", encoding="utf-8")

    snapshot = capture_repository_context(source)

    assert snapshot.git_root == repository.resolve()
    assert snapshot.analysis_root == source.resolve()
    assert snapshot.analysis_prefix == "src dir-测试"
    assert snapshot.branch == "main"
    assert snapshot.detached is False
    assert snapshot.git_metadata_kind == "directory"
    assert snapshot.git_root_dirty is True
    assert snapshot.analysis_scope_dirty is False
    assert snapshot.untracked_in_scope_count == 1
    assert snapshot.untracked_in_scope_paths == ("untracked decoy.py",)
    assert [item.path for item in snapshot.manifest] == [
        "line\nname.py",
        "tracked file-测试.py",
    ]
    assert all(item.git_path.startswith("src dir-测试/") for item in snapshot.manifest)
    assert snapshot.normalized_remote_identity == "github.com/Org/Repo"
    assert snapshot.remote_sanitized is True
    assert "secret" not in snapshot.model_dump_json()


def test_capture_records_symlink_and_staged_delete_without_view_policy(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    source = repository / "src dir-测试"
    (source / "target.py").write_text("target = True\n", encoding="utf-8")
    (source / "link.py").symlink_to("target.py")
    _git(repository, "add", "src dir-测试/target.py", "src dir-测试/link.py")
    _git(repository, "commit", "-qm", "add symlink")

    snapshot = capture_repository_context(source)

    assert snapshot.symlink_paths == ("link.py",)
    assert snapshot.unsupported_paths == ("link.py",)
    link = next(item for item in snapshot.manifest if item.path == "link.py")
    assert link.symlink is True
    assert link.submodule is False

    _git(repository, "rm", "-q", "src dir-测试/target.py")
    worktree_snapshot = capture_repository_context(
        source,
        mode="tracked_worktree",
    )
    assert "target.py" in worktree_snapshot.deleted_paths
    target = next(item for item in worktree_snapshot.manifest if item.path == "target.py")
    assert target.deleted is True
    assert target.staged_deleted is True


@pytest.mark.parametrize("statuses", ["tracked_first", "untracked_first"])
def test_status_for_path_keeps_tracked_status_authoritative(statuses: str) -> None:
    staged_delete = repository_context._Status(
        path="foo.py",
        original_path=None,
        index_status="D",
        worktree_status=" ",
        untracked=False,
        deleted=True,
        staged_deleted=True,
        unmerged=False,
    )
    untracked = repository_context._Status(
        path="foo.py",
        original_path=None,
        index_status="?",
        worktree_status="?",
        untracked=True,
        deleted=False,
        staged_deleted=False,
        unmerged=False,
    )
    ordered = (
        (staged_delete, untracked) if statuses == "tracked_first" else (untracked, staged_delete)
    )

    selected = repository_context._status_for_path(ordered)

    assert selected["foo.py"] == staged_delete


def test_cached_delete_does_not_resurrect_same_path_untracked_content(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    target = "src dir-测试/tracked file-测试.py"
    head_object = _git(repository, "rev-parse", f"HEAD:{target}").strip()
    _git(repository, "rm", "-q", "--cached", target)

    snapshot = capture_repository_context(
        repository / "src dir-测试",
        mode="tracked_worktree",
    )

    removed = next(item for item in snapshot.manifest if item.path == "tracked file-测试.py")
    assert removed.status is repository_context.RepositoryFileStatus.DELETED
    assert removed.deleted is True
    assert removed.staged_deleted is True
    assert removed.object_id == head_object
    assert snapshot.untracked_in_scope_paths == ("tracked file-测试.py",)


def test_capture_rejects_scope_dirty_and_prefix_traversal(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    source = repository / "src dir-测试"
    (source / "tracked file-测试.py").write_text("changed = True\n", encoding="utf-8")

    with pytest.raises(RepositoryContextError, match="clean tracked analysis scope") as error:
        capture_repository_context(source)
    assert error.value.code == "analysis_scope_dirty"

    with pytest.raises(RepositoryContextError) as error:
        capture_repository_context(source, analysis_prefix="../outside")
    assert error.value.code == "invalid_prefix"


def test_capture_detects_detached_head_and_ambiguous_remote(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _git(repository, "checkout", "--detach", "-q", "HEAD")
    detached = capture_repository_context(repository)
    assert detached.branch is None
    assert detached.detached is True

    ambiguous = _repository(tmp_path / "ambiguous")
    _git(ambiguous, "remote", "add", "upstream", "ssh://git@example.net/a/repo.git")
    _git(ambiguous, "remote", "add", "mirror", "https://example.net/a/repo.git")
    snapshot = capture_repository_context(ambiguous)
    assert snapshot.remote_selection_source == "ambiguous"
    assert snapshot.remote_name is None
    assert snapshot.normalized_remote_identity is None
    assert snapshot.repository_identity is None


def test_capture_explicit_remote_precedes_ambiguous_configured_remotes(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _git(repository, "remote", "add", "upstream", "ssh://git@example.net/a/repo.git")
    _git(repository, "remote", "add", "mirror", "https://example.net/a/repo.git")

    snapshot = capture_repository_context(repository, remote="mirror")

    assert snapshot.remote_name == "mirror"
    assert snapshot.remote_selection_source == "explicit"
    assert snapshot.remote_url_count == 1
    assert snapshot.normalized_remote_identity == "example.net/a/repo"


@pytest.mark.parametrize(
    "remote_url",
    [
        "git@example.com:org/repo?token=syntheticSecret",
        "https://example.com/org/repo%3Ftoken%3DsyntheticSecret.git",
        "git@example.com:org/repo%253Ftoken%253DsyntheticSecret.git",
        "ssh://example.com/org/repo%253Ftoken%253DsyntheticSecret.git",
        "https://example.com/org/repo%2523token%253DsyntheticSecret.git",
        "https://example%253Ftoken%253DsyntheticSecret/org/repo.git",
    ],
)
def test_remote_query_or_encoded_query_is_not_retained(
    tmp_path: Path,
    remote_url: str,
) -> None:
    repository = _repository(tmp_path)
    _git(repository, "remote", "add", "unsafe", remote_url)

    snapshot = capture_repository_context(repository, remote="unsafe")

    assert snapshot.normalized_remote_identity is None
    assert snapshot.repository_identity is None
    assert snapshot.remote_sanitized is True
    assert "syntheticSecret" not in snapshot.model_dump_json()


@pytest.mark.parametrize(
    ("remote_url", "expected_identity"),
    [
        ("ssh://git%3Asecret%40example.com/org/repo.git", "example.com/org/repo"),
        ("git%3Asecret%40example.com:org/repo.git", "example.com/org/repo"),
    ],
)
def test_remote_multilayer_userinfo_is_removed_for_url_and_scp_forms(
    tmp_path: Path,
    remote_url: str,
    expected_identity: str,
) -> None:
    repository = _repository(tmp_path)
    _git(repository, "remote", "add", "unsafe", remote_url)

    snapshot = capture_repository_context(repository, remote="unsafe")

    assert snapshot.normalized_remote_identity == expected_identity
    assert snapshot.remote_sanitized is True
    assert "secret" not in snapshot.model_dump_json()


def test_remote_invalid_urlsplit_fails_closed_without_echoing_input() -> None:
    identity, sanitized = repository_context._normalize_remote_url(
        "https://[invalid-host/org/repo.git"
    )

    assert identity is None
    assert sanitized is True


def test_tracked_rename_does_not_resurrect_old_head_path(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    source = repository / "src dir-测试"
    _git(repository, "mv", "src dir-测试/tracked file-测试.py", "src dir-测试/renamed.py")

    snapshot = capture_repository_context(source, mode="tracked_worktree")

    paths = {item.path for item in snapshot.manifest}
    assert "renamed.py" in paths
    assert "tracked file-测试.py" not in paths
    renamed = next(item for item in snapshot.manifest if item.path == "renamed.py")
    assert renamed.status.value == "renamed"
    assert "tracked file-测试.py" in snapshot.deleted_paths
    status = next(item for item in snapshot.status_entries if item.path == "renamed.py")
    assert status.original_path == "tracked file-测试.py"


def test_rename_source_recreated_in_scope_is_not_manifest_content(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    source = repository / "src dir-测试"
    old_path = "src dir-测试/tracked file-测试.py"
    _git(repository, "mv", old_path, "src dir-测试/renamed.py")
    (source / "tracked file-测试.py").write_text("replacement = True\n", encoding="utf-8")

    snapshot = capture_repository_context(source, mode="tracked_worktree")

    paths = {item.path for item in snapshot.manifest}
    assert "renamed.py" in paths
    assert "tracked file-测试.py" not in paths
    assert snapshot.untracked_in_scope_paths == ("tracked file-测试.py",)
    recreated_status = next(
        item for item in snapshot.status_entries if item.path == "tracked file-测试.py"
    )
    assert recreated_status.untracked is True


def test_rename_source_recreated_across_scope_is_not_manifest_content(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    source = repository / "src dir-测试"
    old_path = "src dir-测试/tracked file-测试.py"
    _git(repository, "mv", old_path, "renamed outside.py")
    (source / "tracked file-测试.py").write_text("replacement = True\n", encoding="utf-8")

    snapshot = capture_repository_context(source, mode="tracked_worktree")

    paths = {item.path for item in snapshot.manifest}
    assert "tracked file-测试.py" not in paths
    assert "renamed outside.py" not in paths
    assert snapshot.untracked_in_scope_paths == ("tracked file-测试.py",)
    recreated_status = next(
        item for item in snapshot.status_entries if item.path == "tracked file-测试.py"
    )
    assert recreated_status.untracked is True


def test_manifest_tree_is_bound_to_captured_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    tree_commands: list[tuple[str, ...]] = []
    original_run_git = repository_context._run_git

    def record_tree_command(
        git_root: Path,
        args: tuple[str, ...] | list[str],
        *,
        check: bool = True,
    ) -> bytes:
        if args and args[0] == "ls-tree":
            tree_commands.append(tuple(args))
        return original_run_git(git_root, args, check=check)

    monkeypatch.setattr(repository_context, "_run_git", record_tree_command)
    snapshot = capture_repository_context(repository)

    assert tree_commands
    assert snapshot.commit_oid in tree_commands[0]
    assert "HEAD" not in tree_commands[0]
