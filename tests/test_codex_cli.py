from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from repo_issue_intelligence.codex_cli import (
    CODEX_CLI_DEFAULT_MODEL,
    CODEX_CLI_PROVIDER,
    CodexCLIIssueAnalyzer,
    CodexCLIReranker,
    _event_metadata,
)
from repo_issue_intelligence.llm_client import LLMProviderError
from repo_issue_intelligence.models import (
    EvidenceSnippet,
    InvestigationReport,
    IssueRecord,
    ReproductionPlan,
)


def _issue() -> IssueRecord:
    return IssueRecord(
        number=42,
        title="Refresh token failure",
        body="刷新路径 returns an unexpected error. 🔒",
        labels=["bug", "国际化"],
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        updated_at=datetime(2026, 8, 2, tzinfo=UTC),
    )


def _evidence() -> list[EvidenceSnippet]:
    return [
        EvidenceSnippet(
            id="E1",
            file="src/auth.py",
            symbol="refresh",
            lines="10-20",
            content="10: def refresh():\n11:     return validate()",
        ),
        EvidenceSnippet(
            id="E2",
            file="src/token.py",
            symbol="validate",
            lines="30-40",
            content="30: def validate():\n31:     raise TokenError",
        ),
    ]


def _output_path(command: list[str]) -> Path:
    return Path(command[command.index("--output-last-message") + 1])


def _report() -> InvestigationReport:
    return InvestigationReport(
        issue=_issue(),
        confirmed_facts=[],
        candidates=[],
        hypotheses=[],
        reproduction_plan=ReproductionPlan(
            runtime="python",
            setup_commands=[],
            reproduction_steps=[],
            safety_constraints=[],
            open_questions=[],
        ),
        repository_root=Path("."),
    )


def test_event_metadata_rejects_boolean_and_object_token_counts() -> None:
    stdout = json.dumps(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": True, "output_tokens": {}},
        }
    )

    _, input_tokens, output_tokens, _ = _event_metadata(stdout)

    assert input_tokens == 0
    assert output_tokens == 0


def test_codex_cli_reranker_uses_isolated_strict_contract(tmp_path: Path) -> None:
    observed: dict[str, object] = {}
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    auth_file = source_home / "auth.json"
    auth_file.write_text('{"test":"credential"}', encoding="utf-8")
    (source_home / "AGENTS.md").write_text("contaminating guidance", encoding="utf-8")

    def fake_run(command: list[str], **options) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed["options"] = options
        schema_path = Path(command[command.index("--output-schema") + 1])
        observed["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
        isolated_home = Path(options["env"]["CODEX_HOME"])
        observed["isolated_home"] = isolated_home
        observed["isolated_files"] = sorted(path.name for path in isolated_home.iterdir())
        observed["auth"] = (isolated_home / "auth.json").read_text(encoding="utf-8")
        _output_path(command).write_text(
            json.dumps({"reranked_evidence_ids": ["E2", "E2", "E1"]}),
            encoding="utf-8",
        )
        stdout = "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": "thread-123"}),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 123,
                            "cached_input_tokens": 40,
                            "output_tokens": 7,
                        },
                    }
                ),
            )
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    reranker = CodexCLIReranker(auth_file=auth_file, run_command=fake_run)
    result = reranker.rerank(_issue(), _evidence())

    command = observed["command"]
    options = observed["options"]
    schema = observed["schema"]
    assert isinstance(command, list)
    assert isinstance(options, dict)
    assert isinstance(schema, dict)
    assert command[:2] == ["codex", "exec"]
    assert command[command.index("--model") + 1] == CODEX_CLI_DEFAULT_MODEL
    assert command[command.index("--sandbox") + 1] == "read-only"
    config_values = {
        command[index + 1]
        for index, argument in enumerate(command)
        if argument == "--config"
    }
    assert config_values == {
        'model_reasoning_effort="medium"',
        "project_doc_max_bytes=0",
    }
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--ephemeral" in command
    assert "--skip-git-repo-check" in command
    assert command[-1] == "-"
    disabled = {
        command[index + 1]
        for index, argument in enumerate(command)
        if argument == "--disable"
    }
    assert {"shell_tool", "unified_exec", "apps", "browser_use"} <= disabled
    assert "skill_search" not in disabled
    assert options["capture_output"] is True
    assert options["text"] is True
    assert options["encoding"] == "utf-8"
    assert options["check"] is False
    assert options["shell"] is False
    assert options["timeout"] == 180
    assert observed["isolated_files"] == ["auth.json"]
    assert observed["auth"] == '{"test":"credential"}'
    assert not Path(observed["isolated_home"]).exists()
    assert str(options["cwd"]) == command[command.index("--cd") + 1]
    prompt = options["input"]
    assert isinstance(prompt, str)
    assert "Refresh token failure" in prompt
    assert "刷新路径" in prompt
    assert "🔒" in prompt
    assert "src/auth.py" in prompt
    assert "Refresh token failure" not in " ".join(command)
    property_schema = schema["properties"]["reranked_evidence_ids"]
    assert schema["additionalProperties"] is False
    assert property_schema["minItems"] == 1
    assert property_schema["maxItems"] == 3
    assert result.provider == CODEX_CLI_PROVIDER
    assert result.model == CODEX_CLI_DEFAULT_MODEL
    assert result.request_id == "thread-123"
    assert result.input_tokens == 123
    assert result.output_tokens == 7
    assert result.attempts == 1
    assert result.analysis.reranked_evidence_ids == ["E2", "E1"]


