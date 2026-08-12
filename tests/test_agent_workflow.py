from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from repo_issue_intelligence.agent_store import AgentStore
from repo_issue_intelligence.agent_workflow import run_agent
from repo_issue_intelligence.llm_client import LLMProviderError
from repo_issue_intelligence.models import (
    AgentRunStatus,
    IssueRecord,
    LLMAnalysis,
    LLMAnalysisResult,
    ReviewDecision,
)


def issue(number: int, title: str, body: str) -> IssueRecord:
    timestamp = datetime(2026, 7, 27, tzinfo=UTC)
    return IssueRecord(
        number=number,
        title=title,
        body=body,
        created_at=timestamp,
        updated_at=timestamp,
    )


def create_repository(root: Path) -> Path:
    repository = root / "repository"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (repository / "data_store.py").write_text(
        'def persist_data():\n    """Persist user data safely."""\n',
        encoding="utf-8",
    )
    return repository


class FakeAnalyzer:
    provider = "opencode"
    model = "deepseek-v4-flash-free"

    def analyze(self, issue, report, evidence):
        assert issue.number == report.issue.number
        assert evidence[0].id == "E1"
        return LLMAnalysisResult(
            provider=self.provider,
            model=self.model,
            request_id="request-test",
            input_tokens=250,
            output_tokens=100,
            elapsed_ms=12.5,
            analysis=LLMAnalysis(
                summary="Persistence evidence matches the issue.",
                issue_type="bug",
                affected_component="data_store",
                reproduction_completeness="partial",
                evidence_observations=[
                    {
                        "evidence_id": "E1",
                        "alignment": "supports_issue",
                        "observation": "The file contains the persistence function.",
                    }
                ],
                contradictions=[],
                reranked_evidence_ids=["E1"],
                hypotheses=[
                    {
                        "description": "The persistence path may lose data.",
                        "confidence": 0.8,
                        "evidence_ids": ["E1"],
                        "missing_evidence": ["Failing test"],
                        "validation_step": (
                            "Run the existing persistence test and inspect the stored state."
                        ),
                    }
                ],
                needs_more_evidence=True,
            ),
        )

    def close(self) -> None:
        return None


class FailIfCalledAnalyzer:
    provider = "opencode"
    model = "deepseek-v4-flash-free"

    def analyze(self, issue, report, evidence):
        raise AssertionError("analyzer must not be called without evidence")


class InvalidResponseAnalyzer:
    provider = "opencode"
    model = "deepseek-v4-flash-free"

    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, issue, report, evidence):
        self.calls += 1
        raise LLMProviderError(
            "invalid structured response",
            retryable=False,
            category="invalid_response",
        )


class RateLimitedAnalyzer(FakeAnalyzer):
    def __init__(self, failures: int, retry_after: float | None = None) -> None:
        self.failures = failures
        self.retry_after = retry_after
        self.calls = 0

    def analyze(self, issue, report, evidence):
        self.calls += 1
        if self.calls <= self.failures:
            raise LLMProviderError(
                "rate limited",
                retryable=True,
                retry_after=self.retry_after,
                category="rate_limit",
            )
        return super().analyze(issue, report, evidence)


def test_agent_run_persists_state_traces_snapshots_and_review(tmp_path: Path) -> None:
    repository = create_repository(tmp_path)
    store = AgentStore(tmp_path / "agent.sqlite3")
    issues = [
        issue(
            1,
            "Data loss in persistence layer",
            "Data loss is reproducible with steps to reproduce and a stack trace.",
        ),
        issue(2, "Improve documentation", "Add more examples to the setup guide."),
    ]

    run = run_agent(issues, repository, top_k=1, store=store)

    assert run.status is AgentRunStatus.AWAITING_REVIEW
    assert run.selected_issue_numbers == [1]
    assert len(run.investigations) == 1
    assert [trace.node_name for trace in run.traces] == [
        "rank_issues",
        "route_top_k",
        "build_repository_map",
        "investigate_issues",
        "human_review",
    ]
    assert all(trace.status == "completed" for trace in run.traces)
    assert len(store.list_snapshots(run.run_id)) == 5
    assert store.get_run(run.run_id) == run

    reviewed = store.review(run.run_id, ReviewDecision.APPROVED, "Evidence looks reasonable")

    assert reviewed.status is AgentRunStatus.APPROVED
    assert reviewed.review_notes == "Evidence looks reasonable"


