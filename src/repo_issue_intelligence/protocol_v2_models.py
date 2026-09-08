"""Immutable in-memory contracts used by the first Protocol v2 boundary.

This module deliberately contains no persistence or provider code.  The models
are the value objects that are captured before a future V2 run is created.  A
V1 ``AgentRun`` is not converted implicitly; callers must opt in to the
capture helpers in :mod:`repository_context` and :mod:`run_configuration`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_serializer, model_validator

from .models import (
    EvidenceAlignment,
    EvidenceSnippet,
    IssueRecord,
    IssueType,
    Priority,
    PriorityResult,
    ReproductionCompleteness,
    ScoreFactors,
    Severity,
    Urgency,
)


class ProtocolV2Model(BaseModel):
    """Base model with strict fields and assignment-level immutability."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
        validate_assignment=True,
    )


class FrozenDict(dict[str, Any]):
    """A JSON-compatible recursively immutable mapping.

    Pydantic's ``frozen=True`` protects model attributes, but does not protect
    a nested ``dict`` or ``list``.  Protocol inputs are persisted/replayed, so
    allowing a caller to mutate a nested object after capture would violate the
    immutable run contract.  This small dict subclass keeps normal read and
    JSON-serialization behaviour while rejecting every mutating operation.
    """

    def __init__(self, values: Mapping[str, Any] | None = None, **kwargs: Any) -> None:
        source: Mapping[str, Any] = values or {}
        if kwargs:
            source = {**source, **kwargs}
        dict.__init__(self, {str(key): _freeze_value(value) for key, value in source.items()})

    @staticmethod
    def _immutable(*_args: Any, **_kwargs: Any) -> None:
        raise TypeError("captured Protocol v2 values are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable

    def copy(self) -> FrozenDict:
        return FrozenDict(self)


def _freeze_value(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        return FrozenDict(value)
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_value(item) for item in value)
    return value


class RepositoryCaptureMode(StrEnum):
    """Input capture modes supported by the Protocol v2 boundary."""

    COMMITTED = "committed"
    TRACKED_WORKTREE = "tracked_worktree"


class RepositoryFileStatus(StrEnum):
    TRACKED = "tracked"
    MODIFIED = "modified"
    ADDED = "added"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    UNMERGED = "unmerged"
    UNTRACKED = "untracked"


class RepositoryFileRecord(ProtocolV2Model):
    """One tracked manifest entry, relative to the analysis root."""

    path: str = Field(min_length=1)
    git_path: str = Field(min_length=1)
    status: RepositoryFileStatus = RepositoryFileStatus.TRACKED
    index_status: str = " "
    worktree_status: str = " "
    mode: str | None = None
    object_id: str | None = None
    stage: int = Field(default=0, ge=0, le=3)
    tracked: bool = True
    deleted: bool = False
    staged_deleted: bool = False
    unmerged: bool = False
    symlink: bool = False
    submodule: bool = False

    @property
    def relative_path(self) -> str:
        """Compatibility name used by manifest readers."""

        return self.path

    @property
    def is_symlink(self) -> bool:
        return self.symlink

    @property
    def is_submodule(self) -> bool:
        return self.submodule

    @property
    def blob_oid(self) -> str | None:
        """Git object identity captured for the manifest entry."""

        return self.object_id


class RepositoryStatusEntry(ProtocolV2Model):
    """NUL-status observation, including paths absent from the current index."""

    path: str = Field(min_length=1)
    original_path: str | None = None
    index_status: str = " "
    worktree_status: str = " "
    untracked: bool = False
    deleted: bool = False
    staged_deleted: bool = False
    unmerged: bool = False


class RepositorySnapshot(ProtocolV2Model):
    """Captured Git/repository identity and scoped file facts.

    ``manifest`` is relative to ``analysis_root`` while ``git_path`` on each
    entry is relative to ``git_root``.  This distinction is important for
    nested demo repositories and for a later fixed-revision materialized view.
    """

    schema_version: ClassVar[int] = 1
    git_root: Path
    analysis_root: Path
    analysis_prefix: str = ""
    commit_oid: str | None = None
    branch: str | None = None
    detached: bool = False
    mode: RepositoryCaptureMode = RepositoryCaptureMode.COMMITTED
    git_metadata_kind: str = "unknown"
    git_dir: Path | None = None
    analysis_scope_dirty: bool = False
    git_root_dirty: bool = False
    untracked_in_scope_count: int = Field(default=0, ge=0)
    untracked_in_scope_paths: tuple[str, ...] = ()
    status_entries: tuple[RepositoryStatusEntry, ...] = ()
    manifest: tuple[RepositoryFileRecord, ...] = ()
    deleted_paths: tuple[str, ...] = ()
    staged_deleted_paths: tuple[str, ...] = ()
    unmerged_paths: tuple[str, ...] = ()
    symlink_paths: tuple[str, ...] = ()
    submodule_paths: tuple[str, ...] = ()
    unsupported_paths: tuple[str, ...] = ()
    skipped_paths: tuple[str, ...] = ()
    remote_name: str | None = None
    remote_selection_source: str | None = None
    remote_url_count: int = Field(default=0, ge=0)
    remote_sanitized: bool = False
    normalized_remote_identity: str | None = None
    repository_identity: str | None = None
    input_representation: str = "git-index-paths"
    captured_at: AwareDatetime

    @property
    def tracked_files(self) -> tuple[RepositoryFileRecord, ...]:
        return self.manifest

    @property
    def scoped_file_manifest(self) -> tuple[RepositoryFileRecord, ...]:
        return self.manifest

    @property
    def commit(self) -> str | None:
        return self.commit_oid

    @property
    def captured_revision(self) -> str | None:
        """Explicit name used by the fixed-revision source-view boundary."""

        return self.commit_oid

    @property
    def git_root_is_dirty(self) -> bool:
        return self.git_root_dirty

    @property
    def analysis_root_is_dirty(self) -> bool:
        return self.analysis_scope_dirty


class EngineRuntime(ProtocolV2Model):
    """Version/provenance of the actually imported analysis engine."""

    package_name: str | None = None
    package_version: str | None = None
    imported_module: str | None = None
    imported_source: Path | None = None
    source_root: Path | None = None
    source_revision: str | None = None
    source_dirty: bool | None = None
    source_provenance: str = "unknown"
    python_version: str
    python_implementation: str
    runtime: str

    @property
    def version(self) -> str | None:
        return self.package_version

    @property
    def revision(self) -> str | None:
        return self.source_revision

    @property
    def dirty(self) -> bool | None:
        return self.source_dirty


class ParameterOrigin(StrEnum):
    USER_CONFIG = "user_config"
    CLIENT_DEFAULT = "client_default"
    OMITTED = "omitted"


class ClientConfiguration(ProtocolV2Model):
    """Safe client identity; secret-bearing settings are intentionally absent."""

    backend: str = "api"
    provider: str | None = None
    endpoint: str | None = None
    cli_executable: str | None = None
    cli_version: str | None = None
    transport: str | None = None
    response_format_json: bool | None = None


class BudgetConfiguration(ProtocolV2Model):
    output_tokens: int | None = Field(default=None, ge=1)
    evidence_chars: int | None = Field(default=None, ge=1)
    evidence_lines: int | None = Field(default=None, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0)
    retry_policy: FrozenDict = Field(default_factory=FrozenDict)

    @property
    def max_output_tokens(self) -> int | None:
        return self.output_tokens


class ProtocolConfiguration(ProtocolV2Model):
    provider_schema_version: str = "provider-schema-v1"
    prompt_version: str = "prompt-v1"
    analysis_protocol_version: str = "analysis-v2"
    retrieval_protocol_version: str = "retrieval-v1"
    index_version: str = "repository-index-v1"
    top_k: int | None = Field(default=None, ge=1)
    selection_protocol: str = "priority-score-desc-v1"


class RunConfiguration(ProtocolV2Model):
    """Immutable requested/client configuration, never an ``effective`` config."""

    schema_version: ClassVar[int] = 1
    llm_enabled: bool = False
    client: ClientConfiguration = Field(default_factory=ClientConfiguration)
    requested_model: str | None = None
    request_parameters: FrozenDict = Field(default_factory=FrozenDict)
    parameter_origins: FrozenDict = Field(default_factory=FrozenDict)
    budgets: BudgetConfiguration = Field(default_factory=BudgetConfiguration)
    protocol: ProtocolConfiguration = Field(default_factory=ProtocolConfiguration)
    engine: EngineRuntime
    safe_cli_flags: tuple[str, ...] = ()
    captured_at: AwareDatetime

    @field_serializer("budgets")
    def serialize_budgets(self, value: BudgetConfiguration) -> dict[str, Any]:
        return value.model_dump(
            exclude={
                name
                for name in ("output_tokens", "timeout_seconds")
                if name not in value.model_fields_set and getattr(value, name) is None
            }
        )

    def has_request_budget(self, name: str) -> bool:
        value = getattr(self.budgets, name)
        origin = self.parameter_origins.get(
            f"budget.{name}", self.parameter_origins.get(name, "omitted")
        )
        # A model_copy can add explicit null without updating origin markers.
        return value is not None or name in self.budgets.model_fields_set or origin != "omitted"

    @model_validator(mode="after")
    def validate_request_budget_consistency(self) -> Self:
        for parameter, budget in (
            ("max_output_tokens", "output_tokens"),
            ("output_tokens", "output_tokens"),
            ("timeout_seconds", "timeout_seconds"),
        ):
            value = getattr(self.budgets, budget)
            if (
                value is None
                and budget in self.budgets.model_fields_set
                and self.parameter_origins.get(f"budget.{budget}") == "omitted"
            ):
                raise ValueError("Conflicting explicit-null budget and omitted origin")
            if (
                parameter in self.request_parameters
                and self.has_request_budget(budget)
                and self.request_parameters[parameter] != value
            ):
                raise ValueError("Conflicting request-parameter and budget values")
        if (
            "output_tokens" in self.request_parameters
            and "max_output_tokens" in self.request_parameters
            and self.request_parameters["output_tokens"]
            != self.request_parameters["max_output_tokens"]
        ):
            raise ValueError("Conflicting output-token request aliases")
        return self

    @property
    def requested(self) -> FrozenDict:
        """Read-only conceptual group used by the R5 contract."""

        return FrozenDict(
            {
                "requested_model": self.requested_model,
                "request_parameters": self.request_parameters,
                "parameter_origins": self.parameter_origins,
                "budgets": self.budgets.model_dump(mode="json"),
            }
        )

    @property
    def requested_parameters(self) -> FrozenDict:
        """Compatibility spelling used by the protocol documentation."""

        return self.request_parameters

    @property
    def model(self) -> str | None:
        return self.requested_model


class FrozenIssueSnapshot(ProtocolV2Model):
    """Deep-frozen snapshot of one input Issue."""

    number: int = Field(ge=1)
    title: str = Field(min_length=1)
    body: str = ""
    labels: tuple[str, ...] = ()
    comments_count: int = Field(default=0, ge=0)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    html_url: str | None = None
    author: str | None = None

    @classmethod
    def from_issue(cls, issue: IssueRecord) -> Self:
        return cls(
            number=issue.number,
            title=issue.title,
            body=issue.body,
            labels=tuple(issue.labels),
            comments_count=issue.comments_count,
            created_at=issue.created_at,
            updated_at=issue.updated_at,
            html_url=issue.html_url,
            author=issue.author,
        )

    def to_issue(self) -> IssueRecord:
        return IssueRecord(
            number=self.number,
            title=self.title,
            body=self.body,
            labels=list(self.labels),
            comments_count=self.comments_count,
            created_at=self.created_at,
            updated_at=self.updated_at,
            html_url=self.html_url,
            author=self.author,
        )

    @property
    def text(self) -> str:
        return " ".join((self.title, self.body, *self.labels)).lower()


class FrozenScoreFactors(ProtocolV2Model):
    severity: float
    urgency: float
    affected_users: float
    reproducibility: float
    duplicate_count: float
    release_blocking: float
    recency: float

    @classmethod
    def from_factors(cls, factors: ScoreFactors) -> Self:
        return cls(**factors.model_dump())

    def to_factors(self) -> ScoreFactors:
        return ScoreFactors(**self.model_dump())


class FrozenPriorityResult(ProtocolV2Model):
    """Deep-frozen ranking result retained for replay/audit."""

    issue_number: int = Field(ge=1)
    severity: Severity
    urgency: Urgency
    priority: Priority
    priority_score: float
    priority_reasons: tuple[str, ...] = ()
    factors: FrozenScoreFactors
    needs_information: bool = False
    ordinal: int | None = Field(default=None, ge=1)

    @classmethod
    def from_result(cls, result: PriorityResult, *, ordinal: int | None = None) -> Self:
        return cls(
            issue_number=result.issue_number,
            severity=result.severity,
            urgency=result.urgency,
            priority=result.priority,
            priority_score=result.priority_score,
            priority_reasons=tuple(result.priority_reasons),
            factors=FrozenScoreFactors.from_factors(result.factors),
            needs_information=result.needs_information,
            ordinal=ordinal,
        )

    def to_result(self) -> PriorityResult:
        values = self.model_dump(exclude={"ordinal"})
        values["priority_reasons"] = list(self.priority_reasons)
        values["factors"] = self.factors.to_factors()
        return PriorityResult(**values)


class FrozenSelection(ProtocolV2Model):
    """Selected Issues and their ranking ordinals, retained without re-ranking."""

    selected_issue_numbers: tuple[int, ...] = ()
    selected_ordinals: tuple[int, ...] = ()
    top_k: int | None = Field(default=None, ge=1)
    selection_protocol: str = "priority-score-desc-v1"

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        if any(number < 1 for number in self.selected_issue_numbers):
            raise ValueError("selected Issue numbers must be positive")
        if any(ordinal < 1 for ordinal in self.selected_ordinals):
            raise ValueError("selected ranking ordinals must be positive")
        if len(self.selected_issue_numbers) != len(set(self.selected_issue_numbers)):
            raise ValueError("selected issue numbers must be unique")
        if len(self.selected_ordinals) != len(set(self.selected_ordinals)):
            raise ValueError("selected ranking ordinals must be unique")
        if len(self.selected_issue_numbers) != len(self.selected_ordinals):
            raise ValueError("selected issues and ordinals must have the same length")
        if self.top_k is not None and len(self.selected_issue_numbers) > self.top_k:
            raise ValueError("selection exceeds top_k")
        return self


class RunInputs(ProtocolV2Model):
    """Immutable Issues, ranking snapshot and selection used by a run."""

    schema_version: ClassVar[int] = 1
    issues: tuple[FrozenIssueSnapshot, ...]
    as_of: AwareDatetime
    ranked_results: tuple[FrozenPriorityResult, ...]
    selection: FrozenSelection
    selection_protocol: str = "priority-score-desc-v1"

    @model_validator(mode="after")
    def validate_inputs(self) -> Self:
        issue_numbers = [issue.number for issue in self.issues]
        if len(issue_numbers) != len(set(issue_numbers)):
            raise ValueError("Issue numbers must be unique")
        ranked_numbers = [result.issue_number for result in self.ranked_results]
        if len(ranked_numbers) != len(set(ranked_numbers)):
            raise ValueError("ranked issue numbers must be unique")
        missing = set(ranked_numbers) - set(issue_numbers)
        if missing:
            raise ValueError(f"ranking contains unknown Issue numbers: {sorted(missing)}")
        unranked = set(issue_numbers) - set(ranked_numbers)
        if unranked:
            raise ValueError(f"ranking omits Issue numbers: {sorted(unranked)}")
        rank_by_ordinal = {
            (result.ordinal if result.ordinal is not None else index + 1): result
            for index, result in enumerate(self.ranked_results)
        }
        for issue_number, ordinal in zip(
            self.selection.selected_issue_numbers,
            self.selection.selected_ordinals,
            strict=True,
        ):
            result = rank_by_ordinal.get(ordinal)
            if result is None or result.issue_number != issue_number:
                raise ValueError("selection does not match frozen ranking")
        return self

    @property
    def issue_snapshots(self) -> tuple[FrozenIssueSnapshot, ...]:
        return self.issues

    @property
    def ranked_issues(self) -> tuple[FrozenPriorityResult, ...]:
        return self.ranked_results

    @property
    def selected_issue_numbers(self) -> tuple[int, ...]:
        return self.selection.selected_issue_numbers

    @property
    def selected_ordinals(self) -> tuple[int, ...]:
        return self.selection.selected_ordinals


def freeze_mapping(values: Mapping[str, Any] | None) -> FrozenDict:
    """Public helper for constructing recursively immutable JSON-like values."""

    return FrozenDict(values or {})


def freeze_run_inputs(
    issues: list[IssueRecord] | tuple[IssueRecord, ...],
    *,
    as_of: datetime,
    ranked_results: list[PriorityResult] | tuple[PriorityResult, ...],
    selected_issue_numbers: list[int] | tuple[int, ...] | None = None,
    selected_ordinals: list[int] | tuple[int, ...] | None = None,
    top_k: int | None = None,
    selection_protocol: str = "priority-score-desc-v1",
) -> RunInputs:
    """Freeze already-computed ranking data without syncing or ranking again."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    frozen_results = tuple(
        FrozenPriorityResult.from_result(result, ordinal=index + 1)
        for index, result in enumerate(ranked_results)
    )
    if selected_issue_numbers is None and selected_ordinals is None:
        limit = top_k if top_k is not None else len(frozen_results)
        selected = frozen_results[:limit]
        selected_issue_numbers = tuple(result.issue_number for result in selected)
        selected_ordinals = tuple(
            result.ordinal or index + 1 for index, result in enumerate(selected)
        )
    elif selected_issue_numbers is None:
        selected_ordinals = tuple(selected_ordinals or ())
        by_ordinal = {result.ordinal: result for result in frozen_results}
        selected_issue_numbers = tuple(
            by_ordinal[ordinal].issue_number
            if ordinal in by_ordinal
            else (_raise_unknown_ordinal(ordinal))
            for ordinal in selected_ordinals
        )
    elif selected_ordinals is None:
        by_issue = {result.issue_number: result for result in frozen_results}
        selected_issue_numbers = tuple(selected_issue_numbers)
        selected_ordinals = tuple(
            by_issue[number].ordinal
            if number in by_issue and by_issue[number].ordinal is not None
            else (_raise_unknown_issue(number))
            for number in selected_issue_numbers
        )
    else:
        selected_issue_numbers = tuple(selected_issue_numbers)
        selected_ordinals = tuple(selected_ordinals)
    selection = FrozenSelection(
        selected_issue_numbers=selected_issue_numbers,
        selected_ordinals=selected_ordinals,
        top_k=top_k,
        selection_protocol=selection_protocol,
    )
    return RunInputs(
        issues=tuple(FrozenIssueSnapshot.from_issue(issue) for issue in issues),
        as_of=as_of.astimezone(UTC),
        ranked_results=frozen_results,
        selection=selection,
        selection_protocol=selection_protocol,
    )


def _raise_unknown_ordinal(ordinal: int) -> int:
    raise ValueError(f"selected ranking ordinal is not present: {ordinal}")


def _raise_unknown_issue(number: int) -> int:
    raise ValueError(f"selected Issue is not present in ranking: {number}")


def thaw_issue_snapshots(issues: tuple[FrozenIssueSnapshot, ...]) -> list[IssueRecord]:
    """Return detached V1 Issue objects for a deliberately explicit adapter."""

    return [issue.to_issue() for issue in issues]


class RunV2(ProtocolV2Model):
    run_id: str
    parent_run_id: str | None
    snapshot: RepositorySnapshot
    configuration: RunConfiguration
    inputs: RunInputs
    selection: FrozenSelection
    status: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class IssueExecutionV2(ProtocolV2Model):
    run_id: str
    issue_number: int
    deterministic_state: str
    deterministic_report: FrozenDict | None
    evidence_set_id: str | None
    selected_analysis_attempt_id: str | None
    review_version: int
    llm_state: str
    attempt_count: int = 0
    analysis: FrozenDict | None = None


class EvidenceItemV2(ProtocolV2Model):
    evidence_id: str = Field(min_length=1, max_length=128)
    ordinal: int = Field(ge=0)
    candidate_rank: int | None = Field(default=None, ge=0)
    selection_kind: Literal["candidate", "alternate_symbol", "fallback"]
    file: str = Field(min_length=1)
    symbol: str | None = None
    requested_range: tuple[int, int]
    actual_range: tuple[int, int]
    truncation_reason: str | None = Field(default=None, min_length=1, max_length=128)
    char_count: int = Field(ge=0)
    content: str = Field(min_length=1)
    collector_protocol: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_metadata(self) -> Self:
        path = PurePosixPath(self.file)
        if path.is_absolute() or ".." in path.parts or "\\" in self.file:
            raise ValueError("evidence file must be analysis-relative")
        start, end = self.actual_range
        requested_start, requested_end = self.requested_range
        if not 1 <= requested_start <= start <= end <= requested_end:
            raise ValueError("actual evidence range must be within requested range")
        if len(self.content.splitlines()) != end - start + 1:
            raise ValueError("actual evidence range must describe stored content lines")
        if self.char_count != len(self.content):
            raise ValueError("evidence character count must match stored content")
        if self.actual_range != self.requested_range and not self.truncation_reason:
            raise ValueError("shortened evidence requires a truncation reason")
        return self


class EvidenceCollectionContext(ProtocolV2Model):
    snapshot_commit: str | None
    analysis_prefix: str = ""
    collector_protocol: str = Field(min_length=1, max_length=128)
    budget_chars: int | None = Field(default=None, ge=1)
    budget_lines: int | None = Field(default=None, ge=1)


class EvidenceSetV2(ProtocolV2Model):
    evidence_set_id: str
    run_id: str
    issue_number: int
    collection_context: EvidenceCollectionContext
    sealed_at: AwareDatetime
    items: tuple[EvidenceItemV2, ...]


class FrozenEvidenceSnippet(EvidenceSnippet):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceObservationV2(ProtocolV2Model):
    evidence_id: str = Field(min_length=1)
    alignment: EvidenceAlignment
    observation: str


class AnalysisHypothesisV2(ProtocolV2Model):
    description: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    missing_evidence: tuple[str, ...]
    validation_step: str = Field(min_length=1)


class AnalysisV2(ProtocolV2Model):
    """Local storage contract; provider normalization belongs to PR3."""

    summary: str = Field(min_length=1)
    issue_type: IssueType
    affected_component: str = Field(min_length=1)
    reproduction_completeness: ReproductionCompleteness
    evidence_observations: tuple[EvidenceObservationV2, ...]
    contradictions: tuple[str, ...]
    input_evidence_ids: tuple[str, ...] = Field(min_length=1)
    primary_evidence_id: str = Field(min_length=1)
    hypotheses: tuple[AnalysisHypothesisV2, ...] = Field(min_length=1)
    needs_more_evidence: bool

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        ids = set(self.input_evidence_ids)
        observed = tuple(item.evidence_id for item in self.evidence_observations)
        if (
            len(ids) != len(self.input_evidence_ids)
            or len(set(observed)) != len(observed)
            or set(observed) != ids
        ):
            raise ValueError("analysis requires unique input IDs and complete observation coverage")
        for hypothesis in self.hypotheses:
            if (
                len(set(hypothesis.evidence_ids)) != len(hypothesis.evidence_ids)
                or not set(hypothesis.evidence_ids) <= ids
            ):
                raise ValueError("hypothesis requires unique known evidence references")
        if self.primary_evidence_id != self.hypotheses[0].evidence_ids[0]:
            raise ValueError("primary evidence must be the first cited hypothesis reference")
        return self


class SafeMetadata(ProtocolV2Model):
    """Closed short observations, never provider diagnostics or raw payloads."""

    model_config = ConfigDict(hide_input_in_errors=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_safe_metadata(self) -> Self:
        from .run_configuration import _url_security_violation

        for value in self.model_dump().values():
            if isinstance(value, str) and (
                len(value) > 512
                or "\n" in value
                or "\r" in value
                or _url_security_violation(value) is not None
                or re.search(
                    r"(?i)\bbearer\s+\S+|(?:api[_-]?key|password|secret|authorization|"
                    r"access[_-]?token)\s*[:=]"
                    r"|(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{8,}",
                    value,
                )
            ):
                raise ValueError("metadata contains unsafe or oversized text")
        return self


class AttemptRequest(SafeMetadata):
    model: str | None = None
    provider: str | None = None
    backend: str = "api"
    temperature: float | None = None
    seed: int | None = None
    reasoning_effort: str | None = None
    service_tier: str | None = None
    max_output_tokens: int | None = Field(default=None, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0)
    response_format_json: bool | None = None


class AttemptError(SafeMetadata):
    category: Literal[
        "transport",
        "timeout",
        "provider",
        "invalid_response",
        "interrupted",
        "outcome_uncertain",
        "local",
    ]
    detail: str = Field(min_length=1, max_length=512)


class ReportedObservation(SafeMetadata):
    model: str | None = None
    provider: str | None = None
    temperature: float | None = None
    seed: int | None = None
    reasoning_effort: str | None = None
    service_tier: str | None = None
    request_id: str | None = None
    system_fingerprint: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class LocalObservation(SafeMetadata):
    elapsed_ms: float | None = Field(default=None, ge=0)
    exit_code: int | None = None
    category: Literal["completed", "transport", "timeout", "interrupted", "local"] | None = None
    invocation_id: str | None = None


class AttemptTerminalFields(ProtocolV2Model):
    model_config = ConfigDict(hide_input_in_errors=True)

    state: Literal["success", "failure", "unknown"]
    analysis: AnalysisV2 | None = None
    error: AttemptError | None = None
    reported: ReportedObservation | None = None
    local: LocalObservation | None = None

    @model_validator(mode="after")
    def validate_terminal_fields(self) -> Self:
        if self.state == "success":
            if self.analysis is None or self.error is not None:
                raise ValueError("successful attempt requires analysis and no error")
        elif self.analysis is not None or self.error is None:
            raise ValueError("failed or unknown attempt requires error and no analysis")
        if self.state == "unknown" and self.error.category not in {
            "interrupted",
            "outcome_uncertain",
        }:
            raise ValueError("unknown attempt requires an explicit uncertain-outcome reason")
        return self


class AttemptV2(ProtocolV2Model):
    attempt_id: str
    run_id: str
    issue_number: int
    evidence_set_id: str
    ordinal: int
    request: AttemptRequest
    started_at: AwareDatetime
    state: Literal["in_progress", "success", "failure", "unknown"]
    finished_at: AwareDatetime | None
    analysis: AnalysisV2 | None
    error: AttemptError | None
    reported: ReportedObservation | None
    local: LocalObservation | None


class TracePayloadV2(SafeMetadata):
    event: Literal[
        "run_created",
        "deterministic_started",
        "deterministic_completed",
        "evidence_sealed",
        "attempt_started",
        "attempt_finished",
        "run_interrupted",
    ]
    issue_number: int | None = Field(default=None, ge=1)
    evidence_set_id: str | None = Field(default=None, min_length=1, max_length=128)
    attempt_id: str | None = Field(default=None, min_length=1, max_length=128)
    item_count: int | None = Field(default=None, ge=0)
    elapsed_ms: float | None = Field(default=None, ge=0)


class TraceV2(ProtocolV2Model):
    trace_id: str
    run_id: str
    payload: TracePayloadV2
    created_at: AwareDatetime


__all__ = [
    "AnalysisHypothesisV2",
    "AnalysisV2",
    "AttemptError",
    "AttemptRequest",
    "AttemptTerminalFields",
    "AttemptV2",
    "BudgetConfiguration",
    "ClientConfiguration",
    "EngineRuntime",
    "EvidenceCollectionContext",
    "EvidenceItemV2",
    "EvidenceObservationV2",
    "EvidenceSetV2",
    "FrozenDict",
    "FrozenEvidenceSnippet",
    "FrozenIssueSnapshot",
    "FrozenPriorityResult",
    "FrozenScoreFactors",
    "FrozenSelection",
    "ParameterOrigin",
    "IssueExecutionV2",
    "LocalObservation",
    "ProtocolConfiguration",
    "ProtocolV2Model",
    "RepositoryCaptureMode",
    "RepositoryFileRecord",
    "RepositoryFileStatus",
    "RepositorySnapshot",
    "RepositoryStatusEntry",
    "ReportedObservation",
    "RunConfiguration",
    "RunInputs",
    "RunV2",
    "TracePayloadV2",
    "TraceV2",
    "freeze_mapping",
    "freeze_run_inputs",
    "thaw_issue_snapshots",
]
