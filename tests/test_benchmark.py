import subprocess
from datetime import UTC, datetime
from pathlib import Path

from repo_issue_intelligence.benchmark import (
    BenchmarkCase,
    BenchmarkManifest,
    BenchmarkRun,
    BenchmarkSymbolTarget,
    BenchmarkTier,
    BenchmarkVariant,
    _aggregate,
    evaluate_case,
    load_manifest,
    prepare_repository,
    run_benchmark,
    tracked_repository_files,
)
from repo_issue_intelligence.llm_client import LLMProviderError
from repo_issue_intelligence.models import (
    EvidenceRerankAnalysis,
    EvidenceRerankResult,
    IssueRecord,
)
from repo_issue_intelligence.repository_index import build_repository_map


def benchmark_issue(updated_at: datetime) -> IssueRecord:
    return IssueRecord(
        number=42,
        title="Token processing failure",
        body="Token processing fails in the request path with steps to reproduce.",
        labels=["bug"],
        created_at=updated_at,
        updated_at=updated_at,
    )


def benchmark_case(updated_at: datetime) -> BenchmarkCase:
    return BenchmarkCase(
        id="example-token-service",
        tier=BenchmarkTier.MAIN,
        repository="example/project",
        issue_number=42,
        issue_updated_at=updated_at,
        issue_snapshot=benchmark_issue(updated_at),
        fix_pr_number=43,
        pre_fix_sha="a" * 40,
        expected_files=["src/token_service.py"],
    )


def create_repository(root: Path) -> Path:
    repository = root / "repository"
    source = repository / "src"
    source.mkdir(parents=True)
    (source / "token_router.py").write_text(
        "def handle_token():\n    return None\n",
        encoding="utf-8",
    )
    (source / "token_service.py").write_text(
        "def validate_token():\n    return None\n",
        encoding="utf-8",
    )
    return repository


class ReverseEvidenceAnalyzer:
    provider = "opencode"
    model = "deepseek-v4-flash"
    reasoning_effort = None
    temperature = 0.1
    seed = 1337
    timeout_seconds = 180.0
    rerank_initial_output_tokens = 8_192
    rerank_max_output_tokens = 20_000
    rerank_reasoning_effort = "none"

    def rerank(self, issue, evidence):
        reversed_ids = [snippet.id for snippet in reversed(evidence)]
        return EvidenceRerankResult(
            provider="opencode",
            model=self.model,
            request_id="benchmark-request",
            system_fingerprint="benchmark-fingerprint",
            input_tokens=200,
            output_tokens=100,
            elapsed_ms=10,
            analysis=EvidenceRerankAnalysis(
                reranked_evidence_ids=reversed_ids,
            ),
        )

    def close(self) -> None:
        return None


def test_real_benchmark_manifest_has_expected_project_tiers() -> None:
    manifest = load_manifest(Path("benchmarks/cases.json"))

    assert manifest.version == 12
    assert len(manifest.cases) == 90
    assert sum(case.tier is BenchmarkTier.MAIN for case in manifest.cases) == 17
    assert sum(case.tier is BenchmarkTier.CALIBRATION for case in manifest.cases) == 11
    assert sum(case.tier is BenchmarkTier.GENERALIZATION for case in manifest.cases) == 62
    assert len({case.repository for case in manifest.cases}) == 43
    assert all(case.issue_snapshot.number == case.issue_number for case in manifest.cases)
    assert all(case.issue_snapshot.updated_at == case.issue_updated_at for case in manifest.cases)
    assert all(case.issue_snapshot.title for case in manifest.cases)
    assert all(case.issue_snapshot.body for case in manifest.cases)
    assert sum(len(case.expected_files) for case in manifest.cases) == 135
    assert sum(len(case.expected_files) > 1 for case in manifest.cases) == 31
    assert sum(bool(case.expected_symbols) for case in manifest.cases) == 63
    assert sum(len(case.expected_symbols) for case in manifest.cases) == 87

    qualified_base = load_manifest(
        Path("benchmarks/cases-v0.12-qualified-symbols-32-cases.json")
    )
    assert qualified_base.version == 7
    assert len(qualified_base.cases) == 32

    expanded_base = load_manifest(Path("benchmarks/cases-v0.11-32-cases.json"))
    assert expanded_base.version == 6
    assert len(expanded_base.cases) == 32

    corrected_base = load_manifest(
        Path("benchmarks/cases-v0.10-corrected-20-cases.json")
    )
    assert corrected_base.version == 5
    assert len(corrected_base.cases) == 20

    historical = load_manifest(Path("benchmarks/cases-v0.3.json"))
    assert historical.version == 2
    assert len(historical.cases) == 9
    clean_file_manifest = load_manifest(Path("benchmarks/cases-v0.7-clean-pre-fix.json"))
    assert clean_file_manifest.version == 4
    assert all(not case.expected_symbols for case in clean_file_manifest.cases)


