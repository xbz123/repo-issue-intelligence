from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from statistics import fmean
from time import perf_counter, sleep
from typing import Protocol

from pydantic import BaseModel, Field

from .evidence import collect_evidence
from .github_client import REPOSITORY_PATTERN, GitHubClient
from .investigator import investigate
from .llm_client import GroqAPIError
from .models import (
    EvidenceRerankResult,
    EvidenceSnippet,
    InvestigationReport,
    IssueRecord,
    LLMAnalysisResult,
)
from .repository_index import build_repository_map

SHA_PATTERN = re.compile(r"[0-9a-f]{40}")


class BenchmarkTier(StrEnum):
    MAIN = "main"
    CALIBRATION = "calibration"
    GENERALIZATION = "generalization"


class BenchmarkVariant(StrEnum):
    DETERMINISTIC = "deterministic"
    HYBRID = "hybrid"
    HYBRID_FULL = "hybrid-full"


class EvidenceReranker(Protocol):
    model: str

    def rerank(
        self,
        issue: IssueRecord,
        evidence: Sequence[EvidenceSnippet],
    ) -> EvidenceRerankResult: ...

    def analyze(
        self,
        issue: IssueRecord,
        report: InvestigationReport,
        evidence: Sequence[EvidenceSnippet],
    ) -> LLMAnalysisResult: ...


class BenchmarkCase(BaseModel):
    id: str
    tier: BenchmarkTier
    repository: str
    issue_number: int = Field(ge=1)
    issue_updated_at: datetime
    fix_pr_number: int = Field(ge=1)
    pre_fix_sha: str
    expected_files: list[str] = Field(min_length=1)

    def model_post_init(self, __context: object) -> None:
        if REPOSITORY_PATTERN.fullmatch(self.repository) is None:
            raise ValueError("repository must use the owner/name format")
        if SHA_PATTERN.fullmatch(self.pre_fix_sha) is None:
            raise ValueError("pre_fix_sha must be a 40-character lowercase Git SHA")
        if len(set(self.expected_files)) != len(self.expected_files):
            raise ValueError("expected_files must not contain duplicates")


class BenchmarkManifest(BaseModel):
    name: str
    version: int = Field(ge=1)
    cases: list[BenchmarkCase] = Field(min_length=1)

    def model_post_init(self, __context: object) -> None:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("benchmark case IDs must be unique")


class BenchmarkCaseResult(BaseModel):
    case_id: str
    tier: BenchmarkTier
    repository: str
    issue_number: int
    issue_url: str
    fix_pr_url: str
    pre_fix_sha: str
    issue_updated_at: datetime
    expected_files: list[str]
    candidate_files: list[str] = Field(default_factory=list)
    matched_files_at_5: list[str] = Field(default_factory=list)
    file_recall_at_1: float = 0
    file_recall_at_5: float = 0
    reciprocal_rank: float = 0
    analysis_elapsed_ms: float = 0
    llm_attempts: int = 0
    llm_fallback_used: bool = False
    llm_request_id: str | None = None
    llm_system_fingerprint: str | None = None
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_elapsed_ms: float = 0
    error: str | None = None


class BenchmarkAggregate(BaseModel):
    cases: int
    completed: int
    failed: int
    file_recall_at_1: float
    file_recall_at_5: float
    mean_reciprocal_rank: float
    average_analysis_elapsed_ms: float
    llm_success_rate: float | None = None
    llm_cases: int = 0
    llm_successes: int = 0
    llm_fallbacks: int = 0
    average_llm_elapsed_ms: float | None = None
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0


class BenchmarkRun(BaseModel):
    manifest_name: str
    manifest_version: int
    variant: BenchmarkVariant
    model: str | None = None
    max_evidence_chars: int | None = None
    max_output_tokens: int | None = None
    max_llm_attempts: int | None = None
    llm_delay_seconds: float | None = None
    reasoning_effort: str | None = None
    temperature: float | None = None
    seed: int | None = None
    created_at: datetime
    results: list[BenchmarkCaseResult]
    overall: BenchmarkAggregate
    by_tier: dict[str, BenchmarkAggregate]


def load_manifest(path: Path) -> BenchmarkManifest:
    return BenchmarkManifest.model_validate_json(path.read_text(encoding="utf-8"))


