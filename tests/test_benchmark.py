from datetime import UTC, datetime
from pathlib import Path

from repo_issue_intelligence.benchmark import (
    BenchmarkCase,
    BenchmarkManifest,
    BenchmarkTier,
    BenchmarkVariant,
    evaluate_case,
    load_manifest,
    run_benchmark,
)
from repo_issue_intelligence.models import (
    EvidenceRerankAnalysis,
    EvidenceRerankResult,
    IssueRecord,
    LLMAnalysis,
    LLMAnalysisResult,
)


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
    model = "openai/gpt-oss-20b"
    reasoning_effort = "low"
    temperature = 0.1
    seed = 1337

    def rerank(self, issue, evidence):
        reversed_ids = [snippet.id for snippet in reversed(evidence)]
        return EvidenceRerankResult(
            provider="groq",
            model=self.model,
            request_id="benchmark-request",
            system_fingerprint="benchmark-fingerprint",
            input_tokens=200,
            output_tokens=100,
            elapsed_ms=10,
            analysis=EvidenceRerankAnalysis(
                summary="The service evidence is more specific.",
                reranked_evidence_ids=reversed_ids,
            ),
        )

    def analyze(self, issue, report, evidence):
        reversed_ids = [snippet.id for snippet in reversed(evidence)]
        return LLMAnalysisResult(
            provider="groq",
            model=self.model,
            request_id="full-benchmark-request",
            system_fingerprint="full-benchmark-fingerprint",
            input_tokens=300,
            output_tokens=150,
            elapsed_ms=15,
            analysis=LLMAnalysis(
                summary="The service evidence is more specific.",
                issue_type="bug",
                affected_component="token",
                reproduction_completeness="partial",
                evidence_observations=[
                    {
                        "evidence_id": snippet.id,
                        "alignment": "supports_issue",
                        "observation": f"{snippet.file} contains token handling.",
                    }
                    for snippet in evidence
                ],
                contradictions=[],
                reranked_evidence_ids=reversed_ids,
                hypotheses=[
                    {
                        "description": "Token validation may fail.",
                        "confidence": 0.7,
                        "evidence_ids": [reversed_ids[0]],
                        "missing_evidence": ["Failing test"],
                        "validation_step": "Inspect the existing token tests.",
                    }
                ],
                needs_more_evidence=True,
            ),
        )

    def close(self) -> None:
        return None


def test_real_benchmark_manifest_has_expected_project_tiers() -> None:
    manifest = load_manifest(Path("benchmarks/cases.json"))

    assert len(manifest.cases) == 9
    assert sum(case.tier is BenchmarkTier.MAIN for case in manifest.cases) == 4
    assert sum(case.tier is BenchmarkTier.CALIBRATION for case in manifest.cases) == 2
    assert sum(case.tier is BenchmarkTier.GENERALIZATION for case in manifest.cases) == 3


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
    assert result.reciprocal_rank > 0


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


def test_evaluate_case_supports_full_schema_hybrid(tmp_path: Path) -> None:
    updated_at = datetime(2026, 7, 30, tzinfo=UTC)
    result = evaluate_case(
        benchmark_case(updated_at),
        benchmark_issue(updated_at),
        create_repository(tmp_path),
        BenchmarkVariant.HYBRID_FULL,
        analyzer=ReverseEvidenceAnalyzer(),
    )

    assert result.candidate_files[0] == "src/token_service.py"
    assert result.llm_request_id == "full-benchmark-request"
    assert result.llm_input_tokens == 300
    assert result.llm_fallback_used is False


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
            github_client=None,  # type: ignore[arg-type]
            case_ids={"misspelled-case"},
        )
    except ValueError as error:
        assert "Unknown benchmark case IDs: misspelled-case" in str(error)
    else:
        raise AssertionError("Expected an unknown case ID to fail")
