import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from repo_issue_intelligence.agent_evaluation import (
    AgentAnalysisRun,
    _aggregate,
    _quality_metrics,
    run_agent_analysis_evaluation,
    save_agent_analysis_run,
)
from repo_issue_intelligence.benchmark import (
    BenchmarkCase,
    BenchmarkManifest,
    BenchmarkTier,
    load_manifest,
)
from repo_issue_intelligence.llm_client import LLMProviderError
from repo_issue_intelligence.models import (
    CandidateLocation,
    Hypothesis,
    InvestigationReport,
    IssueRecord,
    LLMAnalysis,
    LLMAnalysisResult,
    ReproductionPlan,
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
    model = "deepseek-v4-flash"
    temperature = 0.1
    seed = 1337
    max_output_tokens = 20_000

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
    model = "deepseek-v4-flash"
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
    assert result.expected_files == ["data_store.py"]
    assert result.evidence_files == ["data_store.py"]
    assert result.expected_files_in_evidence == ["data_store.py"]
    assert result.hypothesis_cited_files == ["data_store.py"]
    assert result.expected_files_cited_by_hypothesis == ["data_store.py"]
    assert result.expected_file_evidence_recall == 1
    assert result.hypothesis_expected_file_recall == 1
    assert result.hypothesis_expected_file_hit is True
    assert run.overall.analysis_success_rate == 1
    assert run.overall.first_attempt_success_rate == 1
    assert run.overall.persistence_verified == 1
    assert run.max_output_tokens == 20_000
    assert run.max_evidence_chars == 100_000
    assert run.max_lines_per_evidence == 200
    assert run.overall.evidence_quality_cases == 1
    assert run.overall.expected_file_evidence_hit_rate == 1
    assert run.overall.mean_expected_file_evidence_recall == 1
    assert run.overall.hypothesis_quality_cases == 1
    assert run.overall.overall_hypothesis_expected_file_hit_rate == 1
    assert run.overall.hypothesis_expected_file_hit_rate == 1
    assert run.overall.mean_hypothesis_expected_file_recall == 1
    assert run.overall.hypothesis_hit_rate_when_expected_evidence_available == 1

    missed = result.model_copy(
        update={
            "hypothesis_cited_files": ["other.py"],
            "expected_files_cited_by_hypothesis": [],
            "hypothesis_expected_file_recall": 0,
            "hypothesis_expected_file_hit": False,
        }
    )
    aggregate = _aggregate([result, missed])
    assert aggregate.hypothesis_expected_file_hit_rate == 0.5
    assert aggregate.mean_hypothesis_expected_file_recall == 0.5
    assert aggregate.hypothesis_hit_rate_when_expected_evidence_available == 0.5

    unpersisted = result.model_copy(
        update={"analysis_succeeded": False, "persistence_verified": False}
    )
    failed_aggregate = _aggregate([unpersisted])
    assert failed_aggregate.hypothesis_quality_cases == 0
    assert failed_aggregate.hypothesis_expected_file_hits == 0
    assert failed_aggregate.overall_hypothesis_expected_file_hit_rate == 0

    output = tmp_path / "result.json"
    save_agent_analysis_run(run, output)
    assert AgentAnalysisRun.model_validate_json(output.read_text(encoding="utf-8")) == run

    historical_payload = run.model_dump(mode="json")
    historical_payload.pop("max_output_tokens")
    historical_payload["overall"].pop(
        "overall_hypothesis_expected_file_hit_rate"
    )
    historical_payload["by_tier"]["main"].pop(
        "overall_hypothesis_expected_file_hit_rate"
    )
    historical = AgentAnalysisRun.model_validate(historical_payload)
    assert historical.max_output_tokens is None
    assert historical.overall.overall_hypothesis_expected_file_hit_rate is None


def test_quality_metrics_separates_retrieval_from_hypothesis_citation(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "decoy.py").write_text("def unrelated():\n    pass\n", encoding="utf-8")
    (repository / "data_store.py").write_text(
        "def persist_data():\n    pass\n",
        encoding="utf-8",
    )
    case = _manifest().cases[0]
    report = InvestigationReport(
        issue=case.issue_snapshot,
        confirmed_facts=[],
        candidates=[
            CandidateLocation(
                file="decoy.py",
                symbol="unrelated",
                lines="1-2",
                confidence=0.8,
                evidence=["lexical match"],
            ),
            CandidateLocation(
                file="data_store.py",
                symbol="persist_data",
                lines="1-2",
                confidence=0.7,
                evidence=["symbol match"],
            ),
        ],
        hypotheses=[
            Hypothesis(
                id="H1",
                description="The persistence path may lose data.",
                confidence=0.7,
                supporting_evidence=["symbol match"],
            )
        ],
        reproduction_plan=ReproductionPlan(
            runtime="Python",
            setup_commands=[],
            reproduction_steps=[],
            safety_constraints=[],
            open_questions=[],
        ),
        repository_root=repository,
    )
    analysis = LLMAnalysis(
        summary="The decoy is selected.",
        issue_type="bug",
        affected_component="decoy.py::unrelated",
        reproduction_completeness="partial",
        evidence_observations=[],
        contradictions=[],
        reranked_evidence_ids=["E1", "E2"],
        hypotheses=[
            {
                "description": "The decoy may be responsible.",
                "confidence": 0.6,
                "evidence_ids": ["E1"],
                "missing_evidence": [],
                "validation_step": "Inspect the decoy.",
            }
        ],
        needs_more_evidence=False,
    )

    metrics = _quality_metrics(case, report, analysis, None, None)

    assert metrics["evidence_files"] == ["decoy.py", "data_store.py"]
    assert metrics["expected_files_in_evidence"] == ["data_store.py"]
    assert metrics["expected_file_evidence_recall"] == 1
    assert metrics["hypothesis_cited_files"] == ["decoy.py"]
    assert metrics["expected_files_cited_by_hypothesis"] == []
    assert metrics["hypothesis_expected_file_recall"] == 0
    assert metrics["hypothesis_expected_file_hit"] is False


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
    assert run.overall.overall_hypothesis_expected_file_hit_rate == 0
    assert run.overall.persistence_verified == 1
    assert run.overall.error_categories == {"invalid_response": 1}


def test_hypothesis_quality_selection_is_balanced_and_matches_source() -> None:
    manifest = load_manifest(Path("benchmarks/hypothesis-quality-v1-24-cases.json"))
    review = json.loads(
        Path("benchmarks/hypothesis-quality-v1-review.json").read_text(
            encoding="utf-8"
        )
    )
    review_by_id = {entry["case_id"]: entry for entry in review["cases"]}

    assert len(manifest.cases) == 24
    assert len({case.repository for case in manifest.cases}) == 24
    assert all(case.tier is BenchmarkTier.GENERALIZATION for case in manifest.cases)
    assert review["independent_retrieval_held_out"] is False
    assert review["manual_review_status"] == "scored"
    assert Counter(entry["stratum"] for entry in review["cases"]) == {
        "non_python": 8,
        "python_multi": 8,
        "python_single": 8,
    }
    assert set(review_by_id) == {case.id for case in manifest.cases}
    for case in manifest.cases:
        entry = review_by_id[case.id]
        assert entry["repository"] == case.repository
        assert entry["expected_files"] == case.expected_files
        assert entry["expected_symbols"] == [
            target.model_dump(mode="json") for target in case.expected_symbols
        ]
        assert entry["manual_review"]["status"] == "scored"
        assert all(
            0 <= entry["manual_review"][field] <= 2
            for field in (
                "hypothesis_correctness",
                "evidence_sufficiency",
                "missing_evidence_quality",
            )
        )
        if entry["stratum"] == "non_python":
            assert any(not path.endswith(".py") for path in case.expected_files)
        elif entry["stratum"] == "python_multi":
            assert len(case.expected_files) > 1
            assert all(path.endswith(".py") for path in case.expected_files)
        else:
            assert len(case.expected_files) == 1
            assert case.expected_files[0].endswith(".py")

    correctness = [
        entry["manual_review"]["hypothesis_correctness"]
        for entry in review["cases"]
    ]
    assert review["manual_score_summary"]["fully_correct_hypotheses"] == sum(
        score == 2 for score in correctness
    )
    assert review["manual_score_summary"]["contradicted_hypotheses"] == sum(
        score == 0 for score in correctness
    )
