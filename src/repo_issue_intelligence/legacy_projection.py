"""Read original legacy reports without reinterpreting them as V2 executions."""

from __future__ import annotations

import json
import sqlite3
import stat
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .agent_database import read_migration_provenance
from .agent_store_migrations import DatabaseKind, MigrationError, inspect_agent_database
from .analysis_contract import project_analysis_v2_to_v1
from .models import (
    AgentRun,
    AgentRunStatus,
    InvestigationReport,
    LLMAnalysis,
    LLMAnalysisResult,
)
from .protocol_v2_models import AttemptV2, IssueSummaryV2, RunSummaryV2


class V2ProjectionError(MigrationError):
    """A V2 state cannot be represented by the legacy V1 Run shape."""


_PROJECTABLE_LLM_STATES = {
    "disabled",
    "pending",
    "skipped_no_evidence",
    "succeeded",
    "failed",
}
_PROJECTABLE_RUN_STATUSES = {"RUNNING", "AWAITING_REVIEW", "REVIEW_COMPLETED", "FAILED"}


def _validate_v2_state(summary: RunSummaryV2) -> AgentRunStatus:
    if summary.status not in _PROJECTABLE_RUN_STATUSES:
        raise V2ProjectionError(
            f"V2 Run review/status is not representable by V1: {summary.status}"
        )
    if any(issue.llm_state not in _PROJECTABLE_LLM_STATES for issue in summary.issues):
        raise V2ProjectionError("V2 Issue has an unknown or active LLM state")

    review_states = {issue.review_state for issue in summary.issues}
    if any(state not in {"pending", "approved", "rejected"} for state in review_states):
        raise V2ProjectionError("V2 Run has needs_information or another unsupported review state")
    if len(review_states) > 1:
        raise V2ProjectionError("V2 Run has mixed review state")
    if summary.status == "AWAITING_REVIEW" and review_states and review_states != {"pending"}:
        raise V2ProjectionError("V2 Run awaiting review has a non-pending review state")
    if summary.status == "REVIEW_COMPLETED":
        if not summary.issues:
            raise V2ProjectionError("V2 Run has an empty completed review")
        if review_states not in ({"approved"}, {"rejected"}):
            raise V2ProjectionError("V2 Run has incomplete or mixed review state")

    return {
        "RUNNING": AgentRunStatus.RUNNING,
        "AWAITING_REVIEW": AgentRunStatus.AWAITING_REVIEW,
        "REVIEW_COMPLETED": (
            AgentRunStatus.APPROVED if review_states == {"approved"} else AgentRunStatus.REJECTED
        ),
        "FAILED": AgentRunStatus.FAILED,
    }[summary.status]


def _selected_attempt(
    summary: RunSummaryV2,
    issue: IssueSummaryV2,
    selected_attempts: Mapping[str, AttemptV2] | None,
) -> AttemptV2:
    pointer = issue.selected_analysis_attempt_id
    if pointer is None:
        raise V2ProjectionError("V2 succeeded Issue has no selected analysis attempt")
    attempt = (
        selected_attempts.get(pointer)
        if selected_attempts is not None
        else issue.latest_attempt
    )
    if not isinstance(attempt, AttemptV2):
        raise V2ProjectionError("V2 selected analysis attempt is unavailable")
    if (
        attempt.attempt_id != pointer
        or attempt.run_id != summary.run_id
        or attempt.issue_number != issue.issue_number
        or attempt.state != "success"
    ):
        raise V2ProjectionError("V2 selected analysis attempt is not bound to the Issue")
    return attempt


def _project_selected_analysis(attempt: AttemptV2) -> LLMAnalysisResult:
    if attempt.analysis is None:
        raise V2ProjectionError("V2 selected successful attempt has no analysis")
    reported = attempt.reported
    local = attempt.local
    missing = []
    if reported is None:
        missing.extend(("provider", "model", "input_tokens", "output_tokens"))
    else:
        if not isinstance(reported.provider, str) or not reported.provider:
            missing.append("provider")
        if not isinstance(reported.model, str) or not reported.model:
            missing.append("model")
        if reported.input_tokens is None:
            missing.append("input_tokens")
        if reported.output_tokens is None:
            missing.append("output_tokens")
    if local is None or local.elapsed_ms is None:
        missing.append("elapsed_ms")
    if missing:
        raise V2ProjectionError(
            "V2 selected attempt is missing reported/local metadata: " + ", ".join(missing)
        )

    projected = project_analysis_v2_to_v1(attempt.analysis)
    payload = projected["payload"]
    input_ids = list(attempt.analysis.input_evidence_ids)
    primary_id = attempt.analysis.primary_evidence_id
    if primary_id not in input_ids:
        raise V2ProjectionError("V2 selected analysis has an unknown primary evidence ID")
    payload["reranked_evidence_ids"] = list(
        dict.fromkeys(
            (primary_id, *(evidence_id for evidence_id in input_ids if evidence_id != primary_id))
        )
    )
    try:
        analysis = LLMAnalysis.model_validate(payload)
    except ValueError as error:
        raise V2ProjectionError("V2 selected analysis cannot be represented by V1") from error
    return LLMAnalysisResult(
        provider=reported.provider,
        model=reported.model,
        reasoning_effort=reported.reasoning_effort,
        service_tier=reported.service_tier,
        request_id=reported.request_id,
        system_fingerprint=reported.system_fingerprint,
        input_tokens=reported.input_tokens,
        output_tokens=reported.output_tokens,
        elapsed_ms=local.elapsed_ms,
        analysis=analysis,
    )


def project_v2_run(
    summary: RunSummaryV2,
    selected_attempts: Mapping[str, AttemptV2] | None = None,
) -> AgentRun:
    """Project a bounded, read-only V2 summary into the legacy Run shape."""

    status = _validate_v2_state(summary)
    reports = []
    for issue in summary.issues:
        if issue.deterministic_report is None:
            raise V2ProjectionError("V2 Issue has no deterministic report")
        raw_report = dict(issue.deterministic_report)
        raw_report["llm_analysis"] = None
        raw_report["repository_root"] = summary.snapshot.analysis_root
        try:
            report = InvestigationReport.model_validate(raw_report)
        except ValueError as error:
            raise V2ProjectionError(
                "V2 deterministic report cannot be represented by V1"
            ) from error
        if issue.llm_state == "succeeded":
            report = report.model_copy(
                update={"llm_analysis": _project_selected_analysis(
                    _selected_attempt(summary, issue, selected_attempts)
                )}
            )
        reports.append(report)
    return AgentRun(
        run_id=summary.run_id,
        status=status,
        repository_root=summary.snapshot.analysis_root,
        top_k=summary.configuration.protocol.top_k or 1,
        llm_enabled=summary.configuration.llm_enabled,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
        ranked_issues=[result.to_result() for result in summary.inputs.ranked_results],
        selected_issue_numbers=list(summary.inputs.selection.selected_issue_numbers),
        investigations=reports,
    )


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
