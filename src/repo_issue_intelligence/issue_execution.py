"""Foreground Protocol v2 execution; each Issue commits before the next starts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from time import perf_counter, sleep
from typing import Protocol

from .agent_store_v2 import AgentStoreV2, StoreError
from .analysis_contract import ANALYSIS_V2_PROMPT_VERSION, EvidenceLookup
from .analysis_observations import AnalysisResultV2
from .evidence import DEFAULT_MAX_LINES_PER_SNIPPET, DEFAULT_MAX_TOTAL_CHARS, collect_evidence_v2
from .investigator import investigate
from .llm_client import LLMProviderError
from .models import InvestigationReport, IssueRecord, RepositoryMap
from .protocol_v2_models import (
    AnalysisV2,
    AttemptError,
    AttemptRequest,
    AttemptTerminalFields,
    EvidenceCollectionContext,
    IssueExecutionV2,
    LocalObservation,
    ReportedObservation,
    RepositoryCaptureMode,
    RunV2,
    TracePayloadV2,
    freeze_mapping,
    freeze_run_inputs,
)
from .repository_context import RepositoryContextError, capture_repository_context
from .repository_index import REPOSITORY_MAP_INDEX_VERSION, build_repository_map
from .repository_view import RepositoryView, RepositoryViewError, prepare_repository_view
from .run_configuration import (
    RunConfigurationError,
    capture_requested_run_configuration,
    normalize_endpoint,
)
from .service import rank_issues


class IssueAnalyzerV2(Protocol):
    def requested_configuration_v2(self) -> dict[str, object]: ...

    def analyze_v2(
        self,
        issue: IssueRecord,
        report: InvestigationReport,
        input_evidence_ids: tuple[str, ...],
        evidence_lookup: EvidenceLookup,
    ) -> AnalysisResultV2: ...


def _failure_category(error: BaseException) -> str:
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        return "interrupted"
    if isinstance(error, StoreError):
        return "store"
    if isinstance(error, (RepositoryContextError, RepositoryViewError)):
        return "repository"
    if isinstance(error, RunConfigurationError):
        return "configuration"
    return "programming"


def _attempt_request(run: RunV2) -> AttemptRequest:
    configuration = run.configuration
    values = {
        key: value
        for key, value in configuration.request_parameters.items()
        if key in AttemptRequest.model_fields
    }
    values.update(backend=configuration.client.backend, provider=configuration.client.provider)
    if configuration.parameter_origins.get("requested_model") != "omitted":
        values["model"] = configuration.requested_model
    return AttemptRequest.model_validate(values)


def _assert_current_configuration(run: RunV2, analyzer: IssueAnalyzerV2) -> AttemptRequest:
    request = _attempt_request(run)
    if analyzer.requested_configuration_v2() != request.model_dump(exclude_unset=True):
        raise RunConfigurationError("Analyzer request differs from the frozen run configuration")
    client = run.configuration.client
    if (
        normalize_endpoint(getattr(analyzer, "base_url", None)) != client.endpoint
        or getattr(analyzer, "executable", None) != client.cli_executable
    ):
        raise RunConfigurationError(
            "Analyzer destination differs from the frozen run configuration"
        )
    return request


def process_issue(
    run: RunV2,
    issue: IssueRecord,
    repository_map: RepositoryMap,
    repository_view: RepositoryView,
    store: AgentStoreV2,
    llm_analyzer: IssueAnalyzerV2 | None = None,
    *,
    before_provider_case: Callable[[], None] | None = None,
) -> IssueExecutionV2:
    store.set_deterministic_state(run.run_id, issue.number, "running")
    try:
        report = investigate(issue, repository_map).model_copy(
            update={"repository_root": run.snapshot.analysis_root}
        )
    except Exception as error:
        try:
            store.set_deterministic_state(run.run_id, issue.number, "failed")
            store.append_trace(
                run.run_id,
                TracePayloadV2(
                    event="deterministic_failed",
                    issue_number=issue.number,
                    failure_category=_failure_category(error),
                ),
            )
        except StoreError:
            error.add_note("Deterministic failure could not be persisted")
        raise
    result = store.save_deterministic_result(run.run_id, issue.number, report)
    if not run.configuration.llm_enabled:
        return result
    if llm_analyzer is None:
        raise ValueError("The frozen run requires an LLM analyzer")
    budgets = run.configuration.budgets
    evidence = store.seal_evidence_set(
        run.run_id,
        issue.number,
        collect_evidence_v2(
            report,
            max_total_chars=budgets.evidence_chars,
            max_lines_per_snippet=budgets.evidence_lines,
            repository_view=repository_view,
        ),
        EvidenceCollectionContext(
            snapshot_commit=run.snapshot.commit_oid,
            analysis_prefix=run.snapshot.analysis_prefix,
            collector_protocol="v2",
            budget_chars=budgets.evidence_chars,
            budget_lines=budgets.evidence_lines,
        ),
    )
    if not evidence.items:
        return store.get_issue(run.run_id, issue.number)
    input_ids, lookup = store.evidence_lookup(run.run_id, issue.number)
    policy = budgets.retry_policy
    for ordinal in range(policy["max_attempts"]):
        request = _assert_current_configuration(run, llm_analyzer)
        if ordinal == 0 and before_provider_case is not None:
            before_provider_case()
            request = _assert_current_configuration(run, llm_analyzer)
        attempt = store.start_attempt(run.run_id, issue.number, evidence.evidence_set_id, request)
        started = perf_counter()
        try:
            response = llm_analyzer.analyze_v2(issue, report, input_ids, lookup)
            terminal = AttemptTerminalFields(
                state="success",
                analysis=AnalysisV2.model_validate(response.analysis.model_dump()),
                reported=ReportedObservation.model_validate(response.reported),
                local=LocalObservation.model_validate(response.local),
            )
        except LLMProviderError as error:
            observations = getattr(error, "observations", {})
            local = LocalObservation.model_validate(observations.get("local", {}))
            uncertain = error.category in {"transport", "timeout", "interrupted"} and (
                local.http_status is None
            )
            if uncertain:
                local = local.model_copy(update={"category": error.category})
            category = (
                "invalid_response"
                if error.category
                in {
                    "invalid_response",
                    "invalid_json",
                    "schema_validation",
                    "output_truncated",
                    "evidence_validation",
                    "unknown_evidence_id",
                    "evidence_observation_coverage",
                    "missing_output",
                    "output_read",
                    "cli_encoding",
                }
                else "provider"
            )
            terminal = AttemptTerminalFields(
                state="unknown" if uncertain else "failure",
                error=AttemptError(
                    category="outcome_uncertain" if uncertain else category,
                    detail="Remote outcome is unknown" if uncertain else "Provider analysis failed",
                ),
                reported=ReportedObservation.model_validate(observations.get("reported", {})),
                local=local,
            )
            store.finalize_attempt(attempt.attempt_id, terminal)
            if (
                uncertain
                or not error.retryable
                or error.category not in policy["categories"]
                or not (
                    local.http_status == 429
                    or (local.http_status or 0) >= 500
                    or (request.backend == "codex-cli" and local.exit_code not in (None, 0))
                )
                or ordinal + 1 >= policy["max_attempts"]
            ):
                break
            delay = policy["backoff"][min(ordinal, len(policy["backoff"]) - 1)]
            if error.retry_after is not None:
                if not isfinite(error.retry_after) or error.retry_after > 60:
                    break
                delay = max(delay, error.retry_after)
            if delay > 60:
                break
            sleep(delay)
        except (Exception, KeyboardInterrupt, SystemExit) as error:
            interrupted = isinstance(error, (KeyboardInterrupt, SystemExit))
            try:
                store.finalize_attempt(
                    attempt.attempt_id,
                    AttemptTerminalFields(
                        state="unknown" if interrupted else "failure",
                        error=AttemptError(
                            category="interrupted" if interrupted else "local",
                            detail="Local analysis interrupted"
                            if interrupted
                            else "Local analysis failed",
                        ),
                        local=LocalObservation(
                            category="interrupted" if interrupted else "local",
                            elapsed_ms=(perf_counter() - started) * 1000,
                        ),
                    ),
                )
            except StoreError:
                error.add_note("Local attempt failure could not be persisted")
            raise
        else:
            store.finalize_attempt(attempt.attempt_id, terminal)
            break
    return store.get_issue(run.run_id, issue.number)


def run_agent_v2(
    issues: list[IssueRecord],
    repository_root: Path,
    top_k: int,
    store: AgentStoreV2,
    *,
    llm_analyzer: IssueAnalyzerV2 | None = None,
    allow_external_llm: bool = False,
    capture_mode: RepositoryCaptureMode = RepositoryCaptureMode.COMMITTED,
    max_evidence_chars: int | None = DEFAULT_MAX_TOTAL_CHARS,
    max_evidence_lines: int | None = DEFAULT_MAX_LINES_PER_SNIPPET,
    max_attempts: int = 2,
    as_of: datetime | None = None,
    run_id: str | None = None,
    parameter_origins: Mapping[str, str] | None = None,
    before_provider_case: Callable[[], None] | None = None,
) -> RunV2:
    if not issues or len({issue.number for issue in issues}) != len(issues):
        raise ValueError("At least one Issue with unique numbers is required")
    if type(top_k) is not int or top_k < 1:
        raise ValueError("top_k must be a positive integer")
    if llm_analyzer is not None and not allow_external_llm:
        raise ValueError("External LLM transmission requires current explicit permission")
    frozen_as_of = as_of or datetime.now(UTC)
    inputs = freeze_run_inputs(
        issues,
        as_of=frozen_as_of,
        ranked_results=rank_issues(issues, as_of=frozen_as_of),
        top_k=top_k,
    )
    requested = {} if llm_analyzer is None else llm_analyzer.requested_configuration_v2()
    if llm_analyzer is not None:
        AttemptRequest.model_validate(requested)
    client = {
        **requested,
        "endpoint": getattr(llm_analyzer, "base_url", None),
    }
    executable = getattr(llm_analyzer, "executable", None)
    if executable is not None:
        client["cli_executable"] = executable
    budgets = {
        "evidence_chars": max_evidence_chars,
        "evidence_lines": max_evidence_lines,
        "retry_policy": {
            "max_attempts": max_attempts,
            "categories": ["rate_limit", "server_error"],
            "backoff": [1.0],
        },
    }
    for request_key, budget_key in (
        ("timeout_seconds", "timeout_seconds"),
        ("max_output_tokens", "output_tokens"),
    ):
        if request_key in requested:
            budgets[budget_key] = requested[request_key]
    configuration = capture_requested_run_configuration(
        client={**client, **budgets},
        budgets=budgets,
        parameter_origins=parameter_origins,
        protocol={
            "top_k": top_k,
            "prompt_version": ANALYSIS_V2_PROMPT_VERSION,
            "index_version": f"repository-index-v{REPOSITORY_MAP_INDEX_VERSION}",
        },
    )
    origins = dict(configuration.parameter_origins)
    for name in ("evidence_chars", "evidence_lines", "retry_policy"):
        origins[name] = (parameter_origins or {}).get(name, "client_default")
    for request_key, budget_key in (
        ("timeout_seconds", "timeout_seconds"),
        ("max_output_tokens", "output_tokens"),
    ):
        if request_key in requested:
            origin = (parameter_origins or {}).get(request_key, "client_default")
            origins[request_key] = origins[budget_key] = origin
    configuration = configuration.model_copy(
        update={
            "llm_enabled": llm_analyzer is not None,
            "parameter_origins": freeze_mapping(origins),
        }
    )
    with store.writer_lock():
        snapshot = capture_repository_context(repository_root, mode=capture_mode)
        run = store.create_run(snapshot, configuration, inputs, run_id=run_id)
        try:
            with prepare_repository_view(snapshot) as view:
                repository_map = build_repository_map(view)
                by_number = {issue.number: issue for issue in inputs.issues}
                for number in inputs.selection.selected_issue_numbers:
                    process_issue(
                        run,
                        by_number[number].to_issue(),
                        repository_map,
                        view,
                        store,
                        llm_analyzer,
                        before_provider_case=before_provider_case,
                    )
        except (Exception, KeyboardInterrupt, SystemExit) as error:
            interrupted = isinstance(error, (KeyboardInterrupt, SystemExit))
            try:
                store.set_run_status(
                    run.run_id,
                    "INTERRUPTED" if interrupted else "FAILED",
                    expected_status="RUNNING",
                )
                store.append_trace(
                    run.run_id,
                    TracePayloadV2(
                        event="run_interrupted" if interrupted else "run_failed",
                        failure_category=_failure_category(error),
                    ),
                )
            except StoreError:
                error.add_note("Run failure could not be persisted")
            raise
        return store.set_run_status(run.run_id, "AWAITING_REVIEW", expected_status="RUNNING")
