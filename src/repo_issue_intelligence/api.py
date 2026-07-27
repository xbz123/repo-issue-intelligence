from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .models import IssueRecord, PriorityResult, RepositoryMap
from .repository_index import build_repository_map
from .scoring import score_issue
from .service import rank_issues

app = FastAPI(
    title="Repo Issue Intelligence",
    version="0.1.0",
    description="Repository-aware GitHub issue prioritization and investigation MVP.",
)


class RankRequest(BaseModel):
    issues: list[IssueRecord] = Field(min_length=1)


class RepositoryIndexRequest(BaseModel):
    path: str


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
