import json
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from test_agent_store_v2 import new_store

from repo_issue_intelligence.agent_store_v2 import StoreError
from repo_issue_intelligence.codex_cli import CodexCLIIssueAnalyzer
from repo_issue_intelligence.issue_execution import run_agent_v2
from repo_issue_intelligence.llm_client import OpenAICompatibleIssueAnalyzer
from repo_issue_intelligence.models import IssueRecord
from repo_issue_intelligence.run_configuration import RunConfigurationError

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def repository(directory: Path, *, file_count: int = 1) -> Path:
    directory.mkdir()
    (directory / "service.py").write_text(
        "def refresh_token():\n    return 'committed'\n", encoding="utf-8"
    )
    for number in range(2, file_count + 1):
        (directory / f"service{number}.py").write_text(
            "def refresh_token():\n    return 'committed'\n", encoding="utf-8"
        )
    for arguments in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "tests@example.invalid"),
        ("config", "user.name", "Protocol v2 tests"),
        ("add", "."),
        ("commit", "-qm", "initial"),
    ):
        subprocess.run(["git", "-C", str(directory), *arguments], check=True, capture_output=True)
    return directory


def issues(*numbers: int) -> list[IssueRecord]:
    return [
        IssueRecord(
            number=number,
            title="refresh_token returns the wrong value",
            body="service.py refresh_token returns the wrong value",
            created_at=NOW,
            updated_at=NOW,
        )
        for number in numbers
    ]


def test_disabled_run_keeps_frozen_inputs_and_immediate_deterministic_report(tmp_path):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    run = run_agent_v2(issues(1), root, 1, store, as_of=NOW)

    assert run.status == "AWAITING_REVIEW"
    assert run.inputs.as_of == NOW
    assert run.configuration.protocol.index_version == "repository-index-v25"
    assert run.selection.selected_issue_numbers == (1,)
    issue = store.get_issue(run.run_id, 1)
    assert issue.deterministic_state == "succeeded"
    assert issue.deterministic_report["repository_root"] == str(root)
    assert issue.deterministic_report["candidates"][0]["file"] == "service.py"
    assert "llm_analysis" not in issue.deterministic_report
    assert issue.llm_state == "disabled"
    assert store.list_attempts(run.run_id, 1) == ()


def successful_response(evidence, *, metadata=True):
    payload = {
        "summary": "Token value may be incorrect",
        "issue_type": "bug",
        "reproduction_completeness": "partial",
        "evidence_observations": [
            {"evidence_id": item["id"], "alignment": "supports_issue", "observation": "Present"}
            for item in evidence
        ],
        "hypothesis": {
            "description": "Token return value may cause the failure",
            "confidence": 0.5,
            "evidence_ids": [evidence[-1]["id"]],
            "missing_evidence": ["Reproduction"],
        },
    }
    envelope = {"choices": [{"message": {"content": json.dumps(payload)}}]}
    if metadata:
        envelope.update(model="reported-B", id="response-1", usage={"completion_tokens": 9})
    return httpx.Response(
        200, json=envelope, headers={"x-request-id": "request-1"} if metadata else {}
    )


def test_different_result_prompt_version_is_fatal_without_rewriting_siblings(tmp_path):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []

    class CustomAnalyzer(OpenAICompatibleIssueAnalyzer):
        def analyze_v2(self, issue, *args, **kwargs):
            response = super().analyze_v2(issue, *args, **kwargs)
            if issue.number == 2:
                return response.model_copy(update={"prompt_version": "different-prompt-v99"})
            return response

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        received.append(payload["issue"]["number"])
        return successful_response(payload["repository_evidence"])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        analyzer = CustomAnalyzer("test-key", base_url="https://example.test/v1", client=client)
        with pytest.raises(RunConfigurationError, match="prompt version"):
            run_agent_v2(
                issues(1, 2, 3),
                root,
                3,
                store,
                llm_analyzer=analyzer,
                allow_external_llm=True,
                run_id="run",
                as_of=NOW,
                max_attempts=3,
            )
    assert received == [1, 2]
    assert store.get_run("run").status == "FAILED"
    assert store.get_run("run").configuration.protocol.prompt_version == "analysis-v2-primary-1"
    assert store.list_attempts("run", 1)[0].state == "success"
    (attempt,) = store.list_attempts("run", 2)
    assert attempt.state == "failure"
    assert attempt.analysis is None
    assert attempt.error.category == "local"
    assert store.get_issue("run", 2).selected_analysis_attempt_id is None
    assert store.list_attempts("run", 3) == ()
    assert store.list_traces("run")[-1].payload.failure_category == "configuration"
    assert "different-prompt-v99" not in store.get_run_summary("run").model_dump_json()


