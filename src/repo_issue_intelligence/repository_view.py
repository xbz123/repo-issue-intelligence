"""Run-scoped, fixed-input repository views for Protocol v2.

The V1 workflow reads the checkout directly.  Protocol v2 uses this module as
an explicit boundary: a committed run is materialized from the captured Git
tree using raw blobs, while ``tracked_worktree`` copies only the tracked files
currently present in the worktree.  Both modes expose one temporary root to
the indexer and evidence collector, so those consumers cannot accidentally
read a later checkout or an untracked decoy.

This is deliberately a small, process-local view.  It is not a sandbox and it
does not promise that a concurrent process cannot change the source checkout
while a tracked-worktree capture is being copied.  Once a view has been
materialized, however, the view itself is stable until it is closed.
"""

from __future__ import annotations

import os
import posixpath
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from re import fullmatch
from typing import Any

from .protocol_v2_models import (
    RepositoryCaptureMode,
    RepositoryFileRecord,
    RepositorySnapshot,
)


class RepositoryViewError(ValueError):
    """Fail-closed error raised while preparing a materialized view."""

    def __init__(self, message: str, *, code: str) -> None:
        self.code = code
        super().__init__(message)


class DeterministicResumeError(RepositoryViewError):
    """Raised when a tracked-worktree view is used as deterministic input."""

    def __init__(self) -> None:
        super().__init__(
            "tracked_worktree input does not support deterministic resume; start a new run",
            code="tracked_worktree_resume_unsupported",
        )


def _safe_relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RepositoryViewError("Manifest path is invalid", code="invalid_manifest_path")
    if "\\" in value:
        raise RepositoryViewError(
            "Manifest path contains an unsupported separator",
            code="invalid_manifest_path",
        )
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RepositoryViewError(
            "Manifest path escapes the materialized root",
            code="manifest_path_escape",
        )
    return path


def _safe_git_revision(value: str | None) -> str:
    if not value or fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value) is None:
        raise RepositoryViewError(
            "Committed capture is missing a valid captured revision",
            code="missing_captured_revision",
        )
    return value


