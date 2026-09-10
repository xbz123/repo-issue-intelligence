"""Read-only, source-free default views shared by HTTP and explicit CLI queries."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict
from typing_extensions import TypedDict

from .agent_store_v2 import AgentStoreV2, StoreError
from .protocol_v2_models import IssueSummaryV2, RunSummaryV2
from .run_configuration import normalize_endpoint


class QueryResponse(TypedDict, total=False):
    """Shared JSON envelope for the sparse endpoint-specific projections."""

    protocol: str
    run_id: str
    issue_number: int
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int
    next_offset: int | None
    evidence_set_id: str
    sealed_at: str


class QueryResponseModel(BaseModel):
    """OpenAPI-visible sparse response envelope; endpoint extras are preserved."""

    model_config = ConfigDict(extra="allow")
    protocol: str
    run_id: str


class QueryNotFound(StoreError):
    """The requested Run/Issue/sealed evidence binding is not retained."""


def _summary(store: AgentStoreV2, run_id: str) -> RunSummaryV2:
    summary = store.get_run_summary(run_id)
    if summary is None:
        raise QueryNotFound("Run not found")
    return summary


def _page(items: Sequence[Any], limit: int, offset: int) -> QueryResponse:
    if not 1 <= limit <= 100 or offset < 0:
        raise StoreError("Page requires limit 1–100 and nonnegative offset")
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < len(items) else None,
    }


def run_summary(store: AgentStoreV2, run_id: str) -> QueryResponse:
    run = _summary(store, run_id)
    return {
        "protocol": "v2",
        "run_id": run.run_id,
        "parent_run_id": run.parent_run_id,
        "status": run.status,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "snapshot": run.snapshot.model_dump(
            mode="json",
            include={
                "git_root",
                "analysis_root",
                "analysis_prefix",
                "commit_oid",
                "mode",
                "captured_at",
                "repository_identity",
                "normalized_remote_identity",
                "analysis_scope_dirty",
                "git_root_dirty",
                "input_representation",
            },
        ),
        "configuration": run.configuration.model_dump(mode="json"),
        "issue_count": len(run.issues),
        "reviewed_issues": run.reviewed_issues,
        "llm_outcomes": dict(run.llm_outcomes),
    }


def _issue_view(issue: IssueSummaryV2, *, detail: bool) -> dict[str, Any]:
    value = issue.model_dump(
        mode="json", exclude={"latest_attempt", "analysis", "deterministic_report"}
    )
    if detail:
        value["analysis"] = issue.model_dump(mode="json", include={"analysis"})["analysis"]
        report = issue.model_dump(mode="json", include={"deterministic_report"})[
            "deterministic_report"
        ]
        if report and report.get("issue", {}).get("html_url"):
            try:
                normalize_endpoint(report["issue"]["html_url"])
            except ValueError:
                report["issue"]["html_url"] = None
        value["deterministic_report"] = report
    return value


def issue_page(
    store: AgentStoreV2, run_id: str, *, limit: int = 50, offset: int = 0
) -> QueryResponse:
    run = store.get_run(run_id)
    if run is None:
        raise QueryNotFound("Run not found")
    issues = store.list_issues_page(run_id, limit=limit, offset=offset)
    total = len(run.selection.selected_issue_numbers)
    return {
        "protocol": "v2",
        "run_id": run_id,
        **_page(
            [
                issue.model_dump(mode="json", exclude={"analysis", "deterministic_report"})
                for issue in issues
            ],
            limit,
            0,
        ),
        "total": total,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < total else None,
    }


def issue_detail(store: AgentStoreV2, run_id: str, issue_number: int) -> QueryResponse:
    for issue in _summary(store, run_id).issues:
        if issue.issue_number == issue_number:
            return {"protocol": "v2", **_issue_view(issue, detail=True)}
    raise QueryNotFound("Issue not found in Run")


def evidence_page(
    store: AgentStoreV2,
    run_id: str,
    issue_number: int,
    *,
    limit: int = 50,
    offset: int = 0,
) -> QueryResponse:
    page = store.read_evidence_page(run_id, issue_number, limit=limit, offset=offset)
    if page is None:
        raise QueryNotFound("Sealed evidence unavailable for this Run/Issue/set")
    metadata, items = page
    return {
        "protocol": "v2",
        "run_id": run_id,
        "issue_number": issue_number,
        "evidence_set_id": metadata["evidence_set_id"],
        "sealed_at": metadata["sealed_at"],
        "items": list(items),
        "total": metadata["total"],
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < metadata["total"] else None,
    }


def evidence_item(
    store: AgentStoreV2,
    run_id: str,
    issue_number: int,
    evidence_id: str,
    *,
    evidence_set_id: str | None = None,
    include_content: bool = False,
) -> QueryResponse:
    if evidence_set_id is None:
        raise QueryNotFound("Evidence set must be selected explicitly")
    item = store.read_evidence_item(run_id, issue_number, evidence_id, evidence_set_id)
    if item is not None:
        return {
            "protocol": "v2",
            "run_id": run_id,
            "issue_number": issue_number,
            "evidence_set_id": evidence_set_id,
            **item.model_dump(mode="json", exclude=set() if include_content else {"content"}),
        }
    raise QueryNotFound("Evidence ID not found in this Run/Issue/set")


def attempt_page(
    store: AgentStoreV2,
    run_id: str,
    issue_number: int,
    *,
    limit: int = 50,
    offset: int = 0,
) -> QueryResponse:
    run = store.get_run(run_id)
    if run is None or issue_number not in run.selection.selected_issue_numbers:
        raise QueryNotFound("Issue not found in Run")
    attempts, total = store.list_attempts_page(run_id, issue_number, limit=limit, offset=offset)
    return {
        "protocol": "v2",
        "run_id": run_id,
        "issue_number": issue_number,
        **_page(
            [item.model_dump(mode="json", exclude={"analysis"}) for item in attempts],
            limit,
            0,
        ),
        "total": total,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < total else None,
    }