@pytest.mark.parametrize("redirect", [False, True])
def test_v2_custom_http_client_cannot_override_frozen_endpoint(tmp_path, redirect):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []

    def handler(request):
        received.append(str(request.url))
        endpoint = store.get_run("run").configuration.client.endpoint
        assert str(request.url) == f"{endpoint}/chat/completions"
        if redirect:
            return httpx.Response(307, headers={"location": "https://other.test/collect"})
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        return successful_response(payload["repository_evidence"])

    with httpx.Client(
        base_url="https://actual.test/api/v1",
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as client:
        analyzer = OpenAICompatibleIssueAnalyzer(
            "test-key", base_url="https://frozen.test/api/v1", client=client
        )
        run = run_agent_v2(
            issues(1),
            root,
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            run_id="run",
            as_of=NOW,
        )
    assert run.status == "AWAITING_REVIEW"
    assert received == ["https://frozen.test/api/v1/chat/completions"]
    assert store.list_attempts("run", 1)[0].state == ("failure" if redirect else "success")


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.test:80/v1/",
        "http://example.test:443/v1/",
        "https://[::1]:80/v1/",
        "http://[::1]:443/v1/",
        "https://[::1]:443/v1/",
    ],
)
def test_v2_frozen_endpoint_matches_transport_authority(tmp_path, base_url):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []

    def handler(request):
        received.append(request.url)
        frozen = store.get_run("run").configuration.client.endpoint
        assert httpx.URL(frozen) == httpx.URL(base_url.rstrip("/"))
        assert request.url == httpx.URL(base_url).join("chat/completions")
        assert (httpx.URL(frozen).host, httpx.URL(frozen).port) == (
            request.url.host,
            request.url.port,
        )
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        return successful_response(payload["repository_evidence"])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        analyzer = OpenAICompatibleIssueAnalyzer("test-key", base_url=base_url, client=client)
        run = run_agent_v2(
            issues(1), root, 1, store, llm_analyzer=analyzer, allow_external_llm=True, run_id="run"
        )
    assert run.status == "AWAITING_REVIEW"
    assert len(received) == 1


@pytest.mark.parametrize(
    "initial_url,replacement,expected_endpoint,expected_url",
    [
        (
            "https://example.test/v1/",
            "https://example.test/v1",
            "https://example.test/v1",
            "https://example.test/v1/chat/completions",
        ),
        (
            "https://example.test/v1/",
            "https://example.test/v1///",
            "https://example.test/v1",
            "https://example.test/v1/chat/completions",
        ),
        (
            "https://example.test/nested/chat/completions/chat/completions",
            "https://example.test/nested/chat/completions",
            "https://example.test/nested/chat/completions",
            "https://example.test/nested/chat/completions/chat/completions",
        ),
    ],
)
def test_v2_equivalent_endpoint_spelling_keeps_frozen_dispatch(
    tmp_path, initial_url, replacement, expected_endpoint, expected_url
):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []

    def handler(request):
        received.append(str(request.url))
        assert store.get_run("run").configuration.client.endpoint == expected_endpoint
        if len(received) == 1:
            analyzer.base_url = replacement
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        return successful_response(payload["repository_evidence"])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        analyzer = OpenAICompatibleIssueAnalyzer("test-key", base_url=initial_url, client=client)
        run = run_agent_v2(
            issues(1, 2),
            root,
            2,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            run_id="run",
            as_of=NOW,
        )
    assert run.status == "AWAITING_REVIEW"
    assert received == [expected_url, expected_url]