def _path_in_scope(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(f"{prefix}/")


def _run_git(git_root: Path, args: list[str]) -> bytes:
    """Run a read-only Git object/attribute query without filters or fetches."""

    try:
        completed = subprocess.run(
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
        raise RepositoryViewError(
            "Git executable is unavailable; repository view refused",
            code="git_unavailable",
        ) from error
    if completed.returncode != 0:
        raise RepositoryViewError(
            "Git object query failed; repository view refused",
            code="git_object_read_failed",
        )
    return completed.stdout


def _raw_blob(git_root: Path, revision: str, git_path: str) -> bytes:
    # ``git cat-file blob <rev>:<path>`` returns the stored object bytes and
    # does not invoke checkout filters, hooks, smudge, textconv, or network.
    if (
        "\x00" in git_path
        or git_path.startswith("/")
        or any(part in {"", ".", ".."} for part in git_path.split("/"))
    ):
        raise RepositoryViewError("Tracked path is invalid", code="invalid_manifest_path")
    return _run_git(git_root, ["cat-file", "blob", f"{revision}:{git_path}"])


def _tree_entry(
    git_root: Path,
    revision: str,
    git_path: str,
) -> tuple[str, str, str, str] | None:
    """Read one exact tree entry without consulting the mutable index."""

    path = _safe_relative_path(git_path).as_posix()
    raw = _run_git(
        git_root,
        [
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            revision,
            "--",
            f":(literal){path}",
        ],
    )
    records = [record for record in raw.split(b"\0") if record]
    if len(records) != 1:
        return None
    metadata, separator, returned_path = records[0].partition(b"\t")
    if not separator:
        raise RepositoryViewError(
            "Unexpected captured tree record; repository view refused",
            code="git_tree_format",
        )
    values = metadata.split()
    if len(values) != 3:
        raise RepositoryViewError(
            "Unexpected captured tree metadata; repository view refused",
            code="git_tree_format",
        )
    try:
        returned_text = returned_path.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RepositoryViewError(
            "Captured tree path is not UTF-8; repository view refused",
            code="git_tree_encoding",
        ) from error
    return values[0].decode(), values[1].decode(), values[2].decode(), returned_text


def _is_lfs_pointer(data: bytes) -> bool:
    # Do not call git-lfs or download the object.  A pointer is not source
    # code and must never silently enter an analysis request.
    header = b"version https://git-lfs.github.com/spec/v1"
    return data.startswith(header + b"\n") or data.startswith(header + b"\r\n")


def _attribute_query_environment(*, alternate_objects: Path | None = None) -> dict[str, str]:
    environment = {
        **os.environ,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1",
    }
    if alternate_objects is not None:
        # The temporary repository has no index/info attributes.  It reads
        # only the captured tree's objects through this read-only alternate.
        environment.update(
            {
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_ATTR_NOSYSTEM": "1",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(alternate_objects),
            }
        )
    return environment


class _CapturedAttributeContext:
    """One isolated Git context for all paths in a committed view."""

    def __init__(self, git_root: Path, revision: str) -> None:
        self.git_root = git_root
        self.revision = revision
        self._temporary: Any = None
        self._isolated_root: Path | None = None
        self._object_directory: Path | None = None

    def __enter__(self) -> _CapturedAttributeContext:
        try:
            object_format = _run_git(
                self.git_root,
                ["rev-parse", "--show-object-format"],
            ).decode("ascii").strip()
            object_directory = Path(
                _run_git(self.git_root, ["rev-parse", "--git-path", "objects"])
                .decode("utf-8")
                .strip()
            )
            if not object_directory.is_absolute():
                object_directory = self.git_root / object_directory
            object_directory = object_directory.resolve(strict=True)
        except (UnicodeDecodeError, OSError, ValueError) as error:
            raise RepositoryViewError(
                "Git attribute object store could not be isolated",
                code="git_attribute_read_failed",
            ) from error
        if object_format not in {"sha1", "sha256"}:
            raise RepositoryViewError(
                "Unsupported Git object format for captured attributes",
                code="git_attribute_read_failed",
            )
        self._temporary = tempfile.TemporaryDirectory(prefix="rii-v2-attrs-")
        self._isolated_root = Path(self._temporary.name)
        self._object_directory = object_directory
        try:
            initialized = subprocess.run(
                [
                    "git",
                    "init",
                    "--quiet",
                    "--template=",
                    f"--object-format={object_format}",
                    str(self._isolated_root),
                ],
                check=False,
                capture_output=True,
                env=_attribute_query_environment(
                    alternate_objects=object_directory,
                ),
            )
        except OSError as error:
            self.close()
            raise RepositoryViewError(
                "Git attribute query failed; repository view refused",
                code="git_attribute_read_failed",
            ) from error
        if initialized.returncode != 0:
            self.close()
            raise RepositoryViewError(
                "Git attribute query failed; repository view refused",
                code="git_attribute_read_failed",
            )
        return self

    def query(self, git_path: str) -> bytes:
        if self._isolated_root is None or self._object_directory is None:
            raise RepositoryViewError(
                "Git attribute context is closed",
                code="git_attribute_read_failed",
            )
        try:
            checked = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self._isolated_root),
                    "-c",
                    "core.attributesFile=/dev/null",
                    "check-attr",
                    f"--source={self.revision}",
                    "-z",
                    "--all",
                    "--",
                    git_path,
                ],
                check=False,
                capture_output=True,
                env=_attribute_query_environment(
                    alternate_objects=self._object_directory,
                ),
            )
        except OSError as error:
            raise RepositoryViewError(
                "Git attribute query failed; repository view refused",
                code="git_attribute_read_failed",
            ) from error
        if checked.returncode != 0:
            raise RepositoryViewError(
                "Git attribute query failed; repository view refused",
                code="git_attribute_read_failed",
            )
        return checked.stdout

    def external_filter_paths(self, git_paths: Iterable[str]) -> frozenset[str]:
        paths = tuple(git_paths)
        if not paths:
            return frozenset()
        if self._isolated_root is None or self._object_directory is None:
            raise RepositoryViewError(
                "Git attribute context is closed",
                code="git_attribute_read_failed",
            )
        try:
            checked = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self._isolated_root),
                    "-c",
                    "core.attributesFile=/dev/null",
                    "check-attr",
                    f"--source={self.revision}",
                    "-z",
                    "--stdin",
                    "--all",
                ],
                input=b"".join(path.encode("utf-8") + b"\0" for path in paths),
                check=False,
                capture_output=True,
                env=_attribute_query_environment(
                    alternate_objects=self._object_directory,
                ),
            )
        except (OSError, UnicodeEncodeError) as error:
            raise RepositoryViewError(
                "Git attribute query failed; repository view refused",
                code="git_attribute_read_failed",
            ) from error
        if checked.returncode != 0:
            raise RepositoryViewError(
                "Git attribute query failed; repository view refused",
                code="git_attribute_read_failed",
            )
        fields = checked.stdout.split(b"\0")
        if fields and fields[-1] == b"":
            fields.pop()
        if len(fields) % 3:
            raise RepositoryViewError(
                "Unexpected Git attribute output; repository view refused",
                code="git_attribute_format",
            )
        external: set[str] = set()
        try:
            for index in range(0, len(fields), 3):
                path = fields[index].decode("utf-8")
                attribute = fields[index + 1].decode("utf-8")
                fields[index + 2].decode("utf-8")
                # ``--all`` cannot distinguish status words from real filter
                # driver names.  Reject every returned filter triple rather
                # than guessing; a path with no filter has no such triple.
                if attribute == "filter":
                    external.add(path)
        except UnicodeDecodeError as error:
            raise RepositoryViewError(
                "Git attribute output is not UTF-8; repository view refused",
                code="git_attribute_encoding",
            ) from error
        return frozenset(external)

    def close(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        self._isolated_root = None
        self._object_directory = None

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()


def _captured_attribute_query(
    git_root: Path,
    revision: str,
    git_path: str,
) -> bytes:
    """Read attributes from a captured tree without current repo overlays."""

    with _CapturedAttributeContext(git_root, revision) as context:
        return context.query(git_path)


def _filter_attributes(
    git_root: Path,
    git_path: str,
    *,
    revision: str | None = None,
    captured_context: _CapturedAttributeContext | None = None,
) -> list[str]:
    """Return configured external filters for one cached tracked path.

    ``git check-attr`` only reads attributes.  It does not run a filter.  A
    non-empty filter attribute is rejected because resolving its representation
    would require an external filter/smudge process, which this boundary never
    starts.  Committed callers pass ``--source=<captured revision>``; the
    tracked-worktree caller intentionally observes current worktree attrs.
    """

    if revision is not None:
        raw_attributes = (
            captured_context.query(git_path)
            if captured_context is not None
            else _captured_attribute_query(git_root, revision, git_path)
        )
    else:
        try:
            completed = subprocess.run(
                [
                    "git",
                    "-C",
                    str(git_root),
                    "check-attr",
                    "-z",
                    "--all",
                    "--",
                    git_path,
                ],
                check=False,
                capture_output=True,
                env=_attribute_query_environment(),
            )
        except OSError as error:
            raise RepositoryViewError(
                "Git attribute query failed; repository view refused",
                code="git_attribute_read_failed",
            ) from error
        if completed.returncode != 0:
            raise RepositoryViewError(
                "Git attribute query failed; repository view refused",
                code="git_attribute_read_failed",
            )
        raw_attributes = completed.stdout
    try:
        fields = raw_attributes.split(b"\0")
    except (AttributeError, UnicodeDecodeError) as error:
        raise RepositoryViewError(
            "Git attribute output is not UTF-8; repository view refused",
            code="git_attribute_encoding",
        ) from error
    try:
        decoded_fields = [field.decode("utf-8") for field in fields if field]
    except UnicodeDecodeError as error:
        raise RepositoryViewError(
            "Git attribute output is not UTF-8; repository view refused",
            code="git_attribute_encoding",
        ) from error
    if len(decoded_fields) % 3:
        raise RepositoryViewError(
            "Unexpected Git attribute output; repository view refused",
            code="git_attribute_format",
        )
    filters: list[str] = []
    for index in range(0, len(decoded_fields), 3):
        _, attribute, value = decoded_fields[index : index + 3]
        # ``--all`` cannot distinguish status words from real filter driver
        # names.  Preserve every returned filter value so callers reject it;
        # ordinary paths with no filter have no filter triple.
        if attribute == "filter":
            filters.append(value)
    return filters


def _manifest_by_path(manifest: Iterable[RepositoryFileRecord]) -> dict[str, RepositoryFileRecord]:
    result: dict[str, RepositoryFileRecord] = {}
    for record in manifest:
        path = _safe_relative_path(record.path).as_posix()
        if path in result:
            raise RepositoryViewError(
                "Captured manifest contains duplicate paths",
                code="duplicate_manifest_path",
            )
        result[path] = record
    return result


def _validated_manifest_git_path(
    record: RepositoryFileRecord,
    path: str,
    prefix: str,
) -> str:
    """Bind an analysis-relative manifest path to its repository path."""

    git_path = _safe_relative_path(record.git_path).as_posix()
    expected_git_path = f"{prefix}/{path}" if prefix else path
    if git_path != expected_git_path or not _path_in_scope(git_path, prefix):
        raise RepositoryViewError(
            "Captured manifest path does not match its analysis scope",
            code="manifest_path_mismatch",
        )
    if not record.tracked:
        raise RepositoryViewError(
            "Captured manifest contains an untracked entry",
            code="untracked_manifest_entry",
        )
    return git_path


def _symlink_target_from_bytes(data: bytes) -> str:
    try:
        target = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RepositoryViewError(
            "Tracked symlink target is not UTF-8; repository view refused",
            code="invalid_symlink_target",
        ) from error
    if not target or "\x00" in target or "\n" in target or "\r" in target:
        raise RepositoryViewError(
            "Tracked symlink target is invalid",
            code="invalid_symlink_target",
        )
    return target


def _normalized_link_target(source: str, target: str) -> str:
    if target.startswith("/") or "\\" in target:
        raise RepositoryViewError(
            "Absolute or unsupported symlink target is not allowed",
            code="symlink_target_escape",
        )
    source_parent = posixpath.dirname(source)
    resolved = posixpath.normpath(posixpath.join(source_parent, target))
    if resolved in {"", "."} or resolved == ".." or resolved.startswith("../"):
        raise RepositoryViewError(
            "Symlink target escapes the analysis scope",
            code="symlink_target_escape",
        )
    return resolved


def _resolve_symlink_chain(
    path: str,
    links: dict[str, str],
    regular_paths: set[str],
) -> str:
    seen: set[str] = set()
    current = path
    while current in links:
        if current in seen:
            raise RepositoryViewError(
                "Symlink loop is not supported",
                code="symlink_loop",
            )
        seen.add(current)
        current = links[current]
    if current not in regular_paths:
        raise RepositoryViewError(
            "Symlink target is not a tracked regular file in the scope",
            code="symlink_target_unsupported",
        )
    return current


class RepositoryView:
    """One immutable-once-prepared source root used by V2 index/evidence."""

    def __init__(
        self,
        *,
        snapshot: RepositorySnapshot,
        materialized_root: Path,
        manifest: tuple[RepositoryFileRecord, ...],
        representation: str,
    ) -> None:
        self.snapshot = snapshot
        self.materialized_root = materialized_root
        self.root = materialized_root
        self.git_root = snapshot.git_root
        self.analysis_root = snapshot.analysis_root
        self.analysis_prefix = snapshot.analysis_prefix
        self.captured_revision = snapshot.commit_oid
        self.manifest = manifest
        self.captured_manifest = snapshot.manifest
        self.representation = representation
        self._closed = False

    @property
    def supports_deterministic_resume(self) -> bool:
        return self.snapshot.mode is RepositoryCaptureMode.COMMITTED

    @property
    def can_resume_deterministically(self) -> bool:
        return self.supports_deterministic_resume

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(record.path for record in self.manifest)

    def path_for(self, relative_path: str) -> Path:
        if self._closed:
            raise RepositoryViewError(
                "Repository view is already closed",
                code="view_closed",
            )
        relative = _safe_relative_path(relative_path)
        if relative.as_posix() not in set(self.files):
            raise RepositoryViewError(
                "Path is not part of the captured materialized manifest",
                code="path_not_in_manifest",
            )
        return self.materialized_root.joinpath(*relative.parts)

    def __enter__(self) -> RepositoryView:
        if self._closed:
            raise RepositoryViewError("Repository view is already closed", code="view_closed")
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        shutil.rmtree(self.materialized_root, ignore_errors=True)

    def __del__(self) -> None:
        # Best-effort cleanup for callers that did not use the context-manager
        # form.  The explicit close path remains the lifecycle contract.
        try:
            self.close()
        except Exception:
            pass


def _copy_regular(source: Path, destination: Path) -> None:
    try:
        data = source.read_bytes()
    except (OSError, UnicodeError) as error:
        raise RepositoryViewError(
            "Tracked worktree content could not be read",
            code="worktree_read_failed",
        ) from error
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.write_bytes(data)
    except OSError as error:
        raise RepositoryViewError(
            "Materialized file could not be written",
            code="materialization_failed",
        ) from error


def _validate_snapshot_scope(snapshot: RepositorySnapshot) -> None:
    """Validate deserialized paths before reading a tracked worktree."""

    if not snapshot.git_root.is_absolute() or not snapshot.analysis_root.is_absolute():
        raise RepositoryViewError(
            "Repository snapshot paths must be absolute",
            code="snapshot_scope_invalid",
        )
    try:
        git_root = snapshot.git_root.expanduser().resolve(strict=True)
    except OSError as error:
        raise RepositoryViewError(
            "Captured Git root is unavailable",
            code="git_root_invalid",
        ) from error
    if snapshot.git_root != git_root or not git_root.is_dir():
        raise RepositoryViewError(
            "Captured Git root is not canonical",
            code="git_root_invalid",
        )
    try:
        actual_root = Path(
            _run_git(git_root, ["rev-parse", "--show-toplevel"])
            .decode("utf-8")
            .strip()
        ).expanduser().resolve(strict=True)
    except (OSError, UnicodeDecodeError, RepositoryViewError) as error:
        raise RepositoryViewError(
            "Captured Git root is not a real repository root",
            code="git_root_invalid",
        ) from error
    if actual_root != git_root:
        raise RepositoryViewError(
            "Captured Git root does not match the repository",
            code="git_root_mismatch",
        )

    try:
        prefix_path = (
            _safe_relative_path(snapshot.analysis_prefix)
            if snapshot.analysis_prefix
            else None
        )
    except RepositoryViewError as error:
        raise RepositoryViewError(
            "Captured analysis prefix is invalid",
            code="snapshot_scope_invalid",
        ) from error
    expected_root = (
        git_root.joinpath(*prefix_path.parts)
        if prefix_path is not None
        else git_root
    )
    expected_resolved = expected_root.resolve(strict=False)
    if not expected_resolved.is_relative_to(git_root):
        raise RepositoryViewError(
            "Captured analysis prefix escapes the Git root",
            code="snapshot_scope_escape",
        )
    try:
        analysis_root = snapshot.analysis_root.expanduser().resolve(strict=False)
    except OSError as error:
        raise RepositoryViewError(
            "Captured analysis root cannot be resolved",
            code="snapshot_scope_invalid",
        ) from error
    if analysis_root != expected_resolved:
        raise RepositoryViewError(
            "Captured analysis root does not match its Git prefix",
            code="snapshot_scope_mismatch",
        )
    if snapshot.mode is RepositoryCaptureMode.TRACKED_WORKTREE:
        try:
            if (
                not snapshot.analysis_root.is_dir()
                or snapshot.analysis_root.resolve(strict=True) != analysis_root
            ):
                raise RepositoryViewError(
                    "Tracked worktree analysis root is unavailable",
                    code="snapshot_scope_invalid",
                )
        except OSError as error:
            raise RepositoryViewError(
                "Tracked worktree analysis root is unavailable",
                code="snapshot_scope_invalid",
            ) from error


def _load_committed_content(
    snapshot: RepositorySnapshot,
    revision: str,
    prefix: str,
    manifest_by_path: dict[str, RepositoryFileRecord],
) -> tuple[dict[str, bytes], dict[str, str], set[str]]:
    raw_by_path: dict[str, bytes] = {}
    links: dict[str, str] = {}
    regular_paths: set[str] = set()
    validated_git_paths = {
        path: _validated_manifest_git_path(record, path, prefix)
        for path, record in sorted(manifest_by_path.items())
    }
    with _CapturedAttributeContext(snapshot.git_root, revision) as captured_context:
        external_filter_paths = captured_context.external_filter_paths(
            validated_git_paths.values()
        )
        for path, record in sorted(manifest_by_path.items()):
            git_path = validated_git_paths[path]
            if record.unmerged or record.deleted or record.submodule or record.stage:
                raise RepositoryViewError(
                    "Unsupported tracked entry in committed scope",
                    code="unsupported_manifest_entry",
                )
            if record.mode == "160000" or record.submodule:
                raise RepositoryViewError(
                    "Gitlink/submodule input is not supported",
                    code="gitlink_unsupported",
                )
            tree_entry = _tree_entry(snapshot.git_root, revision, git_path)
            if tree_entry is None:
                raise RepositoryViewError(
                    "Captured manifest entry is missing from the captured tree",
                    code="manifest_tree_mismatch",
                )
            tree_mode, tree_type, tree_oid, tree_path = tree_entry
            expected_type = "commit" if record.mode == "160000" else "blob"
            if (
                tree_path != git_path
                or record.mode != tree_mode
                or record.object_id != tree_oid
                or tree_type != expected_type
                or record.symlink != (tree_mode == "120000")
                or record.submodule != (tree_mode == "160000")
            ):
                raise RepositoryViewError(
                    "Captured manifest entry does not match the captured tree",
                    code="manifest_tree_mismatch",
                )
            if git_path in external_filter_paths:
                raise RepositoryViewError(
                    "External Git filter input is not supported",
                    code="external_filter_unsupported",
                )
            data = _raw_blob(snapshot.git_root, revision, git_path)
            raw_by_path[path] = data
            if record.symlink or record.mode == "120000":
                links[path] = _normalized_link_target(path, _symlink_target_from_bytes(data))
            else:
                if _is_lfs_pointer(data):
                    raise RepositoryViewError(
                        "Git LFS pointer input is not source content",
                        code="lfs_pointer_unsupported",
                    )
                regular_paths.add(path)
    return raw_by_path, links, regular_paths


def _prepare_committed(
    snapshot: RepositorySnapshot,
    root: Path,
) -> tuple[tuple[RepositoryFileRecord, ...], str]:
    revision = _safe_git_revision(snapshot.commit_oid)
    manifest_by_path = _manifest_by_path(snapshot.manifest)
    prefix = snapshot.analysis_prefix
    if prefix:
        prefix = _safe_relative_path(prefix).as_posix()
    if snapshot.analysis_scope_dirty or snapshot.deleted_paths or snapshot.staged_deleted_paths:
        raise RepositoryViewError(
            "Committed view requires a clean tracked analysis scope",
            code="analysis_scope_dirty",
        )
    if snapshot.unmerged_paths:
        raise RepositoryViewError(
            "Unresolved merge conflicts are not supported in a committed view",
            code="unmerged_scope",
        )
    raw_by_path, links, regular_paths = _load_committed_content(
        snapshot,
        revision,
        prefix,
        manifest_by_path,
    )

    final_targets = {
        path: _resolve_symlink_chain(path, links, regular_paths)
        for path in links
    }
    for path, record in sorted(manifest_by_path.items()):
        destination = root.joinpath(*_safe_relative_path(path).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path in links:
            target = final_targets[path]
            relative_target = posixpath.relpath(target, posixpath.dirname(path) or ".")
            os.symlink(relative_target, destination)
            continue
        _copy_regular_bytes(raw_by_path[path], destination)
        if record.mode == "100755":
            try:
                destination.chmod(0o755)
            except OSError as error:
                raise RepositoryViewError(
                    "Executable mode could not be preserved",
                    code="materialization_failed",
                ) from error
    return snapshot.manifest, "raw-blob"


def _copy_regular_bytes(data: bytes, destination: Path) -> None:
    try:
        destination.write_bytes(data)
    except OSError as error:
        raise RepositoryViewError(
            "Materialized file could not be written",
            code="materialization_failed",
        ) from error


def _prepare_tracked_worktree(
    snapshot: RepositorySnapshot,
    root: Path,
) -> tuple[tuple[RepositoryFileRecord, ...], str]:
    manifest_by_path = _manifest_by_path(snapshot.manifest)
    if snapshot.unmerged_paths:
        raise RepositoryViewError(
            "Unresolved merge conflicts are not supported in a worktree view",
            code="unmerged_scope",
        )
    links: dict[str, str] = {}
    regular_paths: set[str] = set()
    source_by_path: dict[str, Path] = {}
    prefix = snapshot.analysis_prefix
    if prefix:
        prefix = _safe_relative_path(prefix).as_posix()
    try:
        resolved_analysis_root = snapshot.analysis_root.resolve(strict=True)
    except OSError as error:
        raise RepositoryViewError(
            "Analysis root could not be resolved without symlinks",
            code="worktree_root_invalid",
        ) from error
    if resolved_analysis_root != snapshot.analysis_root:
        raise RepositoryViewError(
            "Analysis root traverses a symlink",
            code="worktree_parent_symlink",
        )
    for path, record in sorted(manifest_by_path.items()):
        git_path = _validated_manifest_git_path(record, path, prefix)
        if record.unmerged or record.submodule or record.stage:
            raise RepositoryViewError(
                "Unsupported tracked entry in worktree scope",
                code="unsupported_manifest_entry",
            )
        if record.deleted or record.staged_deleted:
            continue
        if record.mode == "160000" or record.submodule:
            raise RepositoryViewError(
                "Gitlink/submodule input is not supported",
                code="gitlink_unsupported",
            )
        if _filter_attributes(snapshot.git_root, git_path):
            raise RepositoryViewError(
                "External Git filter input is not supported",
                code="external_filter_unsupported",
            )
        relative_path = _safe_relative_path(path)
        source = snapshot.analysis_root.joinpath(*relative_path.parts)
        try:
            parent = snapshot.analysis_root
            for component in relative_path.parts[:-1]:
                parent = parent / component
                if parent.is_symlink():
                    raise RepositoryViewError(
                        "Tracked worktree path traverses a symlinked directory",
                        code="worktree_parent_symlink",
                    )
            if source.is_symlink():
                if not (record.symlink or record.mode == "120000"):
                    raise RepositoryViewError(
                        "Tracked worktree file type changed to symlink",
                        code="worktree_entry_type_changed",
                    )
                target = os.readlink(source)
                links[path] = _normalized_link_target(path, target)
            elif source.is_file():
                if record.symlink or record.mode == "120000":
                    raise RepositoryViewError(
                        "Tracked worktree symlink target changed to regular file",
                        code="worktree_entry_type_changed",
                    )
                if _is_lfs_pointer(source.read_bytes()):
                    raise RepositoryViewError(
                        "Git LFS pointer input is not source content",
                        code="lfs_pointer_unsupported",
                    )
                regular_paths.add(path)
                source_by_path[path] = source
            else:
                raise RepositoryViewError(
                    "Tracked worktree entry is absent or not a regular file",
                    code="worktree_entry_unsupported",
                )
        except OSError as error:
            raise RepositoryViewError(
                "Tracked worktree entry could not be inspected",
                code="worktree_read_failed",
            ) from error
    final_targets = {
        path: _resolve_symlink_chain(path, links, regular_paths)
        for path in links
    }
    materialized: list[RepositoryFileRecord] = []
    for path, record in sorted(manifest_by_path.items()):
        if path not in regular_paths and path not in links:
            continue
        destination = root.joinpath(*_safe_relative_path(path).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path in links:
            target = final_targets[path]
            relative_target = posixpath.relpath(target, posixpath.dirname(path) or ".")
            os.symlink(relative_target, destination)
        else:
            _copy_regular(source_by_path[path], destination)
            if record.mode == "100755":
                try:
                    destination.chmod(0o755)
                except OSError as error:
                    raise RepositoryViewError(
                        "Executable mode could not be preserved",
                        code="materialization_failed",
                    ) from error
        materialized.append(record)
    return tuple(materialized), "worktree-current-content"


def prepare_repository_view(
    snapshot: RepositorySnapshot | RepositoryView,
    *,
    deterministic_resume: bool = False,
) -> RepositoryView:
    """Materialize a run-owned view for the captured repository snapshot.

    The returned object owns its temporary root.  Callers should use it as a
    context manager (or call ``close``) so only this run's root is removed.
    ``tracked_worktree`` is deliberately rejected for deterministic resume.
    """

    if isinstance(snapshot, RepositoryView):
        if deterministic_resume and not snapshot.supports_deterministic_resume:
            raise DeterministicResumeError()
        return snapshot
    if deterministic_resume and snapshot.mode is RepositoryCaptureMode.TRACKED_WORKTREE:
        raise DeterministicResumeError()
    _validate_snapshot_scope(snapshot)
    if snapshot.mode is RepositoryCaptureMode.COMMITTED and not snapshot.commit_oid:
        raise RepositoryViewError(
            "Committed capture is missing a captured revision",
            code="missing_captured_revision",
        )
    try:
        root = Path(tempfile.mkdtemp(prefix="rii-v2-view-"))
    except OSError as error:
        raise RepositoryViewError(
            "Could not allocate a repository view root",
            code="materialization_failed",
        ) from error
    try:
        if snapshot.mode is RepositoryCaptureMode.COMMITTED:
            manifest, representation = _prepare_committed(snapshot, root)
        elif snapshot.mode is RepositoryCaptureMode.TRACKED_WORKTREE:
            manifest, representation = _prepare_tracked_worktree(snapshot, root)
        else:
            raise RepositoryViewError(
                "Unsupported repository capture mode",
                code="invalid_mode",
            )
        return RepositoryView(
            snapshot=snapshot,
            materialized_root=root,
            manifest=manifest,
            representation=representation,
        )
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def assert_deterministic_resume_supported(
    view_or_snapshot: RepositoryView | RepositorySnapshot,
) -> None:
    """Fail closed for the first-version tracked-worktree resume boundary."""

    mode = (
        view_or_snapshot.snapshot.mode
        if isinstance(view_or_snapshot, RepositoryView)
        else view_or_snapshot.mode
    )
    if mode is RepositoryCaptureMode.TRACKED_WORKTREE:
        raise DeterministicResumeError()


__all__ = [
    "DeterministicResumeError",
    "RepositoryView",
    "RepositoryViewError",
    "assert_deterministic_resume_supported",
    "prepare_repository_view",
]
