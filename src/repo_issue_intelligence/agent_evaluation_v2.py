"""Opt-in evaluation of committed Protocol v2 records, separate from V1 artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from statistics import fmean
from time import sleep
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .agent_database import create_v2_database
from .agent_store_v2 import AgentStoreV2
from .benchmark import BenchmarkManifest, BenchmarkTier, prepare_repository
from .evidence import DEFAULT_MAX_LINES_PER_SNIPPET, DEFAULT_MAX_TOTAL_CHARS
from .issue_execution import IssueAnalyzerV2, run_agent_v2
from .protocol_v2_models import AnalysisV2, AttemptV2, IssueExecutionV2, RepositoryCaptureMode


class AgentAnalysisCaseResultV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Literal["v2"] = "v2"
    case_id: str
    tier: BenchmarkTier
    repository: str
    issue_number: int
    pre_fix_sha: str
    run_id: str
    database_path: str
    commit_oid: str | None
    agent_status: str
    deterministic_state: str
    llm_state: str
    execution_succeeded: bool
    analysis_succeeded: bool
    error_category: Literal["execution_fatal"] | None = None
    skipped_no_evidence: bool
    persistence_verified: bool
    evidence_set_id: str | None
    selected_analysis_attempt_id: str | None
    attempts: tuple[AttemptV2, ...]
    expected_files: list[str]
    evidence_files: list[str]
    expected_files_in_evidence: list[str]
    hypothesis_cited_files: list[str]
    expected_files_cited_by_hypothesis: list[str]
    expected_file_evidence_recall: float | None
    hypothesis_expected_file_recall: float | None
    analysis: AnalysisV2 | None


class AgentAnalysisAggregateV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Literal["v2"] = "v2"
    execution_cases: int
    execution_successes: int
    execution_failures: int
    execution_success_rate: float | None
    provider_cases: int
    provider_attempts: int
    provider_successes: int
    provider_success_rate: float | None
    provider_attempt_success_rate: float | None
    no_evidence_eligible_cases: int
    no_evidence_cases: int
    no_evidence_rate: float | None
    grounding_cases: int
    grounding_hits: int
    grounding_hit_rate: float | None
    mean_hypothesis_expected_file_recall: float | None
    evidence_quality_cases: int
    mean_expected_file_evidence_recall: float | None
    persistence_verified: int
    input_tokens: int | None
    output_tokens: int | None
    input_token_observations: int
    output_token_observations: int
    error_categories: dict[str, int] = Field(default_factory=dict)


class AgentAnalysisRunV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Literal["v2"] = "v2"
    manifest_name: str
    manifest_version: int
    status: Literal["COMPLETED", "FAILED"]
    completed: bool
    planned_cases: int
    attempted_cases: int
    max_evidence_chars: int | None
    max_lines_per_evidence: int | None
    max_llm_attempts: int
    llm_delay_seconds: float
    created_at: datetime
    results: list[AgentAnalysisCaseResultV2]
    overall: AgentAnalysisAggregateV2
    by_tier: dict[str, AgentAnalysisAggregateV2]


def aggregate_agent_analysis_v2(
    results: Sequence[AgentAnalysisCaseResultV2],
) -> AgentAnalysisAggregateV2:
    if any(not isinstance(result, AgentAnalysisCaseResultV2) for result in results):
        raise ValueError("V2 aggregates require exclusively V2 case results")
    attempts = [attempt for result in results for attempt in result.attempts]
    execution_successes = sum(result.execution_succeeded for result in results)
    provider_cases = sum(bool(result.attempts) for result in results)
    provider_successes = sum(result.analysis_succeeded for result in results)
    no_evidence = sum(result.skipped_no_evidence for result in results)
    grounding = [result for result in results if result.hypothesis_expected_file_recall is not None]
    evidence = [result for result in results if result.expected_file_evidence_recall is not None]
    grounding_hits = sum(bool(result.expected_files_cited_by_hypothesis) for result in grounding)
    input_tokens = [
        a.reported.input_tokens
        for a in attempts
        if a.reported is not None and a.reported.input_tokens is not None
    ]
    output_tokens = [
        a.reported.output_tokens
        for a in attempts
        if a.reported is not None and a.reported.output_tokens is not None
    ]
    categories = [a.error.category for a in attempts if a.error is not None]
    categories.extend(result.error_category for result in results if result.error_category)
    evidence_eligible = sum(result.evidence_set_id is not None for result in results)
    return AgentAnalysisAggregateV2(
        execution_cases=len(results),
        execution_successes=execution_successes,
        execution_failures=len(results) - execution_successes,
        execution_success_rate=execution_successes / len(results) if results else None,
        provider_cases=provider_cases,
        provider_attempts=len(attempts),
        provider_successes=provider_successes,
        provider_success_rate=provider_successes / provider_cases if provider_cases else None,
        provider_attempt_success_rate=(
            sum(a.state == "success" for a in attempts) / len(attempts) if attempts else None
        ),
        no_evidence_eligible_cases=evidence_eligible,
        no_evidence_cases=no_evidence,
        no_evidence_rate=no_evidence / evidence_eligible if evidence_eligible else None,
        grounding_cases=len(grounding),
        grounding_hits=grounding_hits,
        grounding_hit_rate=grounding_hits / len(grounding) if grounding else None,
        mean_hypothesis_expected_file_recall=(
            fmean(r.hypothesis_expected_file_recall for r in grounding) if grounding else None
        ),
        evidence_quality_cases=len(evidence),
        mean_expected_file_evidence_recall=(
            fmean(r.expected_file_evidence_recall for r in evidence) if evidence else None
        ),
        persistence_verified=sum(result.persistence_verified for result in results),
        # Partial observed usage is not a known total; counts expose missing coverage.
        input_tokens=sum(input_tokens) if attempts and len(input_tokens) == len(attempts) else None,
        output_tokens=sum(output_tokens)
        if attempts and len(output_tokens) == len(attempts)
        else None,
        input_token_observations=len(input_tokens),
        output_token_observations=len(output_tokens),
        error_categories={
            category: categories.count(category) for category in sorted(set(categories))
        },
    )


def run_agent_analysis_evaluation_v2(
    manifest: BenchmarkManifest,
    workspace: Path,
    analyzer: IssueAnalyzerV2,
    case_ids: set[str] | None = None,
    max_evidence_chars: int | None = DEFAULT_MAX_TOTAL_CHARS,
    max_lines_per_evidence: int | None = DEFAULT_MAX_LINES_PER_SNIPPET,
    max_llm_attempts: int = 2,
    llm_delay_seconds: float = 0,
    *,
    allow_external_llm: bool = False,
    parameter_origins: Mapping[str, str] | None = None,
) -> AgentAnalysisRunV2:
    if not allow_external_llm:
        raise ValueError("V2 evaluation requires explicit allow_external_llm permission")
    if type(llm_delay_seconds) not in (int, float) or not (
        0 <= llm_delay_seconds and isfinite(llm_delay_seconds)
    ):
        raise ValueError("llm_delay_seconds must be finite and non-negative")
    unknown = (case_ids or set()) - {case.id for case in manifest.cases}
    if unknown:
        raise ValueError("Unknown benchmark case IDs: " + ", ".join(sorted(unknown)))
    selected = [case for case in manifest.cases if case_ids is None or case.id in case_ids]
    if not selected:
        raise ValueError("No benchmark cases matched the requested case IDs")
    results = []
    provider_case_seen = False

    def before_provider_case() -> None:
        nonlocal provider_case_seen
        if provider_case_seen and llm_delay_seconds > 0:
            minutes, seconds = divmod(llm_delay_seconds, 60)
            for _ in range(int(minutes)):
                sleep(60)
            if seconds:
                sleep(seconds)
        provider_case_seen = True

    for case in selected:
        root = prepare_repository(case, workspace)
        run_id = str(uuid4())
        database_path = Path(".agent-evaluation-v2") / run_id / "agent.sqlite3"
        path = workspace.expanduser().resolve() / database_path
        if path.parent.parent.is_symlink():
            raise ValueError("V2 evaluation refuses a symlinked retention directory")
        create_v2_database(path)
        store = AgentStoreV2(path)
        error_category = None
        try:
            run = run_agent_v2(
                [case.issue_snapshot.model_copy(deep=True)],
                root,
                1,
                store,
                llm_analyzer=analyzer,
                allow_external_llm=True,
                capture_mode=RepositoryCaptureMode.COMMITTED,
                max_evidence_chars=max_evidence_chars,
                max_evidence_lines=max_lines_per_evidence,
                max_attempts=max_llm_attempts,
                as_of=case.issue_updated_at,
                run_id=run_id,
                parameter_origins=parameter_origins,
                before_provider_case=before_provider_case,
            )
        except Exception:
            run = store.get_run(run_id)
            if run is None or run.status != "FAILED":
                raise
            # Preserve audited progress, but stop rather than continuing after a bug.
            error_category = "execution_fatal"
        restored = AgentStoreV2(path).get_run_summary(run.run_id)
        if restored is None or len(restored.issues) != 1:
            raise RuntimeError("V2 evaluation run summary was not persisted")
        issue = restored.issues[0]
        evidence = store.read_evidence(run.run_id, case.issue_number)
        attempts = store.list_attempts(run.run_id, case.issue_number)
        attempt = next(
            (a for a in attempts if a.attempt_id == issue.selected_analysis_attempt_id), None
        )
        analysis = attempt.analysis if attempt is not None and attempt.state == "success" else None
        files = list(dict.fromkeys(item.file for item in evidence.items)) if evidence else []
        expected = [file for file in case.expected_files if file in files]
        cited_ids = (
            {ref for h in analysis.hypotheses for ref in h.evidence_ids} if analysis else set()
        )
        cited = (
            list(
                dict.fromkeys(item.file for item in evidence.items if item.evidence_id in cited_ids)
            )
            if evidence
            else []
        )
        expected_cited = [file for file in case.expected_files if file in cited]
        execution = store.get_issue(run.run_id, case.issue_number)
        persisted = (
            store.get_run(run.run_id) == run
            and execution is not None
            and execution.model_dump()
            == issue.model_dump(include=set(IssueExecutionV2.model_fields))
            and issue.attempt_count == len(attempts)
            and (evidence.evidence_set_id if evidence else None) == issue.evidence_set_id
        )
        if not persisted:
            raise RuntimeError("V2 evaluation persistence verification failed")
        results.append(
            AgentAnalysisCaseResultV2(
                case_id=case.id,
                tier=case.tier,
                repository=case.repository,
                issue_number=case.issue_number,
                pre_fix_sha=case.pre_fix_sha,
                run_id=run.run_id,
                database_path=database_path.as_posix(),
                commit_oid=restored.snapshot.commit_oid,
                agent_status=restored.status,
                deterministic_state=issue.deterministic_state,
                llm_state=issue.llm_state,
                execution_succeeded=run.status != "FAILED"
                and issue.deterministic_state == "succeeded",
                error_category=error_category,
                analysis_succeeded=analysis is not None,
                skipped_no_evidence=issue.llm_state == "skipped_no_evidence",
                persistence_verified=persisted,
                evidence_set_id=issue.evidence_set_id,
                selected_analysis_attempt_id=issue.selected_analysis_attempt_id,
                attempts=attempts,
                expected_files=case.expected_files,
                evidence_files=files,
                expected_files_in_evidence=expected,
                hypothesis_cited_files=cited,
                expected_files_cited_by_hypothesis=expected_cited,
                expected_file_evidence_recall=len(expected) / len(case.expected_files)
                if evidence
                else None,
                hypothesis_expected_file_recall=(
                    len(expected_cited) / len(case.expected_files) if analysis else None
                ),
                analysis=analysis,
            )
        )
        if error_category is not None:
            break
    completed = not any(result.error_category for result in results)
    return AgentAnalysisRunV2(
        manifest_name=manifest.name,
        manifest_version=manifest.version,
        status="COMPLETED" if completed else "FAILED",
        completed=completed,
        planned_cases=len(selected),
        attempted_cases=len(results),
        max_evidence_chars=max_evidence_chars,
        max_lines_per_evidence=max_lines_per_evidence,
        max_llm_attempts=max_llm_attempts,
        llm_delay_seconds=llm_delay_seconds,
        created_at=datetime.now(UTC),
        results=results,
        overall=aggregate_agent_analysis_v2(results),
        by_tier={
            tier.value: aggregate_agent_analysis_v2([r for r in results if r.tier is tier])
            for tier in BenchmarkTier
            if any(r.tier is tier for r in results)
        },
    )
