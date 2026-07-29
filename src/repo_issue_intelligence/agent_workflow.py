from __future__ import annotations

import operator
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, sleep
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from .agent_store import AgentStore
from .evidence import collect_evidence
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
from .repository_index import build_repository_map
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
                if attempt == max_attempts:
                    failed_state = dict(state)
                    failed_state["traces"] = [*state.get("traces", []), *attempt_traces]
                    failed_state["error"] = trace.error
                    store.save_snapshot(run_id, node_name, failed_state)
                    raise
                retry_after = getattr(error, "retry_after", None)
                if isinstance(retry_after, (int, float)) and retry_after > 0:
                    sleep(min(float(retry_after), 30.0))
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


def _build_repository_map_node(state: AgentGraphState) -> dict[str, Any]:
    return {"repository_map": build_repository_map(Path(state["repository_root"]))}


def _investigate_issues_node(state: AgentGraphState) -> dict[str, Any]:
    reports = [investigate(issue, state["repository_map"]) for issue in state["selected_issues"]]
    return {"investigations": reports}


def _collect_code_evidence_node(
    state: AgentGraphState,
    max_total_chars: int,
) -> dict[str, Any]:
    return {
        "evidence_by_issue": {
            report.issue.number: collect_evidence(report, max_total_chars=max_total_chars)
            for report in state["investigations"]
        }
    }


def _llm_analyze_node(
    state: AgentGraphState,
    analyzer: IssueAnalyzer,
) -> dict[str, Any]:
    updated_reports: list[InvestigationReport] = []
    results = []
    for report in state["investigations"]:
        evidence = state["evidence_by_issue"].get(report.issue.number, [])
        result = analyzer.analyze(report.issue, report, evidence)
        results.append(result)
        updated_reports.append(report.model_copy(update={"llm_analysis": result}))
    return {
        "investigations": updated_reports,
        "_trace_metadata": {
            "provider": "groq",
            "models": sorted({result.model for result in results}),
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
    max_evidence_chars: int = 16_000,
):
    builder = StateGraph(AgentGraphState)
    nodes: list[tuple[str, NodeFunction]] = [
        ("rank_issues", _rank_issues_node),
        ("route_top_k", _route_top_k_node),
        ("build_repository_map", _build_repository_map_node),
        ("investigate_issues", _investigate_issues_node),
    ]
    if llm_analyzer is not None:
        nodes.extend(
            [
                (
                    "collect_code_evidence",
                    lambda state: _collect_code_evidence_node(state, max_evidence_chars),
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
    max_evidence_chars: int = 16_000,
) -> AgentRun:
    if not issues:
        raise ValueError("At least one issue is required")
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
        llm_analyzer=llm_analyzer,
        max_evidence_chars=max_evidence_chars,
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
