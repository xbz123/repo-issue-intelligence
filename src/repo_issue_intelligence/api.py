from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agent_store import AgentStore
from .agent_workflow import run_agent
from .config import Settings
from .models import (
    AgentRun,
    IssueRecord,
    PriorityResult,
    RepositoryMap,
    ReviewDecision,
)
from .repository_index import build_repository_map
from .scoring import score_issue
from .service import rank_issues

app = FastAPI(
    title="Repo Issue Intelligence",
    version="0.3.0",
    description="Repository-aware GitHub issue prioritization and investigation MVP.",
)


class RankRequest(BaseModel):
    issues: list[IssueRecord] = Field(min_length=1)


class RepositoryIndexRequest(BaseModel):
    path: str


class AgentRunRequest(BaseModel):
    issues: list[IssueRecord] = Field(min_length=1)
    repository_path: str
    top_k: int = Field(default=1, ge=1, le=20)


class AgentReviewRequest(BaseModel):
    decision: ReviewDecision
    notes: str | None = None


def get_agent_store() -> AgentStore:
    return AgentStore(Settings().agent_db_path)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/issues/score", response_model=PriorityResult)
def score_issue_endpoint(issue: IssueRecord) -> PriorityResult:
    return score_issue(issue)


@app.post("/v1/issues/rank", response_model=list[PriorityResult])
def rank_issues_endpoint(request: RankRequest) -> list[PriorityResult]:
    return rank_issues(request.issues)


@app.post("/v1/repository/index", response_model=RepositoryMap)
def index_repository_endpoint(request: RepositoryIndexRequest) -> RepositoryMap:
    root = Path(request.path).expanduser().resolve()
    if not root.exists():
        raise HTTPException(status_code=404, detail="Repository path does not exist")
    if not root.is_dir():
        raise HTTPException(status_code=400, detail="Repository path must be a directory")
    return build_repository_map(root)


@app.post("/v1/agent/runs", response_model=AgentRun, status_code=201)
def create_agent_run_endpoint(
    request: AgentRunRequest,
    store: Annotated[AgentStore, Depends(get_agent_store)],
) -> AgentRun:
    try:
        return run_agent(
            request.issues,
            Path(request.repository_path),
            request.top_k,
            store,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/v1/agent/runs/{run_id}", response_model=AgentRun)
def get_agent_run_endpoint(
    run_id: str,
    store: Annotated[AgentStore, Depends(get_agent_store)],
) -> AgentRun:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run


@app.post("/v1/agent/runs/{run_id}/review", response_model=AgentRun)
def review_agent_run_endpoint(
    run_id: str,
    request: AgentReviewRequest,
    store: Annotated[AgentStore, Depends(get_agent_store)],
) -> AgentRun:
    try:
        return store.review(run_id, request.decision, request.notes)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Agent run not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