def test_v2_custom_http_timeout_cannot_override_frozen_budget(tmp_path):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        number = payload["issue"]["number"]
        received.append(number)
        assert store.get_run("run").configuration.budgets.timeout_seconds == 120
        assert store.list_attempts("run", number)[-1].request.timeout_seconds == 120
        assert request.extensions["timeout"] == {
            "connect": 120,
            "read": 120,
            "write": 120,
            "pool": 120,
        }
        client.timeout = httpx.Timeout(0.1)
        return successful_response(payload["repository_evidence"])

    with httpx.Client(
        base_url="https://example.test/v1", timeout=7, transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenAICompatibleIssueAnalyzer(
            "test-key", base_url="https://example.test/v1", timeout_seconds=120, client=client
        )
        run = run_agent_v2(
            issues(1, 2),
            root,
            2,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            run_id="run",
            as_of=NOW,
        )
    assert run.status == "AWAITING_REVIEW"
    assert received == [1, 2]


@pytest.mark.parametrize("file_count,metadata", [(1, True), (7, True), (1, False)])
def test_provider_receives_committed_report_and_exact_sealed_ledger(tmp_path, file_count, metadata):
    root = repository(tmp_path / "repo", file_count=file_count)
    store = new_store(tmp_path / "private")
    received = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        received.append(payload)
        assert store.get_issue("run", 1).deterministic_state == "succeeded"
        ids, lookup = store.evidence_lookup("run", 1)
        assert payload["repository_evidence"] == [
            lookup[key].model_dump(mode="json") for key in ids
        ]
        assert store.list_attempts("run", 1)[-1].state == "in_progress"
        return successful_response(payload["repository_evidence"], metadata=metadata)

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenAICompatibleIssueAnalyzer(
            "test-key", base_url="https://example.test/v1", model="requested-A", client=client
        )
        run = run_agent_v2(
            issues(1),
            root,
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            run_id="run",
            as_of=NOW,
        )

    assert run.status == "AWAITING_REVIEW"
    assert len(received) == 1
    issue = store.get_issue("run", 1)
    (attempt,) = store.list_attempts("run", 1)
    assert issue.llm_state == "succeeded"
    assert issue.selected_analysis_attempt_id == attempt.attempt_id
    assert attempt.state == "success"
    assert attempt.request.model == "requested-A"
    assert run.configuration.parameter_origins["budget.output_tokens"] == "client_default"
    assert run.configuration.parameter_origins["budget.timeout_seconds"] == "client_default"
    assert attempt.reported.model == ("reported-B" if metadata else None)
    assert attempt.reported.response_id == ("response-1" if metadata else None)
    assert attempt.reported.temperature is None
    assert attempt.reported.seed is None
    assert attempt.local.http_status == 200
    assert attempt.analysis.primary_evidence_id == ("E7" if file_count == 7 else "E1")
    assert attempt.analysis.affected_component == (
        "service7.py::refresh_token" if file_count == 7 else "service.py::refresh_token"
    )


@pytest.mark.parametrize("failed_numbers", [(2,), (1, 2, 3)])
def test_provider_failure_retries_only_its_issue_and_still_allows_review(tmp_path, failed_numbers):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        number = payload["issue"]["number"]
        received.append(number)
        if number in failed_numbers:
            return httpx.Response(
                500,
                json={"error": {"message": "secret=never-store"}, "model": "reported-B"},
            )
        return successful_response(payload["repository_evidence"])

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenAICompatibleIssueAnalyzer(
            "test-key", base_url="https://example.test/v1", model="requested-A", client=client
        )
        run = run_agent_v2(
            issues(1, 2, 3),
            root,
            3,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            max_attempts=2,
            run_id="run",
            as_of=NOW,
        )

    assert run.status == "AWAITING_REVIEW"
    assert received == ([1, 2, 2, 3] if failed_numbers == (2,) else [1, 1, 2, 2, 3, 3])
    for number in (1, 2, 3):
        issue = store.get_issue("run", number)
        assert issue.deterministic_state == "succeeded"
        attempts = store.list_attempts("run", number)
        assert [attempt.state for attempt in attempts] == (
            ["failure", "failure"] if number in failed_numbers else ["success"]
        )
        assert len({attempt.evidence_set_id for attempt in attempts}) == 1
        if number in failed_numbers:
            assert issue.llm_state == "failed"
            assert issue.selected_analysis_attempt_id is None
            assert all(attempt.reported.model == "reported-B" for attempt in attempts)
            assert all(attempt.local.http_status == 500 for attempt in attempts)
            assert "never-store" not in "".join(attempt.model_dump_json() for attempt in attempts)


def test_unknown_programming_error_fails_run_without_losing_successful_sibling(tmp_path):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        number = payload["issue"]["number"]
        received.append(number)
        if number == 2:
            raise RuntimeError("unexpected program failure secret=never-store")
        return successful_response(payload["repository_evidence"])

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenAICompatibleIssueAnalyzer(
            "test-key", base_url="https://example.test/v1", client=client
        )
        with pytest.raises(RuntimeError, match="unexpected program failure"):
            run_agent_v2(
                issues(1, 2, 3),
                root,
                3,
                store,
                llm_analyzer=analyzer,
                allow_external_llm=True,
                run_id="run",
                as_of=NOW,
            )

    assert received == [1, 2]
    assert store.get_run("run").status == "FAILED"
    assert store.get_issue("run", 1).llm_state == "succeeded"
    assert store.get_issue("run", 2).deterministic_state == "succeeded"
    assert store.get_issue("run", 3).deterministic_state == "pending"
    (attempt,) = store.list_attempts("run", 2)
    assert attempt.state == "failure"
    assert attempt.error.category == "local"
    assert "never-store" not in attempt.model_dump_json()
    trace = store.list_traces("run")[-1]
    assert trace.payload.event == "run_failed"
    assert trace.payload.failure_category == "programming"


def test_frozen_configuration_keeps_default_origins_and_explicit_budget_overrides(tmp_path):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        return successful_response(payload["repository_evidence"])

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenAICompatibleIssueAnalyzer(
            "test-key",
            client=client,
            max_output_tokens=None,
            reasoning_effort=None,
            response_format_json=False,
        )
        run = run_agent_v2(
            issues(1),
            root,
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            max_evidence_chars=1000,
            run_id="run",
            as_of=NOW,
            parameter_origins={
                "evidence_chars": "user_config",
                "retry_policy": "user_config",
                "requested_model": "user_config",
                "timeout_seconds": "user_config",
            },
        )

    origins = run.configuration.parameter_origins
    assert origins["evidence_chars"] == "user_config"
    assert origins["evidence_lines"] == "client_default"
    assert origins["retry_policy"] == "user_config"
    assert origins["requested_model"] == "user_config"
    assert origins["timeout_seconds"] == "user_config"
    assert origins["budget.timeout_seconds"] == "user_config"
    assert origins["temperature"] == "client_default"
    assert origins["max_output_tokens"] == "omitted"
    assert origins["seed"] == "omitted"
    (attempt,) = store.list_attempts("run", 1)
    assert attempt.state == "success"
    assert "max_output_tokens" not in attempt.request.model_fields_set
    assert "seed" not in attempt.request.model_fields_set
    assert "reasoning_effort" not in attempt.request.model_fields_set
    assert "response_format_json" not in attempt.request.model_fields_set


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError])
def test_uncertain_transport_is_unknown_without_retry_and_keeps_siblings(tmp_path, error_type):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        number = payload["issue"]["number"]
        received.append(number)
        if number == 2:
            raise error_type("unknown remote outcome", request=request)
        return successful_response(payload["repository_evidence"])

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenAICompatibleIssueAnalyzer(
            "test-key", base_url="https://example.test/v1", client=client
        )
        run = run_agent_v2(
            issues(1, 2, 3),
            root,
            3,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            max_attempts=3,
            run_id="run",
            as_of=NOW,
        )

    assert received == [1, 2, 3]
    assert run.status == "AWAITING_REVIEW"
    assert store.get_issue("run", 2).llm_state == "interrupted_unknown"
    (attempt,) = store.list_attempts("run", 2)
    assert attempt.state == "unknown"
    assert attempt.error.category == "outcome_uncertain"
    assert attempt.reported.model is None
    assert attempt.local.http_status is None
    assert store.get_issue("run", 1).llm_state == "succeeded"
    assert store.get_issue("run", 3).llm_state == "succeeded"


