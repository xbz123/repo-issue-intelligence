from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from repo_issue_intelligence.agent_store import AgentStore
from repo_issue_intelligence.agent_workflow import run_agent
from repo_issue_intelligence.investigator import investigate
from repo_issue_intelligence.llm_client import (
    LLMProviderError,
    OpenAICompatibleIssueAnalyzer,
)
from repo_issue_intelligence.models import (
    CandidateLocation,
    EvidenceSnippet,
    InvestigationReport,
    IssueRecord,
    LLMAnalysis,
    LLMAnalysisResponse,
    LLMAnalysisResult,
    ReproductionPlan,
)
from repo_issue_intelligence.repository_index import build_repository_map

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPOSITORY_ROOT / "tests" / "fixtures" / "protocol_v2"
DATABASE_PATH = FIXTURE_DIR / "legacy_agent.sqlite3"
BASELINE_PATH = FIXTURE_DIR / "t0_baseline.json"
FIXED_TIME = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _scenario(baseline: dict, scenario_id: str) -> dict:
    return next(item for item in baseline["scenarios"] if item["id"] == scenario_id)


def _copy_demo_repository(tmp_path: Path, *, with_decoy: bool = False) -> Path:
    repository = tmp_path / ("demo-with-decoy" if with_decoy else "demo-clean")
    shutil.copytree(REPOSITORY_ROOT / "examples" / "demo_repository", repository)
    if with_decoy:
        (repository / "untracked_noise.py").write_text(
            "def untracked_noise():\n    return 'decoy'\n",
            encoding="utf-8",
        )
    return repository


def _issue(number: int, title: str, body: str) -> IssueRecord:
    return IssueRecord(
        number=number,
        title=title,
        body=body,
        labels=["bug"],
        created_at=FIXED_TIME,
        updated_at=FIXED_TIME,
    )


def _evidence_report() -> InvestigationReport:
    issue = _issue(7, "Evidence contract", "The provider cites E7.")
    return InvestigationReport(
        issue=issue,
        confirmed_facts=[],
        candidates=[
            CandidateLocation(
                file=f"candidate-{index}.py",
                symbol=f"symbol_{index}",
                lines="1-2",
                confidence=0.5,
                evidence=[f"candidate {index}"],
            )
            for index in range(1, 8)
        ],
        hypotheses=[],
        reproduction_plan=ReproductionPlan(
            runtime="Synthetic Python runtime",
            setup_commands=[],
            reproduction_steps=[],
            safety_constraints=[],
            open_questions=[],
        ),
        repository_root=Path("synthetic-repository"),
    )


def _successful_result(issue: IssueRecord, evidence: list[EvidenceSnippet]) -> LLMAnalysisResult:
    first = evidence[0]
    return LLMAnalysisResult(
        provider="synthetic-provider",
        model="synthetic-model",
        request_id=f"request-{issue.number}",
        input_tokens=10,
        output_tokens=5,
        elapsed_ms=1.0,
        analysis=LLMAnalysis(
            summary="Synthetic analysis succeeded.",
            issue_type="bug",
            affected_component=f"{first.file}::{first.symbol}",
            reproduction_completeness="partial",
            evidence_observations=[
                {
                    "evidence_id": first.id,
                    "alignment": "supports_issue",
                    "observation": "The synthetic source is relevant.",
                }
            ],
            contradictions=[],
            reranked_evidence_ids=[first.id],
            hypotheses=[
                {
                    "description": "The synthetic source may be involved.",
                    "confidence": 0.5,
                    "evidence_ids": [first.id],
                    "missing_evidence": [],
                    "validation_step": "Run the synthetic test.",
                }
            ],
            needs_more_evidence=False,
        ),
    )


class _FailOnIssueBAnalyzer:
    provider = "synthetic-provider"
    model = "synthetic-model"

    def __init__(self) -> None:
        self.calls: list[int] = []

    def analyze(
        self,
        issue: IssueRecord,
        report: InvestigationReport,
        evidence: list[EvidenceSnippet],
    ) -> LLMAnalysisResult:
        self.calls.append(issue.number)
        if issue.number == 102:
            raise LLMProviderError(
                "synthetic provider failure",
                retryable=False,
                category="synthetic_provider_failure",
            )
        return _successful_result(issue, evidence)

    def close(self) -> None:
        return None


