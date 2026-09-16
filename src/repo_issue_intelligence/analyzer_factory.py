"""Shared Settings-to-analyzer mapping, independent of CLI and HTTP boundaries."""

from typing import Literal

from .codex_cli import CodexCLIIssueAnalyzer
from .config import Settings
from .llm_client import OpenAICompatibleIssueAnalyzer


def build_issue_analyzer(
    settings: Settings,
    *,
    backend: Literal["api", "codex-cli"] | None = None,
    model: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
    fast: bool = False,
    temperature: float | None = None,
    seed: int | None = None,
    omit_max_tokens: bool = False,
    timeout_seconds: float | None = None,
) -> OpenAICompatibleIssueAnalyzer | CodexCLIIssueAnalyzer:
    """Construct from server settings or already-validated CLI overrides; never dispatch."""
    selected = backend or settings.llm_backend
    if selected == "codex-cli":
        return CodexCLIIssueAnalyzer(
            executable=settings.codex_cli_executable,
            model=settings.codex_cli_model if model is None else model,
            timeout_seconds=timeout_seconds or settings.codex_cli_timeout_seconds,
            reasoning_effort=settings.codex_cli_reasoning_effort,
            service_tier="fast" if fast else None,
        )
    if selected != "api":
        raise ValueError("Unsupported analyzer backend")
    if settings.llm_api_key is None:
        raise ValueError("API key is required")
    return OpenAICompatibleIssueAnalyzer(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_api_base_url if base_url is None else base_url,
        model=settings.llm_model if model is None else model,
        provider=settings.llm_api_provider if provider is None else provider,
        max_output_tokens=None if omit_max_tokens else settings.llm_max_output_tokens,
        timeout_seconds=timeout_seconds or settings.llm_timeout_seconds,
        temperature=settings.llm_temperature if temperature is None else temperature,
        seed=seed,
        reasoning_effort=settings.llm_reasoning_effort,
        response_format_json=settings.llm_response_format_json,
    )