def test_interrupted_invocation_is_unknown_and_run_is_interrupted(tmp_path):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []

    def handler(request):
        received.append(request)
        raise KeyboardInterrupt

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenAICompatibleIssueAnalyzer(
            "test-key", base_url="https://example.test/v1", client=client
        )
        with pytest.raises(KeyboardInterrupt):
            run_agent_v2(
                issues(1, 2),
                root,
                2,
                store,
                llm_analyzer=analyzer,
                allow_external_llm=True,
                run_id="run",
                as_of=NOW,
            )

    assert len(received) == 1
    assert store.get_run("run").status == "INTERRUPTED"
    (attempt,) = store.list_attempts("run", 1)
    assert attempt.state == "unknown"
    assert attempt.error.category == "interrupted"
    assert attempt.local.category == "interrupted"
    assert store.get_issue("run", 2).deterministic_state == "pending"


def test_no_evidence_seals_empty_set_without_sending(tmp_path):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []
    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(received.append)
    ) as client:
        analyzer = OpenAICompatibleIssueAnalyzer(
            "test-key", base_url="https://example.test/v1", client=client
        )
        run = run_agent_v2(
            issues(1),
            root,
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            max_evidence_chars=1,
            run_id="run",
            as_of=NOW,
        )

    assert received == []
    assert run.status == "AWAITING_REVIEW"
    assert store.get_issue("run", 1).llm_state == "skipped_no_evidence"
    assert store.get_issue("run", 1).deterministic_state == "succeeded"
    assert store.read_evidence("run", 1).items == ()
    assert store.list_attempts("run", 1) == ()