def test_agent_node_retries_once_before_succeeding(tmp_path: Path, monkeypatch) -> None:
    repository = create_repository(tmp_path)
    store = AgentStore(tmp_path / "agent.sqlite3")
    issues = [issue(1, "Data persistence failure", "Steps to reproduce data failure")]

    from repo_issue_intelligence import agent_workflow

    original = agent_workflow.build_repository_map
    attempts = 0

    def flaky_build(path: Path):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary index failure")
        return original(path)

    monkeypatch.setattr(agent_workflow, "build_repository_map", flaky_build)

    run = run_agent(issues, repository, top_k=1, store=store)
    build_traces = [trace for trace in run.traces if trace.node_name == "build_repository_map"]

    assert [(trace.status, trace.attempt) for trace in build_traces] == [
        ("failed", 1),
        ("completed", 2),
    ]


def test_agent_run_adds_optional_llm_nodes_and_trace_metadata(tmp_path: Path) -> None:
    repository = create_repository(tmp_path)
    store = AgentStore(tmp_path / "agent.sqlite3")
    issues = [
        issue(
            1,
            "Data loss in persistence layer",
            "Data loss is reproducible with steps to reproduce.",
        )
    ]

    run = run_agent(
        issues,
        repository,
        top_k=1,
        store=store,
        llm_analyzer=FakeAnalyzer(),
    )

    assert run.status is AgentRunStatus.AWAITING_REVIEW
    assert run.llm_enabled is True
    assert run.llm_model == "deepseek-v4-flash-free"
    assert [trace.node_name for trace in run.traces] == [
        "rank_issues",
        "route_top_k",
        "build_repository_map",
        "investigate_issues",
        "collect_code_evidence",
        "llm_analyze",
        "human_review",
    ]
    analysis = run.investigations[0].llm_analysis
    assert analysis is not None
    assert analysis.analysis.reranked_evidence_ids == ["E1"]
    llm_trace = next(trace for trace in run.traces if trace.node_name == "llm_analyze")
    assert llm_trace.metadata["input_tokens"] == 250
    assert llm_trace.metadata["request_ids"] == ["request-test"]

    hypothesis = analysis.analysis.hypotheses[0]
    analysis.analysis.hypotheses = [hypothesis.model_copy() for _ in range(3)]
    store.save_run(run)
    restored = store.get_run(run.run_id)
    assert restored is not None
    restored_analysis = restored.investigations[0].llm_analysis
    assert restored_analysis is not None
    assert len(restored_analysis.analysis.hypotheses) == 3


def test_agent_run_skips_llm_when_repository_evidence_is_empty(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "pyproject.toml").write_text(
        "[project]\nname='empty-demo'\n",
        encoding="utf-8",
    )
    store = AgentStore(tmp_path / "agent.sqlite3")

    run = run_agent(
        [issue(1, "Unknown runtime failure", "No source location is available.")],
        repository,
        top_k=1,
        store=store,
        llm_analyzer=FailIfCalledAnalyzer(),
    )

    assert run.status is AgentRunStatus.AWAITING_REVIEW
    assert run.llm_enabled is True
    assert run.investigations[0].llm_analysis is None
    llm_trace = next(trace for trace in run.traces if trace.node_name == "llm_analyze")
    assert llm_trace.status == "completed"
    assert llm_trace.attempt == 1
    assert llm_trace.metadata["analyzed_issue_numbers"] == []
    assert llm_trace.metadata["skipped_no_evidence_issue_numbers"] == [1]
    assert llm_trace.metadata["input_tokens"] == 0
    assert llm_trace.metadata["output_tokens"] == 0


def test_agent_run_analyzes_only_issues_with_repository_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = create_repository(tmp_path)
    store = AgentStore(tmp_path / "agent.sqlite3")
    from repo_issue_intelligence import agent_workflow

    original_collect_evidence = agent_workflow.collect_evidence

    def selective_collect_evidence(report, max_total_chars):
        if report.issue.number == 2:
            return []
        return original_collect_evidence(report, max_total_chars=max_total_chars)

    monkeypatch.setattr(
        agent_workflow,
        "collect_evidence",
        selective_collect_evidence,
    )

    run = run_agent(
        [
            issue(1, "Data loss in persistence layer", "persist_data loses data."),
            issue(2, "Unknown runtime failure", "No source location is available."),
        ],
        repository,
        top_k=2,
        store=store,
        llm_analyzer=FakeAnalyzer(),
    )

    reports = {report.issue.number: report for report in run.investigations}
    assert reports[1].llm_analysis is not None
    assert reports[2].llm_analysis is None
    llm_trace = next(trace for trace in run.traces if trace.node_name == "llm_analyze")
    assert llm_trace.metadata["analyzed_issue_numbers"] == [1]
    assert llm_trace.metadata["skipped_no_evidence_issue_numbers"] == [2]
    assert llm_trace.metadata["input_tokens"] == 250