def test_evaluate_case_measures_deterministic_file_recall(tmp_path: Path) -> None:
    updated_at = datetime(2026, 7, 30, tzinfo=UTC)
    result = evaluate_case(
        benchmark_case(updated_at),
        benchmark_issue(updated_at),
        create_repository(tmp_path),
        BenchmarkVariant.DETERMINISTIC,
    )

    assert "src/token_service.py" in result.candidate_files
    assert result.file_recall_at_5 == 1
    assert result.file_recall_at_10 == 1
    assert result.file_recall_at_20 == 1
    assert result.candidate_pool_recall == 1
    assert result.reciprocal_rank > 0


def test_evaluate_case_measures_optional_symbol_recall(tmp_path: Path) -> None:
    updated_at = datetime(2026, 7, 30, tzinfo=UTC)
    case = benchmark_case(updated_at).model_copy(
        update={
            "expected_symbols": [
                BenchmarkSymbolTarget(
                    file="src/token_service.py",
                    symbol="validate_token",
                )
            ]
        }
    )

    result = evaluate_case(
        case,
        benchmark_issue(updated_at),
        create_repository(tmp_path),
        BenchmarkVariant.DETERMINISTIC,
    )
    aggregate = _aggregate([result])

    assert any(
        candidate.file == "src/token_service.py"
        and candidate.symbol == "validate_token"
        for candidate in result.candidate_symbols
    )
    assert result.symbol_recall_at_5 == 1
    assert result.symbol_recall_at_20 == 1
    assert result.symbol_reciprocal_rank is not None
    assert aggregate.symbol_cases == 1
    assert aggregate.symbol_recall_at_5 == 1
    assert aggregate.symbol_recall_at_20 == 1


def test_evaluate_case_matches_qualified_symbol_ground_truth(
    tmp_path: Path,
) -> None:
    updated_at = datetime(2026, 7, 30, tzinfo=UTC)
    record = IssueRecord(
        number=42,
        title="ThreadCache workers leak context",
        body="The `__init__` method on `WorkerThreads` retains the spawning context.",
        labels=["bug"],
        created_at=updated_at,
        updated_at=updated_at,
    )
    case = BenchmarkCase(
        id="qualified-worker-thread",
        tier=BenchmarkTier.GENERALIZATION,
        repository="example/project",
        issue_number=42,
        issue_updated_at=updated_at,
        issue_snapshot=record,
        fix_pr_number=43,
        pre_fix_sha="a" * 40,
        expected_files=["src/thread_cache.py"],
        expected_symbols=[
            BenchmarkSymbolTarget(
                file="src/thread_cache.py",
                symbol="WorkerThread.__init__",
            )
        ],
    )
    repository = tmp_path / "repository"
    source = repository / "src"
    source.mkdir(parents=True)
    (source / "thread_cache.py").write_text(
        "class Unrelated:\n"
        "    def __init__(self):\n"
        "        pass\n\n"
        "class WorkerThread:\n"
        "    def __init__(self):\n"
        "        self.context = None\n",
        encoding="utf-8",
    )

    result = evaluate_case(
        case,
        record,
        repository,
        BenchmarkVariant.DETERMINISTIC,
    )

    candidate = next(
        candidate
        for candidate in result.candidate_symbols
        if candidate.file == "src/thread_cache.py"
    )
    assert candidate.symbol == "__init__"
    assert candidate.qualified_symbol == "WorkerThread.__init__"
    assert result.symbol_recall_at_1 == 1


def test_benchmark_case_rejects_symbol_outside_expected_files() -> None:
    updated_at = datetime(2026, 7, 30, tzinfo=UTC)
    payload = benchmark_case(updated_at).model_dump()
    payload["expected_symbols"] = [
        BenchmarkSymbolTarget(
            file="src/other.py",
            symbol="other",
        )
    ]

    try:
        BenchmarkCase(**payload)
    except ValueError as error:
        assert "expected symbol files must also appear in expected_files" in str(error)
    else:
        raise AssertionError("Expected symbol ground truth outside expected_files to fail")


def test_benchmark_case_rejects_multiple_symbols_for_one_file() -> None:
    updated_at = datetime(2026, 7, 30, tzinfo=UTC)
    payload = benchmark_case(updated_at).model_dump()
    payload["expected_symbols"] = [
        {"file": "src/token_service.py", "symbol": "validate_token"},
        {"file": "src/token_service.py", "symbol": "refresh_token"},
    ]

    try:
        BenchmarkCase(**payload)
    except ValueError as error:
        assert "one target per file" in str(error)
    else:
        raise AssertionError("Expected multiple symbol targets for one file to fail")


