from __future__ import annotations

import operator
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, sleep
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from .agent_store import AgentStore
from .evidence import (
    DEFAULT_MAX_LINES_PER_SNIPPET,
    DEFAULT_MAX_TOTAL_CHARS,
    collect_evidence,
)
from .investigator import investigate
from .llm_client import IssueAnalyzer
from .models import (
    AgentRun,
    AgentRunStatus,
    EvidenceSnippet,
    InvestigationReport,
    IssueRecord,
    NodeTrace,
    PriorityResult,
    RepositoryMap,
)
from .protocol_v2_models import RepositorySnapshot
from .repository_index import build_repository_map
from .repository_view import RepositoryView, prepare_repository_view
from .service import rank_issues


class AgentGraphState(TypedDict, total=False):
    run_id: str
    issues: list[IssueRecord]
    repository_root: str
    top_k: int
    ranked_issues: list[PriorityResult]
    selected_issues: list[IssueRecord]
    repository_map: RepositoryMap
    investigations: list[InvestigationReport]
    evidence_by_issue: dict[int, list[EvidenceSnippet]]
    traces: Annotated[list[NodeTrace], operator.add]
    status: AgentRunStatus


NodeFunction = Callable[[AgentGraphState], dict[str, Any]]
RETRY_BASE_DELAY_SECONDS = 1.0
RETRY_MAX_DELAY_SECONDS = 30.0


def _summarize(values: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, list):
            summary[key] = len(value)
        elif isinstance(value, BaseModel):
            summary[key] = type(value).__name__
        elif isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = value
        else:
            summary[key] = type(value).__name__
    return summary


def _traced_node(
    node_name: str,
    function: NodeFunction,
    store: AgentStore,
    run_id: str,
    max_attempts: int,
) -> NodeFunction:
    def execute(state: AgentGraphState) -> dict[str, Any]:
        attempt_traces: list[NodeTrace] = []
        for attempt in range(1, max_attempts + 1):
            started_at = datetime.now(UTC)
            started_clock = perf_counter()
            try:
                output = function(state)
                trace_metadata = output.pop("_trace_metadata", {})
            except Exception as error:
                finished_at = datetime.now(UTC)
                trace = NodeTrace(
                    node_name=node_name,
                    status="failed",
                    attempt=attempt,
                    started_at=started_at,
                    finished_at=finished_at,
                    elapsed_ms=round((perf_counter() - started_clock) * 1000, 3),
                    input_summary=_summarize(dict(state)),
                    error=f"{type(error).__name__}: {error}",
                )
                attempt_traces.append(trace)
                store.append_trace(run_id, trace)
                retryable = getattr(error, "retryable", None)
                should_retry = attempt < max_attempts and retryable is not False
                if not should_retry:
                    failed_state = dict(state)
                    failed_state["traces"] = [*state.get("traces", []), *attempt_traces]
                    failed_state["error"] = trace.error
                    store.save_snapshot(run_id, node_name, failed_state)
                    raise
                retry_after = getattr(error, "retry_after", None)
                retry_delay = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                if isinstance(retry_after, (int, float)) and retry_after > 0:
                    retry_delay = max(retry_delay, float(retry_after))
                if retryable is True:
                    sleep(min(retry_delay, RETRY_MAX_DELAY_SECONDS))
            else:
                finished_at = datetime.now(UTC)
                trace = NodeTrace(
                    node_name=node_name,
                    status="completed",
                    attempt=attempt,
                    started_at=started_at,
                    finished_at=finished_at,
                    elapsed_ms=round((perf_counter() - started_clock) * 1000, 3),
                    input_summary=_summarize(dict(state)),
                    output_summary=_summarize(output),
                    metadata=trace_metadata,
                )
                attempt_traces.append(trace)
                store.append_trace(run_id, trace)
                snapshot = {**state, **output}
                snapshot["traces"] = [*state.get("traces", []), *attempt_traces]
                store.save_snapshot(run_id, node_name, snapshot)
                return {**output, "traces": attempt_traces}
        raise RuntimeError(f"Node {node_name} exhausted its retry loop")

    return execute


def _rank_issues_node(state: AgentGraphState) -> dict[str, Any]:
    return {"ranked_issues": rank_issues(state["issues"])}


def _route_top_k_node(state: AgentGraphState) -> dict[str, Any]:
    issue_by_number = {issue.number: issue for issue in state["issues"]}
    selected = [
        issue_by_number[result.issue_number] for result in state["ranked_issues"][: state["top_k"]]
    ]
    return {"selected_issues": selected}