def test_codex_cli_fast_tier_is_explicit() -> None:
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **options) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        _output_path(command).write_text(
            json.dumps({"reranked_evidence_ids": ["E1"]}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    result = CodexCLIReranker(
        service_tier="fast",
        run_command=fake_run,
    ).rerank(_issue(), _evidence())

    command = observed["command"]
    assert isinstance(command, list)
    config_values = {
        command[index + 1]
        for index, argument in enumerate(command)
        if argument == "--config"
    }
    assert 'service_tier="fast"' in config_values
    assert result.analysis.reranked_evidence_ids == ["E1"]


def test_codex_cli_issue_analyzer_uses_full_strict_contract() -> None:
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **options) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed["prompt"] = options["input"]
        schema_path = Path(command[command.index("--output-schema") + 1])
        observed["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
        _output_path(command).write_text(
            json.dumps(
                {
                    "summary": "The refresh path can surface a token error.",
                    "issue_type": "bug",
                    "reproduction_completeness": "partial",
                    "evidence_observations": [
                        {
                            "evidence_id": "E1",
                            "alignment": "supports_issue",
                            "observation": "The refresh function calls validation.",
                        },
                        {
                            "evidence_id": "E2",
                            "alignment": "supports_issue",
                            "observation": "Validation raises TokenError.",
                        },
                    ],
                    "hypothesis": {
                        "description": "TokenError may escape the refresh path.",
                        "confidence": 0.8,
                        "evidence_ids": ["E1", "E2"],
                        "missing_evidence": ["Runtime traceback"],
                    },
                }
            ),
            encoding="utf-8",
        )
        stdout = "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": "analysis-1"}),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 90, "output_tokens": 40},
                    }
                ),
            )
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    result = CodexCLIIssueAnalyzer(
        service_tier="fast",
        run_command=fake_run,
    ).analyze(_issue(), _report(), _evidence())

    command = observed["command"]
    schema = observed["schema"]
    assert isinstance(command, list)
    assert isinstance(schema, dict)
    assert schema["additionalProperties"] is False
    assert "evidence_observations" in schema["properties"]
    assert 'service_tier="fast"' in command
    assert "UNTRUSTED_DATA_BEGIN" in str(observed["prompt"])
    assert result.provider == CODEX_CLI_PROVIDER
    assert result.model == CODEX_CLI_DEFAULT_MODEL
    assert result.reasoning_effort == "medium"
    assert result.service_tier == "fast"
    assert result.request_id == "analysis-1"
    assert result.input_tokens == 90
    assert result.output_tokens == 40
    assert result.analysis.affected_component == "src/auth.py::refresh"
    assert result.analysis.hypotheses[0].evidence_ids == ["E1", "E2"]
    assert "without modifying files" in result.analysis.hypotheses[0].validation_step


