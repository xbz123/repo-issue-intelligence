from repo_issue_intelligence.config import Settings


def test_settings_loads_github_token_from_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GITHUB_TOKEN=example-token\n", encoding="utf-8")

    settings = Settings()

    assert settings.github_token == "example-token"


def test_settings_keeps_groq_key_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "GROQ_API_KEY=test-secret\nLLM_MODEL=openai/gpt-oss-20b\n",
        encoding="utf-8",
    )

    settings = Settings()

    assert settings.groq_api_key is not None
    assert settings.groq_api_key.get_secret_value() == "test-secret"
    assert settings.llm_reasoning_effort == "low"
    assert "test-secret" not in repr(settings)