def _build_repository_map_node(
    state: AgentGraphState,
    included_files: Sequence[str] | None = None,
) -> dict[str, Any]:
    if included_files is None:
        return {"repository_map": build_repository_map(Path(state["repository_root"]))}
    return {
        "repository_map": build_repository_map(
            Path(state["repository_root"]),
            included_files=included_files,
        )
    }


def _investigate_issues_node(state: AgentGraphState) -> dict[str, Any]:
    reports = [investigate(issue, state["repository_map"]) for issue in state["selected_issues"]]
    return {"investigations": reports}


def _collect_code_evidence_node(
    state: AgentGraphState,
    max_total_chars: int | None,
    max_lines_per_snippet: int | None,
) -> dict[str, Any]:
    return {
        "evidence_by_issue": {
            report.issue.number: collect_evidence(
                report,
                max_total_chars=max_total_chars,
                max_lines_per_snippet=max_lines_per_snippet,
            )
            for report in state["investigations"]
        }
    }


def _llm_analyze_node(
    state: AgentGraphState,
    analyzer: IssueAnalyzer,
) -> dict[str, Any]:
    updated_reports: list[InvestigationReport] = []
    results = []
    analyzed_issue_numbers: list[int] = []
    skipped_no_evidence_issue_numbers: list[int] = []
    for report in state["investigations"]:
        evidence = state["evidence_by_issue"].get(report.issue.number, [])
        if not evidence:
            skipped_no_evidence_issue_numbers.append(report.issue.number)
            updated_reports.append(report)
            continue
        result = analyzer.analyze(report.issue, report, evidence)
        results.append(result)
        analyzed_issue_numbers.append(report.issue.number)
        updated_reports.append(report.model_copy(update={"llm_analysis": result}))
    return {
        "investigations": updated_reports,
        "_trace_metadata": {
            "provider": results[0].provider if results else analyzer.provider,
            "models": sorted({result.model for result in results}),
            "analyzed_issue_numbers": analyzed_issue_numbers,
            "skipped_no_evidence_issue_numbers": skipped_no_evidence_issue_numbers,
            "request_ids": [
                result.request_id for result in results if result.request_id is not None
            ],
            "input_tokens": sum(result.input_tokens for result in results),
            "output_tokens": sum(result.output_tokens for result in results),
            "request_elapsed_ms": round(sum(result.elapsed_ms for result in results), 3),
        },
    }


def _human_review_node(state: AgentGraphState) -> dict[str, Any]:
    return {"status": AgentRunStatus.AWAITING_REVIEW}


def build_agent_graph(
    store: AgentStore,
    run_id: str,
    max_attempts: int = 2,
    llm_analyzer: IssueAnalyzer | None = None,
    max_evidence_chars: int | None = DEFAULT_MAX_TOTAL_CHARS,
    max_evidence_lines: int | None = DEFAULT_MAX_LINES_PER_SNIPPET,
    included_files: Sequence[str] | None = None,
):
    builder = StateGraph(AgentGraphState)
    nodes: list[tuple[str, NodeFunction]] = [
        ("rank_issues", _rank_issues_node),
        ("route_top_k", _route_top_k_node),
        (
            "build_repository_map",
            lambda state: _build_repository_map_node(state, included_files),
        ),
        ("investigate_issues", _investigate_issues_node),
    ]
    if llm_analyzer is not None:
        nodes.extend(
            [
                (
                    "collect_code_evidence",
                    lambda state: _collect_code_evidence_node(
                        state,
                        max_evidence_chars,
                        max_evidence_lines,
                    ),
                ),
                (
                    "llm_analyze",
                    lambda state: _llm_analyze_node(state, llm_analyzer),
                ),
            ]
        )
    nodes.append(("human_review", _human_review_node))
    for node_name, function in nodes:
        builder.add_node(
            node_name,
            _traced_node(node_name, function, store, run_id, max_attempts),
        )
    builder.add_edge(START, "rank_issues")
    builder.add_edge("rank_issues", "route_top_k")
    builder.add_edge("route_top_k", "build_repository_map")
    builder.add_edge("build_repository_map", "investigate_issues")
    if llm_analyzer is None:
        builder.add_edge("investigate_issues", "human_review")
    else:
        builder.add_edge("investigate_issues", "collect_code_evidence")
        builder.add_edge("collect_code_evidence", "llm_analyze")
        builder.add_edge("llm_analyze", "human_review")
    builder.add_edge("human_review", END)
    return builder.compile()


