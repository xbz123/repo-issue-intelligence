from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.responses import JSONResponse

from . import agent_queries
from .agent_store import AgentStore
from .agent_store_v2 import AgentStoreV2, StoreConflict, StoreError
from .agent_workflow import run_agent
from .api_security import (
    APIBoundary,
    LocalPrincipal,
    authorize_repository_operation,
    private_legacy_store,
    require_principal,
)
from .config import Settings
from .models import (
    AgentRun,
    IssueRecord,
    PriorityResult,
    RepositoryMap,
    ReviewDecision,
)
from .repository_index import build_repository_map
from .review_service import Correction, ReviewSubmission, submit_issue_review
from .run_configuration import normalize_endpoint
from .scoring import score_issue
from .service import rank_issues

app = FastAPI(
    dependencies=[Depends(require_principal)],
    title="Repo Issue Intelligence",
    version="0.5.0",
    description="Repository-aware GitHub issue prioritization and investigation MVP.",
)
app.add_middleware(APIBoundary)


class APIIssue(IssueRecord):
    model_config = ConfigDict(extra="forbid")

    @field_validator("html_url")
    @classmethod
    def safe_issue_url(cls, value: str | None) -> str | None:
        if value is not None:
            normalize_endpoint(value)
        return value


class RankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issues: list[APIIssue] = Field(min_length=1)


class RepositoryIndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issues: list[APIIssue] = Field(min_length=1)
    repository_path: str
    top_k: int = Field(default=1, ge=1, le=20)


class AgentReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: ReviewDecision
    notes: str | None = None


class V2ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected", "needs_information"]
    notes: str | None = Field(default=None, max_length=512)
    corrections: tuple[Correction, ...] = ()
    evidence_set_id: str | None = None
    selected_attempt_id: str | None = None
    expected_review_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=256)


class V2RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recover_unknown: bool = False


def get_agent_store() -> AgentStore:
    return private_legacy_store()


def get_v2_store() -> AgentStoreV2:
    try:
        path = Settings().api_v2_database
        if path is None:
            raise ValueError
        return AgentStoreV2(path)
    except (OSError, ValueError):
        raise HTTPException(503, "Private V2 database unavailable") from None


V2Store = Annotated[AgentStoreV2, Depends(get_v2_store)]
Principal = Annotated[LocalPrincipal, Depends(require_principal)]
QueryResponse = agent_queries.QueryResponse
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0)]


def _authorize_v2(
    store: AgentStoreV2,
    run_id: str,
    principal: LocalPrincipal,
    operation: str,
) -> None:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    authorize_repository_operation(principal, run.snapshot.analysis_root, operation)


@app.exception_handler(StoreError)
async def invalid_stored_query(request: Request, error: StoreError) -> JSONResponse:
    status = 404 if isinstance(error, agent_queries.QueryNotFound) else 503
    return JSONResponse({"detail": "Retained resource unavailable"}, status)


@app.get("/v2/agent/runs/{run_id}", response_model=agent_queries.QueryResponseModel)
def v2_run(run_id: str, store: V2Store, principal: Principal) -> QueryResponse:
    _authorize_v2(store, run_id, principal, "read")
    return agent_queries.run_summary(store, run_id)


@app.get("/v2/agent/runs/{run_id}/issues", response_model=agent_queries.QueryResponseModel)
def v2_issues(
    run_id: str, store: V2Store, principal: Principal, limit: PageLimit = 50, offset: PageOffset = 0
) -> QueryResponse:
    _authorize_v2(store, run_id, principal, "read")
    return agent_queries.issue_page(store, run_id, limit=limit, offset=offset)


@app.get(
    "/v2/agent/runs/{run_id}/issues/{issue_number}", response_model=agent_queries.QueryResponseModel
)
def v2_issue(run_id: str, issue_number: int, store: V2Store, principal: Principal) -> QueryResponse:
    _authorize_v2(store, run_id, principal, "read")
    return agent_queries.issue_detail(store, run_id, issue_number)


