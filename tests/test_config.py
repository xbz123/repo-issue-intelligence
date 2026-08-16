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
    assert settings.opencode_timeout_seconds == 60
    assert "test-opencode-secret" not in repr(settings)
