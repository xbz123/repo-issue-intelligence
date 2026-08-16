from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Urgency(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Priority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class IssueType(StrEnum):
    BUG = "bug"
    FEATURE = "feature"
    SECURITY = "security"
    PERFORMANCE = "performance"
    DOCUMENTATION = "documentation"
    QUESTION = "question"
    OTHER = "other"


class ReproductionCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class EvidenceAlignment(StrEnum):
    SUPPORTS_ISSUE = "supports_issue"
    CONTRADICTS_ISSUE = "contradicts_issue"
    NEUTRAL = "neutral"


class StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IssueRecord(BaseModel):
    number: int
    title: str
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    comments_count: int = 0
    created_at: datetime
    updated_at: datetime
    html_url: str | None = None
    author: str | None = None

    @property
    def text(self) -> str:
        return " ".join([self.title, self.body, *self.labels]).lower()


class ScoreFactors(BaseModel):
    severity: float
    urgency: float
    affected_users: float
    reproducibility: float
    duplicate_count: float
    release_blocking: float
    recency: float


class PriorityResult(BaseModel):
    issue_number: int
    severity: Severity
    urgency: Urgency
    priority: Priority
    priority_score: float
    priority_reasons: list[str]
    factors: ScoreFactors
    needs_information: bool = False


class DuplicateMatch(BaseModel):
    issue_number: int
    candidate_issue_number: int
    similarity: float
    shared_terms: list[str]


class SymbolRecord(BaseModel):
    name: str
    qualified_name: str | None = None
    kind: str
    line: int
    end_line: int | None = None
    docstring: str | None = None


class ResolvedCall(BaseModel):
    caller: str | None = None
    local_name: str
    target_file: str
    target_symbol: str


class QualifiedExternalCall(BaseModel):
    caller: str | None = None
    target: str


class FileRecord(BaseModel):
    path: str
    language: str
    symbols: list[SymbolRecord] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    local_imports: list[str] = Field(default_factory=list)
    local_import_symbols: dict[str, list[str]] = Field(default_factory=dict)
    module_import_symbols: dict[str, list[str]] = Field(default_factory=dict)
    calls: list[str] = Field(default_factory=list)
    name_calls: list[str] = Field(default_factory=list)
    symbol_calls: dict[str, list[str]] = Field(default_factory=dict)
    qualified_symbol_calls: dict[str, list[str]] = Field(default_factory=dict)
    resolved_calls: list[ResolvedCall] = Field(default_factory=list)
    function_local_import_calls: list[ResolvedCall] = Field(default_factory=list)
    qualified_external_calls: list[QualifiedExternalCall] = Field(
        default_factory=list
    )
    resolved_import_references: dict[str, list[str]] = Field(default_factory=dict)
    references: list[str] = Field(default_factory=list)
    test_file: bool = False


class RepositoryMap(BaseModel):
    root: str
    languages: dict[str, int]
    frameworks: list[str]
    entrypoints: list[str]
    test_directories: list[str]
    runtime_files: list[str]
    files: list[FileRecord]


class CandidateLocation(BaseModel):
    file: str
    symbol: str | None = None
    qualified_symbol: str | None = None
    lines: str | None = None
    confidence: float
    evidence: list[str]


class EvidenceSnippet(BaseModel):
    id: str
    file: str
    symbol: str | None = None
    lines: str
    content: str


class LLMEvidenceObservation(StrictOutputModel):
    evidence_id: str
    alignment: EvidenceAlignment
    observation: str


class LLMHypothesis(StrictOutputModel):
    description: str
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(min_length=1)
    missing_evidence: list[str]
    validation_step: str


class LLMAnalysis(StrictOutputModel):
    summary: str
    issue_type: IssueType
    affected_component: str
    reproduction_completeness: ReproductionCompleteness
    evidence_observations: list[LLMEvidenceObservation]
    contradictions: list[str]
    reranked_evidence_ids: list[str]
    hypotheses: list[LLMHypothesis]
    needs_more_evidence: bool


class LLMProviderHypothesis(StrictOutputModel):
    """Minimal provider hypothesis; validation remains deterministic and local."""

    description: str
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(min_length=1)
    missing_evidence: list[str]


class LLMAnalysisResponse(StrictOutputModel):
    """Compact provider contract; persisted analyses retain the public full schema."""

    summary: str
    issue_type: IssueType
    reproduction_completeness: ReproductionCompleteness
    evidence_observations: list[LLMEvidenceObservation]
    hypothesis: LLMProviderHypothesis


class LLMAnalysisResult(BaseModel):
    provider: str
    model: str
    request_id: str | None = None
    system_fingerprint: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: float
    analysis: LLMAnalysis


class EvidenceRerankAnalysis(StrictOutputModel):
    reranked_evidence_ids: list[str] = Field(min_length=1)


class EvidenceRerankResult(BaseModel):
    provider: str
    model: str
    request_id: str | None = None
    system_fingerprint: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: float
    attempts: int = Field(default=1, ge=1)
    analysis: EvidenceRerankAnalysis


class Hypothesis(BaseModel):
    id: str
    description: str
    confidence: float
    supporting_evidence: list[str]
    missing_evidence: list[str] = Field(default_factory=list)


class ReproductionPlan(BaseModel):
    runtime: str
    setup_commands: list[str]
    baseline_command: str | None = None
    reproduction_steps: list[str]
    safety_constraints: list[str]
    open_questions: list[str]


class InvestigationReport(BaseModel):
    issue: IssueRecord
    confirmed_facts: list[str]
    candidates: list[CandidateLocation]
    hypotheses: list[Hypothesis]
    reproduction_plan: ReproductionPlan
    repository_root: Path
    llm_analysis: LLMAnalysisResult | None = None


class AgentRunStatus(StrEnum):
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class NodeTrace(BaseModel):
    node_name: str
    status: str
    attempt: int
    started_at: datetime
    finished_at: datetime
    elapsed_ms: float
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class AgentRun(BaseModel):
    run_id: str
    status: AgentRunStatus
    repository_root: Path
    top_k: int
    llm_enabled: bool = False
    llm_model: str | None = None
    created_at: datetime
    updated_at: datetime
    ranked_issues: list[PriorityResult] = Field(default_factory=list)
    selected_issue_numbers: list[int] = Field(default_factory=list)
    investigations: list[InvestigationReport] = Field(default_factory=list)
    traces: list[NodeTrace] = Field(default_factory=list)
    review_notes: str | None = None
    error: str | None = None
