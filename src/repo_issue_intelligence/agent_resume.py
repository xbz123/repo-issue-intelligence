"""Strict recovery of committed Protocol v2 work; no implicit provider replay."""

from contextlib import ExitStack, contextmanager

from . import run_configuration
from .agent_store_v2 import AgentStoreV2, StoreConflict, StoreError
from .analysis_contract import ANALYSIS_V2_PROMPT_VERSION
from .issue_execution import (
    IssueAnalyzerV2,
    _assert_current_configuration,
    analyze_sealed_issue,
    process_issue,
)
from .protocol_v2_models import (
    AttemptError,
    AttemptTerminalFields,
    IssueExecutionV2,
    RunConfiguration,
    RunV2,
)
from .repository_index import REPOSITORY_MAP_INDEX_VERSION, build_repository_map
from .repository_view import prepare_repository_view


@contextmanager
def _recovery_execution(run: RunV2, store: AgentStoreV2, attempts):
    # Prove local stop before mutation; keep guards through settlement and execution.
    with ExitStack() as guards:
        for attempt in attempts:
            guards.enter_context(store.execution_lock(attempt.attempt_id))
        try:
            run = store.set_run_status(run.run_id, "RUNNING", expected_status=run.status)
            for attempt in attempts:
                if attempt.state == "in_progress":
                    store.finalize_attempt(
                        attempt.attempt_id,
                        AttemptTerminalFields(
                            state="unknown",
                            error=AttemptError(
                                category="interrupted", detail="Local guarded execution stopped"
                            ),
                        ),
                    )
            yield run
        except (Exception, KeyboardInterrupt, SystemExit) as error:
            try:
                store.set_run_status(
                    run.run_id,
                    "INTERRUPTED"
                    if isinstance(error, (KeyboardInterrupt, SystemExit))
                    else "FAILED",
                    expected_status="RUNNING",
                )
            except StoreError:
                error.add_note("Recovery failure could not be persisted")
            raise


def validate_recovery_configuration(
    run: RunV2,
    analyzer: IssueAnalyzerV2 | None,
    *,
    allow_external_llm: bool,
    current_configuration: RunConfiguration | None = None,
) -> None:
    """Validate actual local input to recovery, never historical provider observations."""
    frozen = run.configuration
    current = run_configuration.capture_engine_runtime()
    for runtime in (frozen.engine, current):
        if (
            runtime.source_dirty is not False
            or runtime.source_provenance != "git"
            or not runtime.source_revision
            or not runtime.package_version
        ):
            raise run_configuration.RunConfigurationError(
                "Recovery requires a known clean engine; create a new run"
            )
    if frozen.engine != current:
        raise run_configuration.RunConfigurationError("Engine runtime changed; create a new run")
    protocol = frozen.protocol
    if (
        protocol.prompt_version != ANALYSIS_V2_PROMPT_VERSION
        or protocol.index_version != f"repository-index-v{REPOSITORY_MAP_INDEX_VERSION}"
        or protocol.provider_schema_version != "provider-schema-v1"
        or protocol.analysis_protocol_version != "analysis-v2"
        or protocol.retrieval_protocol_version != "retrieval-v1"
        or protocol.selection_protocol != run.inputs.selection_protocol
    ):
        raise run_configuration.RunConfigurationError("Protocol changed; create a new run")
    if current_configuration is not None and frozen.model_dump(exclude={"captured_at"}) != (
        current_configuration.model_dump(exclude={"captured_at"})
    ):
        raise run_configuration.RunConfigurationError("Configuration changed; create a new run")
    if frozen.llm_enabled:
        if not allow_external_llm or analyzer is None:
            raise ValueError("Recovery requires current explicit external LLM permission")
        _assert_current_configuration(run, analyzer)
    elif analyzer is not None:
        raise run_configuration.RunConfigurationError("Disabled run cannot acquire an analyzer")


def _finish_recovery(run_id: str, store: AgentStoreV2) -> RunV2:
    issues = store.list_issues(run_id)
    status = (
        "FAILED"
        if any(item.deterministic_state == "failed" for item in issues)
        else "INTERRUPTED"
        if any(
            item.deterministic_state != "succeeded"
            or item.llm_state in {"pending", "in_progress", "interrupted_unknown"}
            or (
                item.selected_analysis_attempt_id is None
                and any(
                    attempt.state == "unknown"
                    for attempt in store.list_attempts(run_id, item.issue_number)
                )
            )
            for item in issues
        )
        else "AWAITING_REVIEW"
    )
    return store.set_run_status(run_id, status, expected_status="RUNNING")


def resume_agent_run(
    run_id: str,
    store: AgentStoreV2,
    *,
    llm_analyzer: IssueAnalyzerV2 | None = None,
    allow_external_llm: bool = False,
    current_configuration: RunConfiguration | None = None,
) -> RunV2:
    """Continue only unfinished committed-input stages under the foreground lock."""
    with store.writer_lock():
        run = store.get_run(run_id)
        if run is None:
            raise StoreError("Run is unavailable")
        validate_recovery_configuration(
            run,
            llm_analyzer,
            allow_external_llm=allow_external_llm,
            current_configuration=current_configuration,
        )
        if run.status not in {"RUNNING", "INTERRUPTED", "FAILED"}:
            raise StoreConflict("Run does not have interrupted work")
        abandoned = [
            attempt
            for number in run.selection.selected_issue_numbers
            for attempt in store.list_attempts(run_id, number)
            if attempt.state == "in_progress"
        ]
        with (
            prepare_repository_view(run.snapshot, deterministic_resume=True) as view,
            _recovery_execution(run, store, abandoned) as run,
        ):
            repository_map = build_repository_map(view)
            by_number = {issue.number: issue for issue in run.inputs.issues}
            for number in run.selection.selected_issue_numbers:
                process_issue(
                    run,
                    by_number[number].to_issue(),
                    repository_map,
                    view,
                    store,
                    llm_analyzer,
                    resume=True,
                )
            return _finish_recovery(run_id, store)


def retry_issue_llm(
    run_id: str,
    issue_number: int,
    store: AgentStoreV2,
    *,
    llm_analyzer: IssueAnalyzerV2,
    allow_external_llm: bool = False,
    current_configuration: RunConfiguration | None = None,
    recover_unknown: bool = False,
) -> IssueExecutionV2:
    """Explicit same-configuration retry without sync, ranking or repository reads."""
    with store.writer_lock():
        run = store.get_run(run_id)
        if run is None:
            raise StoreError("Run is unavailable")
        validate_recovery_configuration(
            run,
            llm_analyzer,
            allow_external_llm=allow_external_llm,
            current_configuration=current_configuration,
        )
        issue = store.get_issue(run_id, issue_number)
        attempts = store.list_attempts(run_id, issue_number)
        if (
            issue is None
            or issue.review_version
            or issue.selected_analysis_attempt_id
            or not attempts
            or attempts[-1].state
            not in ({"failure", "unknown", "in_progress"} if recover_unknown else {"failure"})
        ):
            raise StoreConflict("Retry requires an explicitly failed, unreviewed Issue")
        uncertain = [a for a in attempts if a.state in {"unknown", "in_progress"}]
        if uncertain and not recover_unknown:
            raise StoreConflict("Unknown history requires explicit recover_unknown authorization")
        with _recovery_execution(run, store, uncertain) as run:
            record = next(item for item in run.inputs.issues if item.number == issue_number)
            result = analyze_sealed_issue(
                run,
                record.to_issue(),
                store,
                llm_analyzer,
                retry=True,
                recover_unknown=recover_unknown,
            )
            _finish_recovery(run_id, store)
            return result