def test_baseline_manifest_has_observed_and_future_fields(baseline: dict) -> None:
    assert baseline["schema_version"] == 1
    scenario_ids = {scenario["id"] for scenario in baseline["scenarios"]}
    assert scenario_ids == {
        "t0_5_legacy_schema0_fixture",
        "t0_6_clean_tracked",
        "t0_6_official_demo",
        "t0_6_untracked_decoy",
        "t0_6_e7_primary",
        "t0_6_multi_issue_provider_failure",
    }
    for scenario in baseline["scenarios"]:
        assert set(scenario) >= {"id", "kind", "v1_observed", "v2_expected"}
    serialized = BASELINE_PATH.read_text(encoding="utf-8")
    assert "/Users/" not in serialized
    assert "LLM_API_KEY" not in serialized


def test_legacy_schema0_fixture_is_portable_and_readable_by_old_store(
    tmp_path: Path,
    baseline: dict,
) -> None:
    expected = baseline["fixture"]
    assert DATABASE_PATH.is_file()
    with sqlite3.connect(DATABASE_PATH) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == expected["user_version"]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert set(expected["legacy_tables"]) <= tables
        assert not any(name.startswith("agent_v2") for name in tables)
        for table, count in expected["row_counts"].items():
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == count
        statuses = dict(
            connection.execute(
                "SELECT status, count(*) FROM agent_runs GROUP BY status"
            ).fetchall()
        )
        assert statuses == expected["run_statuses"]
    assert not DATABASE_PATH.with_name(DATABASE_PATH.name + "-wal").exists()
    assert not DATABASE_PATH.with_name(DATABASE_PATH.name + "-shm").exists()

    raw_text = DATABASE_PATH.read_bytes().decode("utf-8", errors="ignore")
    assert str(REPOSITORY_ROOT) not in raw_text
    assert "/Users/" not in raw_text
    assert "api_key" not in raw_text.lower()
    assert "sk-" not in raw_text

    copied_path = tmp_path / "copied" / "legacy_agent.sqlite3"
    copied_path.parent.mkdir()
    shutil.copy2(DATABASE_PATH, copied_path)
    copied_store = AgentStore(copied_path)
    statuses_by_run = {
        run_id: copied_store.get_run(run_id).status.value
        for run_id in (
            "legacy-success-0001",
            "legacy-failed-0001",
            "legacy-reviewed-approved",
            "legacy-reviewed-rejected",
        )
    }
    assert statuses_by_run == {
        "legacy-success-0001": "awaiting_review",
        "legacy-failed-0001": "failed",
        "legacy-reviewed-approved": "approved",
        "legacy-reviewed-rejected": "rejected",
    }
    success = copied_store.get_run("legacy-success-0001")
    failed = copied_store.get_run("legacy-failed-0001")
    assert success is not None and success.investigations[0].llm_analysis is not None
    assert failed is not None and failed.error == "LLMProviderError: synthetic provider failure"
    assert success.investigations[0].llm_analysis.provider == "synthetic-provider"
    assert success.investigations[0].llm_analysis.analysis.hypotheses[0].evidence_ids == ["E1"]
    assert success.repository_root == Path("synthetic-repository")
    assert not success.repository_root.is_absolute()
    assert len(copied_store.list_traces("legacy-success-0001")) == 5
    assert len(copied_store.list_snapshots("legacy-success-0001")) == 1


def test_clean_tracked_baseline_is_deterministic(baseline: dict) -> None:
    repository = REPOSITORY_ROOT / "examples" / "demo_repository"
    repository_map = build_repository_map(repository)
    observed = _scenario(baseline, "t0_6_clean_tracked")["v1_observed"]
    assert [record.path for record in repository_map.files] == observed["source_files"]
    assert repository_map.runtime_files == observed["runtime_files"]


def test_official_demo_stays_within_subdirectory_scope(baseline: dict) -> None:
    repository = REPOSITORY_ROOT / "examples" / "demo_repository"
    repository_map = build_repository_map(repository)
    issue = _issue(
        1,
        "Refresh token fails in auth service",
        "The refresh_token handler returns an error when the token expires.",
    )
    report = investigate(issue, repository_map)
    observed = _scenario(baseline, "t0_6_official_demo")["v1_observed"]
    assert [record.path for record in repository_map.files] == observed["source_files"]
    assert report.candidates[0].file == observed["candidate_file"]
    assert report.candidates[0].symbol == observed["candidate_symbol"]
    assert Path(repository_map.root).resolve() == repository.resolve()


