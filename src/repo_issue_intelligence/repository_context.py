"""Git/repository provenance capture for the opt-in Protocol v2 boundary.

The capture is intentionally read-only.  It does not materialize a source
view, build a repository map, sync Issues, or touch a database; those are later
PRs.  Paths are obtained from NUL-delimited Git output so a tracked filename
containing whitespace, Unicode, or a newline cannot change the manifest.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import NamedTuple
from urllib.parse import urlsplit

from .protocol_v2_models import (
    RepositoryCaptureMode,
    RepositoryFileRecord,
    RepositoryFileStatus,
    RepositorySnapshot,
    RepositoryStatusEntry,
)
from .run_configuration import _fully_percent_decode


class RepositoryContextError(ValueError):
    """Fail-closed repository capture error without echoing sensitive input."""

    def __init__(self, message: str, *, code: str = "repository_context_invalid") -> None:
        self.code = code
        super().__init__(message)


class _GitIndexEntry(NamedTuple):
    mode: str
    object_id: str
    stage: int
    path: str


class _Status(NamedTuple):
    path: str
    original_path: str | None
    index_status: str
    worktree_status: str
    untracked: bool
    deleted: bool
    staged_deleted: bool
    unmerged: bool


def _decode_git(data: bytes, *, context: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RepositoryContextError(
            f"Git returned non-UTF-8 {context}; capture refused",
            code="git_output_encoding",
        ) from error


def _run_git(
    git_root: Path,
    args: Sequence[str],
    *,
    check: bool = True,
) -> bytes:
    """Run a read-only Git command without exposing stderr or URL values."""

    try:
        result = subprocess.run(
            ["git", "-C", str(git_root), *args],
            check=False,
            capture_output=True,
            env={
                **os.environ,
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_NO_LAZY_FETCH": "1",
            },
        )
    except OSError as error:
        raise RepositoryContextError(
            "Git executable is unavailable; repository capture refused",
            code="git_unavailable",
        ) from error
    if check and result.returncode != 0:
        raise RepositoryContextError(
            f"Git command failed during repository capture ({args[0]})",
            code="git_command_failed",
        )
    return result.stdout


def _run_git_text(git_root: Path, args: Sequence[str], *, check: bool = True) -> str:
    return _decode_git(_run_git(git_root, args, check=check), context=args[0])


def _current_external_filter_paths(
    git_root: Path,
    git_paths: Sequence[str],
) -> tuple[str, ...]:
    """Read current attributes once, including local/info/global overlays."""

    if not git_paths:
        return ()
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(git_root),
                "check-attr",
                "-z",
                "--stdin",
                "--all",
            ],
            input=b"".join(path.encode("utf-8") + b"\0" for path in git_paths),
            check=False,
            capture_output=True,
            env={
                **os.environ,
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_NO_LAZY_FETCH": "1",
            },
        )
    except (OSError, UnicodeEncodeError) as error:
        raise RepositoryContextError(
            "Git attributes could not be read; capture refused",
            code="git_attribute_read_failed",
        ) from error
    if completed.returncode != 0:
        raise RepositoryContextError(
            "Git attributes could not be read; capture refused",
            code="git_attribute_read_failed",
        )
    fields = completed.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 3:
        raise RepositoryContextError(
            "Unexpected Git attribute output; capture refused",
            code="git_attribute_format",
        )
    external: set[str] = set()
    for index in range(0, len(fields), 3):
        path = _decode_git(fields[index], context="attribute path")
        attribute = _decode_git(fields[index + 1], context="attribute name")
        _decode_git(fields[index + 2], context="attribute value")
        # ``--all`` does not provide a safe discriminator for filter values:
        # values such as ``set``, ``unset``, ``unspecified`` and ``-`` may be
        # actual driver names.  Any returned filter triple is therefore
        # fail-closed; ordinary paths with no filter have no filter triple.
        if attribute == "filter":
            external.add(path)
    return tuple(sorted(external))


def _resolve_analysis_root(root: Path | str) -> Path:
    candidate = Path(root).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise RepositoryContextError(
            "Analysis root does not exist or cannot be resolved",
            code="analysis_root_missing",
        ) from error
    if not resolved.is_dir():
        raise RepositoryContextError(
            "Analysis root must be a directory",
            code="analysis_root_not_directory",
        )
    return resolved


def _git_root_for(path: Path) -> Path:
    raw = _run_git_text(path, ["rev-parse", "--show-toplevel"])
    try:
        root = Path(raw.strip()).expanduser().resolve(strict=True)
    except OSError as error:
        raise RepositoryContextError(
            "Git root cannot be resolved",
            code="git_root_invalid",
        ) from error
    if not root.is_dir():
        raise RepositoryContextError("Git root is not a directory", code="git_root_invalid")
    try:
        path.relative_to(root)
    except ValueError as error:
        raise RepositoryContextError(
            "Analysis root is outside the Git root",
            code="analysis_root_outside_git_root",
        ) from error
    return root


def _relative_posix(path: Path, base: Path, *, allow_empty: bool = False) -> str:
    try:
        relative = path.relative_to(base)
    except ValueError as error:
        raise RepositoryContextError(
            "Analysis path escapes the Git root",
            code="path_outside_git_root",
        ) from error
    value = PurePosixPath(*relative.parts).as_posix()
    if not value and not allow_empty:
        raise RepositoryContextError("Empty scoped path is not valid", code="empty_path")
    return value


def _validate_prefix(prefix: str) -> str:
    if not isinstance(prefix, str):
        raise RepositoryContextError("analysis_prefix must be a string", code="invalid_prefix")
    if "\x00" in prefix or "\\" in prefix:
        raise RepositoryContextError(
            "analysis_prefix contains an unsupported path separator",
            code="invalid_prefix",
        )
    if prefix in {"", "."}:
        return ""
    pure = PurePosixPath(prefix)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise RepositoryContextError(
            "analysis_prefix must stay within the Git root",
            code="invalid_prefix",
        )
    return pure.as_posix()


def _path_in_scope(git_path: str, prefix: str) -> bool:
    return not prefix or git_path == prefix or git_path.startswith(f"{prefix}/")


def _analysis_relative(git_path: str, prefix: str) -> str:
    if prefix:
        if git_path == prefix:
            return Path(git_path).name
        return git_path[len(prefix) + 1 :]
    return git_path


def _parse_status(raw: bytes) -> list[_Status]:
    records = raw.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    statuses: list[_Status] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 3 or record[2:3] != b" ":
            raise RepositoryContextError(
                "Unexpected NUL-delimited Git status record",
                code="git_status_format",
            )
        code = _decode_git(record[:2], context="status")
        path = _decode_git(record[3:], context="status path")
        original_path: str | None = None
        if code[0] in {"R", "C"} or code[1] in {"R", "C"}:
            if index >= len(records):
                raise RepositoryContextError(
                    "Git rename status omitted its original path",
                    code="git_status_format",
                )
            original_path = _decode_git(records[index], context="rename path")
            index += 1
        untracked = code == "??"
        unmerged = (
            "U" in code
            or code in {"AA", "AU", "UA", "DD", "DU", "UD", "UU"}
        )
        deleted = "D" in code
        statuses.append(
            _Status(
                path=path,
                original_path=original_path,
                index_status=code[0],
                worktree_status=code[1],
                untracked=untracked,
                deleted=deleted,
                staged_deleted=code[0] == "D",
                unmerged=unmerged,
            )
        )
    return statuses


def _parse_ls_files(raw: bytes) -> list[_GitIndexEntry]:
    entries: list[_GitIndexEntry] = []
    records = raw.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    for record in records:
        metadata, separator, path_bytes = record.partition(b"\t")
        if not separator:
            raise RepositoryContextError("Unexpected Git index record", code="git_index_format")
        values = metadata.split()
        if len(values) != 3:
            raise RepositoryContextError("Unexpected Git index metadata", code="git_index_format")
        try:
            stage = int(values[2])
        except ValueError as error:
            raise RepositoryContextError(
                "Unexpected Git index stage", code="git_index_format"
            ) from error
        entries.append(
            _GitIndexEntry(
                mode=_decode_git(values[0], context="index mode"),
                object_id=_decode_git(values[1], context="index object"),
                stage=stage,
                path=_decode_git(path_bytes, context="index path"),
            )
        )
    return entries


def _parse_ls_tree(raw: bytes) -> list[_GitIndexEntry]:
    entries: list[_GitIndexEntry] = []
    records = raw.split(b"\0")
    if records and records[-1] == b"":
        records.pop()
    for record in records:
        metadata, separator, path_bytes = record.partition(b"\t")
        if not separator:
            raise RepositoryContextError("Unexpected Git tree record", code="git_tree_format")
        values = metadata.split()
        if len(values) != 3:
            raise RepositoryContextError("Unexpected Git tree metadata", code="git_tree_format")
        entries.append(
            _GitIndexEntry(
                mode=_decode_git(values[0], context="tree mode"),
                object_id=_decode_git(values[2], context="tree object"),
                stage=0,
                path=_decode_git(path_bytes, context="tree path"),
            )
        )
    return entries


def _status_for_path(statuses: Iterable[_Status]) -> dict[str, _Status]:
    # A conflict can be represented by one porcelain row; retain that single
    # row and do not collapse staged deletion into an untracked/add entry.
    result: dict[str, _Status] = {}
    for status in statuses:
        previous = result.get(status.path)
        if previous is None or (previous.untracked and not status.untracked):
            result[status.path] = status
    return result


def _is_rename(status: _Status) -> bool:
    return status.index_status == "R" or status.worktree_status == "R"


def _manifest(
    *,
    git_root: Path,
    prefix: str,
    index_entries: list[_GitIndexEntry],
    head_entries: list[_GitIndexEntry],
    statuses: dict[str, _Status],
) -> tuple[
    tuple[RepositoryFileRecord, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    # HEAD preserves a path staged for deletion; index entries provide the
    # current mode/object for all other tracked paths.  Conflicted paths may
    # appear at multiple index stages, but the manifest intentionally has one
    # path row with ``unmerged=True``.
    by_path: dict[str, _GitIndexEntry] = {entry.path: entry for entry in head_entries}
    for entry in index_entries:
        by_path.setdefault(entry.path, entry)
        if entry.stage == 0:
            by_path[entry.path] = entry
    records: list[RepositoryFileRecord] = []
    deleted: list[str] = []
    staged_deleted: list[str] = []
    unmerged: list[str] = []
    symlinks: list[str] = []
    submodules: list[str] = []
    for git_path in sorted(by_path):
        if not _path_in_scope(git_path, prefix):
            continue
        entry = by_path[git_path]
        status = statuses.get(git_path)
        # HEAD still contains the old side of a staged rename, while the
        # index contains only the new path.  Do not expose the old blob as a
        # current worktree file; it is represented by deleted_paths below.
        if status is None and any(
            _is_rename(candidate) and candidate.original_path == git_path
            for candidate in statuses.values()
        ):
            continue
        status_code = f"{status.index_status}{status.worktree_status}" if status else "  "
        is_deleted = bool(status and status.deleted)
        is_staged_deleted = bool(status and status.staged_deleted)
        is_unmerged = bool(status and status.unmerged) or entry.stage > 0
        is_symlink = entry.mode == "120000"
        is_submodule = entry.mode == "160000"
        if is_deleted:
            file_status = RepositoryFileStatus.DELETED
            deleted.append(_analysis_relative(git_path, prefix))
        elif is_unmerged:
            file_status = RepositoryFileStatus.UNMERGED
            unmerged.append(_analysis_relative(git_path, prefix))
        elif status and (status.index_status in {"R"} or status.worktree_status in {"R"}):
            file_status = RepositoryFileStatus.RENAMED
        elif status and (status.index_status in {"C"} or status.worktree_status in {"C"}):
            file_status = RepositoryFileStatus.COPIED
        elif status and (status.index_status == "A" or status.worktree_status == "A"):
            file_status = RepositoryFileStatus.ADDED
        elif status and status_code != "  ":
            file_status = RepositoryFileStatus.MODIFIED
        else:
            file_status = RepositoryFileStatus.TRACKED
        if is_staged_deleted:
            staged_deleted.append(_analysis_relative(git_path, prefix))
        if is_symlink:
            symlinks.append(_analysis_relative(git_path, prefix))
        if is_submodule:
            submodules.append(_analysis_relative(git_path, prefix))
        records.append(
            RepositoryFileRecord(
                path=_analysis_relative(git_path, prefix),
                git_path=git_path,
                status=file_status,
                index_status=status.index_status if status else " ",
                worktree_status=status.worktree_status if status else " ",
                mode=entry.mode,
                object_id=entry.object_id,
                stage=entry.stage,
                deleted=is_deleted,
                staged_deleted=is_staged_deleted,
                unmerged=is_unmerged,
                symlink=is_symlink,
                submodule=is_submodule,
            )
        )
    return (
        tuple(records),
        tuple(sorted(set(deleted))),
        tuple(sorted(set(staged_deleted))),
        tuple(sorted(set(unmerged))),
        tuple(sorted(set(symlinks))),
        tuple(sorted(set(submodules))),
    )


def _decode_remote_component(value: str) -> str | None:
    decoded = _fully_percent_decode(value)
    if decoded is None or "?" in decoded or "#" in decoded:
        return None
    return decoded


def _normalize_remote_url(value: str) -> tuple[str | None, bool]:
    """Return a host/path identity and whether unsafe URL parts were removed."""

    text = value.strip()
    if not text:
        return None, False
    sanitized = False
    if "\x00" in text:
        return None, True
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None, True
    if parsed.query or parsed.fragment:
        # A query/fragment may carry a token.  Do not retain even a
        # supposedly harmless path identity when the source URL is not a safe
        # Git repository URL.
        return None, True

    # Git's scp-like SSH form has no URL scheme and uses a colon separator.
    if "://" not in text and ":" in text.split("/", 1)[0]:
        user_host, path = text.split(":", 1)
        decoded_host = _decode_remote_component(user_host)
        path_text = _decode_remote_component(path)
        if decoded_host is None or path_text is None:
            return None, True
        if "@" in decoded_host:
            _, host = decoded_host.rsplit("@", 1)
            sanitized = True
        else:
            host = decoded_host
    else:
        decoded_netloc = _decode_remote_component(parsed.netloc)
        path_text = _decode_remote_component(parsed.path)
        if decoded_netloc is None or path_text is None:
            return None, True
        if not decoded_netloc:
            return None, False
        if "@" in decoded_netloc:
            _, authority = decoded_netloc.rsplit("@", 1)
            sanitized = True
        else:
            authority = decoded_netloc
        try:
            authority_parts = urlsplit(f"//{authority}")
            host = authority_parts.hostname or ""
            port = authority_parts.port
        except ValueError:
            return None, True
        if authority_parts.path or authority_parts.query or authority_parts.fragment:
            return None, True
        if not host:
            return None, True
        if port is not None and port not in {22, 80, 443}:
            host = f"{host}:{port}"
    host = host.strip().lower().rstrip(".")
    path_text = path_text.replace("\\", "/").strip("/")
    if path_text.endswith(".git"):
        path_text = path_text[:-4].rstrip("/")
    if not host or not path_text or any(part in {"", ".", ".."} for part in path_text.split("/")):
        return None, sanitized
    return f"{host}/{path_text}", sanitized


def _remote_selection(
    git_root: Path,
    explicit_remote: str | None,
) -> tuple[str | None, str | None, list[str], bool]:
    names = [
        line.strip()
        for line in _run_git_text(git_root, ["remote"], check=False).splitlines()
        if line.strip()
    ]
    selected_name: str | None = None
    selection_source: str | None = None
    direct_urls: list[str] = []
    if explicit_remote:
        if explicit_remote in names:
            selected_name = explicit_remote
            selection_source = "explicit"
        elif "://" in explicit_remote or explicit_remote.startswith("git@"):
            direct_urls = [explicit_remote]
            selection_source = "explicit_url"
        else:
            raise RepositoryContextError(
                "Requested Git remote is not configured",
                code="remote_not_found",
            )
    elif "origin" in names:
        selected_name = "origin"
        selection_source = "origin"
    elif len(names) == 1:
        selected_name = names[0]
        selection_source = "unique"
    elif len(names) > 1:
        selection_source = "ambiguous"
    if selected_name is not None:
        raw = _run_git_text(
            git_root,
            ["remote", "get-url", "--all", selected_name],
            check=False,
        )
        direct_urls = [line.strip() for line in raw.splitlines() if line.strip()]
    # An explicitly selected remote with multiple URLs is still ambiguous; a
    # caller must choose a single URL explicitly rather than guessing fetch vs
    # push or mirror order.
    unique_urls = list(dict.fromkeys(direct_urls))
    return selected_name, selection_source, unique_urls, len(unique_urls) > 1


def _git_metadata_kind(git_root: Path) -> tuple[str, Path | None]:
    dot_git = git_root / ".git"
    if dot_git.is_file():
        return "file", dot_git
    if dot_git.is_dir():
        return "directory", dot_git
    raw = _run_git_text(git_root, ["rev-parse", "--git-dir"], check=False).strip()
    if raw:
        git_dir = Path(raw)
        if not git_dir.is_absolute():
            git_dir = (git_root / git_dir).resolve()
        return ("directory" if git_dir.is_dir() else "unknown"), git_dir
    return "unknown", None


def capture_repository_context(
    root: Path | str,
    mode: RepositoryCaptureMode | str = RepositoryCaptureMode.COMMITTED,
    *,
    analysis_prefix: str | None = None,
    remote: str | None = None,
    captured_at: datetime | None = None,
) -> RepositorySnapshot:
    """Capture repository identity and a tracked, scoped manifest.

    ``committed`` is the strict default: tracked modifications, staged/deleted
    scope files, and conflicts are reported and rejected.  A caller that needs
    to inspect dirty facts without claiming deterministic committed input can
    explicitly use ``tracked_worktree``.
    Untracked files never enter ``manifest``; their scoped paths/count remain
    visible for audit.
    """

    try:
        capture_mode = RepositoryCaptureMode(mode)
    except ValueError as error:
        raise RepositoryContextError(
            "Unsupported repository capture mode", code="invalid_mode"
        ) from error
    analysis_root = _resolve_analysis_root(root)
    git_root = _git_root_for(analysis_root)
    actual_prefix = _relative_posix(
        git_root if analysis_root == git_root else analysis_root,
        git_root,
        allow_empty=True,
    )
    actual_prefix = _validate_prefix(actual_prefix)
    if analysis_prefix is not None and _validate_prefix(analysis_prefix) != actual_prefix:
        raise RepositoryContextError(
            "analysis_prefix does not match analysis root",
            code="prefix_mismatch",
        )
    prefix = actual_prefix
    # Capture the revision once and use that exact object for the manifest.
    # Reading ``HEAD`` again for ls-tree would allow a concurrent checkout to
    # mix one identity with another tree.
    commit_oid = _run_git_text(git_root, ["rev-parse", "--verify", "HEAD^{commit}"]).strip()
    branch_raw = _run_git_text(
        git_root,
        ["symbolic-ref", "--quiet", "--short", "HEAD"],
        check=False,
    ).strip()
    branch = branch_raw or None
    detached = branch is None
    git_kind, git_dir = _git_metadata_kind(git_root)

    status_raw = _run_git(git_root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    parsed_statuses = _parse_status(status_raw)
    status_by_path = _status_for_path(parsed_statuses)
    scope_statuses = [
        status
        for status in parsed_statuses
        if _path_in_scope(status.path, prefix)
        or (status.original_path is not None and _path_in_scope(status.original_path, prefix))
    ]
    untracked_paths = tuple(
        sorted(
            _analysis_relative(status.path, prefix)
            for status in scope_statuses
            if status.untracked and _path_in_scope(status.path, prefix)
        )
    )
    scope_tracked_changes = tuple(
        status for status in scope_statuses if not status.untracked
    )
    analysis_scope_dirty = bool(scope_tracked_changes)
    git_root_dirty = bool(parsed_statuses)
    deleted_paths = tuple(
        sorted(
            {
                _analysis_relative(status.path, prefix)
                for status in scope_tracked_changes
                if status.deleted and _path_in_scope(status.path, prefix)
            }
            | {
                _analysis_relative(status.original_path, prefix)
                for status in scope_tracked_changes
                if (
                    _is_rename(status)
                    and status.original_path is not None
                    and _path_in_scope(status.original_path, prefix)
                )
            }
        )
    )
    staged_deleted_paths = tuple(
        sorted(
            {
                _analysis_relative(status.path, prefix)
                for status in scope_tracked_changes
                if status.staged_deleted and _path_in_scope(status.path, prefix)
            }
        )
    )
    unmerged_paths = tuple(
        sorted(
            {
                _analysis_relative(status.path, prefix)
                for status in scope_tracked_changes
                if status.unmerged and _path_in_scope(status.path, prefix)
            }
        )
    )
    index_args = ["ls-files", "--stage", "-z", "--full-name", "--"]
    tree_args = ["ls-tree", "-r", "-z", "--full-name", commit_oid, "--"]
    if prefix:
        index_args.append(prefix)
        tree_args.append(prefix)
    index_entries = _parse_ls_files(_run_git(git_root, index_args))
    head_entries = _parse_ls_tree(_run_git(git_root, tree_args))
    (
        manifest,
        manifest_deleted,
        manifest_staged_deleted,
        manifest_unmerged,
        symlink_paths,
        submodule_paths,
    ) = _manifest(
        git_root=git_root,
        prefix=prefix,
        index_entries=index_entries,
        head_entries=head_entries,
        # Untracked rows remain in ``status_entries`` and the scoped audit
        # counters, but can never drive a tracked manifest row.  In
        # particular, a recreated rename source must still be skipped when
        # the tracked destination's rename record names it as original_path.
        statuses={path: status for path, status in status_by_path.items() if not status.untracked},
    )
    external_filter_paths = _current_external_filter_paths(
        git_root,
        tuple(record.git_path for record in manifest),
    )
    if external_filter_paths:
        raise RepositoryContextError(
            "External Git filter input is not supported in the captured scope",
            code="external_filter_unsupported",
        )
    deleted_paths = tuple(sorted(set(deleted_paths) | set(manifest_deleted)))
    staged_deleted_paths = tuple(sorted(set(staged_deleted_paths) | set(manifest_staged_deleted)))
    unmerged_paths = tuple(sorted(set(unmerged_paths) | set(manifest_unmerged)))
    unsupported_paths = tuple(sorted(set(symlink_paths) | set(submodule_paths)))
    status_entries = tuple(
        RepositoryStatusEntry(
            path=_analysis_relative(status.path, prefix)
            if _path_in_scope(status.path, prefix)
            else status.path,
            original_path=(
                _analysis_relative(status.original_path, prefix)
                if status.original_path is not None and _path_in_scope(status.original_path, prefix)
                else status.original_path
            ),
            index_status=status.index_status,
            worktree_status=status.worktree_status,
            untracked=status.untracked,
            deleted=status.deleted,
            staged_deleted=status.staged_deleted,
            unmerged=status.unmerged,
        )
        for status in parsed_statuses
    )
    remote_name, remote_source, urls, multiple_urls = _remote_selection(git_root, remote)
    normalized_identity: str | None = None
    remote_sanitized = False
    if len(urls) == 1:
        normalized_identity, remote_sanitized = _normalize_remote_url(urls[0])
    elif multiple_urls:
        remote_sanitized = False
    timestamp = captured_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise RepositoryContextError("captured_at must be timezone-aware", code="invalid_timestamp")
    snapshot = RepositorySnapshot(
        git_root=git_root,
        analysis_root=analysis_root,
        analysis_prefix=prefix,
        commit_oid=commit_oid,
        branch=branch,
        detached=detached,
        mode=capture_mode,
        git_metadata_kind=git_kind,
        git_dir=git_dir,
        analysis_scope_dirty=analysis_scope_dirty,
        git_root_dirty=git_root_dirty,
        untracked_in_scope_count=len(untracked_paths),
        untracked_in_scope_paths=untracked_paths,
        status_entries=status_entries,
        manifest=manifest,
        deleted_paths=deleted_paths,
        staged_deleted_paths=staged_deleted_paths,
        unmerged_paths=unmerged_paths,
        symlink_paths=symlink_paths,
        submodule_paths=submodule_paths,
        unsupported_paths=unsupported_paths,
        remote_name=remote_name,
        remote_selection_source=remote_source,
        remote_url_count=len(urls),
        remote_sanitized=remote_sanitized,
        normalized_remote_identity=normalized_identity,
        repository_identity=normalized_identity,
        captured_at=timestamp.astimezone(UTC),
    )
    if snapshot.unmerged_paths:
        raise RepositoryContextError(
            "Unresolved merge conflicts are not supported in a captured scope",
            code="unmerged_scope",
        )
    if capture_mode is RepositoryCaptureMode.COMMITTED and snapshot.analysis_scope_dirty:
        raise RepositoryContextError(
            "Committed capture requires a clean tracked analysis scope",
            code="analysis_scope_dirty",
        )
    return snapshot


__all__ = [
    "RepositoryContextError",
    "capture_repository_context",
]
