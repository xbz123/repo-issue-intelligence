"""Explicit-target Protocol v2 persistence; schema ownership stays in migrations."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

try:
    import fcntl
except ImportError:
    fcntl = None

from .agent_store_migrations import DatabaseKind, inspect_agent_database
from .analysis_observations import metadata_diagnostics
from .models import EvidenceSnippet, InvestigationReport
from .protocol_v2_models import (
    AnalysisV2,
    AttemptError,
    AttemptRequest,
    AttemptTerminalFields,
    AttemptV2,
    EvidenceCollectionContext,
    EvidenceItemV2,
    EvidenceSetV2,
    FrozenDict,
    FrozenEvidenceSnippet,
    FrozenSelection,
    IssueExecutionV2,
    IssueSummaryV2,
    LocalObservation,
    ReportedObservation,
    RepositorySnapshot,
    RunConfiguration,
    RunInputs,
    RunSummaryV2,
    RunV2,
    TracePayloadV2,
    TraceV2,
    request_budget_origin,
)


class StoreError(ValueError):
    """A target, input, or stored value violates the Store contract."""


class StoreConflict(StoreError):
    """The requested write could not claim its immutable record or transition."""


_SQLITE_BUSY_TIMEOUT_SECONDS = 5.0


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _decode_run_configuration(serialized: str) -> RunConfiguration:
    configuration = json.loads(serialized)
    # Only stored data uses legacy null-default recovery; in-memory copies must
    # pass their field-presence checks before reaching this boundary.
    for name in ("output_tokens", "timeout_seconds"):
        if (
            request_budget_origin(configuration["parameter_origins"], name) == "omitted"
            and configuration["budgets"].get(name) is None
        ):
            configuration["budgets"].pop(name, None)
    for key in ("request_parameters", "parameter_origins"):
        configuration[key] = FrozenDict(configuration[key])
    configuration["budgets"]["retry_policy"] = FrozenDict(configuration["budgets"]["retry_policy"])
    return RunConfiguration.model_validate(configuration)


class AgentStoreV2:
    """Open only a pre-created, private, structurally known V2 database.

    Each operation owns and closes one connection. No implicit migration,
    directory creation, permission repair, or historical deletion occurs here.
    """

    def __init__(self, path: Path | str):
        if fcntl is None:
            raise StoreError("Protocol v2 storage requires POSIX file locking")
        self.path = Path(path).absolute()
        self._identity: tuple[int, int] | None = None
        self._schema_version: int | None = None
        with self._connect() as connection:
            if inspect_agent_database(connection).kind != DatabaseKind.KNOWN_V2:
                raise StoreError("Store requires an explicitly initialized V2 database")
            self._schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]

    def _check_path(self) -> tuple[int, int]:
        for ancestor in self.path.parents:
            if ancestor.is_symlink() and ancestor not in (Path("/tmp"), Path("/var")):
                raise StoreError("Store refuses symlinked database directories")
        for path, directory in ((self.path.parent, True), (self.path, False)):
            try:
                info = path.lstat()
            except OSError:
                raise StoreError(
                    "Store requires an existing private database and directory"
                ) from None
            expected_type = stat.S_ISDIR if directory else stat.S_ISREG
            if (
                not expected_type(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or (not directory and info.st_nlink != 1)
            ):
                raise StoreError("Store refuses unsafe database paths or permissions")
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = self.path.with_name(self.path.name + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                info = sidecar.lstat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o077
                    or info.st_nlink != 1
                ):
                    raise StoreError("Store refuses unsafe SQLite sidecars")
        info = self.path.lstat()
        identity = info.st_dev, info.st_ino
        if self._identity is not None and identity != self._identity:
            raise StoreError("Store target has been replaced")
        return identity

    @contextmanager
    def writer_lock(self):
        """Refuse a second foreground V2 executor without locking SQLite readers."""
        identity = self._check_path()
        # A flock on the database itself also blocks SQLite writes on macOS.
        lock_path = self.path.with_name(self.path.name + ".writer.lock")
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_nlink != 1
            ):
                raise StoreError("Store refuses unsafe writer lock files")
            try:
                # ponytail: one foreground run per database; use per-run locks if parallelized.
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StoreConflict("V2 database already has a foreground writer") from None
            current = lock_path.lstat()
            if self._check_path() != identity or (current.st_dev, current.st_ino) != (
                info.st_dev,
                info.st_ino,
            ):
                raise StoreError("Store target changed during writer lock acquisition")
            yield
        finally:
            # Keep the sidecar: unlinking it could allow two separately locked inodes.
            os.close(descriptor)

    @contextmanager
    def _connect(self):
        identity = self._check_path()
        with closing(
            sqlite3.connect(
                self.path.as_uri() + "?mode=rw",
                uri=True,
                timeout=_SQLITE_BUSY_TIMEOUT_SECONDS,
            )
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                if self._check_path() != identity:
                    raise StoreError("Store target changed during open")
                self._identity = identity
                if self._schema_version is not None and (
                    connection.execute("PRAGMA user_version").fetchone()[0] != 2
                    or connection.execute("PRAGMA schema_version").fetchone()[0]
                    != self._schema_version
                ):
                    raise StoreError("Store target schema changed; explicit reopen required")
                with connection:
                    yield connection
            except sqlite3.IntegrityError:
                raise StoreConflict(
                    "Store write conflicts with an immutable record or binding"
                ) from None
            except sqlite3.OperationalError as error:
                if getattr(error, "sqlite_errorcode", 0) & 0xFF in {
                    sqlite3.SQLITE_BUSY,
                    sqlite3.SQLITE_LOCKED,
                }:
                    raise StoreConflict("SQLite contention exceeded the bounded wait") from None
                raise StoreError("SQLite operation failed; transaction rolled back") from None
            except sqlite3.DatabaseError:
                raise StoreError("SQLite operation failed; transaction rolled back") from None

    def create_run(
        self,
        snapshot: RepositorySnapshot,
        configuration: RunConfiguration,
        inputs: RunInputs,
        selection: FrozenSelection | None = None,
        *,
        run_id: str | None = None,
        parent_run_id: str | None = None,
    ) -> RunV2:
        # model_copy/model_construct can bypass Pydantic validation at the caller.
        try:
            configuration.validate_request_budget_consistency()
            configuration_json = configuration.model_dump_json()
            # Apply the exact reader to the exact payload before any INSERT, so
            # invalid nested models cannot leave a committed, unreadable run.
            _decode_run_configuration(configuration_json)
        except ValueError:
            raise StoreError("Conflicting or invalid run configuration") from None
        selection = selection if selection is not None else inputs.selection
        if selection != inputs.selection:
            raise StoreError("selection must match frozen run inputs")
        run_id = run_id or str(uuid4())
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO agent_v2_runs VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?)",
                (
                    run_id,
                    parent_run_id,
                    snapshot.model_dump_json(),
                    configuration_json,
                    inputs.model_dump_json(),
                    selection.model_dump_json(),
                    now,
                    now,
                ),
            )
            connection.executemany(
                "INSERT INTO agent_v2_issues (run_id, issue_number, deterministic_state) "
                "VALUES (?, ?, 'pending')",
                [(run_id, number) for number in selection.selected_issue_numbers],
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunV2 | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_v2_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._run(row)

    @staticmethod
    def _run(row: sqlite3.Row | None) -> RunV2 | None:
        if row is None:
            return None
        return RunV2(
            run_id=row["run_id"],
            parent_run_id=row["parent_run_id"],
            snapshot=RepositorySnapshot.model_validate_json(row["snapshot_json"]),
            configuration=_decode_run_configuration(row["configuration_json"]),
            inputs=RunInputs.model_validate_json(row["inputs_json"]),
            selection=FrozenSelection.model_validate_json(row["selection_json"]),
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_issue(self, run_id: str, issue_number: int) -> IssueExecutionV2 | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        with self._connect() as connection:
            connection.execute("BEGIN")
            return self._issue(connection, run, issue_number)

    def _issue(
        self,
        connection: sqlite3.Connection,
        run: RunV2,
        issue_number: int,
    ) -> IssueExecutionV2 | None:
        run_id = run.run_id
        row = connection.execute(
            "SELECT * FROM agent_v2_issues WHERE run_id = ? AND issue_number = ?",
            (run_id, issue_number),
        ).fetchone()
        attempts = connection.execute(
            "SELECT * FROM agent_v2_llm_attempts WHERE run_id=? AND issue_number=? "
            "ORDER BY ordinal",
            (run_id, issue_number),
        ).fetchall()
        item_count = (
            connection.execute(
                "SELECT count(*) FROM agent_v2_evidence_items WHERE evidence_set_id=?",
                (row["evidence_set_id"],),
            ).fetchone()[0]
            if row is not None and row["evidence_set_id"]
            else None
        )
        if row is None:
            return None
        llm_state = "pending" if run.configuration.llm_enabled else "disabled"
        if run.configuration.llm_enabled and item_count == 0:
            llm_state = "skipped_no_evidence"
        if attempts:
            llm_state = {
                "in_progress": "in_progress",
                "success": "succeeded",
                "failure": "failed",
                "unknown": "interrupted_unknown",
            }[attempts[-1]["state"]]
        selected = next(
            (
                attempt
                for attempt in attempts
                if attempt["attempt_id"] == row["selected_analysis_attempt_id"]
            ),
            None,
        )
        return IssueExecutionV2(
            run_id=run_id,
            issue_number=issue_number,
            deterministic_state=row["deterministic_state"],
            deterministic_report=(
                FrozenDict(json.loads(row["deterministic_report_json"]))
                if row["deterministic_report_json"]
                else None
            ),
            evidence_set_id=row["evidence_set_id"],
            selected_analysis_attempt_id=row["selected_analysis_attempt_id"],
            review_version=row["review_version"],
            llm_state=llm_state,
            attempt_count=len(attempts),
            analysis=FrozenDict(json.loads(selected["analysis_json"])) if selected else None,
        )

    def list_issues(self, run_id: str) -> tuple[IssueExecutionV2, ...]:
        run = self.get_run(run_id)
        return (
            ()
            if run is None
            else tuple(
                self.get_issue(run_id, number) for number in run.selection.selected_issue_numbers
            )
        )

    def get_run_summary(self, run_id: str) -> RunSummaryV2 | None:
        """Derive the CLI view from committed stages, attempts and review records."""
        summaries = []
        with self._connect() as connection:
            # Short read snapshot: concurrent readers cannot mix an earlier Issue
            # state with an attempt finalized midway through this projection.
            connection.execute("BEGIN")
            run = self._run(
                connection.execute(
                    "SELECT * FROM agent_v2_runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
            )
            if run is None:
                return None
            for number in run.selection.selected_issue_numbers:
                issue = self._issue(connection, run, number)
                if issue is None:
                    raise StoreError("Stored run is missing a selected Issue")
                latest_row = connection.execute(
                    "SELECT * FROM agent_v2_llm_attempts WHERE run_id=? AND issue_number=? "
                    "ORDER BY ordinal DESC LIMIT 1",
                    (run_id, number),
                ).fetchone()
                latest = self._attempt(latest_row) if latest_row is not None else None
                rows = connection.execute(
                    "SELECT review_id,evidence_set_id,selected_attempt_id,principal_id,"
                    "expected_review_version,decision,created_at FROM agent_v2_reviews "
                    "WHERE run_id=? AND issue_number=? ORDER BY expected_review_version",
                    (run_id, issue.issue_number),
                ).fetchall()
                reviews = tuple(FrozenDict(dict(row)) for row in rows)
                reviewed = bool(
                    reviews
                    and reviews[-1]["evidence_set_id"] == issue.evidence_set_id
                    and reviews[-1]["selected_attempt_id"] == issue.selected_analysis_attempt_id
                    and reviews[-1]["expected_review_version"] + 1 == issue.review_version
                )
                summaries.append(
                    IssueSummaryV2(
                        **{name: getattr(issue, name) for name in IssueExecutionV2.model_fields},
                        latest_attempt=latest,
                        diagnostics=(
                            metadata_diagnostics(
                                latest.request.model_dump(exclude_unset=True),
                                latest.reported.model_dump(),
                            )
                            if latest is not None and latest.reported is not None
                            else ()
                        ),
                        reviews=reviews,
                        review_state=reviews[-1]["decision"] if reviewed else "pending",
                    )
                )
        reviewed_count = sum(issue.review_state != "pending" for issue in summaries)
        fields = {name: getattr(run, name) for name in RunV2.model_fields}
        if run.status in {"AWAITING_REVIEW", "PARTIALLY_REVIEWED", "REVIEW_COMPLETED"}:
            fields["status"] = (
                "REVIEW_COMPLETED"
                if summaries and reviewed_count == len(summaries)
                else "PARTIALLY_REVIEWED"
                if reviewed_count
                else "AWAITING_REVIEW"
            )
        return RunSummaryV2(
            **fields,
            issues=tuple(summaries),
            llm_outcomes=FrozenDict(Counter(issue.llm_state for issue in summaries)),
            reviewed_issues=reviewed_count,
        )

    def set_run_status(self, run_id: str, status: str, *, expected_status: str) -> RunV2:
        """Persist an explicit control decision, not a second attempt outcome."""
        states = {
            "RUNNING",
            "INTERRUPTED",
            "AWAITING_REVIEW",
            "PARTIALLY_REVIEWED",
            "REVIEW_COMPLETED",
            "FAILED",
        }
        if status not in states or expected_status not in states:
            raise StoreError("unsupported run status")
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE agent_v2_runs SET status=?,updated_at=? WHERE run_id=? AND status=?",
                (status, _now(), run_id, expected_status),
            ).rowcount
            if changed != 1:
                raise StoreConflict("run control status changed or run is unavailable")
        return self.get_run(run_id)

    def set_deterministic_state(
        self,
        run_id: str,
        issue_number: int,
        state: str,
    ) -> IssueExecutionV2:
        """Record only started/failed; success always requires the frozen report."""
        if state not in {"running", "failed"}:
            raise StoreError("deterministic success must be saved with its report")
        allowed = ("pending",) if state == "running" else ("pending", "running")
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE agent_v2_issues SET deterministic_state=? "
                "WHERE run_id=? AND issue_number=? "
                f"AND deterministic_state IN ({','.join('?' for _ in allowed)}) "
                "AND deterministic_report_json IS NULL",
                (state, run_id, issue_number, *allowed),
            ).rowcount
            if changed != 1:
                raise StoreConflict("deterministic stage is unavailable or already terminal")
        return self.get_issue(run_id, issue_number)

    def save_deterministic_result(
        self,
        run_id: str,
        issue_number: int,
        report: InvestigationReport,
    ) -> IssueExecutionV2:
        report = InvestigationReport.model_validate(report.model_dump())
        run = self.get_run(run_id)
        if run is None or issue_number not in run.selection.selected_issue_numbers:
            raise StoreError("report must belong to a selected Issue")
        frozen_issue = next(issue for issue in run.inputs.issues if issue.number == issue_number)
        if (
            report.issue != frozen_issue.to_issue()
            or report.repository_root != run.snapshot.analysis_root
            or report.llm_analysis is not None
        ):
            raise StoreError("deterministic report must match frozen input without LLM analysis")
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE agent_v2_issues SET deterministic_state='succeeded', "
                "deterministic_report_json=? WHERE run_id=? AND issue_number=? "
                "AND deterministic_state IN ('pending','running') "
                "AND deterministic_report_json IS NULL",
                (report.model_dump_json(exclude={"llm_analysis"}), run_id, issue_number),
            ).rowcount
            if changed != 1:
                raise StoreConflict("deterministic report is already committed")
        return self.get_issue(run_id, issue_number)

    def seal_evidence_set(
        self,
        run_id: str,
        issue_number: int,
        items: Sequence[EvidenceItemV2],
        collection_context: EvidenceCollectionContext,
        *,
        evidence_set_id: str | None = None,
    ) -> EvidenceSetV2:
        items = tuple(EvidenceItemV2.model_validate(item.model_dump()) for item in items)
        collection_context = EvidenceCollectionContext.model_validate(
            collection_context.model_dump()
        )
        run = self.get_run(run_id)
        if run is None or (
            collection_context.snapshot_commit != run.snapshot.commit_oid
            or collection_context.analysis_prefix != run.snapshot.analysis_prefix
        ):
            raise StoreError("evidence collection context must match frozen snapshot")
        if any(item.collector_protocol != collection_context.collector_protocol for item in items):
            raise StoreError("evidence collector protocol must match collection context")
        if (
            collection_context.budget_chars is not None
            and sum(item.char_count for item in items) > collection_context.budget_chars
        ):
            raise StoreError("evidence exceeds recorded character budget")
        if collection_context.budget_lines is not None and any(
            item.actual_range[1] - item.actual_range[0] + 1 > collection_context.budget_lines
            for item in items
        ):
            raise StoreError("evidence exceeds recorded per-item line budget")
        evidence_set_id = evidence_set_id or str(uuid4())
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO agent_v2_evidence_sets "
                "(evidence_set_id,run_id,issue_number,collection_context_json) VALUES (?,?,?,?)",
                (evidence_set_id, run_id, issue_number, collection_context.model_dump_json()),
            )
            connection.executemany(
                "INSERT INTO agent_v2_evidence_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        evidence_set_id,
                        item.evidence_id,
                        item.ordinal,
                        item.candidate_rank,
                        item.selection_kind,
                        item.file,
                        item.symbol,
                        json.dumps(item.requested_range),
                        json.dumps(item.actual_range),
                        item.truncation_reason,
                        item.char_count,
                        item.content,
                        item.collector_protocol,
                    )
                    for item in items
                ],
            )
            connection.execute(
                "UPDATE agent_v2_evidence_sets SET sealed=1,sealed_at=? WHERE evidence_set_id=?",
                (now, evidence_set_id),
            )
            changed = connection.execute(
                "UPDATE agent_v2_issues SET evidence_set_id=? WHERE run_id=? AND issue_number=? "
                "AND evidence_set_id IS NULL AND deterministic_state='succeeded'",
                (evidence_set_id, run_id, issue_number),
            ).rowcount
            if changed != 1:
                raise StoreConflict("evidence requires an unsealed Issue with a committed report")
        return self.read_evidence(run_id, issue_number)

    def read_evidence(self, run_id: str, issue_number: int) -> EvidenceSetV2 | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT evidence.* FROM agent_v2_evidence_sets AS evidence "
                "JOIN agent_v2_issues AS issue ON issue.evidence_set_id=evidence.evidence_set_id "
                "WHERE issue.run_id=? AND issue.issue_number=? AND evidence.sealed=1",
                (run_id, issue_number),
            ).fetchone()
            if row is None:
                return None
            items = connection.execute(
                "SELECT * FROM agent_v2_evidence_items WHERE evidence_set_id=? ORDER BY ordinal",
                (row["evidence_set_id"],),
            ).fetchall()
        values = []
        for item in items:
            fields = dict(item)
            del fields["evidence_set_id"]
            for key in ("requested_range", "actual_range"):
                fields[key] = json.loads(fields[key])
            values.append(EvidenceItemV2.model_validate(fields))
        return EvidenceSetV2(
            evidence_set_id=row["evidence_set_id"],
            run_id=run_id,
            issue_number=issue_number,
            collection_context=EvidenceCollectionContext.model_validate_json(
                row["collection_context_json"]
            ),
            sealed_at=row["sealed_at"],
            items=tuple(values),
        )

    def evidence_lookup(
        self,
        run_id: str,
        issue_number: int,
    ) -> tuple[tuple[str, ...], Mapping[str, EvidenceSnippet]]:
        evidence = self.read_evidence(run_id, issue_number)
        if evidence is None:
            raise StoreError("sealed evidence unavailable")
        lookup = {
            item.evidence_id: FrozenEvidenceSnippet(
                id=item.evidence_id,
                file=item.file,
                symbol=item.symbol,
                lines=f"{item.actual_range[0]}-{item.actual_range[1]}",
                content=item.content,
            )
            for item in evidence.items
        }
        return tuple(lookup), MappingProxyType(lookup)

    def start_attempt(
        self,
        run_id: str,
        issue_number: int,
        evidence_set_id: str,
        requested_configuration: AttemptRequest,
        *,
        attempt_id: str | None = None,
    ) -> AttemptV2:
        """Claim a sealed Issue in a running run; unknown history is never replayed here."""
        request = AttemptRequest.model_validate(
            requested_configuration.model_dump(exclude_unset=True)
        )
        attempt_id = attempt_id or str(uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = self._run(
                connection.execute(
                    "SELECT * FROM agent_v2_runs WHERE run_id=?", (run_id,)
                ).fetchone()
            )
            if run is None or not run.configuration.llm_enabled:
                raise StoreError("attempt requires an LLM-enabled run")
            if run.status != "RUNNING":
                raise StoreConflict("attempt requires a running run")
            configuration = run.configuration
            if (
                request.backend != configuration.client.backend
                or request.provider != configuration.client.provider
                or request.model != configuration.requested_model
            ):
                raise StoreError("changing the requested model or client requires a new run")
            model_present = (
                configuration.requested_model is not None
                or configuration.parameter_origins.get("requested_model", "omitted") != "omitted"
            )
            if model_present != ("model" in request.model_fields_set):
                raise StoreError("changing requested model omission requires a new run")
            for name in (
                "temperature",
                "seed",
                "reasoning_effort",
                "service_tier",
                "max_output_tokens",
                "timeout_seconds",
                "response_format_json",
            ):
                present = name in configuration.request_parameters
                value = configuration.request_parameters.get(name)
                if name == "max_output_tokens" and not present:
                    present = "output_tokens" in configuration.request_parameters
                    value = configuration.request_parameters.get("output_tokens")
                if name in {"max_output_tokens", "timeout_seconds"}:
                    budget_name = "output_tokens" if name == "max_output_tokens" else name
                    budget_value = getattr(configuration.budgets, budget_name)
                    if configuration.has_request_budget(budget_name):
                        value, present = budget_value, True
                if present != (name in request.model_fields_set) or (
                    present and getattr(request, name) != value
                ):
                    raise StoreError("changing requested parameters or omission requires a new run")
            issue = connection.execute(
                "SELECT * FROM agent_v2_issues WHERE run_id=? AND issue_number=?",
                (run_id, issue_number),
            ).fetchone()
            count = connection.execute(
                "SELECT count(*) FROM agent_v2_evidence_items WHERE evidence_set_id=?",
                (evidence_set_id,),
            ).fetchone()[0]
            if (
                issue is None
                or issue["deterministic_state"] != "succeeded"
                or issue["deterministic_report_json"] is None
                or issue["evidence_set_id"] != evidence_set_id
                or not count
                or issue["selected_analysis_attempt_id"] is not None
            ):
                raise StoreConflict(
                    "attempt requires current nonempty evidence and no selected success"
                )
            if (
                connection.execute(
                    "SELECT 1 FROM agent_v2_llm_attempts "
                    "WHERE run_id=? AND issue_number=? AND state='unknown' LIMIT 1",
                    (run_id, issue_number),
                ).fetchone()
                is not None
            ):
                raise StoreConflict("unknown attempt requires explicit recovery; new start refused")
            ordinal = connection.execute(
                "SELECT coalesce(max(ordinal), -1) + 1 FROM agent_v2_llm_attempts "
                "WHERE run_id=? AND issue_number=?",
                (run_id, issue_number),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO agent_v2_llm_attempts "
                "(attempt_id,run_id,issue_number,evidence_set_id,ordinal,"
                "request_json,started_at,state) "
                "VALUES (?,?,?,?,?,?,?,'in_progress')",
                (
                    attempt_id,
                    run_id,
                    issue_number,
                    evidence_set_id,
                    ordinal,
                    request.model_dump_json(exclude_unset=True),
                    _now(),
                ),
            )
        return self.get_attempt(attempt_id)

    @staticmethod
    def _attempt(row: sqlite3.Row) -> AttemptV2:
        fields = {
            name: row[name]
            for name in (
                "attempt_id",
                "run_id",
                "issue_number",
                "evidence_set_id",
                "ordinal",
                "started_at",
                "state",
                "finished_at",
            )
        }
        for name, model in (
            ("request", AttemptRequest),
            ("analysis", AnalysisV2),
            ("error", AttemptError),
            ("reported", ReportedObservation),
            ("local", LocalObservation),
        ):
            value = row[f"{name}_json"]
            fields[name] = model.model_validate_json(value) if value is not None else None
        return AttemptV2(**fields)

    def get_attempt(self, attempt_id: str) -> AttemptV2 | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_v2_llm_attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
        return self._attempt(row) if row is not None else None

    def list_attempts(self, run_id: str, issue_number: int) -> tuple[AttemptV2, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_v2_llm_attempts WHERE run_id=? AND issue_number=? "
                "ORDER BY ordinal",
                (run_id, issue_number),
            ).fetchall()
        return tuple(self._attempt(row) for row in rows)

    def finalize_attempt(
        self,
        attempt_id: str,
        terminal_fields: AttemptTerminalFields,
    ) -> AttemptV2:
        terminal = AttemptTerminalFields.model_validate(terminal_fields.model_dump())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM agent_v2_llm_attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if row is None or row["state"] != "in_progress":
                raise StoreConflict("attempt is absent or already finalized")
            if terminal.analysis is not None:
                evidence = connection.execute(
                    "SELECT evidence_id,file,symbol FROM agent_v2_evidence_items "
                    "WHERE evidence_set_id=? ORDER BY ordinal",
                    (row["evidence_set_id"],),
                ).fetchall()
                if terminal.analysis.input_evidence_ids != tuple(
                    item["evidence_id"] for item in evidence
                ):
                    raise StoreError("analysis inputs must exactly match the sealed evidence order")
                primary = next(
                    item
                    for item in evidence
                    if item["evidence_id"] == terminal.analysis.primary_evidence_id
                )
                component = (
                    f"{primary['file']}::{primary['symbol']}"
                    if primary["symbol"]
                    else primary["file"]
                )
                if terminal.analysis.affected_component != component:
                    raise StoreError("analysis component must match its primary evidence")
            changed = connection.execute(
                "UPDATE agent_v2_llm_attempts SET state=?,finished_at=?,analysis_json=?,"
                "error_json=?,reported_json=?,local_json=? "
                "WHERE attempt_id=? AND state='in_progress'",
                (
                    terminal.state,
                    _now(),
                    *[
                        value.model_dump_json() if value is not None else None
                        for value in (
                            terminal.analysis,
                            terminal.error,
                            terminal.reported,
                            terminal.local,
                        )
                    ],
                    attempt_id,
                ),
            ).rowcount
            if changed != 1:
                raise StoreConflict("attempt finalization lost its in-progress claim")
            if terminal.state == "success":
                changed = connection.execute(
                    "UPDATE agent_v2_issues SET selected_analysis_attempt_id=? "
                    "WHERE run_id=? AND issue_number=? AND evidence_set_id=? "
                    "AND selected_analysis_attempt_id IS NULL",
                    (attempt_id, row["run_id"], row["issue_number"], row["evidence_set_id"]),
                ).rowcount
                if changed != 1:
                    raise StoreConflict(
                        "selected analysis binding changed; finalization rolled back"
                    )
        return self.get_attempt(attempt_id)

    def append_trace(self, run_id: str, payload: TracePayloadV2) -> TraceV2:
        payload = TracePayloadV2.model_validate(
            payload.model_dump() if isinstance(payload, TracePayloadV2) else payload
        )
        encoded = payload.model_dump_json()
        if len(encoded.encode()) > 1024:
            raise StoreError("trace exceeds small metadata budget")
        trace_id, now = str(uuid4()), _now()
        with self._connect() as connection:
            if (
                payload.issue_number is not None
                and connection.execute(
                    "SELECT 1 FROM agent_v2_issues WHERE run_id=? AND issue_number=?",
                    (run_id, payload.issue_number),
                ).fetchone()
                is None
            ):
                raise StoreError("trace Issue reference is unavailable")
            for reference, table, column in (
                (payload.evidence_set_id, "agent_v2_evidence_sets", "evidence_set_id"),
                (payload.attempt_id, "agent_v2_llm_attempts", "attempt_id"),
            ):
                if reference is not None and (
                    payload.issue_number is None
                    or connection.execute(
                        f"SELECT 1 FROM {table} WHERE {column}=? AND run_id=? AND issue_number=?",
                        (reference, run_id, payload.issue_number),
                    ).fetchone()
                    is None
                ):
                    raise StoreError("trace reference must belong to its run and Issue")
            connection.execute(
                "INSERT INTO agent_v2_traces VALUES (?,?,?,?)",
                (trace_id, run_id, encoded, now),
            )
        return TraceV2(trace_id=trace_id, run_id=run_id, payload=payload, created_at=now)

    def list_traces(self, run_id: str) -> tuple[TraceV2, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_v2_traces WHERE run_id=? ORDER BY rowid",
                (run_id,),
            ).fetchall()
        return tuple(
            TraceV2(
                trace_id=row["trace_id"],
                run_id=row["run_id"],
                created_at=row["created_at"],
                payload=TracePayloadV2.model_validate_json(row["payload_json"]),
            )
            for row in rows
        )