def test_external_transmission_is_denied_without_current_permission(tmp_path):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []
    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(received.append)
    ) as client:
        analyzer = OpenAICompatibleIssueAnalyzer("test-key", client=client)
        with pytest.raises(ValueError, match="explicit permission"):
            run_agent_v2(issues(1), root, 1, store, llm_analyzer=analyzer, run_id="run")

    assert received == []
    assert store.get_run("run") is None


def test_seal_failure_preserves_report_and_never_sends(tmp_path, monkeypatch):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    connect = sqlite3.connect

    def denied_write(action, table, column, database, source):
        if action == sqlite3.SQLITE_INSERT and table == "agent_v2_evidence_items":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def restricted_database(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(denied_write)
        return connection

    monkeypatch.setattr(sqlite3, "connect", restricted_database)
    received = []
    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(received.append)
    ) as client:
        analyzer = OpenAICompatibleIssueAnalyzer("test-key", client=client)
        with pytest.raises(StoreError):
            run_agent_v2(
                issues(1),
                root,
                1,
                store,
                llm_analyzer=analyzer,
                allow_external_llm=True,
                run_id="run",
                as_of=NOW,
            )

    assert received == []
    assert store.get_run("run").status == "FAILED"
    assert store.get_issue("run", 1).deterministic_state == "succeeded"
    assert store.read_evidence("run", 1) is None
    assert store.list_attempts("run", 1) == ()
    assert store.list_traces("run")[-1].payload.failure_category == "store"


def test_invalid_provider_response_is_recorded_without_retry_or_raw_payload(tmp_path):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []

    def handler(request):
        received.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "secret=never-store invalid json"}}]}
        )

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenAICompatibleIssueAnalyzer("test-key", client=client)
        run = run_agent_v2(
            issues(1),
            root,
            1,
            store,
            llm_analyzer=analyzer,
            allow_external_llm=True,
            max_attempts=3,
            run_id="run",
            as_of=NOW,
        )

    assert run.status == "AWAITING_REVIEW"
    assert len(received) == 1
    (attempt,) = store.list_attempts("run", 1)
    assert attempt.state == "failure"
    assert attempt.error.category == "invalid_response"
    assert attempt.local.http_status == 200
    assert "never-store" not in attempt.model_dump_json()


