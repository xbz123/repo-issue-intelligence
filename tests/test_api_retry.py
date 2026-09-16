import json
import multiprocessing

import httpx
import pytest
from fastapi.testclient import TestClient
from test_agent_resume import clean_runtime as clean_runtime
from test_agent_store_v2 import new_store
from test_api_security import AUTH, TOKEN
from test_issue_execution import NOW, issues, repository, successful_response

from repo_issue_intelligence import (
    agent_resume,
    api,
    api_retry,
    cli,
    issue_execution,
    run_configuration,
)
from repo_issue_intelligence.agent_store_v2 import AgentStoreV2
from repo_issue_intelligence.config import Settings
from repo_issue_intelligence.issue_execution import run_agent_v2
from repo_issue_intelligence.protocol_v2_models import EngineRuntime

URL = "/v2/agent/runs/run/issues/1/llm-retry"


@pytest.mark.parametrize("backend", ["api", "codex-cli"])
def test_cli_and_http_analyzer_settings_match(backend):
    settings = Settings(
        _env_file=None,
        llm_backend=backend,
        llm_api_key="synthetic-key",
        llm_api_base_url="https://example.invalid/v1",
        llm_api_provider="synthetic",
        llm_model="configured-model",
        llm_temperature=0,
        llm_max_output_tokens=123,
        llm_timeout_seconds=17,
        llm_reasoning_effort="none",
        llm_response_format_json=False,
        codex_cli_executable="synthetic-codex",
        codex_cli_model="configured-codex",
        codex_cli_timeout_seconds=23,
        codex_cli_reasoning_effort="high",
    )
    http_analyzer = api_retry.build_issue_analyzer(settings)
    cli_analyzer = cli._build_issue_analyzer(settings)
    try:
        assert (
            http_analyzer.requested_configuration_v2() == cli_analyzer.requested_configuration_v2()
        )
        for field in ("base_url", "executable", "model", "timeout_seconds"):
            assert getattr(http_analyzer, field, None) == getattr(cli_analyzer, field, None)
        if backend == "api":
            assert http_analyzer.model == "configured-model"
            assert http_analyzer.temperature == 0
            assert http_analyzer.max_output_tokens == 123
            assert http_analyzer.response_format_json is False
        else:
            assert http_analyzer.model == "configured-codex"
            assert http_analyzer.executable == "synthetic-codex"
            assert http_analyzer.timeout_seconds == 23
    finally:
        http_analyzer.close()
        cli_analyzer.close()


@pytest.mark.parametrize("backend", ["api", "codex-cli"])
def test_cli_analyzer_overrides_stay_local_to_cli(backend):
    settings = Settings(_env_file=None, llm_backend=backend, llm_api_key="synthetic-key")
    options = (
        {
            "base_url": "https://example.invalid/v1",
            "provider": "synthetic",
            "temperature": 0,
            "seed": 0,
            "omit_max_tokens": True,
        }
        if backend == "api"
        else {"fast": True}
    )
    analyzer = cli._build_issue_analyzer(
        settings, model="override-model", timeout_seconds=19, **options
    )
    try:
        assert analyzer.model == "override-model" and analyzer.timeout_seconds == 19
        if backend == "api":
            assert analyzer.base_url == "https://example.invalid/v1/"
            assert analyzer.provider == "synthetic"
            assert analyzer.temperature == analyzer.seed == 0
            assert analyzer.max_output_tokens is None
        else:
            assert analyzer.service_tier == "fast"
    finally:
        analyzer.close()


def setup_retry(tmp_path, monkeypatch, *, unknown=False):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "synthetic-key")
    root = repository(tmp_path / "repo")
    store = new_store(tmp_path / "private")
    calls = []

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(payload)
        if len(calls) == 1:
            if unknown:
                raise httpx.ReadTimeout("synthetic unknown", request=request)
            return httpx.Response(400, json={"error": "synthetic failure"})
        return successful_response(payload["repository_evidence"])

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(handler))
    analyzer = cli._build_issue_analyzer(Settings())
    try:
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
    finally:
        analyzer.close()
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    monkeypatch.setenv("RII_API_V2_DATABASE", str(store.path))
    monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(root)]))
    monkeypatch.setenv(
        "RII_API_OPERATIONS", json.dumps(["read", "review", "retry", "recover-unknown"])
    )
    monkeypatch.setenv(
        "RII_API_EXTERNAL_GRANTS",
        json.dumps(
            [
                {
                    "principal": "local-operator",
                    "analysis_root": str(root),
                    "provider": "opencode",
                    "operation": operation,
                }
                for operation in ("retry", "recover-unknown")
            ]
        ),
    )
    return root, store, calls


