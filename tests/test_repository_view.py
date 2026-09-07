from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

import repo_issue_intelligence.repository_view as repository_view
from repo_issue_intelligence.agent_workflow import run_protocol_v2_investigation
from repo_issue_intelligence.evidence import collect_evidence
from repo_issue_intelligence.investigator import (
    _blame_relations,
    _history_relations,
    investigate,
)
from repo_issue_intelligence.models import IssueRecord
from repo_issue_intelligence.repository_context import (
    RepositoryContextError,
    capture_repository_context,
)
from repo_issue_intelligence.repository_index import build_repository_map
from repo_issue_intelligence.repository_view import (
    DeterministicResumeError,
    RepositoryViewError,
    _is_lfs_pointer,
    prepare_repository_view,
)


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    (repository / "src").mkdir(parents=True)
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "tests@example.invalid")
    _git(repository, "config", "user.name", "Protocol v2 tests")
    (repository / "src" / "service.py").write_text(
        "def refresh_token():\n    return 'committed'\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "initial")
    return repository


def test_committed_view_reads_raw_captured_blob_and_ignores_later_checkout_change(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    snapshot = capture_repository_context(repository / "src")
    view = prepare_repository_view(snapshot)
    try:
        (repository / "src" / "service.py").write_text(
            "def refresh_token():\n    return 'worktree changed'\n",
            encoding="utf-8",
        )
        _git(repository, "add", "src/service.py")
        _git(repository, "commit", "-qm", "advance checkout after capture")
        assert (view.materialized_root / "service.py").read_text(encoding="utf-8") == (
            "def refresh_token():\n    return 'committed'\n"
        )
        assert view.representation == "raw-blob"
        assert view.captured_revision == snapshot.commit_oid
    finally:
        root = view.materialized_root
        view.close()
    assert not root.exists()


def test_committed_view_ignores_index_change_after_capture(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    snapshot = capture_repository_context(repository / "src")
    (repository / "src" / "service.py").write_text(
        "def refresh_token():\n    return 'staged changed'\n",
        encoding="utf-8",
    )
    _git(repository, "add", "src/service.py")
    view = prepare_repository_view(snapshot)
    try:
        assert (view.materialized_root / "service.py").read_text(encoding="utf-8") == (
            "def refresh_token():\n    return 'committed'\n"
        )
    finally:
        view.close()


def test_view_map_and_evidence_share_manifest_and_exclude_untracked_decoy(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    snapshot = capture_repository_context(repository / "src")
    view = prepare_repository_view(snapshot)
    try:
        (repository / "src" / "service.py").write_text(
            "def refresh_token():\n    return 'decoy'\n",
            encoding="utf-8",
        )
        (repository / "src" / "decoy.py").write_text(
            "def refresh_token():\n    return 'untracked'\n",
            encoding="utf-8",
        )
        repository_map = build_repository_map(view)
        assert [file.path for file in repository_map.files] == ["service.py"]
        assert "captured_revision" not in repository_map.model_dump()
        issue = IssueRecord(
            number=1,
            title="refresh token fails",
            body="refresh_token returns the wrong value",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        report = investigate(issue, repository_map)
        report = report.model_copy(
            update={
                "candidates": [
                    report.candidates[0]
                    if report.candidates
                    else {
                        "file": "service.py",
                        "lines": "1-2",
                        "confidence": 0.5,
                        "evidence": [],
                    }
                ]
            }
        )
        evidence = collect_evidence(report, repository_view=view)
        assert evidence and evidence[0].file == "service.py"
        assert "decoy" not in evidence[0].content
    finally:
        view.close()


def test_clean_view_matches_v1_map_candidates_symbols_and_evidence(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    snapshot = capture_repository_context(repository / "src")
    baseline_map = build_repository_map(repository / "src")
    issue = IssueRecord(
        number=8,
        title="refresh token fails",
        body="refresh_token returns the wrong value",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    baseline_report = investigate(issue, baseline_map)
    baseline_evidence = collect_evidence(baseline_report)

    view = prepare_repository_view(snapshot)
    try:
        view_map = build_repository_map(view)
        view_report = investigate(issue, view_map)
        view_evidence = collect_evidence(view_report, repository_view=view)

        assert view_map.languages == baseline_map.languages
        assert view_map.frameworks == baseline_map.frameworks
        assert view_map.entrypoints == baseline_map.entrypoints
        assert view_map.test_directories == baseline_map.test_directories
        assert view_map.runtime_files == baseline_map.runtime_files
        assert [file.model_dump() for file in view_map.files] == [
            file.model_dump() for file in baseline_map.files
        ]
        assert [candidate.model_dump() for candidate in view_report.candidates] == [
            candidate.model_dump() for candidate in baseline_report.candidates
        ]
        assert [snippet.model_dump() for snippet in view_evidence] == [
            snippet.model_dump() for snippet in baseline_evidence
        ]
    finally:
        view.close()


def test_committed_symlink_must_resolve_to_scoped_tracked_regular_file(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    (repository / "src" / "alias.py").symlink_to("service.py")
    _git(repository, "add", "src/alias.py")
    _git(repository, "commit", "-qm", "add internal link")
    snapshot = capture_repository_context(repository / "src")
    view = prepare_repository_view(snapshot)
    try:
        assert (view.materialized_root / "alias.py").is_symlink()
        assert (view.materialized_root / "alias.py").read_text(encoding="utf-8").endswith(
            "committed'\n"
        )
    finally:
        view.close()

    outside_link_repo = _repository(tmp_path / "outside")
    (outside_link_repo / "outside.py").write_text("outside = True\n", encoding="utf-8")
    (outside_link_repo / "src" / "alias.py").symlink_to("../outside.py")
    _git(outside_link_repo, "add", "outside.py", "src/alias.py")
    _git(outside_link_repo, "commit", "-qm", "add boundary link")
    outside_snapshot = capture_repository_context(outside_link_repo / "src")
    with pytest.raises(RepositoryViewError, match="escapes the analysis scope"):
        prepare_repository_view(outside_snapshot)


def test_committed_view_rejects_lfs_pointer_and_external_filter(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "src" / "pointer.py").write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:0123456789abcdef\n"
        "size 42\n",
        encoding="utf-8",
    )
    _git(repository, "add", "src/pointer.py")
    _git(repository, "commit", "-qm", "add pointer")
    snapshot = capture_repository_context(repository / "src")
    with pytest.raises(RepositoryViewError, match="LFS pointer"):
        prepare_repository_view(snapshot)

    filtered = _repository(tmp_path / "filtered")
    (filtered / ".gitattributes").write_text("src/service.py filter=external\n", encoding="utf-8")
    _git(filtered, "add", ".gitattributes")
    _git(filtered, "commit", "-qm", "configure filter")
    (filtered / ".git" / "info" / "attributes").write_text(
        "src/service.py -filter\n",
        encoding="utf-8",
    )
    with pytest.raises(RepositoryContextError, match="External Git filter"):
        capture_repository_context(filtered / "src")


def test_lfs_pointer_detection_accepts_lf_and_crlf_headers() -> None:
    assert _is_lfs_pointer(
        b"version https://git-lfs.github.com/spec/v1\n"
        b"oid sha256:0123456789abcdef\n"
    )
    assert _is_lfs_pointer(
        b"version https://git-lfs.github.com/spec/v1\r\n"
        b"oid sha256:0123456789abcdef\r\n"
    )
    assert not _is_lfs_pointer(b"version https://git-lfs.github.com/spec/v1")


def test_committed_filter_query_is_bound_to_captured_revision_not_current_untracked_attrs(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    snapshot = capture_repository_context(repository / "src")
    initial_view = prepare_repository_view(snapshot)
    initial_view.close()
    (repository / ".git" / "info" / "attributes").write_text(
        "src/service.py filter=external\n",
        encoding="utf-8",
    )
    view = prepare_repository_view(snapshot, deterministic_resume=True)
    try:
        assert (view.materialized_root / "service.py").exists()
    finally:
        view.close()


def test_committed_filter_cannot_be_disabled_by_current_info_attributes(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    captured = capture_repository_context(repository / "src")
    (repository / ".gitattributes").write_text(
        "src/service.py filter=external\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "-qm", "configure captured filter")
    captured_revision = _git(repository, "rev-parse", "HEAD").strip()
    (repository / ".git" / "info" / "attributes").write_text(
        "src/service.py -filter\n",
        encoding="utf-8",
    )
    # Deliberately corrupt the revision provenance to exercise the view's own
    # fixed-tree guard after capture has already been accepted.
    snapshot = captured.model_copy(update={"commit_oid": captured_revision})

    with pytest.raises(RepositoryViewError, match="External Git filter"):
        prepare_repository_view(snapshot)


@pytest.mark.parametrize("filter_value", [None, "set", "unset", "unspecified", "-"])
def test_filter_attribute_detection_is_fail_closed(
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

    monkeypatch.setattr(repository_view.subprocess, "run", fake_run)

    filters = repository_view._filter_attributes(tmp_path, "src/service.py")
    assert filters == ([] if filter_value is None else [filter_value])


def test_capture_rejects_current_external_filter_overlay(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / ".gitattributes").write_text(
        "src/service.py filter=external\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "-qm", "configure captured filter")

    with pytest.raises(RepositoryContextError) as error:
        capture_repository_context(repository / "src")
    assert error.value.code == "external_filter_unsupported"


def test_committed_resume_ignores_xdg_global_attributes_after_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    xdg_config = tmp_path / "xdg"
    (xdg_config / "git").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    repository = _repository(tmp_path / "resume")
    snapshot = capture_repository_context(repository / "src")
    initial_view = prepare_repository_view(snapshot)
    initial_view.close()
    (xdg_config / "git" / "attributes").write_text(
        "src/service.py filter=external\n",
        encoding="utf-8",
    )

    resumed_view = prepare_repository_view(snapshot, deterministic_resume=True)
    try:
        assert (resumed_view.materialized_root / "service.py").exists()
    finally:
        resumed_view.close()


def test_capture_rejects_xdg_global_external_filter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    xdg_config = tmp_path / "xdg"
    (xdg_config / "git").mkdir(parents=True)
    (xdg_config / "git" / "attributes").write_text(
        "src/service.py filter=external\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    repository = _repository(tmp_path / "capture")

    with pytest.raises(RepositoryContextError) as error:
        capture_repository_context(repository / "src")
    assert error.value.code == "external_filter_unsupported"


def test_tracked_worktree_filter_uses_current_info_attributes(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    snapshot = capture_repository_context(
        repository / "src",
        mode="tracked_worktree",
    )
    (repository / ".git" / "info" / "attributes").write_text(
        "src/service.py filter=external\n",
        encoding="utf-8",
    )

    with pytest.raises(RepositoryViewError, match="External Git filter"):
        prepare_repository_view(snapshot)


def test_prepare_rejects_deserialized_scope_root_or_prefix_mismatch(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    snapshot = capture_repository_context(repository / "src")
    outside = (tmp_path / "outside").resolve()
    outside.mkdir()

    for update in (
        {"analysis_root": outside},
        {"analysis_prefix": "other"},
    ):
        corrupted = snapshot.model_copy(update=update)
        with pytest.raises(RepositoryViewError) as error:
            prepare_repository_view(corrupted)
        assert error.value.code == "snapshot_scope_mismatch"


@pytest.mark.parametrize(
    ("record_update", "error_code"),
    [
        ({"git_path": "outside.py"}, "manifest_path_mismatch"),
        ({"tracked": False}, "untracked_manifest_entry"),
        ({"object_id": "0" * 40}, "manifest_tree_mismatch"),
        ({"mode": "100755"}, "manifest_tree_mismatch"),
        ({"git_path": "src/missing.py"}, "manifest_path_mismatch"),
    ],
)
def test_committed_view_validates_manifest_identity_against_captured_tree(
    tmp_path: Path,
    record_update: dict[str, object],
    error_code: str,
) -> None:
    repository = _repository(tmp_path)
    snapshot = capture_repository_context(repository / "src")
    first = snapshot.manifest[0].model_copy(update=record_update)
    corrupted = snapshot.model_copy(
        update={"manifest": (first, *snapshot.manifest[1:])},
    )

    with pytest.raises(RepositoryViewError) as error:
        prepare_repository_view(corrupted)
    assert error.value.code == error_code


def test_closed_view_rejects_path_lookup(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    view = prepare_repository_view(capture_repository_context(repository / "src"))
    view.close()

    with pytest.raises(RepositoryViewError) as error:
        view.path_for("service.py")
    assert error.value.code == "view_closed"


def test_tracked_worktree_rejects_symlinked_parent_directory(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    nested = repository / "src" / "nested"
    nested.mkdir()
    (nested / "service.py").write_text("value = True\n", encoding="utf-8")
    _git(repository, "add", "src/nested/service.py")
    _git(repository, "commit", "-qm", "add nested source")
    snapshot = capture_repository_context(
        repository / "src",
        mode="tracked_worktree",
    )

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "service.py").write_text("decoy = True\n", encoding="utf-8")
    (nested / "service.py").unlink()
    nested.rmdir()
    nested.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RepositoryViewError) as error:
        prepare_repository_view(snapshot)
    assert error.value.code == "worktree_parent_symlink"


def test_newline_filename_is_read_from_captured_blob(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    newline_path = repository / "src" / "line\nname.py"
    newline_path.write_text("value = 'captured'\n", encoding="utf-8")
    _git(repository, "add", "src/line\nname.py")
    _git(repository, "commit", "-qm", "add newline filename")
    snapshot = capture_repository_context(repository / "src")
    view = prepare_repository_view(snapshot)
    try:
        assert (view.materialized_root / "line\nname.py").read_text(encoding="utf-8") == (
            "value = 'captured'\n"
        )
    finally:
        view.close()


def test_tracked_worktree_omits_deleted_entries_and_rejects_deterministic_resume(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    (repository / "src" / "keep.py").write_text("keep = True\n", encoding="utf-8")
    _git(repository, "add", "src/keep.py")
    _git(repository, "commit", "-qm", "add second file")
    _git(repository, "rm", "-q", "src/service.py")
    snapshot = capture_repository_context(repository / "src", mode="tracked_worktree")
    view = prepare_repository_view(snapshot)
    try:
        assert view.files == ("keep.py",)
        assert not (view.materialized_root / "service.py").exists()
        assert view.representation == "worktree-current-content"
    finally:
        view.close()
    with pytest.raises(DeterministicResumeError):
        prepare_repository_view(snapshot, deterministic_resume=True)


def test_explicit_v2_workflow_uses_view_and_keeps_v1_entry_untouched(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    snapshot = capture_repository_context(repository / "src")
    issue = IssueRecord(
        number=7,
        title="refresh token fails",
        body="refresh_token returns the wrong value",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    result = run_protocol_v2_investigation([issue], snapshot)
    assert result.investigations
    assert result.investigations[0].candidates
    assert result.evidence_by_issue[7]
    assert not Path(result.repository_map.root).exists()


def test_history_and_blame_bind_to_captured_revision_and_git_root(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    (repository / "src" / "related.py").write_text(
        "def related():\n    return True\n",
        encoding="utf-8",
    )
    with (repository / "src" / "service.py").open("a", encoding="utf-8") as handle:
        handle.write("\n# changed with related\n")
    _git(repository, "add", "src/related.py", "src/service.py")
    _git(repository, "commit", "-qm", "change related files")
    snapshot = capture_repository_context(repository / "src")
    view = prepare_repository_view(snapshot)
    try:
        repository_map = build_repository_map(view)
        eligible = {"service.py", "related.py"}
        auxiliary = {path: False for path in eligible}
        history = _history_relations(
            Path(repository_map.root),
            ["service.py"],
            eligible,
            auxiliary,
            git_root=Path(repository_map.git_root),
            captured_revision=repository_map.captured_revision,
            analysis_prefix=repository_map.analysis_prefix,
        )
        blame = _blame_relations(
            Path(repository_map.root),
            ["service.py"],
            {"service.py": "3-3"},
            eligible,
            auxiliary,
            git_root=Path(repository_map.git_root),
            captured_revision=repository_map.captured_revision,
            analysis_prefix=repository_map.analysis_prefix,
        )
        assert "related.py" in history
        assert "related.py" in blame
    finally:
        view.close()


def test_sha256_view_history_and_blame_use_captured_revision(tmp_path: Path) -> None:
    repository = tmp_path / "sha256"
    (repository / "src").mkdir(parents=True)
    try:
        _git(repository, "init", "-q", "--object-format=sha256", "-b", "main")
    except subprocess.CalledProcessError as error:
        pytest.skip(f"Git SHA-256 repositories are unavailable: {error}")
    _git(repository, "config", "user.email", "tests@example.invalid")
    _git(repository, "config", "user.name", "Protocol v2 tests")
    (repository / "src" / "service.py").write_text(
        "def refresh_token():\n    return 'initial'\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "initial")
    (repository / "src" / "related.py").write_text(
        "def related():\n    return True\n",
        encoding="utf-8",
    )
    (repository / "src" / "service.py").write_text(
        "def refresh_token():\n    return 'captured'\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "cochange")
    snapshot = capture_repository_context(repository / "src")
    assert snapshot.commit_oid is not None
    assert len(snapshot.commit_oid) == 64

    with (repository / "src" / "service.py").open("w", encoding="utf-8") as handle:
        handle.write("def refresh_token():\n    return 'future'\n")
    (repository / "src" / "future.py").write_text(
        "def future():\n    return True\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "future cochange")

    view = prepare_repository_view(snapshot)
    try:
        repository_map = build_repository_map(view)
        eligible = {"service.py", "related.py", "future.py"}
        auxiliary = {path: False for path in eligible}
        history = _history_relations(
            Path(repository_map.root),
            ["service.py"],
            eligible,
            auxiliary,
            git_root=Path(repository_map.git_root),
            captured_revision=repository_map.captured_revision,
            analysis_prefix=repository_map.analysis_prefix,
        )
        blame = _blame_relations(
            Path(repository_map.root),
            ["service.py"],
            {"service.py": "2-2"},
            eligible,
            auxiliary,
            git_root=Path(repository_map.git_root),
            captured_revision=repository_map.captured_revision,
            analysis_prefix=repository_map.analysis_prefix,
        )
        assert "related.py" in history
        assert "future.py" not in history
        assert "related.py" in blame
        assert "future.py" not in blame
    finally:
        view.close()
