from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    github_token: str | None = None
    agent_db_path: Path = Path("data/agent-runs.sqlite3")
    opencode_api_key: SecretStr | None = None
    llm_max_evidence_chars: int = 16_000
    opencode_max_output_tokens: int = 4_096
    opencode_timeout_seconds: float = 60.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