@pytest.mark.parametrize("unknown", [False, True])
def test_http_retry_reuses_frozen_inputs_and_keeps_previous_attempt(
    tmp_path, monkeypatch, clean_runtime, unknown
):
    root, store, calls = setup_retry(tmp_path, monkeypatch, unknown=unknown)
    before = store.get_issue("run", 1)
    original = store.list_attempts("run", 1)
    (root / "service.py").write_text("changed checkout")
    with TestClient(api.app, base_url="http://127.0.0.1", headers=AUTH) as client:
        if unknown:
            assert client.post(URL, json={}).status_code == 409
            assert len(calls) == 1
        response = client.post(URL, json={"recover_unknown": unknown})
    assert response.status_code == 200, response.text
    assert response.json()["llm_state"] == "succeeded"
    assert calls == [calls[0], calls[0]]
    assert store.list_attempts("run", 1)[:1] == original
    after = store.get_issue("run", 1)
    assert after.deterministic_report == before.deterministic_report
    assert after.evidence_set_id == before.evidence_set_id
    assert "repository_evidence" not in response.text
    assert "deterministic_report" not in response.text
    assert response.json()["duplicate_remote_execution_possible"] is unknown


def revoke(monkeypatch, policy):
    name, value = {
        "token": ("RII_API_TOKEN", "rotated-synthetic-token-0123456789abcdef"),
        "principal": ("RII_API_PRINCIPAL", "different-operator"),
        "scope": ("RII_API_ANALYSIS_ROOTS", "[]"),
        "operation": ("RII_API_OPERATIONS", '["read", "review"]'),
        "provider_grant": ("RII_API_EXTERNAL_GRANTS", "[]"),
        "provider": ("LLM_API_PROVIDER", "another-provider"),
    }[policy]
    monkeypatch.setenv(name, value)


@pytest.mark.parametrize("unknown", [False, True])
@pytest.mark.parametrize(
    "policy", ["token", "principal", "scope", "operation", "provider_grant", "provider"]
)
def test_http_revocation_blocks_dispatch_and_preserves_retained_state(
    tmp_path, monkeypatch, clean_runtime, unknown, policy
):
    _, store, calls = setup_retry(tmp_path, monkeypatch, unknown=unknown)
    before = store.get_run_summary("run")
    # Keep the middleware-authenticated principal, then revoke before service dispatch.
    validate = agent_resume.validate_recovery_configuration

    def revoke_after_validation(*args, **kwargs):
        validate(*args, **kwargs)
        revoke(monkeypatch, policy)

    monkeypatch.setattr(agent_resume, "validate_recovery_configuration", revoke_after_validation)
    with TestClient(api.app, base_url="http://127.0.0.1", headers=AUTH) as client:
        response = client.post(URL, json={"recover_unknown": unknown})
    assert response.status_code == (401 if policy == "token" else 403), response.text
    assert len(calls) == 1
    assert store.get_run_summary("run") == before


@pytest.mark.parametrize("policy", ["token", "scope", "provider_grant"])
def test_revocation_after_attempt_claim_is_checked_before_send(
    tmp_path, monkeypatch, clean_runtime, policy
):
    _, store, calls = setup_retry(tmp_path, monkeypatch)
    original = store.list_attempts("run", 1)
    start = AgentStoreV2.start_attempt

    def claim_then_revoke(*args, **kwargs):
        result = start(*args, **kwargs)
        revoke(monkeypatch, policy)
        return result

    monkeypatch.setattr(AgentStoreV2, "start_attempt", claim_then_revoke)
    with TestClient(api.app, base_url="http://127.0.0.1", headers=AUTH) as client:
        response = client.post(URL, json={})
    assert response.status_code == (401 if policy == "token" else 403)
    assert len(calls) == 1
    attempts = store.list_attempts("run", 1)
    assert attempts[:1] == original
    assert len(attempts) == 2 and attempts[-1].state == "failure"
    assert attempts[-1].error.category == "local"
    assert store.get_run("run").status == "FAILED"


