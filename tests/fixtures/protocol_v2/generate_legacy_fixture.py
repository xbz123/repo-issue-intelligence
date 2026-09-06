"""Generate the checked-in schema-0 AgentStore characterization fixture.

This deliberately builds rows through the legacy public store/model path. The
fixture must continue to exercise the legacy DDL in ``AgentStore`` rather than
copying that DDL into a test helper.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from repo_issue_intelligence.agent_store import AgentStore
from repo_issue_intelligence.llm_client import OpenAICompatibleIssueAnalyzer
from repo_issue_intelligence.models import (
    AgentRun,
    AgentRunStatus,
    CandidateLocation,
    EvidenceSnippet,
    Hypothesis,
    InvestigationReport,
    IssueRecord,
    LLMAnalysisResponse,
    LLMAnalysisResult,
    NodeTrace,
    Priority,
    PriorityResult,
    ReproductionPlan,
    ReviewDecision,
    ScoreFactors,
    Severity,
    Urgency,
)

FIXTURE_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = FIXTURE_DIR / "legacy_agent.sqlite3"
FIXED_NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
FIXED_ISSUE_TIME = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class _FixtureDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FIXED_NOW.replace(tzinfo=None)
        return FIXED_NOW.astimezone(tz)


def _issue(number: int, title: str, body: str) -> IssueRecord:
    return IssueRecord(
        number=number,
        title=title,
        body=body,
        labels=["bug"],
        created_at=FIXED_ISSUE_TIME,
        updated_at=FIXED_ISSUE_TIME,
        html_url=f"https://example.invalid/issues/{number}",
        author="synthetic-user",
    )


def _priority(issue_number: int, priority: Priority = Priority.P1) -> PriorityResult:
    return PriorityResult(
        issue_number=issue_number,
        severity=Severity.HIGH,
        urgency=Urgency.MEDIUM,
        priority=priority,
        priority_score=78.5,
        priority_reasons=["Synthetic characterization input"],
        factors=ScoreFactors(
            severity=0.78,
            urgency=0.5,
            affected_users=0.2,
            reproducibility=0.8,
            duplicate_count=0.0,
            release_blocking=0.0,
            recency=1.0,
        ),
    )


def _report(
    issue: IssueRecord,
    *,
    llm_analysis: LLMAnalysisResult | None = None,
) -> InvestigationReport:
    candidate = CandidateLocation(
        file="src/synthetic_worker.py",
        symbol="handle_request",
        qualified_symbol="synthetic_worker.handle_request",
        lines="1-4",
        confidence=0.75,
        evidence=["Synthetic path evidence"],
    )
    return InvestigationReport(
        issue=issue,
        confirmed_facts=[f"Issue #{issue.number} is synthetic fixture data"],
        candidates=[candidate],
        hypotheses=[
            Hypothesis(
                id="H1",
                description="The synthetic worker may contain the reported failure.",
                confidence=0.75,
                supporting_evidence=["Synthetic path evidence"],
                missing_evidence=["A real runtime trace"],
            )
        ],
        reproduction_plan=ReproductionPlan(
            runtime="Synthetic Python runtime",
            setup_commands=["python -m pip install -e ."],
            baseline_command="python -m pytest -q",
            reproduction_steps=["Run the synthetic regression test."],
            safety_constraints=["Do not use production data or credentials."],
            open_questions=["Which deployed version is affected?"],
        ),
        repository_root=Path("synthetic-repository"),
        llm_analysis=llm_analysis,
    )


def _successful_analysis(issue_number: int) -> LLMAnalysisResult:
    issue = _issue(issue_number, f"Synthetic issue {issue_number}", "Synthetic failure")
    report = _report(issue)
    evidence = [
        EvidenceSnippet(
            id="E1",
            file="src/synthetic_worker.py",
            symbol="handle_request",
            lines="1-4",
            content="1: def handle_request():",
        )
    ]
    provider_response = LLMAnalysisResponse.model_validate(
        {
            "summary": "Synthetic provider analysis succeeded.",
            "issue_type": "bug",
            "reproduction_completeness": "partial",
            "evidence_observations": [
                {
                    "evidence_id": "E1",
                    "alignment": "supports_issue",
                    "observation": "Synthetic evidence supports the issue.",
                }
            ],
            "hypothesis": {
                "description": "The synthetic handler may be involved.",
                "confidence": 0.7,
                "evidence_ids": ["E1"],
                "missing_evidence": ["A real runtime trace"],
            },
        }
    )
    analysis = OpenAICompatibleIssueAnalyzer._normalize_analysis(
        provider_response,
        report,
        evidence,
    )
    OpenAICompatibleIssueAnalyzer._validate_evidence_references(
        analysis,
        evidence,
        "synthetic-provider",
    )
    return LLMAnalysisResult(
        provider="synthetic-provider",
        model="synthetic-model",
        reasoning_effort="none",
        request_id=f"synthetic-request-{issue_number}",
        input_tokens=32,
        output_tokens=16,
        elapsed_ms=4.0,
        analysis=analysis,
    )


def _trace(node_name: str, status: str = "completed") -> NodeTrace:
    return NodeTrace(
        node_name=node_name,
        status=status,
        attempt=1,
        started_at=FIXED_NOW,
        finished_at=FIXED_NOW,
        elapsed_ms=4.0,
        input_summary={"fixture": True},
        output_summary={"status": status},
        metadata={"source": "synthetic"},
        error="synthetic provider failure" if status == "failed" else None,
    )


def _run(
    run_id: str,
    issue_number: int,
    status: AgentRunStatus,
    *,
    llm_analysis: LLMAnalysisResult | None = None,
    error: str | None = None,
    trace_status: str = "completed",
) -> AgentRun:
    issue = _issue(
        issue_number,
        f"Synthetic issue {issue_number}",
        "Synthetic failure with steps to reproduce.",
    )
    report = _report(issue, llm_analysis=llm_analysis)
    traces = [
        _trace("rank_issues"),
        _trace("route_top_k"),
        _trace("build_repository_map"),
        _trace("investigate_issues"),
        _trace("llm_analyze", trace_status),
    ]
    return AgentRun(
        run_id=run_id,
        status=status,
        repository_root=Path("synthetic-repository"),
        top_k=1,
        llm_enabled=True,
        llm_model="synthetic-model",
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        ranked_issues=[_priority(issue_number)],
        selected_issue_numbers=[issue_number],
        investigations=[report],
        traces=traces,
        error=error,
    )


def _snapshot(run_id: str, node_name: str) -> dict:
    return {
        "run_id": run_id,
        "node_name": node_name,
        "repository_root": "synthetic-repository",
        "captured_at": FIXED_NOW.isoformat(),
        "fixture": True,
    }


def build_fixture(
    database_path: Path = DEFAULT_DATABASE_PATH,
    *,
    overwrite: bool = False,
) -> Path:
    database_path = database_path.resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if database_path.exists():
        if not overwrite:
            raise FileExistsError(
                f"Refusing to overwrite existing fixture: {database_path}; pass --overwrite"
            )
        database_path.unlink()

    with patch("repo_issue_intelligence.agent_store.datetime", _FixtureDateTime):
        store = AgentStore(database_path)
        success = _run(
            "legacy-success-0001",
            101,
            AgentRunStatus.AWAITING_REVIEW,
            llm_analysis=_successful_analysis(101),
        )
        failed = _run(
            "legacy-failed-0001",
            102,
            AgentRunStatus.FAILED,
            error="LLMProviderError: synthetic provider failure",
            trace_status="failed",
        )
        approved = _run("legacy-reviewed-approved", 103, AgentRunStatus.AWAITING_REVIEW)
        rejected = _run("legacy-reviewed-rejected", 104, AgentRunStatus.AWAITING_REVIEW)

        for run in (success, failed, approved, rejected):
            store.save_run(run)
            for trace in run.traces:
                store.append_trace(run.run_id, trace)
            store.save_snapshot(
                run.run_id,
                "investigate_issues",
                _snapshot(run.run_id, "investigate_issues"),
            )

        store.review(approved.run_id, ReviewDecision.APPROVED, "Synthetic review approved")
        store.review(rejected.run_id, ReviewDecision.REJECTED, "Synthetic review rejected")
    return database_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    path = build_fixture(args.output, overwrite=args.overwrite)
    print(path)


if __name__ == "__main__":
    main()
