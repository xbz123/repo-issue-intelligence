from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from statistics import fmean
from time import perf_counter, sleep
from typing import Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from .evidence import (
    DEFAULT_MAX_LINES_PER_SNIPPET,
    DEFAULT_MAX_TOTAL_CHARS,
    collect_evidence,
)
from .github_client import REPOSITORY_PATTERN
from .investigator import (
    DEFAULT_CANDIDATE_LIMIT,
    investigate,
)
from .llm_client import LLMProviderError
from .models import (
    CandidateLocation,
    EvidenceRerankResult,
    EvidenceSnippet,
    IssueRecord,
    RepositoryMap,
)
from .repository_index import (
    REPOSITORY_MAP_INDEX_VERSION,
    build_repository_map,
    repository_map_input_files,
)

SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
REPOSITORY_MAP_CACHE_SCHEMA_VERSION = 2
REPOSITORY_MAP_CACHE_DIRECTORY = ".repository-map-cache"
HYBRID_CANDIDATE_POOL_LIMIT = 40
MAX_RERANKED_EVIDENCE_IDS = 3


def _max_chars_per_evidence(
    max_evidence_chars: int | None,
    candidate_pool_limit: int,
) -> int | None:
    if max_evidence_chars is None:
        return None
    return max(1, max_evidence_chars // candidate_pool_limit)


class BenchmarkTier(StrEnum):
    MAIN = "main"
    CALIBRATION = "calibration"
    GENERALIZATION = "generalization"


class BenchmarkVariant(StrEnum):
    DETERMINISTIC = "deterministic"
    HYBRID = "hybrid"


class EvidenceReranker(Protocol):
    provider: str
    model: str

    def rerank(
        self,
        issue: IssueRecord,
        evidence: Sequence[EvidenceSnippet],
    ) -> EvidenceRerankResult: ...


class BenchmarkSymbolTarget(BaseModel):
    file: str
    symbol: str


class BenchmarkSymbolCandidate(BenchmarkSymbolTarget):
    qualified_symbol: str | None = None
    # ``rank`` remains the containing file rank for historical artifacts.
    rank: int = Field(ge=1)
    within_file_rank: int = Field(default=1, ge=1)


class BenchmarkCase(BaseModel):
    id: str
    tier: BenchmarkTier
    repository: str
    issue_number: int = Field(ge=1)
    issue_updated_at: datetime
    issue_snapshot: IssueRecord
    fix_pr_number: int = Field(ge=1)
    pre_fix_sha: str
    expected_files: list[str] = Field(min_length=1)
    expected_symbols: list[BenchmarkSymbolTarget] = Field(default_factory=list)

    def model_post_init(self, __context: object) -> None:
        if REPOSITORY_PATTERN.fullmatch(self.repository) is None:
            raise ValueError("repository must use the owner/name format")
        if SHA_PATTERN.fullmatch(self.pre_fix_sha) is None:
            raise ValueError("pre_fix_sha must be a 40-character lowercase Git SHA")
        if len(set(self.expected_files)) != len(self.expected_files):
            raise ValueError("expected_files must not contain duplicates")
        symbol_keys = {
            (target.file, target.symbol) for target in self.expected_symbols
        }
        if len(symbol_keys) != len(self.expected_symbols):
            raise ValueError("expected_symbols must not contain duplicates")
        unknown_symbol_files = {
            target.file for target in self.expected_symbols
        } - set(self.expected_files)
        if unknown_symbol_files:
            raise ValueError(
                "expected symbol files must also appear in expected_files: "
                + ", ".join(sorted(unknown_symbol_files))
            )
        if self.issue_snapshot.number != self.issue_number:
            raise ValueError("issue_snapshot number must match issue_number")
        if self.issue_snapshot.updated_at != self.issue_updated_at:
            raise ValueError("issue_snapshot updated_at must match issue_updated_at")


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
    expected_symbols: list[BenchmarkSymbolTarget] = Field(default_factory=list)
    candidate_files: list[str] = Field(default_factory=list)
    candidate_symbols: list[BenchmarkSymbolCandidate] = Field(default_factory=list)
    matched_files_at_5: list[str] = Field(default_factory=list)
    matched_files_at_10: list[str] = Field(default_factory=list)
    matched_files_at_20: list[str] = Field(default_factory=list)
    file_recall_at_1: float = 0
    file_recall_at_5: float = 0
    file_recall_at_10: float = 0
    file_recall_at_20: float = 0
    candidate_pool_recall: float = 0
    reciprocal_rank: float = 0
    symbol_recall_at_1: float | None = None
    symbol_recall_at_5: float | None = None
    symbol_recall_at_10: float | None = None
    symbol_recall_at_20: float | None = None
    symbol_reciprocal_rank: float | None = None
    file_conditioned_symbol_targets: int = 0
    file_conditioned_symbol_recall_at_1: float | None = None
    file_conditioned_symbol_recall_at_3: float | None = None
    within_file_symbol_reciprocal_rank: float | None = None
    expected_file_found_but_symbol_missing: list[BenchmarkSymbolTarget] = Field(
        default_factory=list
    )
    analysis_elapsed_ms: float = 0
    repository_map_cache_hit: bool | None = None
    llm_attempts: int = 0
    llm_fallback_used: bool = False
    llm_fallback_reason: str | None = None
    llm_request_id: str | None = None
    llm_system_fingerprint: str | None = None
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_elapsed_ms: float = 0
    error: str | None = None
    execution_succeeded: bool = True


class BenchmarkAggregate(BaseModel):
    cases: int
    completed: int
    failed: int
    file_recall_at_1: float
    file_recall_at_5: float
    file_recall_at_10: float = 0
    file_recall_at_20: float = 0
    candidate_pool_recall: float = 0
    mean_reciprocal_rank: float
    average_analysis_elapsed_ms: float
    llm_success_rate: float | None = None
    llm_cases: int = 0
    llm_successes: int = 0
    llm_fallbacks: int = 0
    llm_fallback_reasons: dict[str, int] = Field(default_factory=dict)
    llm_success_mean_reciprocal_rank: float | None = None
    average_llm_elapsed_ms: float | None = None
    average_llm_success_elapsed_ms: float | None = None
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    symbol_cases: int = 0
    symbol_recall_at_1: float | None = None
    symbol_recall_at_5: float | None = None
    symbol_recall_at_10: float | None = None
    symbol_recall_at_20: float | None = None
    mean_symbol_reciprocal_rank: float | None = None
    file_conditioned_symbol_cases: int = 0
    file_conditioned_symbol_targets: int = 0
    file_conditioned_symbol_recall_at_1: float | None = None
    file_conditioned_symbol_recall_at_3: float | None = None
    mean_within_file_symbol_reciprocal_rank: float | None = None
    expected_file_found_but_symbol_missing_cases: int = 0
    expected_file_found_but_symbol_missing_targets: int = 0


class BenchmarkRun(BaseModel):
    manifest_name: str
    manifest_version: int
    variant: BenchmarkVariant | Literal["hybrid-full"]
    symbol_metric_protocol: Literal["file-cutoff-and-within-file-v1"] = (
        "file-cutoff-and-within-file-v1"
    )
    provider: str | None = None
    model: str | None = None
    max_evidence_chars: int | None = None
    max_lines_per_evidence: int | None = None
    candidate_pool_limit: int | None = None
    max_chars_per_evidence: int | None = None
    initial_output_tokens: int | None = None
    max_output_tokens: int | None = None
    max_llm_attempts: int | None = None
    llm_delay_seconds: float | None = None
    timeout_seconds: float | None = None
    reasoning_effort: str | None = None
    service_tier: str | None = None
    temperature: float | None = None
    seed: int | None = None
    repository_map_cache_schema_version: int | None = None
    created_at: datetime
    results: list[BenchmarkCaseResult]
    overall: BenchmarkAggregate
    by_tier: dict[str, BenchmarkAggregate]


class _RepositoryMapCacheEntry(BaseModel):
    cache_schema_version: int
    index_schema_version: int
    python_identity: str
    repository: str
    pre_fix_sha: str
    tracked_files: list[str]
    materialized_files: list[str]
    repository_map: RepositoryMap


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

    try:
        _run_git(["cat-file", "-e", f"{case.pre_fix_sha}^{{commit}}"], cwd=target)
    except RuntimeError:
        _run_git(["fetch", "--depth=100", "origin", case.pre_fix_sha], cwd=target)
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


def tracked_repository_files(repository_root: Path) -> list[str]:
    output = _run_git(["ls-files", "-z"], cwd=repository_root)
    return [value for value in output.split("\0") if value]


def _normalized_repository_files(
    repository_root: Path,
    included_files: Sequence[str],
) -> tuple[list[str], list[str]]:
    repository_root = repository_root.resolve()
    tracked_files = sorted(set(included_files))
    materialized_files: list[str] = []
    for value in tracked_files:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Repository file must be relative to the root: {value}")
        path = (repository_root / relative).resolve()
        if not path.is_relative_to(repository_root):
            raise ValueError(f"Repository file must stay within the root: {value}")
        if path.is_file():
            materialized_files.append(value)
    return tracked_files, materialized_files


def _repository_map_cache_path(cache_root: Path, case: BenchmarkCase) -> Path:
    repository = case.repository.replace("/", "--")
    return (
        cache_root
        / repository
        / case.pre_fix_sha
        / f"index-v{REPOSITORY_MAP_INDEX_VERSION}"
        / "map.json"
    )


def _load_or_build_repository_map(
    case: BenchmarkCase,
    repository_root: Path,
    included_files: Sequence[str],
    cache_root: Path,
) -> tuple[RepositoryMap, bool]:
    repository_root = repository_root.resolve()
    tracked_files, materialized_files = _normalized_repository_files(
        repository_root,
        included_files,
    )
    indexed_files = repository_map_input_files(repository_root, tracked_files)
    python_identity = (
        f"{sys.implementation.name}-"
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}."
        f"{sys.version_info.releaselevel}.{sys.version_info.serial}-"
        f"{sys.implementation.cache_tag or 'no-cache-tag'}"
    )
    cache_path = _repository_map_cache_path(cache_root.resolve(), case)
    try:
        cached = _RepositoryMapCacheEntry.model_validate_json(
            cache_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError):
        cached = None

    if (
        cached is not None
        and cached.cache_schema_version == REPOSITORY_MAP_CACHE_SCHEMA_VERSION
        and cached.index_schema_version == REPOSITORY_MAP_INDEX_VERSION
        and cached.python_identity == python_identity
        and cached.repository == case.repository
        and cached.pre_fix_sha == case.pre_fix_sha
        and cached.tracked_files == tracked_files
        and cached.materialized_files == materialized_files
        and [record.path for record in cached.repository_map.files] == indexed_files
    ):
        return (
            cached.repository_map.model_copy(
                update={"root": str(repository_root)}
            ),
            True,
        )

    repository_map = build_repository_map(
        repository_root,
        included_files=tracked_files,
    )
    entry = _RepositoryMapCacheEntry(
        cache_schema_version=REPOSITORY_MAP_CACHE_SCHEMA_VERSION,
        index_schema_version=REPOSITORY_MAP_INDEX_VERSION,
        python_identity=python_identity,
        repository=case.repository,
        pre_fix_sha=case.pre_fix_sha,
        tracked_files=tracked_files,
        materialized_files=materialized_files,
        repository_map=repository_map,
    )
    temporary_path: Path | None = None
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=cache_path.parent,
            prefix=f".{cache_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(entry.model_dump(mode="json"), temporary, indent=2)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, cache_path)
    except OSError:
        pass
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
    return repository_map, False