def test_evaluate_case_applies_hybrid_evidence_reranking(tmp_path: Path) -> None:
    updated_at = datetime(2026, 7, 30, tzinfo=UTC)
    result = evaluate_case(
        benchmark_case(updated_at),
        benchmark_issue(updated_at),
        create_repository(tmp_path),
        BenchmarkVariant.HYBRID,
        analyzer=ReverseEvidenceAnalyzer(),
    )

    assert result.candidate_files[0] == "src/token_service.py"
    assert result.file_recall_at_1 == 1
    assert result.llm_attempts == 1
    assert result.llm_request_id == "benchmark-request"
    assert result.llm_system_fingerprint == "benchmark-fingerprint"
    assert result.llm_input_tokens == 200
    aggregate = _aggregate([result])
    assert aggregate.llm_success_rate == 1
    assert aggregate.llm_success_mean_reciprocal_rank == result.reciprocal_rank
    assert aggregate.llm_fallback_reasons == {}


def test_run_benchmark_rejects_unknown_case_id(tmp_path: Path) -> None:
    updated_at = datetime(2026, 7, 30, tzinfo=UTC)
    manifest = BenchmarkManifest(
        name="test",
        version=1,
        cases=[benchmark_case(updated_at)],
    )

    try:
        run_benchmark(
            manifest,
            tmp_path,
            BenchmarkVariant.DETERMINISTIC,
            case_ids={"misspelled-case"},
        )
    except ValueError as error:
        assert "Unknown benchmark case IDs: misspelled-case" in str(error)
    else:
        raise AssertionError("Expected an unknown case ID to fail")


def test_benchmark_run_records_provider(tmp_path: Path, monkeypatch) -> None:
    updated_at = datetime(2026, 7, 30, tzinfo=UTC)
    case = benchmark_case(updated_at)
    manifest = BenchmarkManifest(name="test", version=1, cases=[case])
    repository = create_repository(tmp_path)
    monkeypatch.setattr(
        "repo_issue_intelligence.benchmark.prepare_repository",
        lambda selected, workspace: repository,
    )
    monkeypatch.setattr(
        "repo_issue_intelligence.benchmark.tracked_repository_files",
        lambda root: ["src/token_router.py", "src/token_service.py"],
    )

    run = run_benchmark(
        manifest,
        tmp_path,
        BenchmarkVariant.HYBRID,
        analyzer=ReverseEvidenceAnalyzer(),
    )

    assert run.provider == "opencode"
    assert run.timeout_seconds == 180.0
    assert run.max_chars_per_evidence is None
    assert run.max_evidence_chars == 100_000
    assert run.max_lines_per_evidence == 200
    assert run.initial_output_tokens == 8_192
    assert run.max_output_tokens == 20_000
    assert run.reasoning_effort == "none"

    historical_payload = run.model_dump(mode="json")
    historical_payload["variant"] = "hybrid-full"
    assert BenchmarkRun.model_validate(historical_payload).variant == "hybrid-full"


def test_empty_candidate_case_counts_as_completed_zero_recall(tmp_path: Path) -> None:
    updated_at = datetime(2026, 7, 30, tzinfo=UTC)
    repository = tmp_path / "empty-repository"
    repository.mkdir()

    result = evaluate_case(
        benchmark_case(updated_at),
        benchmark_issue(updated_at),
        repository,
        BenchmarkVariant.DETERMINISTIC,
    )
    aggregate = _aggregate([result])

    assert result.execution_succeeded is True
    assert result.candidate_files == []
    assert aggregate.completed == 1
    assert aggregate.failed == 0
    assert aggregate.file_recall_at_5 == 0
    assert aggregate.mean_reciprocal_rank == 0
    assert aggregate.symbol_cases == 0
    assert aggregate.symbol_recall_at_5 is None
    assert aggregate.symbol_recall_at_20 is None


def test_hybrid_empty_evidence_counts_as_fallback_without_request(tmp_path: Path) -> None:
    updated_at = datetime(2026, 7, 30, tzinfo=UTC)
    repository = tmp_path / "empty-repository"
    repository.mkdir()

    result = evaluate_case(
        benchmark_case(updated_at),
        benchmark_issue(updated_at),
        repository,
        BenchmarkVariant.HYBRID,
        analyzer=ReverseEvidenceAnalyzer(),
    )
    aggregate = _aggregate([result])

    assert result.llm_attempts == 0
    assert result.llm_fallback_used is True
    assert result.llm_fallback_reason == "no_evidence"
    assert aggregate.llm_cases == 1
    assert aggregate.llm_success_rate == 0
    assert aggregate.llm_fallback_reasons == {"no_evidence": 1}


