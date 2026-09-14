import pytest
from pydantic import ValidationError

from repo_issue_intelligence.config import Settings


def test_empty_github_token_environment_keeps_requests_unauthenticated(monkeypatch):
    import httpx

    from repo_issue_intelligence.github_client import GitHubClient

    monkeypatch.setenv("GITHUB_TOKEN", "")
    settings = Settings(_env_file=None)
    assert settings.github_token is not None
    assert settings.github_token.get_secret_value() == ""

    def handler(request):
        assert "authorization" not in request.headers
        return httpx.Response(200, json=[])

    client = GitHubClient(
        settings.github_token, transport=httpx.MockTransport(handler), trust_env=False
    )
    try:
        assert client.fetch_open_issues("example/project") == []
    finally:
        client.close()


def test_github_token_is_redacted_but_authorization_uses_its_value():
    import httpx

    from repo_issue_intelligence.github_client import GitHubClient

    settings = Settings(_env_file=None, github_token="synthetic-private-token")
    assert "synthetic-private-token" not in repr(settings)
    assert "synthetic-private-token" not in str(settings.model_dump())
    assert "synthetic-private-token" not in settings.model_dump_json()

    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-private-token"
        return httpx.Response(200, json=[])

    client = GitHubClient(
        settings.github_token, transport=httpx.MockTransport(handler), trust_env=False
    )
    try:
        assert client.fetch_open_issues("example/project") == []
    finally:
        client.close()


def test_settings_loads_github_token_from_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GITHUB_TOKEN=example-token\n", encoding="utf-8")

    settings = Settings()

    assert settings.github_token.get_secret_value() == "example-token"


def test_settings_keeps_opencode_key_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "OPENCODE_API_KEY=test-opencode-secret\n",
        encoding="utf-8",
    )

    settings = Settings()

    assert settings.opencode_api_key is not None
    assert settings.opencode_api_key.get_secret_value() == "test-opencode-secret"
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "test-opencode-secret"
    assert settings.llm_api_base_url == "https://opencode.ai/zen/go/v1"
    assert settings.llm_api_provider == "opencode"
    assert settings.llm_model == "deepseek-v4-flash"
    assert settings.opencode_max_output_tokens == 20_000
    assert settings.opencode_timeout_seconds == 180
    assert settings.opencode_temperature == 0.1
    assert settings.opencode_max_evidence_chars == 100_000
    assert settings.opencode_max_lines_per_evidence == 200
    assert "test-opencode-secret" not in repr(settings)


def test_settings_loads_custom_llm_api_and_codex_cli(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                "LLM_BACKEND=codex-cli",
                "LLM_API_KEY=custom-secret",
                "LLM_API_BASE_URL=https://gateway.example/v1",
                "LLM_API_PROVIDER=custom-gateway",
                "LLM_MODEL=custom-model",
                "CODEX_CLI_MODEL=gpt-5.6-luna",
                "CODEX_CLI_REASONING_EFFORT=low",
            )
        ),
        encoding="utf-8",
    )

    settings = Settings()

    assert settings.llm_backend == "codex-cli"
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "custom-secret"
    assert settings.llm_api_base_url == "https://gateway.example/v1"
    assert settings.llm_api_provider == "custom-gateway"
    assert settings.llm_model == "custom-model"
    assert settings.codex_cli_model == "gpt-5.6-luna"
    assert settings.codex_cli_reasoning_effort == "low"
    assert "custom-secret" not in repr(settings)


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