@pytest.mark.parametrize(
    ("output", "category"),
    (
        ("not-json", "invalid_json"),
        (
            json.dumps(
                {
                    "reranked_evidence_ids": ["E1"],
                    "unexpected": True,
                }
            ),
            "schema_validation",
        ),
        (
            json.dumps({"reranked_evidence_ids": ["E1", "E2", "E1", "E2"]}),
            "schema_validation",
        ),
    ),
)
def test_codex_cli_reranker_rejects_invalid_structured_output(
    output: str,
    category: str,
) -> None:
    def fake_run(command: list[str], **options) -> subprocess.CompletedProcess[str]:
        _output_path(command).write_text(output, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(LLMProviderError) as caught:
        CodexCLIReranker(run_command=fake_run).rerank(_issue(), _evidence())

    assert caught.value.category == category
    assert caught.value.retryable is False


def test_codex_cli_reranker_rejects_unknown_evidence_id() -> None:
    def fake_run(command: list[str], **options) -> subprocess.CompletedProcess[str]:
        _output_path(command).write_text(
            json.dumps({"reranked_evidence_ids": ["E999"]}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(LLMProviderError) as caught:
        CodexCLIReranker(run_command=fake_run).rerank(_issue(), _evidence())

    assert caught.value.category == "unknown_evidence_id"
    assert "E999" in str(caught.value)
    assert caught.value.retryable is False


def test_codex_cli_reranker_requires_output_file() -> None:
    def fake_run(command: list[str], **options) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(LLMProviderError) as caught:
        CodexCLIReranker(run_command=fake_run).rerank(_issue(), _evidence())

    assert caught.value.category == "missing_output"
    assert caught.value.retryable is False


def test_codex_cli_reranker_classifies_quota_without_retaining_detail() -> None:
    provider_detail = "You've hit your usage limit; try again at a private timestamp"

    def fake_run(command: list[str], **options) -> subprocess.CompletedProcess[str]:
        stdout = "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": "thread-quota"}),
                json.dumps({"type": "error", "message": provider_detail}),
            )
        )
        return subprocess.CompletedProcess(command, 1, stdout, "")

    with pytest.raises(LLMProviderError) as caught:
        CodexCLIReranker(run_command=fake_run).rerank(_issue(), _evidence())

    assert caught.value.category == "quota"
    assert caught.value.retryable is False
    assert caught.value.request_id == "thread-quota"
    assert provider_detail not in str(caught.value)


def test_codex_cli_reranker_marks_transport_error_retryable() -> None:
    def fake_run(command: list[str], **options) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "connection reset by peer")

    with pytest.raises(LLMProviderError) as caught:
        CodexCLIReranker(run_command=fake_run).rerank(_issue(), _evidence())

    assert caught.value.category == "transport"
    assert caught.value.retryable is True


def test_codex_cli_reranker_classifies_timeout_and_missing_executable() -> None:
    def timeout_run(command: list[str], **options) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, options["timeout"])

    with pytest.raises(LLMProviderError) as timeout:
        CodexCLIReranker(run_command=timeout_run).rerank(_issue(), _evidence())
    assert timeout.value.category == "timeout"
    assert timeout.value.retryable is True

    def missing_run(command: list[str], **options) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(command[0])

    with pytest.raises(LLMProviderError) as missing:
        CodexCLIReranker(run_command=missing_run).rerank(_issue(), _evidence())
    assert missing.value.category == "cli_unavailable"
    assert missing.value.retryable is False