@pytest.mark.parametrize("unknown", [False, True])
def test_revocation_during_automatic_backoff_prevents_next_send(
    tmp_path, monkeypatch, clean_runtime, unknown
):
    _, store, calls = setup_retry(tmp_path, monkeypatch, unknown=unknown)
    retry_calls = []

    def rate_limit(request):
        retry_calls.append(request)
        return httpx.Response(429, json={"error": "synthetic rate limit"})

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(rate_limit))
    monkeypatch.setattr(issue_execution, "sleep", lambda _: revoke(monkeypatch, "provider_grant"))
    with TestClient(api.app, base_url="http://127.0.0.1", headers=AUTH) as client:
        response = client.post(URL, json={"recover_unknown": unknown})
    assert response.status_code == 403, response.text
    assert len(calls) == len(retry_calls) == 1
    assert len(store.list_attempts("run", 1)) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("principal_id", "forged"),
        ("model", "changed"),
        ("provider", "changed"),
        ("base_url", "https://example.com"),
        ("database", "private.sqlite3"),
        ("allow_external_llm", True),
        ("api_key", "synthetic-secret-do-not-echo"),
        ("recover_unknown", "true"),
        ("recover_unknown", 1),
    ],
)
def test_http_retry_rejects_client_overrides_and_coerced_confirmation(
    tmp_path, monkeypatch, clean_runtime, field, value
):
    _, store, calls = setup_retry(tmp_path, monkeypatch)
    before = store.get_run_summary("run")
    with TestClient(api.app, base_url="http://127.0.0.1", headers=AUTH) as client:
        response = client.post(URL, json={field: value})
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request fields"}
    assert len(calls) == 1 and store.get_run_summary("run") == before


@pytest.mark.parametrize(
    "setting,value",
    [
        ("LLM_MODEL", "changed-model"),
        ("LLM_API_BASE_URL", "https://example.com/v1"),
        ("LLM_MAX_EVIDENCE_CHARS", "123"),
        ("LLM_MAX_OUTPUT_TOKENS", "99"),
    ],
)
def test_http_retry_refuses_changed_server_configuration(
    tmp_path, monkeypatch, clean_runtime, setting, value
):
    _, store, calls = setup_retry(tmp_path, monkeypatch)
    before = store.get_run_summary("run")
    monkeypatch.setenv(setting, value)
    with TestClient(api.app, base_url="http://127.0.0.1", headers=AUTH) as client:
        response = client.post(URL, json={})
    assert response.status_code == 409
    assert len(calls) == 1 and store.get_run_summary("run") == before


def test_http_retry_requires_explicit_operation_and_transfer_grant(
    tmp_path, monkeypatch, clean_runtime
):
    _, store, calls = setup_retry(tmp_path, monkeypatch, unknown=True)
    before = store.get_run_summary("run")
    with TestClient(api.app, base_url="http://127.0.0.1", headers=AUTH) as client:
        with monkeypatch.context() as denied:
            denied.delenv("RII_API_OPERATIONS")
            assert client.post(URL, json={"recover_unknown": True}).status_code == 403
        with monkeypatch.context() as denied:
            denied.delenv("RII_API_EXTERNAL_GRANTS")
            assert client.post(URL, json={"recover_unknown": True}).status_code == 403
        grants = json.loads(Settings().model_dump_json())["api_external_grants"]
        monkeypatch.setenv("RII_API_EXTERNAL_GRANTS", json.dumps([grants[0]]))
        assert client.post(URL, json={"recover_unknown": True}).status_code == 403
    assert len(calls) == 1 and store.get_run_summary("run") == before


def _http_retry_worker(runtime, go, dispatched, release, results):
    run_configuration.capture_engine_runtime = lambda *a, **kw: EngineRuntime.model_validate(
        runtime
    )
    calls = []

    def handler(request):
        calls.append(True)
        dispatched.set()
        assert release.wait(timeout=20)
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        return successful_response(payload["repository_evidence"])

    httpx.HTTPTransport = lambda **kwargs: httpx.MockTransport(handler)
    assert go.wait(timeout=20)
    with TestClient(api.app, base_url="http://127.0.0.1", headers=AUTH) as client:
        response = client.post(URL, json={})
    results.put((response.status_code, len(calls)))


def test_revocation_during_call_preserves_result_but_withholds_response(
    tmp_path, monkeypatch, clean_runtime
):
    _, store, _ = setup_retry(tmp_path, monkeypatch)
    calls = []

    def handler(request):
        calls.append(True)
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        revoke(monkeypatch, "token")
        return successful_response(payload["repository_evidence"])

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(handler))
    with TestClient(api.app, base_url="http://127.0.0.1", headers=AUTH) as client:
        response = client.post(URL, json={})
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
    assert calls == [True]
    assert store.get_issue("run", 1).llm_state == "succeeded"
    assert [attempt.state for attempt in store.list_attempts("run", 1)] == ["failure", "success"]


