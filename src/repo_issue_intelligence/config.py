from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    github_token: str | None = None
    agent_db_path: Path = Path("data/agent-runs.sqlite3")
    opencode_api_key: SecretStr | None = None
    opencode_max_output_tokens: int = 20_000
    opencode_timeout_seconds: float = 60.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
