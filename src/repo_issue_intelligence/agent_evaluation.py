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
from .evidence import (
    DEFAULT_MAX_LINES_PER_SNIPPET,
    DEFAULT_MAX_TOTAL_CHARS,
    collect_evidence,
)
from .llm_client import IssueAnalyzer, LLMProviderError
from .models import (
    AgentRun,
    AgentRunStatus,
    EvidenceSnippet,
    InvestigationReport,
    LLMAnalysis,
    LLMAnalysisResult,
)


class AgentAnalysisCaseResult(BaseModel):
    case_id: str
    tier: BenchmarkTier
    repository: str
    issue_number: int
    pre_fix_sha: str
    expected_files: list[str] = Field(default_factory=list)
    evidence_files: list[str] = Field(default_factory=list)
    expected_files_in_evidence: list[str] = Field(default_factory=list)
    hypothesis_cited_files: list[str] = Field(default_factory=list)
    expected_files_cited_by_hypothesis: list[str] = Field(default_factory=list)
    expected_file_evidence_recall: float | None = None
    hypothesis_expected_file_recall: float | None = None
    hypothesis_expected_file_hit: bool | None = None
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
    evidence_quality_cases: int = 0
    expected_file_evidence_hits: int = 0
    expected_file_evidence_hit_rate: float | None = None
    mean_expected_file_evidence_recall: float | None = None
    hypothesis_quality_cases: int = 0
    hypothesis_expected_file_hits: int = 0
    overall_hypothesis_expected_file_hit_rate: float | None = None
    hypothesis_expected_file_hit_rate: float | None = None
    mean_hypothesis_expected_file_recall: float | None = None
    hypothesis_hit_rate_when_expected_evidence_available: float | None = None


class AgentAnalysisRun(BaseModel):
    manifest_name: str
    manifest_version: int
    provider: str
    model: str
    max_output_tokens: int | None = None
    max_evidence_chars: int | None
    max_lines_per_evidence: int | None = None
    max_llm_attempts: int
    llm_delay_seconds: float
    temperature: float | None = None
    seed: int | None = None
    reasoning_effort: str | None = None
    service_tier: str | None = None
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
        self.evidence_by_issue: dict[int, tuple[EvidenceSnippet, ...]] = {}

    def analyze(self, issue, report, evidence) -> LLMAnalysisResult:
        self.calls += 1
        self.evidence_by_issue[issue.number] = tuple(evidence)
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
    evidence_quality = [
        result
        for result in results
        if result.expected_file_evidence_recall is not None
    ]
    hypothesis_quality = [
        result
        for result in results
        if result.analysis_succeeded
        and result.hypothesis_expected_file_recall is not None
    ]
    hypothesis_with_expected_evidence = [
        result for result in hypothesis_quality if result.expected_files_in_evidence
    ]
    evidence_hits = sum(
        bool(result.expected_files_in_evidence) for result in evidence_quality
    )
    hypothesis_hits = sum(
        result.hypothesis_expected_file_hit is True for result in hypothesis_quality
    )
    available_hypothesis_hits = sum(
        result.hypothesis_expected_file_hit is True
        for result in hypothesis_with_expected_evidence
    )
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
        evidence_quality_cases=len(evidence_quality),
        expected_file_evidence_hits=evidence_hits,
        expected_file_evidence_hit_rate=(
            round(evidence_hits / len(evidence_quality), 4)
            if evidence_quality
            else None
        ),
        mean_expected_file_evidence_recall=(
            round(
                fmean(
                    result.expected_file_evidence_recall
                    for result in evidence_quality
                    if result.expected_file_evidence_recall is not None
                ),
                4,
            )
            if evidence_quality
            else None
        ),
        hypothesis_quality_cases=len(hypothesis_quality),
        hypothesis_expected_file_hits=hypothesis_hits,
        overall_hypothesis_expected_file_hit_rate=(
            round(hypothesis_hits / len(results), 4) if results else None
        ),
        hypothesis_expected_file_hit_rate=(
            round(hypothesis_hits / len(hypothesis_quality), 4)
            if hypothesis_quality
            else None
        ),
        mean_hypothesis_expected_file_recall=(
            round(
                fmean(
                    result.hypothesis_expected_file_recall
                    for result in hypothesis_quality
                    if result.hypothesis_expected_file_recall is not None
                ),
                4,
            )
            if hypothesis_quality
            else None
        ),
        hypothesis_hit_rate_when_expected_evidence_available=(
            round(
                available_hypothesis_hits / len(hypothesis_with_expected_evidence),
                4,
            )
            if hypothesis_with_expected_evidence
            else None
        ),
    )


