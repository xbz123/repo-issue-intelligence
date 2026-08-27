import pytest
from pydantic import ValidationError

from repo_issue_intelligence.config import Settings


def test_settings_loads_github_token_from_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GITHUB_TOKEN=example-token\n", encoding="utf-8")

    settings = Settings()

    assert settings.github_token == "example-token"


def test_settings_keeps_opencode_key_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "OPENCODE_API_KEY=test-opencode-secret\n",
        encoding="utf-8",
    )

    settings = Settings()

    assert settings.opencode_api_key is not None
    assert settings.opencode_api_key.get_secret_value() == "test-opencode-secret"
    assert settings.opencode_max_output_tokens == 20_000
    assert settings.opencode_timeout_seconds == 180
    assert settings.opencode_temperature == 0.1
    assert settings.opencode_max_evidence_chars == 100_000
    assert settings.opencode_max_lines_per_evidence == 200
    assert "test-opencode-secret" not in repr(settings)


@pytest.mark.parametrize(
    "setting",
    [
        "OPENCODE_MAX_OUTPUT_TOKENS=0",
        "OPENCODE_TIMEOUT_SECONDS=0",
    ],
)
def test_settings_rejects_non_positive_provider_limits(
    tmp_path,
    monkeypatch,
    setting: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"{setting}\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        Settings()
