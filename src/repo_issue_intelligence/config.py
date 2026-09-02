from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .evidence import DEFAULT_MAX_LINES_PER_SNIPPET, DEFAULT_MAX_TOTAL_CHARS


class Settings(BaseSettings):
    github_token: str | None = None
    agent_db_path: Path = Path("data/agent-runs.sqlite3")
    llm_backend: str = Field(default="api", pattern=r"^(api|codex-cli)$")
    llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "llm_api_key",
            "LLM_API_KEY",
            "opencode_api_key",
            "OPENCODE_API_KEY",
        ),
    )
    llm_api_base_url: str = Field(
        default="https://opencode.ai/zen/go/v1",
        validation_alias=AliasChoices(
            "llm_api_base_url",
            "LLM_API_BASE_URL",
            "opencode_api_base_url",
            "OPENCODE_API_BASE_URL",
        ),
    )
    llm_api_provider: str = Field(
        default="opencode",
        validation_alias=AliasChoices(
            "llm_api_provider",
            "LLM_API_PROVIDER",
            "opencode_provider",
            "OPENCODE_PROVIDER",
        ),
        min_length=1,
    )
    llm_model: str = Field(
        default="deepseek-v4-flash",
        validation_alias=AliasChoices(
            "llm_model",
            "LLM_MODEL",
            "opencode_model",
            "OPENCODE_MODEL",
        ),
        min_length=1,
    )
    llm_max_output_tokens: int = Field(
        default=20_000,
        ge=1,
        validation_alias=AliasChoices(
            "LLM_MAX_OUTPUT_TOKENS",
            "opencode_max_output_tokens",
            "OPENCODE_MAX_OUTPUT_TOKENS",
        ),
    )
    llm_timeout_seconds: float = Field(
        default=180.0,
        gt=0,
        validation_alias=AliasChoices(
            "LLM_TIMEOUT_SECONDS",
            "opencode_timeout_seconds",
            "OPENCODE_TIMEOUT_SECONDS",
        ),
    )
    llm_temperature: float = Field(
        default=0.1,
        ge=0,
        le=2,
        validation_alias=AliasChoices(
            "llm_temperature",
            "LLM_TEMPERATURE",
            "opencode_temperature",
            "OPENCODE_TEMPERATURE",
        ),
    )
    llm_reasoning_effort: str | None = Field(
        default="none",
        validation_alias=AliasChoices(
            "LLM_REASONING_EFFORT",
            "opencode_reasoning_effort",
            "OPENCODE_REASONING_EFFORT",
        ),
    )
    llm_response_format_json: bool = True
    llm_max_evidence_chars: int = Field(
        default=DEFAULT_MAX_TOTAL_CHARS,
        ge=1,
        validation_alias=AliasChoices(
            "LLM_MAX_EVIDENCE_CHARS",
            "opencode_max_evidence_chars",
            "OPENCODE_MAX_EVIDENCE_CHARS",
        ),
    )
    llm_max_lines_per_evidence: int = Field(
        default=DEFAULT_MAX_LINES_PER_SNIPPET,
        ge=1,
        validation_alias=AliasChoices(
            "LLM_MAX_LINES_PER_EVIDENCE",
            "opencode_max_lines_per_evidence",
            "OPENCODE_MAX_LINES_PER_EVIDENCE",
        ),
    )
    codex_cli_executable: str = Field(default="codex", min_length=1)
    codex_cli_model: str = Field(default="gpt-5.6-luna", min_length=1)
    codex_cli_reasoning_effort: str = Field(default="medium", min_length=1)
    codex_cli_timeout_seconds: float = Field(default=180.0, gt=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def opencode_api_key(self) -> SecretStr | None:
        return self.llm_api_key

    @property
    def opencode_max_output_tokens(self) -> int:
        return self.llm_max_output_tokens

    @property
    def opencode_timeout_seconds(self) -> float:
        return self.llm_timeout_seconds

    @property
    def opencode_temperature(self) -> float:
        return self.llm_temperature

    @property
    def opencode_max_evidence_chars(self) -> int:
        return self.llm_max_evidence_chars

    @property
    def opencode_max_lines_per_evidence(self) -> int:
        return self.llm_max_lines_per_evidence