def _run_git(arguments: list[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()[-500:]
        raise RuntimeError(f"git {' '.join(arguments[:2])} failed: {detail}")
    return completed.stdout.strip()


def prepare_repository(case: BenchmarkCase, workspace: Path) -> Path:
    workspace = workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / case.repository.replace("/", "--")
    expected_remote = f"https://github.com/{case.repository}.git"
    if not target.exists():
        _run_git(["clone", "--filter=blob:none", "--no-checkout", expected_remote, str(target)])
    elif not (target / ".git").is_dir():
        raise ValueError(f"Benchmark workspace is not a Git repository: {target}")
    else:
        remote = _run_git(["remote", "get-url", "origin"], cwd=target)
        if remote.rstrip("/") not in {
            expected_remote,
            f"git@github.com:{case.repository}.git",
        }:
            raise ValueError(f"Unexpected origin for benchmark workspace: {remote}")
        if _run_git(["status", "--porcelain"], cwd=target):
            raise ValueError(f"Benchmark workspace has uncommitted changes: {target}")

    _run_git(["fetch", "--depth=1", "origin", case.pre_fix_sha], cwd=target)
    _run_git(["checkout", "--detach", case.pre_fix_sha], cwd=target)
    checked_out_sha = _run_git(["rev-parse", "HEAD"], cwd=target)
    if checked_out_sha != case.pre_fix_sha:
        raise ValueError(
            f"Benchmark checkout mismatch: expected {case.pre_fix_sha}, got {checked_out_sha}"
        )
    missing_files = [
        expected_file
        for expected_file in case.expected_files
        if not (target / expected_file).is_file()
    ]
    if missing_files:
        raise ValueError(
            "Expected source files are missing at the pre-fix SHA: "
            + ", ".join(missing_files)
        )
    return target


def _unique_files(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _hybrid_candidate_files(
    issue: IssueRecord,
    report,
    analyzer: EvidenceReranker,
    max_evidence_chars: int,
    max_attempts: int,
    retry_delay_seconds: float,
    full_analysis: bool,
) -> tuple[list[str], EvidenceRerankResult | LLMAnalysisResult, int]:
    evidence = collect_evidence(report, max_total_chars=max_evidence_chars)
    if not evidence:
        raise GroqAPIError("No repository evidence was available for hybrid reranking")
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            if full_analysis:
                analysis = analyzer.analyze(issue, report, evidence)
            else:
                analysis = analyzer.rerank(issue, evidence)
            evidence_files = {snippet.id: snippet.file for snippet in evidence}
            reranked = [
                evidence_files[evidence_id]
                for evidence_id in analysis.analysis.reranked_evidence_ids
            ]
            remaining = [candidate.file for candidate in report.candidates]
            return _unique_files([*reranked, *remaining]), analysis, attempt
        except GroqAPIError as error:
            last_error = error
            if attempt == max_attempts:
                break
            retry_after = error.retry_after
            delay = retry_delay_seconds
            if isinstance(retry_after, (int, float)) and retry_after > 0:
                delay = max(delay, float(retry_after))
            if delay > 0:
                sleep(min(delay, 60.0))
    assert last_error is not None
    raise last_error


def evaluate_case(
    case: BenchmarkCase,
    issue: IssueRecord,
    repository_root: Path,
    variant: BenchmarkVariant,
    analyzer: EvidenceReranker | None = None,
    max_evidence_chars: int = 16_000,
    max_llm_attempts: int = 2,
    llm_retry_delay_seconds: float = 0,
) -> BenchmarkCaseResult:
    if issue.updated_at != case.issue_updated_at:
        raise ValueError(
            f"Issue #{case.issue_number} changed after the benchmark manifest was created"
        )
    if variant is not BenchmarkVariant.DETERMINISTIC and analyzer is None:
        raise ValueError("Hybrid benchmark requires an EvidenceReranker")

    started = perf_counter()
    report = investigate(issue, build_repository_map(repository_root))
    candidate_files = _unique_files([candidate.file for candidate in report.candidates])
    llm_result = None
    llm_attempts = 0
    fallback_used = False
    error = None
    if variant is not BenchmarkVariant.DETERMINISTIC:
        try:
            candidate_files, llm_result, llm_attempts = _hybrid_candidate_files(
                issue,
                report,
                analyzer,
                max_evidence_chars,
                max_llm_attempts,
                llm_retry_delay_seconds,
                variant is BenchmarkVariant.HYBRID_FULL,
            )
        except GroqAPIError as llm_error:
            fallback_used = True
            llm_attempts = max_llm_attempts
            error = f"{type(llm_error).__name__}: {llm_error}"

    expected = set(case.expected_files)
    top_1 = candidate_files[:1]
    top_5 = candidate_files[:5]
    matched_at_5 = sorted(expected.intersection(top_5))
    first_rank = next(
        (index for index, path in enumerate(candidate_files, start=1) if path in expected),
        None,
    )
    return BenchmarkCaseResult(
        case_id=case.id,
        tier=case.tier,
        repository=case.repository,
        issue_number=case.issue_number,
        issue_url=f"https://github.com/{case.repository}/issues/{case.issue_number}",
        fix_pr_url=f"https://github.com/{case.repository}/pull/{case.fix_pr_number}",
        pre_fix_sha=case.pre_fix_sha,
        issue_updated_at=issue.updated_at,
        expected_files=case.expected_files,
        candidate_files=candidate_files,
        matched_files_at_5=matched_at_5,
        file_recall_at_1=round(len(expected.intersection(top_1)) / len(expected), 4),
        file_recall_at_5=round(len(matched_at_5) / len(expected), 4),
        reciprocal_rank=round(1 / first_rank, 4) if first_rank else 0,
        analysis_elapsed_ms=round((perf_counter() - started) * 1000, 3),
        llm_attempts=llm_attempts,
        llm_fallback_used=fallback_used,
        llm_request_id=llm_result.request_id if llm_result else None,
        llm_system_fingerprint=(
            llm_result.system_fingerprint if llm_result else None
        ),
        llm_input_tokens=llm_result.input_tokens if llm_result else 0,
        llm_output_tokens=llm_result.output_tokens if llm_result else 0,
        llm_elapsed_ms=llm_result.elapsed_ms if llm_result else 0,
        error=error,
    )


def _aggregate(results: Sequence[BenchmarkCaseResult]) -> BenchmarkAggregate:
    completed = [result for result in results if result.candidate_files]
    llm_results = [result for result in completed if result.llm_attempts]
    successful_llm_results = [
        result for result in llm_results if not result.llm_fallback_used
    ]
    return BenchmarkAggregate(
        cases=len(results),
        completed=len(completed),
        failed=len(results) - len(completed),
        file_recall_at_1=round(fmean(r.file_recall_at_1 for r in completed), 4)
        if completed
        else 0,
        file_recall_at_5=round(fmean(r.file_recall_at_5 for r in completed), 4)
        if completed
        else 0,
        mean_reciprocal_rank=round(fmean(r.reciprocal_rank for r in completed), 4)
        if completed
        else 0,
        average_analysis_elapsed_ms=round(
            fmean(r.analysis_elapsed_ms for r in completed), 3
        )
        if completed
        else 0,
        llm_success_rate=round(
            sum(not result.llm_fallback_used for result in llm_results) / len(llm_results),
            4,
        )
        if llm_results
        else None,
        llm_cases=len(llm_results),
        llm_successes=len(successful_llm_results),
        llm_fallbacks=len(llm_results) - len(successful_llm_results),
        average_llm_elapsed_ms=round(
            fmean(result.llm_elapsed_ms for result in successful_llm_results), 3
        )
        if successful_llm_results
        else None,
        llm_input_tokens=sum(result.llm_input_tokens for result in completed),
        llm_output_tokens=sum(result.llm_output_tokens for result in completed),
    )


def run_benchmark(
    manifest: BenchmarkManifest,
    workspace: Path,
    variant: BenchmarkVariant,
    github_client: GitHubClient,
    analyzer: EvidenceReranker | None = None,
    case_ids: set[str] | None = None,
    max_evidence_chars: int = 16_000,
    max_llm_attempts: int = 2,
    llm_delay_seconds: float = 0,
) -> BenchmarkRun:
    available_case_ids = {case.id for case in manifest.cases}
    unknown_case_ids = (case_ids or set()) - available_case_ids
    if unknown_case_ids:
        raise ValueError(
            f"Unknown benchmark case IDs: {', '.join(sorted(unknown_case_ids))}"
        )
    selected = [
        case for case in manifest.cases if case_ids is None or case.id in case_ids
    ]
    if not selected:
        raise ValueError("No benchmark cases matched the requested case IDs")

    results: list[BenchmarkCaseResult] = []
    for case in selected:
        try:
            issue = github_client.fetch_issue(case.repository, case.issue_number)
            repository_root = prepare_repository(case, workspace)
            result = evaluate_case(
                case,
                issue,
                repository_root,
                variant,
                analyzer,
                max_evidence_chars=max_evidence_chars,
                max_llm_attempts=max_llm_attempts,
                llm_retry_delay_seconds=llm_delay_seconds,
            )
        except Exception as error:
            result = BenchmarkCaseResult(
                case_id=case.id,
                tier=case.tier,
                repository=case.repository,
                issue_number=case.issue_number,
                issue_url=f"https://github.com/{case.repository}/issues/{case.issue_number}",
                fix_pr_url=f"https://github.com/{case.repository}/pull/{case.fix_pr_number}",
                pre_fix_sha=case.pre_fix_sha,
                issue_updated_at=case.issue_updated_at,
                expected_files=case.expected_files,
                error=f"{type(error).__name__}: {error}",
            )
        results.append(result)
        if (
            variant is not BenchmarkVariant.DETERMINISTIC
            and llm_delay_seconds > 0
            and case is not selected[-1]
        ):
            sleep(llm_delay_seconds)

    by_tier = {
        tier.value: _aggregate([result for result in results if result.tier is tier])
        for tier in BenchmarkTier
        if any(result.tier is tier for result in results)
    }
    return BenchmarkRun(
        manifest_name=manifest.name,
        manifest_version=manifest.version,
        variant=variant,
        model=analyzer.model if analyzer else None,
        max_evidence_chars=max_evidence_chars if analyzer else None,
        max_output_tokens=getattr(analyzer, "max_output_tokens", None),
        max_llm_attempts=max_llm_attempts if analyzer else None,
        llm_delay_seconds=llm_delay_seconds if analyzer else None,
        reasoning_effort=getattr(analyzer, "reasoning_effort", None),
        temperature=getattr(analyzer, "temperature", None),
        seed=getattr(analyzer, "seed", None),
        created_at=datetime.now(UTC),
        results=results,
        overall=_aggregate(results),
        by_tier=by_tier,
    )


def save_benchmark_run(run: BenchmarkRun, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(run.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