def _unique_files(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _merged_hybrid_candidate_pool(
    base_candidates: Sequence[CandidateLocation],
    expanded_candidates: Sequence[CandidateLocation],
) -> list[CandidateLocation]:
    base_paths = {candidate.file for candidate in base_candidates}
    return [
        *base_candidates,
        *[
            candidate
            for candidate in expanded_candidates
            if candidate.file not in base_paths
        ][: max(0, HYBRID_CANDIDATE_POOL_LIMIT - len(base_candidates))],
    ]


def _hybrid_candidate_files(
    issue: IssueRecord,
    report,
    analyzer: EvidenceReranker,
    max_evidence_chars: int | None,
    max_lines_per_evidence: int | None,
    max_attempts: int,
    retry_delay_seconds: float,
) -> tuple[list[str], EvidenceRerankResult, int]:
    base_files = _unique_files(
        [candidate.file for candidate in report.candidates[:DEFAULT_CANDIDATE_LIMIT]]
    )
    max_chars_per_evidence = _max_chars_per_evidence(
        max_evidence_chars,
        HYBRID_CANDIDATE_POOL_LIMIT,
    )
    evidence = collect_evidence(
        report,
        max_total_chars=max_evidence_chars,
        max_lines_per_snippet=max_lines_per_evidence,
        context_lines=4,
        max_chars_per_snippet=max_chars_per_evidence,
    )
    if not evidence:
        error = LLMProviderError(
            "No repository evidence was available for hybrid reranking",
            category="no_evidence",
        )
        error.attempts = 0
        raise error
    last_error: LLMProviderError | None = None
    request_attempts = 0
    failed_input_tokens = 0
    failed_output_tokens = 0
    failed_elapsed_ms = 0.0
    for attempt in range(1, max_attempts + 1):
        try:
            analysis = analyzer.rerank(issue, evidence)
            request_attempts += analysis.attempts
            evidence_files = {snippet.id: snippet.file for snippet in evidence}
            reranked_ids = analysis.analysis.reranked_evidence_ids
            if not reranked_ids:
                error = LLMProviderError(
                    "Reranker returned no evidence IDs",
                    category="invalid_rank",
                )
                error.attempts = 0
                error.input_tokens = analysis.input_tokens
                error.output_tokens = analysis.output_tokens
                error.elapsed_ms = analysis.elapsed_ms
                error.request_id = analysis.request_id
                error.system_fingerprint = analysis.system_fingerprint
                raise error
            unknown_ids = set(reranked_ids) - evidence_files.keys()
            if unknown_ids:
                error = LLMProviderError(
                    "Reranker returned unknown evidence IDs: "
                    + ", ".join(sorted(unknown_ids)),
                    category="unknown_evidence_id",
                )
                error.attempts = 0
                error.input_tokens = analysis.input_tokens
                error.output_tokens = analysis.output_tokens
                error.elapsed_ms = analysis.elapsed_ms
                error.request_id = analysis.request_id
                error.system_fingerprint = analysis.system_fingerprint
                raise error
            reranked = _unique_files(
                [evidence_files[evidence_id] for evidence_id in reranked_ids]
            )[:MAX_RERANKED_EVIDENCE_IDS]
            remaining_base = [
                path for path in base_files if path not in set(reranked)
            ]
            remaining = [*reranked, *remaining_base]
            analysis = analysis.model_copy(
                update={
                    "input_tokens": analysis.input_tokens + failed_input_tokens,
                    "output_tokens": analysis.output_tokens + failed_output_tokens,
                    "elapsed_ms": round(analysis.elapsed_ms + failed_elapsed_ms, 3),
                }
            )
            return remaining[:DEFAULT_CANDIDATE_LIMIT], analysis, request_attempts
        except LLMProviderError as error:
            last_error = error
            request_attempts += error.attempts
            failed_input_tokens += error.input_tokens
            failed_output_tokens += error.output_tokens
            failed_elapsed_ms += error.elapsed_ms
            error.attempts = request_attempts
            error.input_tokens = failed_input_tokens
            error.output_tokens = failed_output_tokens
            error.elapsed_ms = round(failed_elapsed_ms, 3)
            if not error.retryable or attempt == max_attempts:
                break
            retry_after = error.retry_after
            delay = max(retry_delay_seconds, 1.0) * (2 ** (attempt - 1))
            if isinstance(retry_after, (int, float)) and retry_after > 0:
                delay = max(delay, float(retry_after))
            sleep(min(delay, 60.0))
    assert last_error is not None
    raise last_error


def evaluate_case(
    case: BenchmarkCase,
    issue: IssueRecord,
    repository_root: Path,
    variant: BenchmarkVariant,
    analyzer: EvidenceReranker | None = None,
    max_evidence_chars: int | None = DEFAULT_MAX_TOTAL_CHARS,
    max_lines_per_evidence: int | None = DEFAULT_MAX_LINES_PER_SNIPPET,
    max_llm_attempts: int = 2,
    llm_retry_delay_seconds: float = 0,
    included_files: Sequence[str] | None = None,
    repository_map_cache: Path | None = None,
) -> BenchmarkCaseResult:
    if issue != case.issue_snapshot:
        raise ValueError(f"Issue #{case.issue_number} does not match the frozen snapshot")
    if variant is not BenchmarkVariant.DETERMINISTIC and analyzer is None:
        raise ValueError("Hybrid benchmark requires an EvidenceReranker")

    started = perf_counter()
    repository_map_cache_hit = None
    if repository_map_cache is None:
        repository_map = build_repository_map(
            repository_root,
            included_files=included_files,
        )
    else:
        if included_files is None:
            raise ValueError("Repository-map caching requires tracked repository files")
        repository_map, repository_map_cache_hit = _load_or_build_repository_map(
            case,
            repository_root,
            included_files,
            repository_map_cache,
        )
    base_report = investigate(
        issue,
        repository_map,
        candidate_limit=DEFAULT_CANDIDATE_LIMIT,
    )
    if variant is BenchmarkVariant.HYBRID:
        expanded_report = investigate(
            issue,
            repository_map,
            candidate_limit=HYBRID_CANDIDATE_POOL_LIMIT,
        )
        report = base_report.model_copy(
            update={
                "candidates": _merged_hybrid_candidate_pool(
                    base_report.candidates,
                    expanded_report.candidates,
                )
            }
        )
    else:
        report = base_report
    candidate_locations = {candidate.file: candidate for candidate in report.candidates}
    candidate_pool_files = _unique_files(
        [candidate.file for candidate in report.candidates]
    )
    candidate_files = _unique_files(
        [candidate.file for candidate in report.candidates[:DEFAULT_CANDIDATE_LIMIT]]
    )
    llm_result = None
    llm_failure = None
    llm_attempts = 0
    fallback_used = False
    fallback_reason = None
    error = None
    if variant is not BenchmarkVariant.DETERMINISTIC:
        try:
            candidate_files, llm_result, llm_attempts = _hybrid_candidate_files(
                issue,
                report,
                analyzer,
                max_evidence_chars,
                max_lines_per_evidence,
                max_llm_attempts,
                llm_retry_delay_seconds,
            )
        except LLMProviderError as llm_error:
            llm_failure = llm_error
            fallback_used = True
            fallback_reason = llm_error.category
            llm_attempts = llm_error.attempts
            error = f"{type(llm_error).__name__}: {llm_error}"

    expected = set(case.expected_files)
    top_1 = candidate_files[:1]
    top_5 = candidate_files[:5]
    top_10 = candidate_files[:10]
    top_20 = candidate_files[:20]
    matched_at_5 = sorted(expected.intersection(top_5))
    matched_at_10 = sorted(expected.intersection(top_10))
    matched_at_20 = sorted(expected.intersection(top_20))
    first_rank = next(
        (index for index, path in enumerate(candidate_files, start=1) if path in expected),
        None,
    )
    candidate_symbols: list[BenchmarkSymbolCandidate] = []
    for file_rank, file in enumerate(candidate_files, start=1):
        location = candidate_locations.get(file)
        if location is None:
            continue
        ranked_locations: list[tuple[str, str | None]] = []
        if location.symbol is not None:
            ranked_locations.append(
                (location.symbol, location.qualified_symbol)
            )
        ranked_locations.extend(
            (alternate.symbol, alternate.qualified_symbol)
            for alternate in location.alternate_symbols
        )
        candidate_symbols.extend(
            BenchmarkSymbolCandidate(
                file=file,
                symbol=symbol,
                qualified_symbol=qualified_symbol,
                rank=file_rank,
                within_file_rank=within_file_rank,
            )
            for within_file_rank, (symbol, qualified_symbol) in enumerate(
                ranked_locations,
                start=1,
            )
        )
    expected_symbol_keys = {
        (target.file, target.symbol) for target in case.expected_symbols
    }
    symbol_ranks: dict[tuple[str, str], int] = {}
    within_file_symbol_ranks: dict[tuple[str, str], int] = {}
    for target in case.expected_symbols:
        matching_candidates = [
            candidate
            for candidate in candidate_symbols
            if candidate.file == target.file
            and target.symbol
            in {candidate.symbol, candidate.qualified_symbol}
        ]
        if matching_candidates:
            key = (target.file, target.symbol)
            symbol_ranks[key] = min(
                candidate.rank for candidate in matching_candidates
            )
            within_file_symbol_ranks[key] = min(
                candidate.within_file_rank for candidate in matching_candidates
            )
    first_symbol_rank = min(
        (
            symbol_ranks[key]
            for key in expected_symbol_keys
            if key in symbol_ranks
        ),
        default=None,
    )

    def symbol_recall_at(limit: int) -> float | None:
        if not expected_symbol_keys:
            return None
        matched = sum(
            symbol_ranks.get(key, limit + 1) <= limit
            for key in expected_symbol_keys
        )
        return round(matched / len(expected_symbol_keys), 4)

    candidate_file_set = set(candidate_files)
    file_conditioned_symbol_keys = {
        key for key in expected_symbol_keys if key[0] in candidate_file_set
    }

    def file_conditioned_symbol_recall_at(limit: int) -> float | None:
        if not file_conditioned_symbol_keys:
            return None
        matched = sum(
            within_file_symbol_ranks.get(key, limit + 1) <= limit
            for key in file_conditioned_symbol_keys
        )
        return round(matched / len(file_conditioned_symbol_keys), 4)

    first_within_file_symbol_rank = min(
        (
            within_file_symbol_ranks[key]
            for key in file_conditioned_symbol_keys
            if key in within_file_symbol_ranks
        ),
        default=None,
    )
    missing_file_conditioned_symbols = [
        target
        for target in case.expected_symbols
        if target.file in candidate_file_set
        and (target.file, target.symbol) not in within_file_symbol_ranks
    ]

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
        expected_symbols=case.expected_symbols,
        candidate_files=candidate_files,
        candidate_symbols=candidate_symbols,
        matched_files_at_5=matched_at_5,
        matched_files_at_10=matched_at_10,
        matched_files_at_20=matched_at_20,
        file_recall_at_1=round(len(expected.intersection(top_1)) / len(expected), 4),
        file_recall_at_5=round(len(matched_at_5) / len(expected), 4),
        file_recall_at_10=round(len(matched_at_10) / len(expected), 4),
        file_recall_at_20=round(len(matched_at_20) / len(expected), 4),
        candidate_pool_recall=round(
            len(expected.intersection(candidate_pool_files)) / len(expected),
            4,
        ),
        reciprocal_rank=round(1 / first_rank, 4) if first_rank else 0,
        symbol_recall_at_1=symbol_recall_at(1),
        symbol_recall_at_5=symbol_recall_at(5),
        symbol_recall_at_10=symbol_recall_at(10),
        symbol_recall_at_20=symbol_recall_at(20),
        symbol_reciprocal_rank=(
            round(1 / first_symbol_rank, 4)
            if first_symbol_rank is not None
            else 0
            if expected_symbol_keys
            else None
        ),
        file_conditioned_symbol_targets=len(file_conditioned_symbol_keys),
        file_conditioned_symbol_recall_at_1=file_conditioned_symbol_recall_at(1),
        file_conditioned_symbol_recall_at_3=file_conditioned_symbol_recall_at(3),
        within_file_symbol_reciprocal_rank=(
            round(1 / first_within_file_symbol_rank, 4)
            if first_within_file_symbol_rank is not None
            else 0
            if file_conditioned_symbol_keys
            else None
        ),
        expected_file_found_but_symbol_missing=missing_file_conditioned_symbols,
        analysis_elapsed_ms=round((perf_counter() - started) * 1000, 3),
        repository_map_cache_hit=repository_map_cache_hit,
        llm_attempts=llm_attempts,
        llm_fallback_used=fallback_used,
        llm_fallback_reason=fallback_reason,
        llm_request_id=(
            llm_result.request_id
            if llm_result
            else llm_failure.request_id
            if llm_failure
            else None
        ),
        llm_system_fingerprint=(
            llm_result.system_fingerprint
            if llm_result
            else llm_failure.system_fingerprint
            if llm_failure
            else None
        ),
        llm_input_tokens=(
            llm_result.input_tokens
            if llm_result
            else llm_failure.input_tokens
            if llm_failure
            else 0
        ),
        llm_output_tokens=(
            llm_result.output_tokens
            if llm_result
            else llm_failure.output_tokens
            if llm_failure
            else 0
        ),
        llm_elapsed_ms=(
            llm_result.elapsed_ms
            if llm_result
            else llm_failure.elapsed_ms
            if llm_failure
            else 0
        ),
        error=error,
    )


