from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.responses import JSONResponse

from .agent_store import AgentStore
from .agent_workflow import run_agent
from .api_security import (
    APIBoundary,
    LocalPrincipal,
    authorize_repository_operation,
    private_legacy_store,
    require_principal,
)
from .models import (
    AgentRun,
    IssueRecord,
    PriorityResult,
    RepositoryMap,
    ReviewDecision,
)
from .repository_index import build_repository_map
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


def get_agent_store() -> AgentStore:
    return private_legacy_store()


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, error: RequestValidationError):
    # Pydantic's default response includes offending input, including unknown secret fields.
    return JSONResponse({"detail": "Invalid request fields"}, 422)


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
