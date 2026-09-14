"""HTTP-only authorization adapter around the transport-independent PR5B service."""

from fastapi import HTTPException, Request

from . import agent_queries
from .agent_resume import retry_issue_llm
from .agent_store_v2 import AgentStoreV2
from .api_security import authorize_repository_operation, revalidate_principal
from .codex_cli import CodexCLIIssueAnalyzer
from .config import Settings
from .issue_execution import capture_execution_configuration
from .llm_client import OpenAICompatibleIssueAnalyzer


def _server_analyzer(settings: Settings):
    # HTTP accepts no analyzer overrides; current server configuration must match the run.
    if settings.llm_backend == "codex-cli":
        return CodexCLIIssueAnalyzer(
            executable=settings.codex_cli_executable,
            model=settings.codex_cli_model,
            timeout_seconds=settings.codex_cli_timeout_seconds,
            reasoning_effort=settings.codex_cli_reasoning_effort,
        )
    if not settings.llm_api_key:
        raise ValueError("Server analyzer unavailable")
    return OpenAICompatibleIssueAnalyzer(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_api_base_url,
        model=settings.llm_model,
        provider=settings.llm_api_provider,
        max_output_tokens=settings.llm_max_output_tokens,
        timeout_seconds=settings.llm_timeout_seconds,
        temperature=settings.llm_temperature,
        reasoning_effort=settings.llm_reasoning_effort,
        response_format_json=settings.llm_response_format_json,
    )


def retry_from_http(
    request: Request, store: AgentStoreV2, run_id: str, issue_number: int, *, recover_unknown: bool
) -> agent_queries.QueryResponse:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    operation = "recover-unknown" if recover_unknown else "retry"
    provider = run.configuration.client.provider
    if run.configuration.client.backend == "codex-cli":
        provider = "codex-cli"
    settings = Settings()
    analyzer_settings = {
        key: value
        for key, value in settings.model_dump().items()
        if key.startswith(("llm_", "codex_"))
    }

    def authorize_attempt():
        principal = revalidate_principal(request)
        authorize_repository_operation(
            principal, run.snapshot.analysis_root, operation, provider=provider
        )
        current = Settings()
        if any(getattr(current, key) != value for key, value in analyzer_settings.items()):
            raise HTTPException(409, "Server analyzer configuration changed")

    authorize_attempt()
    if store.get_issue(run_id, issue_number) is None:
        raise HTTPException(404, "Issue not found")
    analyzer = _server_analyzer(settings)
    try:
        current = capture_execution_configuration(
            analyzer,
            top_k=run.configuration.protocol.top_k,
            max_evidence_chars=settings.llm_max_evidence_chars,
            max_evidence_lines=settings.llm_max_lines_per_evidence,
            max_attempts=run.configuration.budgets.retry_policy["max_attempts"],
            parameter_origins=run.configuration.parameter_origins,
        )
        result = retry_issue_llm(
            run_id,
            issue_number,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            current_configuration=current,
            recover_unknown=recover_unknown,
            before_provider_attempt=authorize_attempt,
        )
        authorize_attempt()
        # Source, reports, analyses and raw provider errors are not part of this response.
        return {
            "protocol": "v2",
            "run_id": run_id,
            "issue_number": issue_number,
            "llm_state": result.llm_state,
            "selected_analysis_attempt_id": result.selected_analysis_attempt_id,
            "evidence_set_id": result.evidence_set_id,
            "review_version": result.review_version,
            "duplicate_remote_execution_possible": recover_unknown,
        }
    finally:
        analyzer.close()