def _aggregate(results: Sequence[BenchmarkCaseResult]) -> BenchmarkAggregate:
    completed = [result for result in results if result.execution_succeeded]
    symbol_results = [result for result in completed if result.expected_symbols]
    file_conditioned_symbol_results = [
        result
        for result in symbol_results
        if result.file_conditioned_symbol_targets > 0
    ]
    llm_results = [
        result
        for result in completed
        if result.llm_attempts or result.llm_fallback_used
    ]
    successful_llm_results = [
        result for result in llm_results if not result.llm_fallback_used
    ]
    fallback_reasons = {
        reason: sum(result.llm_fallback_reason == reason for result in llm_results)
        for reason in sorted(
            {
                result.llm_fallback_reason
                for result in llm_results
                if result.llm_fallback_reason is not None
            }
        )
    }
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
        file_recall_at_10=round(fmean(r.file_recall_at_10 for r in completed), 4)
        if completed
        else 0,
        file_recall_at_20=round(fmean(r.file_recall_at_20 for r in completed), 4)
        if completed
        else 0,
        candidate_pool_recall=round(
            fmean(r.candidate_pool_recall for r in completed),
            4,
        )
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
        llm_fallback_reasons=fallback_reasons,
        llm_success_mean_reciprocal_rank=round(
            fmean(result.reciprocal_rank for result in successful_llm_results),
            4,
        )
        if successful_llm_results
        else None,
        average_llm_elapsed_ms=round(
            fmean(result.llm_elapsed_ms for result in llm_results), 3
        )
        if llm_results
        else None,
        average_llm_success_elapsed_ms=round(
            fmean(result.llm_elapsed_ms for result in successful_llm_results), 3
        )
        if successful_llm_results
        else None,
        llm_input_tokens=sum(result.llm_input_tokens for result in completed),
        llm_output_tokens=sum(result.llm_output_tokens for result in completed),
        symbol_cases=len(symbol_results),
        symbol_recall_at_1=round(
            fmean(
                result.symbol_recall_at_1
                for result in symbol_results
                if result.symbol_recall_at_1 is not None
            ),
            4,
        )
        if symbol_results
        else None,
        symbol_recall_at_5=round(
            fmean(
                result.symbol_recall_at_5
                for result in symbol_results
                if result.symbol_recall_at_5 is not None
            ),
            4,
        )
        if symbol_results
        else None,
        symbol_recall_at_10=round(
            fmean(
                result.symbol_recall_at_10
                for result in symbol_results
                if result.symbol_recall_at_10 is not None
            ),
            4,
        )
        if symbol_results
        else None,
        symbol_recall_at_20=round(
            fmean(
                result.symbol_recall_at_20
                for result in symbol_results
                if result.symbol_recall_at_20 is not None
            ),
            4,
        )
        if symbol_results
        else None,
        mean_symbol_reciprocal_rank=round(
            fmean(
                result.symbol_reciprocal_rank
                for result in symbol_results
                if result.symbol_reciprocal_rank is not None
            ),
            4,
        )
        if symbol_results
        else None,
        file_conditioned_symbol_cases=len(file_conditioned_symbol_results),
        file_conditioned_symbol_targets=sum(
            result.file_conditioned_symbol_targets
            for result in file_conditioned_symbol_results
        ),
        file_conditioned_symbol_recall_at_1=round(
            fmean(
                result.file_conditioned_symbol_recall_at_1
                for result in file_conditioned_symbol_results
                if result.file_conditioned_symbol_recall_at_1 is not None
            ),
            4,
        )
        if file_conditioned_symbol_results
        else None,
        file_conditioned_symbol_recall_at_3=round(
            fmean(
                result.file_conditioned_symbol_recall_at_3
                for result in file_conditioned_symbol_results
                if result.file_conditioned_symbol_recall_at_3 is not None
            ),
            4,
        )
        if file_conditioned_symbol_results
        else None,
        mean_within_file_symbol_reciprocal_rank=round(
            fmean(
                result.within_file_symbol_reciprocal_rank
                for result in file_conditioned_symbol_results
                if result.within_file_symbol_reciprocal_rank is not None
            ),
            4,
        )
        if file_conditioned_symbol_results
        else None,
        expected_file_found_but_symbol_missing_cases=sum(
            bool(result.expected_file_found_but_symbol_missing)
            for result in file_conditioned_symbol_results
        ),
        expected_file_found_but_symbol_missing_targets=sum(
            len(result.expected_file_found_but_symbol_missing)
            for result in file_conditioned_symbol_results
        ),
    )