@app.get(
    "/v2/agent/runs/{run_id}/issues/{issue_number}/evidence",
    response_model=agent_queries.QueryResponseModel,
)
def v2_evidence(
    run_id: str,
    issue_number: int,
    store: V2Store,
    principal: Principal,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> QueryResponse:
    _authorize_v2(store, run_id, principal, "evidence")
    return agent_queries.evidence_page(store, run_id, issue_number, limit=limit, offset=offset)


@app.get(
    "/v2/agent/runs/{run_id}/issues/{issue_number}/evidence/{evidence_id}",
    response_model=agent_queries.QueryResponseModel,
)
def v2_evidence_content(
    run_id: str,
    issue_number: int,
    evidence_id: str,
    store: V2Store,
    principal: Principal,
    evidence_set_id: str | None = None,
) -> QueryResponse:
    _authorize_v2(store, run_id, principal, "evidence")
    return agent_queries.evidence_item(
        store,
        run_id,
        issue_number,
        evidence_id,
        evidence_set_id=evidence_set_id,
        include_content=True,
    )


@app.get(
    "/v2/agent/runs/{run_id}/issues/{issue_number}/attempts",
    response_model=agent_queries.QueryResponseModel,
)
def v2_attempts(
    run_id: str,
    issue_number: int,
    store: V2Store,
    principal: Principal,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
) -> QueryResponse:
    _authorize_v2(store, run_id, principal, "read")
    return agent_queries.attempt_page(store, run_id, issue_number, limit=limit, offset=offset)


@app.post(
    "/v2/agent/runs/{run_id}/issues/{issue_number}/reviews",
    response_model=agent_queries.QueryResponseModel,
)
def v2_review(
    run_id: str,
    issue_number: int,
    request: V2ReviewRequest,
    store: V2Store,
    principal: Principal,
) -> QueryResponse:
    _authorize_v2(store, run_id, principal, "review")
    try:
        return submit_issue_review(
            store,
            run_id=run_id,
            issue_number=issue_number,
            principal_id=principal.name,
            idempotency_key=request.idempotency_key,
            expected_review_version=request.expected_review_version,
            request=ReviewSubmission(
                decision=request.decision,
                notes=request.notes,
                corrections=request.corrections,
                evidence_set_id=request.evidence_set_id,
                selected_attempt_id=request.selected_attempt_id,
            ),
        )
    except StoreConflict as error:
        raise HTTPException(409, "Review conflicts with current state") from error
    except StoreError as error:
        raise HTTPException(400, "Review request refused") from error


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, error: RequestValidationError):
    # Pydantic's default response includes offending input, including unknown secret fields.
    return JSONResponse({"detail": "Invalid request fields"}, 422)


def _public_issue(issue: IssueRecord) -> IssueRecord:
    if issue.html_url is None:
        return issue
    try:
        normalize_endpoint(issue.html_url)
    except ValueError:
        return issue.model_copy(update={"html_url": None})
    return issue


def _public_run(run: AgentRun) -> AgentRun:
    """Do not expose historical raw errors or rewrite their stored records."""
    return run.model_copy(
        update={
            "error": "Local execution failed (details withheld)" if run.error else None,
            "traces": [
                trace.model_copy(
                    update={
                        "error": "Node execution failed (details withheld)"
                        if trace.error
                        else None,
                    }
                )
                for trace in run.traces
            ],
            "investigations": [
                report.model_copy(update={"issue": _public_issue(report.issue)})
                for report in run.investigations
            ],
        }
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/issues/score", response_model=PriorityResult)
def score_issue_endpoint(issue: APIIssue) -> PriorityResult:
    return score_issue(issue)


@app.post("/v1/issues/rank", response_model=list[PriorityResult])
def rank_issues_endpoint(request: RankRequest) -> list[PriorityResult]:
    return rank_issues(request.issues)


@app.post("/v1/repository/index", response_model=RepositoryMap)
def index_repository_endpoint(
    request: RepositoryIndexRequest,
    principal: Annotated[LocalPrincipal, Depends(require_principal)],
) -> RepositoryMap:
    root = authorize_repository_operation(principal, request.path, "index")
    return build_repository_map(root)


@app.post("/v1/agent/runs", response_model=AgentRun, status_code=201)
def create_agent_run_endpoint(
    request: AgentRunRequest,
    store: Annotated[AgentStore, Depends(get_agent_store)],
    principal: Annotated[LocalPrincipal, Depends(require_principal)],
) -> AgentRun:
    root = authorize_repository_operation(principal, request.repository_path, "run")
    try:
        return _public_run(
            run_agent(
                request.issues,
                root,
                request.top_k,
                store,
            )
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid local run input") from error


@app.get("/v1/agent/runs/{run_id}", response_model=AgentRun)
def get_agent_run_endpoint(
    run_id: str,
    store: Annotated[AgentStore, Depends(get_agent_store)],
    principal: Annotated[LocalPrincipal, Depends(require_principal)],
) -> AgentRun:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    authorize_repository_operation(principal, run.repository_root, "read")
    return _public_run(run)


@app.post("/v1/agent/runs/{run_id}/review", response_model=AgentRun)
def review_agent_run_endpoint(
    run_id: str,
    request: AgentReviewRequest,
    store: Annotated[AgentStore, Depends(get_agent_store)],
    principal: Annotated[LocalPrincipal, Depends(require_principal)],
) -> AgentRun:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Agent run not found")
    authorize_repository_operation(principal, run.repository_root, "review")
    try:
        return _public_run(store.review(run_id, request.decision, request.notes))
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Agent run not found") from error
    except ValueError as error:
        raise HTTPException(
            status_code=409, detail="Review conflicts with current state"
        ) from error