def test_v1_indexes_untracked_decoy_and_records_v2_target(tmp_path: Path, baseline: dict) -> None:
    repository = _copy_demo_repository(tmp_path, with_decoy=True)
    repository_map = build_repository_map(repository)
    observed = _scenario(baseline, "t0_6_untracked_decoy")["v1_observed"]
    assert [record.path for record in repository_map.files] == observed["source_files"]
    assert observed["untracked_decoy_indexed"] is True
    expected_v2 = _scenario(baseline, "t0_6_untracked_decoy")["v2_expected"]
    assert "untracked_noise.py" not in expected_v2["tracked_only_source_files"]


def test_e7_characterizes_v1_primary_component_and_v2_target(
    baseline: dict,
) -> None:
    evidence = [
        EvidenceSnippet(
            id=f"E{index}",
            file=f"candidate-{index}.py",
            symbol=f"symbol_{index}",
            lines="1-2",
            content=f"{index}: synthetic evidence",
        )
        for index in range(1, 8)
    ]
    response = LLMAnalysisResponse.model_validate(
        {
            "summary": "The seventh evidence item is primary.",
            "issue_type": "bug",
            "reproduction_completeness": "partial",
            "evidence_observations": [
                {
                    "evidence_id": snippet.id,
                    "alignment": "supports_issue",
                    "observation": "Synthetic observation.",
                }
                for snippet in evidence
            ],
            "hypothesis": {
                "description": "The cited item may be relevant.",
                "confidence": 0.7,
                "evidence_ids": ["E7"],
                "missing_evidence": [],
            },
        }
    )
    report = _evidence_report()
    analysis = OpenAICompatibleIssueAnalyzer._normalize_analysis(response, report, evidence)
    OpenAICompatibleIssueAnalyzer._validate_evidence_references(
        analysis,
        evidence,
        "synthetic-provider",
    )
    observed = _scenario(baseline, "t0_6_e7_primary")["v1_observed"]
    assert analysis.reranked_evidence_ids == observed["reranked_evidence_ids"]
    assert analysis.affected_component == observed["affected_component"]
    assert observed["validation_step_component"] in analysis.hypotheses[0].validation_step
    expected_v2 = _scenario(baseline, "t0_6_e7_primary")["v2_expected"]
    assert expected_v2["primary_evidence_id"] == "E7"
    assert expected_v2["affected_component"] != analysis.affected_component


def test_v1_provider_failure_aborts_multi_issue_batch_without_external_calls(
    tmp_path: Path,
    monkeypatch,
    baseline: dict,
) -> None:
    repository = tmp_path / "multi-issue"
    repository.mkdir()
    (repository / "pyproject.toml").write_text(
        "[project]\nname = 'synthetic-multi-issue'\n",
        encoding="utf-8",
    )
    (repository / "worker.py").write_text(
        "def issue_a_provider_failure():\n    return 'A'\n\n"
        "def issue_b_provider_failure():\n    return 'B'\n\n"
        "def issue_c_provider_failure():\n    return 'C'\n",
        encoding="utf-8",
    )
    analyzer = _FailOnIssueBAnalyzer()
    store = AgentStore(tmp_path / "multi-issue.sqlite3")
    run_id = "11111111-1111-4111-8111-111111111111"
    from repo_issue_intelligence import agent_workflow

    monkeypatch.setattr(agent_workflow, "uuid4", lambda: UUID(run_id))
    issues = [
        _issue(101, "Provider failure A", "issue_a_provider_failure fails."),
        _issue(102, "Provider failure B", "issue_b_provider_failure fails."),
        _issue(103, "Provider failure C", "issue_c_provider_failure fails."),
    ]
    with pytest.raises(LLMProviderError, match="synthetic provider failure"):
        run_agent(
            issues,
            repository,
            top_k=3,
            store=store,
            llm_analyzer=analyzer,
            max_attempts=1,
        )

    failed_run = store.get_run(run_id)
    assert failed_run is not None
    assert failed_run.status.value == _scenario(
        baseline,
        "t0_6_multi_issue_provider_failure",
    )["v1_observed"]["run_status"]
    assert analyzer.calls == [101, 102]
    assert failed_run.investigations == []
    assert failed_run.error == "LLMProviderError: synthetic provider failure"
    llm_traces = [trace for trace in store.list_traces(run_id) if trace.node_name == "llm_analyze"]
    assert [(trace.status, trace.attempt) for trace in llm_traces] == [("failed", 1)]
    expected_v2 = _scenario(baseline, "t0_6_multi_issue_provider_failure")["v2_expected"]
    assert expected_v2["issue_B_failure_isolated"] is True
    assert expected_v2["issue_C_still_processed"] is True