def test_tracked_repository_files_exclude_ignored_artifacts(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    source = repository / "src"
    ignored = repository / ".tox"
    source.mkdir(parents=True)
    ignored.mkdir()
    (repository / ".gitignore").write_text(".tox/\n", encoding="utf-8")
    (source / "target.py").write_text("def target():\n    pass\n", encoding="utf-8")
    (ignored / "noise.py").write_text("def noise():\n    pass\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "add", ".gitignore", "src/target.py"],
        check=True,
    )

    included_files = tracked_repository_files(repository)
    repository_map = build_repository_map(repository, included_files=included_files)

    assert included_files == [".gitignore", "src/target.py"]
    assert [record.path for record in repository_map.files] == ["src/target.py"]


def test_prepare_repository_skips_fetch_when_commit_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    updated_at = datetime(2026, 7, 30, tzinfo=UTC)
    case = benchmark_case(updated_at)
    workspace = tmp_path / "workspace"
    repository = workspace / "example--project"
    (repository / ".git").mkdir(parents=True)
    expected_file = repository / case.expected_files[0]
    expected_file.parent.mkdir(parents=True)
    expected_file.write_text("def validate_token():\n    return None\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run_git(arguments, cwd=None):
        calls.append(arguments)
        if arguments[:3] == ["remote", "get-url", "origin"]:
            return "https://github.com/example/project.git"
        if arguments[:2] == ["rev-parse", "HEAD"]:
            return case.pre_fix_sha
        return ""

    monkeypatch.setattr("repo_issue_intelligence.benchmark._run_git", fake_run_git)

    assert prepare_repository(case, workspace) == repository
    assert ["cat-file", "-e", f"{case.pre_fix_sha}^{{commit}}"] in calls
    assert not any(arguments[0] == "fetch" for arguments in calls)


class UnknownEvidenceAnalyzer(ReverseEvidenceAnalyzer):
    def rerank(self, issue, evidence):
        return EvidenceRerankResult(
            provider="custom",
            model=self.model,
            input_tokens=7,
            output_tokens=9,
            elapsed_ms=1,
            analysis=EvidenceRerankAnalysis(
                reranked_evidence_ids=["E999"],
            ),
        )


def test_hybrid_unknown_evidence_id_falls_back_without_retry(tmp_path: Path) -> None:
    updated_at = datetime(2026, 7, 30, tzinfo=UTC)

    result = evaluate_case(
        benchmark_case(updated_at),
        benchmark_issue(updated_at),
        create_repository(tmp_path),
        BenchmarkVariant.HYBRID,
        analyzer=UnknownEvidenceAnalyzer(),
    )

    assert result.execution_succeeded is True
    assert result.llm_attempts == 1
    assert result.llm_fallback_used is True
    assert result.llm_fallback_reason == "unknown_evidence_id"
    assert result.llm_input_tokens == 7
    assert result.llm_output_tokens == 9
    assert result.llm_elapsed_ms == 1
    assert result.candidate_files
    assert result.error == "LLMProviderError: Reranker returned unknown evidence IDs: E999"
    aggregate = _aggregate([result])
    assert aggregate.llm_success_rate == 0
    assert aggregate.llm_success_mean_reciprocal_rank is None
    assert aggregate.llm_fallback_reasons == {"unknown_evidence_id": 1}
    assert aggregate.average_llm_elapsed_ms == 1
    assert aggregate.average_llm_success_elapsed_ms is None
    assert aggregate.llm_input_tokens == 7
    assert aggregate.llm_output_tokens == 9


class TransientEvidenceAnalyzer(ReverseEvidenceAnalyzer):
    def __init__(self) -> None:
        self.calls = 0

    def rerank(self, issue, evidence):
        self.calls += 1
        if self.calls == 1:
            raise LLMProviderError(
                "OpenCode request timed out",
                retryable=True,
                input_tokens=10,
                output_tokens=20,
                elapsed_ms=30,
            )
        return super().rerank(issue, evidence)


def test_hybrid_retries_transient_errors_with_backoff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    updated_at = datetime(2026, 7, 30, tzinfo=UTC)
    delays: list[float] = []
    monkeypatch.setattr("repo_issue_intelligence.benchmark.sleep", delays.append)

    result = evaluate_case(
        benchmark_case(updated_at),
        benchmark_issue(updated_at),
        create_repository(tmp_path),
        BenchmarkVariant.HYBRID,
        analyzer=TransientEvidenceAnalyzer(),
    )

    assert result.llm_attempts == 2
    assert result.llm_fallback_used is False
    assert result.llm_input_tokens == 210
    assert result.llm_output_tokens == 120
    assert result.llm_elapsed_ms == 40
    assert delays == [1.0]