@pytest.mark.parametrize("outcome", ["success", "timeout"])
def test_controlled_cli_invocation_preserves_local_and_reported_observations(tmp_path, outcome):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []

    def fake_run(command, **options):
        if command == ["codex", "--version"]:
            assert options["input"] == ""
            assert options["timeout"] == 5
            assert not options["shell"]
            return subprocess.CompletedProcess(command, 0, "codex-cli 1.2.3\n", "")
        payload = json.loads(
            options["input"]
            .split("UNTRUSTED_DATA_BEGIN\n", 1)[1]
            .split("\nUNTRUSTED_DATA_END", 1)[0]
        )
        received.append(payload)
        assert store.get_run("run").configuration.client.cli_version == "codex-cli 1.2.3"
        ids, lookup = store.evidence_lookup("run", 1)
        assert payload["repository_evidence"] == [
            lookup[key].model_dump(mode="json") for key in ids
        ]
        assert store.list_attempts("run", 1)[-1].state == "in_progress"
        event = json.dumps({"type": "thread.started", "thread_id": "cli-thread-1"})
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, options["timeout"], output=event)
        output_path = Path(command[command.index("--output-last-message") + 1])
        envelope = successful_response(payload["repository_evidence"]).json()
        output_path.write_text(envelope["choices"][0]["message"]["content"], encoding="utf-8")
        completed = json.dumps(
            {
                "type": "turn.completed",
                "model": "reported-B",
                "service_tier": "default",
                "usage": {"input_tokens": 0, "output_tokens": 7},
            }
        )
        return subprocess.CompletedProcess(command, 0, event + "\n" + completed, "")

    analyzer = CodexCLIIssueAnalyzer(
        model="requested-A",
        service_tier="fast",
        run_command=fake_run,
        auth_file=tmp_path / "missing-auth",
    )
    run = run_agent_v2(
        issues(1),
        root,
        1,
        store,
        llm_analyzer=analyzer,
        allow_external_llm=True,
        run_id="run",
        as_of=NOW,
    )

    assert run.status == "AWAITING_REVIEW"
    assert len(received) == 1
    assert run.configuration.client.cli_version == "codex-cli 1.2.3"
    (attempt,) = store.list_attempts("run", 1)
    assert attempt.state == ("success" if outcome == "success" else "unknown")
    assert attempt.request.backend == "codex-cli"
    assert attempt.request.model == "requested-A"
    assert attempt.request.service_tier == "fast"
    assert attempt.local.thread_id == "cli-thread-1"
    assert attempt.reported.request_id is None
    assert attempt.reported.model == ("reported-B" if outcome == "success" else None)
    assert attempt.local.exit_code == (0 if outcome == "success" else None)
    assert attempt.local.elapsed_ms >= 0


@pytest.mark.parametrize("failure", ["missing", "timeout", "exit", "unsafe_output"])
def test_cli_version_preflight_failure_leaves_no_run_or_provider_dispatch(tmp_path, failure):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")

    def fake_run(command, **options):
        assert command == ["codex", "--version"]
        assert options["input"] == ""
        assert not (Path(options["env"]["CODEX_HOME"]) / "auth.json").exists()
        if failure == "missing":
            raise FileNotFoundError("private diagnostic")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 5, output="private diagnostic")
        return subprocess.CompletedProcess(
            command,
            1 if failure == "exit" else 0,
            "codex-cli secret=private-diagnostic",
            "private diagnostic",
        )

    analyzer = CodexCLIIssueAnalyzer(run_command=fake_run, auth_file=tmp_path / "missing-auth")
    with pytest.raises(RunConfigurationError, match="version could not be verified") as error:
        run_agent_v2(
            issues(1), root, 1, store, llm_analyzer=analyzer, allow_external_llm=True, run_id="run"
        )
    assert "private" not in str(error.value)
    assert store.get_run("run") is None
    assert store.list_issues("run") == ()


