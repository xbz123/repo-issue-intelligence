from datetime import UTC, datetime
from pathlib import Path

from repo_issue_intelligence.agent_evaluation import (
    AgentAnalysisRun,
    run_agent_analysis_evaluation,
    save_agent_analysis_run,
)
from repo_issue_intelligence.benchmark import (
    BenchmarkCase,
    BenchmarkManifest,
    BenchmarkTier,
)
from repo_issue_intelligence.llm_client import LLMProviderError
from repo_issue_intelligence.models import (
    IssueRecord,
    LLMAnalysis,
    LLMAnalysisResult,
)


def _manifest() -> BenchmarkManifest:
    timestamp = datetime(2026, 7, 30, tzinfo=UTC)
    issue = IssueRecord(
        number=42,
        title="Persistence loses user data",
        body="persist_data loses data after the request completes.",
        labels=["bug"],
        created_at=timestamp,
        updated_at=timestamp,
    )
    return BenchmarkManifest(
        name="agent-analysis-test",
        version=1,
        cases=[
            BenchmarkCase(
                id="persistence-data-loss",
                tier=BenchmarkTier.MAIN,
                repository="example/project",
                issue_number=42,
                issue_updated_at=timestamp,
                issue_snapshot=issue,
                fix_pr_number=43,
                pre_fix_sha="a" * 40,
                expected_files=["data_store.py"],
            )
        ],
    )


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "data_store.py").write_text(
        'def persist_data():\n    """Persist user data."""\n',
        encoding="utf-8",
    )
    return repository


class ValidAnalyzer:
    provider = "opencode"
    model = "deepseek-v4-flash-free"
    temperature = 0.1
    seed = 1337

    def analyze(self, issue, report, evidence):
        evidence_ids = [snippet.id for snippet in evidence]
        return LLMAnalysisResult(
            provider=self.provider,
            model=self.model,
            request_id="request-valid",
            input_tokens=120,
            output_tokens=60,
            elapsed_ms=8.5,
            analysis=LLMAnalysis(
                summary="The persistence path is relevant.",
                issue_type="bug",
                affected_component="data_store",
                reproduction_completeness="partial",
                evidence_observations=[
                    {
                        "evidence_id": evidence_id,
                        "alignment": "supports_issue",
                        "observation": "The source contains the persistence path.",
                    }
                    for evidence_id in evidence_ids
                ],
                contradictions=[],
                reranked_evidence_ids=evidence_ids,
                hypotheses=[
                    {
                        "description": "The persistence path may lose data.",
                        "confidence": 0.7,
                        "evidence_ids": [evidence_ids[0]],
                        "missing_evidence": ["Runtime trace"],
                        "validation_step": "Run the existing persistence test and inspect state.",
                    }
                ],
                needs_more_evidence=True,
            ),
        )

    def close(self):
        return None


class InvalidAnalyzer:
    provider = "opencode"
    model = "deepseek-v4-flash-free"
    temperature = 0.1
    seed = 1337

    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, issue, report, evidence):
        self.calls += 1
        raise LLMProviderError(
            "OpenCode returned invalid JSON",
            category="invalid_response",
            input_tokens=100 + self.calls,
            output_tokens=50 + self.calls,
            elapsed_ms=10 + self.calls,
            request_id=f"request-invalid-{self.calls}",
        )

    def close(self):
        return None


def _patch_repository(monkeypatch, repository: Path) -> None:
    monkeypatch.setattr(
        "repo_issue_intelligence.agent_evaluation.prepare_repository",
        lambda case, workspace: repository,
    )
    monkeypatch.setattr(
        "repo_issue_intelligence.agent_evaluation.tracked_repository_files",
        lambda root: ["data_store.py"],
    )


def test_agent_analysis_evaluation_records_contract_and_persistence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_repository(monkeypatch, _repository(tmp_path))

    run = run_agent_analysis_evaluation(
        _manifest(),
        tmp_path / "workspace",
        ValidAnalyzer(),
    )
    result = run.results[0]

    assert result.analysis_succeeded is True
    assert result.persistence_verified is True
    assert result.agent_status == "awaiting_review"
    assert result.llm_attempts == 1
    assert result.request_ids == ["request-valid"]
    assert result.evidence_observations > 0
    assert result.analysis is not None
    assert run.overall.analysis_success_rate == 1
    assert run.overall.first_attempt_success_rate == 1
    assert run.overall.persistence_verified == 1

    output = tmp_path / "result.json"
    save_agent_analysis_run(run, output)
    assert AgentAnalysisRun.model_validate_json(output.read_text(encoding="utf-8")) == run


def test_agent_analysis_evaluation_records_non_retryable_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_repository(monkeypatch, _repository(tmp_path))

    run = run_agent_analysis_evaluation(
        _manifest(),
        tmp_path / "workspace",
        InvalidAnalyzer(),
        max_llm_attempts=2,
    )
    result = run.results[0]

    assert result.analysis_succeeded is False
    assert result.agent_status == "failed"
    assert result.persistence_verified is True
    assert result.llm_attempts == 1
    assert result.request_ids == ["request-invalid-1"]
    assert result.input_tokens == 101
    assert result.output_tokens == 51
    assert result.llm_elapsed_ms == 11
    assert result.error_category == "invalid_response"
    assert result.error == "LLMProviderError: OpenCode returned invalid JSON"
    assert run.overall.failures == 1
    assert run.overall.persistence_verified == 1
    assert run.overall.error_categories == {"invalid_response": 1}
