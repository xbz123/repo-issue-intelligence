from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .evidence import DEFAULT_MAX_LINES_PER_SNIPPET, DEFAULT_MAX_TOTAL_CHARS


class Settings(BaseSettings):
    github_token: str | None = None
    agent_db_path: Path = Path("data/agent-runs.sqlite3")
    opencode_api_key: SecretStr | None = None
    opencode_max_output_tokens: int = Field(default=20_000, ge=1)
    opencode_timeout_seconds: float = Field(default=180.0, gt=0)
    opencode_temperature: float = Field(default=0.1, ge=0, le=2)
    opencode_max_evidence_chars: int = Field(
        default=DEFAULT_MAX_TOTAL_CHARS,
        ge=1,
    )
    opencode_max_lines_per_evidence: int = Field(
        default=DEFAULT_MAX_LINES_PER_SNIPPET,
        ge=1,
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