def test_cli_version_drift_is_rejected_before_provider_dispatch(tmp_path):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    versions = iter(["codex-cli 1.2.3", "codex-cli 1.2.4"])

    def fake_run(command, **options):
        assert command == ["codex", "--version"]
        return subprocess.CompletedProcess(command, 0, next(versions), "")

    analyzer = CodexCLIIssueAnalyzer(run_command=fake_run, auth_file=tmp_path / "missing-auth")
    with pytest.raises(RunConfigurationError, match="frozen run configuration"):
        run_agent_v2(
            issues(1), root, 1, store, llm_analyzer=analyzer, allow_external_llm=True, run_id="run"
        )
    run = store.get_run("run")
    assert run.status == "FAILED"
    assert run.configuration.client.cli_version == "codex-cli 1.2.3"
    assert store.list_attempts("run", 1) == ()


@pytest.mark.parametrize("field", ["model", "base_url"])
def test_current_analyzer_drift_is_rejected_before_next_issue_dispatch(tmp_path, field):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        received.append(payload["issue"]["number"])
        setattr(analyzer, field, "changed-model" if field == "model" else "https://other.test/v1")
        return successful_response(payload["repository_evidence"])

    with httpx.Client(
        base_url="https://example.test/v1", transport=httpx.MockTransport(handler)
    ) as client:
        analyzer = OpenAICompatibleIssueAnalyzer(
            "test-key", base_url="https://example.test/v1", client=client
        )
        with pytest.raises(RunConfigurationError, match="frozen run configuration"):
            run_agent_v2(
                issues(1, 2),
                root,
                2,
                store,
                llm_analyzer=analyzer,
                allow_external_llm=True,
                run_id="run",
                as_of=NOW,
            )

    assert received == [1]
    assert store.get_run("run").status == "FAILED"
    assert store.get_issue("run", 1).llm_state == "succeeded"
    assert store.get_issue("run", 2).deterministic_state == "succeeded"
    assert store.list_attempts("run", 2) == ()
    assert store.list_traces("run")[-1].payload.failure_category == "configuration"


def test_confirmed_cli_rate_limit_retries_only_failed_issue(tmp_path):
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    received = []

    def fake_run(command, **options):
        if command == ["codex", "--version"]:
            return subprocess.CompletedProcess(command, 0, "codex-cli 1.2.3\n", "")
        payload = json.loads(
            options["input"]
            .split("UNTRUSTED_DATA_BEGIN\n", 1)[1]
            .split("\nUNTRUSTED_DATA_END", 1)[0]
        )
        number = payload["issue"]["number"]
        received.append(number)
        ids, lookup = store.evidence_lookup("run", number)
        assert payload["repository_evidence"] == [
            lookup[key].model_dump(mode="json") for key in ids
        ]
        if number == 2 and received.count(2) == 1:
            return subprocess.CompletedProcess(command, 1, "", "429 rate limit")
        output_path = Path(command[command.index("--output-last-message") + 1])
        envelope = successful_response(payload["repository_evidence"]).json()
        output_path.write_text(envelope["choices"][0]["message"]["content"], encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    analyzer = CodexCLIIssueAnalyzer(run_command=fake_run, auth_file=tmp_path / "missing-auth")
    run = run_agent_v2(
        issues(1, 2, 3),
        root,
        3,
        store,
        llm_analyzer=analyzer,
        allow_external_llm=True,
        max_attempts=2,
        run_id="run",
        as_of=NOW,
    )

    assert run.status == "AWAITING_REVIEW"
    assert received == [1, 2, 2, 3]
    first, second = store.list_attempts("run", 2)
    assert (first.state, second.state) == ("failure", "success")
    assert first.local.exit_code == 1
    assert first.local.http_status is None
    assert first.error.category == "provider"
    assert first.evidence_set_id == second.evidence_set_id
    assert store.get_issue("run", 2).selected_analysis_attempt_id == second.attempt_id
    assert store.get_issue("run", 1).llm_state == "succeeded"
    assert store.get_issue("run", 3).llm_state == "succeeded"
