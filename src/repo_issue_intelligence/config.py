from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    github_token: str | None = None
    agent_db_path: Path = Path("data/agent-runs.sqlite3")
    groq_api_key: SecretStr | None = None
    groq_api_key_fallback: SecretStr | None = None
    llm_model: str = "openai/gpt-oss-20b"
    llm_max_evidence_chars: int = 16_000
    llm_max_output_tokens: int = 1_600
    llm_timeout_seconds: float = 30.0
    llm_reasoning_effort: Literal["low", "medium", "high"] = "low"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