def run_agent(
    issues: list[IssueRecord],
    repository_root: Path,
    top_k: int,
    store: AgentStore,
    llm_analyzer: IssueAnalyzer | None = None,
    max_evidence_chars: int | None = DEFAULT_MAX_TOTAL_CHARS,
    max_evidence_lines: int | None = DEFAULT_MAX_LINES_PER_SNIPPET,
    included_files: Sequence[str] | None = None,
    max_attempts: int = 2,
) -> AgentRun:
    if not issues:
        raise ValueError("At least one issue is required")
    issue_numbers = [issue.number for issue in issues]
    if len(issue_numbers) != len(set(issue_numbers)):
        raise ValueError("Issue numbers must be unique")
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    repository_root = repository_root.expanduser().resolve()
    if not repository_root.exists():
        raise ValueError("Repository path does not exist")
    if not repository_root.is_dir():
        raise ValueError("Repository path must be a directory")

    now = datetime.now(UTC)
    run = AgentRun(
        run_id=str(uuid4()),
        status=AgentRunStatus.RUNNING,
        repository_root=repository_root,
        top_k=top_k,
        llm_enabled=llm_analyzer is not None,
        llm_model=llm_analyzer.model if llm_analyzer is not None else None,
        created_at=now,
        updated_at=now,
    )
    store.save_run(run)
    graph = build_agent_graph(
        store,
        run.run_id,
        max_attempts=max_attempts,
        llm_analyzer=llm_analyzer,
        max_evidence_chars=max_evidence_chars,
        max_evidence_lines=max_evidence_lines,
        included_files=included_files,
    )
    initial_state: AgentGraphState = {
        "run_id": run.run_id,
        "issues": issues,
        "repository_root": str(repository_root),
        "top_k": top_k,
        "traces": [],
        "status": AgentRunStatus.RUNNING,
    }

    try:
        result = graph.invoke(initial_state)
    except Exception as error:
        run.status = AgentRunStatus.FAILED
        run.updated_at = datetime.now(UTC)
        run.traces = store.list_traces(run.run_id)
        run.error = f"{type(error).__name__}: {error}"
        store.save_run(run)
        raise

    run.status = result["status"]
    run.updated_at = datetime.now(UTC)
    run.ranked_issues = result["ranked_issues"]
    run.selected_issue_numbers = [issue.number for issue in result["selected_issues"]]
    run.investigations = result["investigations"]
    run.traces = result["traces"]
    store.save_run(run)
    return run


@dataclass(frozen=True)
class ProtocolV2InvestigationResult:
    """Pure in-memory output of the explicit PR1B read/evidence boundary."""

    repository_map: RepositoryMap
    investigations: tuple[InvestigationReport, ...]
    evidence_by_issue: dict[int, tuple[EvidenceSnippet, ...]]


def run_protocol_v2_investigation(
    issues: Sequence[IssueRecord],
    snapshot: RepositorySnapshot | RepositoryView,
    *,
    candidate_limit: int = 20,
    max_evidence_chars: int | None = DEFAULT_MAX_TOTAL_CHARS,
    max_evidence_lines: int | None = DEFAULT_MAX_LINES_PER_SNIPPET,
    max_chars_per_snippet: int | None = None,
    deterministic_resume: bool = False,
) -> ProtocolV2InvestigationResult:
    """Run the explicit PR1B map/investigate/evidence path in memory.

    This is deliberately not an AgentRun/Store/LLM workflow.  PR1B owns only
    the source-view I/O boundary; later PRs will add V2 persistence and
    execution orchestration.  A snapshot-created view is closed after all
    reports and evidence are collected.  A caller-supplied prepared view
    remains owned by its caller.
    """

    if not issues:
        raise ValueError("At least one issue is required")
    issue_numbers = [issue.number for issue in issues]
    if len(issue_numbers) != len(set(issue_numbers)):
        raise ValueError("Issue numbers must be unique")
    if candidate_limit < 1:
        raise ValueError("candidate_limit must be positive")

    owned_view = not isinstance(snapshot, RepositoryView)
    view = prepare_repository_view(snapshot, deterministic_resume=deterministic_resume)
    try:
        repository_map = build_repository_map(view)
        investigations = tuple(
            investigate(issue, repository_map, candidate_limit=candidate_limit)
            for issue in issues
        )
        evidence_by_issue = {
            report.issue.number: tuple(
                collect_evidence(
                    report,
                    max_total_chars=max_evidence_chars,
                    max_lines_per_snippet=max_evidence_lines,
                    max_chars_per_snippet=max_chars_per_snippet,
                    repository_view=view,
                )
            )
            for report in investigations
        }
        return ProtocolV2InvestigationResult(
            repository_map=repository_map,
            investigations=investigations,
            evidence_by_issue=evidence_by_issue,
        )
    finally:
        if owned_view:
            view.close()