def _quality_metrics(
    case: BenchmarkCase,
    report: InvestigationReport,
    analysis: LLMAnalysis | None,
    max_evidence_chars: int | None,
    max_lines_per_evidence: int | None,
) -> dict[str, object]:
    evidence = collect_evidence(
        report,
        max_total_chars=max_evidence_chars,
        max_lines_per_snippet=max_lines_per_evidence,
    )
    return _quality_metrics_from_evidence(case, evidence, analysis)


def _quality_metrics_from_evidence(
    case: BenchmarkCase,
    evidence: Sequence[EvidenceSnippet],
    analysis: LLMAnalysis | None,
) -> dict[str, object]:
    evidence_files = list(dict.fromkeys(snippet.file for snippet in evidence))
    expected_files = set(case.expected_files)
    expected_files_in_evidence = [
        path for path in case.expected_files if path in evidence_files
    ]
    cited_evidence_ids = (
        {
            evidence_id
            for hypothesis in analysis.hypotheses
            for evidence_id in hypothesis.evidence_ids
        }
        if analysis is not None
        else set()
    )
    hypothesis_cited_files = list(
        dict.fromkeys(
            snippet.file for snippet in evidence if snippet.id in cited_evidence_ids
        )
    )
    expected_files_cited = [
        path for path in case.expected_files if path in hypothesis_cited_files
    ]
    return {
        "expected_files": case.expected_files,
        "evidence_files": evidence_files,
        "expected_files_in_evidence": expected_files_in_evidence,
        "hypothesis_cited_files": hypothesis_cited_files,
        "expected_files_cited_by_hypothesis": expected_files_cited,
        "expected_file_evidence_recall": round(
            len(expected_files_in_evidence) / len(expected_files),
            4,
        ),
        "hypothesis_expected_file_recall": (
            round(len(expected_files_cited) / len(expected_files), 4)
            if analysis is not None
            else None
        ),
        "hypothesis_expected_file_hit": (
            bool(expected_files_cited) if analysis is not None else None
        ),
    }


def _evaluate_case(
    case: BenchmarkCase,
    workspace: Path,
    analyzer: IssueAnalyzer,
    max_evidence_chars: int | None,
    max_lines_per_evidence: int | None,
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
                max_evidence_lines=max_lines_per_evidence,
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
            supplied_evidence = tracking_analyzer.evidence_by_issue.get(
                case.issue_number
            )
            quality_metrics = (
                _quality_metrics_from_evidence(case, supplied_evidence, None)
                if supplied_evidence is not None
                else {"expected_files": case.expected_files}
            )
            return AgentAnalysisCaseResult(
                case_id=case.id,
                tier=case.tier,
                repository=case.repository,
                issue_number=case.issue_number,
                pre_fix_sha=case.pre_fix_sha,
                **quality_metrics,
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
        analysis_succeeded = (
            analysis_result is not None
            and run.status is AgentRunStatus.AWAITING_REVIEW
            and persistence_verified
        )
        quality_metrics = _quality_metrics(
            case,
            report,
            analysis_result.analysis if analysis_succeeded else None,
            max_evidence_chars,
            max_lines_per_evidence,
        )
        return AgentAnalysisCaseResult(
            case_id=case.id,
            tier=case.tier,
            repository=case.repository,
            issue_number=case.issue_number,
            pre_fix_sha=case.pre_fix_sha,
            **quality_metrics,
            agent_status=run.status,
            analysis_succeeded=analysis_succeeded,
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
    max_evidence_chars: int | None = DEFAULT_MAX_TOTAL_CHARS,
    max_lines_per_evidence: int | None = DEFAULT_MAX_LINES_PER_SNIPPET,
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
                max_lines_per_evidence,
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
                expected_files=case.expected_files,
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
        max_output_tokens=getattr(analyzer, "max_output_tokens", None),
        max_evidence_chars=max_evidence_chars,
        max_lines_per_evidence=max_lines_per_evidence,
        max_llm_attempts=max_llm_attempts,
        llm_delay_seconds=llm_delay_seconds,
        temperature=getattr(analyzer, "temperature", None),
        seed=getattr(analyzer, "seed", None),
        reasoning_effort=getattr(analyzer, "reasoning_effort", None),
        service_tier=getattr(analyzer, "service_tier", None),
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