def run_benchmark(
    manifest: BenchmarkManifest,
    workspace: Path,
    variant: BenchmarkVariant,
    analyzer: EvidenceReranker | None = None,
    case_ids: set[str] | None = None,
    max_evidence_chars: int | None = DEFAULT_MAX_TOTAL_CHARS,
    max_lines_per_evidence: int | None = DEFAULT_MAX_LINES_PER_SNIPPET,
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

    workspace = workspace.expanduser().resolve()
    repository_map_cache = workspace / REPOSITORY_MAP_CACHE_DIRECTORY
    results: list[BenchmarkCaseResult] = []
    for case in selected:
        try:
            repository_root = prepare_repository(case, workspace)
            result = evaluate_case(
                case,
                case.issue_snapshot.model_copy(deep=True),
                repository_root,
                variant,
                analyzer,
                max_evidence_chars=max_evidence_chars,
                max_lines_per_evidence=max_lines_per_evidence,
                max_llm_attempts=max_llm_attempts,
                llm_retry_delay_seconds=llm_delay_seconds,
                included_files=tracked_repository_files(repository_root),
                repository_map_cache=repository_map_cache,
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
                expected_symbols=case.expected_symbols,
                error=f"{type(error).__name__}: {error}",
                execution_succeeded=False,
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
        provider=getattr(analyzer, "provider", None),
        model=analyzer.model if analyzer else None,
        max_evidence_chars=max_evidence_chars if analyzer else None,
        max_lines_per_evidence=max_lines_per_evidence if analyzer else None,
        candidate_pool_limit=HYBRID_CANDIDATE_POOL_LIMIT if analyzer else None,
        max_chars_per_evidence=(
            _max_chars_per_evidence(
                max_evidence_chars,
                HYBRID_CANDIDATE_POOL_LIMIT,
            )
            if analyzer
            else None
        ),
        initial_output_tokens=getattr(
            analyzer,
            "rerank_initial_output_tokens",
            None,
        ),
        max_output_tokens=getattr(
            analyzer,
            "rerank_max_output_tokens",
            getattr(analyzer, "max_output_tokens", None),
        ),
        max_llm_attempts=max_llm_attempts if analyzer else None,
        llm_delay_seconds=llm_delay_seconds if analyzer else None,
        timeout_seconds=getattr(analyzer, "timeout_seconds", None),
        reasoning_effort=getattr(
            analyzer,
            "rerank_reasoning_effort",
            getattr(analyzer, "reasoning_effort", None),
        ),
        service_tier=getattr(analyzer, "service_tier", None),
        temperature=getattr(analyzer, "temperature", None),
        seed=getattr(analyzer, "seed", None),
        repository_map_cache_schema_version=REPOSITORY_MAP_CACHE_SCHEMA_VERSION,
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
