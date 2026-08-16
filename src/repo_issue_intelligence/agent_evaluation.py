from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from tempfile import TemporaryDirectory
from time import sleep

from pydantic import BaseModel, Field

from .agent_store import AgentStore
from .agent_workflow import run_agent
from .benchmark import (
    BenchmarkCase,
    BenchmarkManifest,
    BenchmarkTier,
    prepare_repository,
    tracked_repository_files,
)
from .llm_client import IssueAnalyzer, LLMProviderError
from .models import AgentRun, AgentRunStatus, LLMAnalysis, LLMAnalysisResult


class AgentAnalysisCaseResult(BaseModel):
    case_id: str
    tier: BenchmarkTier
    repository: str
    issue_number: int
    pre_fix_sha: str
    agent_status: AgentRunStatus | None = None
    analysis_succeeded: bool = False
    skipped_no_evidence: bool = False
    persistence_verified: bool = False
    llm_attempts: int = 0
    request_ids: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    llm_elapsed_ms: float = 0
    evidence_observations: int = 0
    hypotheses: int = 0
    needs_more_evidence: bool | None = None
    analysis: LLMAnalysis | None = None
    error_category: str | None = None
    error: str | None = None


class AgentAnalysisAggregate(BaseModel):
    cases: int
    analysis_successes: int
    skipped_no_evidence: int
    failures: int
    analysis_success_rate: float
    first_attempt_success_rate: float
    persistence_verified: int
    input_tokens: int
    output_tokens: int
    average_llm_elapsed_ms: float | None = None
    error_categories: dict[str, int] = Field(default_factory=dict)


class AgentAnalysisRun(BaseModel):
    manifest_name: str
    manifest_version: int
    provider: str
    model: str
    max_evidence_chars: int | None
    max_llm_attempts: int
    llm_delay_seconds: float
    temperature: float | None = None
    seed: int | None = None
    created_at: datetime
    results: list[AgentAnalysisCaseResult]
    overall: AgentAnalysisAggregate
    by_tier: dict[str, AgentAnalysisAggregate]


class _TrackingAnalyzer:
    def __init__(self, analyzer: IssueAnalyzer) -> None:
        self._analyzer = analyzer
        self.provider = analyzer.provider
        self.model = analyzer.model
        self.calls = 0
        self.outcomes: list[LLMAnalysisResult | Exception] = []

    def analyze(self, issue, report, evidence) -> LLMAnalysisResult:
        self.calls += 1
        try:
            result = self._analyzer.analyze(issue, report, evidence)
        except Exception as error:
            self.outcomes.append(error)
            raise
        self.outcomes.append(result)
        return result

    def close(self) -> None:
        return None

    def telemetry(self) -> dict[str, object]:
        provider_outcomes = [
            outcome
            for outcome in self.outcomes
            if isinstance(outcome, (LLMAnalysisResult, LLMProviderError))
        ]
        return {
            "request_ids": [
                outcome.request_id
                for outcome in provider_outcomes
                if outcome.request_id is not None
            ],
            "input_tokens": sum(outcome.input_tokens for outcome in provider_outcomes),
            "output_tokens": sum(outcome.output_tokens for outcome in provider_outcomes),
            "llm_elapsed_ms": round(
                sum(outcome.elapsed_ms for outcome in provider_outcomes),
                3,
            ),
        }


class _CapturingAgentStore(AgentStore):
    def __init__(self, database_path: Path) -> None:
        self.last_run_id: str | None = None
        super().__init__(database_path)

    def save_run(self, run: AgentRun) -> None:
        self.last_run_id = run.run_id
        super().save_run(run)


def _aggregate(results: Sequence[AgentAnalysisCaseResult]) -> AgentAnalysisAggregate:
    successful = [result for result in results if result.analysis_succeeded]
    categories = {
        category: sum(result.error_category == category for result in results)
        for category in sorted(
            {
                result.error_category
                for result in results
                if result.error_category is not None
            }
        )
    }
    return AgentAnalysisAggregate(
        cases=len(results),
        analysis_successes=len(successful),
        skipped_no_evidence=sum(result.skipped_no_evidence for result in results),
        failures=sum(result.error is not None for result in results),
        analysis_success_rate=round(len(successful) / len(results), 4) if results else 0,
        first_attempt_success_rate=round(
            sum(result.analysis_succeeded and result.llm_attempts == 1 for result in results)
            / len(results),
            4,
        )
        if results
        else 0,
        persistence_verified=sum(result.persistence_verified for result in results),
        input_tokens=sum(result.input_tokens for result in results),
        output_tokens=sum(result.output_tokens for result in results),
        average_llm_elapsed_ms=round(
            fmean(result.llm_elapsed_ms for result in successful),
            3,
        )
        if successful
        else None,
        error_categories=categories,
    )