def test_http_unknown_recovery_refuses_a_live_execution_guard(tmp_path, monkeypatch, clean_runtime):
    _, store, calls = setup_retry(tmp_path, monkeypatch, unknown=True)
    before = store.get_run_summary("run")
    attempt = store.list_attempts("run", 1)[0]
    with store.execution_lock(attempt.attempt_id):
        with TestClient(api.app, base_url="http://127.0.0.1", headers=AUTH) as client:
            response = client.post(URL, json={"recover_unknown": True})
    assert response.status_code == 409
    assert len(calls) == 1 and store.get_run_summary("run") == before


@pytest.mark.parametrize(
    "denial", ["token", "host", "origin", "missing-run", "missing-issue", "busy"]
)
def test_http_retry_boundary_refuses_without_mutation(tmp_path, monkeypatch, clean_runtime, denial):
    _, store, calls = setup_retry(tmp_path, monkeypatch)
    before = store.get_run_summary("run")
    headers = dict(AUTH)
    url = URL
    expected = 403
    if denial == "token":
        headers["Authorization"] = "Bearer invalid"
        expected = 401
    elif denial == "host":
        headers["Host"] = "example.com"
    elif denial == "origin":
        headers["Origin"] = "https://example.com"
    elif denial.startswith("missing"):
        url = (
            URL.replace("/runs/run/", "/runs/missing/")
            if denial == "missing-run"
            else URL.replace("/1/", "/999/")
        )
        expected = 404
    with TestClient(api.app, base_url="http://127.0.0.1", headers=headers) as client:
        if denial == "busy":
            from repo_issue_intelligence.api_security import APIBoundary

            middleware = api.app.middleware_stack
            while not isinstance(middleware, APIBoundary):
                middleware = middleware.app
            assert middleware.work_slot.acquire(blocking=False)
            try:
                response = client.post(url, json={})
            finally:
                middleware.work_slot.release()
            expected = 503
            assert response.headers["retry-after"] == "1"
        else:
            response = client.post(url, json={})
    assert response.status_code == expected, response.text
    assert len(calls) == 1 and store.get_run_summary("run") == before


@pytest.mark.parametrize("order", ["review-first", "retry-first", "two-retries"])
def test_http_retry_and_review_compete_across_processes(
    tmp_path, monkeypatch, clean_runtime, order
):
    _, store, calls = setup_retry(tmp_path, monkeypatch)
    issue = store.get_issue("run", 1)
    original = store.list_attempts("run", 1)
    context = multiprocessing.get_context("spawn")
    go, dispatched, release, results = (
        context.Event(),
        context.Event(),
        context.Event(),
        context.Queue(),
    )
    process = context.Process(
        target=_http_retry_worker,
        args=(clean_runtime.model_dump(), go, dispatched, release, results),
    )
    process.start()
    try:
        if order != "review-first":
            go.set()
            assert dispatched.wait(timeout=20)
        with TestClient(api.app, base_url="http://127.0.0.1", headers=AUTH) as client:
            if order == "two-retries":
                response = client.post(URL, json={})
            else:
                response = client.post(
                    URL.removesuffix("llm-retry") + "reviews",
                    json={
                        "decision": "approved",
                        "expected_review_version": 0,
                        "idempotency_key": "review-versus-http-retry",
                        "evidence_set_id": issue.evidence_set_id,
                    },
                )
        assert response.status_code == (200 if order == "review-first" else 409), response.text
        go.set()
        release.set()
        assert results.get(timeout=20) == ((409, 0) if order == "review-first" else (200, 1))
    finally:
        go.set()
        release.set()
        process.join(timeout=20)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        results.close()
        results.join_thread()
    assert process.exitcode == 0
    assert len(calls) == 1
    attempts = store.list_attempts("run", 1)
    assert attempts[:1] == original
    assert len(attempts) == (1 if order == "review-first" else 2)
    after = store.get_issue("run", 1)
    assert after.review_version == (1 if order == "review-first" else 0)
    assert after.evidence_set_id == issue.evidence_set_id
    assert after.deterministic_report == issue.deterministic_report