def test_agent_does_not_retry_non_retryable_provider_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = create_repository(tmp_path)
    store = AgentStore(tmp_path / "agent.sqlite3")
    analyzer = InvalidResponseAnalyzer()
    run_id = "ac36f97b-535f-44f3-b638-452285d5119c"
    from repo_issue_intelligence import agent_workflow

    monkeypatch.setattr(agent_workflow, "uuid4", lambda: UUID(run_id))

    with pytest.raises(LLMProviderError, match="invalid structured response"):
        run_agent(
            [issue(1, "Data persistence failure", "persist_data loses data")],
            repository,
            top_k=1,
            store=store,
            llm_analyzer=analyzer,
            max_attempts=3,
        )

    assert analyzer.calls == 1
    failed_run = store.get_run(run_id)
    assert failed_run is not None
    llm_traces = [trace for trace in failed_run.traces if trace.node_name == "llm_analyze"]
    assert [(trace.status, trace.attempt) for trace in llm_traces] == [("failed", 1)]


@pytest.mark.parametrize(
    ("retry_after", "expected_delays"),
    [(None, [1.0, 2.0]), (4.0, [4.0, 4.0])],
)
def test_agent_retries_rate_limit_with_bounded_backoff(
    tmp_path: Path,
    monkeypatch,
    retry_after: float | None,
    expected_delays: list[float],
) -> None:
    repository = create_repository(tmp_path)
    store = AgentStore(tmp_path / "agent.sqlite3")
    analyzer = RateLimitedAnalyzer(failures=2, retry_after=retry_after)
    delays: list[float] = []
    from repo_issue_intelligence import agent_workflow

    monkeypatch.setattr(agent_workflow, "sleep", delays.append)

    run = run_agent(
        [issue(1, "Data persistence failure", "persist_data loses data")],
        repository,
        top_k=1,
        store=store,
        llm_analyzer=analyzer,
        max_attempts=3,
    )

    assert run.status is AgentRunStatus.AWAITING_REVIEW
    assert analyzer.calls == 3
    assert delays == expected_delays
    llm_traces = [trace for trace in run.traces if trace.node_name == "llm_analyze"]
    assert [(trace.status, trace.attempt) for trace in llm_traces] == [
        ("failed", 1),
        ("failed", 2),
        ("completed", 3),
    ]


def test_agent_run_limits_repository_index_to_included_files(tmp_path: Path) -> None:
    repository = create_repository(tmp_path)
    (repository / "ignored_noise.py").write_text(
        "def ignored_noise():\n    return 'ignored noise failure'\n",
        encoding="utf-8",
    )
    store = AgentStore(tmp_path / "agent.sqlite3")

    run = run_agent(
        [issue(1, "Ignored noise failure", "ignored_noise fails")],
        repository,
        top_k=1,
        store=store,
        included_files=["pyproject.toml", "data_store.py"],
    )

    assert all(
        candidate.file != "ignored_noise.py"
        for candidate in run.investigations[0].candidates
    )


def test_agent_persists_terminal_failure_after_retries(tmp_path: Path, monkeypatch) -> None:
    repository = create_repository(tmp_path)
    store = AgentStore(tmp_path / "agent.sqlite3")
    issues = [issue(1, "Data persistence failure", "Steps to reproduce data failure")]
    run_id = "c9f6baed-c09b-4d34-b3e2-d5a8073c1f67"

    from repo_issue_intelligence import agent_workflow

    monkeypatch.setattr(agent_workflow, "uuid4", lambda: UUID(run_id))

    def broken_build(path: Path):
        raise OSError(f"cannot index {path.name}")

    monkeypatch.setattr(agent_workflow, "build_repository_map", broken_build)

    with pytest.raises(OSError, match="cannot index repository"):
        run_agent(issues, repository, top_k=1, store=store)

    failed_run = store.get_run(run_id)
    assert failed_run is not None
    assert failed_run.status is AgentRunStatus.FAILED
    assert failed_run.error == "OSError: cannot index repository"
    assert [(trace.node_name, trace.status, trace.attempt) for trace in failed_run.traces[-2:]] == [
        ("build_repository_map", "failed", 1),
        ("build_repository_map", "failed", 2),
    ]