def _evaluate_case(
    case: BenchmarkCase,
    workspace: Path,
    analyzer: IssueAnalyzer,
    max_evidence_chars: int | None,
    max_llm_attempts: int,
) -> AgentAnalysisCaseResult:
    repository_root = prepare_repository(case, workspace)
    tracking_analyzer = _TrackingAnalyzer(analyzer)
    with TemporaryDirectory(prefix="rii-agent-evaluation-") as temporary_directory:
        store = _CapturingAgentStore(Path(temporary_directory) / "agent.sqlite3")
        try:
            run = run_agent(
                [case.issue_snapshot.model_copy(deep=True)],
                repository_root,
                top_k=1,
                store=store,
                llm_analyzer=tracking_analyzer,
                max_evidence_chars=max_evidence_chars,
                included_files=tracked_repository_files(repository_root),
                max_attempts=max_llm_attempts,
            )
        except Exception as error:
            category = (
                error.category if isinstance(error, LLMProviderError) else type(error).__name__
            )
            error_text = f"{type(error).__name__}: {error}"
            restored = (
                store.get_run(store.last_run_id)
                if store.last_run_id is not None
                else None
            )
            telemetry = tracking_analyzer.telemetry()
            return AgentAnalysisCaseResult(
                case_id=case.id,
                tier=case.tier,
                repository=case.repository,
                issue_number=case.issue_number,
                pre_fix_sha=case.pre_fix_sha,
                agent_status=restored.status if restored is not None else None,
                persistence_verified=(
                    restored is not None
                    and restored.status is AgentRunStatus.FAILED
                    and restored.error == error_text
                ),
                llm_attempts=tracking_analyzer.calls,
                **telemetry,
                error_category=category,
                error=error_text,
            )

        restored = store.get_run(run.run_id)
        llm_trace = next(
            trace for trace in reversed(run.traces) if trace.node_name == "llm_analyze"
        )
        report = run.investigations[0]
        analysis_result = report.llm_analysis
        telemetry = tracking_analyzer.telemetry()
        persistence_verified = (
            restored is not None
            and restored.model_dump(mode="json") == run.model_dump(mode="json")
        )
        skipped = case.issue_number in llm_trace.metadata.get(
            "skipped_no_evidence_issue_numbers",
            [],
        )
        return AgentAnalysisCaseResult(
            case_id=case.id,
            tier=case.tier,
            repository=case.repository,
            issue_number=case.issue_number,
            pre_fix_sha=case.pre_fix_sha,
            agent_status=run.status,
            analysis_succeeded=(
                analysis_result is not None
                and run.status is AgentRunStatus.AWAITING_REVIEW
                and persistence_verified
            ),
            skipped_no_evidence=skipped,
            persistence_verified=persistence_verified,
            llm_attempts=tracking_analyzer.calls,
            **telemetry,
            evidence_observations=(
                len(analysis_result.analysis.evidence_observations)
                if analysis_result is not None
                else 0
            ),
            hypotheses=(
                len(analysis_result.analysis.hypotheses)
                if analysis_result is not None
                else 0
            ),
            needs_more_evidence=(
                analysis_result.analysis.needs_more_evidence
                if analysis_result is not None
                else None
            ),
            analysis=(
                LLMAnalysis.model_validate(analysis_result.analysis.model_dump())
                if analysis_result is not None
                else None
            ),
        )


def run_agent_analysis_evaluation(
    manifest: BenchmarkManifest,
    workspace: Path,
    analyzer: IssueAnalyzer,
    case_ids: set[str] | None = None,
    max_evidence_chars: int | None = None,
    max_llm_attempts: int = 2,
    llm_delay_seconds: float = 0,
) -> AgentAnalysisRun:
    available_case_ids = {case.id for case in manifest.cases}
    unknown_case_ids = (case_ids or set()) - available_case_ids
    if unknown_case_ids:
        raise ValueError(
            "Unknown benchmark case IDs: " + ", ".join(sorted(unknown_case_ids))
        )
    selected = [
        case for case in manifest.cases if case_ids is None or case.id in case_ids
    ]
    if not selected:
        raise ValueError("No benchmark cases matched the requested case IDs")

    results: list[AgentAnalysisCaseResult] = []
    for case in selected:
        try:
            result = _evaluate_case(
                case,
                workspace,
                analyzer,
                max_evidence_chars,
                max_llm_attempts,
            )
        except Exception as error:
            category = (
                error.category if isinstance(error, LLMProviderError) else type(error).__name__
            )
            result = AgentAnalysisCaseResult(
                case_id=case.id,
                tier=case.tier,
                repository=case.repository,
                issue_number=case.issue_number,
                pre_fix_sha=case.pre_fix_sha,
                error_category=category,
                error=f"{type(error).__name__}: {error}",
            )
        results.append(result)
        if llm_delay_seconds > 0 and case is not selected[-1]:
            sleep(llm_delay_seconds)

    return AgentAnalysisRun(
        manifest_name=manifest.name,
        manifest_version=manifest.version,
        provider=analyzer.provider,
        model=analyzer.model,
        max_evidence_chars=max_evidence_chars,
        max_llm_attempts=max_llm_attempts,
        llm_delay_seconds=llm_delay_seconds,
        temperature=getattr(analyzer, "temperature", None),
        seed=getattr(analyzer, "seed", None),
        created_at=datetime.now(UTC),
        results=results,
        overall=_aggregate(results),
        by_tier={
            tier.value: _aggregate([result for result in results if result.tier is tier])
            for tier in BenchmarkTier
            if any(result.tier is tier for result in results)
        },
    )


def save_agent_analysis_run(run: AgentAnalysisRun, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(run.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
